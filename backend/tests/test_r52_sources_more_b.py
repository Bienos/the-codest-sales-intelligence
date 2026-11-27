"""Round 8, helper B: more news sources (press-release wires, trade press, company engineering blogs) and the account scan's detection of an account's engineering blog
and press/insights feed. No network: sites are FAKES built from fixtures in tests/fixtures/sources_more_b/; names are invented."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
import yaml

from ri import budget, usage
from ri.events.budgeted import BudgetedExtractor
from ri.sources import autovalidate
from ri.sources.account_scan import (FEED_LABEL, classify_feed, discover, first_party_resolver, is_tech_section, medium_feed, parse_page, run_scan,
                                     same_company)
from ri.sources.registry import seed_registry
from ri.sources.util import sha
from ri.sources.webwatch import CONTENT_EXCERPT, FeedConnector, first_party_publishers
from test_r7_verify_discovery_usage import ANCHOR, TEXT, FakeInner

FIX = Path(__file__).parent / "fixtures" / "sources_more_b"
RULES = Path(__file__).resolve().parents[1] / "rules"

WIRES = ["attention-presseportal-finanzen", "attention-finanznachrichten-adhoc", "attention-finanznachrichten-fs"]
TRADE = ["attention-nordicfintech", "attention-finews", "attention-fintechbaltic"]
BLOGS = ["blog-wise-engineering", "blog-adyen-tech", "blog-qonto-engineering", "blog-allegro-tech"]
# looked at on 27 Nov 2026 and NOT registered, each for a stated reason (see the final report): no feed, a robots.txt or terms bar, a bot check, or off topic
REJECTED_HOSTS = ("eqs-news.com", "investegate.co.uk", "businesswire.com", "ots.at", "lse.co.uk", "londonstockexchange.com", "news.cision.com", "oslobors.no", "euronext.com",
                  "cio.de", "computerwoche.de", "intellinews.com", "computersweden.se", "bankingtech.pl", "gpw.pl", "versicherungsbote.de")
BLOCKED_HOSTS = ("techuk.org", "uktech.news", "fintechfutures.com", "finextra.com", "bankier.pl", "paymentexpert.com", "finanz-szene.de", "computing.co.uk")


def _rows() -> list[dict]:
    return yaml.safe_load((RULES / "sources.yaml").read_text(encoding="utf-8"))["sources"]


def _by_id() -> dict[str, dict]:
    return {s["source_id"]: s for s in _rows()}


def _fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------------------ the registry entries
def test_the_new_sources_are_public_keyless_feeds_the_daily_test_can_switch_on():
    by, rows = _by_id(), _rows()
    assert len({s["source_id"] for s in rows}) == len(rows)
    urls: dict[str, list[str]] = {}
    for s in rows:
        if (s.get("config") or {}).get("url"):
            urls.setdefault(s["config"]["url"], []).append(s["source_id"])
    for sid in [*WIRES, *TRADE, *BLOGS]:
        s = by[sid]
        assert s["collection_method"] == "feed" and "status" not in s and s.get("auth_mode", "none") == "none"        # waiting for validation: the daily test decides
        assert s["retention_policy"] == "metadata_only" and "metadata + short excerpt only" in s["licence_notes"]
        assert s["signal_rule_ids"] and s["languages"] and s["countries"] and s["poll_interval_s"] in (86400, 172800)
        url = s["config"]["url"]
        host = urlparse(url).hostname
        assert urlparse(url).scheme == "https" and (host == s["canonical_domain"] or host.endswith("." + s["canonical_domain"])), sid
        assert urls[url] == [sid], f"{url} is registered twice: {urls[url]}"
        assert not any(host.endswith(b) for b in BLOCKED_HOSTS), sid
        assert "27 Nov 2026" in s["config"]["url_basis"] and not re.search(r"verify|terms vary|commercial|per-employer|per country", s["licence_notes"], re.I)
        # exactly what the daily test checks before it switches a source on by itself
        pol = autovalidate.policy({"source_id": sid, "status": "pending_connector_validation", "collection_method": "feed", "auth_mode": s.get("auth_mode", "none"),
                                   "licence_notes": s["licence_notes"], "config": s["config"]}, None)
        assert pol["eligible"] and pol["state"] == "testing", (sid, pol)


def test_wires_and_trade_press_are_media_and_the_blogs_are_company_sites():
    by = _by_id()
    for sid in [*WIRES, *TRADE]:
        s = by[sid]
        assert sid.startswith("attention-") and s["source_tier"] == "S4" and s["source_family"] == "attention" and s["source_type"] == "news"
    for sid in BLOGS:
        s = by[sid]
        # the same class as a company's own pages: first-party statements, so the programme rules (S1-S3) can use them
        assert sid.startswith("blog-") and s["source_tier"] == "S2" and s["source_type"] == "company_site" and s["source_family"] == "company_ir" and s["provider"]
    assert [by[s]["provider"] for s in BLOGS] == ["Wise", "Adyen", "Qonto", "Allegro"]           # the exact account names of the lead list: the model names the company the same way, so findings link


def test_nothing_that_was_checked_and_refused_is_registered_and_the_finanznachrichten_licence_is_quoted():
    for s in _rows():
        hosts = [(s.get("canonical_domain") or ""), urlparse((s.get("config") or {}).get("url") or "").hostname or ""]
        assert not any(h == r or h.endswith("." + r) for h in hosts if h for r in REJECTED_HOSTS), s["source_id"]
    by = _by_id()
    for sid in ("attention-finanznachrichten-adhoc", "attention-finanznachrichten-fs"):
        assert "headlines with a link" in by[sid]["licence_notes"] and "revocable" in by[sid]["licence_notes"]


def test_seeding_registers_the_new_sources_waiting_for_validation(conn):
    seed_registry(conn)
    for sid in [*WIRES, *TRADE, *BLOGS]:
        r = conn.execute("select status, collection_method, source_tier from sources where source_id=%s", (sid,)).fetchone()
        assert r and r["status"] == "pending_connector_validation" and r["collection_method"] == "feed"
    assert conn.execute("select count(*) c from sources where source_id = any(%s) and source_tier='S2'", (BLOGS,)).fetchone()["c"] == 4


# ------------------------------------------------------------------------------------------------ the feed reader
def _client(body: str, *, robots: str | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots) if robots is not None else httpx.Response(404)
        return httpx.Response(200, text=body, headers={"content-type": "application/rss+xml"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _read(body: str, source_id: str = "test-feed", **kw):
    con = FeedConnector(_client(body), "https://feed.example/rss", source_id=source_id, **kw)
    batch = asyncio.run(con.fetch(None, 50))
    return con, batch, [con.canonicalize(i) for i in batch.items]


def test_a_feed_that_starts_with_a_blank_line_is_read():
    _, batch, docs = _read(_fixture("feed_blank_first_line.xml"))
    assert [d.title for d in docs] == ["Bank selects vendor for payments platform"]


def test_a_post_without_a_description_is_read_from_the_start_of_its_text():
    _, batch, docs = _read(_fixture("feed_content_only.xml"))
    first, second = docs
    assert "relational database & a batch job" in first.content_text and "<" not in first.content_text and "&amp;" not in first.content_text
    assert len(batch.items[0].payload["content"]) <= CONTENT_EXCERPT
    assert second.content_text == "Observability notes. A short description that wins over the body."          # a feed that gives a description is read exactly as before


def test_the_excerpt_is_capped_and_the_version_key_does_not_depend_on_it():
    long_body = "<p>" + "word " * 2000 + "</p>"
    feed = f"<rss xmlns:content='http://purl.org/rss/1.0/modules/content/' version='2.0'><channel><item><title>T</title><link>https://x.example/1</link><guid>g1</guid><content:encoded><![CDATA[{long_body}]]></content:encoded></item></channel></rss>"
    _, batch, docs = _read(feed)
    assert len(batch.items[0].payload["content"]) == CONTENT_EXCERPT
    # the key that decides "new, changed or unchanged" is built from the id, the title and the description only: adding the excerpt cannot make stored posts look changed
    assert docs[0].version_key == sha("g1", "T", None)


def test_a_company_blog_says_who_published_each_post():
    assert first_party_publishers()["blog-wise-engineering"] == "Wise" and "attention-finextra" not in first_party_publishers()
    _, _, docs = _read(_fixture("feed_content_only.xml"), source_id="blog-wise-engineering")
    assert docs[0].content_text.startswith("Published by Wise. Moving our ledger to an event-driven platform.")
    _, _, other = _read(_fixture("feed_content_only.xml"), source_id="attention-sifted")
    assert not other[0].content_text.startswith("Published by")
    _, _, explicit = _read(_fixture("feed_content_only.xml"), source_id="corporate-ir-newsrooms", publisher="Synthetic Example Bank AG")
    assert explicit[0].content_text.startswith("Published by Synthetic Example Bank AG. ")


def test_a_date_wrapped_in_a_time_tag_is_still_a_date():
    _, _, docs = _read(_fixture("feed_time_tag_date.xml"))
    assert docs[0].published_at == datetime(2026, 11, 24, 8, 26, 43, tzinfo=timezone.utc)


def test_the_feed_reader_still_obeys_robots_txt():
    from ri.sources.base import PolicyBlocked
    con = FeedConnector(_client("<rss/>", robots="User-agent: *\nDisallow: /"), "https://feed.example/rss", source_id="x")
    with pytest.raises(PolicyBlocked):
        asyncio.run(con.fetch(None, 5))


# ------------------------------------------------------------------------------------------------ naming the engineering blog and the press feed
@pytest.mark.parametrize("url, expected", [
    ("https://bank.example/engineering/feed", "engineering"),
    ("https://bank.example/en/tech/rss.xml", "engineering"),
    ("https://engineering.bank.example/feed.xml", "engineering"),
    ("https://tech.bank.example/rss", "engineering"),
    ("https://bank.example/blog/engineering/feed", "engineering"),
    ("https://bank.example/press/rss", "press"),
    ("https://bank.example/newsroom/feed.xml", "press"),
    ("https://bank.example/insights/feed", "press"),
    ("https://bank.example/pressemitteilungen/rss", "press"),
    ("https://bank.example/blog/feed", "blog"),
    ("https://bank.example/feed", None), ("https://bank.example/developers/feed", None),
    ("https://medium.com/feed/wise-engineering", "engineering"),
    ("https://medium.com/feed/ing-blog", "blog"),
])
def test_feed_classes_come_from_the_address(url, expected):
    assert classify_feed(url, "bank.example") == expected


def test_a_company_called_tech_something_is_not_an_engineering_blog_for_that_reason():
    assert classify_feed("https://techbank.example/feed", "techbank.example") is None
    assert classify_feed("https://www.techbank.example/feed", "techbank.example") is None
    assert classify_feed("https://techbank.example/feed", "techbank.example", page_hint="press") == "press"


@pytest.mark.parametrize("url, expected", [
    ("https://bank.example/engineering", True), ("https://bank.example/en/engineering/", True), ("https://bank.example/tech", True),
    ("https://bank.example/engineering/blog", True), ("https://bank.example/blog/engineering", True), ("https://engineering.bank.example/", True),
    ("https://bank.example/about/tech-blog", True), ("https://bank.example/developers", False), ("https://bank.example/engineering-careers", False),
    ("https://tech.bank.example", True), ("https://bank.example/engineering/2026/a-long-article-title", False),
    ("https://bank.example/technology-partners", False), ("https://tech.other.example/", False), ("https://other.example/engineering", False),
    ("https://bank.example/news", False), ("https://dev.bank.example/", False),
])
def test_only_the_accounts_own_engineering_section_pages_count(url, expected):
    assert is_tech_section(url, "bank.example") is expected


@pytest.mark.parametrize("url, domain, expected", [
    ("https://medium.com/wise-engineering", "wise.com", "https://medium.com/feed/wise-engineering"),
    ("https://adyen.medium.com/", "adyen.com", "https://medium.com/feed/adyen"),
    ("https://medium.com/qonto-way", "qonto.com", "https://medium.com/feed/qonto-way"),
    ("https://medium.com/building-things", "ing.pl", None),             # "ing" inside another word is not the company
    ("https://medium.com/@ceo", "wise.com", None),
    ("https://medium.com/wise-engineering/an-article-1a2b", "wise.com", None),
    ("https://medium.com/some-other-company", "wise.com", None),
    ("https://example.com/wise-engineering", "wise.com", None),
])
def test_a_medium_publication_is_taken_only_when_its_name_holds_the_accounts_own_name(url, domain, expected):
    assert medium_feed(url, domain) == expected


def test_parse_page_lists_engineering_sections_and_medium_publications_without_changing_the_rest():
    f = parse_page(_fixture("home_with_engineering.html"), "https://syntheticexample.example/", "syntheticexample.example")
    assert f["tech"] == ["https://syntheticexample.example/en/engineering"]
    assert f["medium"] == ["https://medium.com/feed/syntheticexample-labs"]
    assert f["feeds"] == ["https://syntheticexample.example/feed.xml"] and f["news"] == ["https://syntheticexample.example/en/newsroom/"]
    assert f["careers"] == ["https://syntheticexample.example/careers"]
    sub = parse_page(_fixture("home_subdomain_engineering.html"), "https://bank.example/", "bank.example")
    assert sub["tech"] == ["https://engineering.bank.example/"]


# ------------------------------------------------------------------------------------------------ discovery: what the scan finds, and what it costs
def _site(pages: dict[str, str], log: list[str], robots: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots) if robots is not None else httpx.Response(404)
        body = pages.get(request.url.path)
        return httpx.Response(200, text=body) if body is not None else httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _pages(home: str) -> dict[str, str]:
    return {"/": _fixture(home), "/en/newsroom/": _fixture("newsroom_page.html"), "/en/engineering": _fixture("engineering_page.html"),
            "/careers": "<html><body>Careers</body></html>"}


def _discover(home: str, robots: str | None = None):
    log: list[str] = []
    out = asyncio.run(discover(_site(_pages(home), log, robots), "syntheticexample.example"))
    return out, log


def _feeds(out) -> dict[str, str | None]:
    return {e["url"]: e["provider"] for e in out["endpoints"] if e["kind"] == "feed"}


def test_discovery_finds_the_engineering_feed_from_its_page_and_the_press_feed_from_the_newsroom():
    out, log = _discover("home_with_engineering.html")
    feeds = _feeds(out)
    assert feeds["https://syntheticexample.example/en/engineering/feed.xml"] == FEED_LABEL["engineering"] == "Engineering blog"
    assert feeds["https://syntheticexample.example/en/newsroom/rss"] == FEED_LABEL["press"] == "Press and insights"
    assert feeds["https://syntheticexample.example/feed.xml"] is None                       # the site's main feed is kept as it always was
    assert "/en/engineering" in log
    assert len(feeds) == 3                          # the first three feeds the site gives: the Medium publication (a plain blog) is not worth a fourth read


def test_an_engineering_publication_on_medium_is_found_from_a_link_without_any_page_read():
    home = '<html><body><a href="https://medium.com/syntheticexample-engineering">Engineering</a><a href="https://medium.com/syntheticexample-blog">Blog</a></body></html>'
    log: list[str] = []
    out = asyncio.run(discover(_site({"/": home}, log), "syntheticexample.example"))
    assert _feeds(out) == {"https://medium.com/feed/syntheticexample-engineering": "Engineering blog", "https://medium.com/feed/syntheticexample-blog": "Blog"}
    assert [x for x in log if x != "/robots.txt"] == ["/", "/feed", "/rss.xml", "/news/feed"]      # only the home page, then the ordinary feed guesses; Medium itself is not asked here


def test_detection_costs_at_most_three_requests_more_than_before():
    with_eng, log_with = _discover("home_with_engineering.html")
    without, log_without = _discover("home_without_engineering.html")
    extra_pages = len(log_with) - len(log_without)
    extra_feeds = len(_feeds(with_eng)) - len(_feeds(without))          # every stored feed is read once on each later scan
    assert extra_pages == 1 and 0 < extra_feeds <= 2
    assert extra_pages + extra_feeds <= 3
    assert len(_feeds(with_eng)) <= 5


def test_a_feed_the_home_page_already_names_costs_no_extra_page_read():
    pages = _pages("home_four_feeds.html")
    log: list[str] = []
    out = asyncio.run(discover(_site(pages, log), "syntheticexample.example"))
    assert "/engineering" not in log and "/en/engineering" not in log
    feeds = _feeds(out)
    assert feeds["https://syntheticexample.example/engineering/feed.xml"] == "Engineering blog"       # kept although it is the fourth feed listed
    assert feeds["https://syntheticexample.example/press/rss"] == "Press and insights"                # and so is the fifth, the press feed
    assert len(feeds) == 5 and sum(1 for v in feeds.values() if v is None) == 3


def test_discovery_does_not_read_an_engineering_page_that_robots_txt_closes():
    out, log = _discover("home_with_engineering.html", robots="User-agent: *\nDisallow: /en/engineering\n")
    assert "/en/engineering" not in log
    assert "Engineering blog" not in _feeds(out).values()
    assert "/en/newsroom/" in log                    # the rest is read as before


def test_a_site_wide_feed_listed_on_the_engineering_page_is_not_called_the_engineering_blog():
    pages = _pages("home_with_engineering.html")
    pages["/en/engineering"] = '<html><head><link rel="alternate" type="application/rss+xml" href="/content/rss/everything.xml"></head></html>'
    out = asyncio.run(discover(_site(pages, []), "syntheticexample.example"))
    assert "Engineering blog" not in _feeds(out).values() and "https://syntheticexample.example/content/rss/everything.xml" not in _feeds(out)


def test_a_site_linking_an_engineering_subdomain_is_followed_to_its_feed():
    pages = {"/": _fixture("home_subdomain_engineering.html")}
    sub_feed = '<html><head><link rel="alternate" type="application/atom+xml" href="/atom.xml"></head></html>'
    log: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        log.append((request.url.host, request.url.path))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "engineering.bank.example":
            return httpx.Response(200, text=sub_feed) if request.url.path == "/" else httpx.Response(404)
        return httpx.Response(200, text=pages["/"]) if request.url.path == "/" else httpx.Response(404)
    out = asyncio.run(discover(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "bank.example"))
    assert _feeds(out) == {"https://engineering.bank.example/atom.xml": "Engineering blog"}
    assert ("tech.other.example", "/") not in log                    # another company's site is never followed


# ------------------------------------------------------------------------------------------------ a scan stores the class and links the findings to the account
class _ShortNameExtractor(FakeInner):
    """The model names the account the short way, as a company does on its own pages."""
    async def extract(self, *, title, text, published_at):
        out = await super().extract(title=title, text=text, published_at=published_at)
        out["events"][0]["subject_name"] = "Synthetic Example"
        return out


ENG_FEED = ("<rss xmlns:content='http://purl.org/rss/1.0/modules/content/' version='2.0'><channel><title>Engineering</title><item><title>How we replace our core banking platform</title>"
            f"<link>https://bank.example/en/engineering/1</link><guid>e1</guid><pubDate>Tue, 24 Nov 2026 09:00:00 GMT</pubDate><content:encoded><![CDATA[<p>{TEXT}</p>]]></content:encoded></item></channel></rss>")


def _bank_site(log: list[str]):
    pages = {"/": ('<html><head><link rel="alternate" type="application/rss+xml" href="/news/feed.xml"></head><body><a href="/en/engineering">Engineering</a>'
                   '<a href="/newsroom/">News</a></body></html>'),
             "/en/engineering": '<html><head><link rel="alternate" type="application/rss+xml" href="/en/engineering/feed.xml"></head></html>',
             "/en/engineering/feed.xml": ENG_FEED,
             "/news/feed.xml": "<rss version='2.0'><channel><title>News</title></channel></rss>",
             "/newsroom/": "<html><body>Newsroom</body></html>"}
    return _site(pages, log)


def test_a_scan_stores_the_class_in_the_endpoint_and_links_a_short_name_to_the_account(conn):
    seed_registry(conn)
    budget.set_cap(conn, "anthropic", "monthly", usage.period_now(), 1_000_000, "tokens")
    org = str(conn.execute("INSERT INTO organizations(display_name, canonical_domain, country_code) VALUES ('Synthetic Example Bank AG','bank.example','DE') RETURNING id").fetchone()["id"])
    conn.commit()
    out = run_scan(conn, org, client=_bank_site([]), extractor=BudgetedExtractor(_ShortNameExtractor(ANCHOR), conn), resolver=None)
    assert out["status"] == "ok"
    eps = {r["url"]: r for r in conn.execute("select * from account_endpoints where organization_id=%s and kind='feed'", (org,)).fetchall()}
    assert eps["https://bank.example/en/engineering/feed.xml"]["provider"] == "Engineering blog" and eps["https://bank.example/en/engineering/feed.xml"]["docs_seen"] == 1
    assert eps["https://bank.example/news/feed.xml"]["provider"] == "Press and insights"          # its address says news
    ev = conn.execute("select subject_org_id, subject_name_raw from events").fetchall()
    assert ev and all(str(e["subject_org_id"]) == org and e["subject_name_raw"] == "Synthetic Example" for e in ev)        # not left unmatched


def test_a_feed_found_earlier_without_a_class_gets_it_on_the_next_discovery_and_never_loses_it(conn):
    seed_registry(conn)
    org = str(conn.execute("INSERT INTO organizations(display_name, canonical_domain, country_code) VALUES ('Synthetic Example Bank AG','bank.example','DE') RETURNING id").fetchone()["id"])
    conn.execute("INSERT INTO account_endpoints(organization_id, kind, url) VALUES (%s,'feed','https://bank.example/en/engineering/feed.xml')", (org,))
    conn.commit()
    run_scan(conn, org, client=_bank_site([]), force_discover=True)
    assert conn.execute("select provider from account_endpoints where organization_id=%s and url='https://bank.example/en/engineering/feed.xml'", (org,)).fetchone()["provider"] == "Engineering blog"
    conn.execute("UPDATE account_endpoints SET provider='Press and insights' WHERE organization_id=%s AND url='https://bank.example/en/engineering/feed.xml'", (org,))
    conn.commit()
    run_scan(conn, org, client=_bank_site([]), force_discover=True)
    assert conn.execute("select provider from account_endpoints where organization_id=%s and url='https://bank.example/en/engineering/feed.xml'", (org,)).fetchone()["provider"] == "Press and insights"


@pytest.mark.parametrize("account, subject, expected", [
    ("Nordea Bank Abp", "Nordea", True), ("Wise", "Wise plc", True), ("Synthetic Example Bank AG", "Synthetic Example", True),
    ("Santander Consumer Bank Polska", "Santander", False),               # a parent is not the account
    ("Nordea Bank Abp", "Nordea Finance", False), ("Nordea Bank Abp", "Danske Bank", False), ("Nordea Bank Abp", "", False), ("Nordea Bank Abp", None, False)])
def test_a_subject_is_the_account_only_when_it_is_the_account_written_short(account, subject, expected):
    assert same_company(account, subject) is expected


def test_the_first_party_resolver_leaves_every_other_name_to_the_normal_check():
    seen: list[str] = []

    def base(name: str):
        seen.append(name)
        return "other-org"
    resolve = first_party_resolver("acct-1", "Nordea Bank Abp", base)
    assert resolve("Nordea") == "acct-1" and resolve("Backbase") == "other-org" and seen == ["Backbase"]


def test_the_scan_label_is_shown_by_the_account_page_as_it_is():
    # the account page prints `Feed (<provider>)`; the labels are the plain words a seller reads
    assert set(FEED_LABEL.values()) == {"Engineering blog", "Press and insights", "Blog"}
