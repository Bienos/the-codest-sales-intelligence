"""R3 acceptance: waterfall gating, approvals/credit controls, decision engine, state machine, counterevidence, actions, planner."""
from datetime import datetime, timedelta, timezone

import pytest

from ri import budget
from ri.approvals import ApprovalError, decide, payload_hash, request, validate_for_execution
from ri.enrichment.providers import ApolloProvider, FakeProvider, enrich_person_with_approval
from ri.enrichment.waterfall import run_waterfall
from ri.ingest.process import finish_event
from ri.opportunity import engine, states
from ri.opportunity.review import review
from ri.research import planner
from ri.signals.baseline import import_baseline
from ri.signals.persist import sync_rule_versions
from ri.sources import registry
from ri.sources.base import BudgetExceeded
from test_r2_initiatives import CORE, OTHERS, mk_event

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def env(conn):
    import_baseline(conn); sync_rule_versions(conn); registry.seed_registry(conn)
    bank = str(conn.execute("insert into organizations(display_name,country_code,employees_estimate,employees_basis,segment,in_master_list) "
                            "values ('Example Bank','GB',60000,'sheet_recorded','Banking',true) returning id").fetchone()["id"])
    conn.commit()
    return {"bank": bank, "n": [0]}


def make_hyp(conn, env, route="prime_subcontract", crm=False, owner=555):
    """Core-banking programme: bank + Temenos (vendor) + GFT (SI with dev scope)."""
    e1 = mk_event(conn, env, env["bank"], "vendor_selected", {**CORE, "counterparty_selected": True, "programme_name": "Atlas"}, [OTHERS[0]])
    e2 = mk_event(conn, env, env["bank"], "vendor_selected", {**CORE, "counterparty_selected": True, "award_scope_includes_dev": True, "programme_name": "Atlas"}, [OTHERS[1]])
    r = finish_event(conn, e1, now=NOW); finish_event(conn, e2, now=NOW)
    if crm:
        conn.execute("insert into crm_organizations(crm_id, organization_id, name, owner_id, owner_name, link_status) values (900, %s, 'Example Bank', %s, 'Owner Rep', 'linked')", (env["bank"], owner))
    ids = engine.build_for_initiative(conn, r["initiative_id"])
    hyps = {x["route_type"]: str(x["id"]) for x in conn.execute("select id, route_type from opportunity_hypotheses")}
    return r["initiative_id"], hyps


# ------------------------------------------------------------------- one hypothesis per route, never triple counted
def test_three_routes_three_hypotheses_none_confirmed(conn, env):
    ini, hyps = make_hyp(conn, env)
    assert set(hyps) == {"direct", "vendor", "prime_subcontract"}
    for h in hyps.values():
        out = engine.evaluate(conn, h, now=NOW)
        assert out["demand"] == "unknown"                     # D3 programme evidence is NOT external demand
    states_ = {r["state"] for r in conn.execute("select state from opportunity_hypotheses")}
    assert states_ <= {"observed", "verified", "relevant", "investigating", "actionable_hypothesis"}
    assert not states_ & {"engaged", "buyer_need_confirmed", "commercially_qualified"}


def test_si_award_restricts_direct_but_partner_route_gets_investigation_action(conn, env):
    ini, hyps = make_hyp(conn, env)
    d = engine.evaluate(conn, hyps["direct"], now=NOW)
    p = engine.evaluate(conn, hyps["prime_subcontract"], now=NOW)
    assert d["gates"]["G5"]["status"] == "fail" and "restricted" in d["gates"]["G5"]["reason"]
    assert p["action"] == "investigate_partner_route"
    assert engine.evaluate(conn, hyps["vendor"], now=NOW)["action"] == "investigate_vendor_route"


def test_ai_investment_without_external_route_never_commercially_qualified(conn, env):
    e = mk_event(conn, env, env["bank"], "programme_announced", {"programme_domain": "ai_use_case", "named_systems": ["doc-intelligence"], "programme_status": "implementation"})
    r = finish_event(conn, e, now=NOW)
    engine.build_for_initiative(conn, r["initiative_id"])
    h = str(conn.execute("select id from opportunity_hypotheses").fetchone()["id"])
    out = engine.evaluate(conn, h, now=NOW)
    assert out["gates"]["G4"]["status"] == "unknown" and out["action"] == "qualify_external_demand"
    with pytest.raises(states.TransitionError):
        states.transition(conn, h, "commercially_qualified", actor="u")        # skip forbidden
    cur = conn.execute("select state from opportunity_hypotheses").fetchone()["state"]
    assert cur == "actionable_hypothesis"
    with pytest.raises(states.TransitionError):
        states.transition(conn, h, "engaged", actor="u", evidence={"kind": "meeting_scheduled"})


# ------------------------------------------------------------------- scoring is explicit, separate from confidence
def test_score_is_rule_based_bounded_and_not_a_probability(conn, env):
    ini, hyps = make_hyp(conn, env)
    out = engine.evaluate(conn, hyps["prime_subcontract"], now=NOW)
    row = conn.execute("select score_json, score_total, evidence_confidence, research_completeness from opportunity_hypotheses where id=%s", (hyps["prime_subcontract"],)).fetchone()
    assert sum(v["points"] for v in row["score_json"].values()) == row["score_total"] <= 100
    assert all(v["points"] <= v["max"] for v in row["score_json"].values())
    assert {"icp", "engineering_relevance", "external_demand", "access", "timing", "relationship_delivery"} == set(row["score_json"])
    assert row["evidence_confidence"] in {"low", "medium", "high"} and 0 <= float(row["research_completeness"]) <= 1


def test_icp_boundary_estimate_is_penalised_and_flagged(conn, env):
    conn.execute("update organizations set employees_estimate=498, employees_basis='provider_estimate', employees_uncertain=true, segment='Payments software' where id=%s", (env["bank"],))
    ini, hyps = make_hyp(conn, env)
    out = engine.evaluate(conn, hyps["prime_subcontract"], now=NOW)
    assert out["gates"]["G1"]["status"] == "unknown"
    assert "boundary uncertain" in conn.execute("select score_json->'icp'->>'why' w from opportunity_hypotheses where id=%s", (hyps["prime_subcontract"],)).fetchone()["w"]


# ------------------------------------------------------------------- blockers override ranking
def test_hard_blockers_override_score_and_block_state(conn, env):
    ini, hyps = make_hyp(conn, env)
    h = hyps["prime_subcontract"]
    engine.evaluate(conn, h, now=NOW)
    engine.record_hard_block(conn, env["bank"], "ineligible_tender", "requires national security clearance Codest lacks", "analyst")
    out = engine.evaluate(conn, h, now=NOW)
    assert out["priority"] == "blocked" and out["action"] == "monitor"
    assert conn.execute("select state from opportunity_hypotheses where id=%s", (h,)).fetchone()["state"] == "blocked"
    with pytest.raises(states.TransitionError):
        states.transition(conn, h, "actionable_hypothesis", actor="u")                  # no reopening without new evidence/review
    assert conn.execute("select count(*) c from signals where status='active'").fetchone()["c"] >= 1      # positives preserved


def test_cancelled_programme_blocks_all_routes(conn, env):
    ini, hyps = make_hyp(conn, env)
    finish_event(conn, mk_event(conn, env, env["bank"], "negative_cancellation", {"programme_status": "cancelled", "programme_domain": "core_banking", "programme_name": "Atlas"}, days_ago=1), now=NOW)
    for h in hyps.values():
        out = engine.evaluate(conn, h, now=NOW)
        assert out["priority"] == "blocked" and any(b["type"] == "cancelled_workstream" for b in out["blockers"])


def test_ownership_conflict_routes_to_owner_and_never_outreach(conn, env):
    ini, hyps = make_hyp(conn, env, crm=True, owner=555)
    out = engine.evaluate(conn, hyps["prime_subcontract"], now=NOW, requester_crm_user=777)      # requester is not the owner
    assert out["priority"] == "blocked" and out["action"] == "route_to_owner"
    act = conn.execute("select owner_id, action_type from recommended_actions where status='proposed' and hypothesis_id=%s", (hyps["prime_subcontract"],)).fetchone()
    assert act["owner_id"] == 555
    # the owner themselves is not blocked by the ownership gate
    out2 = engine.evaluate(conn, hyps["prime_subcontract"], now=NOW, requester_crm_user=555)
    assert not any(b["type"] == "ownership_conflict" for b in out2["blockers"])


def test_unknown_ownership_restricts_to_research_actions_with_visible_note(conn, env):
    ini, hyps = make_hyp(conn, env, crm=False)
    engine.evaluate(conn, hyps["direct"], now=NOW)
    a = conn.execute("select owner_id, owner_note from recommended_actions where hypothesis_id=%s and status='proposed'", (hyps["direct"],)).fetchone()
    assert a["owner_id"] is None and "Ownership unverified" in a["owner_note"]


def test_crm_history_does_not_override_opt_out(conn, env):
    ini, hyps = make_hyp(conn, env, crm=True)
    p = conn.execute("insert into people(full_name, suppressed, suppression_reason) values ('Opted Out','true','opt-out') returning id").fetchone()["id"]
    conn.execute("insert into employment(person_id, organization_id, title, buyer_lane, verification_status) values (%s,%s,'CTO','Technology','verified')", (p, env["bank"]))
    out = engine.evaluate(conn, hyps["prime_subcontract"], now=NOW, requester_crm_user=555)
    assert any(b["type"] == "opt_out" for b in out["blockers"]) and out["priority"] == "blocked"


# ------------------------------------------------------------------- live tender: urgent only when live
def tender_chain(conn, env, deadline):
    e = mk_event(conn, env, env["bank"], "procurement_published", {"notice_id": "N-1", "notice_status": "open", "buyer_entity": "Example Bank", "scope": "Software development services",
                                                                "authoritative_url": "https://e.invalid/n", "submission_deadline": deadline.isoformat(), "relevant_services": True,
                                                                "programme_domain": "core_banking", "named_systems": ["Temenos"], "source_tier": "S1"})
    conn.execute("insert into procurement_notices(source_id,notice_id,buyer_name,buyer_org_id,title,status,deadline_at,deadline_tz,last_status_check_at) "
                 "values ('procurement-ted','N-1','Example Bank',%s,'Software development services','open',%s,'+01:00',%s)", (env["bank"], deadline, NOW))
    r = finish_event(conn, e, now=NOW)
    engine.build_for_initiative(conn, r["initiative_id"])
    return str(conn.execute("select id from opportunity_hypotheses where route_type='procurement'").fetchone()["id"])


def test_live_tender_is_urgent_with_bid_no_bid_action_and_deadline_trigger(conn, env):
    h = tender_chain(conn, env, NOW + timedelta(days=9))
    out = engine.evaluate(conn, h, now=NOW)
    assert out["demand"] == "explicit" and out["priority"] == "urgent" and out["action"] == "bid_no_bid_review"
    a = conn.execute("select due_at, reason from recommended_actions where hypothesis_id=%s and status='proposed'", (h,)).fetchone()
    assert a["due_at"] is not None and "+01:00" in a["reason"] and "rechecked" in a["reason"]


def test_expired_tender_is_never_live_action(conn, env):
    h = tender_chain(conn, env, NOW + timedelta(days=9))
    engine.evaluate(conn, h, now=NOW)
    out = engine.evaluate(conn, h, now=NOW + timedelta(days=20))               # deadline now in the past
    assert out["priority"] == "blocked" and any(b["type"] == "expired_procurement" for b in out["blockers"])
    assert out["action"] == "monitor"


# ------------------------------------------------------------------- counterevidence + high-priority completeness
def test_high_priority_has_citations_counterevidence_unknowns_route_action(conn, env):
    h = tender_chain(conn, env, NOW + timedelta(days=40))
    engine.evaluate(conn, h, now=NOW)
    row = conn.execute("select * from opportunity_hypotheses where id=%s", (h,)).fetchone()
    assert row["priority"] in {"high", "urgent", "medium"}
    if row["priority"] in {"high", "urgent"}:
        r = row["review_json"]
        assert r["unknowns"] and r["falsifiers"] and {"supports", "contradicts"} <= set(r)
        assert row["supporting_claim_ids"], "high priority must cite claims"
    assert conn.execute("select count(*) c from recommended_actions where hypothesis_id=%s and status='proposed'", (h,)).fetchone()["c"] == 1


def test_model_reviewer_can_only_downgrade(conn, env):
    from ri.opportunity import model
    h = tender_chain(conn, env, NOW + timedelta(days=9))
    f = model.collect(conn, h, NOW)
    class Optimist:
        def review(self, pack): return {"supports": [{"question": "q", "detail": "looks great", "claim_ids": []}], "contradicts": [], "unknowns": []}
    base = review(f)
    with_model = review(f, model_reviewer=Optimist())
    assert len(with_model["supports"]) == len(base["supports"])             # model 'supports' ignored
    class Skeptic:
        def review(self, pack): return {"contradicts": [{"question": "q", "detail": "x", "claim_ids": ["c1"]}, {"question": "uncited", "detail": "y"}]}
    r = review(f, model_reviewer=Skeptic())
    assert r["confidence_impact"] == "downgrade" and len(r["contradicts"]) == len(base["contradicts"]) + 1      # uncited contradiction dropped


def test_one_active_action_enforced_by_db_and_actions_need_due_or_trigger(conn, env):
    import psycopg
    ini, hyps = make_hyp(conn, env)
    h = hyps["direct"]
    engine.evaluate(conn, h, now=NOW)
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("insert into recommended_actions(hypothesis_id,action_type,objective,reason,trigger_condition,expected_outcome,stop_condition) values (%s,'x','o','r','t','e','s')", (h,))
    conn.rollback()


def test_action_check_constraint_requires_due_or_trigger(conn, env):
    import psycopg
    ini, hyps = make_hyp(conn, env)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("insert into recommended_actions(hypothesis_id,action_type,objective,reason,expected_outcome,stop_condition,status) values (%s,'x','o','r','e','s','done')", (hyps["direct"],))


def test_reevaluate_on_initiative_revision_supersedes_stale_conclusion(conn, env):
    ini, hyps = make_hyp(conn, env)
    h = hyps["direct"]
    engine.evaluate(conn, h, now=NOW)
    a1 = conn.execute("select id, action_type from recommended_actions where hypothesis_id=%s and status='proposed'", (h,)).fetchone()
    finish_event(conn, mk_event(conn, env, env["bank"], "programme_completed", {"programme_domain": "core_banking", "programme_status": "completed", "programme_name": "Atlas"}, days_ago=1), now=NOW)
    # the pipeline now re-evaluates the moment the programme moves, so nothing is left stale for the sweep
    assert engine.evaluate_stale(conn, NOW) == 0
    a2 = conn.execute("select action_type from recommended_actions where hypothesis_id=%s and status='proposed'", (h,)).fetchone()
    assert a2["action_type"] == "monitor" and a1["action_type"] != "monitor"
    assert conn.execute("select status from recommended_actions where id=%s", (a1["id"],)).fetchone()["status"] == "superseded"


# ------------------------------------------------------------------- state machine
def test_state_machine_evidence_gates_reversal_reopen_and_audit(conn, env):
    ini, hyps = make_hyp(conn, env)
    h = hyps["prime_subcontract"]
    engine.evaluate(conn, h, now=NOW)
    assert conn.execute("select state from opportunity_hypotheses where id=%s", (h,)).fetchone()["state"] == "actionable_hypothesis"
    for bad in ({"kind": "meeting_held"}, {}, {"kind": "crm_buyer_response"}):
        with pytest.raises(states.TransitionError):
            states.transition(conn, h, "engaged", actor="bdm", evidence=bad)
    states.transition(conn, h, "engaged", actor="bdm", evidence={"kind": "crm_buyer_response", "crm_ref": "activity:1"})
    with pytest.raises(states.TransitionError):
        states.transition(conn, h, "buyer_need_confirmed", actor="bdm", evidence={"kind": "meeting_held", "crm_ref": "a:2"})
    states.transition(conn, h, "buyer_need_confirmed", actor="bdm", evidence={"kind": "buyer_confirmed_fact", "crm_ref": "note:7"})
    with pytest.raises(states.TransitionError) as ei:
        states.transition(conn, h, "commercially_qualified", actor="bdm")
    assert "G" in str(ei.value)                                                      # critical buying/feasibility gates unresolved
    with pytest.raises(states.TransitionError):
        states.transition(conn, h, "engaged", actor="bdm")                           # reversal needs a reason
    states.transition(conn, h, "engaged", actor="bdm", reason="buyer retracted")
    states.transition(conn, h, "monitor", actor="bdm", reason="parked until Q2")
    with pytest.raises(states.TransitionError):
        states.transition(conn, h, "engaged", actor="bdm")
    states.transition(conn, h, "engaged", actor="bdm", new_material_evidence=True)
    n = conn.execute("select count(*) c from opportunity_transitions where hypothesis_id=%s", (h,)).fetchone()["c"]
    assert n >= 6 and conn.execute("select count(*) c from audit_events where operation='opportunity_transition'").fetchone()["c"] >= n - 4


def test_transitions_are_append_only(conn, env):
    import psycopg
    ini, hyps = make_hyp(conn, env)
    engine.evaluate(conn, hyps["direct"], now=NOW)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("delete from opportunity_transitions")


def test_disqualified_cannot_be_resurrected_without_review(conn, env):
    ini, hyps = make_hyp(conn, env)
    h = hyps["direct"]
    states.transition(conn, h, "disqualified", actor="analyst", reason="out of ICP")
    with pytest.raises(states.TransitionError):
        states.transition(conn, h, "observed", actor="analyst", new_material_evidence=True)
    states.transition(conn, h, "observed", actor="manager", approved_review=True, reason="manager review overturns")


# ------------------------------------------------------------------- waterfall gates
def test_account_without_relevant_workstream_stops_early_and_spends_nothing(conn, env):
    res = run_waterfall(conn, org_id=env["bank"], initiative_id=None, until="E6")
    assert res.reached == "E2" and res.stop_reason in {"no_relevant_initiative_or_relationship", "no_initiative"}
    assert conn.execute("select coalesce(sum(spent+reserved),0) s from provider_budgets").fetchone()["s"] == 0


def test_waterfall_requires_human_for_e5_and_approval_for_e6(conn, env):
    ini, hyps = make_hyp(conn, env)
    assert run_waterfall(conn, org_id=env["bank"], initiative_id=ini, until="E6").stop_reason == "e5_requires_human_accepted_research_candidate"
    assert run_waterfall(conn, org_id=env["bank"], initiative_id=ini, until="E6", accepted_by_human=True).stop_reason == "e6_requires_explicit_prior_approval"


def test_identity_review_blocks_enrichment_at_e1(conn, env):
    conn.execute("insert into identity_reviews(raw_company_name, candidate_org_ids, reason) values ('Example Bank', %s, 'ambiguous_match')", ([env["bank"]],))
    assert run_waterfall(conn, org_id=env["bank"], initiative_id=None).stop_reason == "identity_review_open"


def test_unknown_field_states_are_distinct_never_guessed(conn, env):
    run_waterfall(conn, org_id=env["bank"], initiative_id=None, until="E2")
    rows = {r["field"]: r["state"] for r in conn.execute("select field, state from enrichment_fields")}
    assert rows["crm_owner"] == "unknown" and rows["legal_identity"] == "unknown"


# ------------------------------------------------------------------- approvals / contact credits
def approved(conn, kind="contact_enrichment", target="person-1", op="enrich_person", payload=None, requester="bdm1", approver="mgr1", role="sales_manager", key="a1", ttl=72):
    a = request(conn, kind=kind, target_system="fake", target_id=target, operation=op, payload=payload or {"provider": "fake", "person_external_id": "p1", "credits": 1.0},
                requested_by=requester, justification="verified initiative, owner cleared", idempotency_key=key, ttl_hours=ttl)
    return decide(conn, str(a["id"]), approver=approver, approver_role=role, approve=True)


def test_no_enrichment_without_approval_and_provider_never_called(conn):
    p = FakeProvider()
    budget.set_cap(conn, "fake", "contact_enrichment", "2026-11", 10)
    with pytest.raises(ApprovalError):
        enrich_person_with_approval(conn, p, person_external_id="p1", person_id="person-1", approval_id=None, period="2026-11", key="k")
    assert p.calls == []


def test_approved_enrichment_is_single_use_and_bound_to_one_person(conn):
    p = FakeProvider()
    budget.set_cap(conn, "fake", "contact_enrichment", "2026-11", 10)
    a = approved(conn)
    r = enrich_person_with_approval(conn, p, person_external_id="p1", person_id="person-1", approval_id=str(a["id"]), period="2026-11", key="k1")
    assert r.work_email == "synthetic@example.invalid" and p.calls == ["enrich_person"]
    with pytest.raises(ApprovalError):                                       # re-use (e.g. a bulk job) is rejected
        enrich_person_with_approval(conn, p, person_external_id="p1", person_id="person-1", approval_id=str(a["id"]), period="2026-11", key="k2")
    b = approved(conn, key="a2")
    with pytest.raises(ApprovalError):                                       # approval for person-1 cannot enrich person-2
        enrich_person_with_approval(conn, p, person_external_id="p2", person_id="person-2", approval_id=str(b["id"]), period="2026-11", key="k3")
    assert p.calls == ["enrich_person"] and budget.remaining(conn, "fake", "contact_enrichment", "2026-11") == 9.0


def test_enrichment_respects_hard_budget_even_with_approval(conn):
    p = FakeProvider(credits=5.0)
    budget.set_cap(conn, "fake", "contact_enrichment", "2026-11", 3)
    a = approved(conn, payload={"provider": "fake", "person_external_id": "p1", "credits": 5.0})
    with pytest.raises(BudgetExceeded):
        enrich_person_with_approval(conn, p, person_external_id="p1", person_id="person-1", approval_id=str(a["id"]), period="2026-11", key="k")
    assert p.calls == []


def test_unknown_provider_price_cannot_be_spent(conn):
    import httpx
    ap = ApolloProvider(httpx.AsyncClient(), "dummy-key")
    with pytest.raises(ApprovalError, match="price unknown"):
        enrich_person_with_approval(conn, ap, person_external_id="x", person_id="p", approval_id="x", period="2026-11", key="k")


def test_approval_rules_two_person_expiry_mismatch_tamper(conn):
    with pytest.raises(ApprovalError, match="own"):
        approved(conn, kind="crm_write", requester="bdm1", approver="bdm1", role="bdm", key="s1")
    a = approved(conn, kind="crm_write", target="crm:org:1", op="create_activity", payload={"subject": "x"}, key="c1")
    with pytest.raises(ApprovalError, match="payload differs"):
        validate_for_execution(conn, str(a["id"]), kind="crm_write", target_system="fake", target_id="crm:org:1", operation="create_activity", payload={"subject": "TAMPERED"})
    with pytest.raises(ApprovalError, match="target_id mismatch"):
        validate_for_execution(conn, str(a["id"]), kind="crm_write", target_system="fake", target_id="crm:org:2", operation="create_activity", payload={"subject": "x"})
    validate_for_execution(conn, str(a["id"]), kind="crm_write", target_system="fake", target_id="crm:org:1", operation="create_activity", payload={"subject": "x"})
    exp = request(conn, kind="crm_write", target_system="fake", target_id="t", operation="o", payload={"a": 1}, requested_by="u", justification="j", idempotency_key="e1", ttl_hours=1)
    with pytest.raises(ApprovalError, match="expired"):
        decide(conn, str(exp["id"]), approver="m", approver_role="sales_manager", approve=True, now=NOW + timedelta(days=400))
    with pytest.raises(ApprovalError):
        request(conn, kind="crm_write", target_system="fake", target_id="t", operation="o", payload={"a": 2}, requested_by="u", justification="j", idempotency_key="e1")   # key reuse w/ different payload
    assert payload_hash({"b": 1, "a": 2}) == payload_hash({"a": 2, "b": 1})


# ------------------------------------------------------------------- research planner
def test_planner_skips_buyer_only_questions_and_never_repeats(conn, env):
    ini, hyps = make_hyp(conn, env)
    h = hyps["prime_subcontract"]
    engine.evaluate(conn, h, now=NOW)
    step, why = planner.plan_next(conn, h, remaining_budget=5)
    assert why == "resolvable_only_by_buyer" and step is None                # G4 first: a buyer question, not web research
    s2, w2 = planner.plan_next(conn, h, remaining_budget=5)
    assert s2 is not None and s2.gate == "G5" and s2.decision_effect == "route"
    s3, w3 = planner.plan_next(conn, h, remaining_budget=5)
    assert s3.gate == "G6"                                                    # G5 already queued => not repeated


def test_planner_budget_exhaustion_and_hard_block(conn, env):
    ini, hyps = make_hyp(conn, env)
    h = hyps["prime_subcontract"]
    engine.evaluate(conn, h, now=NOW)
    planner.plan_next(conn, h, remaining_budget=0)                            # buyer-only skip
    assert planner.plan_next(conn, h, remaining_budget=0) == (None, "budget_exceeded")
    engine.record_hard_block(conn, env["bank"], "unapproved_claim", "x", "a")
    engine.evaluate(conn, h, now=NOW)
    assert planner.plan_next(conn, h, remaining_budget=99)[1] == "hard_blocker"


def test_planner_execution_is_budget_capped_and_diminishing_returns_stop(conn, env):
    ini, hyps = make_hyp(conn, env)
    h = hyps["prime_subcontract"]
    engine.evaluate(conn, h, now=NOW)
    planner.plan_next(conn, h, remaining_budget=5)
    step, _ = planner.plan_next(conn, h, remaining_budget=5)
    assert planner.execute(conn, h, step, lambda s: {"cost": 1.0}, period=h)["reason"] == "budget_exceeded"     # no cap configured => cap 0
    budget.set_cap(conn, "exa", "hypothesis", h, 5.0)
    conn.execute("update research_tasks set status='planned', stop_reason=null where status='budget_exceeded'")
    out = planner.execute(conn, h, step, lambda s: {"cost": 0.5, "changed_decision": False}, period=h)
    assert out["cost"] == 0.5 and budget.remaining(conn, "exa", "hypothesis", h) == 4.5
    for i in range(3):
        conn.execute("insert into research_tasks(target_type,target_id,research_question,evidence_gap,decision_effect,method,status,stop_reason,idempotency_key) values ('hypothesis',%s,%s,'g','route','targeted_retrieval','completed','no_decision_change',%s)", (h, f"q{i}", f"dim{i}"))
    assert planner.plan_next(conn, h, remaining_budget=99)[1] == "diminishing_value"
    def boom(s): raise TimeoutError()
    conn.execute("update research_tasks set status='planned' where id=(select id from research_tasks where research_question=%s)", (step.question,))
    with pytest.raises(TimeoutError):
        planner.execute(conn, h, step, boom, period=h)
    assert budget.remaining(conn, "exa", "hypothesis", h) == 4.5              # reservation released on provider failure


def test_new_company_outside_master_lists_enters_separate_workflow(conn, env):
    from ri.identity.discovery import register_discovered_company
    oid, st = register_discovered_company(conn, name="Brand New Fintech Ltd", country="SE", domain="newfintech.example", event_id=None, employees=700)
    assert st == "new_candidate_pending_qualification"
    assert conn.execute("select in_master_list from organizations where id=%s", (oid,)).fetchone()["in_master_list"] is False
    assert conn.execute("select status from new_company_candidates where organization_id=%s", (oid,)).fetchone()["status"] == "pending_qualification"
    again, st2 = register_discovered_company(conn, name="Example Bank", country="GB", domain=None, event_id=None)
    assert again == env["bank"] and st2 == "existing_account"                  # existing accounts are never duplicated


def test_verified_programme_without_demand_or_route_footing_is_never_high(conn, env):
    ini, hyps = make_hyp(conn, env)
    for h in hyps.values():
        out = engine.evaluate(conn, h, now=NOW)
        assert out["demand"] == "unknown" and out["priority"] in {"medium", "low", "blocked"}, out["priority"]
    # a live official tender provides explicit demand => may rank high/urgent
    t = tender_chain(conn, env, NOW + timedelta(days=40))
    assert engine.evaluate(conn, t, now=NOW)["priority"] in {"high", "urgent", "medium"}


def test_sweep_still_catches_a_programme_revised_outside_the_pipeline(conn, env):
    ini, hyps = make_hyp(conn, env)
    h = hyps["direct"]
    engine.evaluate(conn, h, now=NOW)
    conn.execute("update initiatives set revision = revision + 1 where id=%s", (ini,))      # e.g. an analyst edit
    assert engine.evaluate_stale(conn, NOW) >= 1
    assert engine.evaluate_stale(conn, NOW) == 0


def test_verified_event_becomes_an_opportunity_automatically_and_maintenance_is_a_safety_net(conn, env):
    from ri.scheduler import run_maintenance
    e1 = mk_event(conn, env, env["bank"], "vendor_selected", {**CORE, "counterparty_selected": True, "programme_name": "Atlas"}, [OTHERS[0]])
    out = finish_event(conn, e1, now=NOW)
    assert out["initiative_id"] and out["hypotheses"]                                      # no manual build step
    n = conn.execute("select count(*) c from opportunity_hypotheses where last_checked_at is not null").fetchone()["c"]
    assert n >= 1
    conn.execute("TRUNCATE recommended_actions, opportunity_transitions, hypothesis_seen, hypothesis_user_state, opportunity_hypotheses CASCADE")
    m = run_maintenance(conn, NOW)                                                          # a run that was interrupted is repaired by the scheduler
    assert m["hypotheses_built"] >= 1
    assert conn.execute("select count(*) c from opportunity_hypotheses").fetchone()["c"] >= 1


def test_a_small_bank_fails_the_icp_gate_until_an_exception_is_approved(conn, env):
    conn.execute("update organizations set employees_estimate=2500, employees_uncertain=false, segment='Retail bank' where id=%s", (env["bank"],))
    ini, hyps = make_hyp(conn, env)
    out = engine.evaluate(conn, hyps["prime_subcontract"], now=NOW)
    assert out["gates"]["G1"]["status"] == "fail" and "bank below the 5,000" in out["gates"]["G1"]["reason"] and "approved exception" in out["gates"]["G1"]["reason"]
