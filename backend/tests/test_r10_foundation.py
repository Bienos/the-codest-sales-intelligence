"""Foundation: market lanes, proven vs linked, candidate accounts, add-a-company, 12-hour sessions, the funnel. All names are synthetic."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ri.api.app import create_app
from ri.api.auth import issue_session, make_dev_token
from ri.config import Settings
from ri.db import Database
from ri.events.store import add_claim, store_event
from ri.events.verify import tag_untagged, verify_event
from ri.identity.discovery import promote_candidates
from ri.market import alpha2, is_public_body, lane_for, tier_for
from ri.sources.registry import seed_registry
from test_r4_crm_notify import mk_user
from test_r7_verify_discovery_usage import SECRET

NOW = datetime.now(timezone.utc)


def test_market_tiers_put_the_main_markets_first_and_keep_europe_and_the_us_as_a_secondary_pool():
    assert [tier_for(c) for c in ("GB", "DEU", "PL", "SE", "ESP", "US", "JP", None)] == ["core", "core", "core", "core", "secondary", "secondary", "other", "unknown"]
    assert alpha2("NOR") == "NO" and alpha2("xx") == "XX"


def test_public_bodies_go_to_their_own_lane_and_banks_never_do():
    for n in ("Bundesagentur für Arbeit", "Gemeinde Zürich", "NHS England", "City of London Corporation", "Ministerstwo Finansów", "University of Oxford"):
        assert lane_for(n) == "public", n
    for n in ("Barclays Bank PLC", "Sparkasse Köln", "Bank of England", "Temenos AG", "Klarna Bank AB", "Allianz Versicherungs AG"):
        assert lane_for(n) == "private", n
    assert not is_public_body("") and lane_for("Anything", "public_body") == "public"


def _event(conn, name, summary, *, org=None, verified_anchor=True, country_src=None, etype="vendor_selected", days_ago=3):
    cid = add_claim(conn, subject=name, predicate="selected_vendor", obj={}, doc_id=None, anchor=summary[:60], kind="quote", anchor_verified=verified_anchor)
    eid, _ = store_event(conn, event_type=etype, subject_name=name, subject_org_id=org, summary=summary, attrs={}, key_parts=(summary,),
                         event_at=NOW - timedelta(days=days_ago), pub_at=NOW - timedelta(days=days_ago), claim_ids=[cid], document_ids=[])
    return eid


def test_a_quoted_event_about_an_unknown_company_is_proven_but_not_linked_and_a_missing_quote_is_neither(conn):
    good = _event(conn, "Synthetic Nordic Bank AB", "Synthetic Nordic Bank AB selected a vendor to replace its core platform")
    bad = _event(conn, "Synthetic Other Bank AB", "Synthetic Other Bank AB is rumoured to be thinking about a new core", verified_anchor=False)
    assert verify_event(conn, good) == "unverified"          # not linked to an account, so not a signal-grade verified event
    assert verify_event(conn, bad) == "failed"
    r = {str(x["id"]): x for x in conn.execute("select id, proven, lane, market_tier from events").fetchall()}
    assert r[good]["proven"] is True and r[good]["lane"] == "private"
    assert r[bad]["proven"] is False


def test_tagging_uses_the_account_country_and_back_fills_old_events(conn):
    org = conn.execute("insert into organizations(display_name, country_code, canonical_domain) values ('Synthetic Polish Bank SA','PL','pl-bank.invalid') returning id").fetchone()["id"]
    e = _event(conn, "Synthetic Polish Bank SA", "Synthetic Polish Bank SA announced a payments platform programme", org=str(org), etype="programme_announced")
    conn.execute("update events set market_tagged_at = NULL")
    assert tag_untagged(conn, 100) == 1
    row = conn.execute("select market_tier, market_country, proven, verification_status from events where id=%s", (e,)).fetchone()
    assert (row["market_tier"], row["market_country"], row["proven"], row["verification_status"]) == ("core", "PL", True, "verified")
    assert tag_untagged(conn, 100) == 0                      # idempotent


def test_proven_events_about_unknown_core_market_companies_become_candidates_once_and_public_bodies_do_not(conn):
    conn.execute("insert into sources(source_id, display_name, source_tier, source_type, source_family, provider, countries, collection_method, status) "
                 "values ('t-src','T','S2','vendor','vendor','T','{SE}','feed','active')")
    for name, summ in (("Synthetic Nordic Bank AB", "Synthetic Nordic Bank AB selected a vendor to replace its core platform"),
                       ("Synthetic Nordic Bank AB", "Synthetic Nordic Bank AB announced a payments programme"),
                       ("Gemeinde Syntheticdorf", "Gemeinde Syntheticdorf selected a vendor for its website")):
        eid = _event(conn, name, summ, etype="vendor_selected")
        did = conn.execute("insert into source_documents(source_id, external_id, version_key, title) values ('t-src',%s,'v','t') returning id", (summ,)).fetchone()["id"]
        conn.execute("insert into event_documents(event_id, document_id) values (%s,%s)", (eid, did))
        verify_event(conn, eid)
    out = promote_candidates(conn, 10)
    assert out["candidates_created"] == 1
    names = [r["display_name"] for r in conn.execute("select o.display_name from organizations o join new_company_candidates c on c.organization_id=o.id").fetchall()]
    assert names == ["Synthetic Nordic Bank AB"]
    assert promote_candidates(conn, 10)["candidates_created"] == 0


@pytest.fixture()
def client(conn, db_url):
    mk_user(conn, "admin@x", "admin")
    mk_user(conn, "bdm@x", "bdm", 555)
    conn.commit()
    db = Database(db_url, max_size=4)
    app = create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET, session_secret="s" * 32), db=db)
    yield TestClient(app), (lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"})
    db.close()


def test_feed_views_split_accounts_main_markets_rest_of_europe_and_public_sector(client, conn):
    c, h = client
    for name, summ, country in (("Synthetic Swiss Bank AG", "Synthetic Swiss Bank AG selected a vendor for core banking", "CH"),
                                ("Synthetic Spanish Bank SA", "Synthetic Spanish Bank SA selected a vendor for payments", "ES"),
                                ("Stadt Syntheticburg", "Stadt Syntheticburg selected a vendor for e-government", "DE")):
        e = _event(conn, name, summ)
        conn.execute("insert into sources(source_id, display_name, source_tier, source_type, source_family, provider, countries, collection_method, status) values (%s,'T','S2','vendor','vendor','T',%s,'feed','active') on conflict do nothing",
                     (f"s-{country}", [country]))
        did = conn.execute("insert into source_documents(source_id, external_id, version_key, title) values (%s,%s,'v','t') returning id", (f"s-{country}", summ)).fetchone()["id"]
        conn.execute("insert into event_documents(event_id, document_id) values (%s,%s)", (e, did))
        verify_event(conn, e)
    conn.commit()
    counts = c.get("/api/feed?view=accounts", headers=h("bdm")).json()["view_counts"]
    assert (counts["accounts"], counts["market"], counts["rest"], counts["public"]) == (0, 1, 1, 1)
    m = c.get("/api/feed?view=market", headers=h("bdm")).json()["items"]
    assert [i["scope"]["buyer"] for i in m] == ["Synthetic Swiss Bank AG"] and m[0]["organization"] is None and m[0]["scope"]["proven"] is True
    assert c.get("/api/feed?view=public", headers=h("bdm")).json()["count"] == 1


def test_add_a_company_needs_only_a_website_dedupes_by_domain_stores_context_and_queues_the_scan(client, conn):
    c, h = client
    assert c.post("/api/accounts/add", headers=h("bdm"), json={"website": "not a site"}).status_code == 400
    assert c.post("/api/accounts/add", headers=h("bdm"), json={"website": "http://169.254.169.254/"}).status_code == 400
    r = c.post("/api/accounts/add", headers=h("bdm"), json={"website": "https://www.Synthetic-Fintech.example/about?x=1", "links": ["https://www.linkedin.com/company/synthetic", "javascript:alert(1)"], "notes": "met at a conference", "region": "UK"})
    assert r.status_code == 200 and r.json()["domain"] == "synthetic-fintech.example" and r.json()["created"] is True
    again = c.post("/api/accounts/add", headers=h("bdm"), json={"website": "synthetic-fintech.example"}).json()
    assert again["created"] is False and again["id"] == r.json()["id"]
    ctx = c.get(f"/api/accounts/{r.json()['id']}/context", headers=h("bdm")).json()["items"]
    assert sorted(x["kind"] for x in ctx) == ["link", "note", "region"] and all(not x["value"].startswith("javascript") for x in ctx)
    assert conn.execute("select count(*) c from jobs where kind='account_scan'").fetchone()["c"] >= 1
    assert conn.execute("select count(*) c from new_company_candidates").fetchone()["c"] == 1


def test_daily_limit_stops_one_person_from_burning_the_search_budget(client, conn):
    c, h = client
    from ri.api import intel
    for i in range(intel.DAILY_SCAN_LIMIT):
        conn.execute("insert into audit_events(actor, operation, record_type) values ('bdm@x','company_added','organization')")
    conn.commit()
    r = c.post("/api/accounts/add", headers=h("bdm"), json={"website": "one-more.example"})
    assert r.status_code == 429 and "Daily limit" in r.text


def test_sessions_last_twelve_hours_and_still_need_an_active_user(client, conn):
    c, h = client
    st = Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET, session_secret="s" * 32)
    tok = c.post("/api/auth/session", headers=h("bdm")).json()
    assert tok["expires_at"] - int(datetime.now(timezone.utc).timestamp()) > 11 * 3600
    assert c.get("/api/me", headers={"Authorization": f"Bearer {tok['token']}"}).json()["email"] == "bdm@x"
    expired, _ = issue_session(st, "bdm@x", hours=-1)
    assert c.get("/api/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    conn.execute("update users set active=false where email='bdm@x'"); conn.commit()
    assert c.get("/api/me", headers={"Authorization": f"Bearer {tok['token']}"}).status_code == 403


def test_pipeline_funnel_shows_where_things_stop(client, conn):
    c, h = client
    _event(conn, "Synthetic Nordic Bank AB", "Synthetic Nordic Bank AB selected a vendor to replace its core platform")
    conn.commit()
    f = c.get("/api/admin/pipeline", headers=h("admin")).json()
    stages = {x["key"]: x["n"] for x in f["funnel"]}
    assert stages["events"] == 1 and stages["linked"] == 0 and f["accounts"] == 0


def test_a_month_only_milestone_from_the_model_is_read_as_end_of_month_and_junk_is_ignored():
    from ri.initiatives.link import parse_milestone
    assert parse_milestone("2027-03") == "2027-03-31" and parse_milestone("2027-02-10T00:00:00Z") == "2027-02-10" and parse_milestone("2028") == "2028-12-31"
    assert parse_milestone("Q3 2027") is None and parse_milestone("2027-13") is None and parse_milestone("2027-02-31") is None and parse_milestone(None) is None


def test_one_failing_maintenance_step_is_reported_and_the_others_still_run(conn, monkeypatch):
    from ri import scheduler
    import ri.scoring as scoring
    def boom(*a, **k):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(scoring, "recompute_scores", boom)
    out = scheduler.run_maintenance(conn)
    assert out["score_error"].startswith("RuntimeError") and "market_tagged" in out and "reevaluated" in out
