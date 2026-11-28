"""Forecast fixes of 28 Nov 2026: the chance of reaching the target counts what is already won this year; a deal long past its stage's usual
time is neither expected to close at once nor at the full rate (the same rule as the funnel outlook); a deal won from an early stage counts
as getting past every later stage; a product line priced by the hour is not read as that many people."""
from datetime import date

import numpy as np
import pytest

from ri.crm import fields as pdf
from ri.forecast import engine, outlook
from ri.forecast.history import load_funnel, measure
from test_r64_forecast import NOW, add_history, seed_stages


def sims(inp, today=date(2027, 1, 5), year=2027, runs=2000):
    cal = engine.Calendar(today, year)
    dr = engine.Draws(runs, len(inp.open_stage), len(inp.conv), len(inp.streams), len(cal.months), 1.0)
    return engine.simulate(inp, cal, dr)


# ------------------------------------------------------------------ chance of target
def test_chance_of_target_counts_what_is_already_won():
    res = {"_year_samples": np.array([0.0, 500_000.0, 1_000_000.0, 1_500_000.0])}
    assert engine.chance_at_least(res, 2_000_000) == 0.0
    assert engine.chance_at_least(res, 2_000_000, already=1_000_000) == 0.5     # 1M won + the 1M and 1.5M runs reach 2M
    assert engine.chance_at_least(res, 2_000_000, already=-5) == 0.0            # a negative "won" never lowers the bar
    assert engine.chance_at_least(res, 0, already=1_000_000) is None


# ------------------------------------------------------------------ stale deals
def test_one_stale_rule_for_the_forecast_and_the_outlook():
    assert outlook.STALE is engine.STALE and outlook.stale_factor is engine.stale_factor
    assert list(engine.stale_factor(np.array([5.0, 25.0, 50.0]), np.array([10.0, 10.0, 10.0]))) == [1.0, 0.5, 0.25]


def test_an_overdue_deal_gets_a_fresh_stage_time_not_three_days():
    d = np.array([30.0, 30.0, 30.0])
    left = engine.residual(d, np.array([10.0, 29.0, 200.0]))
    assert list(left) == [20.0, engine.MIN_LEFT, 30.0]                          # inside the time: what is left; 200 days in: another 30


def test_a_months_old_deal_closes_later_and_less_often_than_a_fresh_one():
    """Same stage, rate and value; only the days already spent in the stage differ. Before the fix the old deal closed sooner."""
    fresh = engine.Inputs(conv=[0.6], days=[30], open_stage=[0], open_value=[100_000], open_age=[5], streams=[(0.0, 0)], deal_value=1)
    old = engine.Inputs(conv=[0.6], days=[30], open_stage=[0], open_value=[100_000], open_age=[200], streams=[(0.0, 0)], deal_value=1)
    f, o = sims(fresh), sims(old)
    assert f["wins"] == pytest.approx(0.6, abs=0.04)
    assert o["wins"] == pytest.approx(0.6 * 0.25, abs=0.04)                    # past four times the median: a quarter of the rate
    assert o["buckets"][1]["wins"] < f["buckets"][1]["wins"]


# ------------------------------------------------------------------ early wins
def test_a_deal_won_from_sql_counts_as_passing_every_later_stage(conn):
    seed_stages(conn)
    add_history(conn, 1, [(10, 0), (7, 10), (12, 30)], "won", value=150_000)   # won straight from SQL
    add_history(conn, 2, [(10, 0), (7, 10), (12, 30), (13, 40)], "lost")       # lost in Proposal
    h = measure(conn, load_funnel(conn, 2, ["Marketing Nurturing"], ["Active Delivery"]), now=NOW)
    names = [s.name for s in h.funnel.stages]
    by = dict(zip(names, [(s.passed, s.lost) for s in h.stats], strict=True))
    assert by["SQL"] == (2, 0) and by["Proposal/Shortlist"] == (1, 1) and by["Commercials/NDA"] == (1, 0)
    assert h.stats[names.index("Proposal/Shortlist")].days == []              # a skipped stage gets no made-up time in it


# ------------------------------------------------------------------ people on product lines
def test_hourly_lines_become_full_time_equivalents():
    s = pdf.summarize_products([
        {"name": "Senior developer", "quantity": 2, "item_price": 9000, "billing_frequency": "monthly"},
        {"name": "QA engineer (hourly)", "quantity": 80, "item_price": 40, "billing_frequency": "monthly"},
        {"name": "Support", "quantity": 160, "item_price": 30, "billing_frequency": "monthly"}])
    assert s["fte"] == pytest.approx(2 + 80 / 160 + 1)                          # name says hours; 160 a month can only be hours
    assert s["monthly"] == 2 * 9000 + 80 * 40 + 160 * 30                        # money is unchanged
    w = pdf.summarize_products([{"name": "Dev, godz.", "quantity": 40, "item_price": 50, "billing_frequency": "weekly"}])
    assert w["fte"] == pytest.approx(40 * 52 / 12 / 160, abs=0.01)             # 40 hours a week is about one person
