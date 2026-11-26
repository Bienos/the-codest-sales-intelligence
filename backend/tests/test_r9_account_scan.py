"""The account scan: find where an account publishes, read it, push it through the normal pipeline. Sites are FAKES; names are synthetic."""
import asyncio

import httpx

from ri import budget, usage
from ri.events.budgeted import BudgetedExtractor
from ri.sources.account_scan import discover, parse_page, run_scan
from ri.sources.registry import seed_registry
from test_r7_verify_discovery_usage import ANCHOR, TEXT, FakeInner

HOME = """<html><head><link rel="alternate" type="application/rss+xml" href="/news/feed.xml"></head><body>
<a href="/en/suppliers">Suppliers</a><a href="https://other.example/tenders">External tender portal</a><a href="/newsroom/">News</a><a href="/careers">Careers</a><a href="https://other.example/press">not ours</a></body></html>"""
CAREERS = '<html><body><a href="https://boards.greenhouse.io/syntheticbank">Open roles</a><a href="https://jobs.lever.co/embed">x</a></body></html>'
RSS = (f"<rss><channel><item><title>Bank selects vendor for core banking platform replacement</title><link>https://bank.example/news/1</link><guid>n1</guid>"
       f"<description>{TEXT}</description></item></channel></rss>")
GH = {"jobs": [{"id": 1, "title": "Senior Java Engineer, Payments", "location": {"name": "London"}, "updated_at": "2026-11-18T10:00:00Z",
                "first_published": "2026-11-10T10:00:00Z", "absolute_url": "https://boards.greenhouse.io/syntheticbank/jobs/1", "content": "Kafka and AWS contract role",
                "departments": [{"name": "Engineering"}]}]}


def site(request: httpx.Request) -> httpx.Response:
    u = request.url
    if u.path == "/robots.txt":
        return httpx.Response(404)
    if u.host == "boards-api.greenhouse.io":
        return httpx.Response(200, json=GH)
    if u.host == "bank.example":
        return {"/": httpx.Response(200, text=HOME), "/careers": httpx.Response(200, text=CAREERS), "/news/feed.xml": httpx.Response(200, text=RSS, headers={"content-type": "application/rss+xml"}),
                "/en/suppliers": httpx.Response(200, text="<html><body>Supplier notices</body></html>"), "/newsroom/": httpx.Response(200, text="<html><body>Newsroom</body></html>")}.get(u.path, httpx.Response(404))
    return httpx.Response(404)


def test_parse_local_company_sections_and_ignore_comment_feeds():
    html = """
        <link rel="alternate" type="application/rss+xml" href="/comments/feed/">
        <a href="/de/ueber-uns/medien.html">Medien</a>
        <a href="/de/ueber-uns/jobs/offene-stellen.html">Stellen</a>
        <a href="https://karriere.bank.example/">Karriere</a>
        <a href="/en/supplying-goods-and-services/become-a-supplier/">Become a supplier</a>
    """
    result = parse_page(html, "https://bank.example/", "bank.example")
    assert result["feeds"] == []
    assert result["news"] == ["https://bank.example/de/ueber-uns/medien.html"]
    assert result["careers"] == [
        "https://bank.example/de/ueber-uns/jobs/offene-stellen.html", "https://karriere.bank.example/",
    ]
    assert result["procurement"] == ["https://bank.example/en/supplying-goods-and-services/become-a-supplier/"]


def test_parse_page_finds_feed_news_careers_and_job_board_but_not_other_sites():
    f = parse_page(HOME + CAREERS, "https://bank.example/", "bank.example")
    assert f["procurement"] == ["https://bank.example/en/suppliers"]
    assert f["feeds"] == ["https://bank.example/news/feed.xml"]
    assert "https://bank.example/newsroom/" in f["news"] and "https://bank.example/careers" in f["careers"]
    assert ("greenhouse", "syntheticbank") in f["ats"] and all(b != "embed" for _, b in f["ats"])
    assert not any("other.example" in u for u in f["news"])


def test_single_articles_are_not_mistaken_for_a_newsroom():
    f = parse_page('<a href="/en/blog/some-long-article-title/">x</a><a href="/en/blog/">Blog</a><a href="/en/careers/">Jobs</a>', "https://bank.example/", "bank.example")
    assert f["news"] == ["https://bank.example/en/blog/"] and f["careers"] == ["https://bank.example/en/careers/"]


def test_discover_follows_careers_page_to_the_job_board_and_honours_robots():
    out = asyncio.run(discover(httpx.AsyncClient(transport=httpx.MockTransport(site)), "bank.example"))
    kinds = {(e["kind"], e["board"]) for e in out["endpoints"]}
    assert ("feed", None) in kinds and ("job_board", "syntheticbank") in kinds
    blocked = lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /") if r.url.path == "/robots.txt" else site(r)  # noqa: E731
    out2 = asyncio.run(discover(httpx.AsyncClient(transport=httpx.MockTransport(blocked)), "bank.example"))
    assert out2["endpoints"] == [] and "could not be read" in out2["notes"][0]


def test_scan_reads_feed_and_job_board_extracts_a_quoted_event_and_records_coverage(conn):
    seed_registry(conn)
    budget.set_cap(conn, "anthropic", "monthly", usage.period_now(), 1_000_000, "tokens")
    org = conn.execute("INSERT INTO organizations(display_name, canonical_domain, country_code) VALUES ('Synthetic Example Bank AG','bank.example','GB') RETURNING id").fetchone()["id"]
    conn.commit()
    client = httpx.AsyncClient(transport=httpx.MockTransport(site))
    out = run_scan(conn, str(org), client=client, extractor=BudgetedExtractor(FakeInner(ANCHOR), conn), resolver=None)
    assert out["status"] == "ok" and out["scanned"] >= 2 and out["documents"] >= 1
    assert conn.execute("select count(*) c from job_postings where organization_id=%s", (org,)).fetchone()["c"] == 1
    assert conn.execute("select count(*) c from events where subject_org_id=%s", (org,)).fetchone()["c"] >= 1
    cov = {r["source_family"]: r["coverage_state"] for r in conn.execute("select * from account_source_coverage where organization_id=%s", (org,)).fetchall()}
    assert cov == {"newsroom": "monitored_healthy", "jobs": "monitored_healthy", "procurement": "monitored_healthy"}
    again = run_scan(conn, str(org), client=client, extractor=BudgetedExtractor(FakeInner(ANCHOR), conn), resolver=None)
    assert again["documents"] == 0                      # nothing new: a second press of the button costs nothing


def test_scan_without_a_website_says_so_instead_of_guessing(conn):
    org = conn.execute("INSERT INTO organizations(display_name) VALUES ('Synthetic No Site Ltd') RETURNING id").fetchone()["id"]
    conn.commit()
    out = run_scan(conn, str(org), client=httpx.AsyncClient(transport=httpx.MockTransport(site)))
    assert out["status"] == "no_website"


def test_connections_directory_lists_every_source_with_a_plain_state_and_what_to_do(conn):
    from ri.connections import classify, directory
    seed_registry(conn)
    rows = {r["source_id"]: r for r in directory(conn)}
    assert len(rows) >= 60 and all(r["state_label"] for r in rows.values())
    assert rows["registry-companies-house"]["state"] == "needs_credential" and "developer.company-information" in rows["registry-companies-house"]["where_to_get"]
    assert rows["ats-greenhouse"]["state"] == "per_account"
    assert rows["attention-fintechglobal"]["state"] == "needs_retest" and rows["attention-fintechglobal"]["address"] == "https://fintech.global/feed/"
    assert rows["attention-leaddev"]["state"] == "paused" and rows["attention-leaddev"]["address"] == "https://leaddev.com/feed"      # paused by the 26 Nov 2026 review
    assert rows["attention-finextra"]["state"] == "blocked_by_site" and rows["jobs-arbeitnow-de"]["state"] == "needs_retest"
    assert classify({"source_id": "attention-unlisted", "status": "pending_connector_validation", "collection_method": "feed", "config": {}}, None) == "needs_address"
    live = dict(rows["ats-lever"], status="active")
    assert classify({"source_id": "ats-lever", "status": "active", "collection_method": "official_api", "config": {}}, None) == "live" and live


def test_feed_connector_reads_rss_1_rdf_feeds_such_as_bis():
    from ri.sources.webwatch import FeedConnector
    rdf = ('<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<channel rdf:about="https://x.example/"><title>t</title></channel>'
           '<item rdf:about="https://x.example/p1"><title>Payments resilience report</title><link>https://x.example/p1</link><description>About payments.</description><dc:date>2026-11-20T09:00:00Z</dc:date></item></rdf:RDF>')
    c = FeedConnector(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404) if r.url.path == "/robots.txt" else httpx.Response(200, text=rdf))), "https://x.example/f.rss", source_id="regulator-bis")
    b = asyncio.run(c.fetch(None, 10))
    assert [i.external_id for i in b.items] == ["https://x.example/p1"] and c.canonicalize(b.items[0]).title == "Payments resilience report"


def test_google_sheets_client_accepts_a_key_pasted_as_json_and_rejects_an_incomplete_one():
    import json

    import pytest

    from ri.sheets.client import ConnectorBlocked, GoogleSheetsClient
    ok = json.dumps({"client_email": "sync@example.iam.gserviceaccount.com", "private_key": "x", "token_uri": "https://oauth2.example/token"})
    assert GoogleSheetsClient(None, credentials_json=ok)._sa["client_email"].startswith("sync@")
    with pytest.raises(ConnectorBlocked):
        GoogleSheetsClient(None, credentials_json='{"client_email": "a@b"}')
    with pytest.raises(ConnectorBlocked):
        GoogleSheetsClient(None)


def test_sync_endpoint_explains_what_is_missing_and_queues_once_connected(conn, db_url):
    from fastapi.testclient import TestClient

    from ri.api.app import create_app
    from ri.api.auth import make_dev_token
    from ri.config import Settings
    from ri.db import Database
    from test_r4_crm_notify import mk_user
    from test_r7_verify_discovery_usage import SECRET
    mk_user(conn, "admin@x", "admin")
    conn.commit()
    h = {"Authorization": f"Bearer {make_dev_token(SECRET, 'admin@x')}"}
    db = Database(db_url, max_size=2)
    try:
        c = TestClient(create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET), db=db))
        r = c.post("/api/admin/import/sync?workbook=A", headers=h)
        assert r.status_code == 409 and "GOOGLE_SERVICE_ACCOUNT_JSON" in r.text
        c2 = TestClient(create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET,
                                            google_service_account_json='{"client_email":"sync@example.iam.gserviceaccount.com","private_key":"x","token_uri":"t"}'), db=db))
        assert c2.post("/api/admin/import/sync?workbook=A", headers=h).json()["queued"] is True
        assert c2.get("/api/admin/imports", headers=h).json()["google_sheets"]["service_account_email"] == "sync@example.iam.gserviceaccount.com"
    finally:
        db.close()


def test_truncated_model_output_keeps_the_complete_events_and_never_invents_the_rest():
    from ri.events.extract import parse_events_json
    whole = '{"events":[{"event_type":"vendor_selected","subject_name":"A"},{"event_type":"programme_announced","subject_name":"B"}]}'
    assert len(parse_events_json(whole)["events"]) == 2
    cut = '{"events":[{"event_type":"vendor_selected","subject_name":"A","claims":[]},{"event_type":"programme_announced","subject_name":"B","claims":[{"anchor":"unfinished'
    out = parse_events_json(cut)
    assert [e["subject_name"] for e in out["events"]] == ["A"] and out["_salvaged"] is True
    import pytest
    with pytest.raises(ValueError):
        parse_events_json("sorry, no JSON here")


# ---------------------------------------------------------------- Polish BZP connector (fixture is a recorded sample of the live service, identifiers redacted)
def _bzp_sample():
    import json
    from pathlib import Path
    return json.loads((Path(__file__).parent / "fixtures" / "bzp_sample.json").read_text())


def test_bzp_relevance_uses_cpv_codes_and_polish_wording():
    from ri.sources.procurement import bzp_codes, is_bzp_software
    assert bzp_codes("72000000-5 (Usługi IT),45000000-7 (Roboty)") == ["72000000", "45000000"]
    assert is_bzp_software({"cpvCode": "72000000-5 (Usługi IT)", "orderObject": "x"})
    assert is_bzp_software({"cpvCode": "45000000-7 (Roboty)", "orderObject": "Wdrożenie systemu informatycznego do obsługi płatności"})
    assert not is_bzp_software({"cpvCode": "48000000-8 (Pakiety)", "orderObject": "Przedłużenie rocznej subskrypcji licencji oprogramowania"})      # a licence renewal is not work for us
    assert not is_bzp_software({"cpvCode": "15100000-9 (Mięso)", "orderObject": "Dostawa mięsa"})


def test_bzp_connector_reads_pages_filters_and_canonicalises_recorded_notices():
    import asyncio
    from datetime import datetime, timezone
    import httpx
    from ri.sources.procurement import PolishBzpConnector
    sample = _bzp_sample()
    notices = [n for n in sample if n["noticeType"] == "ContractNotice"]
    results = [n for n in sample if n["noticeType"] == "TenderResultNotice"]
    seen = []

    def handler(req):
        q = dict(req.url.params)
        seen.append((q["NoticeType"], q.get("PageNumber")))
        if q["NoticeType"] == "ContractNotice":
            return httpx.Response(200, json=notices if q["PageNumber"] == "1" else [])
        return httpx.Response(200, json=results if q["PageNumber"] == "1" else [])
    now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
    c = PolishBzpConnector(httpx.AsyncClient(transport=httpx.MockTransport(handler)), now_fn=lambda: now)
    batch = asyncio.run(c.fetch(None, 100))
    assert [k for k, _ in seen] == ["ContractNotice", "TenderResultNotice"]         # short page ends the kind; both kinds are read
    assert len(batch.items) == 2 and all(i.payload["cpvCode"].startswith("48") for i in batch.items)          # the software work of the five notices (a licence renewal is not)
    assert batch.next_cursor.startswith("from=2026-11-21T12:00:00Z") and not batch.has_more
    d = c.canonicalize(batch.items[0])
    assert d.kind == "procurement_notice" and d.canonical_url.startswith("https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/id/2026%2FBZP%20")
    assert d.structured["country"] == "PL" and d.structured["service_codes"][0].startswith("48") and d.structured["notice_kind"] == "contract_notice"


def test_bzp_results_are_awarded_only_when_a_contract_was_concluded_and_never_open():
    from datetime import datetime, timezone
    import httpx
    from ri.sources.base import RawItem
    from ri.sources.procurement import PolishBzpConnector
    c = PolishBzpConnector(httpx.AsyncClient(), now_fn=lambda: datetime(2026, 11, 21, tzinfo=timezone.utc))
    base = {"noticeNumber": "2026/BZP 1/01", "noticeType": "TenderResultNotice", "orderObject": "Wdrożenie systemu", "cpvCode": "72000000-5 (IT)", "publicationDate": "2026-11-20T10:00:00Z",
            "submittingOffersDate": None, "organizationName": "X", "contractors": [{"contractorName": "Vendor sp. z o.o."}]}
    won = c.canonicalize(RawItem("a", None, {**base, "procedureResult": "zawarcieUmowy"}))
    assert won.structured["status"] == "awarded" and won.structured["awarded_to"] == ["Vendor sp. z o.o."]
    annulled = c.canonicalize(RawItem("b", None, {**base, "procedureResult": "uniewaznienie", "contractors": None}))
    assert annulled.structured["status"] == "cancelled" and annulled.structured["awarded_to"] == []


def test_bzp_schema_drift_and_resume_cursor():
    import asyncio
    import httpx
    import pytest
    from ri.sources.base import SchemaDrift
    from ri.sources.procurement import BZP_PAGE, PolishBzpConnector
    full = [{"noticeNumber": f"n{i}", "cpvCode": "15100000-9 (x)", "orderObject": "y"} for i in range(BZP_PAGE)]
    c = PolishBzpConnector(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=full))))
    b = asyncio.run(c.fetch("from=2026-11-20T00:00:00Z;kind=ContractNotice;page=1", 100))
    assert b.has_more and b.next_cursor == "from=2026-11-20T00:00:00Z;kind=ContractNotice;page=41"          # full pages forever: stop after the per-run limit and resume
    bad = PolishBzpConnector(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"oops": 1}))))
    with pytest.raises(SchemaDrift):
        asyncio.run(bad.fetch(None, 10))


def test_scan_preserves_last_success_when_a_company_source_fails(conn):
    from datetime import datetime, timedelta, timezone

    seed_registry(conn)
    org = conn.execute("INSERT INTO organizations(display_name, canonical_domain, country_code) VALUES ('Synthetic Bank','bank.example','GB') RETURNING id").fetchone()["id"]
    old = datetime.now(timezone.utc) - timedelta(days=1)
    conn.execute("""INSERT INTO account_endpoints(organization_id,kind,url,last_scanned_at,last_success_at,last_status)
                    VALUES (%s,'newsroom','https://bank.example/newsroom/',%s,%s,'ok')""", (org, old, old))
    conn.execute("""INSERT INTO account_source_coverage(organization_id,source_family,coverage_state,last_success_at)
                    VALUES (%s,'procurement','source_not_found',NULL)""", (org,))
    conn.execute("""INSERT INTO account_source_coverage(organization_id,source_family,coverage_state,last_success_at)
                    VALUES (%s,'newsroom','monitored_healthy',%s)""", (org, old))
    conn.commit()
    def failed(request):
        return httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(500)
    result = run_scan(conn, str(org), client=httpx.AsyncClient(transport=httpx.MockTransport(failed)))
    assert result["status"] == "all_sources_failed"
    row = conn.execute("SELECT * FROM account_endpoints WHERE organization_id=%s", (org,)).fetchone()
    assert row["last_status"] == "failed" and row["last_success_at"] == old
    coverage = conn.execute("SELECT * FROM account_source_coverage WHERE organization_id=%s AND source_family='newsroom'", (org,)).fetchone()
    assert coverage["coverage_state"] == "monitored_degraded" and coverage["last_success_at"] == old


def test_page_health_rejects_a_javascript_shell():
    from ri.sources.webwatch import PageWatchConnector
    def shell(request):
        return httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(200, text="<html><body><p>Loading...</p></body></html>")
    connector = PageWatchConnector(httpx.AsyncClient(transport=httpx.MockTransport(shell)), "https://bank.example/news", source_id="test")
    health = asyncio.run(connector.health_check())
    assert not health.ok and health.error_class == "schema_drift"
