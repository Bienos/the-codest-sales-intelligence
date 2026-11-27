"""From an approved draft to a Lemlist campaign, under a two-person approval. The campaign is created in Lemlist with its leads but NOT launched:
launching stays a human click in Lemlist. No email address is ever guessed; any address we hold (verified ones first; unverified ones are sent too,
by the owner's decision of 25 Nov 2026, and a bounce suppresses the address everywhere), minus anyone suppressed, unsubscribed or bounced."""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any

import httpx

from .. import approvals as appr
from ..audit import audit
from ..config import Settings
from ..db import Conn, jb, q1, qa
from . import email_rules
from .lemlist import LemlistClient, LemlistError

KIND, SYSTEM, OP = "sequence_enrolment", "lemlist", "enrol"
NO_EMAIL_STATUSES = ("Not Found", "unknown", "Missing")
# SQL twin of plan_contacts' rule, for counts: someone we could write to (any address we hold that isn't marked missing or for review)
USABLE_EMAIL_SQL = ("({p}.work_email LIKE '%%@%%' AND {p}.erased_at IS NULL AND NOT {p}.suppressed "
                    "AND coalesce({p}.email_verification_status, '') NOT IN ('Not Found', 'unknown', 'Missing', 'Needs Manual Review'))")
STATUS_FROM_EVENT = {"emailsReplied": "replied", "emailsBounced": "bounced", "emailsUnsubscribed": "unsubscribed", "interested": "interested", "notInterested": "not_interested",
                     "emailsInterested": "interested", "emailsNotInterested": "not_interested"}
TRACKED = set(STATUS_FROM_EVENT) | {"emailsSendFailed"}
RANK = {"pushed": 0, "replied": 1, "not_interested": 2, "interested": 3, "bounced": 4, "unsubscribed": 5, "failed": 0}


def plan_contacts(conn: Conn, org_id: str, *, resume_draft: str | None = None) -> dict[str, Any]:
    """Who at this company can be emailed, and why not for the rest. The country rule (rules/email_rules.yaml) is applied last: someone with a
    usable address in a country where cold email needs consent, and no consent recorded, is listed under `consent_needed`, never `eligible`."""
    rows = qa(conn, "SELECT p.id, p.full_name, p.work_email, p.email_verification_status AS st, p.suppressed, p.erased_at, p.email_consent_at, e.title, e.buyer_lane "
                    "FROM employment e JOIN people p ON p.id=e.person_id WHERE e.organization_id=%s ORDER BY p.full_name", (org_id,))
    blocked_emails = {r["e"] for r in qa(conn, "SELECT lower(trim(email)) AS e FROM outbound_enrolments WHERE status IN ('unsubscribed','bounced')")}
    enrolled_emails = {r["e"] for r in qa(conn, """SELECT lower(trim(email)) AS e FROM outbound_enrolments
                      WHERE status <> 'failed' AND campaign_draft_id IS DISTINCT FROM %s::uuid""", (resume_draft,))}
    org = q1(conn, "SELECT country_code FROM organizations WHERE id=%s", (org_id,)) or {}
    rules = email_rules.load()
    rule = email_rules.rule_for(org.get("country_code"), rules)
    out: dict[str, Any] = {"eligible": [], "needs_verification": [], "no_email": 0, "suppressed": 0, "manual_review": 0, "already_enrolled": 0,
                           "consent_needed": [], "email_rule": rule}
    for r in rows:
        card = {"id": str(r["id"]), "name": r["full_name"], "title": r["title"], "lane": r["buyer_lane"], "email_status": r["st"]}
        email = (r["work_email"] or "").strip()
        if r["erased_at"] or r["suppressed"] or email.lower() in blocked_emails:
            out["suppressed"] += 1
        elif email.lower() in enrolled_emails:
            out["already_enrolled"] += 1
        elif not email or "@" not in email or r["st"] in NO_EMAIL_STATUSES:
            out["no_email"] += 1
        elif r["st"] == "Needs Manual Review":
            out["manual_review"] += 1
        elif not (ok := email_rules.check(org.get("country_code"), email, r["email_consent_at"], rules))[0]:
            out["consent_needed"].append({**card, "reason": ok[1]})
        else:
            out["eligible"].append({**card, "email": email, "verified": r["st"] == "Verified"})
            if r["st"] != "Verified":
                out["needs_verification"].append(card)   # usable, but not checked: Lemlist's own checks and the bounce feedback apply
    out["eligible"].sort(key=lambda c: (not c["verified"], c["name"] or ""))
    return out


def ownership_check(conn: Conn, org_id: str, requester_crm_user: int | None) -> dict[str, str] | None:
    """Returns a blocker when the account belongs to another seller or is an existing customer. Not linked to Pipedrive => cannot be checked (reported, not blocked)."""
    crm = q1(conn, """
        SELECT c.owner_id, c.open_deals_count,
               EXISTS (SELECT 1 FROM crm_deals d WHERE d.crm_org_id=c.crm_id AND d.status='open') AS has_open_deal
        FROM crm_organizations c WHERE c.organization_id=%s AND c.link_status='linked'
    """, (org_id,))
    if not crm:
        return None
    if crm["owner_id"] and requester_crm_user != crm["owner_id"]:
        return {"code": "ownership_conflict", "detail": "This account is owned by another seller in Pipedrive: route to the owner."}
    if crm["has_open_deal"] or (crm["open_deals_count"] or 0) > 0:
        return {"code": "open_deal", "detail": "This account already has an open deal in Pipedrive: do not run a cold sequence."}
    return None


def request_enrolment(conn: Conn, draft_id: str, person_ids: list[str], *, by: str, requester_crm_user: int | None) -> dict[str, Any]:
    d = q1(conn, "SELECT d.*, o.display_name, o.canonical_domain FROM campaign_drafts d JOIN organizations o ON o.id=d.organization_id WHERE d.id=%s", (draft_id,))
    if not d:
        raise appr.ApprovalError("draft not found")
    if d["status"] != "approved":
        raise appr.ApprovalError("only an approved draft can be enrolled")
    blocker = ownership_check(conn, str(d["organization_id"]), requester_crm_user)
    if blocker:
        raise appr.ApprovalError(blocker["detail"])
    plan = plan_contacts(conn, str(d["organization_id"]))
    chosen = [c for c in plan["eligible"] if c["id"] in set(person_ids)]
    if not chosen:
        held = [c for c in plan["consent_needed"] if c["id"] in set(person_ids)]
        if held:
            raise appr.ApprovalError(email_rules.blocked_reason(plan["email_rule"], len(held)))
        raise appr.ApprovalError("none of the selected contacts has an email address that can be used")
    steps, prev = [], 0
    for s in d["steps"]:
        steps.append({"subject": s["subject"], "body": s["body"], "delay_days": max(int(s.get("day") or 0) - prev, 0)})
        prev = int(s.get("day") or 0)
    payload = {"draft_id": draft_id, "campaign_name": f"RI {d['display_name']} {datetime.now(timezone.utc):%Y-%m-%d}", "company": d["display_name"], "domain": d["canonical_domain"],
               "contacts": [{"person_id": c["id"], "email": c["email"], "name": c["name"], "title": c["title"]} for c in chosen], "steps": steps}
    a = appr.request(conn, kind=KIND, target_system=SYSTEM, target_id=draft_id, operation=OP, payload=payload, requested_by=by,
                     justification=f"Enrol {len(chosen)} contact(s) at {d['display_name']} in a {len(steps)}-email sequence. The campaign is created in Lemlist but not launched.",
                     idempotency_key=f"enrol:{draft_id}:{hashlib.sha256(','.join(sorted(c['id'] for c in chosen)).encode()).hexdigest()[:16]}")
    return {"approval_id": str(a["id"]), "status": a["status"], "contacts": len(chosen), "ownership_checked": bool(q1(conn, "SELECT 1 FROM crm_organizations WHERE organization_id=%s AND link_status='linked'", (str(d["organization_id"]),)))}


def execute_enrolment(conn: Conn, approval_id: str, client: LemlistClient | None, settings: Settings) -> dict[str, Any]:
    """The only path to Lemlist. Kill switch, approval and payload checks, single use, resumable after an ambiguous failure."""
    if not settings.outbound_enabled:
        raise appr.ApprovalError("outbound is switched off in this environment (RI_OUTBOUND_ENABLED=false)")
    a = q1(conn, "SELECT * FROM approvals WHERE id=%s", (approval_id,))
    if not a:
        raise appr.ApprovalError("approval not found")
    appr.validate_for_execution(conn, approval_id, kind=KIND, target_system=SYSTEM, target_id=a["target_id"], operation=OP, payload=a["payload"])
    if client is None:
        from ..sheets.client import ConnectorBlocked
        raise ConnectorBlocked("Lemlist is not connected")
    p = a["payload"]
    draft = q1(conn, "SELECT organization_id, status FROM campaign_drafts WHERE id=%s", (p["draft_id"],))
    if not draft or draft["status"] != "approved":
        raise appr.ApprovalError("the draft is no longer approved")
    org_id = str(draft["organization_id"])
    requester = q1(conn, "SELECT pipedrive_user_id FROM users WHERE email=%s AND active", (a["requested_by"],))
    blocker = ownership_check(conn, org_id, requester["pipedrive_user_id"] if requester else None)
    if blocker:
        raise appr.ApprovalError(blocker["detail"])
    # Approval covers the exact address, even if the contact record changes afterwards.
    eligible = {(c["id"], c["email"].casefold()) for c in plan_contacts(conn, org_id, resume_draft=p["draft_id"])["eligible"]}
    contacts = [c for c in p["contacts"] if (c["person_id"], c["email"].casefold()) in eligible]
    if not contacts:
        raise appr.ApprovalError("no approved contact is still eligible")
    appr.mark_executing(conn, approval_id, hashlib.sha256(approval_id.encode()).hexdigest()[:12])
    conn.commit()
    prior = (a["result"] or {})
    try:
        cid = prior.get("lemlist_campaign_id")
        skipped = 0
        memberships = {}
        candidates = []
        for contact in contacts:
            history = client.leads_by_email(contact["email"])
            if history and (not cid or any((lead.get("campaign") or {}).get("id") != cid or lead.get("status") == "unsubscribed" for lead in history)):
                skipped += 1
                continue
            if history:
                memberships[contact["email"].lower()] = history[0]
            candidates.append(contact)
        if not candidates:
            raise LemlistError("Every selected address is already enrolled or opted out in Lemlist", 409)
        if not cid:
            cid = client.create_campaign(p["campaign_name"]).get("_id")
            if not cid:
                raise LemlistError("Lemlist did not return a campaign id")
            conn.execute("UPDATE approvals SET result = coalesce(result,'{}'::jsonb) || %s::jsonb WHERE id=%s", (jb({"lemlist_campaign_id": cid}), approval_id))
            conn.commit()
        if not prior.get("steps_added"):
            seqs = client.sequences(cid)
            seq_id = (seqs[0] if isinstance(seqs, list) and seqs else (list(seqs.values())[0] if isinstance(seqs, dict) and seqs else {})).get("_id") if seqs else None
            if not seq_id:
                raise LemlistError("Lemlist returned no sequence for the new campaign")
            for i, s in enumerate(p["steps"]):
                client.add_step(cid, seq_id, subject=s["subject"] if i == 0 else None, message=s["body"].replace("\n", "<br>"), delay_days=s["delay_days"])
            conn.execute("UPDATE approvals SET result = coalesce(result,'{}'::jsonb) || %s::jsonb WHERE id=%s", (jb({"steps_added": True}), approval_id))
            conn.commit()
        done_emails = {r["e"] for r in qa(conn, "SELECT lower(email) AS e FROM outbound_enrolments WHERE campaign_draft_id=%s", (p["draft_id"],))}
        pushed = len(done_emails)
        for c in candidates:
            email = c["email"].lower()
            if email in done_emails:
                continue
            first = (c["name"] or "").split(" ")[0]
            last = " ".join((c["name"] or "").split(" ")[1:])
            r = memberships.get(email)
            if r is None:
                try:
                    r = client.add_lead(cid, c["email"], {"firstName": first, "lastName": last, "companyName": p["company"], "companyDomain": p.get("domain") or "", "jobTitle": c.get("title") or ""})
                except LemlistError as exc:
                    if exc.status == 409 and "LEAD_ALREADY_IN_OTHER_CAMPAIGN" in str(exc):
                        skipped += 1
                        continue
                    raise
            conn.execute("INSERT INTO outbound_enrolments(campaign_draft_id, organization_id, person_id, email, lemlist_campaign_id, lemlist_lead_id, approval_id) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                         "ON CONFLICT (campaign_draft_id, email) DO NOTHING", (p["draft_id"], draft["organization_id"], c["person_id"], c["email"].lower(), cid, (r or {}).get("_id"), approval_id))
            done_emails.add(email)
            pushed += 1
            conn.commit()
        appr.mark_done(conn, approval_id, True, {"lemlist_campaign_id": cid, "leads": pushed, "skipped_existing": skipped})
        audit(conn, actor="system:executor", operation="lemlist_enrolment_created", record_type="campaign_draft", record_id=p["draft_id"], approval_id=approval_id, detail={"campaign": cid, "leads": pushed})
        return {"status": "executed", "lemlist_campaign_id": cid, "leads": pushed, "skipped_existing": skipped, "note": "The campaign is in Lemlist and has not been launched. Review it there and press Launch."}
    except (LemlistError, OSError, httpx.HTTPError) as exc:
        if isinstance(exc, LemlistError) and exc.status and exc.status < 500 and exc.status != 429:
            appr.mark_done(conn, approval_id, False, {"error": str(exc)[:200]})
            conn.commit()                 # the caller turns this into an HTTP error, which would roll the failure record back
            raise
        conn.execute("UPDATE approvals SET status='approved' WHERE id=%s AND status='executing'", (approval_id,))      # ambiguous: a retry resumes from the stored campaign id
        conn.commit()
        raise


def verify_webhook_secret(given: str | None, expected: str | None) -> bool:
    return bool(given and expected and hmac.compare_digest(given.encode(), expected.encode()))


def record_event(conn: Conn, body: dict[str, Any]) -> dict[str, Any]:
    """Lemlist webhook payload -> enrolment status. Unsubscribes and bounces suppress the person everywhere. Replays are harmless."""
    typ = str(body.get("type") or "")
    if typ and typ not in TRACKED:
        return {"recorded": False, "reason": "ignored type"}
    email = str(body.get("leadEmail") or body.get("email") or "").strip().lower()
    cid = body.get("campaignId")
    if not typ or not email:
        return {"recorded": False, "reason": "no type or email"}
    key = hashlib.sha256(f"{typ}|{cid}|{email}|{body.get('_id') or body.get('createdAt') or ''}".encode()).hexdigest()
    matches = qa(conn, """SELECT id, status FROM outbound_enrolments WHERE lower(trim(email))=%s
                          AND (%s::text IS NULL OR lemlist_campaign_id=%s)
                          ORDER BY created_at DESC LIMIT 2 FOR UPDATE""", (email, cid, cid))
    enrol = matches[0] if len(matches) == 1 else None
    row = conn.execute("INSERT INTO outbound_events(enrolment_id, kind, payload, dedupe_key) VALUES (%s,%s,%s,%s) ON CONFLICT (dedupe_key) DO NOTHING RETURNING id",
                       (enrol["id"] if enrol else None, typ, jb({k: body.get(k) for k in ("type", "campaignId", "leadEmail", "createdAt")}), key)).fetchone()
    if not row:
        return {"recorded": False, "reason": "duplicate"}
    new = STATUS_FROM_EVENT.get(typ)
    if enrol and new and RANK[new] >= RANK[enrol["status"]]:
        conn.execute("UPDATE outbound_enrolments SET status=%s, last_event_at=now() WHERE id=%s", (new, enrol["id"]))
    if new == "unsubscribed" or (enrol and new == "bounced"):
        conn.execute("UPDATE people SET suppressed=true, suppression_reason=%s WHERE lower(trim(work_email))=%s AND NOT suppressed",
                     ("unsubscribed through Lemlist" if new == "unsubscribed" else "email bounced in Lemlist", email))
    return {"recorded": True, "status": new, "matched": bool(enrol)}
