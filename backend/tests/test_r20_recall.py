"""Recall assurance: coverage grid, source yield drops, reported misses, and the canary checks that use them."""
from datetime import datetime, timedelta, timezone

import pytest


def _event(conn, etype, country, key):
    conn.execute("insert into events(event_key, event_type, subject_name_raw, summary, attrs, market_country, first_observed_at, verification_status, lifecycle_status, extraction_model_version) "
                 "values (%s,%s,'x','s','{}',%s, now(), 'verified', 'active', 'test')", (key, etype, country))


def _source(conn, sid):
    conn.execute("insert into sources(source_id, display_name, source_tier, source_type, source_family, provider, status, collection_method) "
                 "values (%s,%s,'S1','procurement','procurement','t','active','official_api') on conflict do nothing", (sid, sid))


def _run(conn, sid, days_ago, new):
    conn.execute("insert into monitoring_runs(source_id, started_at, finished_at, status, items_new, items_changed, items_unchanged, items_unavailable) "
                 "values (%s, now() - make_interval(days => %s), now(), 'success', %s, 0, 0, 0)", (sid, days_ago, new))


def test_coverage_grid_marks_empty_market_and_kind_cells(conn):
    from ri.recall import coverage_grid
    _event(conn, "procurement_published", "PL", "k1")
    _event(conn, "procurement_awarded", "DE", "k2")
    _event(conn, "funding_announced", "GB", "k3")
    g = coverage_grid(conn, 14)
    cell = {(c["market"], c["kind"]): c for c in g["cells"]}
    assert cell[("Poland", "Tenders")]["count"] == 1 and not cell[("Poland", "Tenders")]["empty"]
    assert cell[("DACH", "Tenders")]["count"] == 1
    assert cell[("UK", "Funding and deals")]["count"] == 1
    assert cell[("Nordics", "Tenders")]["empty"] and "Nordics: Tenders" in g["empty"]
    assert cell[("UK", "Tenders")]["empty"]


def test_events_without_a_market_are_not_counted_anywhere(conn):
    from ri.recall import coverage_grid
    conn.execute("insert into events(event_key, event_type, subject_name_raw, summary, attrs, first_observed_at, verification_status, lifecycle_status, extraction_model_version) "
                 "values ('nm','procurement_published','x','s','{}', now(), 'verified', 'active', 'test')")
    assert all(c["count"] == 0 for c in coverage_grid(conn, 14)["cells"])


def test_a_source_that_still_runs_but_returns_far_less_is_flagged_a_young_or_quiet_one_is_not(conn):
    from ri.recall import source_yield_drops
    _source(conn, "drop-me"); _source(conn, "healthy"); _source(conn, "young"); _source(conn, "quiet")
    for d, n in [(25, 60), (18, 60), (12, 60)]:
        _run(conn, "drop-me", d, n)
        _run(conn, "healthy", d, n)
    _run(conn, "drop-me", 2, 3)                    # this week: 3, usually about 60 a week
    _run(conn, "healthy", 2, 55)
    _run(conn, "young", 4, 0)                      # under 14 days of history: never flagged
    for d in (25, 18, 12):
        _run(conn, "quiet", d, 1)                  # about 1 a week: baseline below the floor
    _run(conn, "quiet", 2, 0)
    flagged = {d["source_id"]: d for d in source_yield_drops(conn)}
    assert set(flagged) == {"drop-me"}
    assert flagged["drop-me"]["last_7_days"] == 3 and flagged["drop-me"]["usual_per_week"] >= 40


def test_report_a_miss_says_whether_we_already_had_the_page_and_queues_it_otherwise(conn):
    from ri.recall import misses, report_miss
    _source(conn, "news1")
    conn.execute("insert into source_documents(source_id, external_id, version_key, canonical_url, title, content_text, raw_meta) "
                 "values ('news1','a','v','https://www.example.com/news/bank-deal/','t','x','{}')")
    had = report_miss(conn, url="https://example.com/news/bank-deal", note="Saw this on LinkedIn", by="a@example.invalid")
    assert had["already_known"] is True and had["source_id"] == "news1"
    new = report_miss(conn, url="https://example.org/press/other", note="", by="a@example.invalid")
    assert new["already_known"] is False and new["status"] == "queued"
    assert report_miss(conn, url="https://example.org/press/other", note="", by="b@example.invalid")["duplicate"] is True
    m = misses(conn)
    assert m["total"] == 2 and m["missed"] == 1 and m["caught"] == 1
    with pytest.raises(ValueError):
        report_miss(conn, url="not a url", note="", by="a@example.invalid")
    assert conn.execute("select count(*) n from jobs where kind='verify_url'").fetchone()["n"] == 2


def test_canary_judges_tender_coverage_only_after_the_tender_feeds_have_really_run(conn):
    from ri.canary import run_checks
    assert "coverage_gap" not in {c["code"] for c in run_checks(conn)}          # fresh deployment: nothing to judge yet
    _source(conn, "tenders-a")
    for d in (1, 2, 3, 4, 5, 6):
        _run(conn, "tenders-a", d, 10)
    assert "coverage_gap" in {c["code"] for c in run_checks(conn)}              # feeds ran, yet no market has any tender
    for c in ("GB", "DE", "SE", "PL"):
        _event(conn, "procurement_published", c, f"t-{c}")
    assert "coverage_gap" not in {c["code"] for c in run_checks(conn)}


def test_report_a_miss_endpoint_accepts_a_json_body_and_admin_recall_is_admin_only(conn, db_url):
    from fastapi.testclient import TestClient
    from ri.api.app import create_app
    from ri.api.auth import make_dev_token
    from ri.config import Settings
    from ri.db import Database
    from test_r15_contacts_deadlines import mk_user
    from test_r7_verify_discovery_usage import SECRET
    mk_user(conn, "boss@x", "admin")
    mk_user(conn, "bdm@x", "bdm", 5)
    conn.commit()
    db = Database(db_url, max_size=2)
    try:
        c = TestClient(create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET), db=db))
        h = lambda e: {"Authorization": f"Bearer {make_dev_token(SECRET, e)}"}      # noqa: E731
        r = c.post("/api/misses", headers=h("bdm@x"), json={"url": "https://example.org/press/bank-picks-vendor", "note": "Seen on LinkedIn"})
        assert r.status_code == 200 and r.json()["already_known"] is False and r.json()["status"] == "queued"
        assert c.post("/api/misses", headers=h("bdm@x"), json={"url": "nonsense-link"}).status_code in (400, 422)
        d = c.get("/api/admin/recall", headers=h("boss@x")).json()
        assert set(d) == {"grid", "yield_drops", "misses"} and d["misses"]["total"] == 1 and d["misses"]["missed"] == 1
        assert len(d["grid"]["cells"]) == len(d["grid"]["markets"]) * len(d["grid"]["kinds"])
        assert c.get("/api/admin/recall", headers=h("bdm@x")).status_code == 403
    finally:
        db.close()
