"""Renewal radar: award notices to expected end dates, with stated and assumed terms kept apart."""
from datetime import date

import pytest

from ri import renewals as rn


def _notice(**kw):
    base = {"publication-number": "100-2026", "notice-title": {"eng": "Poland – IT services – Development of a case management system"}, "buyer-name": {"pol": ["Urząd Testowy"]},
            "buyer-country": ["POL"], "publication-date": "2023-03-10+01:00", "dispatch-date": "2023-03-08+01:00", "classification-cpv": ["72262000", "72000000"],
            "winner-name": {"eng": ["Acme Systems Ltd", "Acme Systems Ltd"], "pol": ["Acme Systems sp. z o.o."]}, "winner-country": ["GBR"], "winner-size": ["sme"],
            "duration-period-value-lot": ["24", "48"], "duration-period-unit-lot": ["MONTH", "MONTH"]}
    base.update(kw)
    return base


def test_award_parsing_uses_the_latest_lot_term_one_language_and_no_duplicate_winners():
    r = rn.parse_award(_notice())
    assert r["notice_id"] == "100-2026" and r["buyer_country"] == "PL" and r["winner_country"] == "GB"
    assert r["winners"] == ["Acme Systems Ltd"]                       # one language, de-duplicated
    assert r["duration_months"] == 48 and r["end_basis"] == "stated"
    assert r["awarded_on"] == date(2023, 3, 8) and r["expected_end"] == date(2027, 3, 8)
    assert r["relevant"] is True and r["winner_size"] == "sme"


def test_units_and_missing_or_absurd_terms():
    assert rn.duration_months(["5"], ["YEAR"]) == 60
    assert rn.duration_months(["730"], ["DAY"]) == 24
    assert rn.duration_months(["999"], ["MONTH"]) is None               # indefinite or a data error
    assert rn.duration_months(None, None) is None and rn.duration_months(["abc"], ["MONTH"]) is None
    r = rn.parse_award(_notice(**{"duration-period-value-lot": None, "duration-period-unit-lot": None}))
    assert r["duration_months"] is None and r["end_basis"] == "assumed" and r["expected_end"] == date(2026, 3, 8)       # 36 months, labelled assumed
    assert rn.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)


def test_goods_and_support_awards_are_stored_but_not_marked_relevant():
    r = rn.parse_award(_notice(**{"notice-title": {"eng": "Poland – Servers – Supply of servers and storage"}, "classification-cpv": ["48820000", "30233000"]}))
    assert r["relevant"] is False


def test_radar_lists_relevant_contracts_ending_in_the_window_soonest_first_and_filters(conn):
    rows = [rn.parse_award(_notice(**{"publication-number": f"{i}-2026", "dispatch-date": d, "duration-period-value-lot": [m], "duration-period-unit-lot": ["MONTH"],
                                      **extra})) for i, (d, m, extra) in enumerate([
        ("2023-03-08", "48", {}),                                                         # ends 2027-03-08
        ("2022-12-01", "36", {"buyer-country": ["DEU"], "buyer-name": {"deu": ["Amt Test"]}}),       # ends 2025-12-01: already over
        ("2024-06-01", "30", {"winner-name": {"eng": ["Other Co"]}}),                      # ends 2026-12-01
        ("2023-01-01", "120", {"notice-title": {"eng": "Poland – Servers – Supply of servers"}, "classification-cpv": ["48820000"]}),      # not relevant
    ])]
    rn.upsert(conn, rows)
    today = date(2026, 11, 23)
    out = rn.ending_soon(conn, months=18, today=today)
    assert [r["notice_id"] for r in out] == ["2-2026", "0-2026"] and out[0]["months_left"] == 0 and out[0]["end_basis"] == "stated"
    assert [r["notice_id"] for r in rn.ending_soon(conn, months=3, today=today)] == ["2-2026"]
    assert rn.ending_soon(conn, months=18, country="DE", today=today) == []
    assert [r["notice_id"] for r in rn.ending_soon(conn, months=18, q="other co", today=today)] == ["2-2026"]
    inc = rn.incumbents(conn, years=5, today=today)
    assert {i["supplier"] for i in rn.incumbents(conn, years=3, today=today)} == {"Other Co"}          # the 3-year window drops the older awards
    assert {i["supplier"]: i["awards"] for i in inc} == {"Acme Systems Ltd": 2, "Other Co": 1}


def test_buyers_link_to_known_accounts_only_on_an_exact_name_match(conn):
    conn.execute("insert into organizations(display_name, country_code, entity_type) values ('Urząd Testowy','PL','company'), ('Urząd Testowy Wschód','PL','company')")
    rn.upsert(conn, [rn.parse_award(_notice())])
    assert rn.link_buyers(conn) == 1
    row = conn.execute("select o.display_name from awarded_contracts a join organizations o on o.id=a.buyer_org_id").fetchone()
    assert row["display_name"] == "Urząd Testowy"


def test_window_fetch_pages_through_results_and_a_repeat_changes_nothing(conn):
    import asyncio
    import json

    import httpx

    pages = []

    def handler(req):
        body = json.loads(req.content)
        pages.append(body["page"])
        notices = [_notice(**{"publication-number": f"{body['page']}-{i}-2026"}) for i in range(3)]
        return httpx.Response(200, json={"notices": notices, "totalNoticeCount": 250})
    c = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = asyncio.run(rn.fetch_window(c, conn, date(2023, 3, 1), date(2023, 3, 31), pause=0))
    assert pages == [1, 2, 3] and out["stored"] == 9
    asyncio.run(rn.fetch_window(httpx.AsyncClient(transport=httpx.MockTransport(handler)), conn, date(2023, 3, 1), date(2023, 3, 31), pause=0))
    assert conn.execute("select count(*) n from awarded_contracts").fetchone()["n"] == 9


def test_the_daily_refresh_is_queued_once_a_day(conn):
    from datetime import datetime, timezone
    now = datetime(2026, 11, 23, 8, 0, tzinfo=timezone.utc)
    assert rn.schedule_recent(conn, now) == 1 and rn.schedule_recent(conn, now) == 0


def test_renewal_api_needs_a_seller_login_and_validates_filters(conn, db_url):
    from fastapi.testclient import TestClient
    from ri.api.app import create_app
    from ri.api.auth import make_dev_token
    from ri.config import Settings
    from ri.db import Database
    from test_r15_contacts_deadlines import mk_user
    from test_r7_verify_discovery_usage import SECRET
    mk_user(conn, "bdm@x", "bdm", 5)
    conn.commit()
    db = Database(db_url, max_size=2)
    try:
        c = TestClient(create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET), db=db))
        h = {"Authorization": f"Bearer {make_dev_token(SECRET, 'bdm@x')}"}
        r = c.get("/api/renewals?months=12", headers=h).json()
        assert r["items"] == [] and r["contracts_known"] == 0
        assert c.get("/api/renewals?country=US", headers=h).status_code == 422          # not one of our markets
        assert c.get("/api/renewals/incumbents", headers=h).json() == {"items": []}
        assert c.get("/api/renewals").status_code in (401, 403)
    finally:
        db.close()


def test_stored_awards_are_rechecked_when_the_relevance_rule_changes(conn):
    rn.upsert(conn, [rn.parse_award(_notice())])
    conn.execute("update awarded_contracts set relevant=false, relevance_v='old'")
    assert rn.recheck_awards(conn) == 1
    row = conn.execute("select relevant, relevance_v from awarded_contracts").fetchone()
    assert row["relevant"] is True and row["relevance_v"] == rn.RELEVANCE_V
    assert rn.recheck_awards(conn) == 0
