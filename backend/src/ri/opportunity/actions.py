"""Next-best-action policy (spec 07 §9). ONE action that reduces a real commercial bottleneck, with owner, objective,
reason, evidence, due/trigger and stop condition. Never a generic 'follow up'. Ownership/suppression are checked first."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from ..db import Conn, q1
from .model import Facts


def decide(f: Facts, gates: dict[str, Any], blockers: list[dict[str, str]]) -> dict[str, Any]:
    owner_id = f.crm["owner_id"] if f.crm else None
    owner_note = None if f.crm else "Ownership unverified (Pipedrive link missing): check CRM before ANY outreach; only research actions are proposed."
    claims = [str(c) for s in f.signals for c in (s["evidence_claim_ids"] or [])][:6]
    base: dict[str, Any] = {"owner_id": owner_id, "owner_note": owner_note, "evidence_claim_ids": claims, "approval_required": True}
    hard = {b["type"] for b in blockers if b["scope"] == "hard"}
    if "ownership_conflict" in hard:
        return {**base, "action_type": "route_to_owner", "target_role": "account_owner", "objective": "Hand the signal to the account owner; do not contact the account",
                "reason": next(b["detail"] for b in blockers if b["type"] == "ownership_conflict"), "trigger_condition": "On ownership change in Pipedrive",
                "expected_outcome": "Owner decides whether and how to act", "stop_condition": "Owner acknowledges or ownership changes"}
    if hard or gates["G2"]["status"] == "fail":
        why = next((b["detail"] for b in blockers if b["scope"] == "hard"), gates["G2"]["reason"])
        return {**base, "action_type": "monitor", "target_role": None, "objective": "No sales action: wait for a new material event", "reason": why,
                "trigger_condition": "New official progress event for this initiative", "expected_outcome": "Re-evaluation only on new evidence",
                "stop_condition": "Initiative re-opened by new evidence", "approval_required": False}
    if f.tender is not None and any(s["rule_id"] == "SRC-01" for s in f.signals) and f.hyp["route_type"] in {"procurement", "direct"}:
        dl = f.tender["deadline_at"]
        return {**base, "action_type": "bid_no_bid_review", "target_role": "procurement_owner / technical_sponsor",
                "objective": "Decide bid/no-bid: confirm eligibility (alone or consortium), scope fit, certifications, location and contract terms",
                "reason": f"Live official notice {f.tender['notice_id']} (status rechecked {f.tender['last_status_check_at']:%Y-%m-%d}); deadline {dl.isoformat() if dl else 'unknown'} ({f.tender['deadline_tz'] or 'tz unknown'})",
                "due_at": (dl - timedelta(days=7)) if dl and (dl - f.now).days > 7 else f.now + timedelta(days=1), "expected_outcome": "Documented bid/no-bid with eligibility answers",
                "stop_condition": "Deadline passes or notice is cancelled/awarded"}
    g = lambda k: gates[k]["status"]  # noqa: E731
    if g("G3") == "fail" or g("G1") == "fail":
        return {**base, "action_type": "disqualify_review", "target_role": "research_analyst", "objective": "Confirm out-of-ICP/capability and disqualify or record an explicit exception",
                "reason": gates["G1" if g("G1") == "fail" else "G3"]["reason"], "trigger_condition": "Analyst review within the next research cycle",
                "expected_outcome": "Disqualified, or exception rationale recorded", "stop_condition": "Decision recorded", "approval_required": False}
    if f.route and f.route["route_type"] == "prime_subcontract" and g("G5") != "pass":
        return {**base, "action_type": "investigate_partner_route", "target_role": "partner_delivery / alliance lead",
                "objective": f"Establish whether {f.hyp['counterparty_name']} subcontracts specialist engineering and under what eligibility rules",
                "reason": "Prime holds the programme contract; direct-bank access is restricted, partner route is unverified",
                "trigger_condition": "Within 10 working days or on prime partner-programme announcement", "expected_outcome": "Partner route verified, restricted or blocked, with source",
                "stop_condition": "Route status verified or partner states no external capacity"}
    if f.route and f.route["route_type"] == "vendor_implementation" and g("G5") != "pass":
        return {**base, "action_type": "investigate_vendor_route", "target_role": "vendor implementation / partner manager",
                "objective": f"Establish whether {f.hyp['counterparty_name']} needs external implementation capacity for this rollout",
                "reason": "Platform vendor may own the rollout; implementation-partner eligibility is unverified", "trigger_condition": "Within 10 working days",
                "expected_outcome": "Vendor route verified or blocked", "stop_condition": "Vendor states no external capacity or lists an approved-partner process"}
    if g("G4") != "pass":
        return {**base, "action_type": "qualify_external_demand", "target_role": "programme/technical owner",
                "objective": "Ask whether the separable engineering workstream is sourced externally and through which supplier model (buyer conversation, not more web research)",
                "reason": "Verified programme exists but external demand is NOT established; public research cannot resolve it (unknown_until_buyer)",
                "trigger_condition": "After owner/suppression check and approved first-touch draft", "expected_outcome": "Buyer-confirmed answer on external capacity and supplier route",
                "stop_condition": "Buyer confirms or denies external need"}
    if g("G6") != "pass":
        return {**base, "action_type": "identify_delivery_owner", "target_role": "research_analyst", "objective": "Identify and verify the programme delivery owner for this workstream (role-first)",
                "reason": "Demand evidenced but no verified buyer responsible for the workstream", "trigger_condition": "Within 5 working days",
                "expected_outcome": "Verified role/person with initiative responsibility evidence", "stop_condition": "Owner verified or not publicly identifiable", "approval_required": False}
    if g("G7") != "pass":
        return {**base, "action_type": "delivery_feasibility_check", "target_role": "delivery_expert", "objective": "Confirm internal capability, capacity and timing for the scoped work",
                "reason": "Commercial gates pass; execution feasibility is not internally confirmed", "trigger_condition": "Before any proposal or capacity statement",
                "expected_outcome": "Delivery lead records feasibility (or declines)", "stop_condition": "Feasibility decision recorded"}
    return {**base, "action_type": "advance_discovery", "target_role": "economic buyer / sponsor", "objective": "Run discovery to confirm sponsor, procurement path, scope and next commitment",
            "reason": "All gates pass", "trigger_condition": "Within 5 working days", "expected_outcome": "Mutually agreed next step recorded in Pipedrive", "stop_condition": "Buyer declines or next step agreed"}


def upsert_action(conn: Conn, hyp_id: str, spec: dict[str, Any], now) -> str:
    """Supersede the previous active action; enforce the one-active-action-per-hypothesis invariant."""
    cur = q1(conn, "SELECT id, action_type, objective FROM recommended_actions WHERE hypothesis_id=%s AND status IN ('proposed','accepted')", (hyp_id,))
    if cur and cur["action_type"] == spec["action_type"] and cur["objective"] == spec["objective"]:
        return str(cur["id"])
    if cur:
        conn.execute("UPDATE recommended_actions SET status='superseded' WHERE id=%s", (cur["id"],))
    due = spec.get("due_at") or (None if spec.get("trigger_condition") else now + timedelta(days=5))
    row = conn.execute(
        "INSERT INTO recommended_actions(hypothesis_id, owner_id, owner_note, target_role, action_type, objective, reason, evidence_claim_ids, due_at, trigger_condition, "
        "approval_required, expected_outcome, stop_condition) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (hyp_id, spec.get("owner_id"), spec.get("owner_note"), spec.get("target_role"), spec["action_type"], spec["objective"], spec["reason"], spec["evidence_claim_ids"],
         due, spec.get("trigger_condition"), spec["approval_required"], spec["expected_outcome"], spec["stop_condition"])).fetchone()
    return str(row["id"])  # type: ignore[index]
