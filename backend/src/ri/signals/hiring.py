"""Hard-to-fill engineering roles (CAP-03 in the tracker: "same niche role reposted or open 60+ days").

Read from ``job_postings``, which the job board readers keep current; nothing is fetched here. An open ad at a known company counts when
it is still listed (the reader saw it in the last few days) and either
  * it has been open for ``OPEN_DAYS`` days or more, counted from the earlier of its posting date and the day we first saw it, but not
    more than ``EVERGREEN_DAYS`` (an ad left open for over a year is a standing ad, not a search that is failing), or
  * the same role was advertised before and that ad closed in the last ``REPOST_DAYS`` days (titles compared without gender tags,
    punctuation or case), which means the first search did not fill it.
Each ad gives at most one ``job_hard_to_fill`` event (keyed on the ad), and the CAP-03 rule then judges it like any other event, so
talent-pool and internship ads are left out by the rule's own exclusions. The daily lane ``hard_to_fill_roles`` runs this.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import Conn, qa
from ..events.classify import engineering_relevant
from ..events.store import add_claim, store_event

OPEN_DAYS = 60
EVERGREEN_DAYS = 365
SEEN_WITHIN_DAYS = 3
REPOST_DAYS = 180
EVENT_TYPE = "job_hard_to_fill"
# Ads that are always open by design, or are not a hire we could fill: never "hard to fill" (the CAP-03 rule excludes the same words)
_EVERGREEN = re.compile(r"\b(talent pool|talent community|open application|general application|spontaneous application|initiativbewerbung|"
                        r"intern|internship|working student|werkstudent|trainee|praktikum)\b", re.I)
_GENDER = re.compile(r"\((?:[mwfdx]|div|all genders?)(?:\s*[/|,]\s*(?:[mwfdx]|div))*\)|\b(?:m/w/d|f/m/x|m/f/d|w/m/d|all genders)\b", re.I)


def norm_title(title: str | None) -> str:
    t = _GENDER.sub(" ", title or "").casefold()
    t = re.sub(r"[^\w]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _when(v: Any) -> datetime | None:
    if v is None:
        return None
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def find(conn: Conn, now: datetime, limit: int = 200) -> list[dict[str, Any]]:
    """Open ads that qualify and have no event yet, oldest first."""
    rows = qa(conn, f"""
        SELECT j.* FROM job_postings j
        WHERE j.organization_id IS NOT NULL AND j.open_status='open' AND j.last_seen_at >= %s
          AND NOT EXISTS (SELECT 1 FROM events e WHERE e.event_type='{EVENT_TYPE}' AND e.attrs->>'provider'=j.provider
                          AND e.attrs->>'board'=j.board AND e.attrs->>'job_id'=j.job_id)
        ORDER BY least(coalesce(j.posted_at, j.first_seen_at), j.first_seen_at), j.id""", (now - timedelta(days=SEEN_WITHIN_DAYS),))   # noqa: S608
    if not rows:
        return []
    orgs = list({str(r["organization_id"]) for r in rows})
    closed: dict[tuple[str, str], list[datetime]] = {}
    for c in qa(conn, "SELECT organization_id, title, closed_at FROM job_postings WHERE organization_id = ANY(%s::uuid[]) AND closed_at IS NOT NULL "
                      "AND closed_at >= %s", (orgs, now - timedelta(days=REPOST_DAYS * 2))):
        closed.setdefault((str(c["organization_id"]), norm_title(c["title"])), []).append(_when(c["closed_at"]))
    out = []
    for r in rows:
        if not engineering_relevant(r["title"]) or _EVERGREEN.search(r["title"] or ""):
            continue
        start = min(d for d in (_when(r["posted_at"]), _when(r["first_seen_at"])) if d is not None)
        days_open = (now - start).days
        seen = _when(r["first_seen_at"])
        earlier = [d for d in closed.get((str(r["organization_id"]), norm_title(r["title"])), []) if d <= seen and d >= seen - timedelta(days=REPOST_DAYS)]
        reposted = bool(earlier)
        if OPEN_DAYS <= days_open <= EVERGREEN_DAYS or reposted:
            out.append({**r, "days_open": days_open, "reposted": reposted, "previous_closed_at": max(earlier) if earlier else None})
        if len(out) >= limit:
            break
    return out


def emit_hard_to_fill(conn: Conn, now: datetime | None = None, limit: int = 200) -> dict[str, int]:
    """The daily lane: one event per qualifying ad, then the normal path (verification, signal rules, programmes). Idempotent."""
    from ..ingest.process import finish_event
    now = now or datetime.now(timezone.utc)
    found = find(conn, now, limit)
    events = signals = 0
    for j in found:
        why = (f"posted again after an earlier ad for the same role closed on {j['previous_closed_at']:%d %b %Y}" if j["reposted"]
               else f"still open after {j['days_open']} days")
        why_pl = (f"ogłoszenie opublikowane ponownie po zamknięciu wcześniejszego ({j['previous_closed_at']:%d.%m.%Y})" if j["reposted"]
                  else f"ogłoszenie otwarte od {j['days_open']} dni")
        name = j["employer_name"] or j["board"]
        attrs = {"provider": j["provider"], "board": j["board"], "job_id": j["job_id"], "title": j["title"], "team": j["team"], "location": j["location"],
                 "employment_type": j["employment_type"], "tech_keywords": list(j["tech_keywords"] or []), "job_url": j["url"], "open_status": "open",
                 "days_open": j["days_open"], "reposted": j["reposted"],
                 "previous_closed_at": j["previous_closed_at"].isoformat() if j["previous_closed_at"] else None,
                 "posted_at": j["posted_at"].isoformat() if j["posted_at"] else None, "first_seen_at": j["first_seen_at"].isoformat(),
                 "last_seen_at": j["last_seen_at"].isoformat(), "source_tier": "S2", "agency_eligibility": "unknown",
                 # its own fit reason and Polish summary, so the AI fit job never spends on it (as with calendar deadlines)
                 "fit": {"level": "possible", "service": "team_extension", "basis": "hiring",
                         "reason": f"They have not filled this engineering role ({why}). An engineer from us could start while they keep looking."},
                 "summary_pl": f"{name}: {j['title']}, {why_pl}.", "summary_pl_checked": True}
        claim = add_claim(conn, subject=name, predicate="advertises_role",
                          obj={"title": j["title"], "url": j["url"], "days_open": j["days_open"], "reposted": j["reposted"]}, doc_id=None, anchor=j["title"])
        seen = _when(j["last_seen_at"])
        eid, created = store_event(conn, event_type=EVENT_TYPE, subject_name=j["employer_name"], subject_org_id=str(j["organization_id"]),
                                   summary=f"{name}: {j['title']}, {why}.", attrs=attrs,
                                   key_parts=(j["provider"], j["board"], j["job_id"], EVENT_TYPE), event_at=seen, pub_at=seen, claim_ids=[claim],
                                   document_ids=[], origin_group=f"official:{j['provider']}:{j['board']}:{j['job_id']}")
        if created:
            events += 1
            signals += len(finish_event(conn, eid, now=now)["signals"])
    return {"hard_to_fill_events": events, "hard_to_fill_signals": signals, "hard_to_fill_more": int(len(found) >= limit)}
