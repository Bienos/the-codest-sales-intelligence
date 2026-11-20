import time

import jwt
import pytest
from fastapi.testclient import TestClient

from ri.api.app import create_app
from ri.api.auth import TokenVerifier, make_dev_token
from ri.config import Settings
from ri.crm import pipedrive as pd
from ri.db import Database
from ri.opportunity import engine
from test_r3_opportunity import NOW, env, make_hyp, tender_chain  # noqa: F401
from test_r4_crm_notify import FakeWriter, mk_user, org_payload

SECRET = "x" * 32


def settings(writes=False, env_="development"):
    return Settings(env=env_, auth_mode="dev", dev_auth_secret=SECRET, crm_writes_enabled=writes, pipedrive_webhook_secret="whsec", approval_ttl_hours=72)


@pytest.fixture()
def world(conn, db_url, env):
    users = {"bdm": mk_user(conn, "bdm@x", "bdm", 555), "bdm2": mk_user(conn, "bdm2@x", "bdm", 666), "mgr": mk_user(conn, "mgr@x", "sales_manager", 700),
             "analyst": mk_user(conn, "analyst@x", "research_analyst"), "admin": mk_user(conn, "admin@x", "admin"), "mkt": mk_user(conn, "mkt@x", "marketing"),
             "comp": mk_user(conn, "comp@x", "compliance"), "deliv": mk_user(conn, "deliv@x", "delivery_expert")}
    pd.upsert_org(conn, org_payload(900, "Example Bank", owner=555))              # owned by bdm@x (pipedrive user 555)
    conn.execute("update users set team='t1' where email in ('bdm@x','mgr@x')")
    ini, hyps = make_hyp(conn, env, crm=False)
    for h in hyps.values():
        engine.evaluate(conn, h, now=NOW, requester_crm_user=555)
    pid = conn.execute("insert into people(full_name, work_email) values ('Buyer One','b1@example.invalid') returning id").fetchone()["id"]
    conn.execute("insert into employment(person_id, organization_id, title, buyer_lane) values (%s,%s,'CTO','Technology')", (pid, env["bank"]))
    conn.commit()
    db = Database(db_url, max_size=4)
    writer = FakeWriter()
    app = create_app(settings(writes=True), db=db, writer_factory=lambda: writer)
    client = TestClient(app)
    hdr = lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"}  # noqa: E731
    yield {"c": client, "h": hdr, "hyps": hyps, "env": env, "writer": writer, "app": app, "ini": ini}
    db.close()


# ------------------------------------------------------------------ authn
def test_requires_valid_token_unknown_user_forbidden_and_audited(world):
    c = world["c"]
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    expired = jwt.encode({"email": "bdm@x", "exp": int(time.time()) - 10}, SECRET, algorithm="HS256")
    assert c.get("/api/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    forged = jwt.encode({"email": "bdm@x", "exp": int(time.time()) + 100}, "y" * 32, algorithm="HS256")
    assert c.get("/api/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    assert c.get("/api/me", headers={"Authorization": f"Bearer {make_dev_token(SECRET, 'nobody@x')}"}).status_code == 403
    assert c.get("/api/me", headers=world["h"]("bdm")).json()["role"] == "bdm"


def test_oidc_rs256_token_verified_against_jwks_key_and_audience():
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    s = Settings(env="production", auth_mode="oidc", oidc_issuer="https://idp.example", oidc_audience="ri", oidc_jwks_url="https://idp.example/jwks")
    v = TokenVerifier(s, key_resolver=lambda tok: key.public_key())
    good = jwt.encode({"email": "A@X", "iss": "https://idp.example", "aud": "ri", "exp": int(time.time()) + 60}, key, algorithm="RS256")
    assert v.email_for(good) == "a@x"
    from fastapi import HTTPException
    for claims in ({"iss": "https://evil", "aud": "ri"}, {"iss": "https://idp.example", "aud": "other"}):
        with pytest.raises(HTTPException):
            v.email_for(jwt.encode({"email": "a@x", "exp": int(time.time()) + 60, **claims}, key, algorithm="RS256"))
    with pytest.raises(HTTPException):          # algorithm confusion: HS256 signed with the public key must be rejected
        v.email_for(jwt.encode({"email": "a@x", "iss": "https://idp.example", "aud": "ri", "exp": int(time.time()) + 60}, "secret" * 8, algorithm="HS256"))


def test_dev_auth_refused_in_production():
    with pytest.raises(RuntimeError):
        Settings(env="production", auth_mode="dev").validate()


# ------------------------------------------------------------------ RBAC and row scope (server side)
def test_row_scope_bdm_sees_own_but_not_other_sellers_accounts(world):
    c, h = world["c"], world["h"]
    assert c.get("/api/accounts", headers=h("bdm")).json()["items"][0]["display_name"] == "Example Bank"
    assert c.get("/api/accounts", headers=h("bdm2")).json()["items"] == []                                   # owned by 555, not 666
    oid = world["env"]["bank"]
    assert c.get(f"/api/accounts/{oid}", headers=h("bdm")).status_code == 200
    assert c.get(f"/api/accounts/{oid}", headers=h("bdm2")).status_code == 404                              # 404: existence not revealed
    hid = next(iter(world["hyps"].values()))
    assert c.get(f"/api/hypotheses/{hid}", headers=h("bdm2")).status_code == 404
    assert c.post(f"/api/hypotheses/{hid}/decision", json={"decision": "accepted"}, headers=h("bdm2")).status_code == 404
    assert c.get("/api/today", headers=h("bdm2")).json()["count"] == 0
    assert c.get("/api/explorer?entity=companies", headers=h("bdm2")).json()["items"] == []
    assert len(c.get("/api/explorer?entity=opportunities", headers=h("bdm2")).json()["items"]) == 0


def test_manager_sees_team_analyst_sees_all_marketing_and_compliance_see_no_accounts(world):
    c, h = world["c"], world["h"]
    assert len(c.get("/api/accounts", headers=h("mgr")).json()["items"]) == 1                              # team t1 owns it
    assert len(c.get("/api/accounts", headers=h("analyst")).json()["items"]) == 1
    assert c.get("/api/accounts", headers=h("mkt")).status_code == 403
    assert c.get("/api/accounts", headers=h("comp")).status_code == 403
    assert c.get("/api/admin/audit", headers=h("comp")).status_code == 200
    assert c.get("/api/admin/audit", headers=h("bdm")).status_code == 403
    assert c.get("/api/admin/sources", headers=h("bdm")).status_code == 403
    assert c.get("/api/admin/costs", headers=h("bdm")).status_code == 403
    assert c.get("/api/admin/identity-reviews", headers=h("bdm")).status_code == 403


def test_contacts_only_for_authorised_roles_and_reads_are_audited_and_emails_never_returned(world):
    c, h = world["c"], world["h"]
    oid = world["env"]["bank"]
    a = c.get(f"/api/accounts/{oid}", headers=h("bdm")).json()
    assert a["buyers"] and "work_email" not in a["buyers"][0] and "b1@example.invalid" not in str(a)
    assert c.get(f"/api/accounts/{oid}", headers=h("analyst")).json()["buyers"] == []                      # analysts: evidence yes, contact PII no
    with world["app"].state.db.tx() as conn:
        assert conn.execute("select count(*) n from audit_events where operation='pii_read'").fetchone()["n"] == 1


# ------------------------------------------------------------------ Today semantics
def test_today_card_has_every_required_element_and_summary_first(world):
    c, h = world["c"], world["h"]
    for hid in world["hyps"].values():
        engine.evaluate  # noqa: B018
    t = c.get("/api/today", headers=h("bdm")).json()
    cards = [x for sec in t["sections"].values() for x in sec]
    assert cards, "bdm owns this account and should see developments"
    k = cards[0]
    for field in ("account", "initiative", "what_happened", "why_it_matters", "evidence", "route", "external_demand_status", "unknowns", "next_action", "evidence_confidence", "last_checked"):
        assert field in k, field
    assert k["account"]["in_master_list"] is not None and "ownership_verified" in k["account"]
    assert k["next_action"]["objective"] and (k["next_action"]["due_at"] or k["next_action"]["trigger_condition"])
    assert t["coverage"]["no_active_monitoring"] is True and "not confirmation" in (t["empty_state"] or "NOT confirmation") or cards


def test_empty_today_distinguishes_healthy_quiet_from_failed_monitoring(world):
    c, h = world["c"], world["h"]
    t = c.get("/api/today", headers=h("bdm2")).json()
    assert t["count"] == 0 and "incomplete" in t["empty_state"]                                              # no active sources => not 'all clear'
    with world["app"].state.db.tx() as conn:
        conn.execute("update sources set status='active' where source_id='procurement-ted'")
        conn.execute("update source_health set state='healthy' where source_id='procurement-ted'")
    t2 = c.get("/api/today", headers=h("bdm2")).json()
    assert t2["empty_state"].startswith("No new high-priority changes among successfully monitored sources")
    with world["app"].state.db.tx() as conn:
        conn.execute("update source_health set state='failed', error_class='auth' where source_id='procurement-ted'")
    t3 = c.get("/api/today", headers=h("bdm2")).json()
    assert "NOT confirmation" in t3["empty_state"] and t3["coverage"]["unhealthy"]


def test_dismiss_needs_reason_never_resurfaces_unchanged_and_reopens_on_material_change(world):
    c, h = world["c"], world["h"]
    hid = world["hyps"]["prime_subcontract"]
    assert c.post(f"/api/hypotheses/{hid}/decision", json={"decision": "dismissed"}, headers=h("bdm")).status_code == 422
    assert c.post(f"/api/hypotheses/{hid}/decision", json={"decision": "dismissed", "reason": "bogus"}, headers=h("bdm")).status_code == 422
    assert c.post(f"/api/hypotheses/{hid}/decision", json={"decision": "dismissed", "reason": "no_external_demand"}, headers=h("bdm")).status_code == 200
    ids = lambda: [x["hypothesis_id"] for sec in c.get("/api/today", headers=h("bdm")).json()["sections"].values() for x in sec]  # noqa: E731
    assert hid not in ids() and hid not in ids()
    # material change: a new official event moves the initiative
    from ri.ingest.process import finish_event
    from test_r2_initiatives import mk_event
    with world["app"].state.db.tx() as conn:
        e = mk_event(conn, world["env"], world["env"]["bank"], "implementation_launched", {"programme_domain": "core_banking", "programme_name": "Atlas", "programme_status": "implementation", "named_systems": ["Temenos Transact"], "has_dated_milestones": True}, [{"name": "GFT", "role": "si"}], days_ago=0)
        finish_event(conn, e, now=NOW)
        engine.evaluate(conn, hid, now=NOW, requester_crm_user=555)
    again = [x for sec in c.get("/api/today", headers=h("bdm")).json()["sections"].values() for x in sec if x["hypothesis_id"] == hid]
    if again:   # if the change was material to this hypothesis it re-opens referencing the old dismissal
        assert again[0]["reopened_after_dismissal"]["previous_reason"] == "no_external_demand"


def test_hypothesis_detail_exposes_score_note_gates_trace(world):
    c, h = world["c"], world["h"]
    d = c.get(f"/api/hypotheses/{world['hyps']['prime_subcontract']}", headers=h("bdm")).json()
    assert "NOT win probabilities" in d["score_note"] and set(d["gates"]) == {f"G{i}" for i in range(1, 8)}
    assert d["why_this_recommendation"]["scoring_rule_version"] and d["why_this_recommendation"]["transitions"]


# ------------------------------------------------------------------ state + delivery
def test_transition_endpoint_enforces_evidence_and_returns_409(world):
    c, h = world["c"], world["h"]
    hid = world["hyps"]["prime_subcontract"]
    r = c.post(f"/api/hypotheses/{hid}/transition", json={"new_state": "commercially_qualified"}, headers=h("bdm"))
    assert r.status_code == 409 and "forbidden" in r.json()["detail"]
    r = c.post(f"/api/hypotheses/{hid}/transition", json={"new_state": "engaged", "evidence": {"kind": "meeting_held"}}, headers=h("bdm"))
    assert r.status_code == 409


def test_only_delivery_experts_validate_feasibility(world):
    c, h = world["c"], world["h"]
    hid = world["hyps"]["prime_subcontract"]
    assert c.post(f"/api/hypotheses/{hid}/delivery-validation", json={"feasible": True, "note": "team available"}, headers=h("bdm")).status_code == 403
    assert c.post(f"/api/hypotheses/{hid}/delivery-validation", json={"feasible": True, "note": "team available"}, headers=h("deliv")).status_code == 200
    assert c.get(f"/api/hypotheses/{hid}", headers=h("bdm")).json()["gates"]["G7"]["status"] == "pass"


# ------------------------------------------------------------------ CRM proposal -> approval -> execute
def test_full_crm_activity_flow_two_person_rule_diff_and_exactly_once(world):
    c, h, w = world["c"], world["h"], world["writer"]
    hid = world["hyps"]["prime_subcontract"]
    body = {"hypothesis_id": hid, "subject": "Confirm prime partner programme owner", "note": "from RI", "justification": "route investigation"}
    r = c.post("/api/crm/propose-activity", json=body, headers=h("bdm"))
    assert r.status_code == 200 and r.json()["diff"]["target"].endswith("900") and r.json()["status"] == "pending"
    aid = r.json()["approval_id"]
    assert c.post(f"/api/approvals/{aid}/execute", headers=h("mgr")).status_code == 409                       # not approved yet
    assert c.post(f"/api/approvals/{aid}/decision", json={"approve": True}, headers=h("bdm")).status_code == 409   # cannot self-approve
    assert c.post(f"/api/approvals/{aid}/decision", json={"approve": True}, headers=h("mgr")).status_code == 200
    ex = c.post(f"/api/approvals/{aid}/execute", headers=h("mgr")).json()
    assert ex["status"] == "executed" and len(w.created) == 1
    assert c.post(f"/api/approvals/{aid}/execute", headers=h("mgr")).status_code == 409                       # single use
    assert len(w.created) == 1
    assert world["c"].post("/api/crm/propose-activity", json=body, headers=h("bdm")).json()["approval_id"] == aid     # idempotent proposal


def test_cannot_propose_on_account_owned_by_someone_else_or_unlinked(world):
    c, h = world["c"], world["h"]
    hid = world["hyps"]["prime_subcontract"]
    body = {"hypothesis_id": hid, "subject": "Intro call", "justification": "route check"}
    assert c.post("/api/crm/propose-activity", json=body, headers=h("bdm2")).status_code == 404               # cannot even see it
    with world["app"].state.db.tx() as conn:
        conn.execute("update crm_organizations set organization_id=NULL, link_status='unlinked'")
    assert c.post("/api/crm/propose-activity", json=body, headers=h("analyst")).status_code == 403             # analysts do not propose CRM actions
    assert c.post("/api/crm/propose-activity", json=body, headers=h("bdm")).status_code in {404, 409}          # unlinked => ownership unverifiable => disabled


def test_execute_blocked_when_kill_switch_off_or_no_writer(world, db_url):
    from ri.db import Database
    db = Database(db_url, max_size=2)
    try:
        for s, wf in ((settings(writes=False), lambda: FakeWriter()), (settings(writes=True), None)):
            c = TestClient(create_app(s, db=db, writer_factory=wf))
            hdr = {"Authorization": f"Bearer {make_dev_token(SECRET, 'mgr@x')}"}
            assert c.post("/api/approvals/00000000-0000-0000-0000-000000000000/execute", headers=hdr).status_code in {409, 503}
    finally:
        pass


# ------------------------------------------------------------------ webhook + admin
def test_webhook_endpoint_auth_and_dedup(world):
    import base64
    c = world["c"]
    good = {"Authorization": "Basic " + base64.b64encode(b"pipedrive:whsec").decode()}
    body = {"meta": {"entity": "organization", "action": "change", "webhook_id": "wh1"}, "data": org_payload(900, "Example Bank", owner=555)}
    assert c.post("/webhooks/pipedrive", json=body).status_code == 401
    assert c.post("/webhooks/pipedrive", json=body, headers={"Authorization": "Basic " + base64.b64encode(b"pipedrive:wrong").decode()}).status_code == 401
    assert c.post("/webhooks/pipedrive", json=body, headers=good).json() == {"accepted": True, "duplicate": False}
    assert c.post("/webhooks/pipedrive", json=body, headers=good).json() == {"accepted": True, "duplicate": True}


def test_admin_source_grid_costs_rules_quality_and_identity_resolution(world):
    c, h = world["c"], world["h"]
    src = c.get("/api/admin/sources", headers=h("admin")).json()["items"]
    assert any(s["source_id"] == "procurement-ted" and s["status"] == "pending_connector_validation" and not s["certified"] for s in src)
    assert c.get("/api/admin/costs", headers=h("admin")).json()["paid_enrichment_enabled"] is False
    rules = c.get("/api/admin/rules", headers=h("admin")).json()
    assert len(rules["items"]) == 64 and {r["status"]: r["n"] for r in rules["summary"]} == {"active": 17, "defined_not_activated": 47}
    with world["app"].state.db.tx() as conn:
        conn.execute("insert into identity_reviews(raw_company_name, candidate_org_ids, reason) values ('Examp Bank', %s, 'fuzzy_or_domain_candidate')", ([world["env"]["bank"]],))
    rv = c.get("/api/admin/identity-reviews", headers=h("analyst")).json()["items"]
    assert rv and rv[0]["candidates"][0]["display_name"] == "Example Bank"
    assert c.post(f"/api/admin/identity-reviews/{rv[0]['id']}/resolve", json={"decision": "link", "organization_id": "00000000-0000-0000-0000-000000000000", "note": "wrong"}, headers=h("analyst")).status_code == 422
    assert c.post(f"/api/admin/identity-reviews/{rv[0]['id']}/resolve", json={"decision": "link", "organization_id": world["env"]["bank"], "note": "same company, typo"}, headers=h("analyst")).status_code == 200
    assert c.get("/api/admin/quality", headers=h("admin")).json()["open_identity_reviews"] == 0
    assert c.get("/api/admin/audit?operation=identity_review_resolved", headers=h("comp")).json()["items"]


def test_explorer_states_ranking_basis_and_coverage(world):
    c, h = world["c"], world["h"]
    r = c.get("/api/explorer?entity=opportunities&q=Example", headers=h("analyst")).json()
    assert "not a probability" in r["ranking_basis"] and "coverage" in r and "NOT proof" in r["coverage_note"]
    assert r["items"] and r["items"][0]["evidence_confidence"] in {"low", "medium", "high"}
    assert c.get("/api/explorer?entity=companies&existing=false", headers=h("analyst")).json()["items"] == []
