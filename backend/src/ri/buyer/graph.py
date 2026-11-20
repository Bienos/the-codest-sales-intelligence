"""Buyer access graph (spec 08 §3-§4): roles are first-class; a title is not authority; access is ranked by VERIFIABILITY."""
from __future__ import annotations

from typing import Any

from ..audit import audit
from ..db import Conn, q1, qa

ACCESS_RANK = ["existing_crm_relationship", "buyer_confirmed_owner", "official_procurement_channel", "verified_prime_or_vendor_route",
               "introduction_via_intermediary", "targeted_functional_outreach", "strategic_familiarity", "public_event_context"]
CONTEXT_ONLY = {"public_event_context"}           # attention != intent; shared event != relationship


def add_role(conn: Conn, hyp_id: str, role: str, *, actor: str, person_id: str | None = None, role_title: str | None = None, role_is_verified: bool = False,
             initiative_responsibility_verified: bool = False, evidence_claim_ids: list[str] | None = None) -> str:
    if person_id is None and not role_title:
        raise ValueError("a buyer role needs a person or a role title (role-first)")
    contact = "unknown"
    if person_id:
        p = q1(conn, "SELECT suppressed FROM people WHERE id=%s AND erased_at IS NULL", (person_id,))
        if not p:
            raise ValueError("person not found or erased")
        contact = "suppressed" if p["suppressed"] else "unknown"
    row = conn.execute(
        "INSERT INTO buyer_roles(hypothesis_id, role, person_id, role_title, role_is_verified, current_role_verified_at, initiative_responsibility_verified, contactability, evidence_claim_ids, created_by) "
        "VALUES (%s,%s,%s,%s,%s,CASE WHEN %s THEN now() END,%s,%s,%s,%s) ON CONFLICT (hypothesis_id, role, person_id) DO UPDATE SET role_title=EXCLUDED.role_title RETURNING id",
        (hyp_id, role, person_id, role_title, role_is_verified, role_is_verified, initiative_responsibility_verified, contact, evidence_claim_ids or [], actor)).fetchone()
    if person_id and role_is_verified:      # promote the canonical employment edge only on a human-verified role
        h = q1(conn, "SELECT organization_id FROM opportunity_hypotheses WHERE id=%s", (hyp_id,))
        conn.execute("UPDATE employment SET verification_status='verified' WHERE person_id=%s AND organization_id=%s", (person_id, h["organization_id"]))  # type: ignore[index]
    audit(conn, actor=actor, operation="buyer_role_added", record_type="opportunity_hypothesis", record_id=hyp_id, detail={"role": role})
    return str(row["id"])  # type: ignore[index]


def add_access_route(conn: Conn, hyp_id: str, kind: str, *, evidence: str, actor: str, verified: bool = False, person_id: str | None = None, intermediary: str | None = None) -> str:
    if not evidence.strip():
        raise ValueError("evidence required: shared events and assumed connections are not access")
    if kind == "introduction_via_intermediary" and not (intermediary and verified):
        raise ValueError("an introduction needs a NAMED intermediary and verification; no fabricated mutual connections")
    row = conn.execute("INSERT INTO access_routes(hypothesis_id, kind, verified, person_id, intermediary, evidence, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                       (hyp_id, kind, verified, person_id, intermediary, evidence, actor)).fetchone()
    return str(row["id"])  # type: ignore[index]


def ranked_access(conn: Conn, hyp_id: str) -> list[dict[str, Any]]:
    """Rank by verifiable access, not seniority. Suppressed/owner-restricted routes are excluded before ranking."""
    h = q1(conn, "SELECT * FROM opportunity_hypotheses WHERE id=%s", (hyp_id,))
    out = []
    for r in qa(conn, "SELECT r.*, p.suppressed FROM access_routes r LEFT JOIN people p ON p.id=r.person_id WHERE r.hypothesis_id=%s", (hyp_id,)):
        if r["suppressed"]:
            continue
        out.append({"kind": r["kind"], "verified": r["verified"], "evidence": r["evidence"], "intermediary": r["intermediary"], "context_only": r["kind"] in CONTEXT_ONLY,
                    "rank": ACCESS_RANK.index(r["kind"]) + (0 if r["verified"] else 20)})
    crm = q1(conn, "SELECT 1 FROM crm_organizations WHERE organization_id=%s AND link_status='linked' AND owner_id IS NOT NULL", (h["organization_id"],))  # type: ignore[index]
    if crm and not any(o["kind"] == "existing_crm_relationship" for o in out):
        deals = q1(conn, "SELECT count(*) n FROM crm_deals d JOIN crm_organizations o ON o.crm_id=d.crm_org_id WHERE o.organization_id=%s", (h["organization_id"],))["n"]  # type: ignore[index]
        if deals:
            out.append({"kind": "existing_crm_relationship", "verified": True, "evidence": f"{deals} CRM deal(s) on this account", "intermediary": None, "context_only": False, "rank": 0})
    return sorted(out, key=lambda x: x["rank"])


def committee(conn: Conn, hyp_id: str) -> dict[str, Any]:
    roles = qa(conn, "SELECT b.role, b.role_title, b.role_is_verified, b.initiative_responsibility_verified, b.buyer_authority_inferred, b.contactability, p.full_name, p.suppressed "
                     "FROM buyer_roles b LEFT JOIN people p ON p.id=b.person_id WHERE b.hypothesis_id=%s", (hyp_id,))
    have = {r["role"] for r in roles}
    return {"roles": roles, "missing": [r for r in ("problem_owner", "programme_owner", "economic_buyer", "technical_evaluator", "procurement_owner", "risk_security") if r not in have],
            "single_threaded": len({r["full_name"] for r in roles if r["full_name"]}) <= 1,
            "caveat": "Current corporate role does not prove budget authority; buyer_authority_inferred stays true until buyer-confirmed."}
