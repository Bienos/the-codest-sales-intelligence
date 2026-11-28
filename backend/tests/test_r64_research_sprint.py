"""Research sprint (lane ``research_sprint``): Exa company research for P1 then P2 accounts, a few an hour, inside the automatic share of the
Exa budget and the AI reserve, stopping after repeated failures. Nothing here calls Exa: the lane only queues jobs. Names are invented."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from ri.db import q1, qa
from ri.research import sprint
from ri.scheduler import LANE, run_maintenance

NOW = datetime(2026, 11, 28, 10, 0, tzinfo=timezone.utc)
READY = SimpleNamespace(exa_api_key="k", exa_monthly_cap_usd=20.0, anthropic_api_key="k", discovery_auto_budget_share=0.6)


def acct(conn, name, priority, *, website=True, master=True, score=50):
    return str(conn.execute(
        "insert into organizations(display_name, canonical_domain, country_code, segment, entity_type, company_ownership, in_master_list, list_eligibility, "
        "list_priority, list_score) values (%s,%s,'GB','Banking','company','private',%s,'Eligible',%s,%s) returning id",
        (name, f"{name.lower().replace(' ', '')}.example" if website else None, master, priority, score)).fetchone()["id"])


def queued(conn):
    return [r["display_name"] for r in qa(conn, "select o.display_name from account_profiles ap join organizations o on o.id=ap.organization_id "
                                                "where ap.requested_by='research-sprint' order by ap.created_at, o.display_name")]


def budget(conn, provider, cap, spent, unit):
    conn.execute("insert into provider_budgets(provider, kind, period, cap, spent, reserved, unit) values (%s,'monthly','2026-11',%s,%s,0,%s)", (provider, cap, spent, unit))


def test_p1_comes_before_p2_and_each_run_queues_two_jobs(conn):
    acct(conn, "Beta P2 Bank", "P2", score=99)
    acct(conn, "Alpha P1 Bank", "P1", score=10)
    acct(conn, "Gamma P1 Bank", "P1", score=80)
    acct(conn, "Plain Bank", None)
    acct(conn, "No Site P1 Bank", "P1", website=False)
    out = sprint.schedule(conn, NOW, READY)
    assert out["queued"] == 2 and set(queued(conn)) == {"Alpha P1 Bank", "Gamma P1 Bank"}        # both P1s before the higher-scored P2
    assert (out["p1_left"], out["p2_left"]) == (2, 1)           # counted before this run queued them
    assert q1(conn, "select count(*) n from jobs where kind='account_profile'")["n"] == 2
    assert sprint.left(conn, NOW) == {"p1_left": 0, "p2_left": 1}


def test_accounts_with_a_profile_or_a_recent_failure_are_left_alone(conn):
    done = acct(conn, "Done Bank", "P1")
    failed = acct(conn, "Failed Bank", "P1")
    old = acct(conn, "Old Failure Bank", "P1")
    conn.execute("insert into account_profiles(organization_id, requested_by, status) values (%s,'seller@x','done')", (done,))
    conn.execute("insert into account_profiles(organization_id, requested_by, status, created_at) values (%s,'seller@x','failed',%s)", (failed, NOW - timedelta(days=2)))
    conn.execute("insert into account_profiles(organization_id, requested_by, status, created_at) values (%s,'seller@x','failed',%s)", (old, NOW - timedelta(days=20)))
    sprint.schedule(conn, NOW, READY)
    assert queued(conn) == ["Old Failure Bank"]


def test_it_holds_when_not_set_up_over_the_exa_share_or_the_ai_reserve(conn):
    acct(conn, "P1 Bank", "P1")
    assert sprint.schedule(conn, NOW, SimpleNamespace(exa_api_key=None, exa_monthly_cap_usd=0, anthropic_api_key=None))["held_by"] == "not_set_up"
    budget(conn, "exa", 20, 11.99, "usd")                       # 60% of $20 is $12: one more profile would pass it
    assert sprint.schedule(conn, NOW, READY)["held_by"] == "exa_budget"
    conn.execute("update provider_budgets set spent=5 where provider='exa'")
    budget(conn, "anthropic", 1_000_000, 750_000, "tokens")     # 25% of the AI limit left, under the 30% reserve
    assert sprint.schedule(conn, NOW, READY)["held_by"] == "ai_budget"
    conn.execute("update provider_budgets set spent=100000 where provider='anthropic'")
    assert sprint.schedule(conn, NOW, READY)["queued"] == 1 and queued(conn) == ["P1 Bank"]


def test_three_failures_in_a_row_stop_it_and_the_day_has_a_limit(conn):
    for i in range(20):
        acct(conn, f"Bank {i:02d}", "P1")
    for h in range(8):
        sprint.schedule(conn, NOW + timedelta(minutes=10 * h), READY)
        conn.execute("update account_profiles set status='done', finished_at=now() where requested_by='research-sprint' and status='queued'")
    assert len(queued(conn)) == sprint.PER_DAY and sprint.schedule(conn, NOW + timedelta(hours=2), READY)["held_by"] == "daily_limit"
    conn.execute("update account_profiles set status='failed', finished_at=now() + interval '1 hour' where id in "
                 "(select id from account_profiles where requested_by='research-sprint' order by created_at desc limit 3)")
    assert sprint.schedule(conn, NOW + timedelta(days=1), READY)["held_by"] == "repeated_failures"


def test_the_lane_is_hourly_pausable_and_runs_in_the_scheduler(conn, monkeypatch):
    lane = LANE["research_sprint"]
    assert lane.every_s == 3600 and lane.can_pause and lane.main and lane.queues == "account_profile"
    acct(conn, "P1 Bank", "P1")
    monkeypatch.setattr(sprint, "_settings", lambda: READY)
    run_maintenance(conn, NOW)
    row = q1(conn, "select last_result, last_error from scheduler_lanes where lane='research_sprint'")
    assert row["last_error"] is None and row["last_result"]["queued"] == 1
