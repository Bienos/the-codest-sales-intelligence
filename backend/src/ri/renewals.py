"""Renewal radar: past IT contract awards (official TED award notices), who won them, and when they probably end.
The end date is NOT in the notice. It is the dispatch date plus the stated contract duration; with no duration stated it is assumed to be 36 months
and labelled so. Values are deliberately not stored: the value fields in award notices are often placeholders (1, 0.01)."""
from __future__ import annotations

import asyncio
import calendar
from datetime import date, timedelta
from typing import Any

import httpx

from .db import Conn, qa
from .events.classify import services_relevant
from .market import ALPHA3
from .sources.http import request_json
from .sources.procurement import TED_URL
from .sources.util import first_text

IT_CPV = "72000000 72200000 72260000 72300000 72500000 72600000 48000000"
CORE_ISO3 = "GBR DEU AUT CHE SWE DNK NOR FIN POL"
FIELDS = ["publication-number", "notice-title", "buyer-name", "buyer-country", "publication-date", "dispatch-date", "classification-cpv", "links",
          "winner-name", "winner-country", "winner-size", "duration-period-value-lot", "duration-period-unit-lot"]
ASSUMED_MONTHS = 36
MAX_MONTHS = 180            # longer than 15 years is "indefinite" or a data error: treated as no stated term


def _months(value: Any, unit: Any) -> int | None:
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    u = str(unit or "").upper()
    m = v * 12 if u.startswith("YEAR") else v / 30 if u.startswith("DAY") else v if u.startswith("MONTH") else None
    return int(round(m)) if m is not None and 0 < m <= MAX_MONTHS else None


def duration_months(values: Any, units: Any) -> int | None:
    vs = values if isinstance(values, list) else [values] if values not in (None, "") else []
    us = units if isinstance(units, list) else [units]
    found = [m for i, v in enumerate(vs) if (m := _months(v, us[i] if i < len(us) else (us[0] if us else None))) is not None]
    return max(found) if found else None


def add_months(d: date, months: int) -> date:
    y, m0 = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m0 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _day(v: Any) -> date | None:
    t = first_text(v)
    try:
        return date.fromisoformat(t[:10]) if t else None
    except ValueError:
        return None


def _winners(v: Any) -> list[str]:
    if isinstance(v, dict):                                   # {lang: [names]}: take one language, never mix translations of the same name
        v = v.get("eng") or v.get("en") or next(iter(v.values()), [])
    names = [str(x).strip() for x in (v if isinstance(v, list) else [v]) if x and str(x).strip()]
    out: list[str] = []
    for n in names:
        if n not in out:
            out.append(n)
    return out[:5]


def parse_award(n: dict[str, Any]) -> dict[str, Any] | None:
    nid = first_text(n.get("publication-number"))
    if not nid:
        return None
    title = first_text(n.get("notice-title"))
    cpv = [str(c) for c in (n.get("classification-cpv") or [])]
    awarded = _day(n.get("dispatch-date")) or _day(n.get("publication-date"))
    months = duration_months(n.get("duration-period-value-lot"), n.get("duration-period-unit-lot"))
    links = n.get("links") or {}
    url = first_text(links.get("html") if isinstance(links, dict) else None) or f"https://ted.europa.eu/en/notice/-/detail/{nid}"
    wc = first_text(n.get("winner-country"))
    return {"notice_id": nid, "buyer_name": first_text(n.get("buyer-name")), "buyer_country": ALPHA3.get(first_text(n.get("buyer-country")) or "", None),
            "title": title, "cpv": cpv, "winners": _winners(n.get("winner-name")), "winner_country": ALPHA3.get(wc or "", wc),
            "winner_size": first_text(n.get("winner-size")), "awarded_on": awarded, "duration_months": months,
            "expected_end": add_months(awarded, months or ASSUMED_MONTHS) if awarded else None, "end_basis": "stated" if months else "assumed",
            "relevant": services_relevant(title, None, cpv), "official_url": url}


def upsert(conn: Conn, rows: list[dict[str, Any]]) -> int:
    for r in rows:
        conn.execute(
            "INSERT INTO awarded_contracts(notice_id, buyer_name, buyer_country, title, cpv, winners, winner_country, winner_size, awarded_on, duration_months, expected_end, end_basis, relevant, official_url) "
            "VALUES (%(notice_id)s,%(buyer_name)s,%(buyer_country)s,%(title)s,%(cpv)s,%(winners)s,%(winner_country)s,%(winner_size)s,%(awarded_on)s,%(duration_months)s,%(expected_end)s,%(end_basis)s,%(relevant)s,%(official_url)s) "
            "ON CONFLICT (notice_id) DO UPDATE SET buyer_name=EXCLUDED.buyer_name, title=EXCLUDED.title, cpv=EXCLUDED.cpv, winners=EXCLUDED.winners, winner_country=EXCLUDED.winner_country, "
            "winner_size=EXCLUDED.winner_size, awarded_on=EXCLUDED.awarded_on, duration_months=EXCLUDED.duration_months, expected_end=EXCLUDED.expected_end, "
            "end_basis=EXCLUDED.end_basis, relevant=EXCLUDED.relevant, updated_at=now()", r)
    return len(rows)


def link_buyers(conn: Conn) -> int:
    """Attach the buyer to a known account when the name matches exactly (case-insensitive). Never a fuzzy guess."""
    return conn.execute(
        "UPDATE awarded_contracts a SET buyer_org_id = o.id FROM organizations o WHERE a.buyer_org_id IS NULL AND a.buyer_name IS NOT NULL "
        "AND lower(o.display_name) = lower(a.buyer_name)").rowcount


async def fetch_window(client: httpx.AsyncClient, conn: Conn, start: date, end: date, *, pause: float = 1.5, max_pages: int = 80) -> dict[str, int]:
    query = (f"notice-type = can-standard AND classification-cpv IN ({IT_CPV}) AND buyer-country IN ({CORE_ISO3}) "
             f"AND publication-date >= {start:%Y%m%d} AND publication-date <= {end:%Y%m%d}")
    seen = stored = 0
    for page in range(1, max_pages + 1):
        d = await request_json(client, "POST", TED_URL, json={"query": query, "fields": FIELDS, "page": page, "limit": 100, "scope": "ALL", "paginationMode": "PAGE_NUMBER"})
        notices = d.get("notices") or []
        rows = [r for r in (parse_award(n) for n in notices) if r]
        seen += len(notices)
        stored += upsert(conn, rows)
        conn.commit()
        if page * 100 >= int(d.get("totalNoticeCount") or 0) or not notices:
            break
        await asyncio.sleep(pause)
    link_buyers(conn)
    conn.commit()
    return {"seen": seen, "stored": stored, "pages": page}


def run_window(conn: Conn, start: str, end: str) -> dict[str, int]:
    async def go() -> dict[str, int]:
        async with httpx.AsyncClient(timeout=40) as c:
            return await fetch_window(c, conn, date.fromisoformat(start), date.fromisoformat(end))
    return asyncio.run(go())


def ending_soon(conn: Conn, *, months: int = 12, country: str | None = None, q: str | None = None, stated_only: bool = False,
                limit: int = 100, today: date | None = None) -> list[dict[str, Any]]:
    t = today or date.today()
    rows = qa(conn,
              "SELECT a.notice_id, a.buyer_name, a.buyer_country, a.title, a.winners, a.winner_country, a.winner_size, a.awarded_on, a.duration_months, a.expected_end, a.end_basis, "
              "a.official_url, a.buyer_org_id, (o.id IS NOT NULL AND coalesce(o.entity_type,'') <> 'public_body') AS on_lists "
              "FROM awarded_contracts a LEFT JOIN organizations o ON o.id = a.buyer_org_id "
              "WHERE a.relevant AND a.expected_end BETWEEN %s AND %s AND (%s::text IS NULL OR a.buyer_country = %s) "
              "AND (%s::text IS NULL OR a.buyer_name ILIKE %s OR a.title ILIKE %s OR array_to_string(a.winners, ' ') ILIKE %s) AND (NOT %s OR a.end_basis = 'stated') "
              "ORDER BY a.expected_end, a.buyer_name LIMIT %s",
              (t, t + timedelta(days=int(months * 30.5)), country, country, q, f"%{q}%", f"%{q}%", f"%{q}%", stated_only, limit))
    out = []
    for r in rows:
        left = (r["expected_end"] - t).days
        out.append({**r, "buyer_org_id": str(r["buyer_org_id"]) if r["buyer_org_id"] else None, "awarded_on": r["awarded_on"].isoformat() if r["awarded_on"] else None,
                    "expected_end": r["expected_end"].isoformat(), "months_left": max(round(left / 30.5), 0)})
    return out


def incumbents(conn: Conn, *, country: str | None = None, years: int = 3, limit: int = 40, today: date | None = None) -> list[dict[str, Any]]:
    """Who wins IT work from public buyers in our markets: supplier, number of awards, countries, latest award."""
    t = today or date.today()
    return qa(conn,
              "SELECT w AS supplier, count(*) AS awards, array_agg(DISTINCT a.buyer_country ORDER BY a.buyer_country) AS countries, max(a.awarded_on) AS latest, "
              "mode() WITHIN GROUP (ORDER BY a.winner_size) AS size FROM awarded_contracts a, unnest(a.winners) w "
              "WHERE a.relevant AND a.awarded_on >= %s AND (%s::text IS NULL OR a.buyer_country = %s) GROUP BY w ORDER BY awards DESC, w LIMIT %s",
              (t - timedelta(days=365 * years), country, country, limit))


def schedule_recent(conn: Conn, now: Any) -> int:
    """Once a day, re-read the last 5 weeks of award notices (late publications and corrections are picked up; upserts make it safe to repeat)."""
    from .jobs.queue import enqueue
    d = now.date() if hasattr(now, "date") else date.today()
    _, created = enqueue(conn, "ted_awards_window", {"start": (d - timedelta(days=35)).isoformat(), "end": d.isoformat()},
                         idempotency_key=f"ted_awards:recent:{d.isoformat()}", max_attempts=3)
    return int(created)
