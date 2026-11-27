"""The Calendar page's month view: what falls on each day of a range of days, in layers a person can switch on and off.

* ``regulatory``: the EU deadlines of the regulatory calendar (ri.regulatory_calendar), each with how many of the person's target accounts it
  applies to. A deadline whose day the law does not fix yet (``approximate``) is never put on a day: it is listed under ``undated`` for
  every range that touches its year, so the page can say "about 2028" without pretending to know the day.
* ``tenders``: the submission deadlines of open official tenders from buyers in our ICP sectors (ri.tenders.open_tenders with sector="icp",
  exactly as Today reads them), on the day the notice gives in its own time zone. Only open tenders: once a deadline passes, the tender
  leaves the calendar.

Row scope is the one of the page each layer comes from: a deadline's account counts and names follow ``org_scope_sql`` (as on
GET /api/regulatory-calendar), and a tender linked to an account the person may not see is left out (open_tenders applies the tenders
page's rule). Each layer also needs the permission of its page: ``accounts.read`` for deadlines, ``today.read`` or ``explorer.read`` for
tenders. A layer the person may not see is dropped and named in ``unavailable``.

Each day carries at most ``per_day`` items (deadlines first, then tenders: banks first, then insurers, payments, lenders, markets and the
other ICP sectors; best fit first, then the biggest value) and says how many more there are. ``per_day=0`` returns the counts only (the
page's year strip uses it to show which months have something)."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import regulatory_calendar as rc
from .access import Principal, org_scope_sql
from .db import Conn

LAYERS = ("regulatory", "tenders")
PER_DAY = 4                # items per day in a month (the rest are counted: "+5 more")
MAX_PER_DAY = 50           # one day opened in full
MAX_SPAN_DAYS = 400        # a month grid is 42 days; a year strip asks for one year of counts
FIT_RANK = {"strong": 0, "possible": 1, "weak": 2, "none": 3}
_OFFSET = re.compile(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2}):?(\d{2})?$", re.I)


def allowed(p: Principal, layer: str) -> bool:
    """The permission of the page a layer comes from."""
    if layer == "regulatory":
        return p.can("accounts.read")
    return p.can("today.read") or p.can("explorer.read")


def notice_day(deadline: datetime, tz: str | None) -> date:
    """The day a tender closes, as the notice states it: in its own time zone (an offset such as "+01:00", or a zone name such as "CET"),
    else in UTC. TED gives a closing date as midnight local time, so "2026-12-03+01:00" is stored as 23:00 UTC on 2 December and is a
    3 December deadline."""
    raw = (tz or "").strip()
    zone: Any = timezone.utc
    m = _OFFSET.match(raw)
    if m:
        sign = -1 if m.group(1) == "-" else 1
        zone = timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0)))
    elif raw:
        try:
            zone = ZoneInfo(raw)
        except (ZoneInfoNotFoundError, ValueError):
            zone = timezone.utc
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline.astimezone(zone).date()


def _regulatory(conn: Conn, p: Principal, start: date, end: date, now: datetime, cal: rc.Calendar | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The deadlines on a day in the range, and the ones of the range's years whose day is not fixed yet."""
    where, args = org_scope_sql(p, "o.id")
    data = rc.calendar(conn, where, args, now=now, cal=cal)
    dated, undated = [], []
    for d in data["items"]:
        item = {**d, "layer": "regulatory"}
        if d["approximate"]:
            if start.year <= d["year"] <= end.year:
                undated.append(item)
        elif start.isoformat() <= d["date"] <= end.isoformat():
            dated.append(item)
    return dated, undated


def _tenders(conn: Conn, p: Principal, start: date, end: date) -> list[dict[str, Any]]:
    """Open tenders from ICP buyers whose deadline falls in the range, as small calendar items."""
    from .tender_sector import GROUP_RANK, group_of
    from .tenders import open_tenders
    out = open_tenders(conn, p, sector="icp", min_days=0, limit=100_000, sort="deadline")
    items = []
    for t in out["items"]:
        day = notice_day(t["deadline"], t["deadline_tz"])
        if not start <= day <= end:
            continue
        s = t.get("summary") or {}
        level = (s.get("fit") or {}).get("level")
        group = group_of(t.get("sector"), t.get("sector_kind"))
        items.append({
            "layer": "tenders", "id": t["id"], "date": day.isoformat(), "deadline": t["deadline"], "deadline_tz": t["deadline_tz"],
            "days_left": t["days_left"], "title": s.get("title_en") or t["title"], "title_pl": s.get("title_pl"), "title_original": t["title"],
            "buyer": t["buyer"], "account": t["account"], "country": t["country"], "url": t["url"], "source": t["source"],
            "value_eur": t["value_eur"], "sector": t["sector"], "kind": t["sector_kind"], "group": group, "fit": level,
            "_rank": (GROUP_RANK.get(group, 9), FIT_RANK.get(level or "", 4), -(t["value_eur"] or 0), t["deadline"], t["id"]),
        })
    items.sort(key=lambda i: i["_rank"])
    for i in items:
        del i["_rank"]
    return items


def events(conn: Conn, p: Principal, start: date, end: date, layers: list[str] | tuple[str, ...] = LAYERS, *, per_day: int = PER_DAY,
           now: datetime | None = None, cal: rc.Calendar | None = None) -> dict[str, Any]:
    """Everything in the asked layers from ``start`` to ``end`` (both included), by day. See the module docstring."""
    if end < start:
        raise ValueError("the range ends before it starts")
    if (end - start).days + 1 > MAX_SPAN_DAYS:
        raise ValueError(f"at most {MAX_SPAN_DAYS} days at a time")
    unknown = [x for x in layers if x not in LAYERS]
    if unknown:
        raise ValueError(f"unknown layer {unknown[0]!r}")
    now = now or datetime.now(timezone.utc)
    per_day = max(0, min(int(per_day), MAX_PER_DAY))
    asked = [x for x in LAYERS if x in layers]
    shown = [x for x in asked if allowed(p, x)]
    found: list[dict[str, Any]] = []
    undated: list[dict[str, Any]] = []
    if "regulatory" in shown:
        dated, undated = _regulatory(conn, p, start, end, now, cal)
        found += dated
    if "tenders" in shown:
        found += _tenders(conn, p, start, end)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for i in found:                                   # deadlines first (they were added first), then tenders in their order
        by_day.setdefault(i["date"], []).append(i)
    days = []
    months: dict[str, dict[str, int]] = {}
    totals = {x: 0 for x in shown}
    for key in sorted(by_day):
        rows = by_day[key]
        counts = {x: sum(1 for r in rows if r["layer"] == x) for x in shown}
        month = months.setdefault(key[:7], {x: 0 for x in shown})
        for x, n in counts.items():
            month[x] += n
            totals[x] += n
        days.append({"date": key, "counts": counts, "total": len(rows), "items": rows[:per_day], "more": max(0, len(rows) - per_day)})
    return {"from": start.isoformat(), "to": end.isoformat(), "today": now.date().isoformat(), "layers": shown,
            "unavailable": [x for x in asked if x not in shown], "per_day": per_day, "days": days, "months": months, "totals": totals,
            "undated": undated}
