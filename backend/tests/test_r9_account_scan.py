"""The account scan: find where an account publishes, read it, push it through the normal pipeline. Sites are FAKES; names are synthetic."""
import asyncio

import httpx

from ri import budget, usage
from ri.events.budgeted import BudgetedExtractor
from ri.sources.account_scan import discover, parse_page, run_scan
from ri.sources.registry import seed_registry
from test_r7_verify_discovery_usage import ANCHOR, TEXT, FakeInner

HOME = """<html><head><link rel="alternate" type="application/rss+xml" href="/news/feed.xml"></head><body>
<a href="/newsroom/">News</a><a href="/careers">Careers</a><a href="https://other.example/press">not ours</a></body></html>"""
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
                "/newsroom/": httpx.Response(200, text="<html><body>Newsroom</body></html>")}.get(u.path, httpx.Response(404))
    return httpx.Response(404)


def test_parse_page_finds_feed_news_careers_and_job_board_but_not_other_sites():
    f = parse_page(HOME + CAREERS, "https://bank.example/", "bank.example")
    assert f["feeds"] == ["https://bank.example/news/feed.xml"]
    assert "https://bank.example/newsroom/" in f["news"] and "https://bank.example/careers" in f["careers"]
    assert ("greenhouse", "syntheticbank") in f["ats"] and all(b != "embed" for _, b in f["ats"])
    assert not any("other.example" in u for u in f["news"])


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
    assert cov == {"newsroom": "monitored_healthy", "jobs": "monitored_healthy"}
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
    assert rows["attention-leaddev"]["state"] == "needs_address"
    live = dict(rows["ats-lever"], status="active")
    assert classify({"source_id": "ats-lever", "status": "active", "collection_method": "official_api", "config": {}}, None) == "live" and live
