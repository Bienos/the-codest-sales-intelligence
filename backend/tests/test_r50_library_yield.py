"""Library, "What it brought" (round 7, area 5): for every source, the pages read and the findings in the last 30 days, how many of those findings
are about our target accounts, and one quiet label for a source that reads many pages but finds little for us, or is paused with a reason.
All figures come from a fixed number of grouped queries (the old endpoint ran sub-queries for every source), every key the page used before is
kept, and "about our accounts" uses the one definition of a target account. Names are invented."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ri.api import intel
from ri.sources import registry
from ri.sources.registry import seed_registry
from test_r1_pipeline import CERT

NOW = datetime.now(timezone.utc)
OLD_KEYS = {"source_id", "display_name", "source_tier", "source_type", "source_family", "provider", "countries", "signal_rule_ids", "collection_method",
            "auth_mode", "retention_policy", "licence_notes", "poll_interval_s", "status", "cost_profile", "canonical_domain", "url", "url_basis",
            "validation", "health", "last_success_at", "last_attempt_at", "consecutive_failures", "error_class", "health_detail", "documents", "events",
            "recent_buying_findings", "cost_usd_month", "units_month", "monitoring_mode"}


@pytest.fixture()
def client(conn, db_url):
    from ri.api.app import create_app
    from ri.api.auth import make_dev_token
    from ri.config import Settings
    from ri.db import Database
    conn.execute("insert into users(email, display_name, role, team) values ('admin@x','admin@x','admin','t1')")
    conn.commit()
    secret = "x" * 32
    db = Database(db_url, max_size=2)
    c = TestClient(create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=secret), db=db))
    yield c, {"Authorization": f"Bearer {make_dev_token(secret, 'admin@x')}"}
    db.close()


def _org(conn, name, *, target):
    """A target account (the lead list marks it Eligible) or a company that is not one (the list marks it Out of scope)."""
    return conn.execute("insert into organizations(display_name, country_code, segment, list_eligibility) values (%s,'GB','Banking',%s) returning id",
                        (name, "Eligible" if target else "Out of scope")).fetchone()["id"]


def _doc(conn, sid, ext, *, days=1):
    return conn.execute("insert into source_documents(source_id, external_id, version_key, canonical_url, title, content_text, raw_meta, fetched_at) "
                        "values (%s,%s,'v1',%s,'t','body','{}',%s) returning id", (sid, ext, f"https://x.example/{sid}/{ext}", NOW - timedelta(days=days))).fetchone()["id"]


def _event(conn, key, *, org=None, doc=None, days=1, etype="product_launch", attrs=None, replay=False):
    eid = conn.execute("insert into events(event_key, event_type, subject_org_id, subject_name_raw, summary, attrs, first_observed_at, lane, extraction_model_version, is_historical_replay) "
                       "values (%s,%s,%s,'Acme','x',%s,%s,'private','test',%s) returning id", (key, etype, org, json.dumps(attrs or {}), NOW - timedelta(days=days), replay)).fetchone()["id"]
    if doc:
        conn.execute("insert into event_documents(event_id, document_id) values (%s,%s)", (eid, doc))
    return eid


def _items(conn):
    return {r["source_id"]: r for r in intel.library_items(conn, NOW)}


def _world(conn):
    seed_registry(conn)
    for sid in ("attention-finextra", "regulator-ecb", "procurement-ted", "jobs-arbeitnow-de", "regulator-fca"):
        registry.activate(conn, sid, CERT, "t")
    target, other = _org(conn, "Example Target Bank", target=True), _org(conn, "Example Other Retailer", target=False)
    # Finextra: 4 pages in the last 30 days and one 40 days ago. Findings: one about a target, one about another company, one about nobody,
    # one 40 days old (about a target), one replayed history (about a target). Only the first three count in the window.
    docs = [_doc(conn, "attention-finextra", f"a{i}", days=2 + i) for i in range(4)] + [_doc(conn, "attention-finextra", "old", days=40)]
    _event(conn, "f1", org=target, doc=docs[0])
    _event(conn, "f2", org=other, doc=docs[1])
    _event(conn, "f3", doc=docs[2])
    _event(conn, "f4", org=target, doc=docs[4], days=40)
    _event(conn, "f5", org=target, doc=docs[3], replay=True)
    # the same finding read on two pages counts once
    _event(conn, "f6", org=target, doc=docs[0])
    conn.execute("insert into event_documents(event_id, document_id) select e.id, %s from events e where e.event_key='f6'", (docs[3],))
    conn.commit()
    return {"target": target, "other": other}


def test_counts_per_source_for_pages_findings_and_findings_about_target_accounts(conn):
    _world(conn)
    r = _items(conn)["attention-finextra"]
    assert r["pages_30d"] == 4 and r["documents"] == 5
    assert r["findings_30d"] == 4            # f1, f2, f3, f6 (f4 is 40 days old, f5 is replayed history)
    assert r["findings_target_30d"] == 2     # f1 and f6: about a target account; f2 is about a company that is not one
    assert r["events"] == 6                  # all time, as before
    assert OLD_KEYS <= set(r)


def test_a_company_that_is_not_a_target_account_never_counts_as_about_our_accounts(conn):
    w = _world(conn)
    conn.execute("update organizations set list_eligibility='Out of scope' where id=%s", (w["target"],))
    conn.commit()
    assert _items(conn)["attention-finextra"]["findings_target_30d"] == 0
    conn.execute("update organizations set list_eligibility='Eligible' where id=%s", (w["target"],))
    conn.commit()
    assert _items(conn)["attention-finextra"]["findings_target_30d"] == 2


def test_an_empty_source_has_zeros_and_no_label(conn):
    _world(conn)
    r = _items(conn)["regulator-fca"]
    assert (r["pages_30d"], r["findings_30d"], r["findings_target_30d"], r["documents"], r["events"], r["recent_buying_findings"]) == (0, 0, 0, 0, 0, 0)
    assert r["yield_note"] is None and r["pause_reason"] is None


def test_job_findings_that_name_their_board_count_for_that_board_once(conn):
    w = _world(conn)
    _event(conn, "j1", org=w["target"], etype="engineering_job_opened", attrs={"provider": "arbeitnow", "job_id": "1"})
    _event(conn, "j2", org=w["other"], etype="engineering_job_opened", attrs={"provider": "arbeitnow", "job_id": "2"})
    _event(conn, "j3", org=w["target"], etype="engineering_job_opened", attrs={"provider": "arbeitnow", "job_id": "3"}, days=45)    # outside the window
    # a job finding that does carry a page is counted through the page, not a second time through the board
    d = _doc(conn, "jobs-arbeitnow-de", "p1")
    _event(conn, "j4", org=w["target"], etype="engineering_job_opened", attrs={"provider": "arbeitnow", "job_id": "4"}, doc=d)
    # the career systems a company scan reads count for their own source (Greenhouse here, Workday through the ats-<name> rule)
    _event(conn, "j5", org=w["target"], etype="engineering_job_opened", attrs={"provider": "greenhouse", "job_id": "5"})
    _event(conn, "j6", org=w["target"], etype="engineering_job_opened", attrs={"provider": "workday", "job_id": "6"})
    _event(conn, "j7", org=w["other"], etype="job_closed", attrs={"provider": "somethingelse", "job_id": "7"})        # no such source: ignored
    conn.commit()
    got = _items(conn)
    r = got["jobs-arbeitnow-de"]
    assert r["pages_30d"] == 1 and r["findings_30d"] == 3 and r["findings_target_30d"] == 2
    assert (got["ats-greenhouse"]["findings_30d"], got["ats-greenhouse"]["findings_target_30d"]) == (1, 1)
    assert (got["ats-workday"]["findings_30d"], got["ats-workday"]["findings_target_30d"]) == (1, 1)


def test_a_source_that_reads_many_pages_but_finds_little_gets_one_quiet_label(conn):
    w = _world(conn)
    for i in range(25):
        _doc(conn, "regulator-ecb", f"r{i}", days=3)
    for i in range(30):
        _doc(conn, "procurement-ted", f"t{i}", days=3)               # tenders come from public buyers: never judged by accounts
    for i in range(10):
        _doc(conn, "regulator-fca", f"c{i}", days=3)                 # too few pages to say anything
    conn.commit()
    got = _items(conn)
    assert got["regulator-ecb"]["yield_note"] == "finds_little"
    assert got["procurement-ted"]["yield_note"] is None and got["regulator-fca"]["yield_note"] is None
    assert got["attention-finextra"]["yield_note"] is None          # it did bring findings about target accounts
    # one finding about a target account is enough for a source that read few pages, not for one that read hundreds
    _event(conn, "e1", org=w["target"], doc=_doc(conn, "regulator-ecb", "hit", days=1))
    conn.commit()
    assert _items(conn)["regulator-ecb"]["yield_note"] is None
    for i in range(100):
        _doc(conn, "regulator-ecb", f"m{i}", days=2)
    conn.commit()
    assert _items(conn)["regulator-ecb"]["yield_note"] == "finds_little"
    for i in range(2):
        _event(conn, f"e2{i}", org=w["target"], doc=_doc(conn, "regulator-ecb", f"h{i}", days=1))
    conn.commit()
    assert _items(conn)["regulator-ecb"]["yield_note"] is None      # three in about 130 pages: at least one per hundred


def test_a_source_paused_by_a_review_says_so_and_why_and_a_pause_by_a_person_only_says_why(conn):
    _world(conn)
    for i in range(30):
        _doc(conn, "regulator-ecb", f"r{i}", days=3)
    # a person pauses it and writes a reason: just "paused", the reason is shown
    registry.pause(conn, "regulator-ecb", "admin@x", "Taking it off while we change the address.")
    conn.commit()
    r = _items(conn)["regulator-ecb"]
    assert r["status"] == "paused" and r["yield_note"] is None and r["pause_reason"] == "Taking it off while we change the address."
    # a source review pauses it and keeps its plain reason in the source's config: "paused after a review"
    registry.review_pause(conn, "regulator-ecb", "Paused after a review: 30 pages in a month, none about our accounts.")
    conn.commit()
    r = _items(conn)["regulator-ecb"]
    assert r["status"] == "paused" and r["yield_note"] == "paused_after_review" and "none about our accounts" in r["pause_reason"]
    assert r["pages_30d"] == 30 and r["documents"] == 30            # what it collected is kept and still shown
    # switched on again by hand: the review note is gone with the pause, the label with it
    registry.activate(conn, "regulator-ecb", CERT, "admin@x")
    conn.commit()
    r = _items(conn)["regulator-ecb"]
    assert r["status"] == "active" and r["pause_reason"] is None and r["review_note"] is None and r["yield_note"] == "finds_little"
    # paused with nothing written down: just paused, no claim about a review
    conn.execute("update sources set status='paused' where source_id='regulator-ecb'")
    conn.execute("update source_health set state='unconfigured', detail=NULL where source_id='regulator-ecb'")
    conn.commit()
    r = _items(conn)["regulator-ecb"]
    assert r["status"] == "paused" and r["yield_note"] is None and r["pause_reason"] is None


class _Counting:
    """Counts the statements sent to the database."""

    def __init__(self, conn):
        self.conn, self.n = conn, 0

    def execute(self, *a, **k):
        self.n += 1
        return self.conn.execute(*a, **k)


def test_the_number_of_queries_does_not_grow_with_the_number_of_sources(conn):
    w = _world(conn)
    first = _Counting(conn)
    rows = intel.library_items(first, NOW)
    for i in range(60):
        sid = f"test-extra-{i}"
        conn.execute("insert into sources(source_id, display_name, source_tier, source_type, source_family, provider, collection_method, status, poll_interval_s) "
                     "values (%s,%s,'S4','feed','attention','Test',  'feed','active',86400)", (sid, f"Extra {i}"))
        conn.execute("insert into source_health(source_id, state) values (%s,'healthy')", (sid,))
        d = _doc(conn, sid, "p", days=3)
        _event(conn, f"x{i}", org=w["target"], doc=d)
    conn.commit()
    second = _Counting(conn)
    more = intel.library_items(second, NOW)
    assert len(more) == len(rows) + 60
    assert first.n == second.n <= 8
    assert {r["source_id"]: r for r in more}["test-extra-7"]["findings_target_30d"] == 1


def test_the_endpoint_keeps_every_old_key_and_adds_the_new_ones(client, conn):
    c, h = client
    _world(conn)
    items = {s["source_id"]: s for s in c.get("/api/admin/library", headers=h).json()["items"]}
    fx = items["attention-finextra"]
    assert OLD_KEYS <= set(fx) and {"pages_30d", "findings_30d", "findings_target_30d", "yield_note", "pause_reason", "review_note"} <= set(fx)
    assert (fx["pages_30d"], fx["findings_30d"], fx["findings_target_30d"]) == (4, 4, 2) and fx["monitoring_mode"] == "scheduled"
    assert c.get("/api/admin/library").status_code == 401
