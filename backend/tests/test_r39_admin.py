"""Administration tracking: the 'Is everything working?' overview, the job history, source checks, the import history, spend per provider
(Apollo credits included), every paid call with filters, and the audit log with filters and record names."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ri import budget, usage
from ri.api import admin_ops
from ri.audit import audit
from ri.sources.registry import seed_registry

NOW = datetime.now(timezone.utc)


def _job(conn, kind, status, *, payload=None, at=None, claimed=None, error=None, error_class=None, result=None, attempts=1):
    at = at or NOW
    return conn.execute(
        "insert into jobs(kind, payload, payload_hash, idempotency_key, status, attempts, last_error, last_error_class, result, created_at, updated_at, claimed_at) "
        "values (%s,%s,'h',%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
        (kind, json.dumps(payload or {}), f"t:{kind}:{status}:{at.timestamp()}:{json.dumps(payload or {})}", status, attempts, error, error_class,
         json.dumps(result) if result is not None else None, at - timedelta(minutes=5), at, claimed)).fetchone()["id"]


@pytest.fixture()
def client(conn, db_url):
    from ri.api.app import create_app
    from ri.api.auth import make_dev_token
    from ri.config import Settings
    from ri.db import Database
    for email, role in (("admin@x", "admin"), ("bdm@x", "bdm"), ("analyst@x", "research_analyst"), ("comp@x", "compliance")):
        conn.execute("insert into users(email, display_name, role, team) values (%s,%s,%s,'t1')", (email, email, role))
    conn.commit()
    secret = "x" * 32
    db = Database(db_url, max_size=2)
    c = TestClient(create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=secret), db=db))
    yield c, (lambda who: {"Authorization": f"Bearer {make_dev_token(secret, who)}"})
    db.close()


# ---------------------------------------------------------------- job history

def test_job_history_filters_by_plain_status_kind_and_window_and_names_the_subject(conn):
    seed_registry(conn)
    _job(conn, "source_run", "completed", payload={"source_id": "procurement-ted"}, claimed=NOW - timedelta(seconds=42), result={"items_new": 3, "ok": True})
    _job(conn, "source_run", "permanent_failed", payload={"source_id": "procurement-ted"}, error="SchemaDrift: unexpected status 400 Bad Request", error_class="schema_drift")
    _job(conn, "verify_url", "retryable_failed", error="BillingError: Your credit balance is too low", error_class="provider_billing")
    _job(conn, "account_scan", "completed", at=NOW - timedelta(days=10))
    conn.commit()

    d = admin_ops.job_history(conn, since="7d")
    assert d["total"] == 3 and d["groups"] == {"done": 1, "failed": 1, "retrying": 1}
    assert {k["kind"]: k["n"] for k in d["kinds"]} == {"source_run": 2, "verify_url": 1}
    done = next(x for x in d["items"] if x["group"] == "done")
    assert done["subject"] == "EU TED (Tenders Electronic Daily)" and 41 <= done["duration_s"] <= 43
    assert done["result"] == [["items_new", 3]]                      # numbers only: a flag is not a count
    failed = admin_ops.job_history(conn, status="failed", since="7d")["items"]
    assert len(failed) == 1 and failed[0]["cause"] == "http_400" and failed[0]["status"] == "permanent_failed"
    assert admin_ops.job_history(conn, kind="verify_url", since="7d")["items"][0]["cause"] == "ai_credit"
    assert admin_ops.job_history(conn, since="all")["total"] == 4      # 'all' has no window
    page = admin_ops.job_history(conn, since="all", limit=2, offset=2)
    assert page["total"] == 4 and len(page["items"]) == 2


def test_job_history_endpoint_needs_admin_jobs_and_rejects_unknown_status(client, conn):
    c, h = client
    _job(conn, "sheet_sync", "completed")
    conn.commit()
    assert c.get("/api/admin/jobs/history", headers=h("bdm@x")).status_code == 403
    assert c.get("/api/admin/jobs/history?status=exploded", headers=h("admin@x")).status_code == 400
    r = c.get("/api/admin/jobs/history?since=24h&status=done", headers=h("admin@x"))
    assert r.status_code == 200 and r.json()["total"] == 1 and r.json()["items"][0]["kind"] == "sheet_sync"


# ---------------------------------------------------------------- source checks

def test_source_checks_list_runs_and_count_sources_read_today(conn):
    seed_registry(conn)
    conn.execute("insert into monitoring_runs(source_id, started_at, finished_at, status, items_new, items_changed) values "
                 "('procurement-ted', now() - interval '2 hours', now() - interval '2 hours' + interval '30 seconds', 'success', 4, 1), "
                 "('procurement-ted', now() - interval '3 days', now() - interval '3 days', 'failed', 0, 0)")
    # a deployment that only keeps the health record still counts as read today
    conn.execute("insert into source_health(source_id, state, last_success_at) values ('procurement-uk-fts', 'healthy', now() - interval '1 hour') "
                 "on conflict (source_id) do update set last_success_at = excluded.last_success_at")
    conn.execute("insert into source_health(source_id, state, last_attempt_at, last_success_at) values ('regulator-boe', 'failed', now() - interval '1 hour', now() - interval '5 days') "
                 "on conflict (source_id) do update set last_attempt_at = excluded.last_attempt_at, last_success_at = excluded.last_success_at")
    conn.commit()
    d = admin_ops.source_checks(conn, since="7d")
    assert d["total"] == 2 and d["by_status"] == {"success": 1, "failed": 1}
    assert d["items"][0]["display_name"] == "EU TED (Tenders Electronic Daily)" and d["items"][0]["duration_s"] == 30.0
    assert admin_ops.source_checks(conn, since="24h")["total"] == 1
    assert admin_ops.source_checks(conn, status="failed", since="7d")["items"][0]["status"] == "failed"
    t = d["today"]
    assert t["checked"] == 3 and t["failing"] == 1 and t["items_new"] == 4


# ---------------------------------------------------------------- imports

def test_import_history_merges_uploads_and_syncs_with_row_totals(conn):
    stats = {"tabs": {"MASTER ACCOUNTS": {"status": "ok", "rows": 10, "blank": 2, "new": 5}, "MASTER CONTACTS": {"status": "ok", "rows": 4, "blank": 0, "new": 4},
                      "ARCHIVE": {"status": "unknown_tab"}}}
    run_up = conn.execute("insert into import_runs(workbook_key, workbook_id, status, started_at, finished_at, stats) values ('A','wa','completed', now() - interval '1 day', now() - interval '1 day', %s) returning id",
                          (json.dumps(stats),)).fetchone()["id"]
    conn.execute("insert into import_uploads(workbook_key, filename, sha256, size_bytes, status, uploaded_by, import_run_id, created_at) values ('A','a.xlsx','x',10,'completed','admin@x',%s, now() - interval '1 day')",
                 (run_up,))
    conn.execute("insert into import_runs(workbook_key, workbook_id, status, started_at, error) values ('B','wb','failed', now(), 'tab moved')")
    conn.commit()
    items = admin_ops.import_history(conn)["items"]
    assert [i["how"] for i in items] == ["sync", "upload"]            # newest first, and the upload's own run is not listed twice
    assert items[0]["status"] == "failed" and items[0]["error"] == "tab moved"
    assert (items[1]["name"], items[1]["new"], items[1]["rows"]) == ("a.xlsx", 9, 12)


# ---------------------------------------------------------------- spend and paid calls

def test_spend_shows_each_provider_with_apollo_credits_and_calls_stopped_by_the_limit(conn):
    period = usage.period_now()
    budget.set_cap(conn, "exa", "monthly", period, 20, "usd")
    budget.set_cap(conn, "apollo", "contact_enrichment", period, 50, "credits")
    budget.reserve(conn, "apollo", "contact_enrichment", period, 8, key="rev-1")
    budget.settle(conn, "apollo", "contact_enrichment", period, 8, 8, key="rev-1")
    with pytest.raises(Exception):
        budget.reserve(conn, "exa", "monthly", period, 25, key="too-big")
    usage.record(conn, provider="exa", operation="search", units=1, unit="searches", cost_usd=0.012, cost_basis="reported")
    conn.commit()

    class S:
        anthropic_monthly_token_cap = 1000.0
        exa_monthly_cap_usd = 20.0
        apollo_credits_per_reveal = 8.0
    d = admin_ops.spend_this_month(conn, S())
    assert d["apollo"]["cap"] == 50 and d["apollo"]["used"] == 8 and d["apollo"]["credits_per_reveal"] == 8.0
    assert d["exa"]["blocked_by_limit"] == 1 and d["exa"]["calls"] == 1 and d["exa"]["usd"] == pytest.approx(0.012)
    assert d["ai"]["cap"] == 1000.0 and d["ai"]["cap_set"] is True and d["ai"]["used"] == 0      # no row yet: the configured limit


def test_paid_calls_filter_by_purpose_and_result_with_totals(conn):
    usage.record(conn, provider="anthropic", operation="extract", units=100, unit="tokens", tokens_in=80, tokens_out=20, cost_usd=0.01, cost_basis="estimated")
    usage.record(conn, provider="anthropic", operation="extract", units=50, unit="tokens", tokens_in=50, tokens_out=0, cost_usd=0.0, cost_basis="estimated", status="error:provider_billing")
    usage.record(conn, provider="exa", operation="search", units=1, unit="searches", cost_usd=0.012, cost_basis="reported")
    usage.record(conn, provider="anthropic", operation="something_new", units=10, unit="tokens", tokens_in=10, tokens_out=0, cost_usd=0.001, cost_basis="estimated")
    conn.commit()
    d = admin_ops.paid_calls(conn)
    assert d["total"] == 4 and d["failed"] == 1 and d["tokens"] == 160
    assert {p["key"]: p["n"] for p in d["purposes"]} == {"reading": 2, "web_search": 1, "other": 1}
    r = admin_ops.paid_calls(conn, purpose="reading", status="failed")
    assert r["total"] == 1 and r["items"][0]["purpose_label"] == "Reading pages"
    assert admin_ops.paid_calls(conn, purpose="other")["items"][0]["operation"] == "something_new"
    assert admin_ops.paid_calls(conn, provider="exa")["usd"] == pytest.approx(0.012)


# ---------------------------------------------------------------- the overview

def test_overview_answers_is_everything_working_per_permission(client, conn):
    c, h = client
    _job(conn, "source_run", "permanent_failed", error="boom", error_class="server_error")
    _job(conn, "source_run", "completed")
    conn.execute("insert into import_runs(workbook_key, workbook_id, status, started_at, stats) values ('A','wa','completed', now(), %s)",
                 (json.dumps({"tabs": {"MASTER ACCOUNTS": {"status": "ok", "rows": 3, "blank": 0, "new": 3}}}),))
    audit(conn, actor="admin@x", operation="user_add", record_type="user", record_id="u1")
    audit(conn, actor="admin@x", operation="pii_read", record_type="organization", record_id="o1")
    conn.commit()
    d = c.get("/api/admin/overview", headers=h("admin@x")).json()
    assert d["jobs"]["failed_24h"] == 1 and d["jobs"]["done_24h"] == 1
    assert d["last_import"]["workbook"] == "A" and d["last_import"]["new"] == 3
    assert d["changes"] == {"changes_24h": 1, "all_24h": 2}
    assert {"sources", "spend", "matching"} <= set(d)
    a = c.get("/api/admin/overview", headers=h("analyst@x")).json()       # sources and matching only
    assert set(a) == {"at", "sources", "matching"}
    assert c.get("/api/admin/overview", headers=h("bdm@x")).status_code == 403


# ---------------------------------------------------------------- audit log

def test_audit_log_hides_views_and_bulk_rows_from_changes_and_names_records(client, conn):
    c, h = client
    org = conn.execute("insert into organizations(display_name) values ('Synthetic Bank plc') returning id").fetchone()["id"]
    audit(conn, actor="admin@x", operation="account_owner_set", record_type="organization", record_id=str(org), detail={"owner": "bdm@x"})
    for _ in range(3):
        audit(conn, actor="admin@x", operation="pii_read", record_type="organization", record_id=str(org), detail={"count": 2})
    audit(conn, actor="system:sheet-reconcile", operation="reconcile_row", record_type="account_source_link", record_id="x")
    audit(conn, actor="bdm@x", operation="leads_exported", record_type="people_list", record_id="list")
    conn.commit()
    d = c.get("/api/admin/audit?view=changes", headers=h("admin@x")).json()
    assert d["total"] == 2 and d["hidden"] == 4
    assert {o["operation"] for o in d["operations"]} == {"account_owner_set", "leads_exported"}
    owner = next(i for i in d["items"] if i["operation"] == "account_owner_set")
    assert owner["record_name"] == "Synthetic Bank plc"
    assert c.get("/api/admin/audit?view=changes&actor=bdm@x", headers=h("admin@x")).json()["total"] == 1
    assert c.get("/api/admin/audit?view=reads", headers=h("admin@x")).json()["total"] == 3
    allx = c.get("/api/admin/audit?limit=2&offset=2", headers=h("admin@x")).json()          # the old call still lists everything
    assert allx["total"] == 6 and len(allx["items"]) == 2
    assert c.get("/api/admin/audit?operation=pii_read", headers=h("admin@x")).json()["total"] == 3
    assert c.get("/api/admin/audit", headers=h("bdm@x")).status_code == 403
