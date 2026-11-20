"""Durable scheduling (spec 13 §5). The scheduler only ENQUEUES; idempotency keys make double-scheduling and restarts harmless."""
from __future__ import annotations

from datetime import datetime, timezone

from .db import Conn, qa
from .jobs.queue import enqueue

SCHEDULED_STATUSES = ("active", "degraded")      # pending/blocked/failed/paused sources are never polled


def due_sources(conn: Conn, now: datetime) -> list[dict]:
    return qa(conn, "SELECT s.source_id, s.poll_interval_s, h.last_attempt_at FROM sources s LEFT JOIN source_health h USING (source_id) "
                    "WHERE s.status = ANY(%s) AND s.collection_method <> 'internal' AND (h.last_attempt_at IS NULL OR h.last_attempt_at + make_interval(secs => s.poll_interval_s) <= %s)",
              (list(SCHEDULED_STATUSES), now))


def schedule_due(conn: Conn, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    n = 0
    for s in due_sources(conn, now):
        bucket = int(now.timestamp() // max(s["poll_interval_s"], 60))
        _, created = enqueue(conn, "source_run", {"source_id": s["source_id"]}, idempotency_key=f"source_run:{s['source_id']}:{bucket}", correlation_id=f"sched-{bucket}")
        n += created
    return n


def run_maintenance(conn: Conn, now: datetime | None = None) -> dict:
    """Idempotent housekeeping: staleness => degraded coverage, expiry sweeps, initiative refresh, hypothesis re-evaluation."""
    from .ingest.pipeline import evaluate_staleness
    from .initiatives.lifecycle import refresh
    from .opportunity.engine import evaluate_stale
    from .signals.persist import sweep_expiry
    now = now or datetime.now(timezone.utc)
    return {"degraded_sources": evaluate_staleness(conn, now_fn=lambda: now), **sweep_expiry(conn, now), **refresh(conn, now), "reevaluated": evaluate_stale(conn, now)}
