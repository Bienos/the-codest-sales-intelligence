"""Administration, Data health (data pipeline review, round 4.3): every source with how often it is read, when it was last and will next be
read, what it found and what became findings matched to accounts; the scheduler's lanes with pause / cadence / run-at-next-tick; the
background jobs with their failure cause in plain words; and the source controls for cadence and "read now"."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ri.api import admin_ops
from ri.sources import registry
from ri.sources.registry import seed_registry
from test_r1_pipeline import CERT

NOW = datetime.now(timezone.utc)


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


def _doc(conn, sid, ext, *, days=1, triage=None, pending=False):
    meta = {**({"triage": triage} if triage else {}), **({"pending_extraction": True} if pending else {})}
    return conn.execute("insert into source_documents(source_id, external_id, version_key, canonical_url, title, content_text, raw_meta, fetched_at) "
                        "values (%s,%s,'v1',%s,'t','body',%s,%s) returning id", (sid, ext, f"https://x.example/{ext}", json.dumps(meta), NOW - timedelta(days=days))).fetchone()["id"]


def _event(conn, key, *, org=None, doc=None, etype="product_launch", attrs=None):
    eid = conn.execute("insert into events(event_key, event_type, subject_org_id, subject_name_raw, summary, attrs, first_observed_at, lane, extraction_model_version) "
                       "values (%s,%s,%s,'Acme','x',%s,now(),'private','test') returning id", (key, etype, org, json.dumps(attrs or {}))).fetchone()["id"]
    if doc:
        conn.execute("insert into event_documents(event_id, document_id) values (%s,%s)", (eid, doc))
    return eid


def _setup(conn):
    seed_registry(conn)
    for sid in ("attention-finextra", "regulator-ecb", "procurement-ted", "jobs-arbeitnow-de", "regulator-fca"):
        registry.activate(conn, sid, CERT, "t")
    org = conn.execute("insert into organizations(display_name, country_code) values ('Acme Bank','GB') returning id").fetchone()["id"]
    # a feed that reads fine and makes findings, one matched to an account
    conn.execute("update source_health set last_attempt_at=%s, last_success_at=%s where source_id in ('attention-finextra','regulator-ecb','jobs-arbeitnow-de')",
                 (NOW - timedelta(hours=2), NOW - timedelta(hours=2)))
    d1 = _doc(conn, "attention-finextra", "a1", days=10)
    _event(conn, "e1", org=org, doc=d1)
    _event(conn, "e2", doc=_doc(conn, "attention-finextra", "a2", days=2))
    # a feed that reads fine but nothing it read became a finding in 30 days (and it is mostly off-topic)
    for i in range(25):
        _doc(conn, "regulator-ecb", f"r{i}", days=12 if i == 0 else 3, triage="skipped_not_relevant" if i < 22 else None, pending=(i == 24))
    # a failing source and one that was never read
    conn.execute("update source_health set last_attempt_at=%s, consecutive_failures=2, state='degraded', error_class='server_error', detail='unexpected status 503' "
                 "where source_id='procurement-ted'", (NOW - timedelta(hours=1),))
    # job-board findings carry the provider, not a page
    _event(conn, "j1", org=org, etype="engineering_job_opened", attrs={"provider": "arbeitnow", "board": "arbeitnow", "job_id": "1"})
    # a per-account source that holds pages although the registry has it off
    _doc(conn, "ats-greenhouse", "g1")
    conn.execute("insert into monitoring_runs(source_id, status, started_at, finished_at, items_new) values ('attention-finextra','success',%s,%s,2)",
                 (NOW - timedelta(days=1), NOW - timedelta(days=1)))
    conn.commit()
    return org


def test_data_health_says_for_each_source_how_often_last_next_what_it_found_and_whether_it_matters(conn):
    _setup(conn)
    d = admin_ops.data_health(conn, NOW, can_edit_sources=True, can_edit_lanes=True)
    by = {s["source_id"]: s for s in d["sources"]}
    fx = by["attention-finextra"]
    assert fx["state"] == "ok" and fx["findings_30d"] == 2 and fx["matched_30d"] == 1 and fx["new_14d"] == 2 and fx["runs_14d"] == 1
    assert fx["every_s"] == 86400 and fx["scheduled"] and abs((fx["next_at"] - (NOW + timedelta(hours=22))).total_seconds()) < 5 and fx["can_read_now"]
    ecb = by["regulator-ecb"]
    assert ecb["state"] == "no_findings" and "mostly_skipped" in ecb["flags"] and "waiting_to_be_read" in ecb["flags"] and ecb["pending"] == 1
    assert by["procurement-ted"]["state"] == "failing" and by["procurement-ted"]["failures"] == 2 and "503" in by["procurement-ted"]["error"]
    assert by["regulator-fca"]["state"] == "never"
    assert by["jobs-arbeitnow-de"]["findings_30d"] == 1 and by["jobs-arbeitnow-de"]["matched_30d"] == 1
    assert by["ats-greenhouse"]["state"] == "per_account" and by["ats-greenhouse"]["next_at"] is None
    assert by["verify-original-page"]["state"] == "on_demand"
    assert not any("pipedrive" in s["source_id"] for s in d["sources"])
    order = [s["state"] for s in d["sources"]]
    assert order.index("failing") < order.index("no_findings") < order.index("ok") < order.index("off")      # what needs a person first
    assert d["counts"]["failing"] == 1 and d["backlog"]["pages_to_read"] == 1 and d["tick_seconds"] == 300
    assert {"organizations", "events", "source_documents"} <= {t["table"] for t in d["stored"]}
    lanes = {x["key"]: x for x in d["lanes"]}
    assert lanes["fit_queued"]["can_pause"] and not lanes["expiry"]["can_pause"] and lanes["retriaged"]["adjustable"] and lanes["fit_queued"]["main"]
    assert lanes["retriaged"]["last_run_at"] is None and lanes["retriaged"]["next_at"] == NOW
    ro = admin_ops.data_health(conn, NOW)
    assert not any(s["can_read_now"] or s["can_change_cadence"] for s in ro["sources"]) and not any(x["can_pause"] or x["can_run"] for x in ro["lanes"])


def test_job_failures_are_named_in_plain_words_including_ai_answers_that_were_cut_off_or_unreadable(conn):
    def job(kind, status, err=None, ec=None, at=NOW):
        conn.execute("insert into jobs(kind, payload, payload_hash, idempotency_key, status, last_error, last_error_class, updated_at) values (%s,'{}','h',%s,%s,%s,%s,%s)",
                     (kind, f"{kind}:{status}:{at.timestamp()}:{err}", status, err, ec, at))
    job("assess_fit", "permanent_failed", "JSONDecodeError: Expecting ',' delimiter: line 40 column 7 (char 9311)", "transient_network")
    job("assess_fit", "completed", at=NOW - timedelta(hours=3))
    job("tender_summaries", "permanent_failed", "ValueError: the answer was cut off at the length limit (4000 tokens)", "transient_network")
    job("source_run", "completed")
    conn.commit()
    assert admin_ops.classify("assess_fit", "transient_network", "JSONDecodeError: Expecting value: line 1 column 1 (char 0)") == "unreadable_answer"
    assert admin_ops.classify("assess_fit", "transient_network", "ValueError: the answer was cut off at the length limit (4000 tokens)") == "cut_off"
    assert admin_ops.classify("verify_url", "transient_network", "ValueError: empty model output (stop_reason: end_turn)") == "empty_output"
    jobs = {j["kind"]: j for j in admin_ops.data_health(conn, NOW)["jobs"]}
    assert jobs["assess_fit"]["cause"] == "unreadable_answer" and jobs["assess_fit"]["failing"] and jobs["assess_fit"]["done"] == 1
    assert jobs["tender_summaries"]["cause"] == "cut_off" and jobs["source_run"]["failed"] == 0 and not jobs["source_run"]["failing"]
    assert dict(admin_ops.CAUSE)["unreadable_answer"][0] and dict(admin_ops.CAUSE)["cut_off"][0]


def test_lane_controls_pause_only_what_is_safe_change_only_what_is_adjustable_and_are_audited(client, conn):
    c, h = client
    assert c.get("/api/admin/data-health", headers=h("bdm@x")).status_code == 403
    assert c.get("/api/admin/data-health", headers=h("analyst@x")).status_code == 200
    assert c.put("/api/admin/lanes/fit_queued", json={"enabled": False}, headers=h("analyst@x")).status_code == 403
    r = c.put("/api/admin/lanes/fit_queued", json={"enabled": False}, headers=h("admin@x"))
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert c.put("/api/admin/lanes/expiry", json={"enabled": False}, headers=h("admin@x")).status_code == 400
    assert c.put("/api/admin/lanes/fit_queued", json={"every_s": 7200}, headers=h("admin@x")).status_code == 400
    assert c.put("/api/admin/lanes/retriaged", json={"every_s": 7200}, headers=h("admin@x")).json()["every_s"] == 7200
    assert c.put("/api/admin/lanes/retriaged", json={"every_s": 100000}, headers=h("admin@x")).status_code == 400
    assert c.put("/api/admin/lanes/retriaged", json={"every_s": 3600}, headers=h("admin@x")).json()["every_s"] == 3600       # back to the built-in one
    assert conn.execute("select every_s from scheduler_lanes where lane='retriaged'").fetchone()["every_s"] is None
    assert c.put("/api/admin/lanes/nope", json={"enabled": True}, headers=h("admin@x")).status_code == 404
    assert c.post("/api/admin/lanes/score/run", headers=h("admin@x")).json()["run_requested"] is True
    assert conn.execute("select run_requested_by from scheduler_lanes where lane='score'").fetchone()["run_requested_by"] == "admin@x"
    ops = [r["operation"] for r in conn.execute("select operation from audit_events where record_type='scheduler_lane' order by id")]
    assert ops.count("scheduler_lane_changed") == 3 and ops[-1] == "scheduler_lane_run_requested"
    lanes = {x["key"]: x for x in c.get("/api/admin/data-health", headers=h("admin@x")).json()["lanes"]}
    assert lanes["fit_queued"]["enabled"] is False and lanes["fit_queued"]["next_at"] is None and lanes["score"]["requested_by"] == "admin@x"


def test_source_cadence_and_read_now(client, conn):
    c, h = client
    seed_registry(conn)
    registry.activate(conn, "attention-finextra", CERT, "t")
    conn.commit()
    assert c.put("/api/admin/sources/attention-finextra/cadence", json={"every_s": 3600}, headers=h("admin@x")).status_code == 400   # never more than every 6 hours
    assert c.put("/api/admin/sources/attention-finextra/cadence", json={"every_s": 43200}, headers=h("analyst@x")).status_code == 403
    assert c.put("/api/admin/sources/attention-finextra/cadence", json={"every_s": 43200}, headers=h("admin@x")).json()["every_s"] == 43200
    assert conn.execute("select poll_interval_s from sources where source_id='attention-finextra'").fetchone()["poll_interval_s"] == 43200
    assert c.put("/api/admin/sources/verify-original-page/cadence", json={"every_s": 43200}, headers=h("admin@x")).status_code == 400
    assert c.post("/api/admin/sources/regulator-boe/read-now", headers=h("admin@x")).status_code == 400          # not switched on
    r = c.post("/api/admin/sources/attention-finextra/read-now", headers=h("admin@x")).json()
    assert r["queued"] is True
    job = conn.execute("select kind, payload, correlation_id from jobs where id=%s", (r["job_id"],)).fetchone()
    assert job["kind"] == "source_run" and job["payload"] == {"source_id": "attention-finextra"} and job["correlation_id"] == "manual"
    assert c.post("/api/admin/sources/attention-finextra/read-now", headers=h("admin@x")).json() == {"source_id": "attention-finextra", "queued": False, "reason": "already_waiting"}
    conn.execute("update jobs set status='completed'")
    conn.execute("update source_health set last_attempt_at=now() - interval '3 minutes' where source_id='attention-finextra'")
    conn.commit()
    assert c.post("/api/admin/sources/attention-finextra/read-now", headers=h("admin@x")).json()["reason"] == "read_recently"
    assert {r["operation"] for r in conn.execute("select operation from audit_events where record_type='source'")} >= {"source_cadence_changed", "source_read_now"}


def test_failure_subjects_only_look_up_well_formed_ids(conn):
    assert admin_ops._uuids(["not-an-id", None, "0b8e6a8e-6f9b-4a0d-9a4e-1e6c2f6b9a11", 5]) == ["0b8e6a8e-6f9b-4a0d-9a4e-1e6c2f6b9a11"]
    out = admin_ops._subjects(conn, [{"id": 1, "payload": {"document_id": "nope", "organization_id": "also-nope"}}])
    assert out == {"1": ""}
