"""Rotating check of EVERY target account's own pages (newsroom, careers, supplier page, job boards), longest-unchecked first.

Why: the daily check reads the same 25 priority accounts and the weekly one the starred accounts, so on production only 54 of about 920
target accounts had ever had their own pages read. This lane gives every target account its turn. It queues a few accounts an hour (about
``per_day`` a day), so the whole target list is covered in about 920 / 20 = 46 days and then starts over with whoever was checked longest ago.
It runs next to the daily and weekly checks and never competes with them: whatever those read counts as "checked", so the rotation moves on.

Which accounts, in this order (``candidates``)
  1. a target account (``target_market.target_sql``, the one definition of a target) with a website (``canonical_domain``), not stopped
     (the same exclusion the daily check uses: an account with an active "stop" finding is not pursued);
  2. never checked first, then the oldest check. "Checked" is the later of the account's ``account_source_coverage`` record and the last time
     one of its pages in ``account_endpoints`` was read, so a scan from any source (daily, weekly, a seller's button) counts;
  3. among accounts never checked: the lead list's P1 accounts first, then P2, then P3 and the rest; within each, on the master list first,
     then by account score, then by id (stable).
  An account checked less than ``rescan_days`` ago (never fewer than 14) is left alone.

What it skips
  * an account with a scan job already waiting or running (any scan kind, so a seller's button press or the daily check is never doubled);
  * an account whose scan job failed or was refused in the last ``FAILED_PAUSE_DAYS`` (3) days: it is tried again after that, never in a loop;
  * an account this lane already queued today, whatever became of that job (the idempotency key ``scan-rotation:<account>:<day>`` also makes
    the same scheduler tick, run twice, queue nothing new).

Limits (``Limits``, settings ``RI_SCAN_ROTATION_*`` in ``config.Settings``)
  * ``per_day`` 20 accounts a day, ``per_run`` 2 accounts per hourly run (so the day's 20 are queued over the first ten hours, not in a burst),
  * ``max_queued`` 6 jobs of this lane waiting or running at once: the background worker also reads every source, so it is never flooded.
  * Coverage sprint: while any P1 or P2 account with a website has never been checked, the lane runs faster (at least ``SPRINT_PER_DAY``
    a day, ``SPRINT_PER_RUN`` an hour, ``SPRINT_MAX_QUEUED`` waiting at once) and says how many are left (``sprint_left``). Once they are all
    checked it falls back to the normal pace by itself. A ``per_day`` of 0 (paused) is never raised. The AI limit stop below still applies.

Money and the AI
  * Reading pages costs no Exa. Discovery of an account's pages and every read use public pages and public job-board APIs only, through the
    safe client with robots.txt honoured; nothing here calls Exa or Apollo.
  * Changed pages are read by the AI through the existing ``BudgetedExtractor`` inside the ``account_scan`` job handler, with the same reserve
    the daily check uses (background reading stops at 70% of the monthly AI limit, ``AI_RESERVE_FRACTION``). The lane also stops queueing new
    accounts at that point, so pages are not collected only to wait unread (they would be read later, oldest first, but a month of pile-up
    helps nobody). With no AI limit saved at all, accounts are still checked (their pages are found and kept for later reading).

The jobs run on the BACKGROUND worker lane: ``KIND`` is a different job kind from the seller-facing ``account_scan`` (which the worker keeps
for the people pressing buttons) and the worker runs every kind not on its interactive list in the background lane. The handler is the same
function as ``account_scan`` (``jobs.handlers.build_handlers`` maps ``KIND`` to it).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import Conn, q1, qa
from ..jobs.queue import enqueue
from ..target_market import target_sql

KIND = "account_scan_background"      # job kind: the account scan, on the background worker lane
INTERACTIVE_KIND = "account_scan"     # job kind a seller's button press and the daily / weekly checks use
SCAN_KINDS = (INTERACTIVE_KIND, KIND)
CORRELATION = "scan-rotation"
KEY_PREFIX = "scan-rotation"          # idempotency key: scan-rotation:<organization id>:<UTC day>

DEFAULT_PER_DAY = 20                  # accounts queued in a UTC day: 20 a day covers about 920 targets in 46 days
DEFAULT_PER_RUN = 2                   # accounts per hourly run
DEFAULT_MAX_QUEUED = 6                # jobs of this lane waiting or running at once
DEFAULT_RESCAN_DAYS = 14              # a checked account is left alone this long
SPRINT_PER_DAY = 60                   # while P1/P2 accounts wait for their first check: about 800 of them in two weeks
SPRINT_PER_RUN = 5
SPRINT_MAX_QUEUED = 10
SPRINT_PRIORITIES = ("P1", "P2")
MIN_RESCAN_DAYS = 14                  # a setting can lengthen the interval, never shorten it
FAILED_PAUSE_DAYS = 3                 # an account whose scan failed is not queued again for this long
AI_RESERVE_FRACTION = 0.3             # the same reserve as jobs.handlers.account_scan sets on its extractor: stop at 70% of the monthly AI limit

IN_FLIGHT = ("scheduled", "claimed", "fetching", "parsing", "verifying", "committing", "retryable_failed", "requires_approval")
FAILED = ("permanent_failed", "quarantined")


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        return min(max(int(os.environ.get(name, default)), low), high)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Limits:
    per_day: int = DEFAULT_PER_DAY
    per_run: int = DEFAULT_PER_RUN
    max_queued: int = DEFAULT_MAX_QUEUED
    rescan_days: int = DEFAULT_RESCAN_DAYS

    @classmethod
    def from_env(cls) -> Limits:
        """``RI_SCAN_ROTATION_PER_DAY`` (0-200, 0 queues nothing), ``..._PER_RUN`` (1-20), ``..._MAX_QUEUED`` (1-50), ``..._RESCAN_DAYS`` (14 or more)."""
        return cls(per_day=_int_env("RI_SCAN_ROTATION_PER_DAY", DEFAULT_PER_DAY, 0, 200),
                   per_run=_int_env("RI_SCAN_ROTATION_PER_RUN", DEFAULT_PER_RUN, 1, 20),
                   max_queued=_int_env("RI_SCAN_ROTATION_MAX_QUEUED", DEFAULT_MAX_QUEUED, 1, 50),
                   rescan_days=_int_env("RI_SCAN_ROTATION_RESCAN_DAYS", DEFAULT_RESCAN_DAYS, MIN_RESCAN_DAYS, 365))


def targets(conn: Conn) -> list[dict[str, Any]]:
    """Every target account, oldest-checked first (never checked first), with the time of its last check. One query; about 900 rows."""
    return qa(conn, f"""
        SELECT o.id AS organization_id, o.display_name, nullif(btrim(o.canonical_domain), '') AS website, o.list_priority,
               coalesce(sc.stopped, false) AS stopped, GREATEST(c.last_at, e.last_at) AS last_checked_at
        FROM organizations o
        LEFT JOIN account_scores sc ON sc.organization_id = o.id
        LEFT JOIN (SELECT organization_id, max(updated_at) AS last_at FROM account_source_coverage GROUP BY 1) c ON c.organization_id = o.id
        LEFT JOIN (SELECT organization_id, max(last_scanned_at) AS last_at FROM account_endpoints GROUP BY 1) e ON e.organization_id = o.id
        WHERE {target_sql('o')}
        ORDER BY GREATEST(c.last_at, e.last_at) ASC NULLS FIRST,
                 CASE o.list_priority WHEN 'P1' THEN 0 WHEN 'P2' THEN 1 WHEN 'P3' THEN 2 ELSE 3 END, o.in_master_list DESC, sc.score DESC NULLS LAST, o.id
        """)   # noqa: S608 - target_sql builds fixed SQL


def scan_jobs(conn: Conn, now: datetime, day: str, failed_pause_days: int = FAILED_PAUSE_DAYS) -> tuple[set[str], set[str]]:
    """(accounts to leave alone because a scan job is waiting or running, or this lane already queued them today, whatever became of that job;
    accounts whose scan job failed or was refused in the last few days). Any scan kind counts."""
    busy: set[str] = set()
    failed: set[str] = set()
    for r in qa(conn, "SELECT payload->>'organization_id' AS oid, status, (kind=%s AND split_part(idempotency_key, ':', 3)=%s) AS today FROM jobs "
                      "WHERE kind = ANY(%s) AND (status = ANY(%s) OR (status = ANY(%s) AND updated_at > %s) OR (kind=%s AND split_part(idempotency_key, ':', 3)=%s))",
                (KIND, day, list(SCAN_KINDS), list(IN_FLIGHT), list(FAILED), now - timedelta(days=failed_pause_days), KIND, day)):
        if not r["oid"]:
            continue
        if r["today"] or r["status"] in IN_FLIGHT:
            busy.add(str(r["oid"]))
        else:
            failed.add(str(r["oid"]))
    return busy, failed


def queued_today(conn: Conn, day: str) -> int:
    return int((q1(conn, "SELECT count(*) AS n FROM jobs WHERE kind=%s AND split_part(idempotency_key, ':', 3)=%s", (KIND, day)) or {"n": 0})["n"])


def in_flight(conn: Conn) -> int:
    return int((q1(conn, "SELECT count(*) AS n FROM jobs WHERE kind=%s AND status = ANY(%s)", (KIND, list(IN_FLIGHT))) or {"n": 0})["n"])


def ai_share_left(conn: Conn, now: datetime) -> float | None:
    """Share of this month's AI limit not yet used or reserved; None when no limit is saved (the AI is not set up on the worker)."""
    r = q1(conn, "SELECT cap, spent, reserved FROM provider_budgets WHERE provider='anthropic' AND kind='monthly' AND period=%s", (now.strftime("%Y-%m"),))
    if not r or float(r["cap"]) <= 0:
        return None
    return (float(r["cap"]) - float(r["spent"]) - float(r["reserved"])) / float(r["cap"])


def pick(rows: list[dict[str, Any]], now: datetime, limit: int, *, rescan_days: int, busy: set[str], failed: set[str]) -> list[dict[str, Any]]:
    """The next accounts to check: rows are already in rotation order, so take the first ones that are due and not skipped."""
    cutoff = now - timedelta(days=max(rescan_days, MIN_RESCAN_DAYS))
    out: list[dict[str, Any]] = []
    for r in rows:
        if len(out) >= limit:
            break
        last = r["last_checked_at"]
        if last is not None and last > cutoff:
            continue            # checked recently (by this lane, the daily or weekly check, or a seller): not due
        oid = str(r["organization_id"])
        if oid in busy or oid in failed:
            continue
        out.append(r)
    return out


def schedule(conn: Conn, now: datetime | None = None, limits: Limits | None = None) -> dict[str, Any]:
    """The lane step: queue the next few accounts and say how far the rotation has got. Numbers only (the page prints each as "<n> <name>"),
    plus ``held_by`` when a limit stopped it: ``ai_budget``, ``daily_limit`` or ``queue_busy``."""
    now = now or datetime.now(timezone.utc)
    limits = limits or Limits.from_env()
    day = now.astimezone(timezone.utc).date().isoformat()
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{KEY_PREFIX}:{day}",))       # two ticks at once cannot both queue the same day's work

    rows = targets(conn)
    scannable = [r for r in rows if r["website"] and not r["stopped"]]
    out: dict[str, Any] = {"queued": 0, "queued_today": queued_today(conn, day),
                           "targets_checked": sum(1 for r in scannable if r["last_checked_at"] is not None), "targets_total": len(scannable),
                           "targets_without_website": sum(1 for r in rows if not r["website"] and not r["stopped"])}

    out["sprint_left"] = sum(1 for r in scannable if r["last_checked_at"] is None and r.get("list_priority") in SPRINT_PRIORITIES)
    if out["sprint_left"] and limits.per_day > 0:
        limits = Limits(per_day=max(limits.per_day, SPRINT_PER_DAY), per_run=max(limits.per_run, SPRINT_PER_RUN),
                        max_queued=max(limits.max_queued, SPRINT_MAX_QUEUED), rescan_days=limits.rescan_days)
    share = ai_share_left(conn, now)
    if share is not None and share < AI_RESERVE_FRACTION:
        out["held_by"] = "ai_budget"            # the last 30% of the month's AI limit is kept for the sellers' own checks
        return out
    room = min(limits.per_run, limits.per_day - out["queued_today"], limits.max_queued - in_flight(conn))
    if room <= 0:
        out["held_by"] = "daily_limit" if limits.per_day - out["queued_today"] <= 0 else "queue_busy"
        return out

    busy, failed = scan_jobs(conn, now, day)
    for r in pick(scannable, now, room, rescan_days=limits.rescan_days, busy=busy, failed=failed):
        oid = str(r["organization_id"])
        _, created = enqueue(conn, KIND, {"organization_id": oid, "rediscover": r["last_checked_at"] is not None, "rotation": True},
                             idempotency_key=f"{KEY_PREFIX}:{oid}:{day}", correlation_id=CORRELATION, max_attempts=2)
        out["queued"] += int(created)
    out["queued_today"] += out["queued"]
    return out
