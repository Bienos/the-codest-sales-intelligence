"""Scheduler lanes (data pipeline review, round 4.3): each housekeeping step runs on its own cadence, records its last run and result,
can be paused or asked to run by a person when that is safe, and the expensive every-tick steps found on production no longer scan
whole tables to find nothing."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ri import scheduler
from ri.scheduler import LANE, LANES, awards_need_recheck, expire_past_tenders, lane_due, lane_next, record_lane, run_maintenance
from ri.signals.baseline import import_baseline
from ri.signals.persist import sync_rule_versions
from ri.sources import registry

NOW = datetime(2026, 11, 26, 10, 2, tzinfo=timezone.utc)


def _base(conn):
    import_baseline(conn); sync_rule_versions(conn); registry.seed_registry(conn)


def _lane(conn, key):
    return conn.execute("select * from scheduler_lanes where lane=%s", (key,)).fetchone()


def test_every_lane_has_a_plain_label_and_a_cadence_within_a_day():
    assert len({lane.key for lane in LANES}) == len(LANES)
    for lane in LANES:
        assert lane.label and lane.what and scheduler.TICK_SECONDS <= lane.every_s <= 86400, lane.key
    assert scheduler.tick_seconds() == scheduler.TICK_SECONDS == 300


def test_first_tick_runs_everything_and_records_it_hourly_lanes_then_wait(conn):
    _base(conn)
    out = run_maintenance(conn, NOW)
    assert "market_tagged" in out and "retriaged" in out and "tenders_expired" in out and "reevaluated" in out
    row = _lane(conn, "retriaged")
    assert row["last_run_at"] == NOW and row["runs"] == 1 and row["last_error"] is None and row["last_result"] == {"n": 0}
    out2 = run_maintenance(conn, NOW + timedelta(minutes=5))
    assert "market_tagged" in out2 and "reevaluated" in out2          # every-tick lanes run again
    assert "retriaged" not in out2 and "tenders_expired" not in out2 and "tenders_rechecked" not in out2   # hourly lanes wait
    assert _lane(conn, "market_tagged")["runs"] == 2 and _lane(conn, "retriaged")["runs"] == 1
    out3 = run_maintenance(conn, NOW + timedelta(minutes=60))
    assert "retriaged" in out3                                         # an hour later (with a minute of slack) it runs again


def test_a_paused_lane_waits_a_correctness_lane_cannot_be_paused_and_a_request_runs_it_at_the_next_tick(conn):
    _base(conn)
    conn.execute("insert into scheduler_lanes(lane, enabled) values ('fit_queued', false), ('expiry', false)")
    out = run_maintenance(conn, NOW)
    assert "fit_queued" not in out                                     # paused by a person
    assert "signals_expired" in out                                    # signal expiry keeps the data right: the flag is ignored
    assert lane_next(LANE["fit_queued"], _lane(conn, "fit_queued"), NOW) is None
    conn.execute("update scheduler_lanes set last_run_at=%s, run_requested_by='admin@x' where lane='retriaged'", (NOW,))
    rows = scheduler.lane_rows(conn)
    assert lane_due(LANE["retriaged"], rows["retriaged"], NOW + timedelta(minutes=5))
    run_maintenance(conn, NOW + timedelta(minutes=5))
    assert _lane(conn, "retriaged")["run_requested_by"] is None and _lane(conn, "retriaged")["runs"] == 2


def test_a_person_can_slow_down_an_adjustable_lane_but_not_a_fixed_one():
    retri, fixed = LANE["retriaged"], LANE["expiry"]
    last = {"enabled": True, "every_s": 3 * 3600, "last_run_at": NOW, "run_requested_by": None}
    assert not lane_due(retri, last, NOW + timedelta(hours=2)) and lane_due(retri, last, NOW + timedelta(hours=3))
    assert lane_due(fixed, {**last, "every_s": 86400}, NOW + timedelta(minutes=5))      # a stored cadence on a fixed lane is ignored


def test_a_lane_that_stops_at_its_batch_limit_runs_again_at_the_next_tick(conn, monkeypatch):
    import ri.events.structured as structured
    _base(conn)
    monkeypatch.setattr(structured, "recheck_procurement_relevance", lambda c, limit: {"tenders_rechecked": limit, "tender_signals_dropped": 0})
    run_maintenance(conn, NOW)
    assert _lane(conn, "tender_relevance")["run_requested_by"] == scheduler.MORE_WORK
    out = run_maintenance(conn, NOW + timedelta(minutes=5))
    assert out.get("tenders_rechecked") == 2000                                   # still catching up, not an hour later
    monkeypatch.setattr(structured, "recheck_procurement_relevance", lambda c, limit: {"tenders_rechecked": 3, "tender_signals_dropped": 0})
    run_maintenance(conn, NOW + timedelta(minutes=10))
    assert _lane(conn, "tender_relevance")["run_requested_by"] is None
    assert "tenders_rechecked" not in run_maintenance(conn, NOW + timedelta(minutes=15))


def test_a_failing_lane_records_its_error_and_the_others_still_run(conn, monkeypatch):
    import ri.scoring as scoring
    _base(conn)
    monkeypatch.setattr(scoring, "recompute_scores", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic failure")))
    out = run_maintenance(conn, NOW)
    assert out["score_error"].startswith("RuntimeError") and "reevaluated" in out
    assert _lane(conn, "score")["last_error"].startswith("RuntimeError")
    import ri.events.triage as triage
    monkeypatch.setattr(triage, "retriage", lambda *a, **k: (_ for _ in ()).throw(ValueError("broken word list")))
    conn.execute("update scheduler_lanes set last_run_at=NULL where lane='retriaged'")
    assert run_maintenance(conn, NOW + timedelta(minutes=5))["retriaged_error"].startswith("ValueError")
    later = run_maintenance(conn, NOW + timedelta(minutes=10))           # an hourly lane that failed is not due yet ...
    assert later["retriaged_error"].startswith("ValueError")             # ... but its error stays visible to the self-test until it works again


def test_tender_backfill_is_hourly_but_runs_straight_after_a_failed_tender_run(conn, monkeypatch):
    import ri.events.structured as structured
    _base(conn)
    calls = []
    monkeypatch.setattr(structured, "backfill_procurement_events", lambda c, limit: calls.append(1) or 0)
    run_maintenance(conn, NOW)
    run_maintenance(conn, NOW + timedelta(minutes=5))
    assert len(calls) == 1
    conn.execute("insert into monitoring_runs(source_id, status, started_at, finished_at, error_class) values ('procurement-ted','partial',%s,%s,'rate_limited')",
                 (NOW + timedelta(minutes=7), NOW + timedelta(minutes=8)))
    run_maintenance(conn, NOW + timedelta(minutes=10))
    assert len(calls) == 2                                              # the stopped run's notices get their findings now, not an hour later
    run_maintenance(conn, NOW + timedelta(minutes=15))
    assert len(calls) == 2


def test_awards_are_rechecked_only_when_one_is_stored_under_an_older_rule(conn, monkeypatch):
    from ri import renewals
    calls = []
    real = renewals.recheck_awards
    monkeypatch.setattr(renewals, "recheck_awards", lambda c, limit=4000: calls.append(1) or real(c, limit))
    conn.execute("insert into awarded_contracts(notice_id, title, cpv, relevant, relevance_v, end_basis) values ('1-2026','Core banking software','{72000000}',true,%s,'assumed')",
                 (renewals.RELEVANCE_V,))
    assert awards_need_recheck(conn) is False and scheduler.recheck_awards_if_needed(conn) == 0 and calls == []
    conn.execute("insert into awarded_contracts(notice_id, title, cpv, relevant, relevance_v, end_basis) values ('2-2026','Office chairs','{39110000}',true,'v1','assumed'), "
                 "('3-2026','Payments platform','{72000000}',false,NULL,'assumed')")
    assert awards_need_recheck(conn) is True
    assert scheduler.recheck_awards_if_needed(conn) == 2 and len(calls) == 1
    assert awards_need_recheck(conn) is False
    assert conn.execute("select count(*) n from awarded_contracts where relevance_v=%s", (renewals.RELEVANCE_V,)).fetchone()["n"] == 3


def test_open_tenders_past_their_deadline_are_closed_after_a_grace_period(conn):
    registry.seed_registry(conn)
    rows = [("A-1", "open", NOW - timedelta(days=2)), ("A-2", "amended", NOW - timedelta(hours=13)), ("A-3", "open", NOW - timedelta(hours=2)),
            ("A-4", "open", NOW + timedelta(days=5)), ("A-5", "awarded", NOW - timedelta(days=9)), ("A-6", "open", None)]
    for nid, st, dl in rows:
        conn.execute("insert into procurement_notices(source_id, notice_id, title, status, deadline_at, official_url) values ('procurement-ted',%s,'IT',%s,%s,'https://ted.europa.eu/x')",
                     (nid, st, dl))
    assert expire_past_tenders(conn, NOW) == 2
    got = {r["notice_id"]: r["status"] for r in conn.execute("select notice_id, status from procurement_notices")}
    assert got == {"A-1": "expired", "A-2": "expired", "A-3": "open", "A-4": "open", "A-5": "awarded", "A-6": "open"}


def test_discovery_plans_are_synced_hourly_not_on_every_tick(conn, monkeypatch):
    import ri.discovery.plans as plans
    _base(conn)
    calls = []
    monkeypatch.setattr(plans, "sync_plans", lambda c: calls.append("plans") or 0)
    monkeypatch.setattr(plans, "sync_account_plans", lambda c: calls.append("accounts") or 0)
    scheduler.schedule_discovery(conn, NOW, enabled=False)
    scheduler.schedule_discovery(conn, NOW + timedelta(minutes=5), enabled=False)
    assert calls == ["plans", "accounts"]
    scheduler.schedule_discovery(conn, NOW + timedelta(minutes=61), enabled=False)
    assert calls == ["plans", "accounts", "plans", "accounts"]
    assert _lane(conn, "discovery_plans")["last_result"] == {"plans_added": 0, "account_plans_added": 0}


def test_bookkeeping_never_breaks_a_tick(conn):
    record_lane(conn, "x" * 3, NOW, {"nested": {"a": 1}, "n": 3, "items": [1, 2]})       # only plain figures are kept
    assert _lane(conn, "xxx")["last_result"] == {"n": 3}
    conn.execute("alter table scheduler_lanes rename to scheduler_lanes_tmp")
    try:
        assert scheduler.lane_rows(conn) == {}
        record_lane(conn, "retriaged", NOW, 1)                          # no table: nothing recorded, no error, the transaction still usable
        assert conn.execute("select 1 as one").fetchone()["one"] == 1
    finally:
        conn.execute("alter table scheduler_lanes_tmp rename to scheduler_lanes")


def test_the_new_indexes_exist_and_the_awards_check_can_use_its_index(conn):
    names = {r["indexname"] for r in conn.execute("select indexname from pg_indexes where schemaname='public'")}
    assert {"awarded_contracts_relevance_v_idx", "awarded_contracts_relevant_awarded_idx", "source_documents_skipped_idx", "source_documents_pending_idx",
            "legacy_research_org_kind_idx", "procurement_notices_notice_id_idx"} <= names
    conn.execute("set local enable_seqscan = off")
    plan = "\n".join(r["QUERY PLAN"] for r in conn.execute(
        "explain select 1 from awarded_contracts where relevance_v is null or relevance_v < 'v4' or relevance_v > 'v4' limit 1"))
    assert "awarded_contracts_relevance_v_idx" in plan
    plan = "\n".join(r["QUERY PLAN"] for r in conn.execute(
        "explain select id from source_documents where raw_meta->>'pending_extraction'='true' and content_text is not null order by fetched_at limit 50"))
    assert "source_documents_pending_idx" in plan
