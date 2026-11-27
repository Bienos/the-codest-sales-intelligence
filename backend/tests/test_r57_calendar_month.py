"""The Calendar page's month view (GET /api/calendar/events, src/ri/calendar_events.py): regulatory deadlines and the closing dates of open
tenders from ICP buyers, day by day, for the range the page shows; each layer with the permission and row scope of its own page; at most a
few items a day, with how many more. Company and buyer names are invented."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from test_r56_regulatory_calendar import api, orgs  # noqa: F401  (fixtures)

from ri import calendar_events as ce

NOW = datetime.now(timezone.utc)
TODAY = NOW.date()


def notice(conn, nid, buyer, *, days, status="open", org=None, tz="+01:00", cpv=("72000000",), value=None):
    """An open TED notice for IT work whose deadline is `days` days from now, at midnight of that day in the notice's own time zone, with an
    estimated value in euros when `value` is given."""
    day = TODAY + timedelta(days=days)
    deadline = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - timedelta(hours=1)        # "<day>+01:00", as TED gives it
    return str(conn.execute(
        "insert into procurement_notices(source_id, notice_id, buyer_name, title, status, deadline_at, deadline_tz, official_url, country, service_codes, "
        "source_detail, published_at, last_status_check_at, buyer_org_id) values ('procurement-ted',%s,%s,%s,%s,%s,%s,%s,'DEU',%s,%s,%s,now(),%s) returning id",
        (nid, buyer, f"Deutschland – IT-Dienste – Projekt {nid}", status, deadline, tz, f"https://ted.europa.eu/en/notice/-/detail/{nid}", list(cpv),
         json.dumps({"value": value, "currency": "EUR"}) if value else None, NOW - timedelta(days=3), org)).fetchone()["id"])


def get(c, who, h, start, end, **qs):
    q = "&".join(f"{k}={v}" for k, v in qs.items())
    return c.get(f"/api/calendar/events?from={start}&to={end}{'&' + q if q else ''}", headers=h(who))


def tender_ids(body):
    return {i["id"] for d in body["days"] for i in d["items"] if i["layer"] == "tenders"}


def test_a_tender_closes_on_the_day_its_notice_gives():
    late = datetime(2026, 12, 2, 23, 0, tzinfo=timezone.utc)
    assert ce.notice_day(late, "+01:00") == date(2026, 12, 3)            # TED: "2026-12-03+01:00" is a 3 December deadline
    assert ce.notice_day(late, "CET") == date(2026, 12, 3)
    assert ce.notice_day(late, "+00:00") == ce.notice_day(late, None) == ce.notice_day(late, "not a zone") == date(2026, 12, 2)
    assert ce.notice_day(late, "-05:00") == date(2026, 12, 2)


def test_a_range_holds_only_its_own_days_in_both_layers(api, conn):
    c, h, o = api["c"], api["h"], api["o"]
    near = notice(conn, "N-NEAR", "Kreissparkasse Musterstadt", days=5)
    linked = notice(conn, "N-LINKED", "Wisla Savings Bank", days=6, org=o["pl_bank"], value=250000)
    notice(conn, "N-LATER", "Kreissparkasse Spätberg", days=40)                       # after the range
    notice(conn, "N-PUBLIC", "Stadt Beispiel", days=5)                                # a public body: not our ICP
    notice(conn, "N-CANCELLED", "Volksbank Musterland eG", days=5, status="cancelled")
    notice(conn, "N-SERVICES", "Kreissparkasse Bauhof", days=5, cpv=("45000000",))    # building work, not software
    conn.commit()
    start, end = TODAY, TODAY + timedelta(days=20)
    r = get(c, "admin", h, start, end)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["layers"] == ["regulatory", "tenders"] and body["unavailable"] == [] and body["from"] == start.isoformat()
    assert tender_ids(body) == {near, linked}
    assert all(start.isoformat() <= d["date"] <= end.isoformat() for d in body["days"])
    day = next(d for d in body["days"] if any(i["id"] == linked for i in d["items"]))
    assert day["date"] == (TODAY + timedelta(days=6)).isoformat()                    # the notice's own day, not the UTC one
    item = next(i for i in day["items"] if i["id"] == linked)
    assert item["account"] == {"id": o["pl_bank"], "name": "Wisla Savings Bank"} and item["group"] == "banks"
    assert item["url"].startswith("https://ted.europa.eu/") and item["value_eur"] == 250000 and item["title"] == "Projekt N-LINKED"      # the buyer's own title
    assert body["totals"]["tenders"] == 2 and sum(m.get("tenders", 0) for m in body["months"].values()) == 2
    # the regulatory layer: a deadline on its own day, with how many of our target accounts it applies to
    jan = get(c, "admin", h, "2027-01-01", "2027-01-31", layers="regulatory").json()
    assert jan["layers"] == ["regulatory"] and [d["date"] for d in jan["days"]] == ["2027-01-09"]
    dl = jan["days"][0]["items"][0]
    assert dl["id"] == "ipr-non-euro-receive" and dl["layer"] == "regulatory" and dl["certainty"] == "adopted" and dl["accounts"]["count"] == 1
    assert dl["sources"][0].startswith("https://") and jan["undated"] == [] and jan["months"] == {"2027-01": {"regulatory": 1}}
    # a deadline whose day the law does not fix yet is never put on a day
    year = get(c, "admin", h, "2028-01-01", "2028-12-31", layers="regulatory", per_day=0).json()
    assert [u["id"] for u in year["undated"]] == ["psd3-psr"]
    assert "psd3-psr" not in {i["id"] for d in year["days"] for i in d["items"]}
    # only the asked layer
    only = get(c, "admin", h, start, end, layers="tenders").json()
    assert only["layers"] == ["tenders"] and "regulatory" not in only["totals"] and tender_ids(only) == {near, linked}


def test_the_range_and_layers_are_checked(api):
    c, h = api["c"], api["h"]
    assert get(c, "admin", h, "2027-02-01", "2027-01-01").status_code == 422             # ends before it starts
    assert get(c, "admin", h, "2027-01-01", "2028-03-01").status_code == 422             # more than 400 days
    assert get(c, "admin", h, "2027-01-01", "2027-01-31", layers="regulatory,gossip").status_code == 422
    assert get(c, "admin", h, "2027-01-01", "2027-01-31", per_day=51).status_code == 422
    assert get(c, "admin", h, "2027-13-01", "2027-01-31").status_code == 422
    assert c.get("/api/calendar/events?from=2027-01-01&to=2027-01-31").status_code in (401, 403)


def test_each_layer_follows_the_scope_of_its_own_page(api, conn):
    """A seller does not see a tender linked to another seller's account (the Open tenders page hides it too), nor that account among the
    accounts a deadline applies to."""
    c, h, o = api["c"], api["h"], api["o"]
    mine = notice(conn, "N-MINE", "Wisla Savings Bank", days=4, org=o["pl_bank"])
    theirs = notice(conn, "N-THEIRS", "Elbe Owned Bank", days=4, org=o["owned"])
    free = notice(conn, "N-FREE", "Kreissparkasse Musterstadt", days=4)
    conn.commit()
    start, end = TODAY, TODAY + timedelta(days=10)
    assert tender_ids(get(c, "admin", h, start, end).json()) == {mine, theirs, free}
    seller = get(c, "bdm", h, start, end).json()
    assert tender_ids(seller) == {mine, free}
    page = c.get("/api/tenders?group=all&min_days=0&limit=100", headers=h("bdm")).json()      # the tenders page, same person
    assert {i["id"] for i in page["items"]} == {mine, free}
    # the deadlines: the other seller's bank is not counted nor named for the seller
    july = {i["id"]: i for d in get(c, "bdm", h, "2027-07-01", "2027-07-31", layers="regulatory").json()["days"] for i in d["items"]}
    every = {i["id"]: i for d in get(c, "admin", h, "2027-07-01", "2027-07-31", layers="regulatory").json()["days"] for i in d["items"]}
    assert every["amlr"]["accounts"]["count"] == 5 and july["amlr"]["accounts"]["count"] == 4
    assert "Elbe Owned Bank" in [a["name"] for a in every["amlr"]["accounts"]["sample"]]
    assert "Elbe Owned Bank" not in [a["name"] for a in july["amlr"]["accounts"]["sample"]]


def test_a_day_shows_a_few_items_and_says_how_many_more(api, conn):
    c, h = api["c"], api["h"]
    insurer = notice(conn, "N-INS", "Versicherungskammer Musterland", days=9, value=9_000_000)
    banks = [notice(conn, f"N-B{i}", f"Kreissparkasse Ort{i}", days=9, value=100_000 * (i + 1)) for i in range(6)]
    conn.commit()
    the_day = (TODAY + timedelta(days=9)).isoformat()
    month = get(c, "admin", h, TODAY, TODAY + timedelta(days=30), layers="tenders").json()
    day = next(d for d in month["days"] if d["date"] == the_day)
    assert day["total"] == 7 and day["counts"] == {"tenders": 7} and len(day["items"]) == ce.PER_DAY == 4 and day["more"] == 3
    assert [i["id"] for i in day["items"]] == banks[::-1][:4]           # banks first (the biggest value first), the insurer after them
    full = get(c, "admin", h, the_day, the_day, per_day=50).json()["days"][0]
    assert len(full["items"]) == 7 and full["more"] == 0 and full["items"][-1]["id"] == insurer
    counts = get(c, "admin", h, TODAY, TODAY + timedelta(days=30), per_day=0, layers="tenders").json()
    assert next(d for d in counts["days"] if d["date"] == the_day) == {"date": the_day, "counts": {"tenders": 7}, "total": 7, "items": [], "more": 7}


def test_people_without_either_page_get_nothing(api, conn):
    from test_r4_crm_notify import mk_user
    mk_user(conn, "marketing@x", "marketing")
    conn.commit()
    c, h = api["c"], api["h"]
    assert get(c, "marketing", h, "2027-01-01", "2027-01-31").status_code == 403


def test_each_deadline_has_a_short_label_for_the_month_grid(api):
    """The grid shows a deadline as a small pill ("IPR: send + VoP"): every deadline carries one in both languages, on both endpoints."""
    from ri import regulatory_calendar as rc
    c, h = api["c"], api["h"]
    dl = get(c, "admin", h, "2027-07-01", "2027-07-31", layers="regulatory").json()["days"][0]["items"][0]
    assert dl["id"] == "ipr-non-euro-send" and dl["short"] == "IPR: send + VoP" and dl["short_pl"] == "IPR: wysyłka + weryfikacja"
    every = c.get("/api/regulatory-calendar", headers=h("admin")).json()["items"]
    assert len(every) >= 8 and all(0 < len(i["short"]) <= rc.SHORT_MAX and 0 < len(i["short_pl"]) <= rc.SHORT_MAX for i in every)
    # an entry without its own label gets the regulation's abbreviation and its title; a label that is too long is an error
    base = {"groups": {"eu": ["DE"]}, "kinds": {"bank": {"label": "bank", "segments": ["Banking"]}}}
    ok = {"id": "x-rule", "title": "Pay faster", "title_pl": "Płać szybciej", "regulation": "Faster Payments Act (FPA)", "date": "2027-01-01",
          "certainty": "adopted", "countries": "eu", "kinds": ["bank"], "applies_to": "Banks", "must": "do x", "sources": ["https://example.org/x"]}
    assert rc.short_label(rc.parse({**base, "deadlines": [ok]}).deadlines[0]) == ("FPA: Pay faster", "FPA: Płać szybciej")
    assert rc.short_label(rc.parse({**base, "deadlines": [{**ok, "regulation": "Faster Payments Act"}]}).deadlines[0]) == ("Pay faster", "Płać szybciej")
    with pytest.raises(ValueError, match="x-rule"):
        rc.parse({**base, "deadlines": [{**ok, "short": "A label far too long for one small pill in a grid"}]})
