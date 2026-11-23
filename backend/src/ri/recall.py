"""Recall assurance: are we finding what is out there? Three independent checks that do not depend on the model being available.
1. Coverage grid: market x kind of finding over the last N days, so an empty cell is visible (e.g. nothing from DACH hiring).
2. Yield drops: a source that still runs but returns far less than it used to (the failure that hid the empty UK tender feed).
3. Reported misses: a seller pastes a page they saw elsewhere; we record whether we already had it, and queue it to be read."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .db import Conn, q1, qa

MARKETS: list[tuple[str, tuple[str, ...]]] = [
    ("UK", ("GB",)), ("DACH", ("DE", "AT", "CH")), ("Nordics", ("SE", "DK", "NO", "FI", "IS")), ("Poland", ("PL",)),
]
KINDS: list[tuple[str, tuple[str, ...]]] = [
    ("Tenders", ("procurement_published", "procurement_amended", "procurement_awarded")),
    ("Funding and deals", ("funding_announced", "acquisition_announced", "relationship_changed", "vendor_selected")),
    ("Programmes and launches", ("programme_announced", "programme_delayed", "programme_completed", "implementation_launched", "product_launch", "integration_release")),
    ("Leaders and hiring", ("technology_leader_appointed", "hiring_surge", "job_posted")),
    ("Regulation", ("regulatory_change", "deadline_approaching")),
]
# cells where a long gap is not a sign of failure: the market has no such feed today. Everything else being empty IS worth a look.
EXPECTED_EMPTY: set[tuple[str, str]] = set()


def coverage_grid(conn: Conn, days: int = 14) -> dict[str, Any]:
    rows = qa(conn, "SELECT market_country AS c, event_type AS t, count(*) AS n FROM events WHERE first_observed_at > now() - make_interval(days => %s) "
                    "AND market_country IS NOT NULL GROUP BY 1, 2", (days,))
    by: dict[tuple[str, str], int] = {(r["c"], r["t"]): int(r["n"]) for r in rows}
    cells: list[dict[str, Any]] = []
    for mname, countries in MARKETS:
        for kname, types in KINDS:
            n = sum(v for (c, t), v in by.items() if c in countries and t in types)
            cells.append({"market": mname, "kind": kname, "count": n, "empty": n == 0 and (mname, kname) not in EXPECTED_EMPTY})
    return {"days": days, "markets": [m for m, _ in MARKETS], "kinds": [k for k, _ in KINDS], "cells": cells,
            "empty": [f"{c['market']}: {c['kind']}" for c in cells if c["empty"]]}


def source_yield_drops(conn: Conn, *, min_history_days: int = 14, min_baseline: int = 5, ratio: float = 0.3) -> list[dict[str, Any]]:
    """Sources whose last 7 days returned under `ratio` of their usual weekly volume. Needs at least `min_history_days` of history so a young
    source is never flagged, and a real baseline so a quiet newsroom (one page a week) is never flagged either."""
    out: list[dict[str, Any]] = []
    for r in qa(conn, "SELECT s.source_id, extract(epoch FROM now() - min(m.started_at)) / 86400 AS span_days, "
                      "coalesce(sum((m.items_new + m.items_changed)) FILTER (WHERE m.started_at > now() - interval '7 days'), 0) AS last7, "
                      "coalesce(sum((m.items_new + m.items_changed)) FILTER (WHERE m.started_at <= now() - interval '7 days'), 0) AS before "
                      "FROM sources s JOIN monitoring_runs m ON m.source_id = s.source_id WHERE s.status = 'active' GROUP BY s.source_id "
                      "HAVING min(m.started_at) < now() - make_interval(days => %s)", (min_history_days,)):
        weeks_before = max((float(r["span_days"]) - 7) / 7, 1.0)
        baseline = float(r["before"]) / weeks_before
        if baseline >= min_baseline and float(r["last7"]) < ratio * baseline:
            out.append({"source_id": r["source_id"], "last_7_days": int(r["last7"]), "usual_per_week": round(baseline)})
    return out


def normalise_url(url: str) -> str:
    u = urlparse(url.strip())
    host = (u.hostname or "").lower().removeprefix("www.")
    return f"{host}{u.path.rstrip('/')}" + (f"?{u.query}" if u.query else "")


def report_miss(conn: Conn, *, url: str, note: str, by: str) -> dict[str, Any]:
    """Record a page a seller found elsewhere. Tells them whether we already had it, and queues it to be read if not."""
    from .api.intel import queue_verification
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("a full web address is needed (starting with http:// or https://)")
    norm = normalise_url(url)
    have = q1(conn, "SELECT id, source_id, fetched_at FROM source_documents WHERE regexp_replace(lower(canonical_url), '^https?://(www\\.)?|/+$', '', 'g') = %s LIMIT 1", (norm,))
    out = queue_verification(conn, org_id=None, url=url.strip(), origin="seller_miss", claimed={"claimed_text": note[:300], "claimed_date": ""}, by=by)
    out["already_known"] = bool(have)
    out["source_id"] = have["source_id"] if have else None
    return out


def misses(conn: Conn, limit: int = 50) -> dict[str, Any]:
    rows = qa(conn, "SELECT v.id, v.url, v.claimed_text AS note, v.status, v.events_created, v.requested_by, v.created_at, v.result "
                    "FROM verification_requests v WHERE v.origin = 'seller_miss' ORDER BY v.created_at DESC LIMIT %s", (limit,))
    items = []
    for r in rows:
        have = q1(conn, "SELECT source_id FROM source_documents WHERE regexp_replace(lower(canonical_url), '^https?://(www\\.)?|/+$', '', 'g') = %s "
                        "AND fetched_at <= %s LIMIT 1", (normalise_url(r["url"]), r["created_at"]))
        items.append({"id": str(r["id"]), "url": r["url"], "note": r["note"], "status": r["status"], "events_created": r["events_created"],
                      "reported_by": r["requested_by"], "reported_at": r["created_at"].isoformat(), "we_already_had_it": bool(have),
                      "from_source": have["source_id"] if have else None})
    total = len(items)
    missed = sum(1 for i in items if not i["we_already_had_it"])
    return {"items": items, "total": total, "missed": missed, "caught": total - missed}
