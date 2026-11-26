"""Administration housekeeping: failed jobs grouped by cause with safe bulk retry and dismiss, the daily source test with a safe automatic
switch-on, and the usage insight figures."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ri import budget, usage
from ri.api import admin_ops
from ri.sources import autovalidate
from ri.sources.registry import seed_registry


def _letter(conn, kind, ec, err, payload=None, at=None, job_status="permanent_failed"):
    payload = payload or {}
    jid = conn.execute("insert into jobs(kind, payload, payload_hash, idempotency_key, status, last_error, last_error_class, updated_at) "
                       "values (%s,%s,'h',%s,%s,%s,%s,coalesce(%s, now())) returning id",
                       (kind, json.dumps(payload), f"t:{kind}:{datetime.now().timestamp()}:{err[:20]}:{json.dumps(payload)}", job_status, err, ec, at)).fetchone()["id"]
    return conn.execute("insert into dead_letters(job_id, kind, payload, error_class, error, created_at) values (%s,%s,%s,%s,%s,coalesce(%s, now())) returning id",
                        (jid, kind, json.dumps(payload), ec, err, at)).fetchone()["id"]


@pytest.fixture()
def client(conn, db_url):
    from ri.api.app import create_app
    from ri.api.auth import make_dev_token
    from ri.config import Settings
    from ri.db import Database
    for email, role in (("admin@x", "admin"), ("bdm@x", "bdm"), ("analyst@x", "research_analyst")):
        conn.execute("insert into users(email, display_name, role, team) values (%s,%s,%s,'t1')", (email, email, role))
    conn.commit()
    secret = "x" * 32
    db = Database(db_url, max_size=2)
    c = TestClient(create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=secret), db=db))
    yield c, (lambda who: {"Authorization": f"Bearer {make_dev_token(secret, who)}"})
    db.close()


# ---------------------------------------------------------------- failed jobs

def test_classify_known_causes():
    c = admin_ops.classify
    assert c("verify_url", "provider_billing", "BillingError: Your credit balance is too low") == "ai_credit"
    assert c("extract_document", "invalid_payload", "JobError: Your credit balance is too low to access the Anthropic API") == "ai_credit"
    assert c("assess_fit", "transient_network", "InFailedSqlTransaction: current transaction is aborted") == "fit_bug"
    assert c("source_run", "schema_drift", "unexpected status 400 Bad Request") == "http_400"
    assert c("account_scan", "transient_network", "ReadTimeout: timed out") == "timeout"
    assert c("extract_document", "invalid_payload", "ValueError: empty model output (stop_reason: refusal)") == "empty_output"
    assert c("weird", "quarantined", "no handler for kind weird") == "no_handler"
    assert c("source_run", "policy_block", "robots.txt disallows https://x") == "blocked"


def test_failures_group_by_cause_say_when_and_whether_the_cause_is_gone(conn):
    old = datetime.now(timezone.utc) - timedelta(days=3)
    for i in range(3):
        _letter(conn, "verify_url", "provider_billing", "BillingError: Your credit balance is too low", {"request_id": f"r{i}"}, old)
    _letter(conn, "extract_document", "invalid_payload", "JobError: credit balance is too low", {"document_id": "00000000-0000-0000-0000-000000000009"}, old - timedelta(days=1))
    for _ in range(2):
        _letter(conn, "source_run", "schema_drift", "unexpected status 400 Bad Request", {"source_id": "procurement-ted"})
    _letter(conn, "assess_fit", "transient_network", "InFailedSqlTransaction: current transaction is aborted", {"batches": 4}, old)
    conn.execute("insert into jobs(kind, payload, payload_hash, idempotency_key, status, updated_at) values ('assess_fit','{}','h','fit-ok','completed', now())")
    usage.record(conn, provider="anthropic", operation="extract", units=10, unit="tokens", cost_usd=0.001, cost_basis="estimated")
    conn.commit()
    d = admin_ops.failure_groups(conn)
    g = {x["key"]: x for x in d["groups"]}
    assert set(g) == {"ai_credit", "http_400:procurement-ted", "fit_bug"}
    assert g["ai_credit"]["open"] == 4 and g["ai_credit"]["state"] == "resolved" and g["ai_credit"]["state_basis"] == "ai_ok"
    assert g["http_400:procurement-ted"]["state"] == "ongoing" and "400" in g["http_400:procurement-ted"]["title"]
    assert g["fit_bug"]["state"] == "resolved" and d["open"] == 7
    assert d["groups"][0]["key"] == "http_400:procurement-ted"           # what is still happening comes first
    assert "–" in g["ai_credit"]["when"] or g["ai_credit"]["when"]


def test_retry_requeues_each_piece_of_work_once_and_pages_for_reading_again(conn):
    seed_registry(conn)
    for _ in range(3):
        _letter(conn, "source_run", "schema_drift", "unexpected status 400 Bad Request", {"source_id": "procurement-ted"})
    doc = conn.execute("insert into source_documents(source_id, external_id, version_key, canonical_url, content_text) "
                       "values ('procurement-ted','x1','v1','https://x.example/1','some text') returning id").fetchone()["id"]
    gone = conn.execute("insert into source_documents(source_id, external_id, version_key, canonical_url) values ('procurement-ted','x2','v1','https://x.example/2') returning id").fetchone()["id"]
    _letter(conn, "extract_document", "invalid_payload", "ValueError: empty model output", {"document_id": str(doc)})
    _letter(conn, "extract_document", "invalid_payload", "ValueError: empty model output", {"document_id": str(gone)})
    conn.commit()
    r = admin_ops.retry_group(conn, "http_400:procurement-ted", "admin@x")
    conn.commit()
    assert (r["queued"], r["covered"]) == (1, 2)
    assert conn.execute("select count(*) n from jobs where idempotency_key like 'replay:%%'").fetchone()["n"] == 1
    r = admin_ops.retry_group(conn, "empty_output", "admin@x")
    conn.commit()
    assert (r["pages_requeued"], r["skipped"], r["queued"]) == (1, 1, 0)
    assert conn.execute("select raw_meta from source_documents where id=%s", (doc,)).fetchone()["raw_meta"].get("pending_extraction") is True
    assert admin_ops.failure_groups(conn)["open"] == 0
    a = conn.execute("select detail from audit_events where operation='dead_letters_retried' order by id limit 1").fetchone()["detail"]
    assert a["queued"] == 1 and a["covered"] == 2


def test_dismiss_marks_handled_and_keeps_the_trail(conn):
    for _ in range(2):
        _letter(conn, "account_scan", "transient_network", "ReadTimeout: timed out", {"organization_id": "00000000-0000-0000-0000-000000000001"})
    conn.commit()
    assert admin_ops.dismiss_group(conn, "timeout", "admin@x", "old timeouts")["dismissed"] == 2
    conn.commit()
    rows = conn.execute("select dismissed_by, dismiss_note, error from dead_letters").fetchall()
    assert all(r["dismissed_by"] == "admin@x" and r["dismiss_note"] == "old timeouts" and r["error"] for r in rows)
    g = admin_ops.failure_groups(conn)["groups"][0]
    assert g["open"] == 0 and g["dismissed"] == 2 and g["state"] == "handled"


def test_failures_api_needs_admin_jobs_and_404s_an_empty_group(client, conn):
    c, h = client
    _letter(conn, "account_scan", "transient_network", "ReadTimeout: timed out")
    conn.commit()
    assert c.get("/api/admin/failures", headers=h("bdm@x")).status_code == 403
    d = c.get("/api/admin/failures", headers=h("admin@x")).json()
    assert d["groups"][0]["key"] == "timeout" and d["groups"][0]["retryable"]
    assert c.post("/api/admin/failures/dismiss", json={"key": "timeout"}, headers=h("admin@x")).json() == {"dismissed": 1}
    assert c.post("/api/admin/failures/dismiss", json={"key": "timeout"}, headers=h("admin@x")).status_code == 404
    assert c.post("/api/admin/failures/retry", json={"key": "timeout"}, headers=h("admin@x")).status_code == 404


# ---------------------------------------------------------------- sources

def _src(conn, sid):
    return conn.execute("select * from sources where source_id=%s", (sid,)).fetchone()


def test_policy_switches_on_only_public_keyless_sources_and_says_why_for_the_rest(conn):
    seed_registry(conn)
    notes = autovalidate._notes()
    p = lambda sid: autovalidate.policy(_src(conn, sid), notes.get(sid))  # noqa: E731
    assert p("attention-fintechglobal")["eligible"] and p("regulator-eba")["eligible"]                       # public feeds
    assert p("procurement-ezamowienia")["eligible"]                                                    # open data API, keyless
    assert not p("procurement-mercell")["eligible"] and "Commercial" in p("procurement-mercell")["reason"]
    ch = p("registry-companies-house")
    assert not ch["eligible"] and ch["state"] == "needs_credential" and "COMPANIES_HOUSE_API_KEY" in ch["reason"]
    assert p("attention-techuk")["state"] == "blocked_by_site" and not p("attention-techuk")["eligible"]
    terms = p("procurement-doffin")
    assert terms["state"] in ("needs_terms", "not_built") and not terms["eligible"]
    assert p("procurement-udbud")["state"] in ("needs_terms", "not_built")
    assert p("ats-greenhouse")["state"] == "per_account" and p("internal-sheets")["state"] == "built_in"
    conn.execute("update sources set status='paused' where source_id='regulator-eba'")
    assert autovalidate.policy(_src(conn, "regulator-eba"), notes.get("regulator-eba"), paused_by_person=True)["state"] == "paused"
    assert autovalidate.policy(_src(conn, "regulator-eba"), notes.get("regulator-eba"), paused_by_person=False)["eligible"]   # paused by the registry


def test_daily_run_switches_on_what_passes_and_records_why_the_rest_stays_off(conn):
    seed_registry(conn)
    conn.commit()
    results = {"attention-fintechglobal": {"result": "passed", "ok": True}, "regulator-eba": {"result": "failed", "ok": False, "error_class": "not_found", "detail": "unexpected status 404"},
               "regulator-boe": {"result": "failed", "ok": False, "error_class": "policy_block", "detail": "robots.txt disallows https://x"},
               "procurement-uk-fts": {"result": "passed", "ok": True}}
    tested = []

    def tester(c, sid):
        tested.append(sid)
        return results.get(sid, {"result": "failed", "ok": False, "error_class": "timeout", "detail": "ReadTimeout"})
    out = autovalidate.run_daily(conn, tester=tester, only=["attention-fintechglobal", "regulator-eba", "regulator-boe", "procurement-uk-fts", "procurement-mercell", "internal-sheets"])
    assert out["switched_on"] == ["attention-fintechglobal"]
    assert _src(conn, "attention-fintechglobal")["status"] == "active" and _src(conn, "attention-fintechglobal")["certification"]["technical_owner"] == autovalidate.BY
    a = lambda sid: _src(conn, sid)["config"]["auto"]  # noqa: E731
    assert a("regulator-eba")["state"] == "needs_address" and "404" in a("regulator-eba")["reason"]
    assert a("regulator-boe")["state"] == "blocked_by_site" and "robots" in a("regulator-boe")["reason"]
    assert _src(conn, "procurement-uk-fts")["status"] != "active" and a("procurement-uk-fts")["state"] == "needs_terms"     # passed, but terms to check
    assert a("procurement-mercell")["state"] == "needs_credential" and "internal-sheets" not in tested
    from ri.connections import directory
    rows = {r["source_id"]: r for r in directory(conn)}
    assert rows["regulator-boe"]["state"] == "blocked_by_site" and "robots" in rows["regulator-boe"]["what_to_do"]
    assert rows["attention-fintechglobal"]["state"] == "live"


def test_a_source_a_person_paused_is_never_switched_on(conn):
    from ri.sources import registry
    seed_registry(conn)
    registry.pause(conn, "regulator-eba", "admin@x", "too noisy")
    conn.commit()
    out = autovalidate.run_daily(conn, tester=lambda c, sid: {"result": "passed", "ok": True}, only=["regulator-eba"])
    assert out["switched_on"] == [] and _src(conn, "regulator-eba")["status"] == "paused"
    assert _src(conn, "regulator-eba")["config"]["auto"]["state"] == "paused"


def test_sources_overview_and_schedule(client, conn):
    c, h = client
    seed_registry(conn)
    conn.commit()
    d = c.get("/api/admin/sources/auto", headers=h("admin@x")).json()
    assert d["total"] >= 60 and sum(d["counts"].values()) == d["total"]
    states = {g["state"] for g in d["groups"]}
    assert "needs_credential" in states and "blocked_by_site" in states
    ch = next(i for g in d["groups"] for i in g["items"] if i["source_id"] == "registry-companies-house")
    assert "COMPANIES_HOUSE_API_KEY" in ch["reason"]
    assert c.post("/api/admin/sources/auto/run", headers=h("bdm@x")).status_code == 403
    assert c.post("/api/admin/sources/auto/run", headers=h("admin@x")).json()["queued"] is True
    now = datetime(2026, 11, 25, 3, 0, tzinfo=timezone.utc)
    assert autovalidate.schedule(conn, now) == 1 and autovalidate.schedule(conn, now + timedelta(hours=5)) == 0


# ---------------------------------------------------------------- usage

def test_usage_insights_gauges_forecast_daily_and_cost_per_useful_result(conn):
    from ri.config import Settings
    now = datetime.now(timezone.utc)
    period = now.strftime("%Y-%m")
    budget.set_cap(conn, "exa", "monthly", period, 20, "usd")
    budget.set_cap(conn, "anthropic", "monthly", period, 1_000_000, "tokens")
    conn.execute("update provider_budgets set spent = 4 where provider='exa'")
    conn.execute("update provider_budgets set spent = 300000 where provider='anthropic'")
    usage.record(conn, provider="exa", operation="identity_match", units=1, unit="searches", cost_usd=0.007, cost_basis="reported")
    usage.record(conn, provider="exa", operation="search", units=1, unit="searches", cost_usd=0.01, cost_basis="reported")
    usage.record(conn, provider="anthropic", operation="tender_summary", units=3000, unit="tokens", tokens_in=2500, tokens_out=500, cost_usd=0.0005, cost_basis="estimated")
    usage.record(conn, provider="anthropic", operation="extract", units=2000, unit="tokens", tokens_in=1800, tokens_out=200, cost_usd=0.0003, cost_basis="estimated")
    conn.commit()
    d = admin_ops.usage_insights(conn, Settings(env="test", anthropic_price_in_per_mtok=0.1, anthropic_price_out_per_mtok=0.5), now)
    assert d["exa"]["cap"] == 20 and d["exa"]["used"] == 4 and d["ai"]["pct"] == 30.0
    assert d["exa"]["forecast"] >= 4 and d["days_in_month"] >= 28 and len(d["daily"]) == now.day
    today = d["daily"][-1]
    assert round(today["exa_usd"], 3) == 0.017 and today["ai_tokens"] == 5000
    purposes = {p["key"]: p for p in d["purposes"]}
    assert {"matching", "web_search", "tenders", "reading"} <= set(purposes)
    assert purposes["tenders"]["useful_label"] == "summaries" and purposes["matching"]["label"] == "Company matching"
    assert d["total_usd"] == pytest.approx(0.0178, abs=1e-6)


def test_usage_insights_api_needs_cost_permission(client):
    c, h = client
    assert c.get("/api/admin/usage/insights", headers=h("bdm@x")).status_code == 403
    d = c.get("/api/admin/usage/insights", headers=h("admin@x")).json()
    assert "ai" in d and "exa" in d and isinstance(d["daily"], list)
