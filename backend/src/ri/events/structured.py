"""Deterministic event emission for structured sources: procurement notices and job postings (spec 02 §7)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..db import Conn, q1
from .classify import engineering_relevant, services_relevant
from .store import add_claim, store_event

_EVENT_FOR_STATUS = {"open": "procurement_published", "amended": "procurement_amended", "awarded": "procurement_awarded"}


def emit_procurement_events(conn: Conn, notice_id: str, change: str, *, replay: bool = False) -> str | None:
    n = q1(conn, "SELECT * FROM procurement_notices WHERE id=%s", (notice_id,))
    if not n:
        return None
    status = n["status"]
    etype = _EVENT_FOR_STATUS.get(status)
    if status in {"expired", "cancelled", "planning", "unknown"}:
        etype = "procurement_published" if status in {"expired", "planning"} else "negative_cancellation"
    if etype is None:
        return None
    if change.startswith("amended") and status in {"open", "amended"}:
        etype, status = "procurement_amended", "amended"      # a changed deadline/scope is an amendment of the SAME notice
    relevant = services_relevant(n["title"], n["scope"], list(n["service_codes"] or []))
    doc = str(n["document_id"]) if n["document_id"] else None
    claim_ids = [
        add_claim(conn, subject=n["buyer_name"] or n["notice_id"], predicate="has_official_notice",
                  obj={"notice_id": n["notice_id"], "title": n["title"], "status": status,
                       "submission_deadline": n["deadline_at"].isoformat() if n["deadline_at"] else None, "deadline_tz": n["deadline_tz"],
                       "service_codes": list(n["service_codes"] or []), "official_url": n["official_url"]},
                  doc_id=doc, anchor=f"{n['notice_id']}: {n['title']}", kind="structured")]
    attrs: dict[str, Any] = {
        "notice_id": n["notice_id"], "notice_status": status, "buyer_entity": n["buyer_name"], "scope": n["title"],
        "authoritative_url": n["official_url"], "submission_deadline": n["deadline_at"].isoformat() if n["deadline_at"] else None,
        "deadline_tz": n["deadline_tz"], "relevant_services": relevant, "service_codes": list(n["service_codes"] or []),
        "country": n["country"], "source_tier": "S1", "last_status_check": n["last_status_check_at"].isoformat() if n["last_status_check_at"] else None}
    # amendment events are keyed on the new deadline/status so a re-delivered identical amendment never duplicates
    key = (n["notice_id"], etype, n["deadline_at"].isoformat() if n["deadline_at"] else "", status)
    lifecycle = "expired" if status in {"expired", "awarded", "cancelled"} and etype != "procurement_awarded" else "active"
    eid, created = store_event(conn, event_type=etype, subject_name=n["buyer_name"], subject_org_id=str(n["buyer_org_id"]) if n["buyer_org_id"] else None,
                               summary=f"{n['buyer_name'] or 'Buyer'}: {n['title']} ({status})", attrs=attrs, key_parts=key,
                               event_at=n["amended_at"] or n["published_at"], pub_at=n["published_at"], claim_ids=claim_ids,
                               document_ids=[doc] if doc else [], origin_group=f"official:{n['source_id']}:{n['notice_id']}",
                               replay=replay, lifecycle=lifecycle)
    return eid if created else None


_BANKISH = re.compile(r"\b(bank|banken|sparbank|sparkasse|volksbank|raiffeisen|banco|banque|bankas|pankki|sparebank)\b", re.I)
_FINTECHISH = re.compile(r"\b(pay|payments?|fintech|betal\w*|finans\w*|financial|kredit|capital|insur\w*|försäkring\w*|forsikring|versicherung\w*)\b", re.I)


def employer_role(name: str | None) -> str | None:
    """A hint from the employer's NAME only (never from the advert), used so a financial employer can be offered as a candidate account. Unknown stays unknown."""
    n = name or ""
    if _BANKISH.search(n):
        return "bank"
    if _FINTECHISH.search(n):
        return "fintech"
    return None


def emit_job_event(conn: Conn, job_row_id: str, change: str, *, now: datetime | None = None, replay: bool = False) -> str | None:
    j = q1(conn, "SELECT * FROM job_postings WHERE id=%s", (job_row_id,))
    if not j:
        return None
    now = now or datetime.now(timezone.utc)
    if change == "closed":
        etype = "job_closed"
    elif not engineering_relevant(j["title"]):
        return None
    elif change in {"new", "reopened"}:
        etype = "contractor_job_opened" if j["employment_type"] in {"contract", "interim", "freelance"} else "engineering_job_opened"
    else:
        return None
    age = (now - j["posted_at"]).days if j["posted_at"] else None
    attrs = {"provider": j["provider"], "board": j["board"], "job_id": j["job_id"], "title": j["title"], "team": j["team"], "location": j["location"],
             "employment_type": j["employment_type"], "tech_keywords": list(j["tech_keywords"] or []), "posting_age_days": age,
             "job_url": j["url"], "open_status": j["open_status"], "source_tier": "S2", "agency_eligibility": "unknown"}   # role != supplier eligibility
    role = employer_role(j["employer_name"])
    if role:
        attrs["role_of_subject"] = role
    claim = add_claim(conn, subject=j["employer_name"] or j["board"], predicate="advertises_role",
                      obj={"title": j["title"], "employment_type": j["employment_type"], "url": j["url"]}, doc_id=None, anchor=j["title"])
    eid, created = store_event(conn, event_type=etype, subject_name=j["employer_name"], subject_org_id=str(j["organization_id"]) if j["organization_id"] else None,
                               summary=f"{j['employer_name'] or j['board']}: {j['title']} ({j['employment_type']})", attrs=attrs,
                               key_parts=(j["provider"], j["board"], j["job_id"], etype), event_at=j["posted_at"] if etype != "job_closed" else j["closed_at"],
                               pub_at=j["posted_at"], claim_ids=[claim], document_ids=[], origin_group=f"official:{j['provider']}:{j['board']}:{j['job_id']}",
                               replay=replay)
    return eid if created else None


def backfill_procurement_events(conn: Conn, limit: int = 300) -> int:
    """Notices stored by a run that stopped before its events were made (rate limit, outage): make them now. Idempotent: events are keyed on the notice."""
    from ..ingest.process import finish_event
    n = 0
    for r in conn.execute("SELECT n.id FROM procurement_notices n WHERE n.status IN ('open','amended','awarded') AND NOT EXISTS "
                          "(SELECT 1 FROM events e WHERE e.attrs->>'notice_id' = n.notice_id) ORDER BY n.published_at DESC NULLS LAST LIMIT %s", (limit,)).fetchall():
        eid = emit_procurement_events(conn, str(r["id"]), "new")
        if eid:
            finish_event(conn, eid)
            n += 1
    return n
