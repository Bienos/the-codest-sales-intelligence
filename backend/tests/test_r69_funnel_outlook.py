"""Funnel outlook: arrivals per stage in the next 90 days, stage-to-stage chances (MQL to SQL), measured rates with an interval, and the
monthly actuals the prediction is checked against."""
from datetime import datetime, timedelta, timezone

import pytest

from ri.config import Settings
from ri.forecast import outlook, service
from test_r64_forecast import NOW, add_history, history  # noqa: F401  (fixture)


def test_wilson_interval_narrows_with_evidence():
    lo6, hi6 = outlook.wilson(3, 6)
    lo600, hi600 = outlook.wilson(300, 600)
    assert lo6 < 0.5 < hi6 and lo600 < 0.5 < hi600 and (hi6 - lo6) > 5 * (hi600 - lo600)
    assert outlook.wilson(0, 0) is None


def test_reach_multiplies_rates_and_adds_days():
    r = outlook.reach([0.5, 0.4, 0.5], [10, 20, 30])
    assert r[0][0] is None and r[0][1] == {"p": 0.5, "days": 10}
    assert r[0][2]["p"] == pytest.approx(0.2) and r[0][2]["days"] == 30
    assert r[1][3]["p"] == pytest.approx(0.2) and r[1][3]["days"] == 50          # SQL to won


def test_projection_counts_arrivals_not_deals_already_there():
    sure = outlook.project([1.0, 1.0, 1.0], [5, 5, 5], open_stage=[0, 0, 1], open_age=[0, 0, 0], streams=[], runs=200)
    first, second, third, won = (s["total"]["mean"] for s in sure["stages"])
    assert first == 0                     # the two deals in stage 1 are already there
    assert second == 2 and third == 3 and won == 3
    dead = outlook.project([0.0, 1.0, 1.0], [5, 5, 5], open_stage=[0, 0], open_age=[0, 0], streams=[], runs=200)
    assert all(s["total"]["mean"] == 0 for s in dead["stages"])


def test_new_deals_arrive_at_their_entry_stage_at_the_expected_pace():
    p = outlook.project([0.5, 0.5], [10, 10], open_stage=[], open_age=[], streams=[(10.0, 0)], runs=3000)
    mql = p["stages"][0]["total"]["mean"]
    assert mql == pytest.approx(10 * 90 / 30.4, rel=0.05)
    assert p["stages"][1]["total"]["mean"] < mql * 0.5 + 0.5
    w = [x["mean"] for x in p["stages"][0]["by_window"]]
    assert all(x == pytest.approx(mql / 3, rel=0.12) for x in w)


def test_default_pair_finds_mql_and_sql():
    assert outlook.default_pair(["Webform MQL", "DISCOVERY", "SQL", "Proposal"]) == (0, 2)
    assert outlook.default_pair(["Lead", "Qualified", "Proposal", "Contract"]) == (0, 2)


def test_build_adds_the_outlook_with_actuals(conn, history):  # noqa: F811
    p = service.build(conn, Settings(pipedrive_pipeline_id=2, pipedrive_stage_id=7), now=NOW, use_cache=False)
    o = p["outlook"]
    assert "error" not in o
    assert [s["name"] for s in o["stages"]] == ["Webform MQL", "DISCOVERY", "SQL", "Proposal/Shortlist", "Commercials/NDA"]
    assert o["pair"] == {"from": 0, "to": 2} and o["reach"][0][2]["p"] == pytest.approx(o["stages"][0]["conv"] * o["stages"][1]["conv"])
    mql = o["stages"][0]
    assert mql["decided"] == 3 and mql["interval"][0] < mql["measured"] < mql["interval"][1]
    assert len(o["actuals"]["months"]) == 12 and o["actuals"]["months"][-1] == "2026-11-01"
    assert len(o["actuals"]["counts"]) == 6                            # five stages and won
    aug = o["actuals"]["months"].index("2026-08-01")
    assert o["actuals"]["counts"][0][aug] == 3 and sum(o["actuals"]["counts"][0]) == 3      # deals 1, 2 and 4 entered MQL 100 days before NOW
    assert sum(o["actuals"]["counts"][1]) == 3                                               # Discovery: deals 1, 2 and 3
    assert o["stages"][2]["arrivals"]["total"]["p90"] >= o["stages"][2]["arrivals"]["total"]["p10"]
