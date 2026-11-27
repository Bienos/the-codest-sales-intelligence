"""Lemlist enrolment, webhook, Pipedrive deal approvals and integration status. HTTP is faked: nothing here touches a real service."""
import json

import pytest

from ri import approvals as appr
from ri.config import Settings
from ri.outbound import enrol
from ri.outbound.lemlist import LemlistClient, LemlistError
from test_r4_crm_notify import mk_user

STEPS = [{"day": 0, "subject": "S1", "body": "Hello {{firstName}},\n\nOne.", "claims_used": [], "fact_refs": []},
         {"day": 4, "subject": "S2", "body": "Two.", "claims_used": [], "fact_refs": []},
         {"day": 9, "subject": "S3", "body": "Three.", "claims_used": [], "fact_refs": []}]


class FakeLemlist:
    def __init__(self, fail_on_lead=None):
        self.calls, self.fail_on_lead, self.n = [], fail_on_lead, 0

    def create_campaign(self, name):
        self.calls.append(("campaign", name))
        return {"_id": "cam_1"}

    def sequences(self, cid):
        self.calls.append(("sequences", cid))
        return {"seq": {"_id": "seq_1"}}

    def add_step(self, cid, sid, *, subject, message, delay_days):
        self.calls.append(("step", subject, delay_days, message))
        return {}

    def leads_by_email(self, email):
        return []

    def add_lead(self, cid, email, fields):
        self.calls.append(("lead", email))
        if self.fail_on_lead == email:
            raise LemlistError("boom", 422)
        self.n += 1
        return {"_id": f"lead_{self.n}"}


def S(**kw):
    return Settings(env="development", auth_mode="dev", dev_auth_secret="x" * 32, **kw)


def seed(conn, statuses=(("Verified", "ok@example.invalid"), ("Found — Unverified", "u@example.invalid"), ("Not Found", None))):
    # a UK company: cold email to companies is allowed there with an opt-out (rules/email_rules.yaml); the country rule has its own tests (r61)
    org = str(conn.execute("insert into organizations(display_name, canonical_domain, country_code) values ('Synthetic Outbound Bank Ltd','outbound.example.invalid','GB') returning id").fetchone()["id"])
    ids = []
    for i, (st, em) in enumerate(statuses):
        pid = str(conn.execute("insert into people(full_name, work_email, email_verification_status) values (%s,%s,%s) returning id", (f"Person {i} Name", em, st)).fetchone()["id"])
        conn.execute("insert into employment(person_id, organization_id, title) values (%s,%s,'CTO')", (pid, org))
        ids.append(pid)
    did = str(conn.execute("insert into campaign_drafts(organization_id, requested_by, status, steps) values (%s,'x','approved',%s::jsonb) returning id", (org, json.dumps(STEPS))).fetchone()["id"])
    conn.commit()
    return org, ids, did


def approve(conn, aid):
    mk_user(conn, "approver@x", "sales_manager", 9)
    appr.decide(conn, aid, approver="approver@x", approver_role="sales_manager", approve=True)
    conn.commit()


def test_any_held_unsuppressed_email_is_eligible_verified_first(conn):
    """Owner's decision (25 Nov 2026): unverified addresses are used too; verified ones come first."""
    org, ids, _ = seed(conn)
    conn.execute("insert into people(full_name, work_email, email_verification_status, suppressed) values ('Sup Person','s@example.invalid','Verified',true) returning id")
    sid = str(conn.execute("select id from people where full_name='Sup Person'").fetchone()["id"])
    conn.execute("insert into employment(person_id, organization_id, title) values (%s,%s,'CIO')", (sid, org))
    review = str(conn.execute("insert into people(full_name, work_email, email_verification_status) values ('Rev Person','r@example.invalid','Needs Manual Review') returning id").fetchone()["id"])
    conn.execute("insert into employment(person_id, organization_id, title) values (%s,%s,'CIO')", (review, org))
    plan = enrol.plan_contacts(conn, org)
    assert [(c["email"], c["verified"]) for c in plan["eligible"]] == [("ok@example.invalid", True), ("u@example.invalid", False)]
    assert len(plan["needs_verification"]) == 1 and plan["no_email"] == 1 and plan["suppressed"] == 1 and plan["manual_review"] == 1


def test_a_request_needs_a_contact_with_an_address_and_an_approved_draft(conn):
    org, ids, did = seed(conn)
    with pytest.raises(appr.ApprovalError):
        enrol.request_enrolment(conn, did, [ids[2]], by="bdm@x", requester_crm_user=None)          # no address
    conn.execute("update campaign_drafts set status='draft' where id=%s", (did,))
    with pytest.raises(appr.ApprovalError):
        enrol.request_enrolment(conn, did, [ids[0]], by="bdm@x", requester_crm_user=None)


def test_owned_or_open_deal_accounts_are_blocked(conn):
    org, ids, did = seed(conn)
    conn.execute("insert into crm_organizations(crm_id, organization_id, name, owner_id, open_deals_count, link_status) values (77,%s,'x',5,0,'linked')", (org,))
    with pytest.raises(appr.ApprovalError, match="another seller"):
        enrol.request_enrolment(conn, did, [ids[0]], by="bdm@x", requester_crm_user=6)
    conn.execute("update crm_organizations set open_deals_count=2")
    with pytest.raises(appr.ApprovalError, match="open deal"):
        enrol.request_enrolment(conn, did, [ids[0]], by="bdm@x", requester_crm_user=5)


def test_execution_is_gated_by_switch_approval_and_requester_cannot_self_approve(conn):
    org, ids, did = seed(conn)
    r = enrol.request_enrolment(conn, did, [ids[0]], by="bdm@x", requester_crm_user=None)
    with pytest.raises(appr.ApprovalError, match="switched off"):
        enrol.execute_enrolment(conn, r["approval_id"], FakeLemlist(), S())
    with pytest.raises(appr.ApprovalError, match="approval is pending"):
        enrol.execute_enrolment(conn, r["approval_id"], FakeLemlist(), S(outbound_enabled=True))
    mk_user(conn, "bdm@x", "bdm", 1)
    with pytest.raises(appr.ApprovalError, match="own"):
        appr.decide(conn, r["approval_id"], approver="bdm@x", approver_role="bdm", approve=True)


def test_approved_enrolment_builds_the_campaign_but_never_launches_and_is_single_use(conn):
    org, ids, did = seed(conn)
    r = enrol.request_enrolment(conn, did, [ids[0]], by="bdm@x", requester_crm_user=None)
    approve(conn, r["approval_id"])
    fake = FakeLemlist()
    out = enrol.execute_enrolment(conn, r["approval_id"], fake, S(outbound_enabled=True))
    assert out["leads"] == 1 and out["lemlist_campaign_id"] == "cam_1"
    kinds = [c[0] for c in fake.calls]
    assert kinds == ["campaign", "sequences", "step", "step", "step", "lead"] and "launch" not in kinds
    assert [c[2] for c in fake.calls if c[0] == "step"] == [0, 4, 5]          # delays between emails, not days since the first
    assert [c[1] for c in fake.calls if c[0] == "step"] == ["S1", None, None]          # follow-ups carry no subject so they thread as replies
    assert "<br>" in fake.calls[2][3]
    assert conn.execute("select status from outbound_enrolments").fetchone()["status"] == "pushed"
    with pytest.raises(appr.ApprovalError):
        enrol.execute_enrolment(conn, r["approval_id"], FakeLemlist(), S(outbound_enabled=True))


def test_a_contact_who_unsubscribed_after_approval_is_dropped_at_send_time(conn):
    org, ids, did = seed(conn)
    r = enrol.request_enrolment(conn, did, [ids[0]], by="bdm@x", requester_crm_user=None)
    approve(conn, r["approval_id"])
    conn.execute("update people set suppressed=true where id=%s", (ids[0],))
    with pytest.raises(appr.ApprovalError, match="no approved contact"):
        enrol.execute_enrolment(conn, r["approval_id"], FakeLemlist(), S(outbound_enabled=True))


def test_a_payload_change_after_approval_is_refused(conn):
    org, ids, did = seed(conn)
    r = enrol.request_enrolment(conn, did, [ids[0]], by="bdm@x", requester_crm_user=None)
    approve(conn, r["approval_id"])
    conn.execute("update approvals set payload = jsonb_set(payload, '{campaign_name}', '\"tampered\"') where id=%s", (r["approval_id"],))
    with pytest.raises(appr.ApprovalError, match="differs"):
        enrol.execute_enrolment(conn, r["approval_id"], FakeLemlist(), S(outbound_enabled=True))


def test_webhook_events_update_status_dedupe_and_suppress(conn):
    org, ids, did = seed(conn)
    r = enrol.request_enrolment(conn, did, [ids[0]], by="bdm@x", requester_crm_user=None)
    approve(conn, r["approval_id"])
    enrol.execute_enrolment(conn, r["approval_id"], FakeLemlist(), S(outbound_enabled=True))
    ev = {"type": "emailsReplied", "campaignId": "cam_1", "leadEmail": "OK@example.invalid", "_id": "e1"}
    assert enrol.record_event(conn, ev)["status"] == "replied"
    assert enrol.record_event(conn, ev) == {"recorded": False, "reason": "duplicate"}
    assert enrol.record_event(conn, {"type": "interested", "campaignId": "cam_1", "leadEmail": "ok@example.invalid", "_id": "e2"})["status"] == "interested"
    assert enrol.record_event(conn, {"type": "emailsReplied", "campaignId": "cam_1", "leadEmail": "ok@example.invalid", "_id": "e3"})["status"] == "replied"
    assert conn.execute("select status from outbound_enrolments").fetchone()["status"] == "interested"          # a later weaker event never downgrades
    enrol.record_event(conn, {"type": "emailsUnsubscribed", "campaignId": "cam_1", "leadEmail": "ok@example.invalid", "_id": "e4"})
    assert conn.execute("select suppressed from people where id=%s", (ids[0],)).fetchone()["suppressed"] is True
    assert [c["email"] for c in enrol.plan_contacts(conn, org)["eligible"]] == ["u@example.invalid"]          # the opted-out person is gone



def test_existing_workspace_lead_and_new_crm_deal_prevent_enrolment(conn):
    org, ids, did = seed(conn)
    r = enrol.request_enrolment(conn, did, [ids[0]], by="bdm@x", requester_crm_user=None)
    approve(conn, r["approval_id"])
    class Existing(FakeLemlist):
        def leads_by_email(self, email):
            return [{"campaign": {"id": "another"}, "status": "running"}]
    client = Existing()
    with pytest.raises(LemlistError, match="already enrolled"):
        enrol.execute_enrolment(conn, r["approval_id"], client, S(outbound_enabled=True))
    assert client.calls == []

    conn.execute("insert into crm_organizations(crm_id, name, link_status, organization_id) values (90,'x','linked',%s)", (org,))
    conn.execute("insert into crm_deals(crm_id,crm_org_id,title,status) values (91,90,'Open conversation','open')")
    with pytest.raises(appr.ApprovalError, match="open deal"):
        enrol.request_enrolment(conn, did, [ids[0]], by="bdm@x", requester_crm_user=None)


def test_lemlist_client_retries_and_never_needs_a_live_key():
    import httpx
    seen = []

    def handler(req):
        seen.append(req.url.path)
        return httpx.Response(429, headers={"Retry-After": "0"}) if len(seen) == 1 else httpx.Response(200, json={"_id": "t", "name": "Team"})
    c = LemlistClient("k", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert c.team()["name"] == "Team" and len(seen) == 2
    with pytest.raises(Exception):
        LemlistClient(None)
    bad = LemlistClient("k", http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))))
    with pytest.raises(LemlistError, match="refused"):
        bad.team()


def test_endpoints_status_webhook_and_deal_proposal(conn, db_url):
    from fastapi.testclient import TestClient
    from ri.api.app import create_app
    from ri.api.auth import make_dev_token
    from ri.db import Database
    from test_r7_verify_discovery_usage import SECRET
    mk_user(conn, "boss@x", "admin", None)
    mk_user(conn, "bdm@x", "bdm", 555)
    org, ids, did = seed(conn)
    conn.execute("insert into crm_organizations(crm_id, organization_id, name, owner_id, link_status) values (88,%s,'x',555,'linked')", (org,))
    conn.commit()
    db = Database(db_url, max_size=2)
    try:
        st = Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET, lemlist_webhook_secret="s" * 32, pipedrive_api_token="PDTOKEN-SECRET", pipedrive_domain="d")
        c = TestClient(create_app(st, db=db))
        admin = {"Authorization": f"Bearer {make_dev_token(SECRET, 'boss@x')}"}
        bdm = {"Authorization": f"Bearer {make_dev_token(SECRET, 'bdm@x')}"}
        items = {i["key"]: i for i in c.get("/api/admin/integrations", headers=admin).json()["items"]}
        assert items["lemlist"]["configured"] is False and items["lemlist_webhook"]["configured"] is True and items["pipedrive"]["configured"] is True
        assert "PDTOKEN" not in json.dumps(items) and "s" * 32 not in json.dumps(items)
        assert c.post("/api/admin/integrations/lemlist/test", headers=admin).json()["ok"] is False
        assert c.get("/api/admin/integrations", headers=bdm).status_code == 403
        plan = c.get(f"/api/accounts/{org}/contacts-plan", headers=bdm).json()
        assert [e["verified"] for e in plan["eligible"]] == [True, False]          # verified first, unverified usable too
        r = c.post(f"/api/campaigns/{did}/enrol", headers=bdm, json={"person_ids": [ids[0]]})
        assert r.status_code == 200 and r.json()["ownership_checked"] is True
        aid = r.json()["approval_id"]
        assert c.post(f"/api/approvals/{aid}/decision", headers=bdm, json={"approve": True}).status_code == 409          # not your own
        assert c.post(f"/api/approvals/{aid}/decision", headers=admin, json={"approve": True}).json()["status"] == "approved"
        assert c.post(f"/api/approvals/{aid}/execute", headers=admin).status_code == 409                      # switch is off
        assert c.post("/webhooks/lemlist/wrong", json={}).status_code == 401
        ok = c.post(f"/webhooks/lemlist/{'s' * 32}", json={"type": "emailsReplied", "campaignId": "c", "leadEmail": "nobody@example.invalid", "_id": "z"})
        assert ok.status_code == 200 and ok.json()["results"][0]["matched"] is False
        d = c.post(f"/api/accounts/{org}/crm/propose-deal", headers=bdm, json={"title": "Synthetic deal", "justification": "positive reply"})
        assert d.status_code == 200 and d.json()["diff"]["payload"]["pipeline_id"] == 2 and d.json()["diff"]["payload"]["stage_id"] == 7
    finally:
        db.close()


def test_crm_sync_step_does_nothing_without_a_token_and_only_runs_at_the_top_of_the_hour(conn, monkeypatch):
    from datetime import datetime, timezone
    from ri import scheduler
    assert scheduler.crm_sync_step(conn, S(), datetime(2026, 11, 21, 10, 1, tzinfo=timezone.utc)) == {}
    ran = []
    import ri.crm.pipedrive as pdm
    monkeypatch.setattr(pdm, "sync_incremental", lambda c, r: ran.append(1) or {"orgs": 0})
    monkeypatch.setattr(pdm, "process_inbox", lambda c: {"processed": 0})
    st = S(pipedrive_api_token="t", pipedrive_domain="d")
    assert scheduler.crm_sync_step(conn, st, datetime(2026, 11, 21, 10, 30, tzinfo=timezone.utc)) == {} and not ran
    assert scheduler.crm_sync_step(conn, st, datetime(2026, 11, 21, 10, 2, tzinfo=timezone.utc))["crm_sync"] == {"orgs": 0} and ran


def test_canary_reports_failures_once_per_change_and_recovers(conn):
    from datetime import datetime, timezone
    from ri import canary

    class Sink:
        def __init__(self):
            self.sent = []

        def send(self, r, text):
            self.sent.append(text)
    sink, t = Sink(), datetime(2026, 11, 21, 10, 1, tzinfo=timezone.utc)
    assert canary.run_canary(conn, S(), datetime(2026, 11, 21, 10, 30, tzinfo=timezone.utc), sink) == {}          # only at the top of the hour
    out = canary.run_canary(conn, S(), t, sink)
    assert out["canary_failing"] >= 2 and out["canary_alerted"] and len(sink.sent) == 1                          # no worker, no scheduler, no rules in a bare database
    canary.run_canary(conn, S(), t, sink)
    assert len(sink.sent) == 1                                                                                   # unchanged: no repeat message
    codes = {b["code"] for b in canary.run_checks(conn)}
    assert {"worker_silent", "scheduler_silent", "no_active_rules"} <= codes
    conn.execute("insert into system_heartbeats(component, at, detail) values ('worker', now(), '{}'), ('scheduler', now(), '{}') on conflict (component) do update set at=now()")
    from ri.signals.baseline import import_baseline
    from ri.signals.persist import sync_rule_versions
    import_baseline(conn)
    sync_rule_versions(conn)
    out2 = canary.run_canary(conn, S(), t, sink)
    assert out2["canary_failing"] == 0 and "all checks pass" in sink.sent[-1]


def test_failure_records_survive_the_request_rollback_and_ambiguous_failures_can_be_retried(conn, db_url):
    """The API rolls back the request transaction on any error. The failed/retryable state must already be committed."""
    from ri.db import Database
    org, ids, did = seed(conn)
    r = enrol.request_enrolment(conn, did, [ids[0]], by="bdm@x", requester_crm_user=None)
    approve(conn, r["approval_id"])
    db = Database(db_url, max_size=2)
    try:
        class Boom(FakeLemlist):
            def add_lead(self, cid, email, fields):
                raise LemlistError("bad lead", 422)
        with pytest.raises(LemlistError):
            with db.tx() as c2:
                enrol.execute_enrolment(c2, r["approval_id"], Boom(), S(outbound_enabled=True))
        assert conn.execute("select status from approvals where id=%s", (r["approval_id"],)).fetchone()["status"] == "failed"
    finally:
        db.close()


def test_an_ambiguous_failure_returns_the_approval_to_approved_and_a_retry_reuses_the_campaign(conn, db_url):
    from ri.db import Database
    org, ids, did = seed(conn)
    r = enrol.request_enrolment(conn, did, [ids[0]], by="bdm@x", requester_crm_user=None)
    approve(conn, r["approval_id"])
    db = Database(db_url, max_size=2)
    try:
        class Flaky(FakeLemlist):
            def add_lead(self, cid, email, fields):
                raise LemlistError("Lemlist 503 after retries", 503)
        with pytest.raises(LemlistError):
            with db.tx() as c2:
                enrol.execute_enrolment(c2, r["approval_id"], Flaky(), S(outbound_enabled=True))
        row = conn.execute("select status, result from approvals where id=%s", (r["approval_id"],)).fetchone()
        assert row["status"] == "approved" and row["result"]["lemlist_campaign_id"] == "cam_1" and row["result"]["steps_added"] is True
        good = FakeLemlist()
        out = enrol.execute_enrolment(conn, r["approval_id"], good, S(outbound_enabled=True))
        assert out["leads"] == 1 and [c[0] for c in good.calls] == ["lead"]          # no second campaign, no duplicate steps
    finally:
        db.close()


# ---------------------------------------------------------------- review follow-ups: provider billing, public bodies off the hot list
def test_a_provider_out_of_credit_is_billing_not_schema_drift_and_the_job_retries_instead_of_dying():
    import httpx
    from ri.sources.base import BillingError, SchemaDrift
    from ri.sources.http import classify
    body = '{"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too low to access the Anthropic API."}}'
    with pytest.raises(BillingError):
        classify(httpx.Response(400, text=body))
    with pytest.raises(SchemaDrift):
        classify(httpx.Response(400, text='{"error":"bad field"}'))


def test_billing_failures_stay_retryable_and_the_canary_names_them(conn):
    from ri.jobs import queue
    from ri.sources.base import BillingError
    from ri import canary
    jid, _ = queue.enqueue(conn, "source_run", {"source_id": "x"}, idempotency_key="bill-1", max_attempts=1)
    conn.execute("update jobs set attempts=5 where id=%s", (jid,))
    job = conn.execute("select * from jobs where id=%s", (jid,)).fetchone()
    assert queue.fail(conn, job, BillingError("provider billing: credit balance is too low")) == "retryable_failed"
    assert conn.execute("select count(*) n from dead_letters").fetchone()["n"] == 0
    assert "provider_billing" in {b["code"] for b in canary.run_checks(conn)}


def test_public_bodies_regulators_and_research_institutes_are_tagged_and_kept_off_the_hot_list(conn, db_url):
    from fastapi.testclient import TestClient
    from ri.api.app import create_app
    from ri.api.auth import make_dev_token
    from ri.db import Database
    from ri.market import is_public_body, tag_public_bodies
    from test_r7_verify_discovery_usage import SECRET
    for n in ("Finanstilsynet", "Pensionsmyndigheten", "Mölndals stad", "Adda Inköpscentral AB", "Fraunhofer-Gesellschaft - Einkauf B12", "BaFin", "Senaatti-kiinteistöt"):
        assert is_public_body(n), n
    for n in ("Danske Bank A/S", "Nordea Bank Abp", "Klarna Bank AB", "Revolut Ltd", "Pension Fund Software GmbH"):
        assert not is_public_body(n) or n.startswith("Pension"), n
    mk_user(conn, "boss@x", "admin")
    ids = {}
    for name in ("Finanstilsynet", "Danske Bank A/S"):
        ids[name] = str(conn.execute("insert into organizations(display_name, country_code) values (%s,'NO') returning id", (name,)).fetchone()["id"])
        conn.execute("insert into account_scores(organization_id, score, band, groups, signal_count, stopped, computed_at) values (%s, 5, 'warm', '{COM}', 1, false, now())", (ids[name],))
    mine = str(conn.execute("insert into organizations(display_name, in_master_list) values ('Statens Pensionsmyndighet Master', true) returning id").fetchone()["id"])
    conn.commit()
    assert tag_public_bodies(conn) == 1
    types = {r["display_name"]: r["entity_type"] for r in conn.execute("select display_name, entity_type from organizations").fetchall()}
    assert types["Finanstilsynet"] == "public_body" and types["Danske Bank A/S"] == "company" and types["Statens Pensionsmyndighet Master"] == "company"      # your own accounts are never reclassified
    conn.commit()
    db = Database(db_url, max_size=2)
    try:
        c = TestClient(create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET), db=db))
        hot = c.get("/api/today", headers={"Authorization": f"Bearer {make_dev_token(SECRET, 'boss@x')}"}).json()["hot_accounts"]
        assert [h["display_name"] for h in hot] == ["Danske Bank A/S"]
    finally:
        db.close()


def test_public_sector_lane_is_its_own_view_and_the_default_book_stays_private(conn, db_url):
    from fastapi.testclient import TestClient
    from ri.api.app import create_app
    from ri.api.auth import make_dev_token
    from ri.db import Database
    from test_r7_verify_discovery_usage import SECRET
    mk_user(conn, "boss@x", "admin")
    conn.execute("insert into organizations(display_name, country_code, entity_type) values ('Synthetic City Council','DE','public_body'), ('Synthetic Private Bank AG','DE','company')")
    conn.commit()
    db = Database(db_url, max_size=2)
    try:
        c = TestClient(create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET), db=db))
        h = {"Authorization": f"Bearer {make_dev_token(SECRET, 'boss@x')}"}
        names = lambda q: sorted(i["name"] for i in c.get(f"/api/leads{q}", headers=h).json()["items"])      # noqa: E731
        assert "Synthetic City Council" not in names("") and "Synthetic Private Bank AG" in names("")
        assert names("?lane=public") == ["Synthetic City Council"]
        assert c.get("/api/leads.csv?lane=public", headers=h).text.count("Synthetic City Council") == 1
        assert c.get("/api/leads?lane=bogus", headers=h).status_code == 422
    finally:
        db.close()
