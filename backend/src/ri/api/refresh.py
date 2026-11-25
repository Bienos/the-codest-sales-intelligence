"""How fresh the buying signals are, and "Refresh now".

Sources are checked by themselves. The scheduler (``python -m ri.cli schedule``, a cron job) wakes every few minutes and queues one
``source_run`` job for every switched-on source whose own interval (``sources.poll_interval_s``) has passed since its last attempt
(``ri.scheduler.due_sources``). The worker then reads the source, keeps what is new, turns it into events and matches them to the signal
rules. This module only reads that state for the Buying signals page and, when someone asks, queues the same ``source_run`` jobs early.

GET /api/signals/freshness  (accounts.read)
    When the sources behind buying signals were last checked, when the next automatic check is due, whether a check is running now,
    and whether this person may press "Refresh now" (and if not, why, in a code the page turns into words).

POST /api/signals/refresh  (research.request)
    Queues a ``source_run`` for each source that feeds buying signals and is worth checking now. Limits:

    * once per ``COOLDOWN_MINUTES`` for the whole workspace (there is one queue and one set of sources), counted from the last refresh
      that queued work; a press inside the wait answers 429 with Retry-After;
    * only sources that are switched on (the scheduler's own ``SCHEDULED_STATUSES``), that feed buying signals (``SIGNAL_SOURCE_TYPES``),
      and that are free to read: never a search or internal source, never an Exa or Apollo one;
    * a source checked in the last ``RECENT_MINUTES``, already queued or running, or waiting to retry after an error is left alone;
    * at most ``MAX_SOURCES`` per press, the longest-unchecked first;
    * nothing is queued while the background worker is silent (the jobs would only wait), and the cooldown does not start then.

    Budget: the button adds no new kind of paid work. It queues only ``source_run`` jobs, the same jobs the scheduler queues. The model
    that reads new articles runs inside those jobs under the existing hard monthly cap, where background reading stops at 70% of the cap
    (``BudgetedExtractor.reserve_fraction``) so seller checks keep the rest. Unchanged pages are not read again (documents are kept by
    content), so pulling a run forward reads what the next scheduled run would have read anyway. When the reading budget is used up
    the pages are still fetched and wait, marked for reading, until there is budget again. No Exa search, AI research or Apollo lookup
    is started from here.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from ..access import Principal
from ..db import Conn, q1, qa

COOLDOWN_MINUTES = 15
RECENT_MINUTES = 30
MAX_SOURCES = 60
WORKER_SILENT_MINUTES = 15          # the same threshold the rest of the app uses for "the worker is alive"
SCHEDULER_SILENT_MINUTES = 20       # the same threshold as the self-test (ri.canary)
GIVE_UP_HOURS = 3                   # a refresh whose jobs have not all finished by then is no longer shown as running
DEFAULT_TICK_SECONDS = 300          # render.yaml runs the scheduler every 5 minutes; ri.scheduler.TICK_SECONDS wins when it exists
SIGNAL_SOURCE_TYPES = ("news", "event", "vendor", "jobs", "procurement", "company_site")
PAID_PROVIDERS = ("exa", "apollo")
NEVER_COLLECTION = ("search", "internal")
ACTIVE = ("scheduled", "retryable_failed", "claimed", "fetching", "parsing", "verifying", "committing")
IN_PROGRESS = ("claimed", "fetching", "parsing", "verifying", "committing")
PERMISSION = "research.request"
AUDIT_TYPE, AUDIT_ID, AUDIT_OP = "buying_signals", "refresh", "signals_refresh"


def tick_seconds() -> int:
    """How often the scheduler wakes. Read from ri.scheduler when it says so; otherwise the cron schedule in render.yaml (every 5 minutes)."""
    from .. import scheduler
    for name in ("TICK_SECONDS", "SCHEDULE_TICK_SECONDS"):
        v = getattr(scheduler, name, None)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    fn = getattr(scheduler, "tick_seconds", None)
    if callable(fn):
        try:
            v = fn()
            if isinstance(v, (int, float)) and v > 0:
                return int(v)
        except Exception:  # noqa: BLE001  (a helper that fails must not break the page)
            pass
    return DEFAULT_TICK_SECONDS


def _scheduled_statuses() -> list[str]:
    from .. import scheduler
    return list(getattr(scheduler, "SCHEDULED_STATUSES", ("active", "degraded")))


def signal_sources(conn: Conn) -> list[dict[str, Any]]:
    """The switched-on, free sources that feed buying signals, with when each was last tried and when it is next due."""
    return qa(conn, """
        SELECT s.source_id, s.display_name, s.source_type, s.poll_interval_s, h.state, h.last_attempt_at, h.last_success_at,
               greatest(h.last_attempt_at, h.last_success_at) AS last_checked,
               coalesce(h.last_attempt_at + make_interval(secs => s.poll_interval_s), now()) AS due_at
        FROM sources s LEFT JOIN source_health h USING (source_id)
        WHERE s.status = ANY(%s) AND s.source_type = ANY(%s) AND s.collection_method <> ALL(%s)
          AND lower(s.provider) <> ALL(%s) AND lower(s.source_id) !~ %s
        ORDER BY h.last_attempt_at NULLS FIRST, s.source_id
        """, (_scheduled_statuses(), list(SIGNAL_SOURCE_TYPES), list(NEVER_COLLECTION), list(PAID_PROVIDERS), "(^|[^a-z])(exa|apollo)([^a-z]|$)"))


def _beat(conn: Conn, component: str) -> dict[str, Any] | None:
    return q1(conn, "SELECT at, detail, extract(epoch FROM now()-at)/60 AS minutes FROM system_heartbeats WHERE component=%s", (component,))


def _ai_reading(conn: Conn, worker: dict[str, Any] | None) -> str:
    """Whether new articles are being read by the model: on, paused (the background share of the monthly cap is used), or off (no key)."""
    can = ((worker or {}).get("detail") or {}).get("can") if worker else None
    if isinstance(can, dict) and not can.get("ai"):
        return "off"
    from .. import usage
    row = q1(conn, "SELECT cap, spent, reserved FROM provider_budgets WHERE provider='anthropic' AND kind='monthly' AND period=%s", (usage.period_now(),))
    if not row or float(row["cap"] or 0) <= 0:
        return "off"
    left = float(row["cap"]) - float(row["spent"] or 0) - float(row["reserved"] or 0)
    return "paused" if left < float(row["cap"]) * 0.3 else "on"


def _last_manual(conn: Conn) -> dict[str, Any] | None:
    return q1(conn, "SELECT at, actor, detail FROM audit_events WHERE record_type=%s AND record_id=%s AND operation=%s ORDER BY at DESC, id DESC LIMIT 1",
              (AUDIT_TYPE, AUDIT_ID, AUDIT_OP))


def _manual_progress(conn: Conn, last: dict[str, Any] | None, p: Principal, now: datetime) -> dict[str, Any] | None:
    if not last:
        return None
    corr = (last["detail"] or {}).get("correlation_id")
    counts = {r["status"]: int(r["n"]) for r in qa(conn, "SELECT status, count(*) AS n FROM jobs WHERE correlation_id=%s GROUP BY status", (corr,))} if corr else {}
    total = sum(counts.values())
    waiting = sum(n for s, n in counts.items() if s in ACTIVE)
    failed = sum(n for s, n in counts.items() if s in ("permanent_failed", "quarantined", "requires_approval", "cancelled"))
    recent = last["at"] > now - timedelta(hours=GIVE_UP_HOURS)
    return {"at": last["at"], "by_me": last["actor"] == p.email, "jobs": total, "done": total - waiting, "failed": failed,
            "in_progress": sum(n for s, n in counts.items() if s in IN_PROGRESS), "running": bool(waiting) and recent}


def freshness(conn: Conn, p: Principal) -> dict[str, Any]:
    now = q1(conn, "SELECT now() AS t")["t"]  # type: ignore[index]
    tick = tick_seconds()
    sources = signal_sources(conn)
    ids = [s["source_id"] for s in sources]
    jobs = q1(conn, """
        SELECT count(*) FILTER (WHERE status = ANY(%s)) AS in_progress,
               count(*) FILTER (WHERE status IN ('scheduled','retryable_failed') AND run_after <= now()) AS queued
        FROM jobs WHERE kind='source_run' AND status = ANY(%s) AND payload->>'source_id' = ANY(%s)""",
              (list(IN_PROGRESS), list(ACTIVE), ids)) or {"in_progress": 0, "queued": 0}
    worker, sched = _beat(conn, "worker"), _beat(conn, "scheduler")
    worker_ok = bool(worker) and float(worker["minutes"]) < WORKER_SILENT_MINUTES
    automatic = bool(sched) and float(sched["minutes"]) < SCHEDULER_SILENT_MINUTES

    last_checked = max((s["last_checked"] for s in sources if s["last_checked"]), default=None)
    next_check = None
    if automatic and sources:
        next_tick = sched["at"] + timedelta(seconds=tick)   # type: ignore[index]
        while next_tick <= now:
            next_tick += timedelta(seconds=tick)
        earliest = min(s["due_at"] for s in sources)
        if earliest <= next_tick:
            next_check = next_tick
        else:      # the first tick at or after the earliest source falls due
            next_check = next_tick + timedelta(seconds=tick * math.ceil((earliest - next_tick).total_seconds() / tick))
    due_next = sum(1 for s in sources if next_check and s["due_at"] <= next_check)

    last = _last_manual(conn)
    manual = _manual_progress(conn, last, p, now)
    cooldown_until = last["at"] + timedelta(minutes=COOLDOWN_MINUTES) if last and last["at"] + timedelta(minutes=COOLDOWN_MINUTES) > now else None
    running = bool(jobs["in_progress"] or jobs["queued"] or (manual and manual["running"]))
    intervals = [int(s["poll_interval_s"]) for s in sources]

    if not p.can(PERMISSION):
        blocked = "permission"
    elif not sources:
        blocked = "no_sources"
    elif not worker_ok:
        blocked = "worker"
    elif manual and manual["running"]:
        blocked = "running"
    elif cooldown_until:
        blocked = "cooldown"
    else:
        blocked = None
    return {
        "now": now, "tick_minutes": max(1, round(tick / 60)),
        "sources": {"watched": len(sources), "checked_24h": sum(1 for s in sources if s["last_checked"] and s["last_checked"] > now - timedelta(hours=24)),
                    "failing": sum(1 for s in sources if s["state"] in ("degraded", "failed", "blocked")),
                    "every_hours": [round(min(intervals) / 3600), round(max(intervals) / 3600)] if intervals else None},
        "last_checked_at": last_checked, "next_check_at": next_check, "due_next": due_next,
        "automatic": automatic, "worker": worker_ok, "ai_reading": _ai_reading(conn, worker),
        "running": running, "in_progress": int(jobs["in_progress"]), "queued": int(jobs["queued"]),
        "manual": manual, "cooldown_minutes": COOLDOWN_MINUTES, "cooldown_until": cooldown_until, "recent_minutes": RECENT_MINUTES,
        "can_refresh": blocked is None, "blocked": blocked,
        "poll_seconds": 5 if manual and manual["running"] else 20 if running else 60,
    }


class RefreshRefused(Exception):
    def __init__(self, status: int, code: str, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.retry_after = status, code, message, retry_after


def refresh(conn: Conn, p: Principal) -> dict[str, Any]:
    """Queue early checks of the sources behind buying signals. Serialised per workspace so two presses never both pass the cooldown."""
    from ..audit import audit
    from ..jobs.queue import enqueue

    conn.execute("SELECT pg_advisory_xact_lock(hashtext('signals-refresh'))")
    state = freshness(conn, p)
    if state["blocked"] == "no_sources":
        raise RefreshRefused(409, "no_sources", "No sources are switched on yet, so there is nothing to check.")
    if state["blocked"] == "worker":
        raise RefreshRefused(409, "worker", "The background worker is not running, so a check would only wait in the queue. Try again later.")
    if state["blocked"] == "running":
        raise RefreshRefused(409, "running", "A check is already running.")
    if state["blocked"] == "cooldown":
        wait = max(1, math.ceil((state["cooldown_until"] - state["now"]).total_seconds()))
        raise RefreshRefused(429, "cooldown", f"Sources were checked by hand less than {COOLDOWN_MINUTES} minutes ago. Try again in {math.ceil(wait / 60)} min.", wait)

    now = state["now"]
    busy = {r["sid"] for r in qa(conn, "SELECT DISTINCT payload->>'source_id' AS sid FROM jobs WHERE kind='source_run' AND status = ANY(%s)", (list(ACTIVE),))}
    picked, skipped = [], {"recent": 0, "queued": 0, "limit": 0}
    for s in signal_sources(conn):
        if s["source_id"] in busy:
            skipped["queued"] += 1                     # already queued, running, or backing off after an error: never jumped
        elif s["last_checked"] and s["last_checked"] > now - timedelta(minutes=RECENT_MINUTES):
            skipped["recent"] += 1
        elif len(picked) >= MAX_SOURCES:
            skipped["limit"] += 1
        else:
            picked.append(s["source_id"])
    if not picked:
        return {"queued": 0, "skipped": skipped, "freshness": state}

    stamp = now.strftime("%Y%m%dT%H%M%S")
    corr = f"signals-refresh:{stamp}"
    queued = 0
    for sid in picked:
        _, created = enqueue(conn, "source_run", {"source_id": sid}, idempotency_key=f"source_run:{sid}:refresh:{stamp}", max_attempts=2, correlation_id=corr)
        queued += int(created)
    audit(conn, actor=p.email, operation=AUDIT_OP, record_type=AUDIT_TYPE, record_id=AUDIT_ID,
          detail={"correlation_id": corr, "queued": queued, "sources": picked, "skipped": skipped})
    return {"queued": queued, "skipped": skipped, "freshness": freshness(conn, p)}


def register_refresh(app: FastAPI) -> None:
    from .auth import require

    @app.get("/api/signals/freshness")
    def signals_freshness(request: Request, p: Principal = Depends(require("accounts.read"))):
        with request.app.state.db.tx() as conn:
            return freshness(conn, p)

    @app.post("/api/signals/refresh")
    def signals_refresh(request: Request, p: Principal = Depends(require(PERMISSION))):
        try:
            with request.app.state.db.tx() as conn:
                return refresh(conn, p)
        except RefreshRefused as exc:
            headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
            return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=exc.status, headers=headers)

