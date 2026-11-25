"""Durable scheduling (spec 13 §5). The scheduler only ENQUEUES; idempotency keys make double-scheduling and restarts harmless."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .db import Conn, q1, qa
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


def schedule_fit(conn: Conn, now: datetime | None = None) -> int:
    """Once an hour, when findings are waiting for a plain-English fit reason, queue one batch job for the worker (which holds the AI key)."""
    from .events.fit import pending
    now = now or datetime.now(timezone.utc)
    if not pending(conn, 1):
        return 0
    _, created = enqueue(conn, "assess_fit", {"batches": 4}, idempotency_key=f"assess-fit:{now.strftime('%Y-%m-%dT%H')}", max_attempts=2, correlation_id="sched-fit")
    return int(created)


def exa_budget_set(conn: Conn) -> bool:
    """Automatic web discovery runs when a monthly Exa budget above zero is saved. The worker, which holds the key, saves it at start-up; the scheduler does not need the key itself."""
    from . import usage
    r = q1(conn, "SELECT cap FROM provider_budgets WHERE provider='exa' AND kind='monthly' AND period=%s", (usage.period_now(),))
    return bool(r and float(r["cap"]) > 0)


def schedule_discovery(conn: Conn, now: datetime | None = None, *, enabled: bool = True) -> int:
    """One discovery run per day when Exa is configured; the run itself enforces the hard budget and per-run limits."""
    from .discovery.plans import sync_account_plans, sync_plans
    from .discovery.run import due_count
    now = now or datetime.now(timezone.utc)
    sync_plans(conn)
    sync_account_plans(conn)                 # the lead monitor: a weekly search per priority account                       # plans are visible in Administration even before Exa is configured
    if not enabled:
        return 0
    if due_count(conn, 7, now) == 0:
        return 0
    _, created = enqueue(conn, "discovery_run", {}, idempotency_key=f"discovery:{now.date().isoformat()}", max_attempts=2, correlation_id="sched-discovery")
    return int(created)


def schedule_watchlist(conn: Conn, now: datetime | None = None, limit: int = 25, every_days: int = 7) -> int:
    """Starred accounts are re-scanned weekly (their own pages only, no paid search). Idempotent per account per ISO week."""
    now = now or datetime.now(timezone.utc)
    rows = qa(conn, "SELECT w.organization_id FROM (SELECT DISTINCT organization_id FROM watchlist) w "
                    "WHERE NOT EXISTS (SELECT 1 FROM account_endpoints e WHERE e.organization_id=w.organization_id AND e.last_scanned_at > %s) "
                    "ORDER BY (SELECT min(last_scanned_at) FROM account_endpoints e WHERE e.organization_id=w.organization_id) NULLS FIRST LIMIT %s",
              (now - timedelta(days=every_days), limit))
    n = 0
    wk = now.strftime("%G-W%V")
    for r in rows:
        _, created = enqueue(conn, "account_scan", {"organization_id": str(r["organization_id"])}, idempotency_key=f"watch-scan:{r['organization_id']}:{wk}", max_attempts=2, correlation_id="sched-watchlist")
        n += bool(created)
    return n



def schedule_finance_pilot(conn: Conn, now: datetime) -> int:
    """Check the finance pilot accounts daily so open invitations stay current."""
    from .discovery.plans import FINANCE_PILOT_LIMIT, finance_pilot_accounts

    day = now.date().isoformat()
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"finance-pilot:{day}",))
    scheduled = {row["organization_id"] for row in qa(conn, """
        SELECT DISTINCT payload->>'organization_id' AS organization_id FROM jobs
        WHERE kind='account_scan' AND correlation_id='finance-pilot'
        AND split_part(idempotency_key, ':', 3)=%s
        """, (day,))}
    available = FINANCE_PILOT_LIMIT - len(scheduled)
    if available <= 0:
        return 0

    queued = 0
    remaining = [account for account in finance_pilot_accounts(conn) if str(account["organization_id"]) not in scheduled]
    for account in remaining[:available]:
        oid = str(account["organization_id"])
        _, created = enqueue(
            conn, "account_scan", {"organization_id": oid},
            idempotency_key=f"finance-pilot:{oid}:{day}", correlation_id="finance-pilot", max_attempts=2,
        )
        queued += int(created)
    return queued


def requeue_stuck_verifications(conn: Conn, now: datetime | None = None, minutes: int = 20) -> int:
    """A request left 'fetching' or 'extracting' after its job died is retried, at most twice; after that it is marked failed with a reason."""
    from datetime import timedelta
    now = now or datetime.now(timezone.utc)
    n = 0
    for r in qa(conn, "SELECT id, coalesce((result->>'requeues')::int, 0) AS rq FROM verification_requests WHERE status IN ('fetching','extracting') AND updated_at < %s", (now - timedelta(minutes=minutes),)):
        if r["rq"] >= 2:
            conn.execute("UPDATE verification_requests SET status='failed', updated_at=now(), result=%s WHERE id=%s", ('{"reason": "stopped responding after two retries"}', r["id"]))
            continue
        conn.execute("UPDATE verification_requests SET status='queued', updated_at=now(), result=jsonb_build_object('requeues', %s) WHERE id=%s", (r["rq"] + 1, r["id"]))
        enqueue(conn, "verify_url", {"request_id": str(r["id"])}, idempotency_key=f"verify:{r['id']}:requeue{r['rq'] + 1}", max_attempts=3)
        n += 1
    return n


def crm_sync_step(conn: Conn, settings, now: datetime | None = None) -> dict:
    """Hourly Pipedrive mirror refresh, only when a token is set. Runs outside the savepoint steps because the sync commits its own watermarks."""
    now = now or datetime.now(timezone.utc)
    if not (settings.pipedrive_api_token and settings.pipedrive_domain) or now.minute >= 5:
        return {}
    from .crm.pipedrive import PipedriveReader, process_inbox, sync_incremental
    try:
        res = sync_incremental(conn, PipedriveReader(settings.pipedrive_domain, settings.pipedrive_api_token))
        return {"crm_sync": res, "crm_inbox": process_inbox(conn)}
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        return {"crm_sync_error": f"{type(exc).__name__}: {exc}"[:200]}



def lemlist_sync_step(conn: Conn, settings, now: datetime | None = None) -> dict:
    """Hourly read-back of Lemlist activity, only when a key is set. Queued as a job so the scheduler tick stays short."""
    now = now or datetime.now(timezone.utc)
    if not settings.lemlist_api_key or now.minute >= 5:
        return {}
    jid, created = enqueue(conn, "lemlist_sync", {"by": "system:scheduler"}, idempotency_key=f"lemlist_sync:{now:%Y%m%d%H}", max_attempts=2)
    return {"lemlist_sync_job": jid} if created else {}


def requeue_billing_failures(conn: Conn, limit: int = 10) -> int:
    """Recover legacy terminal jobs only after the worker has successfully extracted again."""
    rows = qa(conn, """
        SELECT v.id, v.job_id
        FROM verification_requests v JOIN jobs j ON j.id=v.job_id
        WHERE v.status='queued' AND v.result->>'error_class'='provider_billing'
          AND j.status='permanent_failed'
          AND (SELECT max(at) FROM provider_usage WHERE provider='anthropic' AND operation='extract' AND status='ok')
            > (SELECT max(updated_at) FROM verification_requests
               WHERE status='queued' AND result->>'error_class'='provider_billing')
        ORDER BY v.created_at, v.id LIMIT %s FOR UPDATE OF v SKIP LOCKED
        """, (limit,))
    for row in rows:
        jid, _ = enqueue(conn, "verify_url", {"request_id": str(row["id"])},
                         idempotency_key=f"verify:{row['id']}:recover:{row['job_id']}", max_attempts=3)
        conn.execute("UPDATE verification_requests SET job_id=%s, result='{}'::jsonb, updated_at=now() WHERE id=%s",
                     (jid, row["id"]))
    return len(rows)


def run_maintenance(conn: Conn, now: datetime | None = None) -> dict:
    """Idempotent housekeeping. Every step runs in its own savepoint: one failing step is reported and the rest still run, so the scheduler heartbeat never goes quiet."""
    from .events.relink import relink_unmatched
    from .events.verify import tag_untagged
    from .identity.discovery import promote_candidates, reclassify_candidates
    from .ingest.pipeline import evaluate_staleness
    from .initiatives.lifecycle import refresh
    from .opportunity.engine import ensure_hypotheses, evaluate_stale
    from .scoring import recompute_scores
    from .signals.persist import sweep_expiry
    now = now or datetime.now(timezone.utc)
    out: dict = {}

    def step(name: str, fn) -> None:
        try:
            with conn.transaction():
                r = fn()
        except Exception as exc:  # noqa: BLE001
            out[f"{name}_error"] = f"{type(exc).__name__}: {exc}"[:200]
            return
        if isinstance(r, dict):
            out.update({(k if name in ("expiry", "initiatives") else f"{name}_{k}" if k in ("scored", "cleared") else k): v for k, v in r.items()})
        else:
            out[name] = r

    step("finance_pilot_scans", lambda: schedule_finance_pilot(conn, now))
    step("watchlist_scans", lambda: schedule_watchlist(conn, now))
    step("market_tagged", lambda: tag_untagged(conn, 1500))
    from .market import tag_public_bodies
    step("public_bodies_tagged", lambda: tag_public_bodies(conn, 500))
    from .events.triage import retriage
    step("retriaged", lambda: retriage(conn, 500))
    from .events.structured import backfill_procurement_events
    step("procurement_events_backfilled", lambda: backfill_procurement_events(conn, 300))
    from .events.structured import recheck_procurement_relevance
    step("tender_relevance", lambda: recheck_procurement_relevance(conn, 2000))
    step("fit_queued", lambda: schedule_fit(conn, now))
    from .tender_summary import schedule as schedule_tender_summaries
    step("tender_summaries_queued", lambda: schedule_tender_summaries(conn, now))     # every two hours while tenders wait for a summary
    from .renewals import schedule_recent
    from .renewals import recheck_awards
    step("renewals_refreshed", lambda: schedule_recent(conn, now))
    step("renewals_rechecked", lambda: recheck_awards(conn))
    step("candidates", lambda: promote_candidates(conn, 40))
    step("candidate_gate", lambda: reclassify_candidates(conn, 300))
    step("degraded_sources", lambda: evaluate_staleness(conn, now_fn=lambda: now))
    step("expiry", lambda: sweep_expiry(conn, now))
    step("initiatives", lambda: refresh(conn, now))
    step("relinked_events", lambda: relink_unmatched(conn, 150, now)["linked"])
    step("hypotheses_built", lambda: ensure_hypotheses(conn, now))
    step("verifications_requeued", lambda: requeue_stuck_verifications(conn, now))
    step("billing_jobs_recovered", lambda: requeue_billing_failures(conn))
    step("reevaluated", lambda: evaluate_stale(conn, now))
    step("score", lambda: recompute_scores(conn, now))
    return out
