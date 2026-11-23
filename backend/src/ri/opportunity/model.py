"""Opportunity decision engine: facts -> gates -> scores -> blockers (spec 07). Explicit rules only; the LLM never writes a number."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..db import Conn, q1, qa
from ..sheets.reconcile import icp_band

SCORING_VERSION = "1.0.0"
WEIGHTS = {"icp": 15, "engineering_relevance": 25, "external_demand": 20, "access": 20, "timing": 10, "relationship_delivery": 10}   # hypotheses, not probabilities
CAPS = set(yaml.safe_load((Path(__file__).resolve().parents[3] / "rules" / "codest_capabilities.yaml").read_text())["capabilities"])
HIGH_THRESHOLD, MEDIUM_THRESHOLD = 55, 35
URGENT_DAYS = 14
ACTIVE_STAGES = {"announced", "planning", "sourcing", "implementation", "transition_or_launch"}
ROUTE_MAP = {"buyer_direct": "direct", "vendor_implementation": "vendor", "prime_subcontract": "prime_subcontract", "framework_panel": "procurement",
             "msp_vms": "procurement", "supplier_registration": "procurement", "relationship": "relationship"}


@dataclass
class Facts:
    hyp: dict[str, Any]
    org: dict[str, Any]
    initiative: dict[str, Any]
    workstreams: list[dict[str, Any]]
    signals: list[dict[str, Any]]
    negatives: list[dict[str, Any]]
    route: dict[str, Any] | None
    edges: list[dict[str, Any]]
    crm: dict[str, Any] | None
    contacts: list[dict[str, Any]]
    suppressed_contacts: int
    buyer_confirmed: bool
    open_identity_review: bool
    tender: dict[str, Any] | None
    now: datetime
    delivery_validated: bool = False
    ownership_conflict_for: str | None = None


def initiative_tender(conn: Conn, initiative_id: str, organization_id: str) -> dict[str, Any] | None:
    """Resolve a notice cited by this programme, never another notice for the same buyer."""
    return q1(conn, """
        SELECT n.* FROM procurement_notices n
        JOIN events e ON e.attrs->>'notice_id'=n.notice_id AND e.subject_org_id=n.buyer_org_id
        JOIN initiative_events ie ON ie.event_id=e.id
        WHERE ie.initiative_id=%s AND ie.role='evidence' AND n.buyer_org_id=%s
          AND n.status IN ('open','amended') AND e.verification_status='verified'
          AND (e.independent_origin_group='official:' || n.source_id || ':' || n.notice_id
               OR (coalesce(e.independent_origin_group, '') NOT LIKE 'official:%%'
                   AND NOT EXISTS (
                       SELECT 1 FROM procurement_notices nn
                       WHERE nn.notice_id=n.notice_id AND nn.buyer_org_id=n.buyer_org_id AND nn.source_id<>n.source_id
                   )))
        ORDER BY n.deadline_at NULLS LAST, n.id LIMIT 1
        """, (initiative_id, organization_id))


def collect(conn: Conn, hyp_id: str, now: datetime, *, requester_crm_user: int | None = None) -> Facts:
    h = q1(conn, "SELECT * FROM opportunity_hypotheses WHERE id=%s", (hyp_id,))
    assert h
    org = q1(conn, "SELECT * FROM organizations WHERE id=%s", (h["organization_id"],))
    ini = q1(conn, "SELECT * FROM initiatives WHERE id=%s", (h["initiative_id"],))
    sig = qa(conn, "SELECT s.*, e.attrs AS event_attrs, e.event_type FROM signals s JOIN initiative_events ie ON ie.event_id=s.event_id "
                   "JOIN events e ON e.id=s.event_id WHERE ie.initiative_id=%s AND ie.role='evidence' AND s.status='active' AND s.organization_id=%s AND e.subject_org_id=s.organization_id AND s.verification_status='verified' AND e.verification_status='verified' AND e.lifecycle_status='active' AND (s.expires_at IS NULL OR s.expires_at>%s)", (h["initiative_id"], h["organization_id"], now))
    neg = qa(conn, "SELECT * FROM negative_findings WHERE status='active' AND ((scope_type='account' AND scope_id=%s) OR (scope_type='route' AND scope_id=%s))",
             (str(h["organization_id"]), f"{h['organization_id']}:direct"))
    route = q1(conn, "SELECT * FROM procurement_routes WHERE id=%s", (h["route_id"],)) if h["route_id"] else None
    crm = q1(conn, "SELECT * FROM crm_organizations WHERE organization_id=%s AND link_status='linked'", (h["organization_id"],))
    contacts = qa(conn, "SELECT e.title, e.buyer_lane, e.verification_status, p.suppressed, p.id person_id FROM employment e JOIN people p ON p.id=e.person_id "
                        "WHERE e.organization_id=%s AND p.erased_at IS NULL", (h["organization_id"],))
    confirmed = bool(q1(conn, "SELECT 1 FROM events e JOIN initiative_events ie ON ie.event_id=e.id WHERE ie.initiative_id=%s AND e.event_type='crm_buyer_response' "
                              "AND e.attrs->>'need_confirmed'='true' AND e.verification_status='verified'", (h["initiative_id"],)))
    tender = initiative_tender(conn, h["initiative_id"], h["organization_id"])
    f = Facts(h, org, ini, qa(conn, "SELECT * FROM workstreams WHERE initiative_id=%s", (h["initiative_id"],)), sig, neg, route,
              qa(conn, "SELECT * FROM supplier_edges WHERE initiative_id=%s", (h["initiative_id"],)), crm, [c for c in contacts if not c["suppressed"]],
              len([c for c in contacts if c["suppressed"]]), confirmed,
              bool(q1(conn, "SELECT 1 FROM identity_reviews WHERE status='open' AND %s = ANY(candidate_org_ids)", (str(h["organization_id"]),))), tender, now,
              delivery_validated=bool((h["gates"] or {}).get("G7", {}).get("status") == "pass" and (h["gates"] or {}).get("G7", {}).get("validated_by")))
    if crm and requester_crm_user and crm["owner_id"] and crm["owner_id"] != requester_crm_user:
        f.ownership_conflict_for = crm["owner_name"] or str(crm["owner_id"])
    return f


def demand_status(f: Facts) -> str:
    if f.buyer_confirmed:
        return "buyer_confirmed"
    tiers = {s["demand_tier"] for s in f.signals}
    if "D1" in tiers:
        return "explicit"
    if "D2" in tiers:
        return "inferred"
    return "unknown"          # a verified programme (D3) or attention (D4) is NOT external demand


def evidence_confidence(f: Facts, conn: Conn) -> str:
    groups = set()
    tiers = set()
    for s in f.signals:
        groups |= set(s["independent_origin_groups"] or [])
        tiers.add((s["event_attrs"] or {}).get("source_tier"))
    n = len(groups)
    if f.signals and (tiers & {"S1", "S2"}) and n >= 1:
        return "high" if (tiers & {"S1"} or n >= 2) else "medium"
    if f.signals and n >= 1:
        return "medium" if n >= 2 else "low"
    return "low"


def compute_gates(f: Facts) -> dict[str, dict[str, Any]]:
    g: dict[str, dict[str, Any]] = {}
    icp = icp_band(f.org["employees_estimate"], bool(f.org["employees_uncertain"]), f.org.get("segment"))
    if f.open_identity_review:
        g["G1"] = {"status": "unknown", "reason": "identity review open"}
    elif icp["band"] == "unknown" or icp["boundary_uncertain"]:
        g["G1"] = {"status": "unknown", "reason": "size unknown or estimate straddles an ICP boundary"}
    elif icp["band"] == "below_threshold":
        g["G1"] = {"status": "fail", "reason": (icp.get("reason") or "below ICP size threshold") + " (smaller only by approved exception, with a written rationale)"}
    else:
        g["G1"] = {"status": "pass", "reason": f"ICP band {icp['band']}"}
    st = f.initiative["stage"]
    if st in {"cancelled", "completed", "paused"}:
        g["G2"] = {"status": "fail", "reason": f"initiative {st}"}
    elif st in ACTIVE_STAGES:
        g["G2"] = {"status": "pass", "reason": f"stage {st} per cited event"}
    else:
        g["G2"] = {"status": "unknown", "reason": "initiative stage not established"}
    fit = [w for w in f.workstreams if set(w["needed_capabilities"]) & CAPS]
    solid = [w for w in fit if w["scope_confidence"] in {"confirmed_scope", "implied_engineering_activity"}]
    g["G3"] = ({"status": "pass", "reason": f"{solid[0]['kind']} ({solid[0]['scope_confidence']})"} if solid else
               {"status": "unknown", "reason": "only inferred possible workstreams"} if fit else {"status": "fail", "reason": "no Codest capability fit"})
    ds = demand_status(f)
    g["G4"] = {"status": "pass", "reason": f"external demand {ds}"} if ds in {"explicit", "buyer_confirmed"} else {"status": "unknown", "reason": f"external demand {ds}: not established"}
    acc = f.route["access_status"] if f.route else "unknown"
    g["G5"] = ({"status": "pass", "reason": "route verified"} if acc == "verified" or (f.tender and f.hyp["route_type"] == "procurement") else
               {"status": "fail", "reason": f"route {acc}: {f.route['restriction'] if f.route else ''}"} if acc in {"restricted", "blocked"} else
               {"status": "unknown", "reason": f"route {acc}"})
    bs = buyer_status(f)
    g["G6"] = {"status": "pass", "reason": f"buyer {bs}"} if bs in {"person_verified", "buyer_confirmed"} else {"status": "unknown", "reason": f"buyer {bs}"}
    g["G7"] = ({"status": "pass", "reason": "internal delivery validation recorded", "validated_by": f.hyp["gates"].get("G7", {}).get("validated_by")} if f.delivery_validated
               else {"status": "unknown", "reason": "internal delivery feasibility not confirmed"})
    return g


def buyer_status(f: Facts) -> str:
    if f.buyer_confirmed:
        return "buyer_confirmed"
    if any(c["verification_status"] == "verified" for c in f.contacts):
        return "person_verified"
    return "role_identified" if f.contacts else "none"


def compute_blockers(f: Facts, conn: Conn) -> list[dict[str, str]]:
    b: list[dict[str, str]] = []
    if f.ownership_conflict_for or any(n["kind"] == "ownership_gate" for n in f.negatives):
        b.append({"type": "ownership_conflict", "scope": "hard", "detail": f"account owned by {f.ownership_conflict_for or 'another party'}: route to owner"})
    if f.initiative["stage"] == "cancelled" or any(n["rule_id"] == "NEG-06" for n in f.negatives):
        b.append({"type": "cancelled_workstream", "scope": "hard", "detail": "programme cancelled/paused per cited evidence"})
    for n in f.negatives:
        if n["kind"] == "route_block" and f.hyp["route_type"] == "direct":
            b.append({"type": "explicit_no_supplier" if n["rule_id"] == "NEG-05" else "route_blocked", "scope": "route", "detail": f"{n['rule_id']} blocks the direct route; partner routes remain separate"})
    if f.tender is not None and f.tender["deadline_at"] and f.tender["deadline_at"] <= f.now:
        b.append({"type": "expired_procurement", "scope": "hard", "detail": "official deadline passed"})
    for n in f.negatives:
        if n["kind"] == "hard_block":      # recorded by a human/executor: ineligible_tender, unapproved_claim, confidentiality_restriction ...
            b.append({"type": (n["detail"] or {}).get("blocker", "hard_block"), "scope": "hard", "detail": (n["detail"] or {}).get("reason", n["rule_id"])})
    if f.suppressed_contacts and not f.contacts:
        b.append({"type": "opt_out", "scope": "hard", "detail": "all known contacts are suppressed/opted out"})
    return b


def score(f: Facts, gates: dict[str, Any], status_demand: str) -> tuple[dict[str, dict[str, Any]], int]:
    s: dict[str, dict[str, Any]] = {}
    icp = icp_band(f.org["employees_estimate"], bool(f.org["employees_uncertain"]), f.org.get("segment"))
    base = {"strategic_5000_plus": 15, "enterprise_1000_plus": 12, "scaleup_500_999": 9, "unknown": 4, "below_threshold": 0}[icp["band"]]
    s["icp"] = {"points": max(base - (3 if icp["boundary_uncertain"] else 0), 0), "max": 15, "why": f"{icp['band']}" + (" (boundary uncertain, -3)" if icp["boundary_uncertain"] else "")}
    fit = [w for w in f.workstreams if set(w["needed_capabilities"]) & CAPS]
    if fit:
        best = max({"confirmed_scope": 20, "implied_engineering_activity": 14, "inferred_possible_external": 7}[w["scope_confidence"]] for w in fit)
        s["engineering_relevance"] = {"points": min(best + 5, 25), "max": 25, "why": "best workstream scope confidence + capability match"}
    else:
        s["engineering_relevance"] = {"points": 0, "max": 25, "why": "no capability match"}
    s["external_demand"] = {"points": {"buyer_confirmed": 20, "explicit": 20, "inferred": 12, "unknown": 5 if f.initiative["stage"] in ACTIVE_STAGES else 0}[status_demand], "max": 20, "why": f"external demand {status_demand}"}
    racc = f.route["access_status"] if f.route else "unknown"
    ra = {"verified": 14, "potential": 9, "unknown": 4, "restricted": 2, "blocked": 0}[racc]
    if f.tender and f.hyp["route_type"] == "procurement":
        ra = max(ra, 14)
    rb = {"buyer_confirmed": 6, "person_verified": 6, "role_identified": 3, "none": 0}[buyer_status(f)]
    s["access"] = {"points": ra + rb, "max": 20, "why": f"route {racc}, buyer {buyer_status(f)}"}
    t = 1
    if f.tender and f.tender["deadline_at"]:
        d = (f.tender["deadline_at"] - f.now).days
        t = 10 if 0 <= d <= 30 else 6 if d > 30 else 0
    elif f.initiative["next_milestone_at"]:
        d = (f.initiative["next_milestone_at"] - f.now).days
        t = 7 if 0 <= d <= 90 else 3
    elif f.initiative["stage"] in ACTIVE_STAGES:
        t = 4
    s["timing"] = {"points": t, "max": 10, "why": "verified deadline/milestone" if (f.tender or f.initiative["next_milestone_at"]) else "stage only, no verified date"}
    rel = (5 if f.crm and f.contacts else 0) + (5 if f.delivery_validated else 0)
    s["relationship_delivery"] = {"points": rel, "max": 10, "why": ("CRM relationship; " if f.crm and f.contacts else "no verified relationship; ") + ("delivery validated" if f.delivery_validated else "delivery fit unvalidated")}
    return s, sum(v["points"] for v in s.values())


def priority_for(f: Facts, total: int, blockers: list[dict[str, str]], confidence: str, demand: str = "unknown") -> str:
    if any(b["scope"] == "hard" for b in blockers):
        return "blocked"
    live_tender = f.tender is not None and f.hyp["route_type"] in {"procurement", "direct"} and any(s["rule_id"] == "SRC-01" for s in f.signals)
    if live_tender and f.tender["deadline_at"] and 0 <= (f.tender["deadline_at"] - f.now).days <= URGENT_DAYS:
        return "urgent"
    route_blocked = any(b["scope"] == "route" for b in blockers)
    # commercial-footing floor: a verified programme alone (demand unknown AND route unknown) is never 'high' - technology investment != outsourcing demand
    footing = demand in {"explicit", "inferred", "buyer_confirmed"} or (f.route is not None and f.route["access_status"] in {"verified", "potential"})
    if total >= HIGH_THRESHOLD and confidence in {"medium", "high"} and not route_blocked and footing:
        return "high"
    if total >= MEDIUM_THRESHOLD:
        return "medium"
    return "low"
