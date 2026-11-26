"""The rotating check of every target account's own pages (scheduler lane ``scan_rotation``): who is next, what is skipped, the limits, no
double queueing, the pause switch, the AI reserve, and that the job runs on the background worker lane. Companies are invented; nothing here
reads a website, calls Exa or calls the AI (jobs are only queued)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ri import scheduler
from ri.jobs.queue import enqueue
from ri.scheduler import LANE, lane_next, run_maintenance, schedule_finance_pilot, schedule_watchlist
from ri.signals.baseline import import_baseline
from ri.signals.persist import sync_rule_versions
from ri.sources import registry, scan_rotation
from ri.sources.scan_rotation import KIND, Limits, schedule

NOW = datetime(2026, 11, 26, 10, 2, tzinfo=timezone.utc)
BIG = Limits(per_day=100, per_run=100, max_queued=100)       # no limit in the way: the test is about order and skips


def target(conn, name, *, website=True, eligibility="Eligible", master=False, score=None, stopped=False, checked=None, endpoint_read=None):
    """A target account (the lead list marks it Eligible). `checked`: when its coverage record was written; `endpoint_read`: when one of its pages was last read."""
    slug = name.lower().replace(" ", "")
    oid = str(conn.execute(
        "insert into organizations(display_name, canonical_domain, country_code, segment, entity_type, company_ownership, in_master_list, list_eligibility) "
        "values (%s,%s,'GB','Banking','company','unknown',%s,%s) returning id", (name, f"{slug}.example" if website else None, master, eligibility)).fetchone()["id"])
    if score is not None or stopped:
        conn.execute("insert into account_scores(organization_id, score, band, stopped) values (%s,%s,'none',%s)", (oid, score or 0, stopped))
    if checked:
        conn.execute("insert into account_source_coverage(organization_id, source_family, coverage_state, updated_at) values (%s,'newsroom','source_not_found',%s)", (oid, checked))
    if endpoint_read:
        conn.execute("insert into account_endpoints(organization_id, kind, url, last_scanned_at) values (%s,'newsroom',%s,%s)", (oid, f"https://{slug}.example/news", endpoint_read))
    return oid


def scan_job(conn, oid, status="scheduled", *, kind="account_scan", age_days=0.0):
    jid, _ = enqueue(conn, kind, {"organization_id": oid}, idempotency_key=f"seed:{kind}:{oid}:{status}")
    conn.execute("update jobs set status=%s, updated_at=%s where id=%s", (status, NOW - timedelta(days=age_days), jid))
    return jid


def queued(conn, key_prefix="scan-rotation:"):
    """The accounts this lane queued, in the order the worker will take them (run_after is the insert time, so it keeps the queueing order)."""
    return [str(r["payload"]["organization_id"]) for r in conn.execute("select payload from jobs where kind=%s and idempotency_key like %s order by run_after", (KIND, key_prefix + "%"))]


def names(conn, ids):
    return [conn.execute("select display_name from organizations where id=%s", (i,)).fetchone()["display_name"] for i in ids]


def ai_budget(conn, used, *, reserved=0, period="2026-11", cap=1_000_000):
    conn.execute("insert into provider_budgets(provider, kind, period, cap, spent, reserved, unit) values ('anthropic','monthly',%s,%s,%s,%s,'tokens')", (period, cap, used, reserved))


def quiet_daily_and_weekly_checks(conn):
    """Pause the daily and weekly checks so a test sees the rotation on its own (they would queue every one of a handful of targets first)."""
    conn.execute("insert into scheduler_lanes(lane, enabled) values ('finance_pilot_scans', false), ('watchlist_scans', false)")


def _base(conn):
    import_baseline(conn); sync_rule_versions(conn); registry.seed_registry(conn)
    quiet_daily_and_weekly_checks(conn)


# ------------------------------------------------------------------ the lane
def test_the_lane_is_declared_visible_and_pausable_and_queues_the_background_kind():
    lane = LANE["scan_rotation"]
    assert lane.main and lane.can_pause and lane.queues == KIND == "account_scan_background"
    assert lane.every_s == scheduler.HOUR and not lane.adjustable
    assert lane.label and lane.what and "20 a day" in lane.what
    keys = [x.key for x in scheduler.LANES]
    assert keys.index("finance_pilot_scans") < keys.index("watchlist_scans") < keys.index("scan_rotation")     # the daily and weekly checks queue first


# ------------------------------------------------------------------ who is next
def test_never_checked_come_first_then_the_oldest_check_and_a_page_read_counts_as_checked(conn):
    target(conn, "Old Bank", checked=NOW - timedelta(days=40))
    target(conn, "Older Bank", checked=NOW - timedelta(days=90))
    target(conn, "Read Bank", endpoint_read=NOW - timedelta(days=60))                                     # pages read, no coverage record: still "checked"
    target(conn, "Both Bank", checked=NOW - timedelta(days=20), endpoint_read=NOW - timedelta(days=100))  # the later of the two counts
    target(conn, "Never Bank")
    out = schedule(conn, NOW, BIG)
    assert out["queued"] == 5
    assert names(conn, queued(conn)) == ["Never Bank", "Older Bank", "Read Bank", "Old Bank", "Both Bank"]
    assert out["targets_checked"] == 4 and out["targets_total"] == 5


def test_among_never_checked_the_master_list_then_the_account_score_then_a_stable_order(conn):
    target(conn, "Plain Bank")
    target(conn, "Scored Bank", score=40)
    target(conn, "More Scored Bank", score=90)
    target(conn, "Master Bank", master=True)
    schedule(conn, NOW, BIG)
    assert names(conn, queued(conn)) == ["Master Bank", "More Scored Bank", "Scored Bank", "Plain Bank"]


def test_only_target_accounts_with_a_website_that_are_not_stopped_are_checked(conn):
    ok = target(conn, "Fine Bank")
    target(conn, "No Site Bank", website=False)
    target(conn, "Out Of Scope Bank", eligibility="Out of scope")
    target(conn, "Stopped Bank", stopped=True)
    conn.execute("insert into organizations(display_name, canonical_domain, country_code, segment, entity_type) values ('Not On The List','nolist.example','GB','Banking','company')")
    conn.execute("insert into organizations(display_name, canonical_domain, country_code, segment, entity_type, list_eligibility) values ('Public Body','pb.example','GB','Banking','public_body','Eligible')")
    out = schedule(conn, NOW, BIG)
    assert queued(conn) == [ok]
    assert out["targets_total"] == 1 and out["targets_without_website"] == 1 and out["targets_checked"] == 0


def test_an_account_checked_in_the_last_14_days_is_left_alone_and_the_interval_cannot_be_shortened(conn):
    target(conn, "Fresh Bank", checked=NOW - timedelta(days=13, hours=23))
    due = target(conn, "Due Bank", checked=NOW - timedelta(days=14, hours=1))
    schedule(conn, NOW, Limits(per_day=100, per_run=100, max_queued=100, rescan_days=1))      # even if asked for a 1-day interval, 14 days is the floor
    assert queued(conn) == [due]


def test_a_rescan_asks_the_scan_to_look_for_pages_again_and_a_first_scan_does_not_need_to(conn):
    again = target(conn, "Again Bank", checked=NOW - timedelta(days=50))
    first = target(conn, "First Bank")
    schedule(conn, NOW, BIG)
    jobs = {str(r["payload"]["organization_id"]): r for r in conn.execute("select * from jobs where kind=%s", (KIND,))}
    assert jobs[again]["payload"]["rediscover"] is True and jobs[first]["payload"]["rediscover"] is False
    for j in jobs.values():
        assert j["correlation_id"] == "scan-rotation" and j["max_attempts"] == 2 and j["status"] == "scheduled" and j["payload"]["rotation"] is True
        assert j["idempotency_key"] == f"scan-rotation:{j['payload']['organization_id']}:2026-11-26"


# ------------------------------------------------------------------ skips
def test_an_account_with_a_scan_already_waiting_or_running_is_never_doubled_whoever_queued_it(conn):
    waiting = target(conn, "Waiting Bank")
    running = target(conn, "Running Bank")
    retrying = target(conn, "Retrying Bank")
    free = target(conn, "Free Bank")
    scan_job(conn, waiting, "scheduled")                                   # a seller's button press, or the daily check
    scan_job(conn, running, "claimed", kind=KIND)                          # an earlier job of this lane that the worker holds
    scan_job(conn, retrying, "retryable_failed")
    out = schedule(conn, NOW, BIG)
    assert out["queued"] == 1 and queued(conn) == [free]


def test_an_account_whose_scan_failed_in_the_last_3_days_waits_and_is_tried_again_after(conn):
    failed = target(conn, "Failed Bank")
    refused = target(conn, "Refused Bank")
    long_ago = target(conn, "Long Ago Bank")
    scan_job(conn, failed, "permanent_failed", age_days=2.9)
    scan_job(conn, refused, "quarantined", kind=KIND, age_days=1)          # e.g. a worker without a handler for the kind
    scan_job(conn, long_ago, "permanent_failed", age_days=3.1)
    assert schedule(conn, NOW, BIG)["queued"] == 1 and queued(conn) == [long_ago]
    later = schedule(conn, NOW + timedelta(days=1), BIG)                   # a day later `failed` is 3.9 days old, `refused` 2 days
    assert later["queued"] == 1 and sorted(queued(conn)) == sorted([long_ago, failed]) and refused


def test_a_finished_earlier_job_does_not_block_an_account_that_is_due_again(conn):
    oid = target(conn, "Done Bank", checked=NOW - timedelta(days=30))
    scan_job(conn, oid, "completed", age_days=30)
    assert schedule(conn, NOW, BIG)["queued"] == 1 and queued(conn) == [oid]


# ------------------------------------------------------------------ limits
def test_each_run_queues_a_few_and_the_day_has_a_limit_that_resets_at_midnight_utc(conn):
    for i in range(12):
        target(conn, f"Bank {chr(65 + i)}")
    limits = Limits(per_day=5, per_run=2, max_queued=100)
    assert [schedule(conn, NOW + timedelta(hours=h), limits)["queued"] for h in range(4)] == [2, 2, 1, 0]
    held = schedule(conn, NOW + timedelta(hours=5), limits)
    assert held["queued"] == 0 and held["held_by"] == "daily_limit" and held["queued_today"] == 5
    assert len(queued(conn)) == 5 and len(set(queued(conn))) == 5
    tomorrow = schedule(conn, datetime(2026, 11, 27, 0, 30, tzinfo=timezone.utc), limits)
    assert tomorrow["queued"] == 2 and tomorrow["queued_today"] == 2 and "held_by" not in tomorrow and len(set(queued(conn))) == 7


def test_never_more_than_the_allowed_number_waiting_or_running_at_once(conn):
    for i in range(10):
        target(conn, f"Bank {chr(65 + i)}")
    limits = Limits(per_day=100, per_run=4, max_queued=5)
    assert schedule(conn, NOW, limits)["queued"] == 4
    assert schedule(conn, NOW + timedelta(hours=1), limits)["queued"] == 1 and len(queued(conn)) == 5          # only one slot was free
    full = schedule(conn, NOW + timedelta(hours=2), limits)
    assert full["queued"] == 0 and full["held_by"] == "queue_busy"
    conn.execute("update jobs set status='completed' where id in (select id from jobs where kind=%s order by run_after limit 3)", (KIND,))
    assert schedule(conn, NOW + timedelta(hours=3), limits)["queued"] == 3                                      # slots free up as the worker finishes
    assert len(set(queued(conn))) == 8                                                                          # three new accounts, not the finished three again


def test_the_default_limits_and_their_environment_overrides(monkeypatch):
    d = Limits()
    assert (d.per_day, d.per_run, d.max_queued, d.rescan_days) == (20, 2, 6, 14)
    assert Limits.from_env() == d
    monkeypatch.setenv("RI_SCAN_ROTATION_PER_DAY", "35"); monkeypatch.setenv("RI_SCAN_ROTATION_PER_RUN", "3")
    monkeypatch.setenv("RI_SCAN_ROTATION_MAX_QUEUED", "8"); monkeypatch.setenv("RI_SCAN_ROTATION_RESCAN_DAYS", "30")
    assert Limits.from_env() == Limits(35, 3, 8, 30)
    monkeypatch.setenv("RI_SCAN_ROTATION_RESCAN_DAYS", "3"); monkeypatch.setenv("RI_SCAN_ROTATION_PER_DAY", "100000"); monkeypatch.setenv("RI_SCAN_ROTATION_PER_RUN", "x")
    again = Limits.from_env()
    assert again.rescan_days == 14 and again.per_day == 200 and again.per_run == 2         # never under 14 days, capped, a bad value falls back
    monkeypatch.setenv("RI_SCAN_ROTATION_PER_DAY", "0")
    assert Limits.from_env().per_day == 0


def test_a_day_limit_of_zero_queues_nothing(conn):
    target(conn, "Some Bank")
    out = schedule(conn, NOW, Limits(per_day=0))
    assert out["queued"] == 0 and out["held_by"] == "daily_limit" and queued(conn) == []


# ------------------------------------------------------------------ idempotency and the other checks
def test_running_the_same_tick_twice_queues_nothing_new(conn):
    for i in range(4):
        target(conn, f"Bank {chr(65 + i)}")
    assert schedule(conn, NOW, BIG)["queued"] == 4
    assert schedule(conn, NOW, BIG)["queued"] == 0 and len(queued(conn)) == 4
    conn.execute("update jobs set status='cancelled' where kind=%s", (KIND,))          # even with the first jobs gone from the queue, today's key stays used
    assert schedule(conn, NOW, BIG)["queued"] == 0 and len(queued(conn)) == 4


def test_a_job_that_finished_today_without_a_check_on_record_is_not_picked_again_the_same_day(conn):
    stuck = target(conn, "Stuck Bank")
    other = target(conn, "Other Bank")
    one = Limits(per_day=100, per_run=1, max_queued=100)
    assert schedule(conn, NOW, one)["queued"] == 1
    first = queued(conn)[0]
    conn.execute("update jobs set status='completed' where kind=%s", (KIND,))          # finished, but no coverage was written (should not happen; must not loop)
    assert schedule(conn, NOW + timedelta(hours=1), one)["queued"] == 1
    assert sorted(queued(conn)) == sorted([stuck, other]) and queued(conn)[0] == first  # the second hour moved on to the other account


def test_the_daily_check_and_the_rotation_do_not_double_an_account(conn):
    for i in range(3):
        target(conn, f"Bank {chr(65 + i)}", master=True)
    assert schedule_finance_pilot(conn, NOW) == 3                                       # the top-25 check queued all three
    assert schedule(conn, NOW, BIG)["queued"] == 0 and queued(conn) == []
    assert conn.execute("select count(*) c from jobs where kind='account_scan'").fetchone()["c"] == 3


def test_with_more_than_25_targets_the_daily_check_takes_the_top_25_and_the_rotation_the_rest(conn):
    _base(conn)
    conn.execute("delete from scheduler_lanes")                                         # both the daily and weekly checks and the rotation run this time
    for i in range(30):
        target(conn, f"Bank {i:02d}", master=i < 25, score=100 - i)
    out = run_maintenance(conn, NOW)
    pilot = {str(r["payload"]["organization_id"]) for r in conn.execute("select payload from jobs where kind='account_scan'")}
    rotation = set(queued(conn))
    assert len(pilot) == 25 and len(rotation) == out["scan_rotation_queued"] == 2 and not pilot & rotation
    assert out["scan_rotation_targets_total"] == 30


def test_accounts_the_daily_and_weekly_checks_read_count_as_checked_so_the_rotation_moves_on(conn):
    target(conn, "Daily Bank", checked=NOW - timedelta(hours=9))                                         # the top-25 check read it this morning
    starred = target(conn, "Starred Bank", endpoint_read=NOW - timedelta(days=6))                         # the weekly check read it last week
    stale_star = target(conn, "Stale Star Bank", endpoint_read=NOW - timedelta(days=10))                  # the weekly check queues it; the rotation waits (under 14 days)
    nobody = target(conn, "Nobody Bank")
    conn.execute("insert into watchlist(organization_id, user_email) values (%s,'bdm@x'), (%s,'bdm@x')", (starred, stale_star))
    assert schedule_watchlist(conn, NOW) == 1                                                             # only the stale star
    out = schedule(conn, NOW, BIG)
    assert queued(conn) == [nobody] and out["targets_checked"] == 3


def test_a_starred_account_never_checked_is_queued_by_the_weekly_check_and_left_to_it(conn):
    starred = target(conn, "Starred Bank")
    other = target(conn, "Other Bank")
    conn.execute("insert into watchlist(organization_id, user_email) values (%s,'bdm@x')", (starred,))
    assert schedule_watchlist(conn, NOW) == 1
    schedule(conn, NOW, BIG)
    assert queued(conn) == [other]


# ------------------------------------------------------------------ money: the AI reserve, no Exa
def test_it_stops_queueing_at_70_percent_of_the_monthly_ai_limit_and_resumes_below(conn):
    target(conn, "Some Bank")
    ai_budget(conn, 710_000)
    held = schedule(conn, NOW, BIG)
    assert held["queued"] == 0 and held["held_by"] == "ai_budget" and queued(conn) == []
    assert held["targets_total"] == 1                                                  # the progress figures are still reported while it waits
    conn.execute("update provider_budgets set spent=690000, reserved=5000 where provider='anthropic'")        # 69.5% in use: under the line
    assert schedule(conn, NOW, BIG)["queued"] == 1
    conn.execute("update provider_budgets set spent=700001 where provider='anthropic'")                       # 70.0% spent plus the reservation: over
    assert schedule(conn, NOW + timedelta(hours=1), BIG)["held_by"] == "ai_budget"


def test_a_reserved_but_unspent_share_counts_like_a_spent_one(conn):
    target(conn, "Some Bank")
    ai_budget(conn, 300, reserved=410, cap=1000)
    assert schedule(conn, NOW, BIG)["held_by"] == "ai_budget"


def test_the_ai_limit_of_another_month_or_no_limit_at_all_does_not_hold_it_back(conn):
    target(conn, "Some Bank")
    ai_budget(conn, 990_000, period="2026-10")
    assert schedule(conn, NOW, BIG)["queued"] == 1             # no limit saved this month: pages are still found and kept for later reading


def test_the_reserve_is_the_one_the_scan_handler_gives_its_extractor_and_no_exa_is_touched(conn):
    import ri.jobs.handlers as handlers
    assert scan_rotation.AI_RESERVE_FRACTION == 0.3 and "reserve_fraction = 0.3" in Path(handlers.__file__).read_text()
    code = Path(scan_rotation.__file__).read_text().split("from __future__")[1].lower()
    assert "exa" not in code and "apollo" not in code           # nothing here can call Exa or Apollo
    conn.execute("insert into provider_budgets(provider, kind, period, cap, spent, reserved, unit) values ('exa','monthly','2026-11',20,15,0,'usd')")     # Exa 75% used
    target(conn, "Some Bank")
    assert schedule(conn, NOW, BIG)["queued"] == 1             # reading pages costs no Exa, so Exa spending is no reason to wait
    assert conn.execute("select count(*) c from provider_usage").fetchone()["c"] == 0
    assert float(conn.execute("select spent from provider_budgets where provider='exa'").fetchone()["spent"]) == 15


# ------------------------------------------------------------------ the lane inside the scheduler
def test_the_lane_runs_hourly_records_its_progress_and_waits_for_the_next_hour(conn):
    _base(conn)
    a = target(conn, "Bank A", checked=NOW - timedelta(days=3))
    for i in range(1, 6):
        target(conn, f"Bank {chr(65 + i)}")
    target(conn, "No Site Bank", website=False)
    out = run_maintenance(conn, NOW)
    assert out["scan_rotation_queued"] == 2 and out["scan_rotation_targets_total"] == 6 and out["scan_rotation_targets_checked"] == 1
    row = conn.execute("select * from scheduler_lanes where lane='scan_rotation'").fetchone()
    assert row["runs"] == 1 and row["last_error"] is None
    assert row["last_result"] == {"queued": 2, "queued_today": 2, "targets_checked": 1, "targets_total": 6, "targets_without_website": 1}
    assert "scan_rotation_queued" not in run_maintenance(conn, NOW + timedelta(minutes=5))      # hourly: the next tick waits
    out3 = run_maintenance(conn, NOW + timedelta(minutes=60))
    assert out3["scan_rotation_queued"] == 2 and len(queued(conn)) == 4 and a not in queued(conn)


def test_the_pause_switch_stops_the_lane_and_switching_it_on_again_resumes_it(conn):
    _base(conn)
    target(conn, "Some Bank")
    conn.execute("insert into scheduler_lanes(lane, enabled) values ('scan_rotation', false)")
    assert lane_next(LANE["scan_rotation"], conn.execute("select * from scheduler_lanes where lane='scan_rotation'").fetchone(), NOW) is None
    out = run_maintenance(conn, NOW)
    assert "scan_rotation_queued" not in out and queued(conn) == []
    conn.execute("update scheduler_lanes set enabled=true where lane='scan_rotation'")
    assert run_maintenance(conn, NOW + timedelta(minutes=5))["scan_rotation_queued"] == 1


def test_a_person_can_ask_for_the_lane_to_run_at_the_next_tick(conn):
    _base(conn)
    for i in range(3):
        target(conn, f"Bank {chr(65 + i)}")
    run_maintenance(conn, NOW)                                                           # queues 2 of the 3
    conn.execute("update scheduler_lanes set run_requested_by='admin@x' where lane='scan_rotation'")
    out = run_maintenance(conn, NOW + timedelta(minutes=5))
    assert out["scan_rotation_queued"] == 1 and len(set(queued(conn))) == 3
    assert conn.execute("select run_requested_by from scheduler_lanes where lane='scan_rotation'").fetchone()["run_requested_by"] is None


def test_a_failing_lane_reports_its_error_and_the_rest_of_the_tick_still_runs(conn, monkeypatch):
    _base(conn)
    monkeypatch.setattr(scan_rotation, "schedule", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic failure")))
    out = run_maintenance(conn, NOW)
    assert out["scan_rotation_error"].startswith("RuntimeError") and "reevaluated" in out and "score_scored" in out
    assert conn.execute("select last_error from scheduler_lanes where lane='scan_rotation'").fetchone()["last_error"].startswith("RuntimeError")


def test_data_health_lists_the_lane_with_its_progress(conn):
    from ri.api.admin_ops import data_health
    _base(conn)
    target(conn, "Some Bank")
    run_maintenance(conn, NOW)
    lane = next(x for x in data_health(conn, NOW, can_edit_lanes=True)["lanes"] if x["key"] == "scan_rotation")
    assert lane["main"] and lane["can_pause"] and lane["enabled"] and lane["queues"] == KIND
    assert lane["result"]["targets_total"] == 1 and lane["result"]["queued"] == 1 and lane["last_run_at"] == NOW


# ------------------------------------------------------------------ the background worker lane
def _interactive_kinds() -> list[str]:
    cli = Path(scan_rotation.__file__).parents[1] / "cli.py"
    return re.findall(r'"(\w+)"', re.search(r"INTERACTIVE = \[(.*?)\]", cli.read_text(), re.S).group(1))


def test_the_rotation_job_kind_is_not_on_the_workers_interactive_list():
    interactive = _interactive_kinds()
    assert "account_scan" in interactive             # a seller's button press stays on the fast lane ...
    assert KIND not in interactive                   # ... the scheduled rotation is not on it, so the worker runs it in the background lane (every kind not listed)


def test_the_worker_has_a_handler_for_the_rotation_kind_the_same_function_as_the_account_scan():
    from ri.jobs.handlers import build_handlers
    handlers = build_handlers(connector_factory=lambda sid: None)
    assert KIND in handlers, "jobs/handlers.py must map the kind to the account_scan handler: add \"account_scan_background\": account_scan to its returned dict"
    assert handlers[KIND] is handlers["account_scan"] and getattr(handlers[KIND], "own_transactions", False)


def _worker(conn, monkeypatch, *, ai=True):
    """The worker's handler for the scan with fake pages and a fake model, wired to the rotation kind the way jobs/handlers.py must wire it."""
    import httpx
    from test_r7_verify_discovery_usage import ANCHOR, FakeInner
    from test_r9_account_scan import site
    from ri.events.budgeted import BudgetedExtractor
    from ri.jobs.handlers import build_handlers
    from ri.sources import safe
    monkeypatch.setattr(safe, "SafeClient", lambda timeout=30: httpx.AsyncClient(transport=httpx.MockTransport(site)))
    handlers = build_handlers(connector_factory=lambda sid: None, extractor_factory=(lambda c: BudgetedExtractor(FakeInner(ANCHOR), c)) if ai else None)
    return {KIND: handlers["account_scan"]}


def test_a_rotation_job_runs_through_the_scan_handler_records_the_check_and_the_rotation_moves_on(conn, monkeypatch):
    from ri import budget, usage
    from ri.jobs.queue import run_one
    registry.seed_registry(conn)
    budget.set_cap(conn, "anthropic", "monthly", usage.period_now(), 1_000_000, "tokens")
    oid = str(conn.execute("insert into organizations(display_name, canonical_domain, country_code, segment, entity_type, list_eligibility) "
                           "values ('Synthetic Example Bank AG','bank.example','GB','Banking','company','Eligible') returning id").fetchone()["id"])
    conn.commit()
    assert schedule(conn, NOW, BIG)["queued"] == 1
    conn.commit()
    assert run_one(conn, "background-test", _worker(conn, monkeypatch), [KIND]) == "completed"
    job = conn.execute("select status, result from jobs where kind=%s", (KIND,)).fetchone()
    assert job["status"] == "completed" and job["result"]["status"] == "ok" and job["result"]["documents"] >= 1
    assert conn.execute("select count(*) c from account_source_coverage where organization_id=%s", (oid,)).fetchone()["c"] == 3
    after = schedule(conn, NOW + timedelta(days=1), BIG)                       # it is on record as checked now: the rotation leaves it alone
    assert after["queued"] == 0 and after["targets_checked"] == 1 and "held_by" not in after
    again = schedule(conn, datetime.now(timezone.utc) + timedelta(days=15), BIG)           # two weeks on it is due again, and asks the scan to look for pages anew
    assert again["queued"] == 1
    assert conn.execute("select payload from jobs where kind=%s order by run_after desc", (KIND,)).fetchone()["payload"]["rediscover"] is True


def test_a_job_queued_before_the_ai_reserve_was_reached_still_reads_pages_but_does_not_spend_into_the_reserve(conn, monkeypatch):
    from ri import budget, usage
    from ri.jobs.queue import run_one
    registry.seed_registry(conn)
    budget.set_cap(conn, "anthropic", "monthly", usage.period_now(), 1_000_000, "tokens")
    conn.execute("update provider_budgets set spent=720000 where provider='anthropic'")                     # 72% used: the worker's extractor must refuse
    oid = str(conn.execute("insert into organizations(display_name, canonical_domain, country_code, segment, entity_type, list_eligibility) "
                           "values ('Synthetic Example Bank AG','bank.example','GB','Banking','company','Eligible') returning id").fetchone()["id"])
    scan_rotation_job = enqueue(conn, KIND, {"organization_id": oid, "rediscover": False, "rotation": True}, idempotency_key=f"scan-rotation:{oid}:2026-11-26")
    conn.commit()
    assert scan_rotation_job and run_one(conn, "background-test", _worker(conn, monkeypatch), [KIND]) == "completed"
    assert float(conn.execute("select spent from provider_budgets where provider='anthropic'").fetchone()["spent"]) == 720000      # nothing was spent
    assert conn.execute("select count(*) c from events where subject_org_id=%s and event_type='vendor_selected'", (oid,)).fetchone()["c"] == 0      # the news page waits for the AI
    assert conn.execute("select count(*) c from source_documents where raw_meta->>'pending_extraction'='true'").fetchone()["c"] >= 1
    assert conn.execute("select count(*) c from account_source_coverage where organization_id=%s", (oid,)).fetchone()["c"] == 3     # but the account is checked
