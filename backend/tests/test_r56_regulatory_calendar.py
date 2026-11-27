"""Regulatory deadline calendar (rules/regulatory_calendar.yaml): hard EU dates pinned to the target accounts they apply to, so sellers see
"this bank must ship X by date Y". Company names are invented."""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from test_r4_crm_notify import mk_user
from test_r29_buying_signals import company

from ri import regulatory_calendar as rc
from ri.api import today as td
from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.db import Database
from ri.scheduler import run_maintenance
from ri.signals.baseline import import_baseline
from ri.signals.persist import sync_rule_versions
from ri.sources.registry import seed_registry

SECRET = "r" * 40
NOW = datetime(2026, 11, 27, 10, 0, tzinfo=timezone.utc)
UPCOMING = {"ipr-non-euro-receive", "ipr-pi-emi", "ipr-non-euro-send", "amlr", "eudi-wallet"}


def _events(conn, **where):
    sql = "select * from events where event_type='deadline_approaching'"
    args = []
    for k, v in where.items():
        sql += f" and {k}=%s"
        args.append(v)
    return conn.execute(sql + " order by event_key", args).fetchall()


@pytest.fixture()
def orgs(conn):
    import_baseline(conn); sync_rule_versions(conn); seed_registry(conn)
    mk_user(conn, "bdm@x", "bdm", 555); mk_user(conn, "admin@x", "admin")
    o = {
        "pl_bank": company(conn, "Wisla Savings Bank", country="PL", segment="Banking", priority="P1", score=90),
        "de_bank": company(conn, "Rheinau Bank", country="DE", segment="Banking", priority="P2", score=70),
        "gb_bank": company(conn, "Thameside Bank", country="GB", segment="Banking", priority="P1", score=95),
        "pl_pay": company(conn, "Oderpay", country="PL", segment="Payments", priority="P3", score=40),
        "de_ins": company(conn, "Alsterhaus Versicherung", country="DE", segment="Insurance", priority="P2", score=60),
        "se_fintech": company(conn, "Fjordlend", country="SE", segment="Fintech"),
        "pl_out": company(conn, "Vistula Out Bank", country="PL", segment="Banking", eligibility="Out of scope"),
        "owned": company(conn, "Elbe Owned Bank", country="DE", segment="Banking", owner=999),
        "central": company(conn, "Musterland Central Bank", country="DE", segment="Banking"),           # on the list, but not who these rules are about
        "statutory": company(conn, "Musterland Krankenkasse", country="DE", segment="Insurance"),
    }
    conn.commit()
    return o


# ---------------------------------------------------------------- the calendar file

def test_the_calendar_loads_and_every_adopted_deadline_has_a_date_and_a_source():
    cal = rc.load()
    ids = [d.id for d in cal.deadlines]
    assert len(ids) == len(set(ids)) >= 8
    assert [d.date for d in cal.deadlines] == sorted(d.date for d in cal.deadlines)
    for d in cal.deadlines:
        assert d.certainty in rc.CERTAINTIES and d.title and d.must and d.applies_to and d.countries and d.kinds
        assert all(s.startswith("https://") for s in d.sources)
        if d.certainty == "adopted":
            assert isinstance(d.date, date) and d.sources, d.id
    by = {d.id: d for d in cal.deadlines}
    assert by["ipr-non-euro-receive"].date == date(2027, 1, 9) and by["ipr-non-euro-receive"].countries == {"PL", "SE", "DK", "CZ", "HU", "RO"}
    assert by["ipr-non-euro-send"].date == date(2027, 7, 9) and by["ipr-pi-emi"].date == date(2027, 4, 9)
    assert by["ipr-vop-euro"].date == date(2025, 10, 9) and by["amlr"].date == date(2027, 7, 10) and by["eudi-wallet"].date == date(2027, 12, 24)
    assert by["ai-act-high-risk"].certainty == "provisional" and "check whether it was adopted" in by["ai-act-high-risk"].note.lower()
    assert by["psd3-psr"].certainty == "possible" and by["psd3-psr"].approximate
    assert not ({"GB", "CH", "NO"} & set().union(*(d.countries for d in cal.deadlines)))      # EU law only


def test_a_broken_entry_is_an_error_not_skipped():
    base = {"groups": {"eu": ["DE"]}, "kinds": {"bank": {"label": "bank", "segments": ["Banking"]}}}
    ok = {"id": "x-rule", "title": "X", "regulation": "X Act", "date": "2027-01-01", "certainty": "adopted", "countries": "eu", "kinds": ["bank"],
          "applies_to": "Banks", "must": "do x", "sources": ["https://example.org/x"]}
    assert rc.parse({**base, "deadlines": [ok]}).deadlines[0].countries == {"DE"}
    for bad in ({"sources": []}, {"certainty": "rumoured"}, {"countries": ["Germany"]}, {"kinds": ["broker"]}, {"date": None}, {"sources": ["http://example.org"]}):
        with pytest.raises(ValueError):
            rc.parse({**base, "deadlines": [{**ok, **bad}]})
    with pytest.raises(ValueError):
        rc.parse({**base, "deadlines": [ok, ok]})                     # a repeated id


def test_who_a_deadline_applies_to_comes_from_the_country_and_the_kind_of_firm():
    cal = rc.load()

    def ids(country, segment, icp=None):
        k = rc.kind_of(cal, segment, icp)
        return {d.id for d in cal.deadlines if rc.applies(d, country, k)}

    pl_bank = ids("PL", "Banking")
    assert {"ipr-non-euro-receive", "ipr-non-euro-send", "amlr", "eudi-wallet"} <= pl_bank and "ipr-vop-euro" not in pl_bank
    de_bank = ids("DE", "banking ")                                    # compared without case or spaces
    assert "ipr-non-euro-receive" not in de_bank and "ipr-non-euro-send" not in de_bank and {"amlr", "eudi-wallet", "ipr-vop-euro"} <= de_bank
    assert ids("GB", "Banking") == set() and ids("CH", "Payments") == set()           # not EU: none of the EU deadlines
    assert "ipr-pi-emi" in ids("DE", "Payments") and "ipr-pi-emi" not in de_bank
    assert ids("DE", "Insurance") == {"amlr", "ai-act-high-risk"}
    assert ids("PL", "Fintech") == set()                               # the list does not say which fintechs hold a licence
    assert ids("PL", "Financial Software", "Large Bank") == pl_bank     # the lead list's "Large Bank" is a bank
    assert rc.why(cal, "bank", "PL") == ("bank in Poland", "bank w Polsce")
    # central banks, statutory health, pension and accident insurers and public bodies are left out by name
    assert cal.left_out("Musterland Central Bank") == "central bank" and cal.left_out("Banque de France") == "central bank"
    assert cal.left_out("National Bank of Greece") is None and cal.left_out("Wisla Savings Bank") is None
    assert cal.left_out("AOK Musterland") and cal.left_out("Musterland Betriebskrankenkasse") and cal.left_out("Allianz Lebensversicherung") is None


# ---------------------------------------------------------------- the daily lane

def test_the_lane_pins_upcoming_adopted_deadlines_to_target_accounts_and_is_idempotent(conn, orgs):
    out = rc.pin(conn, NOW)
    conn.commit()
    rows = _events(conn)
    by_org = {}
    for r in rows:
        by_org.setdefault(str(r["subject_org_id"]), set()).add(r["attrs"]["deadline_id"])
    assert by_org[orgs["pl_bank"]] == {"ipr-non-euro-receive", "ipr-non-euro-send", "amlr", "eudi-wallet"}
    assert by_org[orgs["de_bank"]] == {"amlr", "eudi-wallet"}
    assert by_org[orgs["pl_pay"]] == {"ipr-pi-emi", "ipr-non-euro-send", "amlr", "eudi-wallet"}
    assert by_org[orgs["de_ins"]] == {"amlr"}
    assert by_org[orgs["owned"]] == {"amlr", "eudi-wallet"}             # the lane works for everyone; pages follow each person's scope
    assert not {orgs["gb_bank"], orgs["se_fintech"], orgs["pl_out"]} & set(by_org)     # outside the EU, no known kind, not a target
    assert not {orgs["central"], orgs["statutory"]} & set(by_org)                       # a central bank, a statutory health insurer
    # past (verification of payee in the euro area), provisional (AI Act) and possible (PSD3) deadlines are never pinned
    assert {r["attrs"]["deadline_id"] for r in rows} == UPCOMING
    assert out == {"accounts_pinned": 5, "deadlines_pinned": 5, "events_written": 13, "events_ended": 0, "more_to_pin": False}

    ev = next(r for r in rows if r["event_key"] == f"deadline:ipr-non-euro-receive:{orgs['pl_bank']}")
    assert ev["event_at"] == datetime(2027, 1, 9, tzinfo=timezone.utc) and ev["event_at_known"]
    a = ev["attrs"]
    assert a["days_left"] == 43 and a["certainty"] == "adopted" and a["why"] == "bank in Poland" and a["why_pl"] == "bank w Polsce"
    assert a["source_url"].startswith("https://www.ecb.europa.eu/") and a["fit"]["level"] == "possible" and a["fit"]["reason"]
    assert ev["summary"] == "Wisla Savings Bank must be able to receive instant euro transfers by 9 January 2027, under the Instant Payments Regulation (IPR)."
    assert a["summary_pl"].startswith("Wisla Savings Bank: do 9 stycznia 2027 trzeba")
    # the normal path: checked against its structured claim (the source link), market-tagged, run against the signal rules
    assert ev["verification_status"] == "verified" and ev["proven"] and ev["lane"] == "private" and ev["market_country"] == "PL"
    claim = conn.execute("select c.* from claims c join event_claims ec on ec.claim_id=c.id where ec.event_id=%s", (ev["id"],)).fetchone()
    assert claim["claim_kind"] == "structured" and claim["object_json"]["url"] == a["source_url"]
    assert conn.execute("select count(*) n from signals").fetchone()["n"] == 0     # a deadline alone is context, not a buying signal (REG-01)
    from ri.events.fit import pending
    assert pending(conn, 100) == []                      # its own fit reason and Polish summary: the AI fit job never spends on it

    again = rc.pin(conn, NOW + timedelta(days=1))
    conn.commit()
    rows2 = _events(conn)
    assert [r["id"] for r in rows2] == [r["id"] for r in rows]          # same rows, no duplicates
    assert again["events_written"] == 0 and again["accounts_pinned"] == 5
    assert next(r for r in rows2 if r["id"] == ev["id"])["attrs"]["days_left"] == 42
    assert conn.execute("select count(*) n from claims where predicate='regulatory_deadline'").fetchone()["n"] == 5    # one per deadline


def test_an_account_that_stops_applying_or_a_deadline_that_passes_is_kept_and_marked_expired(conn, orgs):
    rc.pin(conn, NOW)
    total = len(_events(conn))
    conn.execute("update organizations set list_eligibility='Out of scope' where id=%s", (orgs["pl_pay"],))
    out = rc.pin(conn, NOW)
    assert out["events_ended"] == 4 and len(_events(conn)) == total       # never deleted
    gone = _events(conn, subject_org_id=orgs["pl_pay"])
    assert {r["lifecycle_status"] for r in gone} == {"expired"} and {r["attrs"]["ended"] for r in gone} == {"no_longer_applies"}
    # back on the list: the same rows come back
    conn.execute("update organizations set list_eligibility='Eligible' where id=%s", (orgs["pl_pay"],))
    rc.pin(conn, NOW)
    back = _events(conn, subject_org_id=orgs["pl_pay"])
    assert [r["id"] for r in back] == [r["id"] for r in gone] and {r["lifecycle_status"] for r in back} == {"active"}
    assert not any("ended" in r["attrs"] for r in back)
    # the day before a deadline it leaves the accounts (it would otherwise show as news once it has passed)
    late = rc.pin(conn, datetime(2027, 1, 8, 9, 0, tzinfo=timezone.utc))
    receive = _events(conn, event_key=f"deadline:ipr-non-euro-receive:{orgs['pl_bank']}")[0]
    assert receive["lifecycle_status"] == "expired" and receive["attrs"]["ended"] == "deadline_passed" and late["events_ended"] == 1
    assert len(_events(conn)) == total


def test_only_deadlines_within_18_months_are_pinned_and_a_run_stops_at_its_limit(conn, orgs):
    soon, far = NOW.date() + timedelta(days=200), NOW.date() + timedelta(days=600)
    raw = {"groups": {"eu": ["PL", "DE"]}, "kinds": {"bank": {"label": "bank", "segments": ["Banking"]}},
           "deadlines": [{"id": "soon-rule", "title": "Soon", "regulation": "Soon Act", "date": soon.isoformat(), "certainty": "adopted", "countries": "eu",
                          "kinds": ["bank"], "applies_to": "Banks", "must": "do it", "sources": ["https://example.org/soon"]},
                         {"id": "far-rule", "title": "Far", "regulation": "Far Act", "date": far.isoformat(), "certainty": "adopted", "countries": "eu",
                          "kinds": ["bank"], "applies_to": "Banks", "must": "do it", "sources": ["https://example.org/far"]}]}
    cal = rc.parse(raw)
    out = rc.pin(conn, NOW, cal=cal, limit=2)
    assert out["more_to_pin"] and out["events_written"] == 2                 # three banks (PL, DE, the other seller's DE bank), two per run here
    assert rc.pin(conn, NOW, cal=cal, limit=2) == {"accounts_pinned": 3, "deadlines_pinned": 1, "events_written": 1, "events_ended": 0, "more_to_pin": False}
    assert {r["attrs"]["deadline_id"] for r in _events(conn)} == {"soon-rule"}


def test_the_scheduler_runs_it_as_a_daily_lane_with_a_plain_result(conn, orgs):
    run_maintenance(conn, NOW)
    lane = conn.execute("select * from scheduler_lanes where lane='deadlines_pinned'").fetchone()
    assert lane["last_error"] is None and lane["last_result"]["accounts_pinned"] == 5 and lane["last_result"]["deadlines_pinned"] == 5
    run_maintenance(conn, NOW + timedelta(hours=2))                      # daily: not again two hours later
    assert conn.execute("select runs from scheduler_lanes where lane='deadlines_pinned'").fetchone()["runs"] == 1
    conn.execute("update scheduler_lanes set enabled=false where lane='deadlines_pinned'")      # a person can pause it
    run_maintenance(conn, NOW + timedelta(days=2))
    assert conn.execute("select runs from scheduler_lanes where lane='deadlines_pinned'").fetchone()["runs"] == 1


# ---------------------------------------------------------------- the pages

@pytest.fixture()
def api(conn, db_url, orgs):
    db = Database(db_url, max_size=3)
    app = create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET), db=db)
    hdr = lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"}  # noqa: E731
    yield {"c": TestClient(app), "h": hdr, "o": orgs}
    db.close()


def test_the_calendar_page_lists_every_deadline_with_our_accounts_in_the_persons_scope(api):
    c, h, o = api["c"], api["h"], api["o"]
    r = c.get("/api/regulatory-calendar", headers=h("admin"))
    assert r.status_code == 200, r.text
    body = r.json()
    items = {i["id"]: i for i in body["items"]}
    assert set(items) >= UPCOMING | {"ipr-vop-euro", "ai-act-high-risk", "psd3-psr"}          # every certainty
    assert items["ipr-vop-euro"]["past"] and not items["amlr"]["past"] and items["psd3-psr"]["approximate"]
    assert not items["ai-act-high-risk"]["pinned"] and not items["psd3-psr"]["pinned"] and not items["ipr-vop-euro"]["pinned"]
    assert items["ipr-non-euro-receive"]["accounts"]["count"] == 1
    assert [a["name"] for a in items["ipr-non-euro-receive"]["accounts"]["sample"]] == ["Wisla Savings Bank"]
    assert items["amlr"]["accounts"]["count"] == 5 and items["amlr"]["accounts"]["by_kind"] == {"bank": 3, "payments": 1, "insurer": 1}
    assert items["ipr-vop-euro"]["accounts"]["count"] == 2                  # euro-area banks: Rheinau and the other seller's Elbe bank
    assert body["counts"]["left_out"] == 2                                  # the central bank and the statutory health insurer
    assert items["amlr"]["say"].startswith("Banks, payment and e-money firms") and items["amlr"]["sources"][0].startswith("https://")
    # a seller sees only the accounts they may see (another seller's account is left out of the counts and names)
    mine = {i["id"]: i for i in c.get("/api/regulatory-calendar", headers=h("bdm")).json()["items"]}
    assert mine["amlr"]["accounts"]["count"] == 4 and "Elbe Owned Bank" not in [a["name"] for a in mine["amlr"]["accounts"]["sample"]]
    assert c.get("/api/regulatory-calendar").status_code in (401, 403)


def test_the_company_page_lists_the_deadlines_that_apply_to_it(api, conn):
    c, h, o = api["c"], api["h"], api["o"]
    rc.pin(conn, datetime.now(timezone.utc))
    conn.commit()
    r = c.get(f"/api/accounts/{o['pl_bank']}/deadlines", headers=h("bdm"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "bank" and body["country"] == "PL"
    got = {i["id"]: i for i in body["items"]}
    assert {"ipr-non-euro-receive", "ipr-non-euro-send", "amlr", "eudi-wallet", "ai-act-high-risk", "psd3-psr"} <= set(got)
    assert "ipr-vop-euro" not in got and got["amlr"]["why"] == "bank in Poland"
    assert not got["ai-act-high-risk"]["on_account"] and not got["psd3-psr"]["on_account"]
    assert [i["date"] for i in body["items"] if not i["past"]] == sorted(i["date"] for i in body["items"] if not i["past"])
    assert c.get(f"/api/accounts/{o['gb_bank']}/deadlines", headers=h("bdm")).json()["items"] == []
    central = c.get(f"/api/accounts/{o['central']}/deadlines", headers=h("bdm")).json()
    assert central["items"] == [] and central["left_out"] == "central bank"
    assert c.get(f"/api/accounts/{o['owned']}/deadlines", headers=h("bdm")).status_code == 404           # another seller's account
    assert c.get("/api/accounts/not-a-uuid/deadlines", headers=h("bdm")).status_code == 404


def test_today_shows_at_most_two_deadlines_a_day_and_the_findings_feed_shows_none(api, conn):
    c, h, o = api["c"], api["h"], api["o"]
    now = datetime.now(timezone.utc)
    raw = {"groups": {"eu": ["PL", "DE"]}, "kinds": {"bank": {"label": "bank", "segments": ["Banking"]}, "payments": {"label": "payment company", "segments": ["Payments"]},
                                                      "insurer": {"label": "insurer", "segments": ["Insurance"]}},
           "deadlines": [{"id": "test-rule", "title": "Test rule", "regulation": "Test Regulation", "date": (now.date() + timedelta(days=60)).isoformat(),
                          "certainty": "adopted", "countries": "eu", "kinds": ["bank", "payments", "insurer"], "applies_to": "Firms", "must": "build the test thing",
                          "sources": ["https://example.org/test"], "ask_for": "Head of payments"}]}
    rc.pin(conn, now, cal=rc.parse(raw))
    conn.commit()
    b = c.get("/api/today/brief", headers=h("bdm")).json()
    listed = [a for a in b["actions"] + b["queue"] if a["key"].startswith("deadline:")]
    assert len(listed) == td.DEADLINE_CAP == 2                            # four target accounts pinned, two shown
    assert [a["key"].startswith("deadline:") for a in b["actions"]].count(True) == td.DEADLINE_TODAY_CAP == 1    # one in "Do today", one up next
    first = listed[0]
    assert first["regulation"] and first["kind"] == "signal" and first["days_left"] == 60 and first["source"]["url"] == "https://example.org/test"
    assert first["organization"]["name"] == "Wisla Savings Bank"           # same date: the lead list's priority decides
    assert "build the test thing" in first["opener"] and first["who"] == "Head of payments"
    assert "Elbe Owned Bank" not in [(a["organization"] or {}).get("name") for a in listed]
    # marking one done does not bring a third one today
    assert c.post("/api/today/brief/items", headers=h("bdm"), json={"key": first["key"], "outcome": "done", "organization_id": first["organization"]["id"],
                                                                    "kind": "signal", "slot": "today"}).status_code == 200
    b2 = c.get("/api/today/brief", headers=h("bdm")).json()
    left = [a for a in b2["actions"] + b2["queue"] if a["key"].startswith("deadline:")]
    assert len(left) == 1 and left[0]["key"] != first["key"] and left[0] in b2["queue"]       # the one dealt with counts towards both caps
    # the findings feed is for news: pinned deadlines have their own page
    feed = c.get("/api/feed?view=accounts&accounts_only=false", headers=h("admin"))
    assert feed.status_code == 200 and not [i for i in feed.json()["items"] if i["type"] == "deadline_approaching"]
