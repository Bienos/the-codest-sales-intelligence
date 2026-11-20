"""Deal progression support (spec 08 §7/§11). Pipedrive stage is authoritative; we SUGGEST and never write. A meeting alone changes nothing."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..db import Conn, q1, qa

STAGE_TO_STATE = {"Webform MQL": "observed", "Marketing Nurturing": "observed", "DISCOVERY": "engaged", "SQL": "buyer_need_confirmed",
                  "Proposal/Shortlist": "proposal_procurement", "Commercials/NDA": "proposal_procurement", "Active Delivery": "contracted_delivery"}
ORDER = ["observed", "verified", "relevant", "investigating", "actionable_hypothesis", "engaged", "buyer_need_confirmed", "commercially_qualified", "proposal_procurement", "contracted_delivery"]


def deal_view(conn: Conn, hyp_id: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    h = q1(conn, "SELECT * FROM opportunity_hypotheses WHERE id=%s", (hyp_id,))
    assert h
    deals = qa(conn, "SELECT d.* FROM crm_deals d JOIN crm_organizations o ON o.crm_id=d.crm_org_id WHERE o.organization_id=%s AND d.status='open' ORDER BY d.update_time DESC NULLS LAST", (h["organization_id"],))
    if not deals:
        return {"has_open_deal": False, "note": "No open Pipedrive deal on this account. A recommendation is not a deal; creating one needs an approved CRM action."}
    d = deals[0]
    stage = (d["stage_name"] or "").strip()
    implied = STAGE_TO_STATE.get(stage)
    age = (now - d["update_time"]).days if d["update_time"] else None
    committee_missing = qa(conn, "SELECT role FROM (VALUES ('economic_buyer'),('programme_owner'),('procurement_owner')) v(role) WHERE role NOT IN (SELECT role FROM buyer_roles WHERE hypothesis_id=%s)", (hyp_id,))
    last_commit = q1(conn, "SELECT subject, done_at FROM crm_activities WHERE crm_org_id=%s AND done ORDER BY done_at DESC NULLS LAST LIMIT 1", (d["crm_org_id"],))
    blockers = list(h["blockers"] or [])
    unresolved_route = h["access_status"] in {"unknown", "restricted", "blocked"}
    gap = None
    if implied and ORDER.index(implied) > ORDER.index(h["state"]):
        gap = (f"Pipedrive stage '{stage}' implies '{implied}', but the evidence-gated state is '{h['state']}'. "
               "No automatic promotion: record buyer-confirmed facts and satisfy gates G4-G7 first.")
    nxt = ("Assign a purpose-built action: " + ("name the missing buyer role(s) " + ", ".join(m["role"] for m in committee_missing) if committee_missing else
           "resolve the supplier route" if unresolved_route else "agree the next dated commitment with the sponsor"))
    return {"has_open_deal": True, "deal": {"id": d["crm_id"], "title": d["title"], "stage": stage, "owner_id": d["owner_id"], "age_days_since_update": age},
            "evidence_gated_state": h["state"], "stage_implied_state": implied, "stage_vs_evidence_gap": gap,
            "last_buyer_confirmed_commitment": {"subject": last_commit["subject"], "at": last_commit["done_at"]} if last_commit else None,
            "missing_stakeholders": [m["role"] for m in committee_missing], "unresolved_supplier_route": unresolved_route, "blockers": blockers,
            "aging_flag": age is not None and age > 21, "next_step_guidance": {"description": nxt, "owner_id": d["owner_id"], "verify_success": "A dated commitment from the buyer is recorded in Pipedrive",
                                                                               "anti_pattern": "generic 'follow up' tasks are not acceptable"}}
