"""Today: who to contact today, and why.

``GET /api/today/brief`` answers one question for the person who opens the app in the morning: which companies to contact today, why
now, and what to do about each. It only reads data the platform already holds (no paid calls):

* replies to our campaigns in the last few days (Lemlist activity and campaign enrolments): answer them first;
* tenders that close soon: official notices from buyers in our ICP sectors, and open notices the lead list's own research found at
  target companies (the deadline is read from the research text);
* fresh buying signals at target companies: the platform's checked signals, and the lead list's own research log (its "Verified
  event", with the opening question the research suggests).

The answer has four parts:

* ``actions``: at most ``DO_LIMIT`` items, most urgent first, one per company. Each says what happened (with date and source), who to
  ask for, a suggested opening line, and the next step. Items the person marked done (30 days) or "not now" (3 days) are left out.
* ``new``: what appeared since the person's last visit before today (or in the last 7 days on a first visit) that is not already in
  ``actions``, newest first.
* ``glance``: four numbers about the pipeline, each a link on the page.
* ``next_up``: when nothing is urgent, the lead list's top-priority companies and the list's own next step for each.

Each call records a ``today_viewed`` product event; that is how "since your last visit" is known. ``POST /api/today/brief/items``
records "done", "not now", "undo" or "opened" (the person followed the item's link) for one item (product event ``today_item``), which is
also the tracking of what was acted on.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ..access import Principal, org_scope_sql
from ..db import Conn, jb, q1, qa
from ..target_market import target_region, target_sql
from .auth import require

DO_LIMIT = 7            # items in "Do today"
TENDER_CAP = 3          # at most this many tenders in "Do today", so fresh signals still show
QUEUE_LIMIT = 14        # further items, in the same order, for "show more"
NEW_LIMIT = 60          # lines in "New since your last visit" (the page shows the first few)
FRESH_DAYS = 21         # a signal older than this is no longer a reason to write today
TENDER_DAYS = 30        # tenders closing within this many days (prequalification needs time)
REPLY_DAYS = 7          # replies from the last week
NEWS_DAYS = 30          # "accounts with news this month"
FIRST_VISIT_DAYS = 7
MAX_SINCE_DAYS = 14
DONE_DAYS = {"done": 30, "later": 3}
REPLY_TYPES = ("emailsReplied", "emailsInterested", "interested", "linkedinReplied")
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
KEY_PATTERN = r"^[a-z]+:[A-Za-z0-9:._-]{1,200}$"


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

    # choose: most urgent first, one item per company, nothing the person put aside; at most TENDER_CAP tenders in the first
    # DO_LIMIT, so fresh signals still show. What does not fit waits in `queue` (shown on request) in the same order.
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
    actions: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for c in eligible:
        if len(actions) < DO_LIMIT and not (c["kind"] == "tender" and sum(a["kind"] == "tender" for a in actions) >= TENDER_CAP):
            actions.append(c)
        else:
            rest.append(c)

    chosen = {a["key"] for a in actions}
    chosen_orgs = {(a.get("organization") or {}).get("id") for a in actions} - {None}
    fresh = [n for n in news if n["key"] not in chosen and (n.get("organization") or {}).get("id") not in chosen_orgs]
    fresh.sort(key=lambda n: (_ts(n["at"])), reverse=True)

    glance = {"targets": len(targets), "with_news": len(with_news),
              "people_with_email": sum(int(o["people_with_email"]) for o in targets.values()) if contacts else None,
              "accounts_with_email": sum(1 for o in targets.values() if o["people_with_email"]) if contacts else None,
              "campaigns_live": _live_campaigns(conn, p), "open_tenders": tender_count}
    campaigns = [{"key": f"campaign:{c['id']}", "kind": "campaign", "at": c["at"], "campaign": {"id": c["id"], "name": c["name"], "people": int(c["people"])}}
                 for c in _campaigns(conn, p, since)]
    fresh = sorted(fresh + campaigns, key=lambda n: _ts(n["at"]), reverse=True)

    shown = _clean((q1(conn, "SELECT display_name FROM users WHERE id=%s", (p.user_id,)) or {}).get("display_name"))
    first = shown.split(" ")[0] if shown and "@" not in shown and shown.lower() != p.email.split("@")[0].lower() else None
    return {"generated_at": now, "name": first or None, "since": since, "last_visit": last_visit,
            "actions": actions, "queue": rest[:QUEUE_LIMIT], "more_actions": len(rest), "hidden": len(hidden),
            "new": fresh[:NEW_LIMIT], "new_total": len(fresh),
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
            conn.execute("INSERT INTO product_events(user_id, event, organization_id, detail) VALUES (%s,'today_item',%s,%s)",
                         (p.user_id, org, jb({"key": body.key, "outcome": body.outcome})))
            return {"ok": True, "key": body.key, "outcome": body.outcome}
