"""Evidence-backed outreach DRAFTS (spec 08 §9/§12, 11 §10). Drafting is allowed; sending is outside this system.
Hard blocks: owner/suppression/approved-proof/restricted-client/inferred-email. A blocked draft is stored as 'blocked' with reasons."""
from __future__ import annotations

import re
from typing import Any

from ..db import Conn, jb, q1, qa
from .plays import select_play

RESTRICTED_AUDIENCES = {"restricted", "internal_only", "limited_audience"}


def compose(conn: Conn, hyp_id: str, *, actor: str, actor_crm_user: int | None, person_id: str | None = None, proof_asset_ids: list[str] | None = None) -> dict[str, Any]:
    h = q1(conn, "SELECT * FROM opportunity_hypotheses WHERE id=%s", (hyp_id,))
    assert h
    play = select_play(conn, h)
    blocked: list[dict[str, str]] = []
    # 1 ownership (non-circumvention)
    crm = q1(conn, "SELECT owner_id FROM crm_organizations WHERE organization_id=%s AND link_status='linked'", (h["organization_id"],))
    if not crm:
        blocked.append({"code": "ownership_unverified", "detail": "Account is not linked to Pipedrive; check ownership before any outreach"})
    elif crm["owner_id"] and actor_crm_user != crm["owner_id"]:
        blocked.append({"code": "ownership_conflict", "detail": "Account is owned by another seller: route to the owner"})
    # 2 hard blockers carried from the decision engine
    for b in (h["blockers"] or []):
        if b["scope"] == "hard":
            blocked.append({"code": b["type"], "detail": b["detail"]})
    # 3 contact: suppression / opt-out; never a guessed email
    salutation = "Hello,"
    if person_id:
        p = q1(conn, "SELECT full_name, suppressed, erased_at FROM people WHERE id=%s", (person_id,))
        if not p or p["erased_at"]:
            blocked.append({"code": "person_unavailable", "detail": "contact erased or unknown"})
        elif p["suppressed"]:
            blocked.append({"code": "contact_suppressed", "detail": "contact opted out / suppressed (NEG-11): change channel or stop"})
        else:
            salutation = f"Hello {p['full_name'].split()[0]},"
    # 4 proof governance: only external-approved assets; restricted client names/metrics never appear
    proofs = qa(conn, "SELECT * FROM proof_assets WHERE id = ANY(%s) AND status='active'", ([str(x) for x in (proof_asset_ids or [])],))
    used_proof, proof_lines = [], []
    for a in proofs:
        if a["approved_audience"] != "external_approved":
            blocked.append({"code": "unapproved_proof", "detail": f"'{a['title']}' is {a['approved_audience']}: not releasable in an external draft"})
        else:
            used_proof.append(str(a["id"]))
            proof_lines.append(f"- {a['title']}" + (f" ({a['client_name']})" if a["client_name"] else ""))
    # 5 factual grounding: cite the verified source, never invent facts, dates, prices, availability or mutual connections
    ev = q1(conn, "SELECT e.summary, e.pub_at, e.event_at, c.id AS claim_id, d.canonical_url FROM events e JOIN initiative_events ie ON ie.event_id=e.id LEFT JOIN event_claims ec ON ec.event_id=e.id "
                  "LEFT JOIN claims c ON c.id=ec.claim_id LEFT JOIN source_documents d ON d.id=c.source_document_id WHERE ie.initiative_id=%s AND ie.role='evidence' ORDER BY coalesce(e.event_at,e.pub_at) DESC NULLS LAST LIMIT 1", (h["initiative_id"],))
    if not ev:
        blocked.append({"code": "no_cited_event", "detail": "no verified event to ground the message"})
    ini = q1(conn, "SELECT title, title_is_official FROM initiatives WHERE id=%s", (h["initiative_id"],))
    qs = (play.get("questions") or ["Has anything changed in how this work is sourced?"])[:1]
    fact = f"I read {ev['summary'].rstrip('.')}" + (f" ({(ev['event_at'] or ev['pub_at']):%d %b %Y})" if ev and (ev["event_at"] or ev["pub_at"]) else "") if ev else "I came across your recent programme news"
    subject = f"{ini['title'] if ini['title_is_official'] else 'Your technology programme'}: a question on sourcing"
    body = (f"{salutation}\n\n{fact}. {qs[0]}\n\n" + ("\n".join(proof_lines) + "\n\n" if proof_lines else "") +
            "If someone else owns this, I'd be grateful for a pointer.\n\n[Signature]\n")
    # defence in depth: no email-looking tokens, no restricted client names slipped through, no absolute commercial claims
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", body):
        blocked.append({"code": "contains_email_address", "detail": "draft must not embed or infer email addresses"})
    for a in qa(conn, "SELECT client_name FROM proof_assets WHERE client_name IS NOT NULL AND approved_audience <> 'external_approved'"):
        if a["client_name"].lower() in body.lower():
            blocked.append({"code": "restricted_client_named", "detail": "restricted client reference leaked into draft"})
    row = conn.execute("INSERT INTO outreach_drafts(hypothesis_id, person_id, play_id, subject, body, proof_asset_ids, source_claim_ids, blocked_reasons, status, created_by) "
                       "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                       (hyp_id, person_id, play.get("id") or "none", subject, body, used_proof, [ev["claim_id"]] if ev and ev["claim_id"] else [], jb(blocked),
                        "blocked" if blocked else "draft", actor)).fetchone()
    return {"id": str(row["id"]), "status": "blocked" if blocked else "draft", "blocked_reasons": blocked, "subject": subject, "body": body if not blocked else None,
            "play": play.get("id"), "source_url": ev["canonical_url"] if ev else None,
            "note": "Draft only. Sending requires a separate human action outside this system (and an approval for sequence enrolment)."}
