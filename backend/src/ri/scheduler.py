"""Durable scheduling (spec 13 §5). The scheduler only ENQUEUES; idempotency keys make double-scheduling and restarts harmless.

The cron (render.yaml) wakes the scheduler every TICK_SECONDS. Each housekeeping step is a *lane* (LANES below) with its own cadence:
most run on every tick, the ones that only matter hourly or after a rule change run hourly. A lane records its last run and result in
``scheduler_lanes`` (migration 0048), where Administration shows it and where a person can pause it, change how often it runs, or ask
for it to run at the next tick. A lane with no row runs on its built-in cadence, so an empty table behaves like the old scheduler."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .db import Conn, jb, q1, qa
from .jobs.queue import enqueue

SCHEDULED_STATUSES = ("active", "degraded")      # pending/blocked/failed/paused sources are never polled
TICK_SECONDS = 300                               # render.yaml: schedule "*/5 * * * *"
SLACK_SECONDS = 60                               # a tick that starts a little early still counts as due


@dataclass(frozen=True)
class Lane:
    key: str                 # the step name (also the key of its figure in the scheduler's output)
    label: str               # plain name for Administration
    what: str                # one line: what it does
    every_s: int = TICK_SECONDS
    can_pause: bool = False  # only lanes whose pause cannot leave wrong data behind
    adjustable: bool = False # a person may change how often it runs (between TICK_SECONDS and a day)
    main: bool = False       # shown on its own in Administration; the others are grouped as housekeeping
    queues: str | None = None    # the background job kind it starts, when it starts one


HOUR = 3600
LANES: tuple[Lane, ...] = (
    Lane("finance_pilot_scans", "Daily check of the top accounts' own pages", "Once a day, queues a read of the news, careers and supplier pages of the 25 priority accounts.",
         HOUR, can_pause=True, main=True, queues="account_scan"),
    Lane("watchlist_scans", "Weekly check of starred accounts", "Once a week, queues a read of the own pages of every starred account.", HOUR, can_pause=True, main=True, queues="account_scan"),
    Lane("scan_rotation", "Rotating check of every target account's own pages",
         "Every hour, queues a read of the news, careers and supplier pages of a few target accounts, the longest-unchecked first (about 20 a day), so every target gets its turn. Runs in the background.",
         HOUR, can_pause=True, main=True, queues="account_scan_background"),
    Lane("connection_sync", "Personal connections: sync", "Every hour, queues a sync of each person's own connected Gmail and Lemlist, so conversations with our contacts appear on the person and company pages. Runs in the background.",
         HOUR, can_pause=True, main=True, queues="gmail_sync"),
    Lane("market_tagged", "Market and region tags", "Gives each new finding its market, region and public or private lane."),
    Lane("public_bodies_tagged", "Public bodies", "Marks public-sector buyers so they are not offered as sales accounts."),
    Lane("retriaged", "Re-check skipped articles", "Articles the cheap word check skipped are checked again when the word list changes.", HOUR, adjustable=True),
    Lane("procurement_events_backfilled", "Repair tenders left without a finding", "Makes the finding for any tender a stopped run saved without one. Also runs straight after a tender source fails.",
         HOUR, adjustable=True),
    Lane("tender_relevance", "Re-judge tender findings", "Applies the current software-work test to tender findings judged under an older one.", HOUR, adjustable=True),
    Lane("tenders_expired", "Close tenders past their deadline", "Marks open tenders whose deadline passed more than 12 hours ago as expired, so counts stay honest.", HOUR),
    Lane("fit_queued", "AI fit check for new findings", "At most once an hour, queues the AI check that writes why a new finding fits us, with an English and Polish summary.",
         900, can_pause=True, main=True, queues="assess_fit"),
    Lane("tender_summaries_queued", "Tender summaries", "At most once every two hours, queues the AI summary of open tenders in our sectors that have none.", can_pause=True, main=True, queues="tender_summaries"),
    Lane("renewals_refreshed", "Award notices (renewals)", "Once a day, queues a re-read of the last five weeks of TED award notices.", HOUR, can_pause=True, main=True, queues="ted_awards_window"),
    Lane("renewals_rechecked", "Re-judge stored awards", "Applies the current relevance rule to awards stored under an older one. Does nothing when all are current."),
    Lane("identity_auto_queued", "Automatic company matching", "At most once an hour, queues the matcher while company names from the workbooks wait for a decision.", 900, can_pause=True, main=True,
         queues="identity_autoresolve"),
    Lane("source_check_queued", "Daily test of every source", "Once a day, queues the server test that switches safe public sources on by themselves.", HOUR, can_pause=True, main=True,
         queues="sources_validate_daily"),
    Lane("candidates", "New companies from findings", "Turns qualified new company names found in the news into candidate accounts.", can_pause=True),
    Lane("candidate_gate", "Candidate check", "Re-checks candidate companies against the target market rules."),
    Lane("deadlines_pinned", "Regulatory deadlines on accounts",
         "Once a day, pins each upcoming EU deadline from the regulatory calendar to the target accounts it applies to (country and kind of firm), so sellers see what each must build and by when.",
         86400, can_pause=True, main=True),
    Lane("hard_to_fill_roles", "Hard-to-fill roles",
         "Once a day, finds engineering ads at our accounts still open after 60 days, or posted again after an earlier ad for the same role closed, and turns each into a hiring signal.",
         86400, can_pause=True, main=True),
    Lane("degraded_sources", "Late sources", "Marks a source that missed its scheduled reads as late."),
    Lane("expiry", "Signal expiry", "Ends buying signals whose time window has passed."),
    Lane("initiatives", "Programme deadlines", "Moves programmes on when their next milestone date has passed."),
    Lane("relinked_events", "Match findings to new accounts", "Re-matches findings about unknown companies when new accounts appear.", adjustable=True),
    Lane("hypotheses_built", "Opportunity cards", "Builds the opportunity card for any programme that has none."),
    Lane("verifications_requeued", "Stuck page checks", "Retries a page check whose job stopped answering (at most twice)."),
    Lane("billing_jobs_recovered", "Work stopped by AI credit", "Re-queues page checks that stopped when the AI account ran out of credit, once it works again."),
    Lane("reevaluated", "Opportunity re-check", "Re-scores opportunity cards whose programme changed."),
    Lane("score", "Account scores", "Recomputes the account scores used for sorting."),
    Lane("discovery_plans", "Saved web searches", "Keeps the saved web searches in line with the rules and the priority account list.", HOUR, adjustable=True),
)
LANE = {lane.key: lane for lane in LANES}


def tick_seconds() -> int:
    return TICK_SECONDS


def lane_rows(conn: Conn) -> dict[str, dict[str, Any]]:
    """The stored state of every lane. A database without the table (migration 0048 not applied yet) gives no rows: built-in cadences."""
    try:
        with conn.transaction():
            return {r["lane"]: r for r in qa(conn, "SELECT * FROM scheduler_lanes")}
    except Exception:  # noqa: BLE001
        return {}


def lane_every(lane: Lane, row: dict[str, Any] | None) -> int:
    v = (row or {}).get("every_s")
    return int(v) if v and lane.adjustable else lane.every_s


def lane_due(lane: Lane, row: dict[str, Any] | None, now: datetime) -> bool:
    if row and not row["enabled"] and lane.can_pause:
        return False
    if row and row.get("run_requested_by"):
        return True
    last = (row or {}).get("last_run_at")
    return last is None or (now - last).total_seconds() >= lane_every(lane, row) - SLACK_SECONDS


def lane_next(lane: Lane, row: dict[str, Any] | None, now: datetime) -> datetime | None:
    """When the lane runs next (None: paused). Due lanes run at the next tick."""
    if row and not row["enabled"] and lane.can_pause:
        return None
    last = (row or {}).get("last_run_at")
    if last is None or (row or {}).get("run_requested_by"):
        return now
    return max(now, last + timedelta(seconds=lane_every(lane, row)))


def _figures(r: Any) -> dict[str, Any]:
    if isinstance(r, dict):
        return {k: v for k, v in r.items() if isinstance(v, (int, float, str, bool)) or v is None}
    if isinstance(r, list):
        return {"n": len(r)}
    if isinstance(r, (int, float, bool)):
        return {"n": r}
    return {}


PREFIXED_LANES = ("scan_rotation",)         # lanes whose result figures are named for the lane in the scheduler's output ("scan_rotation_queued"), so they cannot clash with another step's
MORE_WORK = "system:more-work"      # a slow lane that stopped at its batch limit runs again at the next tick, not an hour later


def record_lane(conn: Conn, key: str, now: datetime, result: Any = None, error: str | None = None, *, again: bool = False) -> None:
    """Remember that a lane ran, with what it did. Its own savepoint: bookkeeping never breaks the tick."""
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO scheduler_lanes(lane, last_run_at, last_result, last_error, runs, run_requested_by, updated_at) VALUES (%s,%s,%s,%s,1,%s,now()) "
                "ON CONFLICT (lane) DO UPDATE SET last_run_at=EXCLUDED.last_run_at, last_result=EXCLUDED.last_result, last_error=EXCLUDED.last_error, "
                "runs=scheduler_lanes.runs+1, run_requested_by=EXCLUDED.run_requested_by", (key, now, jb(_figures(result)), error, MORE_WORK if again else None))
    except Exception:  # noqa: BLE001
        pass


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
    from .discovery.plans import coverage_figures, sync_account_plans, sync_plans
    from .discovery.run import due_count
    now = now or datetime.now(timezone.utc)
    lane = LANE["discovery_plans"]
    if lane_due(lane, lane_rows(conn).get(lane.key), now):
        # hourly, not every tick: the priority list scans every workbook row (0.4 s a tick on production for no change).
        # Plans are visible in Administration even before Exa is configured; the lead monitor is one search per priority account.
        res = {"plans_added": sync_plans(conn), "account_plans_added": sync_account_plans(conn), **coverage_figures(conn, now)}
        record_lane(conn, lane.key, now, res)
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


def awards_need_recheck(conn: Conn) -> bool:
    """Is any stored award judged under an older relevance rule? A range test on the relevance_v index (migration 0047), not a scan of
    every award: the old `IS DISTINCT FROM` query read all 61k awards on every tick to find nothing."""
    from .renewals import RELEVANCE_V
    return bool(q1(conn, "SELECT 1 FROM awarded_contracts WHERE relevance_v IS NULL OR relevance_v < %s OR relevance_v > %s LIMIT 1", (RELEVANCE_V, RELEVANCE_V)))


def recheck_awards_if_needed(conn: Conn) -> int:
    from .renewals import recheck_awards
    return recheck_awards(conn) if awards_need_recheck(conn) else 0


def tender_run_failed_since(conn: Conn, since: datetime | None) -> bool:
    """A tender source run that failed or stopped part-way since `since` may have left notices without their finding."""
    return bool(q1(conn, "SELECT 1 FROM monitoring_runs r JOIN sources s USING (source_id) WHERE s.source_family='procurement' "
                         "AND r.status IN ('partial','failed') AND r.started_at > %s LIMIT 1", (since or datetime(2000, 1, 1, tzinfo=timezone.utc),)))


def expire_past_tenders(conn: Conn, now: datetime, grace_hours: int = 12) -> int:
    """Open tenders whose deadline passed more than `grace_hours` ago are marked expired (the connectors' own rule: a past deadline is
    expired). Every page already hides them by deadline; this keeps the stored status and the counts honest. A later official update
    with a new deadline re-opens the notice through the normal ingest, exactly as before."""
    return conn.execute("UPDATE procurement_notices SET status='expired', updated_at=now() WHERE status IN ('open','amended') AND deadline_at < %s",
                        (now - timedelta(hours=grace_hours),)).rowcount


def run_maintenance(conn: Conn, now: datetime | None = None) -> dict:
    """Idempotent housekeeping. Every step runs in its own savepoint: one failing step is reported and the rest still run, so the scheduler
    heartbeat never goes quiet. Each step is a lane (LANES) and runs only when it is due on its own cadence and is not paused."""
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
    rows = lane_rows(conn)

    def step(name: str, fn: Callable[[], Any], *, force: Callable[[dict[str, Any] | None], bool] | None = None,
             more: Callable[[Any], bool] | None = None) -> None:
        """Run one lane when it is due (or when `force` says something happened that needs it now); `more` says it stopped at its batch limit."""
        lane = LANE.get(name)
        row = rows.get(name)
        if lane is not None and not lane_due(lane, row, now) and not (force and lane_due(lane, {**(row or {}), "last_run_at": None}, now) and force(row)):
            if row and row.get("last_error") and (row["enabled"] or not lane.can_pause):
                out[f"{name}_error"] = row["last_error"]          # still failing as of its last run: the self-test (canary) keeps seeing it
            return
        try:
            with conn.transaction():
                r = fn()
                again = bool(more and more(r))
        except Exception as exc:  # noqa: BLE001
            out[f"{name}_error"] = f"{type(exc).__name__}: {exc}"[:200]
            record_lane(conn, name, now, None, out[f"{name}_error"])
            return
        record_lane(conn, name, now, r, again=again)
        if isinstance(r, dict):
            out.update({(k if name in ("expiry", "initiatives") else f"{name}_{k}" if (k in ("scored", "cleared") or name in PREFIXED_LANES) else k): v for k, v in r.items()})
        else:
            out[name] = r

    step("finance_pilot_scans", lambda: schedule_finance_pilot(conn, now))
    step("watchlist_scans", lambda: schedule_watchlist(conn, now))
    from .sources.scan_rotation import schedule as schedule_scan_rotation
    step("scan_rotation", lambda: schedule_scan_rotation(conn, now))      # after the daily and weekly checks: whatever they queued today is not doubled
    from .connections.store import schedule_syncs
    step("connection_sync", lambda: schedule_syncs(conn, now))
    step("market_tagged", lambda: tag_untagged(conn, 1500))
    from .market import tag_public_bodies
    step("public_bodies_tagged", lambda: tag_public_bodies(conn, 500))
    from .events.triage import relevance_version, retriage
    step("retriaged", lambda: retriage(conn, 500), more=lambda _r: bool(q1(
        conn, "SELECT 1 FROM source_documents WHERE raw_meta->>'triage'='skipped_not_relevant' AND content_text IS NOT NULL "
              "AND fetched_at > now() - interval '21 days' AND coalesce(raw_meta->>'triage_v','') <> %s LIMIT 1", (relevance_version(),))))
    from .events.structured import backfill_procurement_events
    step("procurement_events_backfilled", lambda: backfill_procurement_events(conn, 300),
         force=lambda row: tender_run_failed_since(conn, (row or {}).get("last_run_at")), more=lambda n: int(n or 0) >= 300)
    from .events.structured import recheck_procurement_relevance
    step("tender_relevance", lambda: recheck_procurement_relevance(conn, 2000), more=lambda r: int((r or {}).get("tenders_rechecked") or 0) >= 2000)
    step("tenders_expired", lambda: expire_past_tenders(conn, now))
    step("fit_queued", lambda: schedule_fit(conn, now))
    from .tender_summary import schedule as schedule_tender_summaries
    step("tender_summaries_queued", lambda: schedule_tender_summaries(conn, now))     # every two hours while tenders wait for a summary
    from .renewals import schedule_recent
    step("renewals_refreshed", lambda: schedule_recent(conn, now))
    step("renewals_rechecked", lambda: recheck_awards_if_needed(conn), more=lambda n: int(n or 0) >= 4000)
    from .identity.auto import schedule as schedule_identity_auto
    step("identity_auto_queued", lambda: schedule_identity_auto(conn, now))      # hourly while company matches wait for an automatic look
    from .sources.autovalidate import schedule as schedule_source_check
    step("source_check_queued", lambda: schedule_source_check(conn, now))        # once a day: test every source, switch on the safe ones that pass
    step("candidates", lambda: promote_candidates(conn, 40))
    step("candidate_gate", lambda: reclassify_candidates(conn, 300))
    from .regulatory_calendar import pin as pin_deadlines
    step("deadlines_pinned", lambda: pin_deadlines(conn, now), more=lambda r: bool((r or {}).get("more_to_pin")))      # after the target checks above
    from .signals.hiring import emit_hard_to_fill
    step("hard_to_fill_roles", lambda: emit_hard_to_fill(conn, now), more=lambda r: bool((r or {}).get("hard_to_fill_more")))
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
