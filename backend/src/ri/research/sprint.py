"""Research sprint: Exa company research for the lead list's P1 accounts, then P2, a few an hour, until each has a profile.

Why: on 27 Nov 2026 only 9 of about 1,600 target accounts had been researched, and 2 of the 231 P1 accounts. A profile (what they do, their
tech stack and vendors, recent change programmes, how we could help, each line with its page) is what the account page, the fit ranking and
the outreach drafts are built from. A seller's "Research with Exa" button does the same for one account.

Which accounts: target accounts (``target_market.target_sql``) with a website, P1 before P2; within each, on the master list first, then the
list's score. An account is skipped while it has a profile that is done, queued or running, or one that failed in the last ``FAILED_PAUSE_DAYS``.

Money: one profile is about four Exa searches, about 3 US cents (production, Nov 2026). It runs only while
  * Exa research is set up (key and monthly cap; ``research.exa_search.configured``);
  * this month's Exa spend is under the automatic share of the cap (``discovery_auto_budget_share``, 60%): the same line automatic web
    searches stop at, so sellers' own buttons always keep the rest;
  * at least ``AI_RESERVE_FRACTION`` (30%) of this month's AI limit is left (writing the profile uses the model);
  * the last ``STOP_AFTER_FAILURES`` sprint profiles did not all fail (a failure still costs the searches, so a broken run is not repeated).
At ``PER_RUN`` an hour and ``PER_DAY`` a day it spends at most about 40 US cents a day. The lane ``research_sprint`` runs it hourly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import Conn, q1, qa
from ..jobs.queue import enqueue
from ..target_market import target_sql

REQUESTED_BY = "research-sprint"
KIND = "account_profile"
PRIORITIES = ("P1", "P2")
PER_RUN = 2
PER_DAY = 12
MAX_IN_FLIGHT = 3
FAILED_PAUSE_DAYS = 14
STOP_AFTER_FAILURES = 3
EST_COST_USD = 0.04
AI_RESERVE_FRACTION = 0.3


def _settings():
    from ..config import Settings
    return Settings.from_env(require_auth=False)


def left(conn: Conn, now: datetime) -> dict[str, int]:
    """How many P1 and P2 target accounts still have no profile (done or under way)."""
    rows = qa(conn, f"""
        SELECT o.list_priority AS p, count(*) AS n FROM organizations o
        WHERE {target_sql('o')} AND o.list_priority = ANY(%s) AND nullif(btrim(o.canonical_domain), '') IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM account_profiles ap WHERE ap.organization_id=o.id AND ap.status IN ('done','queued','running'))
        GROUP BY 1""", (list(PRIORITIES),))   # noqa: S608 - target_sql builds fixed SQL
    by = {r["p"]: int(r["n"]) for r in rows}
    return {"p1_left": by.get("P1", 0), "p2_left": by.get("P2", 0)}


def candidates(conn: Conn, now: datetime, limit: int) -> list[str]:
    return [str(r["id"]) for r in qa(conn, f"""
        SELECT o.id FROM organizations o
        WHERE {target_sql('o')} AND o.list_priority = ANY(%s) AND nullif(btrim(o.canonical_domain), '') IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM account_profiles ap WHERE ap.organization_id=o.id
                          AND (ap.status IN ('done','queued','running') OR (ap.status='failed' AND ap.created_at > %s)))
        ORDER BY CASE o.list_priority WHEN 'P1' THEN 0 ELSE 1 END, o.in_master_list DESC, o.list_score DESC NULLS LAST, o.id
        LIMIT %s""", (list(PRIORITIES), now - timedelta(days=FAILED_PAUSE_DAYS), limit))]   # noqa: S608


def _ai_share_left(conn: Conn, now: datetime) -> float | None:
    r = q1(conn, "SELECT cap, spent, reserved FROM provider_budgets WHERE provider='anthropic' AND kind='monthly' AND period=%s", (now.strftime("%Y-%m"),))
    if not r or float(r["cap"]) <= 0:
        return None
    return (float(r["cap"]) - float(r["spent"]) - float(r["reserved"])) / float(r["cap"])


def _exa_share_stop(conn: Conn, now: datetime, share: float) -> bool:
    b = q1(conn, "SELECT cap, reserved, spent FROM provider_budgets WHERE provider='exa' AND kind='monthly' AND period=%s", (now.strftime("%Y-%m"),))
    if not b or float(b["cap"]) <= 0:
        return False             # no row yet this month: the research job sets the cap itself and the budget check refuses what does not fit
    return float(b["reserved"]) + float(b["spent"]) + EST_COST_USD > float(b["cap"]) * share


def schedule(conn: Conn, now: datetime | None = None, settings: Any = None) -> dict[str, Any]:
    """The lane step. Numbers only, plus ``held_by`` when something stopped it: not_set_up, repeated_failures, exa_budget, ai_budget,
    daily_limit or queue_busy."""
    from .exa_search import configured
    now = now or datetime.now(timezone.utc)
    st = settings or _settings()
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    today = int((q1(conn, "SELECT count(*) AS n FROM account_profiles WHERE requested_by=%s AND created_at >= %s", (REQUESTED_BY, day_start)) or {"n": 0})["n"])
    out: dict[str, Any] = {"queued": 0, "queued_today": today, **left(conn, now)}
    if not out["p1_left"] and not out["p2_left"]:
        return out
    if not configured(st, conn)["ready"]:
        out["held_by"] = "not_set_up"
        return out
    last = [r["status"] for r in qa(conn, "SELECT status FROM account_profiles WHERE requested_by=%s AND status IN ('done','failed') "
                                          "ORDER BY coalesce(finished_at, created_at) DESC LIMIT %s", (REQUESTED_BY, STOP_AFTER_FAILURES))]
    if len(last) == STOP_AFTER_FAILURES and all(s == "failed" for s in last):
        out["held_by"] = "repeated_failures"
        return out
    if _exa_share_stop(conn, now, float(getattr(st, "discovery_auto_budget_share", 0.6) or 0.6)):
        out["held_by"] = "exa_budget"
        return out
    share = _ai_share_left(conn, now)
    if share is not None and share < AI_RESERVE_FRACTION:
        out["held_by"] = "ai_budget"
        return out
    busy = int((q1(conn, "SELECT count(*) AS n FROM account_profiles WHERE requested_by=%s AND status IN ('queued','running')", (REQUESTED_BY,)) or {"n": 0})["n"])
    room = min(PER_RUN, PER_DAY - today, MAX_IN_FLIGHT - busy)
    if room <= 0:
        out["held_by"] = "daily_limit" if PER_DAY - today <= 0 else "queue_busy"
        return out
    for oid in candidates(conn, now, room):
        rid = str(conn.execute("INSERT INTO account_profiles(organization_id, requested_by) VALUES (%s,%s) RETURNING id", (oid, REQUESTED_BY)).fetchone()["id"])  # type: ignore[index]
        enqueue(conn, KIND, {"profile_id": rid}, idempotency_key=f"{KIND}:{rid}", correlation_id=REQUESTED_BY, max_attempts=1)
        out["queued"] += 1
    out["queued_today"] += out["queued"]
    return out
