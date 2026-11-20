"""R5 acceptance: buyer graph, plays, proof governance, drafts, procurement tracking, deal view, outcomes, metrics + end-to-end seller journey."""
import pytest

from ri import outcomes
from ri.buyer import drafts, graph, plays
from ri.deal.progress import deal_view
from ri.metrics import quality_snapshot
from ri.opportunity import engine
from test_r3_opportunity import NOW, env  # noqa: F401  (fixture)
from test_r4_api import SECRET, world  # noqa: F401  (fixture)
from test_r4_crm_notify import mk_user  # noqa: F401


def app_conn(world):
    return world["app"].state.db.tx()


def proof(conn, title, audience, client=None, approved=True):
    return str(conn.execute("insert into proof_assets(title, service_tags, approved_audience, client_name, approval_owner, approved_by) values (%s,'{payments}',%s,%s,%s,%s) returning id",
                            (title, audience, client, "marketing" if audience == "external_approved" else None, "cmo" if audience == "external_approved" else None)).fetchone()["id"])


# ------------------------------------------------------------------ plays + discovery
def test_route_aware_play_selection_and_questions_target_missing_gates(world):
    with app_conn(world) as conn:
        for route, play in (("prime_subcontract", "SP-08"), ("vendor", "SP-05"), ("direct", "SP-05")):
            h = conn.execute("select * from opportunity_hypotheses where id=%s", (world["hyps"][route],)).fetchone()
            sel = plays.select_play(conn, h)
            assert sel["id"] == play, (route, sel["id"])
        h = conn.execute("select * from opportunity_hypotheses where id=%s", (world["hyps"]["prime_subcontract"],)).fetchone()
        qs = plays.discovery_questions(conn, h, plays.select_play(conn, h))
        gates = {q["gate"] for q in qs}
        assert {"G4", "G5", "G6", "G7"} <= gates and any(q["gate"] == "play" for q in qs)


def test_questions_skip_verified_gates(world):
    with app_conn(world) as conn:
        conn.execute("update opportunity_hypotheses set gates = jsonb_set(gates, '{G6}', '{\"status\":\"pass\",\"reason\":\"x\"}') where id=%s", (world["hyps"]["direct"],))
        h = conn.execute("select * from opportunity_hypotheses where id=%s", (world["hyps"]["direct"],)).fetchone()
        assert "G6" not in {q["gate"] for q in plays.discovery_questions(conn, h, plays.select_play(conn, h))}


# ------------------------------------------------------------------ buyer graph
def test_role_first_title_without_person_is_valid_but_neither_is_not(world):
    hid = world["hyps"]["prime_subcontract"]
    with app_conn(world) as conn:
        graph.add_role(conn, hid, "programme_owner", actor="a@x", role_title="Programme Director, Core Banking")
        with pytest.raises(ValueError):
            graph.add_role(conn, hid, "economic_buyer", actor="a@x")
        c = graph.committee(conn, hid)
        assert c["roles"][0]["buyer_authority_inferred"] is True and "economic_buyer" in c["missing"] and "does not prove budget authority" in c["caveat"]


def test_access_ranking_by_verifiability_not_seniority_and_no_fabricated_intro(world):
    hid = world["hyps"]["prime_subcontract"]
    with app_conn(world) as conn:
        graph.add_access_route(conn, hid, "public_event_context", evidence="both spoke at EBAday 2026", actor="a@x")
        graph.add_access_route(conn, hid, "targeted_functional_outreach", evidence="role identified", actor="a@x")
        graph.add_access_route(conn, hid, "official_procurement_channel", evidence="supplier portal lists open call", actor="a@x", verified=True)
        with pytest.raises(ValueError, match="NAMED intermediary"):
            graph.add_access_route(conn, hid, "introduction_via_intermediary", evidence="we probably know someone", actor="a@x")
        with pytest.raises(ValueError):
            graph.add_access_route(conn, hid, "existing_crm_relationship", evidence="  ", actor="a@x")
        ranked = graph.ranked_access(conn, hid)
        assert ranked[0]["kind"] == "official_procurement_channel" and ranked[-1]["kind"] == "public_event_context" and ranked[-1]["context_only"]


def test_suppressed_person_route_excluded_and_verified_role_promotes_employment(world):
    hid = world["hyps"]["prime_subcontract"]
    with app_conn(world) as conn:
        pid = conn.execute("select p.id from people p join employment e on e.person_id=p.id limit 1").fetchone()["id"]
        graph.add_role(conn, hid, "technical_evaluator", actor="a@x", person_id=str(pid), role_is_verified=True, initiative_responsibility_verified=True)
        assert conn.execute("select verification_status from employment where person_id=%s", (pid,)).fetchone()["verification_status"] == "verified"
        graph.add_access_route(conn, hid, "existing_crm_relationship", evidence="met in 2025", actor="a@x", verified=True, person_id=str(pid))
        conn.execute("update people set suppressed=true where id=%s", (pid,))
        assert all(r["kind"] != "existing_crm_relationship" or r["evidence"] != "met in 2025" for r in graph.ranked_access(conn, hid))


# ------------------------------------------------------------------ drafts: governance
def test_draft_for_owner_is_grounded_cited_and_never_contains_email_or_restricted_client(world):
    hid = world["hyps"]["prime_subcontract"]
    with app_conn(world) as conn:
        a_ok = proof(conn, "Payments integration for a European payment institution", "external_approved")
        pid = str(conn.execute("select p.id from people p join employment e on e.person_id=p.id limit 1").fetchone()["id"])
        out = drafts.compose(conn, hid, actor="bdm@x", actor_crm_user=555, person_id=pid, proof_asset_ids=[a_ok])
        assert out["status"] == "draft", out["blocked_reasons"]
        assert "@" not in out["body"] and "Hello Buyer" in out["body"] and "[Signature]" in out["body"] and a_ok in str(conn.execute("select proof_asset_ids from outreach_drafts").fetchone())
        assert conn.execute("select count(*) n from outreach_drafts where status not in ('draft','blocked')").fetchone()["n"] == 0


@pytest.mark.parametrize("scenario", ["other_owner", "unapproved_proof", "suppressed", "hard_blocker", "unlinked"])
def test_draft_hard_blocks(world, scenario):
    hid = world["hyps"]["prime_subcontract"]
    with app_conn(world) as conn:
        pid = str(conn.execute("select p.id from people p join employment e on e.person_id=p.id limit 1").fetchone()["id"])
        actor, proofs = 555, []
        if scenario == "other_owner":
            actor = 666
        elif scenario == "unapproved_proof":
            proofs = [proof(conn, "Bank X core migration", "restricted", client="Secret Bank")]
        elif scenario == "suppressed":
            conn.execute("update people set suppressed=true where id=%s", (pid,))
        elif scenario == "hard_blocker":
            engine.record_hard_block(conn, world["env"]["bank"], "confidentiality_restriction", "NDA forbids contact", "a")
            engine.evaluate(conn, hid, now=NOW, requester_crm_user=555)
        elif scenario == "unlinked":
            conn.execute("update crm_organizations set organization_id=NULL, link_status='unlinked'")
        out = drafts.compose(conn, hid, actor="bdm@x", actor_crm_user=actor, person_id=pid, proof_asset_ids=proofs)
        assert out["status"] == "blocked" and out["body"] is None
        codes = {b["code"] for b in out["blocked_reasons"]}
        expect = {"other_owner": "ownership_conflict", "unapproved_proof": "unapproved_proof", "suppressed": "contact_suppressed", "hard_blocker": "confidentiality_restriction", "unlinked": "ownership_unverified"}[scenario]
        assert expect in codes
        if scenario == "unapproved_proof":
            assert "Secret Bank" not in str(out)


def test_proof_asset_rules_enforced_in_db_and_api(world):
    c, h = world["c"], world["h"]
    r = c.post("/api/proof-assets", json={"title": "x", "approved_audience": "external_approved"}, headers=h("mkt"))
    assert r.status_code == 422                                                                              # approval owner/approver mandatory
    ok = c.post("/api/proof-assets", json={"title": "Approved payments case", "approved_audience": "external_approved", "approval_owner": "Marketing", "approved_by": "CMO", "service_tags": ["payments"]}, headers=h("mkt"))
    assert ok.status_code == 200
    c.post("/api/proof-assets", json={"title": "Internal only case", "approved_audience": "internal_only", "client_name": "Restricted Bank"}, headers=h("mkt"))
    assert len(c.get("/api/proof-assets", headers=h("mkt")).json()["items"]) == 2
    bdm = c.get("/api/proof-assets", headers=h("bdm")).json()["items"]
    assert [x["title"] for x in bdm] == ["Approved payments case"] and "client_name" not in bdm[0]             # sellers only see releasable proof
    assert c.post("/api/proof-assets", json={"title": "x", "approved_audience": "internal_only"}, headers=h("bdm")).status_code == 403


# ------------------------------------------------------------------ deal progression
def test_deal_view_no_deal_is_stated_and_stage_never_auto_promotes(world):
    hid = world["hyps"]["prime_subcontract"]
    with app_conn(world) as conn:
        assert deal_view(conn, hid)["has_open_deal"] is False
        conn.execute("insert into crm_deals(crm_id, crm_org_id, title, owner_id, stage_name, status, update_time) values (1,900,'Atlas',555,'Proposal/Shortlist','open', %s)", (NOW,))
        v = deal_view(conn, hid, NOW)
        assert v["stage_implied_state"] == "proposal_procurement" and v["evidence_gated_state"] != "proposal_procurement"
        assert "No automatic promotion" in v["stage_vs_evidence_gap"] and v["next_step_guidance"]["anti_pattern"]
        assert set(v["missing_stakeholders"]) == {"economic_buyer", "programme_owner", "procurement_owner"}


# ------------------------------------------------------------------ outcomes + learning
def test_outcomes_trace_to_crm_and_small_cohorts_are_flagged_not_concluded(world):
    c, h = world["c"], world["h"]
    hid = world["hyps"]["prime_subcontract"]
    assert c.post(f"/api/hypotheses/{hid}/outcomes", json={"kind": "signed"}, headers=h("bdm")).status_code == 422                 # late-funnel needs a CRM ref
    assert c.post(f"/api/hypotheses/{hid}/outcomes", json={"kind": "discovery_held"}, headers=h("bdm")).status_code == 200
    assert c.post(f"/api/hypotheses/{hid}/outcomes", json={"kind": "signed", "crm_ref": "deal:1"}, headers=h("bdm")).status_code == 200
    f = c.get("/api/admin/funnel", headers=h("admin")).json()
    assert f["cohorts"], f
    cohort = next(iter(f["cohorts"].values()))
    assert cohort["insufficient_sample"] is True and any("causation" in x for x in f["caveats"])
    assert c.get("/api/admin/funnel?group_by=bogus", headers=h("admin")).status_code == 422
    with app_conn(world) as conn:
        ev = conn.execute("select contributions from outcome_events limit 1").fetchone()["contributions"]
        assert {"kind": "route", "ref": "prime_subcontract"} in ev
    assert outcomes.wilson(0, 0) == (0.0, 1.0)
    lo, hi = outcomes.wilson(3, 10)
    assert lo < 0.3 < hi and hi - lo > 0.3                                                                  # wide interval at small n


def test_quality_snapshot_flags_uncited_high_priority(world):
    with app_conn(world) as conn:
        assert quality_snapshot(conn)["high_priority_without_citation"] == 0
        conn.execute("update opportunity_hypotheses set priority='high', supporting_claim_ids='{}' where id=%s", (world["hyps"]["direct"],))
        assert quality_snapshot(conn)["high_priority_without_citation"] == 1
        assert quality_snapshot(conn)["unauthorised_writes_detected"] == 0
    assert "ri_high_priority_without_citation" in world["c"].get("/metrics", headers=world["h"]("admin")).text
    assert world["c"].get("/metrics", headers=world["h"]("bdm")).status_code == 403


# ------------------------------------------------------------------ the definition of done, end to end (spec 15 §12)
def test_seller_journey_open_inspect_prepare_approve_progress_review(world):
    c, h, w = world["c"], world["h"], world["writer"]
    # 1-4. open Today, see meaningful opportunity, master-list membership, original sources and dates
    t = c.get("/api/today", headers=h("bdm")).json()
    cards = [x for s in t["sections"].values() for x in s]
    assert cards
    pick = next(x for x in cards if x["route"]["type"] == "prime_subcontract")
    assert pick["account"]["in_master_list"] is True and pick["evidence"] and pick["evidence"][0]["claim"] is not None
    # 5-7. initiative, workstream, supplier route, uncertainty
    d = c.get(f"/api/hypotheses/{pick['hypothesis_id']}", headers=h("bdm")).json()
    assert d["initiative"]["title"] and d["workstream"] and d["unknowns"] and d["route"]["counterparty"] == "GFT"
    # 8-10. buyer function, suitability, specific next action
    pb = c.get(f"/api/hypotheses/{pick['hypothesis_id']}/playbook", headers=h("bdm")).json()
    assert pb["play"]["id"] == "SP-08" and pb["committee"]["missing"] and pb["meeting_brief"]["assumptions"] and pb["meeting_brief"]["next_action"]["objective"]
    # 11. prepare an approved sales activity: blocked draft until a person/ownership/proof checks pass, then ok
    pid = c.get(f"/api/accounts/{world['env']['bank']}", headers=h("bdm")).json()["buyers"][0]["id"]
    dr = c.post(f"/api/hypotheses/{pick['hypothesis_id']}/draft", json={"person_id": pid}, headers=h("bdm")).json()
    assert dr["status"] == "draft" and dr["source_url"] is None or dr["source_url"]
    # 12. progress in Pipedrive: proposal -> approval (second person) -> executor
    prop = c.post("/api/crm/propose-activity", json={"hypothesis_id": pick["hypothesis_id"], "subject": "Ask GFT alliance lead about specialist subcontracting", "justification": "partner route"}, headers=h("bdm")).json()
    assert c.post(f"/api/approvals/{prop['approval_id']}/decision", json={"approve": True}, headers=h("mgr")).status_code == 200
    assert c.post(f"/api/approvals/{prop['approval_id']}/execute", headers=h("mgr")).json()["status"] == "executed" and len(w.created) == 1
    # 13. outcomes and history
    assert c.post(f"/api/hypotheses/{pick['hypothesis_id']}/outcomes", json={"kind": "recommendation_accepted"}, headers=h("bdm")).status_code == 200
    assert c.post(f"/api/hypotheses/{pick['hypothesis_id']}/decision", json={"decision": "accepted"}, headers=h("bdm")).status_code == 200
    # 14. trust: audit trail captured every step, no unauthorised write
    audit = c.get("/api/admin/audit", headers=h("comp")).json()["items"]
    ops = {a["operation"] for a in audit}
    assert {"approval_requested", "approval_approved", "approval_executed", "crm_activity_created", "outcome_recorded"} <= ops
    assert c.get("/api/admin/metrics", headers=h("admin")).json()["unauthorised_writes_detected"] == 0
