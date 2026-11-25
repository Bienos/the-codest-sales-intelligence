"""Opportunity state machine (spec 07 §5). Event-sourced, evidence-gated, no skipping, no inference from meetings."""
from __future__ import annotations

from typing import Any

from ..audit import audit
from ..db import Conn, jb, q1

ORDER = ["observed", "verified", "relevant", "investigating", "actionable_hypothesis", "engaged", "buyer_need_confirmed",
         "commercially_qualified", "proposal_procurement", "contracted_delivery"]
SIDE = {"monitor", "blocked", "disqualified", "expired", "superseded"}
REOPEN_FROM = {"monitor", "blocked", "expired"}


class TransitionError(Exception):
    pass


LATE_STATES = {"proposal_procurement", "contracted_delivery"}        # a manager or an administrator records these
CRM_EVIDENCE_STATES = {"engaged", "buyer_need_confirmed", "proposal_procurement"}


def crm_ref_exists(conn: Conn, org_id: str, ref: str | None) -> bool:
    """A CRM reference must point at a record in the Pipedrive mirror for THIS account: `activity:<id>` or `deal:<id>`."""
    kind, _, raw = (ref or "").strip().partition(":")
    if not raw.isdigit():
        return False
    table = {"activity": "crm_activities", "a": "crm_activities", "deal": "crm_deals", "d": "crm_deals"}.get(kind.lower())
    if not table:
        return False
    return bool(q1(conn, f"SELECT 1 FROM {table} x JOIN crm_organizations c ON c.crm_id=x.crm_org_id WHERE x.crm_id=%s AND c.organization_id=%s",      # noqa: S608
                   (int(raw), org_id)))


def _checklist(conn: Conn, h: dict[str, Any], target: str, evidence: dict[str, Any]) -> dict[str, bool]:
    g = h["gates"] or {}
    ok = lambda k: (g.get(k) or {}).get("status") == "pass"  # noqa: E731
    if target == "verified":
        return {"supporting_claim": bool(h["supporting_claim_ids"]), "no_hard_blocker": not any(b["scope"] == "hard" for b in h["blockers"])}
    if target == "relevant":
        return {"G1_account": ok("G1"), "G2_initiative": ok("G2"), "G3_engineering": ok("G3")}
    if target == "investigating":
        return {"research_task_or_gap_recorded": bool(q1(conn, "SELECT 1 FROM research_tasks WHERE target_type='hypothesis' AND target_id=%s", (str(h["id"]),))) or bool(h["critical_unknowns"])}
    if target == "actionable_hypothesis":
        return {"gates_G1_G3": all(ok(k) for k in ("G1", "G2", "G3")), "route_not_blocked": h["access_status"] != "blocked",
                "unknowns_recorded": bool(h["critical_unknowns"]) or all(ok(k) for k in g), "has_action": bool(q1(conn, "SELECT 1 FROM recommended_actions WHERE hypothesis_id=%s AND status IN ('proposed','accepted')", (str(h["id"]),)))}
    if target == "engaged":
        return {"buyer_reply_or_crm_activity_evidence": evidence.get("kind") in {"crm_buyer_response", "crm_activity_reply"} and bool(evidence.get("crm_ref")),
                "not_merely_a_meeting_scheduled": evidence.get("kind") != "meeting_scheduled"}
    if target == "buyer_need_confirmed":
        return {"buyer_confirmed_fact": evidence.get("kind") == "buyer_confirmed_fact" and bool(evidence.get("crm_ref") or evidence.get("note_ref")),
                "meeting_held_alone_is_not_enough": evidence.get("kind") != "meeting_held"}
    if target == "commercially_qualified":
        return {"G4_demand": ok("G4"), "G5_supplier_access": ok("G5"), "G6_buyer_path": ok("G6"), "G7_execution": ok("G7"),
                "buyer_confirmed": h["buyer_status"] == "buyer_confirmed" or h["external_demand_status"] == "buyer_confirmed"}
    if target == "proposal_procurement":
        return {"crm_stage_evidence": evidence.get("kind") == "crm_deal_stage" and evidence.get("stage") in {"Proposal/Shortlist", "Commercials/NDA"}}
    if target == "contracted_delivery":
        return {"executed_contract_evidence": evidence.get("kind") == "executed_contract" and bool(evidence.get("contract_ref"))}
    return {}


def transition(conn: Conn, hyp_id: str, new_state: str, *, actor: str, evidence: dict[str, Any] | None = None, reason: str = "",
               controlled_migration: bool = False, new_material_evidence: bool = False, approved_review: bool = False) -> dict[str, Any]:
    h = q1(conn, "SELECT * FROM opportunity_hypotheses WHERE id=%s FOR UPDATE", (hyp_id,))
    if not h:
        raise TransitionError("hypothesis not found")
    cur, evidence = h["state"], evidence or {}
    if new_state == cur:
        return h
    if cur in {"disqualified", "superseded"} and not (approved_review or controlled_migration):
        raise TransitionError(f"{cur} cannot be resurrected without an approved review")
    checklist: dict[str, bool] = {}
    if new_state in SIDE:
        if not reason.strip():
            raise TransitionError("a reason is required for monitor/blocked/disqualified/expired/superseded")
        remember = cur if cur in ORDER else h["prior_active_state"]
        conn.execute("UPDATE opportunity_hypotheses SET state=%s, prior_active_state=%s, updated_at=now() WHERE id=%s", (new_state, remember, hyp_id))
    elif cur in {"disqualified", "superseded"}:
        if new_state not in ORDER or not reason.strip():
            raise TransitionError("an approved review must name the target state and give a reason")
        conn.execute("UPDATE opportunity_hypotheses SET state=%s, updated_at=now() WHERE id=%s", (new_state, hyp_id))
    elif cur in REOPEN_FROM:
        if not (new_material_evidence or approved_review):
            raise TransitionError(f"re-opening from {cur} requires new material evidence or an approved review")
        back = h["prior_active_state"] or "observed"
        if new_state not in ORDER or ORDER.index(new_state) > ORDER.index(back):
            raise TransitionError(f"can only return to {back} or earlier")
        conn.execute("UPDATE opportunity_hypotheses SET state=%s, updated_at=now() WHERE id=%s", (new_state, hyp_id))
    else:
        if cur not in ORDER or new_state not in ORDER:
            raise TransitionError(f"invalid transition {cur} -> {new_state}")
        i, j = ORDER.index(cur), ORDER.index(new_state)
        if j > i + 1 and not controlled_migration:
            raise TransitionError(f"direct promotion {cur} -> {new_state} is forbidden (no skipping)")
        if j > i:
            checklist = _checklist(conn, h, new_state, evidence)
            failed = [k for k, v in checklist.items() if not v]
            if failed and not controlled_migration:
                raise TransitionError(f"requirements unmet for {new_state}: {failed}")
        elif not reason.strip():
            raise TransitionError("moving backwards requires an explicit reason (reversal)")
        conn.execute("UPDATE opportunity_hypotheses SET state=%s, updated_at=now() WHERE id=%s", (new_state, hyp_id))
    conn.execute("INSERT INTO opportunity_transitions(hypothesis_id, prior_state, new_state, actor, evidence_ref, checklist, reason) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                 (hyp_id, cur, new_state, actor, jb(evidence), jb(checklist), reason))
    audit(conn, actor=actor, operation="opportunity_transition", record_type="opportunity_hypothesis", record_id=hyp_id, detail={"from": cur, "to": new_state})
    return q1(conn, "SELECT * FROM opportunity_hypotheses WHERE id=%s", (hyp_id,))  # type: ignore[return-value]
