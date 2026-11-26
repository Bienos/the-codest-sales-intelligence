"""Round 7, area 2: web search for target accounts that have no finding in the last 90 days.

Exa and the AI are fakes: nothing here leaves the machine. The names are invented."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from ri import budget, usage
from ri.config import Settings
from ri.db import jb
from ri.discovery import plans as plans_mod
from ri.discovery.plans import (
    COVERAGE_THEME,
    coverage_candidates,
    coverage_figures,
    coverage_queries,
    coverage_waiting,
    ensure_coverage_plans,
    sync_plans,
)
from ri.discovery.run import COVERAGE_MAX_BACKLOG, DEFAULT_EST, due_count, run_discovery
from ri.sources.base import AuthError, RawItem, TransientError

NOW = datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc)


class FakeExa:
    """Records every plan it is asked to search; returns four distinct pages per query (and one social page, which is never queued)."""

    def __init__(self, cost: float = 0.007, fail=None, fail_for: str | None = None):
        self.cost, self.fail, self.fail_for, self.plans = cost, fail, fail_for, []

    async def search(self, plan, *, num_results=10):
        if self.fail and (self.fail_for is None or self.fail_for in plan.query):
            raise self.fail
        self.plans.append(plan)
        slug = abs(hash(plan.query)) % 10**8
        urls = [f"https://news.invalid/{slug}/{i}" for i in range(1, 5)] + ["https://www.linkedin.com/x"]
        items = [RawItem(external_id=u, version=None, url=u, payload={"title": f"hit {i}", "url": u, "published": "2026-11-12T00:00:00.000Z", "rank": i, "plan_id": plan.plan_id, "query": plan.query})
                 for i, u in enumerate(urls, 1)]
        return items, {"requests": 1, "reported_cost": self.cost, "plan_id": plan.plan_id}


def mk_target(conn, name: str, country: str = "GB", *, master: bool = False, eligibility: str = "Eligible", kind: str = "company", ownership: str = "unknown") -> str:
    return str(conn.execute(
        "INSERT INTO organizations(display_name, country_code, entity_type, list_eligibility, in_master_list, company_ownership) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (name, country, kind, eligibility, master, ownership)).fetchone()["id"])


def mk_finding(conn, org: str, *, days_ago: int = 10, replay: bool = False, lifecycle: str = "active") -> None:
    when = NOW - timedelta(days=days_ago)
    conn.execute("INSERT INTO events(event_key, event_type, subject_org_id, summary, extraction_model_version, first_observed_at, event_at, is_historical_replay, lifecycle_status) "
                 "VALUES (%s,'programme_announced',%s,'A programme',%s,%s,%s,%s,%s)",
                 (f"k-{org}-{days_ago}-{replay}-{lifecycle}", org, "t", when, when, replay, lifecycle))


def caps(conn, *, exa: float = 20.0, ai: float = 1_000_000, exa_spent: float = 0.0, ai_spent: float = 0.0) -> None:
    budget.set_cap(conn, "exa", "monthly", usage.period_now(), exa, "usd")
    budget.set_cap(conn, "anthropic", "monthly", usage.period_now(), ai, "tokens")
    conn.execute("UPDATE provider_budgets SET spent=%s WHERE provider='exa'", (exa_spent,))
    conn.execute("UPDATE provider_budgets SET spent=%s WHERE provider='anthropic'", (ai_spent,))
    conn.commit()


def run(conn, exa=None, **kw):
    kw.setdefault("coverage_searches", 25)
    kw.setdefault("coverage_max_new_requests", 45)
    kw.setdefault("coverage_cycle_days", 35)
    kw.setdefault("auto_budget_share", 0.6)
    kw.setdefault("now", NOW)
    kw.setdefault("max_searches", 16)
    kw.setdefault("max_new_requests", 30)
    return run_discovery(conn, exa or FakeExa(), interval_days=7, est_cost=None, **kw)


# ----------------------------------------------------------------------------------------------------------------- selection

def test_only_target_accounts_without_a_finding_in_90_days_are_picked(conn):
    quiet = mk_target(conn, "Quiet Bank")
    busy = mk_target(conn, "Busy Bank")
    old = mk_target(conn, "Old News Bank")
    replay = mk_target(conn, "Replay Bank")
    superseded = mk_target(conn, "Superseded Bank")
    mk_finding(conn, busy, days_ago=10)
    mk_finding(conn, old, days_ago=120)                          # older than 90 days: no recent finding
    mk_finding(conn, replay, days_ago=5, replay=True)            # reprocessed history is not a finding
    mk_finding(conn, superseded, days_ago=5, lifecycle="superseded")
    mk_target(conn, "Public Agency", kind="public_body")
    mk_target(conn, "Out Of Scope Bank", eligibility="Out of scope")
    mk_target(conn, "State Bank", eligibility="Review", ownership="state_owned")
    stopped = mk_target(conn, "Stopped Bank")
    conn.execute("INSERT INTO account_scores(organization_id, score, band, stopped) VALUES (%s, 1, 'none', true)", (stopped,))
    conn.commit()
    got = {r["display_name"] for r in coverage_candidates(conn, now=NOW, limit=100)}
    assert got == {"Quiet Bank", "Old News Bank", "Replay Bank", "Superseded Bank"}
    assert quiet and busy not in got


def test_rotation_is_never_searched_first_then_oldest_and_a_searched_account_waits_for_the_next_cycle(conn):
    a = mk_target(conn, "Alpha Bank")
    b = mk_target(conn, "Bravo Bank")
    c = mk_target(conn, "Charlie Bank", master=True)             # on the shortlist: first among the never-searched
    d = mk_target(conn, "Delta Bank")
    e = mk_target(conn, "Echo Bank")
    for org, days in ((a, 60), (b, 40), (d, 36), (e, 10)):       # e was searched 10 days ago: not due for 35 days
        ensure_coverage_plans(conn, org, "x", "GB")
        conn.execute("UPDATE discovery_plans SET last_run_at=%s WHERE organization_id=%s", (NOW - timedelta(days=days), org))
    conn.commit()
    order = [r["display_name"] for r in coverage_candidates(conn, now=NOW, limit=10)]
    assert order == ["Charlie Bank", "Alpha Bank", "Bravo Bank", "Delta Bank"]          # never searched, then 60, 40, 36 days ago
    assert coverage_waiting(conn, now=NOW) == 4 and coverage_waiting(conn, now=NOW, at_least_one=True) == 1
    assert [r["display_name"] for r in coverage_candidates(conn, now=NOW, limit=2)] == ["Charlie Bank", "Alpha Bank"]
    assert e and c
    later = [r["display_name"] for r in coverage_candidates(conn, now=NOW + timedelta(days=30), limit=10)]
    assert later[0] == "Charlie Bank" and "Echo Bank" in later and len(later) == 5


def test_an_account_that_gets_a_finding_drops_out_by_itself(conn):
    org = mk_target(conn, "Soon Covered Bank")
    assert coverage_waiting(conn, now=NOW) == 1
    mk_finding(conn, org, days_ago=1)
    assert coverage_waiting(conn, now=NOW) == 0 and coverage_candidates(conn, now=NOW) == []


# ------------------------------------------------------------------------------------------------------------ plan content

def test_each_account_gets_queries_in_its_market_language_with_a_date_window(conn):
    assert [(v, lang) for v, lang, _ in coverage_queries("Rhein Bank AG", "DE")] == [("local", "de"), ("en", "en")]
    assert [(v, lang) for v, lang, _ in coverage_queries("Rhein Bank AG", "CH")] == [("local", "de"), ("en", "en")]
    assert [lang for _, lang, _ in coverage_queries("Nord Bank", "SE")] == ["sv", "en"]
    assert [lang for _, lang, _ in coverage_queries("Wisla Bank", "PL")] == ["pl", "en"]
    assert [lang for _, lang, _ in coverage_queries("Thames Bank", "GB")] == ["en"]
    assert [lang for _, lang, _ in coverage_queries("Fjord Bank", "NO")] == ["en"]                 # no language of its own in the plans: English only
    assert coverage_queries("   ", "GB") == [] and coverage_queries(None, "GB") == []
    q_en = coverage_queries('The "Quoted" Bank', "GB")[0][2]
    assert q_en.startswith('"The Quoted Bank" ') and q_en.count('"') == 2                          # a quote in the name cannot break the phrase
    for _, _, q in coverage_queries("Rhein Bank AG", "DE"):
        assert '"Rhein Bank AG"' in q
    assert "Softwareentwickler" in coverage_queries("Rhein Bank AG", "DE")[0][2] and "rekryterar utvecklare" in coverage_queries("Nord Bank", "SE")[0][2]
    for words in ("programme", "migration"), ("hiring",), ("partner", "vendor"), ("launch",), ("regulatory",):
        assert all(w in q_en for w in words)                                                       # the five engineering demand signals
    assert len(coverage_queries("A" * 300, "GB")[0][2]) < 330                                       # long names are cut


def test_saved_searches_are_not_duplicated_and_are_refreshed_when_the_account_changes(conn):
    org = mk_target(conn, "Rhein Bank AG", "DE")
    first = ensure_coverage_plans(conn, org, "Rhein Bank AG", "DE")
    again = ensure_coverage_plans(conn, org, "Rhein Bank AG", "DE")
    assert len(first) == len(again) == 2
    assert conn.execute("SELECT count(*) n, count(DISTINCT plan_id) d FROM discovery_plans WHERE theme=%s", (COVERAGE_THEME,)).fetchone() == {"n": 2, "d": 2}
    rows = {r["language"]: r for r in again}
    assert rows["de"]["recency_days"] == 90 and rows["en"]["recency_days"] == 90 and rows["de"]["geography"] == "DE" and str(rows["de"]["organization_id"]) == org
    conn.execute("UPDATE discovery_plans SET enabled=false WHERE plan_id=%s", (rows["en"]["plan_id"],))        # a person switched the English one off
    renamed = ensure_coverage_plans(conn, org, "Rhein Bank Gruppe AG", "DE")
    assert [r["language"] for r in renamed] == ["de"] and "Rhein Bank Gruppe AG" in renamed[0]["query"]      # refreshed, and the one switched off stays off
    assert conn.execute("SELECT count(*) n FROM discovery_plans WHERE theme=%s", (COVERAGE_THEME,)).fetchone()["n"] == 2


# --------------------------------------------------------------------------------------------------------------------- run

def test_one_search_per_account_in_the_run_and_the_per_run_cap_holds(conn):
    caps(conn)
    orgs = [mk_target(conn, f"Cap Bank {i}", "DE") for i in range(6)]
    conn.commit()
    exa = FakeExa()
    out = run(conn, exa, coverage_searches=4)
    assert out["searches"] == 4 and out["accounts_searched"] == 4 and len(exa.plans) == 4
    assert len({p.query.split('"')[1] for p in exa.plans}) == 4                                    # four different accounts: never two searches for one
    assert all(p.language == "de" for p in exa.plans)                                               # first search for a German account: its own language
    assert out["accounts_still_without_a_search"] == 2 and out["summary"] == "Searched 4 accounts; 2 accounts still without a search."
    out2 = run(conn, exa, coverage_searches=4)                                                      # the same day again: only the two left
    assert out2["accounts_searched"] == 2 and out2["accounts_still_without_a_search"] == 0 and len(exa.plans) == 6
    assert len(orgs) == 6 and run(conn, exa)["accounts_searched"] == 0                              # nothing left: no more spending


def test_pages_go_through_the_page_check_only_deduplicated_and_never_become_events(conn):
    caps(conn)
    org = mk_target(conn, "Thames Bank", "GB")
    ensure_coverage_plans(conn, org, "Thames Bank", "GB")
    seen = "https://news.invalid/seen"
    conn.execute("INSERT INTO verification_requests(url, origin, requested_by) VALUES (%s,'sheet_signal','t')", (seen,))
    conn.commit()

    class Seen(FakeExa):
        async def search(self, plan, *, num_results=10):
            items, meta = await super().search(plan, num_results=num_results)
            items[0] = RawItem(external_id=seen, version=None, url=seen, payload={"title": "seen", "url": seen, "rank": 1, "plan_id": plan.plan_id, "query": plan.query})
            return items, meta

    out = run(conn, Seen())
    reqs = conn.execute("SELECT organization_id, origin, status, url, plan_id FROM verification_requests WHERE origin='exa_discovery'").fetchall()
    assert out["requests_created"] == len(reqs) == 3                                                # four real pages minus the seen one... capped at three per search
    assert all(str(r["organization_id"]) == org and r["status"] == "queued" and "linkedin" not in r["url"] and r["url"] != seen for r in reqs)
    assert conn.execute("SELECT count(*) n FROM events").fetchone()["n"] == 0                       # nothing becomes a finding without the page check
    assert conn.execute("SELECT count(*) n FROM jobs WHERE kind='verify_url'").fetchone()["n"] == 3
    use = conn.execute("SELECT cost_usd, cost_basis, organization_id, plan_id FROM provider_usage WHERE provider='exa'").fetchone()
    assert float(use["cost_usd"]) == 0.007 and use["cost_basis"] == "reported" and str(use["organization_id"]) == org


def test_per_run_page_limit_for_account_searches(conn):
    caps(conn)
    for i in range(10):
        mk_target(conn, f"Page Bank {i}")
    conn.commit()
    out = run(conn, FakeExa(), coverage_max_new_requests=7)
    assert out["requests_created"] == 7 and out["accounts_searched"] == 3 and out["stopped"] == "per-run request limit reached"


def test_the_two_languages_alternate_across_cycles_and_a_later_search_looks_back_only_as_far_as_needed(conn):
    caps(conn)
    mk_target(conn, "Rhein Bank AG", "DE")
    conn.commit()
    exa = FakeExa()
    run(conn, exa)
    run(conn, exa, now=NOW + timedelta(days=36))
    run(conn, exa, now=NOW + timedelta(days=72))
    assert [p.language for p in exa.plans] == ["de", "en", "de"]
    assert [p.recency_days for p in exa.plans] == [90, 43, 43]                                       # 36 days + 7: the time since the last search, plus a week
    assert conn.execute("SELECT count(*) n FROM discovery_plans WHERE theme=%s", (COVERAGE_THEME,)).fetchone()["n"] == 2        # no duplicate plans
    assert [r["runs"] for r in conn.execute("SELECT runs FROM discovery_plans WHERE theme=%s ORDER BY language", (COVERAGE_THEME,))] == [2, 1]       # German twice, English once


def test_general_saved_searches_never_run_the_account_searches_and_the_other_way_round(conn):
    caps(conn)
    sync_plans(conn)
    mk_target(conn, "Thames Bank", "GB")
    conn.commit()
    exa = FakeExa()
    out = run(conn, exa, coverage_searches=0, max_new_requests=500)                                # lane off: general plans only
    assert out["searches"] == 16 and "accounts_searched" not in out
    assert all('"Thames Bank"' not in p.query for p in exa.plans)
    assert conn.execute("SELECT count(*) n FROM discovery_plans WHERE theme=%s", (COVERAGE_THEME,)).fetchone()["n"] == 0
    out = run(conn, FakeExa(), max_searches=0)                                                     # general plans off: the account search still runs
    assert out["accounts_searched"] == 1 and out["searches"] == 1                                    # the account plans are not picked up by the general loop


# ---------------------------------------------------------------------------------------------------- the 60% stop and gates

def test_automatic_searches_stop_at_60_percent_of_the_monthly_cap_but_buttons_do_not(conn):
    caps(conn, exa=20.0, exa_spent=11.99)                                                           # 11.99 + 0.01 = 12.00 = 60%: still allowed
    mk_target(conn, "Thames Bank", "GB")
    mk_target(conn, "Avon Bank", "GB")
    conn.commit()
    exa = FakeExa(cost=0.007)
    out = run(conn, exa)
    assert out["accounts_searched"] == 1 and len(exa.plans) == 1                                    # one fits under 12.00 (spent is now 11.997) ...
    assert out["stopped"].startswith("automatic web searches stop at 60%") and "sellers" in out["stopped"]
    spent = float(conn.execute("SELECT spent FROM provider_budgets WHERE provider='exa'").fetchone()["spent"])
    assert spent == pytest.approx(11.997) and spent <= 12.0                                          # ... and the next would cross it
    before = len(exa.plans)
    out = run(conn, exa)
    assert len(exa.plans) == before and out["searches"] == 0 and out["stopped"].startswith("automatic web searches stop at 60%")
    # a seller or administrator pressing "Run now" on one saved search may use the rest of the cap
    pid = conn.execute("SELECT plan_id FROM discovery_plans WHERE theme=%s ORDER BY plan_id LIMIT 1", (COVERAGE_THEME,)).fetchone()["plan_id"]
    out = run(conn, exa, plan_ids=[pid])
    assert out["searches"] == 1 and out["stopped"] is None and len(exa.plans) == before + 1


def test_general_searches_stop_at_the_same_share_and_the_whole_run_ends(conn):
    caps(conn, exa=20.0, exa_spent=12.0)
    sync_plans(conn)
    mk_target(conn, "Thames Bank", "GB")
    conn.commit()
    exa = FakeExa()
    out = run(conn, exa)
    assert exa.plans == [] and out["searches"] == 0 and out["stopped"].startswith("automatic web searches stop at 60%") and out["accounts_searched"] == 0
    assert out["accounts_still_without_a_search"] == 1                                               # progress is still reported


def test_the_share_comes_from_settings_and_an_unusable_cap_gives_the_usual_message(conn):
    caps(conn, exa=20.0, exa_spent=9.0)
    mk_target(conn, "Thames Bank", "GB")
    conn.commit()
    out = run(conn, FakeExa(), auto_budget_share=0.45)                                              # 45% of 20 = 9.00: spent already there
    assert out["searches"] == 0 and out["stopped"].startswith("automatic web searches stop at 45%")
    conn.execute("UPDATE provider_budgets SET spent=0 WHERE provider='exa'")
    budget.set_cap(conn, "exa", "monthly", usage.period_now(), 0.005, "usd")                         # a cap below one reservation: budget.reserve says so
    conn.commit()
    out = run(conn, FakeExa())
    assert out["searches"] == 0 and out["stopped"] == "monthly Exa budget reached"


def test_account_searches_wait_while_the_pages_they_find_could_not_be_read(conn):
    mk_target(conn, "Thames Bank", "GB")
    conn.commit()
    # no AI budget at all
    budget.set_cap(conn, "exa", "monthly", usage.period_now(), 20.0, "usd")
    conn.commit()
    exa = FakeExa()
    out = run(conn, exa)
    assert exa.plans == [] and "not set up" in out["coverage_waiting_because"]
    # AI reading paused (70% of the AI limit used)
    caps(conn, ai=1000, ai_spent=800)
    out = run(conn, exa)
    assert exa.plans == [] and "paused" in out["coverage_waiting_because"]
    # a long queue of unread pages
    caps(conn)
    for i in range(COVERAGE_MAX_BACKLOG + 1):
        conn.execute("INSERT INTO verification_requests(url, origin, status, requested_by) VALUES (%s,'exa_discovery','queued','t')", (f"https://wait.invalid/{i}",))
    conn.commit()
    out = run(conn, exa)
    assert exa.plans == [] and str(COVERAGE_MAX_BACKLOG + 1) in out["coverage_waiting_because"]
    conn.execute("DELETE FROM verification_requests")
    conn.commit()
    assert run(conn, exa)["accounts_searched"] == 1                                                  # reader is fine again: the search happens


def test_a_failed_search_costs_nothing_and_does_not_stop_the_others_but_a_refused_key_does(conn):
    caps(conn)
    mk_target(conn, "Avon Bank", "GB")
    mk_target(conn, "Thames Bank", "GB")
    mk_target(conn, "Severn Bank", "GB")
    conn.commit()
    exa = FakeExa(fail=TransientError("boom"), fail_for="Thames")
    out = run(conn, exa)
    assert out["accounts_searched"] == 2 and out["stopped"] is None
    b = conn.execute("SELECT spent, reserved FROM provider_budgets WHERE provider='exa'").fetchone()
    assert float(b["spent"]) == pytest.approx(0.014) and float(b["reserved"]) == 0                   # the failed one was released
    failed = conn.execute("SELECT last_error, last_run_at FROM discovery_plans WHERE query LIKE '%%Thames%%'").fetchone()
    assert failed["last_error"] and failed["last_run_at"] is None                                    # it is first in line tomorrow
    out = run(conn, FakeExa(fail=AuthError("401")))
    assert out["searches"] == 0 and "refused" in out["stopped"]
    assert float(conn.execute("SELECT reserved FROM provider_budgets WHERE provider='exa'").fetchone()["reserved"]) == 0


def test_the_time_limit_keeps_the_job_inside_its_lease(conn):
    caps(conn)
    for i in range(3):
        mk_target(conn, f"Time Bank {i}")
    conn.commit()
    exa = FakeExa()
    out = run(conn, exa, max_seconds=-1)
    assert exa.plans == [] and out["stopped"].startswith("time limit") and out["accounts_searched"] == 0


# ------------------------------------------------------------------------------------------------ visibility and scheduling

def test_progress_figures_for_data_health_and_the_scheduler_check(conn):
    assert coverage_figures(conn, NOW) == {}                                                          # no such account: nothing to show
    caps(conn)
    for i in range(7):
        mk_target(conn, f"Figure Bank {i}")
    conn.commit()
    assert coverage_figures(conn, NOW) == {"accounts_searched": 0, "accounts_still_without_a_search": 7, "accounts_without_a_finding": 7}
    assert due_count(conn, 7, NOW) == 1                                                               # work waiting: the daily run is queued
    run(conn, FakeExa(), coverage_searches=5)
    assert coverage_figures(conn, NOW) == {"accounts_searched": 5, "accounts_still_without_a_search": 2, "accounts_without_a_finding": 7}
    run(conn, FakeExa())
    assert coverage_figures(conn, NOW)["accounts_still_without_a_search"] == 0
    assert due_count(conn, 7, NOW) == 0                                                               # all searched within the cycle: nothing to queue
    assert due_count(conn, 7, NOW + timedelta(days=36)) == 1                                          # the next cycle
    # every figure is a plain number, so the Data health page shows it as "5 accounts searched" and so on
    assert all(isinstance(v, int) for v in coverage_figures(conn, NOW).values())


def test_due_count_counts_general_searches_and_respects_the_off_switch(conn, monkeypatch):
    sync_plans(conn)
    n = due_count(conn, 7, NOW)
    assert n == conn.execute("SELECT count(*) n FROM discovery_plans WHERE theme NOT IN ('account_watch', %s)", (COVERAGE_THEME,)).fetchone()["n"] > 0
    conn.execute("UPDATE discovery_plans SET last_run_at=%s", (NOW,))
    mk_target(conn, "Thames Bank", "GB")
    conn.commit()
    assert due_count(conn, 7, NOW) == 1
    monkeypatch.setenv("RI_DISCOVERY_COVERAGE_SEARCHES_PER_RUN", "0")
    assert due_count(conn, 7, NOW) == 0


def test_settings_defaults_and_environment_overrides(monkeypatch):
    s = Settings()
    assert (s.discovery_coverage_searches_per_run, s.discovery_coverage_max_new_requests_per_run, s.discovery_coverage_cycle_days, s.discovery_auto_budget_share) == (25, 45, 35, 0.60)
    monkeypatch.setenv("RI_DISCOVERY_COVERAGE_SEARCHES_PER_RUN", "10")
    monkeypatch.setenv("RI_DISCOVERY_COVERAGE_MAX_NEW_REQUESTS_PER_RUN", "20")
    monkeypatch.setenv("RI_DISCOVERY_COVERAGE_CYCLE_DAYS", "42")
    monkeypatch.setenv("RI_DISCOVERY_AUTO_BUDGET_SHARE", "0.5")
    s = Settings.from_env(require_auth=False)
    assert (s.discovery_coverage_searches_per_run, s.discovery_coverage_max_new_requests_per_run, s.discovery_coverage_cycle_days, s.discovery_auto_budget_share) == (10, 20, 42, 0.5)
    assert s.discovery_max_searches_per_run == 16 and s.discovery_max_new_requests_per_run == 30          # the general limits are unchanged


def test_the_arithmetic_in_plans_py_holds():
    s = Settings()
    group = 840                                                                                      # target accounts without a finding in 90 days (the production count)
    days = math.ceil(group / s.discovery_coverage_searches_per_run)
    assert 28 <= days <= 42 and days <= s.discovery_coverage_cycle_days                              # the whole group in 4 to 6 weeks
    worst_month = s.discovery_coverage_searches_per_run * 31 * DEFAULT_EST                           # every search billed at the 1-cent reservation
    assert worst_month <= 8.0
    assert s.discovery_auto_budget_share * 20.0 == pytest.approx(12.0)                                # sellers keep at least $8 of the $20
    assert plans_mod.COVERAGE_WINDOW_DAYS == 90


def test_the_worker_needs_no_new_wiring_the_coverage_limits_come_from_the_environment(conn, monkeypatch):
    """cli.py hands run_discovery only the five general settings; the account search then reads its own from Settings (RI_DISCOVERY_COVERAGE_*)."""
    caps(conn)
    for i in range(5):
        mk_target(conn, f"Env Bank {i}")
    conn.commit()
    monkeypatch.setenv("RI_DISCOVERY_COVERAGE_SEARCHES_PER_RUN", "2")
    out = run_discovery(conn, FakeExa(), max_searches=16, max_new_requests=30, interval_days=7, est_cost=None, now=NOW)
    assert out["accounts_searched"] == 2 and out["accounts_still_without_a_search"] == 3
    monkeypatch.setenv("RI_DISCOVERY_AUTO_BUDGET_SHARE", "0.0001")                                    # an operator can tighten the share without a deploy
    out = run_discovery(conn, FakeExa(), max_searches=16, max_new_requests=30, interval_days=7, est_cost=None, now=NOW)
    assert out["accounts_searched"] == 0 and out["stopped"].startswith("automatic web searches stop at 0.01%")
