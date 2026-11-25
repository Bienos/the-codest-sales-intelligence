"""Business-event rules, the account score and the Leads database. Synthetic data only."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.db import Database
from ri.market import region_for
from ri.scoring import band_for, freshness, recompute_scores, score_account
from test_r4_crm_notify import mk_user
from test_r7_verify_discovery_usage import SECRET

NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)


def sig(rule, strength=2, tier="D4", age=5, window=90):
    eff = NOW - timedelta(days=age)
    return {"rule_id": rule, "strength": strength, "demand_tier": tier, "effective_from": eff, "expires_at": eff + timedelta(days=window)}


def test_different_kinds_of_signal_beat_the_same_kind_repeated():
    one_kind = score_account([sig("COM-01"), sig("COM-02"), sig("COM-06")], stopped=False, downgraded=False, now=NOW)
    mixed = score_account([sig("COM-01"), sig("SRC-01", 3, "D1", 2, 30), sig("MAN-01")], stopped=False, downgraded=False, now=NOW)
    assert one_kind["groups"] == ["COM"] and mixed["groups"] == ["COM", "MAN", "SRC"]
    assert mixed["bonus"] == 1.5 and one_kind["bonus"] == 1.0
    assert mixed["score"] > one_kind["score"]


def test_a_rule_that_fires_three_times_counts_once_and_old_signals_fade():
    thrice = score_account([sig("COM-06"), sig("COM-06", age=40), sig("COM-06", age=70)], stopped=False, downgraded=False, now=NOW)
    once = score_account([sig("COM-06")], stopped=False, downgraded=False, now=NOW)
    assert thrice["signal_count"] == 1 and thrice["score"] == once["score"]
    assert freshness(NOW - timedelta(days=89), NOW + timedelta(days=1), NOW) == 0.25 and freshness(NOW, NOW + timedelta(days=90), NOW) == 1.0


def test_stop_rules_end_the_story_and_downgrades_halve_it():
    base = [sig("SRC-01", 3, "D1", 1, 30), sig("COM-02")]
    assert score_account(base, stopped=True, downgraded=False, now=NOW)["band"] == "none"
    full, half = score_account(base, stopped=False, downgraded=False, now=NOW), score_account(base, stopped=False, downgraded=True, now=NOW)
    assert half["score"] == pytest.approx(full["score"] / 2, abs=0.1)
    assert band_for(9.5, False) == "hot" and band_for(4, False) == "warm" and band_for(1, False) == "watch" and band_for(0, False) == "none"


def test_regions_group_the_main_markets():
    assert [region_for(c) for c in ("GB", "DE", "CH", "SE", "PL", "ES", "US", "JP", None)] == ["uk", "dach", "dach", "nordics", "poland", "europe", "us", "other", "unknown"]


def _org(conn, name, country, segment, employees, domain=None, master=True):
    return str(conn.execute("insert into organizations(display_name, country_code, segment, employees_estimate, canonical_domain, in_master_list) values (%s,%s,%s,%s,%s,%s) returning id",
                            (name, country, segment, employees, domain, master)).fetchone()["id"])


def _signal(conn, org, rule, strength=2, days=5):
    conn.execute("insert into events(event_key,event_type,subject_org_id,summary,extraction_model_version,verification_status) values (%s,'funding_announced',%s,'s','t','verified')", (f"k-{org}-{rule}", org))
    eid = conn.execute("select id from events where event_key=%s", (f"k-{org}-{rule}",)).fetchone()["id"]
    conn.execute("insert into signals(event_id, rule_id, rule_version, organization_id, strength, demand_tier, verification_status, effective_from, expires_at, reason_code) "
                 "values (%s,%s,'1.0.0',%s,%s,'D4','verified', now() - make_interval(days => %s), now() + interval '60 days', 'r')", (eid, rule, org, strength, days))


@pytest.fixture()
def world(conn, db_url):
    mk_user(conn, "bdm@x", "bdm", 555)
    a = _org(conn, "Synthetic London Bank plc", "GB", "Bank", 9000, "london-bank.example")
    b = _org(conn, "Synthetic Berlin Scale-up GmbH", "DE", "Fintech", 700, "berlin.example")
    c = _org(conn, "Synthetic Oslo Payments AS", "NO", "Fintech", 300, "oslo.example", master=False)
    d = _org(conn, "Synthetic Warsaw Bank SA", "PL", "Bank", 6000, "warsaw.example")
    _signal(conn, a, "COM-02", 2); _signal(conn, a, "SRC-01", 3); _signal(conn, a, "COM-06", 2)
    _signal(conn, b, "COM-01", 1)
    conn.execute("insert into new_company_candidates(organization_id, icp_note) values (%s,'Added by bdm@x. Not yet qualified.')", (c,))
    conn.execute("insert into negative_findings(rule_id, rule_version, scope_type, scope_id, kind, effect) values ('NEG-05','1.0.0','account',%s,'route_block','stop')", (d,))
    _signal(conn, d, "SRC-01", 3)
    conn.commit()
    out = recompute_scores(conn, datetime.now(timezone.utc)); conn.commit()
    db = Database(db_url, max_size=4)
    app = create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET, session_secret="s" * 32), db=db)
    yield TestClient(app), (lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"}), {"a": a, "b": b, "c": c, "d": d}, out
    db.close()


def test_scores_are_stored_per_account_and_stop_rules_zero_them(world, conn):
    c, h, ids, out = world
    rows = {str(r["organization_id"]): r for r in conn.execute("select * from account_scores").fetchall()}
    assert out["scored"] == 3 and rows[ids["a"]]["band"] in ("hot", "warm") and rows[ids["a"]]["groups"] == ["COM", "SRC"]
    assert rows[ids["d"]]["stopped"] is True and rows[ids["d"]]["band"] == "none"
    assert ids["c"] not in rows


def test_leads_are_filterable_by_region_icp_band_and_origin_and_sorted_by_score(world):
    c, h, ids, _ = world
    j = c.get("/api/leads", headers=h("bdm")).json()
    assert j["total"] == 4 and j["items"][0]["name"] == "Synthetic London Bank plc"
    assert j["facets"]["region"] == {"uk": 1, "dach": 1, "nordics": 1, "poland": 1} and j["facets"]["origin"]["added"] == 1 and j["facets"]["origin"]["list"] == 3
    assert [i["name"] for i in c.get("/api/leads?region=dach", headers=h("bdm")).json()["items"]] == ["Synthetic Berlin Scale-up GmbH"]
    assert [i["name"] for i in c.get("/api/leads?origin=added", headers=h("bdm")).json()["items"]] == ["Synthetic Oslo Payments AS"]
    assert c.get("/api/leads?icp=strategic_5000_plus", headers=h("bdm")).json()["total"] == 2
    assert [i["name"] for i in c.get("/api/leads?band=none&signal_days=30", headers=h("bdm")).json()["items"]] == ["Synthetic Warsaw Bank SA"]
    assert c.get("/api/leads?q=oslo", headers=h("bdm")).json()["total"] == 1
    assert c.get("/api/leads?sort=name", headers=h("bdm")).json()["items"][0]["name"] == "Synthetic Berlin Scale-up GmbH"


def test_csv_export_is_audited_and_neutralises_spreadsheet_formulas(world, conn):
    c, h, ids, _ = world
    _org(conn, "=HYPERLINK(\"http://evil.invalid\")", "GB", "Bank", 100)
    conn.commit()
    r = c.get("/api/leads.csv", headers=h("bdm"))
    assert r.status_code == 200 and r.text.startswith("Company,Website") and "'=HYPERLINK" in r.text and "\n=HYPERLINK" not in r.text
    assert conn.execute("select count(*) c from audit_events where operation='leads_exported'").fetchone()["c"] == 1


def test_bulk_scan_queues_selected_accounts_and_respects_the_daily_limit(world, conn):
    c, h, ids, _ = world
    r = c.post("/api/leads/scan", headers=h("bdm"), json={"ids": [ids["a"], ids["b"], "00000000-0000-0000-0000-000000000000"]})
    assert r.json()["queued"] == 2
    assert conn.execute("select count(*) c from jobs where kind='account_scan'").fetchone()["c"] == 2
    for _ in range(59):
        conn.execute("insert into audit_events(actor, operation, record_type) values ('bdm@x','account_scan_requested','organization')")
    conn.commit()
    assert c.post("/api/leads/scan", headers=h("bdm"), json={"ids": [ids["a"], ids["b"]]}).status_code == 429


def test_watching_an_account_schedules_a_weekly_scan_once_and_shows_in_leads(world, conn):
    from ri.scheduler import schedule_watchlist
    c, h, ids, _ = world
    assert c.post(f"/api/accounts/{ids['a']}/watch", headers=h("bdm")).json() == {"watched": True}
    assert c.post("/api/accounts/00000000-0000-0000-0000-000000000000/watch", headers=h("bdm")).status_code == 404
    items = {i["name"]: i["watched"] for i in c.get("/api/leads", headers=h("bdm")).json()["items"]}
    assert items["Synthetic London Bank plc"] is True and items["Synthetic Berlin Scale-up GmbH"] is False
    assert [i["name"] for i in c.get("/api/leads?watched=true", headers=h("bdm")).json()["items"]] == ["Synthetic London Bank plc"]
    assert schedule_watchlist(conn) == 1 and schedule_watchlist(conn) == 0           # idempotent within the week
    conn.execute("insert into account_endpoints(organization_id, kind, url, last_scanned_at) values (%s,'feed','https://x.invalid/f', now())", (ids["a"],))
    conn.commit()
    conn.execute("delete from jobs where kind='account_scan'"); conn.commit()
    assert schedule_watchlist(conn) == 0                                              # scanned within the last 7 days: nothing to do
    assert c.delete(f"/api/accounts/{ids['a']}/watch", headers=h("bdm")).json() == {"watched": False}
    assert c.get("/api/leads?watched=true", headers=h("bdm")).json()["total"] == 0


def test_a_person_can_qualify_or_reject_a_candidate_and_only_candidates(world, conn):
    c, h, ids, _ = world
    r = c.post(f"/api/leads/{ids['c']}/qualify", headers=h("bdm"), json={"decision": "qualify", "note": "met them"})
    assert r.json() == {"status": "qualified"}
    items = {i["name"]: i["candidate_status"] for i in c.get("/api/leads", headers=h("bdm")).json()["items"]}
    assert items["Synthetic Oslo Payments AS"] == "qualified"
    assert c.post(f"/api/leads/{ids['a']}/qualify", headers=h("bdm"), json={"decision": "reject"}).status_code == 404     # a list account is not a candidate
    assert c.post(f"/api/leads/{ids['c']}/qualify", headers=h("bdm"), json={"decision": "maybe"}).status_code == 422
    assert c.post(f"/api/leads/{ids['c']}/qualify", headers=h("bdm"), json={"decision": "reject"}).json() == {"status": "rejected"}
    assert "Synthetic Oslo Payments AS" not in [i["name"] for i in c.get("/api/leads", headers=h("bdm")).json()["items"]]
    assert conn.execute("select count(*) c from audit_events where operation in ('candidate_qualified','candidate_rejected')").fetchone()["c"] == 2


def test_today_lists_hot_and_warm_accounts_but_never_stopped_ones(world):
    c, h, ids, _ = world
    names = [a["display_name"] for a in c.get("/api/today", headers=h("bdm")).json()["hot_accounts"]]
    assert "Synthetic London Bank plc" in names and "Synthetic Warsaw Bank SA" not in names
