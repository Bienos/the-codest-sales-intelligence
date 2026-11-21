"""Decision-maker discovery and approved email reveals through a provider (Apollo).
Searching finds names and titles only and costs nothing. Revealing an email is paid, needs a monthly credit cap, a switch and one approval per person."""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .. import approvals as appr
from .. import budget, usage
from ..audit import audit
from ..config import Settings
from ..db import Conn, q1, qa
from .providers import ApolloProvider, enrich_person_with_approval

DEFAULT_TITLES = ["CTO", "Chief Technology Officer", "CIO", "Head of Engineering", "VP Engineering", "Head of Platform", "Head of Payments", "Head of Delivery", "Head of IT"]
STATUS_MAP = {"verified": "Verified"}


def email_status_label(email: str | None, provider_status: str | None) -> str:
    if not email:
        return "Not Found"
    return STATUS_MAP.get((provider_status or "").lower(), "Found — Unverified")


def find_candidates(conn: Conn, provider: Any, org_id: str, *, by: str, titles: list[str] | None = None, limit: int = 8) -> dict[str, Any]:
    org = q1(conn, "SELECT id, display_name, canonical_domain FROM organizations WHERE id=%s", (org_id,))
    if not org:
        raise appr.ApprovalError("account not found")
    if not org["canonical_domain"]:
        raise appr.ApprovalError("This account has no website recorded, so people cannot be searched. Add the website first.")
    found = asyncio.run(provider.search_people(company_domain=org["canonical_domain"], titles=titles or DEFAULT_TITLES, limit=limit))
    added = 0
    for c in found:
        pid = q1(conn, "SELECT person_id FROM contact_candidates WHERE organization_id=%s AND provider=%s AND external_id=%s", (org_id, provider.provider_id, c.external_id))
        if pid:
            continue
        person = str(conn.execute("INSERT INTO people(full_name, linkedin_url) VALUES (%s,%s) RETURNING id", (c.name or "Unnamed", c.linkedin_url)).fetchone()["id"])  # type: ignore[index]
        conn.execute("INSERT INTO employment(person_id, organization_id, title, verification_status) VALUES (%s,%s,%s,'unverified')", (person, org_id, c.title))
        conn.execute("INSERT INTO contact_candidates(organization_id, provider, external_id, name, title, linkedin_url, person_id, found_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                     (org_id, provider.provider_id, c.external_id, c.name or "Unnamed", c.title, c.linkedin_url, person, by))
        added += 1
    audit(conn, actor=by, operation="contact_search", record_type="organization", record_id=org_id, detail={"provider": provider.provider_id, "found": len(found), "added": added})
    return {"found": len(found), "added": added}


def request_reveal(conn: Conn, candidate_id: str, *, by: str, settings: Settings) -> dict[str, Any]:
    c = q1(conn, "SELECT * FROM contact_candidates WHERE id=%s", (candidate_id,))
    if not c:
        raise appr.ApprovalError("candidate not found")
    if c["status"] != "found":
        raise appr.ApprovalError(f"candidate is already {c['status']}")
    if settings.apollo_credits_per_reveal is None:
        raise appr.ApprovalError("The price of a reveal is not configured (RI_APOLLO_CREDITS_PER_REVEAL), so spend cannot be approved.")
    payload = {"provider": c["provider"], "person_external_id": c["external_id"], "credits": settings.apollo_credits_per_reveal}
    a = appr.request(conn, kind="contact_enrichment", target_system=c["provider"], target_id=str(c["person_id"]), operation="enrich_person", payload=payload, requested_by=by,
                     justification=f"Reveal the work email of {c['name']} ({c['title'] or 'role unknown'}) for outreach. Costs {settings.apollo_credits_per_reveal:g} credit(s).",
                     idempotency_key=f"reveal:{candidate_id}", ttl_hours=settings.approval_ttl_hours)
    return {"approval_id": str(a["id"]), "status": a["status"], "credits": settings.apollo_credits_per_reveal}


def execute_reveal(conn: Conn, approval_id: str, provider: Any, settings: Settings) -> dict[str, Any]:
    if not settings.paid_enrichment_enabled:
        raise appr.ApprovalError("Paid enrichment is switched off in this environment (RI_PAID_ENRICHMENT_ENABLED=false).")
    a = q1(conn, "SELECT * FROM approvals WHERE id=%s", (approval_id,))
    if not a:
        raise appr.ApprovalError("approval not found")
    cand = q1(conn, "SELECT * FROM contact_candidates WHERE person_id=%s AND provider=%s", (a["target_id"], a["target_system"]))
    if not cand:
        raise appr.ApprovalError("candidate not found")
    if settings.apollo_credits_per_reveal is None or float(a["payload"].get("credits", -1)) != float(settings.apollo_credits_per_reveal):
        raise appr.ApprovalError("The reveal price changed since approval: request a new approval.")
    if budget.remaining(conn, a["target_system"], "contact_enrichment", usage.period_now()) < float(settings.apollo_credits_per_reveal):
        raise appr.ApprovalError("There is not enough credit left under this month's cap. Raise the cap in Administration, Integrations.")
    res = enrich_person_with_approval(conn, provider, person_external_id=cand["external_id"], person_id=a["target_id"], approval_id=approval_id,
                                      period=usage.period_now(), key=approval_id)
    label = email_status_label(res.work_email, res.email_status)
    conn.execute("UPDATE people SET work_email=%s, email_verification_status=%s WHERE id=%s", (res.work_email, label, a["target_id"]))
    conn.execute("UPDATE contact_candidates SET status=%s WHERE id=%s", ("revealed" if res.work_email else "no_email", cand["id"]))
    audit(conn, actor="system:executor", operation="contact_revealed", record_type="person", record_id=str(a["target_id"]), approval_id=approval_id, detail={"status": label})
    return {"status": "executed", "email_status": label, "has_email": bool(res.work_email)}


def make_provider(settings: Settings) -> ApolloProvider:
    return ApolloProvider(httpx.AsyncClient(timeout=30), settings.apollo_api_key, credits_per_enrichment=settings.apollo_credits_per_reveal)


def candidates_for(conn: Conn, org_id: str) -> list[dict[str, Any]]:
    return [dict(r, id=str(r["id"])) for r in qa(conn, "SELECT id, name, title, linkedin_url, status, provider, created_at FROM contact_candidates WHERE organization_id=%s ORDER BY created_at DESC", (org_id,))]
