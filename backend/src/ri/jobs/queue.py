"""Durable Postgres-backed job queue (spec 13 §4/§6/§7). No extra infrastructure: FOR UPDATE SKIP LOCKED.

Lifecycle: scheduled -> claimed -> (fetching|parsing|verifying|committing) -> completed
Failure:   retryable_failed (backoff) -> permanent_failed (+dead letter) | requires_approval | quarantined | cancelled
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from ..db import Conn, canonical_json, jb, q1

LEASE_SECONDS = 300
RETRYABLE = {"rate_limited", "transient_network", "server_error", "timeout"}
PERMANENT = {"auth", "access_denied", "policy_block", "schema_drift", "not_found", "invalid_payload", "budget_exceeded"}


class JobError(Exception):
    def __init__(self, msg: str, error_class: str = "transient_network", retry_after: float | None = None):
        super().__init__(msg)
        self.error_class = error_class
        self.retry_after = retry_after


class RequiresApproval(Exception):
    pass


def now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(conn: Conn, kind: str, payload: dict[str, Any], *, idempotency_key: str | None = None, run_after: datetime | None = None,
            max_attempts: int = 5, correlation_id: str | None = None, deadline_at: datetime | None = None) -> tuple[str, bool]:
    """Returns (job_id, created). Same idempotency key => same job (replay-safe)."""
    phash = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    key = idempotency_key or f"{kind}:{phash}"
    row = conn.execute(
        "INSERT INTO jobs(kind, payload, payload_hash, idempotency_key, run_after, max_attempts, correlation_id, deadline_at) "
        "VALUES (%s,%s,%s,%s,coalesce(%s, clock_timestamp()),%s,%s,%s) ON CONFLICT (idempotency_key) DO NOTHING RETURNING id",
        (kind, jb(payload), phash, key, run_after, max_attempts, correlation_id, deadline_at)).fetchone()
    if row:
        return str(row["id"]), True
    existing = q1(conn, "SELECT id FROM jobs WHERE idempotency_key=%s", (key,))
    return str(existing["id"]), False  # type: ignore[index]


def claim(conn: Conn, worker: str, kinds: list[str] | None = None) -> dict[str, Any] | None:
    """Claim one due job; also reclaims jobs whose lease expired (crashed worker)."""
    row = conn.execute(
        "UPDATE jobs SET status='claimed', claimed_by=%s, claimed_at=now(), attempts=attempts+1, updated_at=now(), "
        "lease_expires_at=now() + make_interval(secs => %s) WHERE id = ("
        "  SELECT id FROM jobs WHERE (kind = ANY(%s) OR %s::text[] IS NULL) AND ("
        "    (status IN ('scheduled','retryable_failed') AND run_after <= clock_timestamp()) OR "
        "    (status IN ('claimed','fetching','parsing','verifying','committing') AND lease_expires_at < clock_timestamp())) "
        "  ORDER BY run_after, created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *",
        (worker, LEASE_SECONDS, kinds, kinds)).fetchone()
    return row


def set_stage(conn: Conn, job_id: str, stage: str) -> None:
    conn.execute("UPDATE jobs SET status=%s, updated_at=now(), lease_expires_at=now() + make_interval(secs => %s) WHERE id=%s",
                 (stage, LEASE_SECONDS, job_id))


def complete(conn: Conn, job_id: str, result: dict[str, Any] | None = None) -> None:
    conn.execute("UPDATE jobs SET status='completed', result=%s, updated_at=now(), lease_expires_at=NULL WHERE id=%s",
                 (jb(result or {}), job_id))


def fail(conn: Conn, job: dict[str, Any], exc: Exception) -> str:
    """Classify, back off (honouring Retry-After), or dead-letter. Returns resulting status."""
    ec = getattr(exc, "error_class", "transient_network")
    msg = f"{type(exc).__name__}: {exc}"[:2000]
    if isinstance(exc, RequiresApproval):
        status, delay = "requires_approval", None
    elif ec == "provider_billing":
        status, delay = "retryable_failed", 3600          # out of provider credit: never dead-letter the work, try again hourly until someone tops up
    elif ec in PERMANENT or job["attempts"] >= job["max_attempts"]:
        status, delay = "permanent_failed", None
    else:
        base = getattr(exc, "retry_after", None)
        delay = base if base else min(2 ** job["attempts"] * 30, 3600)
        status = "retryable_failed"
    conn.execute(
        "UPDATE jobs SET status=%s, last_error=%s, last_error_class=%s, updated_at=now(), lease_expires_at=NULL, "
        "run_after = CASE WHEN %s::float8 IS NULL THEN run_after ELSE clock_timestamp() + make_interval(secs => %s::float8) END WHERE id=%s",
        (status, msg, ec, delay, delay, job["id"]))
    if status == "permanent_failed":
        conn.execute("INSERT INTO dead_letters(job_id, kind, payload, error_class, error) VALUES (%s,%s,%s,%s,%s)",
                     (job["id"], job["kind"], jb(job["payload"]), ec, msg))
    return status


def quarantine(conn: Conn, job: dict[str, Any], reason: str) -> None:
    conn.execute("UPDATE jobs SET status='quarantined', last_error=%s, last_error_class='invalid_payload', updated_at=now() WHERE id=%s",
                 (reason, job["id"]))
    conn.execute("INSERT INTO dead_letters(job_id, kind, payload, error_class, error) VALUES (%s,%s,%s,'quarantined',%s)",
                 (job["id"], job["kind"], jb(job["payload"]), reason))


def replay_dead_letter(conn: Conn, dead_letter_id: str, actor: str) -> str:
    """Recovery procedure (RB runbook): re-enqueue under a fresh idempotency key, mark letter replayed."""
    dl = q1(conn, "SELECT * FROM dead_letters WHERE id=%s AND replayed_at IS NULL", (dead_letter_id,))
    if not dl:
        raise ValueError("dead letter not found or already replayed")
    job_id, _ = enqueue(conn, dl["kind"], dl["payload"], idempotency_key=f"replay:{dead_letter_id}")
    conn.execute("UPDATE dead_letters SET replayed_at=now() WHERE id=%s", (dead_letter_id,))
    from ..audit import audit
    audit(conn, actor=actor, operation="dlq_replay", record_type="dead_letter", record_id=dead_letter_id, detail={"job_id": job_id})
    return job_id


def run_one(conn: Conn, worker: str, handlers: dict[str, Any], kinds: list[str] | None = None) -> str | None:
    """Claim and execute one job. Each job runs in its own transaction; failures never poison the queue."""
    job = claim(conn, worker, kinds)
    conn.commit()
    if not job:
        return None
    handler = handlers.get(job["kind"])
    if handler is None:
        quarantine(conn, job, f"no handler for kind {job['kind']}")
        conn.commit()
        return "quarantined"
    try:
        if getattr(handler, "own_transactions", False):     # long importers commit per unit of work (resumable); no wrapping transaction
            result = handler(conn, job)
            complete(conn, str(job["id"]), result if isinstance(result, dict) else {})
        else:
            with conn.transaction():
                result = handler(conn, job)
                complete(conn, str(job["id"]), result if isinstance(result, dict) else {})
        conn.commit()
        return "completed"
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        status = fail(conn, job, exc)
        conn.commit()
        return status
