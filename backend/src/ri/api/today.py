"""Today: who to contact today, and why.

``GET /api/today/brief`` answers one question for the person who opens the app in the morning: which companies to contact today, why
now, and what to do about each. It only reads data the platform already holds (no paid calls):

* replies to our campaigns in the last few days (Lemlist activity and campaign enrolments): answer them first;
* tenders that close soon: official notices from buyers in our ICP sectors, and open notices the lead list's own research found at
  target companies (the deadline is read from the research text);
* fresh buying signals at target companies: the platform's checked signals, and the lead list's own research log (its "Verified
  event", with the opening question the research suggests);
* follow-ups: people who clicked a link in our email, or opened it more than once, in the last week and have not answered.

The answer:

* ``actions``: today's list, most urgent first, one per company: ``DO_LIMIT`` items less the ones already dealt with today, so the
  list does not refill while the person works through it. Each says what happened (with date and source), who to contact (the people
  we know there, best match first, with photo), a suggested opening line, and the best-fit service when the fit engine is available.
  Items the person marked done (30 days) or "not now" (3 days) are left out.
* ``done_today``: what the person marked done or "not now" today (the page's progress: "2 of 7 done").
* ``queue``: the next items in the same order, for "up next".
* ``week``: tender deadlines in the next ``WEEK_DAYS`` days (for a timeline) and how many new signals, tenders, replies and campaigns
  came in since the last visit.
* ``new``: what appeared since the person's last visit before today (or in the last 7 days on a first visit), newest first; an item
  that is also on the list says so (``in_list``).
* ``glance``: a few numbers about the pipeline.
* ``next_up``: when little is left, the lead list's top-priority companies and the list's own next step for each.

Each call records a ``today_viewed`` product event; that is how "since your last visit" is known. ``POST /api/today/brief/items``
records "done", "not now", "undo" or "opened" (the person followed the item's link) for one item (product event ``today_item``), which is
also the tracking of what was acted on.
"""
from __future__ import annotations

import hashlib
import importlib
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ..access import Principal, org_scope_sql
from ..audit import audit
from ..db import Conn, jb, q1, qa
from ..target_market import target_region, target_sql
from .auth import require

log = logging.getLogger(__name__)

DO_LIMIT = 7            # items in "Do today"
TENDER_CAP = 3          # at most this many tenders in "Do today", so fresh signals still show
FOLLOW_CAP = 2          # at most this many follow-ups in "Do today"
QUEUE_LIMIT = 14        # further items, in the same order, for "show more"
NEW_LIMIT = 60          # lines in "New since your last visit" (the page shows the first few)
FRESH_DAYS = 21         # a signal older than this is no longer a reason to write today
TENDER_DAYS = 30        # tenders closing within this many days (prequalification needs time)
REPLY_DAYS = 7          # replies from the last week
FOLLOW_DAYS = 7         # opens and clicks from the last week
WEEK_DAYS = 14          # the deadline timeline
CONTACTS_SHOWN = 3      # people shown per company
NEWS_DAYS = 30          # "accounts with news this month"
FIRST_VISIT_DAYS = 7
MAX_SINCE_DAYS = 14
DONE_DAYS = {"done": 30, "later": 3}
REPLY_TYPES = ("emailsReplied", "emailsInterested", "interested", "linkedinReplied")
FOLLOW_TYPES = ("emailsOpened", "emailsClicked")
# someone who answered, bounced or asked to stop is not a follow-up
STOP_TYPES = (*REPLY_TYPES, "emailsBounced", "emailsUnsubscribed", "emailsNotInterested", "notInterested")
KINDS = ("reply", "tender", "signal", "follow_up")
_PRIORITY = {"P1": 0, "P2": 1, "P3": 2}
_WB_STRENGTH = {"high": 3, "medium": 2}
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DAY_MONTH = re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r"(?:,?\s+(20\d\d))?", re.I)
_MONTH_DAY = re.compile(r"\b" + _MON + r"\s*(\d{1,2})(?:st|nd|rd|th)?(?!\d)(?:,?\s+(20\d\d))?", re.I)
_ISO = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
_DEADLINE_WORDS = re.compile(r"deadline|clos(e|es|ing)\b|\bdue\b|cut-?off|submission|submit|participation|prequalification|\bbids?\b|applications?", re.I)
# a clause about another notice, an older date or the questions round is not the deadline to show
_NOT_THIS_DEADLINE = re.compile(r"question|clarification|separate|original|superseded|previous|prior|older|earlier", re.I)
_TENDER_WORDS = re.compile(r"\b(tenders?|rfx|rfi|rfp|rfq|procurements?|prequalification|framework|supplier pool|bids?|notice)\b", re.I)
KEY_PATTERN = r"^[a-z_]+:[A-Za-z0-9:._-]{1,200}$"
# words that say nothing about which person to ask for ("Head of ...", "... / Manager")
_ROLE_STOP = {"and", "the", "of", "for", "or", "to", "in", "at", "an", "head", "lead", "manager", "director", "senior", "team", "owner", "group",
              "chief", "officer", "vp", "programme", "program", "relevant", "buyer", "persona"}
_SENIORITY = ((re.compile(r"\b(chief|c[etiodfsx]o|ciso|founder|president|managing director|board|vorstand|prezes)\b", re.I), 0),
              (re.compile(r"\b(vp|vice[- ]president|svp|evp)\b", re.I), 1),
              (re.compile(r"\b(head|director|dyrektor|leiter)\b", re.I), 2),
              (re.compile(r"\b(manager|lead|kierownik)\b", re.I), 3))


# ------------------------------------------------------------------ small helpers

def _iso_date(value: Any) -> date | None:
    m = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def _safe_url(value: Any) -> str | None:
    u = str(value or "").strip()
    return u if re.match(r"^https?://[^\s]+$", u, re.I) else None


def _host(url: str | None) -> str | None:
    if not url:
        return None
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host or None


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _make(day: int, month: int, year: int | None, today: date) -> date | None:
    try:
        d = date(year or today.year, month, day)
    except ValueError:
        return None
    if year is None and d < today - timedelta(days=180):
        d = d.replace(year=d.year + 1)
    return d


def deadline_in(text: str, today: date) -> date | None:
    """The earliest date on or after today that the text gives as a deadline ("closes 30 Nov 2026 12:00", "deadline 1 Dec 2026",
    "participation requests due 21 Dec"). Only clauses that talk about a deadline are read, not those about the questions round, an
    older or superseded date or a separate notice; past dates are ignored."""
    found: list[date] = []
    for sentence in re.split(r"(?<=[.;,])\s+|\s+(?:while|but|whereas)\s+", text or ""):
        if not _DEADLINE_WORDS.search(sentence) or _NOT_THIS_DEADLINE.search(sentence):
            continue
        for m in _DAY_MONTH.finditer(sentence):
            d = _make(int(m[1]), _MONTHS[m[2].lower()[:3]], int(m[3]) if m[3] else None, today)
            if d:
                found.append(d)
        for m in _MONTH_DAY.finditer(sentence):
            d = _make(int(m[2]), _MONTHS[m[1].lower()[:3]], int(m[3]) if m[3] else None, today)
            if d:
                found.append(d)
        for m in _ISO.finditer(sentence):
            try:
                found.append(date(int(m[1]), int(m[2]), int(m[3])))
            except ValueError:
                pass
    upcoming = [d for d in found if d >= today]
    return min(upcoming) if upcoming else None


def _org(o: dict[str, Any] | None, contacts: bool) -> dict[str, Any] | None:
    if not o:
        return None
    return {"id": o["id"], "name": o["name"], "country": o["country"], "region": target_region(o["country"]),
            "priority": o["list_priority"] if o["list_priority"] in _PRIORITY else None, "relationship": o["relationship"],
            "people": int(o["people"]) if contacts else None, "people_with_email": int(o["people_with_email"]) if contacts else None}


# ------------------------------------------------------------------ reading

def _targets(conn: Conn, p: Principal) -> dict[str, dict[str, Any]]:
    """Target companies the person may see, with how many people we know there and how many we could email."""
    where, args = org_scope_sql(p, "o.id")
    rows = qa(conn, f"""
        SELECT o.id::text AS id, o.display_name AS name, o.country_code AS country, o.list_priority, o.list_score, o.relationship,
               coalesce(pc.people, 0) AS people, coalesce(pc.with_email, 0) AS people_with_email
        FROM organizations o
        LEFT JOIN (SELECT em.organization_id, count(DISTINCT em.person_id) AS people,
                          count(DISTINCT em.person_id) FILTER (WHERE pe.work_email IS NOT NULL AND NOT pe.suppressed) AS with_email
                   FROM employment em JOIN people pe ON pe.id=em.person_id AND pe.erased_at IS NULL GROUP BY em.organization_id) pc
          ON pc.organization_id=o.id
        WHERE ({where}) AND {target_sql()}""", args)      # noqa: S608
    return {r["id"]: {**r, "list_score": float(r["list_score"]) if r["list_score"] is not None else None} for r in rows}


def _list_signals(conn: Conn, targets: dict[str, dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """The lead list's research log ("BUYING SIGNAL LOG") at target companies, minus what it marks as past or closed."""
    if not targets:
        return []
    out = []
    for r in qa(conn, "SELECT organization_id::text AS oid, original FROM legacy_account_research "
                      "WHERE kind='buying_signal_log' AND organization_id = ANY(%s::uuid[])", (list(targets),)):
        x = r["original"] or {}
        status = _clean(x.get("Status"))
        if status.lower().startswith(("past", "closed")):
            continue
        text = _clean(x.get("Verified event") or x.get("Verified Event"))
        if not text:
            continue
        notes = " ".join(_clean(x.get(k)) for k in ("Verified event", "Source Notes", "Recommended Action"))
        deadline = deadline_in(notes, today)
        is_tender = bool(deadline) and (status.lower().startswith("open notice") or bool(_TENDER_WORDS.search(text))
                                        or "procure" in _clean(x.get("Signal type")).lower())
        url = _safe_url(x.get("Source URL"))
        sid = re.sub(r"[^A-Za-z0-9._-]", "", str(x.get("Signal ID") or ""))[:40] or hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:12]
        out.append({"oid": r["oid"], "sid": sid, "text": text, "happened": _iso_date(x.get("Event date") or x.get("Event Date")),
                    "checked": _iso_date(x.get("Last Verified") or x.get("Research date")),
                    "category": _clean(x.get("Signal type") or x.get("Signal Category")) or None,
                    "strength": _WB_STRENGTH.get(_clean(x.get("Signal Strength")).lower(), 1),
                    "who": _clean(x.get("Relevant Buyer Persona")) or None, "opener": _clean(x.get("Recommended Action")) or None,
                    "url": url, "deadline": deadline if is_tender else None})
    # The log often holds the same notice twice: an older row and a newer one with a revised deadline. Of the tender rows about the
    # same company and the same day, only the most recently checked ones count as tenders.
    newest: dict[tuple[str, date | None], date] = {}
    for s in out:
        if s["deadline"] and s["checked"]:
            k = (s["oid"], s["happened"])
            newest[k] = max(newest.get(k, s["checked"]), s["checked"])
    for s in out:
        if s["deadline"] and s["checked"] and s["checked"] < newest[(s["oid"], s["happened"])]:
            s["deadline"] = None
            s["superseded"] = True
    return [s for s in out if not s.get("superseded")]


def _checked_signals(conn: Conn, p: Principal) -> dict[str, Any]:
    from .buying_signals import buying_signals
    return buying_signals(conn, p, days=NEWS_DAYS)


def _tenders(conn: Conn, p: Principal) -> tuple[list[dict[str, Any]], int]:
    """Open official tenders from buyers in our ICP sectors (banks, other financial services, other ICP sectors), and how many there are."""
    from ..tenders import open_tenders
    out = open_tenders(conn, p, sector="icp", min_days=0, limit=100, sort="deadline")
    items = list(out.get("items") or [])
    return items, int((out.get("counts") or {}).get("total") or len(items))


def _replies(conn: Conn, p: Principal, since: datetime) -> list[dict[str, Any]]:
    """People who answered our outreach since `since`, newest first, one line per person."""
    where, args = org_scope_sql(p, "em.organization_id")
    rows = qa(conn, f"""
        SELECT * FROM (
          SELECT pe.id::text AS person_id, pe.full_name, la.campaign_name, la.happened_at AS at, em.organization_id::text AS oid, em.title
          FROM lemlist_activities la JOIN people pe ON pe.id=la.person_id AND pe.erased_at IS NULL
          JOIN LATERAL (SELECT e.organization_id, e.title FROM employment e WHERE e.person_id=pe.id ORDER BY e.organization_id LIMIT 1) em ON TRUE
          WHERE la.type = ANY(%s) AND la.happened_at >= %s AND ({where})
          UNION ALL
          SELECT pe.id::text, pe.full_name, sc.name, oe.last_event_at, oe.organization_id::text, em.title
          FROM outbound_enrolments oe JOIN people pe ON pe.id=oe.person_id AND pe.erased_at IS NULL
          LEFT JOIN signal_campaigns sc ON sc.id=oe.signal_campaign_id
          LEFT JOIN LATERAL (SELECT e.organization_id, e.title FROM employment e WHERE e.person_id=pe.id AND e.organization_id=oe.organization_id LIMIT 1) em ON TRUE
          WHERE oe.status IN ('replied','interested') AND oe.last_event_at >= %s AND ({where.replace('em.organization_id', 'oe.organization_id')})
        ) x ORDER BY at DESC""", [list(REPLY_TYPES), since, *args, since, *args])      # noqa: S608
    seen: set[str] = set()
    out = []
    for r in rows:
        if r["person_id"] in seen:
            continue
        seen.add(r["person_id"])
        out.append(r)
    return out


def _follow_ups(conn: Conn, p: Principal, now: datetime) -> list[dict[str, Any]]:
    """People who clicked a link in our email, or opened it at least twice, in the last week, and never answered, bounced or asked to
    stop: worth a short personal follow-up. Newest first, one line per person."""
    where, args = org_scope_sql(p, "em.organization_id")
    return qa(conn, f"""
        SELECT pe.id::text AS person_id, pe.full_name, em.organization_id::text AS oid, em.title, max(la.happened_at) AS at,
               (array_agg(la.campaign_name ORDER BY la.happened_at DESC))[1] AS campaign_name,
               count(*) FILTER (WHERE la.type='emailsOpened') AS opens, count(*) FILTER (WHERE la.type='emailsClicked') AS clicks
        FROM lemlist_activities la JOIN people pe ON pe.id=la.person_id AND pe.erased_at IS NULL AND NOT pe.suppressed
        JOIN LATERAL (SELECT e.organization_id, e.title FROM employment e WHERE e.person_id=pe.id ORDER BY e.organization_id LIMIT 1) em ON TRUE
        WHERE la.type = ANY(%s) AND la.happened_at >= %s AND ({where})
          AND NOT EXISTS (SELECT 1 FROM lemlist_activities x WHERE x.person_id=pe.id AND x.type = ANY(%s))
          AND NOT EXISTS (SELECT 1 FROM outbound_enrolments oe WHERE oe.person_id=pe.id AND oe.status <> 'pushed')
        GROUP BY pe.id, pe.full_name, em.organization_id, em.title
        HAVING count(*) FILTER (WHERE la.type='emailsClicked') > 0 OR count(*) FILTER (WHERE la.type='emailsOpened') >= 2
        ORDER BY max(la.happened_at) DESC LIMIT 20""", [list(FOLLOW_TYPES), now - timedelta(days=FOLLOW_DAYS), *args, list(STOP_TYPES)])  # noqa: S608


def _words(text: Any) -> set[str]:
    return {w for w in re.findall(r"[a-zà-ž]{2,}", str(text or "").lower()) if w not in _ROLE_STOP}


def _seniority(title: str | None, listed: str | None) -> int:
    for rx, rank in _SENIORITY:
        if rx.search(listed or "") or rx.search(title or ""):
            return rank
    return 5


def _committee(title: str | None, seniority: str | None, persona: str | None, role: str | None) -> str | None:
    try:
        from .profiles import committee_group
    except ImportError:     # pragma: no cover - the company page module is optional here
        return None
    return committee_group(title, seniority, persona, role)


def _contacts(conn: Conn, p: Principal, ask_for: dict[str, str | None]) -> dict[str, dict[str, Any]]:
    """The people we know at each company (organisation id -> who to ask for), best match first: the person whose job title matches
    the role the research suggests, then the lead list's priority, then seniority, then whether we hold an email address. People who
    asked not to be contacted are left out. Only for people allowed to see contacts; the read is audited."""
    if not ask_for or not p.can("contacts.read"):
        return {}
    rows = qa(conn, """
        SELECT DISTINCT ON (e.organization_id, pe.id) e.organization_id::text AS oid, pe.id::text AS id, pe.full_name AS name, e.title,
               pe.photo_url, pe.work_email IS NOT NULL AS has_email,
               (SELECT ps.detail FROM person_sources ps WHERE ps.person_id=pe.id AND ps.system='sheets' ORDER BY ps.first_seen_at DESC LIMIT 1) AS lead
        FROM employment e JOIN people pe ON pe.id=e.person_id AND pe.erased_at IS NULL AND NOT pe.suppressed
        WHERE e.organization_id = ANY(%s::uuid[])
        ORDER BY e.organization_id, pe.id, (e.valid_to IS NULL) DESC, e.observed_at DESC NULLS LAST""", (list(ask_for),))
    by_org: dict[str, list[tuple[tuple[Any, ...], dict[str, Any]]]] = {}
    for r in rows:
        lead = r["lead"] if isinstance(r["lead"], dict) else {}
        listed = {k: _clean(lead.get(k)) for k in ("seniority", "persona", "committee_role", "priority") if _clean(lead.get(k)) not in ("", "Unknown")}
        match = len(_words(ask_for.get(r["oid"])) & _words(f"{r['title'] or ''} {listed.get('persona', '')}"))
        rank = (-match, _PRIORITY.get(listed.get("priority", ""), 5), _seniority(r["title"], listed.get("seniority")), 0 if r["has_email"] else 1,
                (r["name"] or "").casefold())
        by_org.setdefault(r["oid"], []).append((rank, {
            "id": r["id"], "name": r["name"], "title": r["title"], "photo_url": _safe_url(r["photo_url"]), "has_email": bool(r["has_email"]),
            "matches": match > 0,
            "committee": _committee(r["title"], listed.get("seniority"), listed.get("persona"), listed.get("committee_role"))}))
    out = {oid: {"people": [x for _, x in sorted(people, key=lambda t: t[0])[:CONTACTS_SHOWN]], "total": len(people)}
           for oid, people in by_org.items()}
    shown = sum(len(v["people"]) for v in out.values())
    if shown:
        audit(conn, actor=p.email, operation="pii_read", record_type="today_brief", record_id=None, detail={"count": shown})
    return out


def _fits(conn: Conn, oids: list[str]) -> dict[str, dict[str, Any]]:
    """The best-fit service for each company, from the fit engine (``ri.solutions.best_fit_many``: {org id: {service, label, level,
    why}}) when it is installed. Anything it cannot answer is simply left out: the page works without it."""
    if not oids:
        return {}
    try:
        best_fit_many = importlib.import_module("ri.solutions").best_fit_many
    except (ImportError, AttributeError):
        return {}
    try:
        with conn.transaction():        # a savepoint: a failure here must not spoil the rest of the brief
            raw = best_fit_many(conn, oids)
    except Exception:       # noqa: BLE001 - optional enrichment of the page
        log.warning("today: best-fit services unavailable", exc_info=True)
        return {}
    out: dict[str, dict[str, Any]] = {}
    for oid, fit in (raw or {}).items() if isinstance(raw, dict) else ():
        if not isinstance(fit, dict):
            continue
        name = _clean(fit.get("label") or fit.get("name"))
        if name and fit.get("level") != "weak":
            out[str(oid)] = {"service": fit.get("service"), "name": name, "level": fit.get("level"), "why": _clean(fit.get("why")) or None}
    return out


def _done_today(conn: Conn, p: Principal, now: datetime) -> list[dict[str, Any]]:
    """What the person marked done or "not now" since the start of today (an "undo" takes it back): the page's progress."""
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = qa(conn, """
        SELECT DISTINCT ON (pe.detail->>'key') pe.detail->>'key' AS key, pe.detail->>'outcome' AS outcome, pe.detail->>'label' AS label,
               pe.detail->>'kind' AS kind, pe.detail->>'slot' AS slot, pe.organization_id::text AS oid, o.display_name AS org_name, pe.at
        FROM product_events pe LEFT JOIN organizations o ON o.id=pe.organization_id
        WHERE pe.user_id=%s AND pe.event='today_item' AND pe.at >= %s AND pe.detail->>'outcome' <> 'opened'
        ORDER BY pe.detail->>'key', pe.at DESC""", (p.user_id, start))
    out = []
    for r in sorted(rows, key=lambda r: r["at"]):
        if r["outcome"] not in DONE_DAYS:
            continue
        kind = r["kind"] if r["kind"] in KINDS else {"reply": "reply", "tender": "tender", "follow": "follow_up"}.get(r["key"].split(":")[0], "signal")
        out.append({"key": r["key"], "outcome": r["outcome"], "kind": kind, "slot": "next" if r["slot"] == "next" else "today",
                    "organization_id": r["oid"], "label": r["org_name"] or _clean(r["label"]) or None, "at": r["at"]})
    return out


def _campaigns(conn: Conn, p: Principal, since: datetime) -> list[dict[str, Any]]:
    mine = p.role in {"sales_manager", "admin"}
    return qa(conn, """
        SELECT c.id::text AS id, c.name, c.pushed_at AS at,
               (SELECT count(*) FROM signal_campaign_members m WHERE m.campaign_id=c.id AND m.included) AS people
        FROM signal_campaigns c
        WHERE c.pushed_at >= %s AND c.status <> 'archived' AND (c.created_by=%s OR %s)
        ORDER BY c.pushed_at DESC LIMIT 10""", (since, p.email, mine))


def _live_campaigns(conn: Conn, p: Principal) -> int:
    mine = p.role in {"sales_manager", "admin"}
    row = q1(conn, "SELECT count(*) AS n FROM signal_campaigns WHERE pushed_at IS NOT NULL AND status NOT IN ('archived') "
                   "AND (created_by=%s OR %s)", (p.email, mine))
    return int(row["n"]) if row else 0


def _hidden(conn: Conn, p: Principal, now: datetime) -> set[str]:
    """Items this person marked done (hidden 30 days) or "not now" (3 days); an "undo" after either brings the item back."""
    rows = qa(conn, """
        SELECT DISTINCT ON (detail->>'key') detail->>'key' AS key, detail->>'outcome' AS outcome, at
        FROM product_events WHERE user_id=%s AND event='today_item' AND at > %s AND detail->>'outcome' <> 'opened'
        ORDER BY detail->>'key', at DESC""", (p.user_id, now - timedelta(days=max(DONE_DAYS.values()))))
    return {r["key"] for r in rows if r["outcome"] in DONE_DAYS and r["at"] > now - timedelta(days=DONE_DAYS[r["outcome"]])}


def _visit(conn: Conn, p: Principal, now: datetime) -> tuple[datetime, datetime | None]:
    """Where "new" starts: the person's last visit before today (at most 14 days back, at least a day), or a week on a first visit.
    Records this visit."""
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    row = q1(conn, "SELECT max(at) AS at FROM product_events WHERE user_id=%s AND event='today_viewed' AND at < %s", (p.user_id, start_of_day))
    last = row["at"] if row else None
    conn.execute("INSERT INTO product_events(user_id, event, detail) VALUES (%s,'today_viewed',%s)", (p.user_id, jb({"where": "today"})))
    if last is None:
        return now - timedelta(days=FIRST_VISIT_DAYS), None
    return max(min(last, now - timedelta(days=1)), now - timedelta(days=MAX_SINCE_DAYS)), last


def _next_up(conn: Conn, targets: dict[str, dict[str, Any]], skip: set[str], contacts: bool) -> list[dict[str, Any]]:
    """The lead list's top-priority companies and the list's own next step: where to look when nothing is urgent."""
    ranked = sorted((o for o in targets.values() if o["id"] not in skip and o["list_priority"] in _PRIORITY),
                    key=lambda o: (_PRIORITY[o["list_priority"]], -(o["list_score"] or 0), o["name"].lower()))[:3]
    if not ranked:
        return []
    notes = {r["oid"]: r["original"] or {} for r in qa(conn, """
        SELECT DISTINCT ON (organization_id) organization_id::text AS oid, original FROM legacy_account_research
        WHERE kind='master_account' AND organization_id = ANY(%s::uuid[]) ORDER BY organization_id, observed_at DESC""",
        ([o["id"] for o in ranked],))}
    return [{"organization": _org(o, contacts), "next_step": _clean(notes.get(o["id"], {}).get("Next Action")) or None,
             "signal": _clean(notes.get(o["id"], {}).get("Latest Linked Event") or notes.get(o["id"], {}).get("Strongest Buying Signal")) or None}
            for o in ranked]


# ------------------------------------------------------------------ the brief

def brief(conn: Conn, p: Principal, *, now: datetime | None = None) -> dict[str, Any]:
    from ..outbound.signal_campaigns import ANGLES, DEFAULT_ANGLE

    now = now or datetime.now(timezone.utc)
    today = now.date()
    contacts = p.can("contacts.read")
    since, last_visit = _visit(conn, p, now)
    hidden = _hidden(conn, p, now)
    done_today = _done_today(conn, p, now)
    # a company whose news was marked done or "not now" stays quiet for that while: its other news does not take the empty place
    quiet = {k.split(":")[1] for k in hidden if k.startswith(("signal:", "list:"))}
    targets = _targets(conn, p)

    candidates: list[dict[str, Any]] = []       # every possible "Do today" item; `rank` decides the order
    news: list[dict[str, Any]] = []             # every "new since" line (filtered against the chosen actions at the end)
    with_news: set[str] = set()

    # 1. replies: the person is waiting for an answer
    if contacts:
        for r in _replies(conn, p, now - timedelta(days=max(REPLY_DAYS, (now - since).days + 1))):
            o = targets.get(r["oid"]) or (q1(conn, "SELECT id::text AS id, display_name AS name, country_code AS country, list_priority, list_score, "
                                                   "relationship, 0 AS people, 0 AS people_with_email FROM organizations WHERE id=%s", (r["oid"],)) if r["oid"] else None)
            key = f"reply:{r['person_id']}:{r['at']:%Y%m%d}"
            item = {"key": key, "kind": "reply", "organization": _org(o, contacts), "title": r["campaign_name"], "text": None,
                    "happened": r["at"], "person": {"id": r["person_id"], "name": r["full_name"], "title": r["title"]}, "strength": 3}
            if r["at"] >= now - timedelta(days=REPLY_DAYS):
                candidates.append({**item, "rank": (0, -r["at"].timestamp())})
            if r["at"] >= since:
                news.append({"key": key, "kind": "reply", "at": r["at"], "organization": item["organization"], "person": item["person"],
                             "text": r["campaign_name"]})
        # people who clicked or kept opening our email and have not answered: a short personal follow-up
        for r in _follow_ups(conn, p, now):
            o = targets.get(r["oid"]) if r["oid"] else None
            candidates.append({"key": f"follow:{r['person_id']}:{r['at']:%Y%m%d}", "kind": "follow_up", "organization": _org(o, contacts),
                               "title": r["campaign_name"], "text": None, "happened": r["at"], "strength": 2,
                               "person": {"id": r["person_id"], "name": r["full_name"], "title": r["title"]},
                               "engagement": {"opens": int(r["opens"]), "clicks": int(r["clicks"])},
                               "rank": (3.5, -r["at"].timestamp())})

    # 2. official tenders from ICP buyers
    tenders, tender_count = _tenders(conn, p)
    for t in tenders:
        acc = (t.get("account") or {}) if isinstance(t.get("account"), dict) else {}
        o = targets.get(str(acc.get("id"))) if acc.get("id") else None
        summary = t.get("summary") if isinstance(t.get("summary"), dict) else {}
        deadline = datetime.fromisoformat(t["deadline"]) if isinstance(t.get("deadline"), str) else t.get("deadline")
        days_left = t.get("days_left")
        url = _safe_url(t.get("url"))
        key = f"tender:{t['id']}"
        title = _clean(summary.get("title_en") or t.get("title"))
        item = {"key": key, "kind": "tender", "organization": _org(o, contacts), "buyer": _clean(t.get("buyer")) or None,
                "title": title, "text": _clean(summary.get("summary_en")) or None, "happened": t.get("published"),
                "deadline": deadline, "days_left": days_left, "source": {"name": t.get("source"), "url": url}, "strength": 3,
                "fit": ((summary.get("fit") or {}).get("level")) if isinstance(summary.get("fit"), dict) else None,
                "value_eur": t.get("value_eur"), "tender_id": t.get("notice_id")}
        if days_left is not None and days_left <= TENDER_DAYS:
            candidates.append({**item, "rank": (1 if days_left <= 7 else 2, days_left)})
        published = datetime.fromisoformat(t["published"]) if isinstance(t.get("published"), str) else t.get("published")
        if published and published >= since:
            news.append({"key": key, "kind": "tender", "at": published, "organization": item["organization"], "buyer": item["buyer"],
                         "text": title, "deadline": deadline, "url": url})

    # 3. buying signals the platform checked (and tenders it found at target companies)
    checked = _checked_signals(conn, p)
    who_by_rule = {k["rule_id"]: k.get("who_to_contact") for k in checked.get("kinds", [])}
    for c in checked.get("companies", []):
        org = c["organization"]
        o = targets.get(org["id"])
        if not o:
            continue
        with_news.add(org["id"])
        s = c["signals"][0]
        happened = s.get("happened")
        age = s.get("age_days")
        tender = s.get("tender") or None
        deadline = datetime.fromisoformat(tender["deadline"]) if tender and isinstance(tender.get("deadline"), str) else (tender or {}).get("deadline")
        days_left = max(0, (deadline - now).days) if isinstance(deadline, datetime) else None
        key = f"signal:{org['id']}:{s['id']}"
        ev = s.get("evidence") or {}
        url = _safe_url(ev.get("url") or (tender or {}).get("url"))
        item = {"key": key, "kind": "tender" if tender else "signal", "organization": _org(o, contacts),
                "title": (tender or {}).get("title") or None, "text": _clean(s.get("summary_en") or s.get("summary")),
                "happened": happened, "deadline": deadline, "days_left": days_left, "strength": int(c["strength"]),
                "source": {"name": _host(url) or ev.get("source"), "url": url}, "who": who_by_rule.get(s["rule_id"]),
                "opener": ANGLES.get(s["rule_id"], DEFAULT_ANGLE), "opener_kind": "line", "rule_id": s["rule_id"], "more": max(0, len(c["signals"]) - 1)}
        if tender and days_left is not None and days_left <= TENDER_DAYS:
            candidates.append({**item, "rank": (1 if days_left <= 7 else 2, days_left)})
        elif age is not None and age <= FRESH_DAYS:
            candidates.append({**item, "rank": (3 if c["strength"] >= 3 and age <= 7 else 4, -int(c["strength"]),
                                                _PRIORITY.get(o["list_priority"] or "", 9), age, -(o["list_score"] or 0))})
        if isinstance(happened, datetime) and happened >= since:
            news.append({"key": key, "kind": item["kind"], "at": happened, "organization": item["organization"], "text": item["text"], "url": url})

    # 4. the lead list's own research log
    since_day = since.date()
    for s in _list_signals(conn, targets, today):
        o = targets[s["oid"]]
        age = (today - s["happened"]).days if s["happened"] else None
        if age is not None and 0 <= age <= NEWS_DAYS:
            with_news.add(s["oid"])
        key = f"list:{s['oid']}:{s['sid']}"
        days_left = (s["deadline"] - today).days if s["deadline"] else None
        item = {"key": key, "kind": "tender" if s["deadline"] else "signal", "organization": _org(o, contacts), "title": s["category"],
                "text": s["text"], "happened": s["happened"], "deadline": s["deadline"], "days_left": days_left, "strength": s["strength"],
                "source": {"name": _host(s["url"]), "url": s["url"]} if s["url"] else None, "who": s["who"], "opener": s["opener"],
                # the list's research suggests either an opening question for the buyer or a step for us ("verify the documents")
                "opener_kind": "line" if s["opener"] and "?" in s["opener"] else "step"}
        if days_left is not None and days_left <= TENDER_DAYS:
            candidates.append({**item, "rank": (1 if days_left <= 7 else 2, days_left)})
        elif age is not None and 0 <= age <= FRESH_DAYS and s["strength"] >= 2:
            candidates.append({**item, "rank": (3 if s["strength"] >= 3 and age <= 7 else 4, -s["strength"],
                                                _PRIORITY.get(o["list_priority"] or "", 9), age, -(o["list_score"] or 0))})
        if s["happened"] and since_day <= s["happened"] <= today:
            news.append({"key": key, "kind": item["kind"], "at": s["happened"], "organization": item["organization"], "text": s["text"],
                         "url": s["url"]})

    # choose: most urgent first, one item per company, nothing the person put aside; at most TENDER_CAP tenders and FOLLOW_CAP
    # follow-ups in today's list, so fresh signals still show. Today's list holds DO_LIMIT items less those already dealt with today,
    # so it does not refill while the person works through it. What does not fit waits in `queue` in the same order.
    candidates.sort(key=lambda c: c["rank"])
    eligible: list[dict[str, Any]] = []
    companies: set[str] = set()
    for c in candidates:
        oid = (c.get("organization") or {}).get("id")
        if c["key"] in hidden or (c["kind"] == "signal" and oid in quiet) or (oid and oid in companies):
            continue
        if oid:
            companies.add(oid)
        eligible.append({k: v for k, v in c.items() if k != "rank"})
    done_kinds = [d["kind"] for d in done_today if d["slot"] == "today"]
    limit = max(0, DO_LIMIT - len(done_kinds))
    caps = {"tender": TENDER_CAP, "follow_up": FOLLOW_CAP}
    actions: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for c in eligible:
        # what was dealt with today still counts towards the caps, so the rest of the list stays as it was this morning
        used = sum(a["kind"] == c["kind"] for a in actions) + done_kinds.count(c["kind"])
        if len(actions) < limit and not (c["kind"] in caps and used >= caps[c["kind"]]):
            actions.append(c)
        else:
            rest.append(c)
    queue = rest[:QUEUE_LIMIT]

    # who to contact and the best-fit service, for the companies on the page
    listed = actions + queue
    ask_for = {a["organization"]["id"]: a.get("who") for a in listed if a.get("organization")}
    people = _contacts(conn, p, ask_for)
    fits = _fits(conn, list(ask_for))
    for a in listed:
        oid = (a.get("organization") or {}).get("id")
        a["contacts"] = people.get(oid, {}).get("people", []) if oid else []
        a["fit"] = fits.get(oid) if oid else None

    where = {**{a["key"]: "next" for a in queue}, **{a["key"]: "today" for a in actions}}
    campaigns = [{"key": f"campaign:{c['id']}", "kind": "campaign", "at": c["at"], "campaign": {"id": c["id"], "name": c["name"], "people": int(c["people"])}}
                 for c in _campaigns(conn, p, since)]
    fresh = sorted([{**n, "in_list": where.get(n["key"])} for n in news] + campaigns, key=lambda n: _ts(n["at"]), reverse=True)
    counts = {k: sum(n["kind"] == k for n in fresh) for k in ("signal", "tender", "reply", "campaign")}

    # the deadline timeline: tenders that close in the next WEEK_DAYS days, one per company or buyer, soonest first
    deadlines: list[dict[str, Any]] = []
    seen: set[str] = set()
    for c in sorted((c for c in candidates if c["kind"] == "tender" and c.get("days_left") is not None and 0 <= c["days_left"] < WEEK_DAYS
                     and c["key"] not in hidden), key=lambda c: c["days_left"]):
        org = c.get("organization") or {}
        who = org.get("id") or (c.get("buyer") or "").casefold()
        if not who or who in seen:
            continue
        seen.add(who)
        d = c["deadline"].date() if isinstance(c["deadline"], datetime) else c["deadline"]
        if not isinstance(d, date):
            continue
        deadlines.append({"key": c["key"], "name": org.get("name") or c.get("buyer"), "organization_id": org.get("id"),
                          "date": d, "days_left": c["days_left"], "title": c.get("title"),
                          "url": (c.get("source") or {}).get("url"), "in_list": where.get(c["key"])})
    week = {"start": today, "days": WEEK_DAYS, "deadlines": deadlines, "new": counts}

    glance = {"targets": len(targets), "with_news": len(with_news),
              "people_with_email": sum(int(o["people_with_email"]) for o in targets.values()) if contacts else None,
              "accounts_with_email": sum(1 for o in targets.values() if o["people_with_email"]) if contacts else None,
              "campaigns_live": _live_campaigns(conn, p), "open_tenders": tender_count}

    shown = _clean((q1(conn, "SELECT display_name FROM users WHERE id=%s", (p.user_id,)) or {}).get("display_name"))
    first = shown.split(" ")[0] if shown and "@" not in shown and shown.lower() != p.email.split("@")[0].lower() else None
    return {"generated_at": now, "name": first or None, "since": since, "last_visit": last_visit,
            "actions": actions, "done_today": done_today, "queue": queue, "more_actions": len(rest), "hidden": len(hidden),
            "week": week, "new": fresh[:NEW_LIMIT], "new_total": len(fresh),
            "glance": glance, "next_up": _next_up(conn, targets, companies, contacts) if len(actions) < 3 else []}


def _ts(v: Any) -> float:
    if isinstance(v, datetime):
        return v.timestamp()
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, 12, tzinfo=timezone.utc).timestamp()
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v).timestamp()
        except ValueError:
            return 0.0
    return 0.0


class ItemIn(BaseModel):
    key: str = Field(pattern=KEY_PATTERN)
    outcome: str = Field(pattern="^(done|later|undo|opened)$")
    organization_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    # for the "done today" list: what the item was called (a buyer without a company record has only its name), its kind, and
    # whether it was on today's list or in "up next"
    label: str | None = Field(default=None, max_length=160)
    kind: str | None = Field(default=None, pattern="^(reply|tender|signal|follow_up)$")
    slot: str | None = Field(default=None, pattern="^(today|next)$")


def register_today(app: FastAPI) -> None:
    @app.get("/api/today/brief")
    def today_brief(request: Request, p: Principal = Depends(require("today.read"))):
        with request.app.state.db.tx() as conn:
            return brief(conn, p)

    @app.post("/api/today/brief/items")
    def today_item(body: ItemIn, request: Request, p: Principal = Depends(require("today.read"))):
        """Mark one "Do today" item as done (hidden for 30 days), "not now" (3 days), undo either, or note that its link was opened.
        Recorded as a product event."""
        with request.app.state.db.tx() as conn:
            org = None
            if body.organization_id:
                where, args = org_scope_sql(p, "o.id")
                row = q1(conn, f"SELECT o.id FROM organizations o WHERE o.id=%s AND ({where})", [body.organization_id, *args])      # noqa: S608
                if not row:
                    raise HTTPException(404, "not found")
                org = row["id"]
            detail = {"key": body.key, "outcome": body.outcome, **{k: v for k, v in (("label", _clean(body.label)[:160] or None),
                                                                                    ("kind", body.kind), ("slot", body.slot)) if v}}
            conn.execute("INSERT INTO product_events(user_id, event, organization_id, detail) VALUES (%s,'today_item',%s,%s)",
                         (p.user_id, org, jb(detail)))
            return {"ok": True, "key": body.key, "outcome": body.outcome}
