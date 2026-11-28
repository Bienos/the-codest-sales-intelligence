"""Leading indicators by week and pipeline movement between two dates (forecast/movement.py, Forecast tabs). Names are invented."""
from datetime import date, timedelta

import pytest

from ri.forecast import movement as mv
from ri.forecast.history import load_funnel
from test_r64_forecast import NOW, add_history, api, seed_stages  # noqa: F401  (api is a fixture)

T0 = NOW - timedelta(days=100)          # add_history's day 0
START = T0 + timedelta(days=70)         # the period: the last 30 days


def funnel(conn):
    return load_funnel(conn, 2, ["Marketing Nurturing"], ["Active Delivery"])


def one(v, cur):
    return float(v)


# ------------------------------------------------------------------ pipeline movement
@pytest.fixture()
def moves(conn):
    seed_stages(conn)
    add_history(conn, 1, [(12, 10), (13, 80)], "open", value=100000)        # in SQL at the start, Proposal now
    add_history(conn, 2, [(10, 0), (7, 70)], "won", value=50000)            # open at the start, won on day 75
    add_history(conn, 3, [(10, 0), (12, 72)], "lost", value=20000)          # open at the start, lost on day 77
    add_history(conn, 4, [(10, 85)], "open", value=30000)                   # new in the period, still open
    add_history(conn, 5, [(12, 10), (11, 80)], "open", value=40000)         # parked in Marketing Nurturing
    add_history(conn, 6, [(7, 85)], "won", value=10000)                     # new and won inside the period
    add_history(conn, 7, [(12, 5)], "lost", value=99000)                    # lost long before: not in the period at all
    conn.execute("update crm_deals set value=150000, expected_close_date=%s where crm_id=1", (date(2027, 3, 31),))   # raised since
    return funnel(conn)


def test_waterfall_balances_and_names_every_movement(conn, moves):
    m = mv.movement(conn, moves, one, START, now=NOW)
    s = {k: (v["n"], v["value"]) for k, v in m["steps"].items()}
    assert s["start"] == (4, 210000.0)                                       # deals 1, 2, 3, 5
    assert s["added"] == (2, 40000.0) and s["revived"] == (0, 0.0)           # deals 4 and 6
    assert s["won"] == (2, 60000.0) and s["lost"] == (1, 20000.0) and s["parked"] == (1, 40000.0)
    assert s["value_up"] == (1, 50000.0) and s["value_down"] == (0, 0.0)
    assert s["end"] == (2, 180000.0)                                         # deals 1 (now 150k) and 4
    assert m["balance_gap"] == 0
    assert m["stage_moves"] == {"forward": 1, "back": 0, "same": 0}
    assert m["steps"]["parked"]["deals"][0]["stage_before"] == "SQL"
    assert m["close_dates"]["measured"] is False                             # no snapshot on the start day


def test_close_dates_moved_need_a_snapshot_at_the_start(conn, moves):
    conn.execute("insert into deal_snapshots_daily(day, crm_deal_id, pipeline_id, stage_id, status, value, currency, expected_close_date) "
                 "values (%s, 1, 2, 12, 'open', 100000, 'EUR', %s)", (START.date(), date(2026, 12, 31)))
    m = mv.movement(conn, moves, one, START, now=NOW)
    assert m["close_dates"] == {"pushed": 1, "pulled": 0, "same": 0, "measured": True}
    assert m["steps"]["start"]["value"] == 210000.0 and m["balance_gap"] == 0


def test_a_deal_back_from_parking_is_revived_not_new(conn):
    seed_stages(conn)
    add_history(conn, 8, [(12, 10), (11, 40), (13, 90)], "open", value=25000)   # parked before the period, back in Proposal on day 90
    m = mv.movement(conn, funnel(conn), one, START, now=NOW)
    assert (m["steps"]["revived"]["n"], m["steps"]["added"]["n"], m["steps"]["end"]["n"]) == (1, 0, 1) and m["balance_gap"] == 0


# ------------------------------------------------------------------ leading indicators
def ev(conn, typ, email, at):
    conn.execute("insert into lemlist_activities(id, type, email, campaign_id, happened_at) values (%s,%s,%s,'c1',%s)", (f"{typ}-{email}-{at}", typ, email, at))


def test_indicators_count_first_events_per_week_against_goals(conn):
    seed_stages(conn)
    week = NOW - timedelta(days=NOW.weekday())                                # Monday of NOW's week (NOW is a Friday)
    for i in range(6):
        ev(conn, "emailsSent", f"p{i}@x.example", week + timedelta(hours=1))
    ev(conn, "emailsSent", "p0@x.example", week + timedelta(days=2))         # second email: still one person contacted
    ev(conn, "emailsSent", "old@x.example", week - timedelta(days=7))
    ev(conn, "emailsReplied", "p1@x.example", week + timedelta(days=1))
    conn.execute("insert into crm_activities(crm_id, type, subject, done, done_at) values (1,'meeting','Intro',true,%s), (2,'call','x',true,%s), "
                 "(3,'meeting','later',false,null)", (week + timedelta(days=1), week + timedelta(days=2)))
    add_history(conn, 1, [(10, 0), (12, 97)], "open")                        # reached SQL this week (day 97 = Tuesday)
    add_history(conn, 2, [(12, 50)], "open")                                 # reached SQL 50 days ago: in the 12 weeks, not this week
    r = mv.indicators(conn, funnel(conn), {"contacted": 10, "replied": 1}, now=NOW)
    by = {x["key"]: x for x in r["rows"]}
    assert len(r["week_starts"]) == mv.WEEKS and r["week_starts"][-1] == week.date().isoformat() and r["days_into_week"] == 5
    assert by["contacted"]["this_week"] == 6 and by["contacted"]["last_week"] == 1
    assert by["contacted"]["pace"] == pytest.approx(6 * 7 / 5, abs=0.1) and by["contacted"]["status"] == "behind"    # 8.4 a week against 10
    assert by["replied"]["status"] == "on_pace"                                                                        # 1.4 a week against 1
    assert by["replied"]["this_week"] == 1 and by["meetings"]["this_week"] == 1 and by["calls"]["this_week"] == 1
    assert by["sql"]["this_week"] == 1 and by["sql"]["total"] == 2 and r["stages"]["sql"] == "SQL"
    assert by["won"]["goal"] is None and by["won"]["status"] is None


def test_indicator_api_and_weekly_goals_are_for_managers(api):
    c, h = api
    assert c.get("/api/forecast/indicators", headers=h("bdm")).status_code == 403
    assert c.get("/api/forecast/movement", headers=h("bdm")).status_code == 403
    r = c.get("/api/forecast/indicators", headers=h("mgr"))
    assert r.status_code == 200 and len(r.json()["rows"]) == len(mv.INDICATORS)
    assert c.get("/api/forecast/movement?days=30", headers=h("mgr")).json()["days"] == 30
    assert c.put("/api/forecast/settings", json={"weekly_goals": {"nonsense": 3}}, headers=h("mgr")).status_code == 422
    assert c.put("/api/forecast/settings", json={"weekly_goals": {"replied": 5, "won": 0}}, headers=h("mgr")).status_code == 200
    got = {x["key"]: x["goal"] for x in c.get("/api/forecast/indicators", headers=h("mgr")).json()["rows"]}
    assert got["replied"] == 5 and got["won"] is None                        # 0 clears a goal
