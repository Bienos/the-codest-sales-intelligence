"""Progressive enrichment E1-E6 (spec 06 §2). Each stage has a gate; paid/personal stages are unreachable until the
earlier gates pass AND an approval exists. Stops early and records WHY (no decision-irrelevant spend).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..db import Conn, jb, q1, qa
from ..sheets.reconcile import icp_band

STAGES = ["E1", "E2", "E3", "E4", "E5", "E6"]


@dataclass
class WaterfallResult:
    reached: str
    stop_reason: str
    fields: dict[str, Any] = field(default_factory=dict)


def put_field(conn: Conn, entity_type: str, entity_id: str, name: str, stage: str, state: str, value: Any, *, provider: str, method: str,
              claims: list[str] | None = None, confidence: str | None = None, verification: str = "unverified") -> None:
    conn.execute(
        "INSERT INTO enrichment_fields(entity_type, entity_id, field, stage, state, value, source_claim_ids, provider, method, confidence, verification_status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (entity_type, entity_id, field, provider) DO UPDATE SET state=EXCLUDED.state, value=EXCLUDED.value, "
        "source_claim_ids=EXCLUDED.source_claim_ids, confidence=EXCLUDED.confidence, verification_status=EXCLUDED.verification_status, observed_at=now()",
        (entity_type, entity_id, name, stage, state, jb(value), claims or [], provider, method, confidence, verification))


def e1_entity(conn: Conn, org_id: str) -> tuple[bool, str, dict[str, Any]]:
    o = q1(conn, "SELECT * FROM organizations WHERE id=%s", (org_id,))
    if not o:
        return False, "organisation_not_found", {}
    pending = q1(conn, "SELECT 1 FROM identity_reviews WHERE status='open' AND %s = ANY(candidate_org_ids)", (org_id,))
    icp = icp_band(o["employees_estimate"], bool(o["employees_uncertain"]))
    put_field(conn, "organization", org_id, "icp_band", "E1", "present" if icp["band"] != "unknown" else "unknown", icp, provider="canonical", method="internal_rule",
              confidence="medium" if o["employees_basis"] == "sheet_recorded" else "low")
    put_field(conn, "organization", org_id, "legal_identity", "E1", "present" if o["verification_status"] in {"registry_verified", "manual_verified"} else "unknown",
              {"verification_status": o["verification_status"]}, provider="canonical", method="registry_lookup")
    if pending:
        return False, "identity_review_open", {"icp": icp}
    return True, "ok", {"icp": icp}


def e2_internal(conn: Conn, org_id: str, initiative_ids: list[str]) -> tuple[bool, str, dict[str, Any]]:
    legacy = qa(conn, "SELECT kind FROM legacy_account_research WHERE organization_id=%s", (org_id,))
    contacts = q1(conn, "SELECT count(*) n FROM employment WHERE organization_id=%s", (org_id,))["n"]  # type: ignore[index]
    crm = q1(conn, "SELECT crm_id, owner_id, owner_name FROM crm_organizations WHERE organization_id=%s AND link_status='linked'", (org_id,))
    deals = qa(conn, "SELECT crm_id, stage_name, status FROM crm_deals WHERE crm_org_id=%s", (crm["crm_id"],)) if crm else []
    info = {"legacy_research_kinds": [r["kind"] for r in legacy], "known_contacts": contacts, "crm_linked": bool(crm), "crm_owner": crm["owner_name"] if crm else None,
            "open_deals": len([d for d in deals if d["status"] == "open"]), "crm_state": "linked" if crm else "unknown_ownership"}
    put_field(conn, "organization", org_id, "crm_owner", "E2", "present" if crm else "unknown", {"owner": info["crm_owner"]}, provider="pipedrive", method="crm_mirror")
    if initiative_ids or contacts or deals:
        return True, "ok", info
    return False, "no_relevant_initiative_or_relationship", info


def e3_technical(conn: Conn, initiative_id: str) -> tuple[bool, str, dict[str, Any]]:
    ws = qa(conn, "SELECT kind, scope_confidence, needed_capabilities FROM workstreams WHERE initiative_id=%s", (initiative_id,))
    if not ws:
        return False, "no_workstream_derivable", {}
    fit = [w for w in ws if w["needed_capabilities"]]
    put_field(conn, "initiative", initiative_id, "workstreams", "E3", "present", [{"kind": w["kind"], "scope_confidence": w["scope_confidence"]} for w in ws], provider="canonical", method="initiative_graph")
    if not fit:
        return False, "no_codest_capability_fit", {"workstreams": len(ws)}
    return True, "ok", {"workstreams": len(ws)}


def e4_commercial(conn: Conn, initiative_id: str) -> tuple[bool, str, dict[str, Any]]:
    routes = qa(conn, "SELECT route_type, access_status FROM procurement_routes WHERE initiative_id=%s", (initiative_id,))
    live_tender = q1(conn, "SELECT 1 FROM signals s JOIN initiative_events ie ON ie.event_id=s.event_id WHERE ie.initiative_id=%s AND s.rule_id='SRC-01' AND s.status='active'", (initiative_id,))
    put_field(conn, "initiative", initiative_id, "routes", "E4", "present" if routes else "unknown", routes, provider="canonical", method="route_graph")
    open_routes = [r for r in routes if r["access_status"] not in {"blocked"}]
    if open_routes or live_tender:
        return True, "ok", {"routes": routes}
    return False, "no_accessible_route_hypothesis", {"routes": routes}


def run_waterfall(conn: Conn, *, org_id: str, initiative_id: str | None, until: str = "E4", accepted_by_human: bool = False,
                  contact_approval_id: str | None = None) -> WaterfallResult:
    """Run stages in order up to `until`. E5 needs a human-accepted research candidate; E6 needs an approval receipt."""
    out = WaterfallResult("none", "not_started")
    ok, why, f = e1_entity(conn, org_id)
    out.fields["E1"] = f
    if not ok:
        return WaterfallResult("E1", why, out.fields)
    out.reached = "E1"
    if until == "E1":
        return WaterfallResult("E1", "requested_stage_reached", out.fields)
    ok, why, f = e2_internal(conn, org_id, [initiative_id] if initiative_id else [])
    out.fields["E2"] = f
    if not ok:
        return WaterfallResult("E2", why, out.fields)
    out.reached = "E2"
    if until == "E2" or not initiative_id:
        return WaterfallResult("E2", "requested_stage_reached" if until == "E2" else "no_initiative", out.fields)
    ok, why, f = e3_technical(conn, initiative_id)
    out.fields["E3"] = f
    if not ok:
        return WaterfallResult("E3", why, out.fields)
    out.reached = "E3"
    if until == "E3":
        return WaterfallResult("E3", "requested_stage_reached", out.fields)
    ok, why, f = e4_commercial(conn, initiative_id)
    out.fields["E4"] = f
    if not ok:
        return WaterfallResult("E4", why, out.fields)
    out.reached = "E4"
    if until == "E4":
        return WaterfallResult("E4", "requested_stage_reached", out.fields)
    if not accepted_by_human:
        return WaterfallResult("E4", "e5_requires_human_accepted_research_candidate", out.fields)
    out.reached = "E5"
    if until == "E5":
        return WaterfallResult("E5", "requested_stage_reached", out.fields)
    if not contact_approval_id:
        return WaterfallResult("E5", "e6_requires_explicit_prior_approval", out.fields)
    return WaterfallResult("E6", "approval_present_executor_must_validate", out.fields)
