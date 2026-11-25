"""One people database across the sheets, Apollo, Pipedrive and Lemlist.

`people` stays the canonical record. Each system that knows a person leaves a row in `person_sources`, so a seller can see at a glance that
a contact came from the sheets, was found in Apollo, sits in Pipedrive with an owner and has replied in Lemlist. Linking is conservative:
the same email address, or a Pipedrive/Lemlist person whose company is already linked. Email addresses are stored but never returned by the API.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Iterable

from .db import Conn, jb, q1, qa

SYSTEM_LABEL = {"sheets": "Sheets", "apollo": "Apollo", "pipedrive": "Pipedrive", "lemlist": "Lemlist", "manual": "Added by hand", "exa": "Found with Exa"}
ENGAGEMENT_ORDER = ["unsubscribed", "bounced", "interested", "replied", "not_interested", "clicked", "opened", "sent", "pushed"]
ACTIVITY_STATUS = {"emailsSent": "sent", "emailsOpened": "opened", "emailsClicked": "clicked", "emailsReplied": "replied", "emailsBounced": "bounced",
                   "emailsUnsubscribed": "unsubscribed", "emailsInterested": "interested", "emailsNotInterested": "not_interested", "interested": "interested",
                   "notInterested": "not_interested"}
FREE_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "icloud.com", "me.com", "proton.me", "protonmail.com",
             "gmx.de", "gmx.net", "web.de", "wp.pl", "onet.pl", "o2.pl", "interia.pl"}


def record_source(conn: Conn, person_id: str, system: str, external_id: str = "", detail: dict[str, Any] | None = None) -> None:
    conn.execute("INSERT INTO person_sources(person_id, system, external_id, detail) VALUES (%s,%s,%s,%s) "
                 "ON CONFLICT (person_id, system, external_id) DO UPDATE SET last_seen_at=now(), detail=person_sources.detail || EXCLUDED.detail",
                 (person_id, system, external_id or "", jb(detail or {})))


def email_domain(email: str | None) -> str | None:
    e = (email or "").strip().lower()
    if "@" not in e:
        return None
    d = e.rsplit("@", 1)[1].removeprefix("www.")
    return None if d in FREE_MAIL else d


def org_for_email(conn: Conn, email: str | None) -> str | None:
    """The account whose website matches the address's domain (exact or parent domain), only when exactly one account matches."""
    d = email_domain(email)
    if not d:
        return None
    parts = d.split(".")
    candidates = [".".join(parts[i:]) for i in range(len(parts) - 1)]
    rows = qa(conn, "SELECT id FROM organizations WHERE canonical_domain = ANY(%s) LIMIT 2", (candidates,))
    return str(rows[0]["id"]) if len(rows) == 1 else None


def person_by_email(conn: Conn, emails: Iterable[str]) -> str | None:
    em = [e.strip().lower() for e in emails if e and "@" in e]
    if not em:
        return None
    r = q1(conn, "SELECT id FROM people WHERE lower(work_email) = ANY(%s) AND erased_at IS NULL ORDER BY created_at LIMIT 1", (em,))
    return str(r["id"]) if r else None


def ensure_person(conn: Conn, *, name: str, email: str | None, org_id: str | None, title: str | None, email_status: str, system: str,
                  external_id: str, detail: dict[str, Any] | None = None) -> str | None:
    """Find the person by email, then by name at the same account (a workbook often has the person without an address, and
    Lemlist or Pipedrive bring the address later), else create them when they can be placed at an account. Never overwrites a
    verified address, and never joins two people whose addresses differ."""
    pid = person_by_email(conn, [email] if email else [])
    if not pid and org_id and name.strip():
        same = q1(conn, "SELECT p.id FROM people p JOIN employment e ON e.person_id=p.id WHERE e.organization_id=%s AND p.erased_at IS NULL "
                        "AND lower(regexp_replace(p.full_name, '\\s+', ' ', 'g')) = lower(regexp_replace(%s, '\\s+', ' ', 'g')) "
                        "AND (p.work_email IS NULL OR lower(p.work_email) = lower(%s)) ORDER BY p.created_at LIMIT 1", (org_id, name.strip(), email or ""))
        pid = str(same["id"]) if same else None
    if not pid and not org_id:
        return None
    if not pid:
        pid = str(conn.execute("INSERT INTO people(full_name, work_email, email_verification_status) VALUES (%s,%s,%s) RETURNING id",
                               (name or (email or "Unknown").split("@")[0], email, email_status if email else "unknown")).fetchone()["id"])     # type: ignore[index]
    elif email:
        conn.execute("UPDATE people SET work_email=%s, email_verification_status=%s WHERE id=%s AND work_email IS NULL", (email, email_status, pid))
    if org_id and not q1(conn, "SELECT 1 FROM employment WHERE person_id=%s AND organization_id=%s", (pid, org_id)):
        conn.execute("INSERT INTO employment(person_id, organization_id, title, verification_status) VALUES (%s,%s,%s,'unverified')", (pid, org_id, title))
    record_source(conn, pid, system, external_id, detail)
    return pid


# ------------------------------------------------------------------ Pipedrive
def _emails(p: dict[str, Any]) -> list[str]:
    raw = p.get("emails") if p.get("emails") is not None else p.get("email")
    out = []
    for e in raw if isinstance(raw, list) else [raw]:
        v = e.get("value") if isinstance(e, dict) else e
        if isinstance(v, str) and "@" in v:
            out.append(v.strip().lower())
    return out


def link_crm_person(conn: Conn, p: dict[str, Any]) -> str | None:
    """Tie a mirrored Pipedrive person to the people database: by email, else create them at the linked (or same-domain) account."""
    if p.get("is_deleted"):
        return None
    emails = _emails(p)
    org_raw = p.get("org_id")
    crm_org = org_raw.get("value") if isinstance(org_raw, dict) else org_raw
    org = q1(conn, "SELECT organization_id FROM crm_organizations WHERE crm_id=%s AND organization_id IS NOT NULL", (crm_org,)) if crm_org else None
    org_id = str(org["organization_id"]) if org else (org_for_email(conn, emails[0]) if emails else None)
    existing = q1(conn, "SELECT person_id FROM crm_persons WHERE crm_id=%s AND person_id IS NOT NULL", (p["id"],))
    pid = str(existing["person_id"]) if existing else None
    if pid:
        record_source(conn, pid, "pipedrive", str(p["id"]))
    else:
        pid = ensure_person(conn, name=p.get("name") or "", email=emails[0] if emails else None, org_id=org_id, title=p.get("job_title"),
                            email_status="Unverified (CRM)", system="pipedrive", external_id=str(p["id"]))
    if pid:
        conn.execute("UPDATE crm_persons SET person_id=%s WHERE crm_id=%s", (pid, p["id"]))
    return pid


# ------------------------------------------------------------------ Lemlist
def apply_lemlist_activity(conn: Conn, a: dict[str, Any]) -> dict[str, Any]:
    """Store one Lemlist activity, link it to a person, and pass reply/bounce/unsubscribe events to the enrolment tracker (which suppresses)."""
    from .outbound.enrol import TRACKED, record_event
    typ = str(a.get("type") or "")
    email = str(a.get("leadEmail") or a.get("email") or "").strip().lower()
    if typ in TRACKED and typ not in ACTIVITY_STATUS:
        return {"stored": False, **record_event(conn, a)}          # e.g. a failed send: tracked on the enrolment only
    if typ not in ACTIVITY_STATUS or "@" not in email:
        return {"stored": False}
    aid = str(a.get("_id") or a.get("id") or hashlib.sha256(f"{typ}|{a.get('campaignId')}|{email}|{a.get('createdAt')}".encode()).hexdigest())
    when = a.get("createdAt") or a.get("date")
    happened = None
    if isinstance(when, str):
        try:
            happened = datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError:
            happened = None
    pid = person_by_email(conn, [email])
    if not pid:
        org_id = org_for_email(conn, email)
        name = " ".join(x for x in (a.get("leadFirstName"), a.get("leadLastName")) if x) or email.split("@")[0]
        pid = ensure_person(conn, name=name, email=email, org_id=org_id, title=a.get("leadJobTitle") or a.get("jobTitle"), email_status="Unverified (Lemlist)",
                            system="lemlist", external_id=str(a.get("campaignId") or ""), detail={"campaign": a.get("campaignName")}) if org_id else None
    elif a.get("campaignId"):
        record_source(conn, pid, "lemlist", str(a.get("campaignId")), {"campaign": a.get("campaignName")})
    row = conn.execute("INSERT INTO lemlist_activities(id, type, email, campaign_id, campaign_name, person_id, happened_at) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                       "ON CONFLICT (id) DO NOTHING RETURNING id", (aid, typ, email, a.get("campaignId"), a.get("campaignName"), pid, happened or datetime.now(timezone.utc))).fetchone()
    tracked: dict[str, Any] = {}
    if row and typ in TRACKED:
        tracked = record_event(conn, {"type": typ, "leadEmail": email, "campaignId": a.get("campaignId"), "_id": aid})
    if row and typ in ("emailsUnsubscribed", "emailsBounced") and pid:
        conn.execute("UPDATE people SET suppressed=true, suppression_reason=coalesce(suppression_reason, %s) WHERE id=%s",
                     ("unsubscribed in Lemlist" if typ == "emailsUnsubscribed" else "email bounced in Lemlist", pid))
    return {**tracked, "stored": bool(row), "person_id": pid}


def sync_lemlist(conn: Conn, client: Any, *, max_pages: int = 30, page_size: int = 100) -> dict[str, int]:
    """Read Lemlist activity newest-first until a page brings nothing new. Commits per page; safe to rerun."""
    out = {"pages": 0, "activities": 0, "new": 0, "linked": 0}
    for page in range(max_pages):
        batch = client.activities(offset=page * page_size, limit=page_size)
        out["pages"] += 1
        if not batch:
            break
        new_here = 0
        for a in batch:
            if not isinstance(a, dict):
                continue
            out["activities"] += 1
            r = apply_lemlist_activity(conn, a)
            if r.get("stored"):
                new_here += 1
                out["linked"] += 1 if r.get("person_id") else 0
        out["new"] += new_here
        conn.execute("INSERT INTO sync_state(system, entity, watermark) VALUES ('lemlist','activities',%s) ON CONFLICT (system, entity) DO UPDATE SET watermark=EXCLUDED.watermark",
                     (datetime.now(timezone.utc).isoformat(),))
        conn.commit()
        if new_here == 0 or len(batch) < page_size:
            break
    return out


# ------------------------------------------------------------------ read model
def engagement(statuses: Iterable[str | None]) -> str | None:
    seen = {s for s in statuses if s}
    return next((s for s in ENGAGEMENT_ORDER if s in seen), None)


def email_state(email: str | None, status: str | None) -> str:
    if not email:
        return "none"
    return "verified" if status == "Verified" else "unverified"
