"""Orchestrates hypothesis creation + evaluation. Deterministic; re-evaluated when the initiative revision changes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..audit import audit
from ..db import Conn, jb, q1, qa
from . import actions, model, review as rv, states


def build_for_initiative(conn: Conn, initiative_id: str) -> list[str]:
    """One hypothesis PER ROUTE (never one per initiative, never three 'confirmed' opportunities for one programme)."""
    ini = q1(conn, "SELECT * FROM initiatives WHERE id=%s AND status='active'", (initiative_id,))
    if not ini:
        return []
    ids = []
    for r in qa(conn, "SELECT * FROM procurement_routes WHERE initiative_id=%s", (initiative_id,)):
        ws = q1(conn, "SELECT id FROM workstreams WHERE initiative_id=%s ORDER BY (scope_confidence='confirmed_scope') DESC, created_at LIMIT 1", (initiative_id,))
        row = conn.execute(
            "INSERT INTO opportunity_hypotheses(initiative_id, workstream_id, route_id, route_type, counterparty_org_id, counterparty_name, organization_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (initiative_id, route_type, counterparty_name) DO UPDATE SET route_id=EXCLUDED.route_id "
            "RETURNING id", (initiative_id, ws["id"] if ws else None, r["id"], model.ROUTE_MAP[r["route_type"]], r["counterparty_org_id"], r["counterparty_name"], ini["owner_org_id"])).fetchone()
        ids.append(str(row["id"]))  # type: ignore[index]
    tender = q1(conn, "SELECT 1 FROM signals s JOIN initiative_events ie ON ie.event_id=s.event_id WHERE ie.initiative_id=%s AND s.rule_id='SRC-01' AND s.status='active'", (initiative_id,))
    if tender:
        o = q1(conn, "SELECT display_name FROM organizations WHERE id=%s", (ini["owner_org_id"],))
        row = conn.execute(
            "INSERT INTO opportunity_hypotheses(initiative_id, route_type, counterparty_org_id, counterparty_name, organization_id) VALUES (%s,'procurement',%s,%s,%s) "
            "ON CONFLICT (initiative_id, route_type, counterparty_name) DO UPDATE SET updated_at=now() RETURNING id",
            (initiative_id, ini["owner_org_id"], o["display_name"], ini["owner_org_id"])).fetchone()
        ids.append(str(row["id"]))  # type: ignore[index]
    return ids


def evaluate(conn: Conn, hyp_id: str, *, now: datetime | None = None, actor: str = "system:opportunity-engine", model_reviewer=None,
             requester_crm_user: int | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    f = model.collect(conn, hyp_id, now, requester_crm_user=requester_crm_user)
    gates = model.compute_gates(f)
    ds = model.demand_status(f)
    blockers = model.compute_blockers(f, conn)
    breakdown, total = model.score(f, gates, ds)
    conf = model.evidence_confidence(f, conn)
    prio = model.priority_for(f, total, blockers, conf, ds)
    if gates["G2"]["status"] == "fail" and prio not in {"blocked"}:
        prio = "low"            # initiative completed/paused: no live commercial action regardless of score
    sev = "hard" if any(b["scope"] == "hard" for b in blockers) else "route" if blockers else "soft" if f.negatives else "none"
    known = [k for k, v in gates.items() if v["status"] != "unknown"]
    unknowns = [{"gate": k, "unknown": v["reason"]} for k, v in gates.items() if v["status"] == "unknown"]
    supporting = list({str(c) for s in f.signals for c in (s["evidence_claim_ids"] or [])} |
                      {str(r["claim_id"]) for r in qa(conn, "SELECT ec.claim_id FROM event_claims ec JOIN initiative_events ie ON ie.event_id=ec.event_id "
                                                           "WHERE ie.initiative_id=%s AND ie.role='evidence'", (f.hyp["initiative_id"],))})
    review_out = None
    if prio in {"urgent", "high"}:
        review_out = rv.review(f, model_reviewer=model_reviewer)
        if review_out["confidence_impact"] == "downgrade" and prio == "high":
            prio = "medium"         # reviewer can downgrade, never elevate
            review_out["priority_note"] = "downgraded from high by counterevidence review"
        elif review_out["confidence_impact"] == "cap_at_medium" and conf == "high":
            conf = "medium"
    f.hyp = {**f.hyp, "gates": gates, "blockers": blockers, "critical_unknowns": unknowns, "external_demand_status": ds, "supporting_claim_ids": supporting,
             "access_status": f.route["access_status"] if f.route else "unknown", "buyer_status": model.buyer_status(f)}
    conn.execute(
        "UPDATE opportunity_hypotheses SET gates=%s, score_json=%s, score_total=%s, evidence_confidence=%s, research_completeness=%s, negative_signal_severity=%s, blockers=%s, "
        "priority=%s, review_json=%s, external_demand_status=%s, access_status=%s, buyer_status=%s, critical_unknowns=%s, supporting_claim_ids=%s, last_checked_at=%s, "
        "initiative_revision=%s, scoring_rule_version=%s, possible_service=%s, updated_at=now() WHERE id=%s",
        (jb(gates), jb(breakdown), total, conf, round(len(known) / 7, 2), sev, jb(blockers), prio, jb(review_out) if review_out else None, ds,
         f.hyp["access_status"], f.hyp["buyer_status"], jb(unknowns), supporting, now, f.initiative["revision"], model.SCORING_VERSION,
         (f.workstreams[0]["kind"] if f.workstreams else None), hyp_id))
    # state progression the engine may do on its own (evidence-checked); later states need CRM / buyer evidence
    cur = q1(conn, "SELECT state FROM opportunity_hypotheses WHERE id=%s", (hyp_id,))["state"]  # type: ignore[index]
    if any(b["scope"] == "hard" for b in blockers) and cur not in states.SIDE:
        states.transition(conn, hyp_id, "blocked", actor=actor, reason="; ".join(b["detail"] for b in blockers if b["scope"] == "hard"))
    elif cur == "blocked" and not any(b["scope"] == "hard" for b in blockers):
        try:
            states.transition(conn, hyp_id, q1(conn, "SELECT prior_active_state p FROM opportunity_hypotheses WHERE id=%s", (hyp_id,))["p"] or "observed",  # type: ignore[index]
                              actor=actor, new_material_evidence=True, reason="hard blocker cleared by new evidence")
        except states.TransitionError:
            pass
    spec = actions.decide(f, gates, blockers)
    actions.upsert_action(conn, hyp_id, spec, now)
    _advance(conn, hyp_id, actor)
    audit(conn, actor=actor, operation="evaluate_hypothesis", record_type="opportunity_hypothesis", record_id=hyp_id,
          after={"score": total, "priority": prio, "gates": {k: v["status"] for k, v in gates.items()}})
    return {"priority": prio, "score": total, "gates": gates, "blockers": blockers, "action": spec["action_type"], "confidence": conf, "demand": ds}


def _advance(conn: Conn, hyp_id: str, actor: str) -> None:
    """Engine-driven promotions stop at actionable_hypothesis. Everything beyond needs external (CRM/buyer) evidence."""
    for target in ("verified", "relevant", "investigating", "actionable_hypothesis"):
        cur = q1(conn, "SELECT state FROM opportunity_hypotheses WHERE id=%s", (hyp_id,))["state"]  # type: ignore[index]
        if cur in states.SIDE or states.ORDER.index(cur) >= states.ORDER.index(target):
            continue
        try:
            if target == "investigating":
                conn.execute("INSERT INTO research_tasks(target_type, target_id, research_question, evidence_gap, decision_effect, method, status, stop_reason, idempotency_key) "
                             "VALUES ('hypothesis',%s,'Initial critical unknowns recorded','see critical_unknowns','route/priority/action','existing_data','planned',NULL,%s) ON CONFLICT DO NOTHING",
                             (hyp_id, f"init-investigation:{hyp_id}"))
            states.transition(conn, hyp_id, target, actor=actor, reason="auto: evidence checklist satisfied")
        except states.TransitionError:
            break


def record_hard_block(conn: Conn, org_id: str, blocker: str, reason: str, actor: str) -> str:
    """Human-recorded hard blocker (e.g. ineligible_tender, unapproved_claim). Preserves all positive evidence; blocks actions."""
    if blocker not in {"ineligible_tender", "unapproved_claim", "confidentiality_restriction", "unavailable_delivery_model", "explicit_no_supplier", "cancelled_workstream"}:
        raise ValueError(f"unknown blocker type {blocker}")
    row = conn.execute("INSERT INTO negative_findings(rule_id, rule_version, scope_type, scope_id, kind, effect, detail) VALUES ('MANUAL','1.0.0','account',%s,'hard_block','stop',%s) RETURNING id",
                       (org_id, jb({"blocker": blocker, "reason": reason, "by": actor}))).fetchone()
    audit(conn, actor=actor, operation="record_hard_block", record_type="organization", record_id=org_id, detail={"blocker": blocker, "reason": reason})
    return str(row["id"])  # type: ignore[index]


def evaluate_stale(conn: Conn, now: datetime | None = None) -> int:
    """Re-evaluate hypotheses whose initiative moved (revision bump invalidates superseded conclusions)."""
    n = 0
    for h in qa(conn, "SELECT h.id FROM opportunity_hypotheses h JOIN initiatives i ON i.id=h.initiative_id WHERE h.initiative_revision < i.revision AND h.state NOT IN ('disqualified','superseded')"):
        evaluate(conn, str(h["id"]), now=now)
        n += 1
    return n


def refresh_hypotheses(conn: Conn, initiative_id: str, now: datetime | None = None) -> list[str]:
    """The live path from 'event linked to a programme' to 'opportunity card': build one hypothesis per route, then evaluate each.
    Idempotent: re-running updates the same hypotheses and never creates duplicates."""
    ids = build_for_initiative(conn, initiative_id)
    for hid in ids:
        evaluate(conn, hid, now=now)
    return ids


def ensure_hypotheses(conn: Conn, now: datetime | None = None) -> int:
    """Safety net for the scheduler: any active programme with routes but no hypothesis gets one (e.g. after an interrupted run)."""
    n = 0
    for i in qa(conn, "SELECT i.id FROM initiatives i WHERE i.status='active' AND i.stage NOT IN ('completed','cancelled') AND "
                      "EXISTS (SELECT 1 FROM procurement_routes r WHERE r.initiative_id=i.id) AND NOT EXISTS (SELECT 1 FROM opportunity_hypotheses h WHERE h.initiative_id=i.id)"):
        n += len(refresh_hypotheses(conn, str(i["id"]), now))
    return n


__all__ = ["build_for_initiative", "ensure_hypotheses", "evaluate", "evaluate_stale", "refresh_hypotheses", "json"]
