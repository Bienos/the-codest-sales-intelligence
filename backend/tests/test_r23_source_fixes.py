"""Source fixes of 25 Nov 2026: the Arbeitnow job board (fixture recorded from the live API), Content Signals in robots.txt,
the UK procurement APIs waiting out a short 429, and the registry picking up corrected addresses on an existing database."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import yaml

from ri.sources.base import PolicyBlocked, RateLimited

FIX = json.loads((Path(__file__).parent / "fixtures" / "arbeitnow_sample.json").read_text(encoding="utf-8"))
JOBS = json.loads((Path(__file__).parent / "fixtures" / "jobboards_sample.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)
ROBOTS_OK = "User-agent: *\nDisallow:\nDisallow: /*?__hstc\n"


def _arbeitnow_client(seen: list, robots: str = ROBOTS_OK):
    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        assert req.url.path == "/api/job-board-api" and req.headers["User-Agent"].startswith("CodestRevenueIntelligence/")
        return httpx.Response(200, json=FIX["page1"] if req.url.params.get("page") == "1" else FIX["page2"])
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _no_wait(log: list):
    async def sleep(s):
        log.append(s)
    return sleep


# ------------------------------------------------------------------------------------------------ Arbeitnow
def test_arbeitnow_keeps_engineering_roles_at_financial_employers_in_our_markets():
    from ri.sources.jobs_market import ArbeitnowConnector
    seen, waits = [], []
    c = ArbeitnowConnector(_arbeitnow_client(seen), now_fn=lambda: NOW)
    c.sleep = _no_wait(waits)
    b1 = asyncio.run(c.fetch(None, 100))
    assert [i.external_id for i in b1.items] == ["lead-platform-engineer-milton-keynes-227352", "devsecops-engineer-frankfurt-am-main-26544"]
    assert b1.next_cursor == "page=2" and b1.has_more and b1.complete_listing is False
    u = b1.quota_usage          # seen versus kept is recorded, with the reason for every drop
    assert (u["hits_seen"], u["kept_financial"], u["dropped_agency"], u["dropped_not_engineering"], u["dropped_other_country"]) == (6, 2, 2, 1, 1)
    b2 = asyncio.run(c.fetch(b1.next_cursor, 100))
    assert [i.external_id for i in b2.items] == ["product-frontend-engineer-zurich-295369"]
    assert b2.next_cursor is None and not b2.has_more          # the listing is finished: the next run starts again from page 1
    assert b2.quota_usage["dropped_not_financial"] == 2         # an ordinary employer, and one whose benefits text mentions banking and payments
    assert waits == [1.5]                                         # a pause before every page after the first
    assert sum(1 for r in seen if r.url.path == "/robots.txt") == 1


def test_arbeitnow_postings_have_the_same_shape_as_jobtech_and_link_back_to_arbeitnow():
    from ri.sources.jobs_market import ArbeitnowConnector, JobTechConnector
    c = ArbeitnowConnector(_arbeitnow_client([]), now_fn=lambda: NOW)
    c.sleep = _no_wait([])
    b = asyncio.run(c.fetch(None, 100))
    d = c.canonicalize(b.items[0])
    s = d.structured
    jt = JobTechConnector(httpx.AsyncClient(), now_fn=lambda: NOW)
    from ri.sources.base import RawItem
    hit = JOBS["jobtech"]["hits"][0]
    assert set(s) == set(jt.canonicalize(RawItem(str(hit["id"]), None, hit)).structured)
    assert d.kind == "job_posting" and s["provider"] == "arbeitnow" and s["board"] == "de" and s["employer_name"] == "Allica Bank"
    assert s["country"] == "GB" and s["employment_type"] == "permanent" and s["posted_at"].tzinfo is not None and s["open_status"] == "open"
    assert d.canonical_url == s["url"] and httpx.URL(s["url"]).host in {"www.arbeitnow.com", "www.arbeitnow.co.uk"}          # the link back Arbeitnow asks for
    swiss = c.canonicalize(asyncio.run(c.fetch("page=2", 100)).items[0]).structured
    assert swiss["country"] == "CH" and swiss["employment_type"] == "permanent"          # "Indefinite contract (unbefristeter Arbeitsvertrag)" is permanent


def test_arbeitnow_drops_old_postings_and_stops_when_robots_txt_refuses():
    from ri.sources.jobs_market import ArbeitnowConnector
    c = ArbeitnowConnector(_arbeitnow_client([]), now_fn=lambda: datetime(2027, 2, 1, tzinfo=timezone.utc))
    b = asyncio.run(c.fetch(None, 100))
    assert b.items == [] and b.quota_usage["dropped_old"] == 2
    blocked = ArbeitnowConnector(_arbeitnow_client([], robots="User-agent: *\nDisallow: /api/\n"), now_fn=lambda: NOW)
    with pytest.raises(PolicyBlocked):
        asyncio.run(blocked.fetch(None, 100))
    assert asyncio.run(blocked.health_check()).ok is False


def test_arbeitnow_helpers_read_free_text_job_types_locations_and_german_titles():
    from ri.events.classify import engineering_relevant
    from ri.sources.jobs_market import CLIENT_ADVERT, country_of, employment_from_types, financial_employer
    assert CLIENT_ADVERT.search("Unser Kunde ist eine Privatbank in Zürich") and CLIENT_ADVERT.search("Our client is an international bank")
    assert not CLIENT_ADVERT.search("We have grown our client base across Europe") and not CLIENT_ADVERT.search("Sie arbeiten eng mit unseren Kunden")
    assert employment_from_types(["Unbefristeter arbeitsvertrag", "berufserfahren"], "x") == "permanent"
    assert employment_from_types(["fulltime fixed term"], "x") == "contract" and employment_from_types(["Befristeter arbeitsvertrag"], "x") == "contract"
    assert employment_from_types(["Selbstständiger / freelancer", "freelance"], "x") == "freelance"
    assert employment_from_types(["Werkstudent"], "x") == "internship" and employment_from_types([], "Senior Engineer") == "unknown"
    assert country_of("Zurich, Zürich, Switzerland") == "CH" and country_of("Wien") == "AT" and country_of("Homeoffice") == "DE"
    assert country_of("Belfast, Northern Ireland, Remote") == "GB" and country_of("Paris, France") == "other" and country_of("Remote") is None
    assert engineering_relevant("Java Entwickler:in (m/w/d)") and engineering_relevant("Senior Softwareentwickler (m/w/d)")
    assert engineering_relevant("Wirtschaftsinformatiker/in (m/w/d)") and engineering_relevant("Applikations-Entwickler SAP BTP")
    assert not engineering_relevant("Hardwareentwickler - Elektronik") and not engineering_relevant("Produktentwickler & Designer (m/w/d)")
    assert financial_employer("Commerzbank AG", "Backend Engineer", "") == "name" and financial_employer("Sparkasse KölnBonn", "Entwickler", "") == "name"
    assert financial_employer("Acme GmbH", "Backend Engineer", "Acme builds robots. " + "x " * 700 + "Benefits: banking app, payments, lending") is None


def test_arbeitnow_runs_through_the_pipeline_and_its_jobs_become_hiring_events(conn):
    from ri.events.structured import emit_job_event
    from ri.ingest.pipeline import run_source
    from ri.sources.factory import build_connector
    from ri.sources.jobs_market import ArbeitnowConnector
    from ri.sources.registry import seed_registry
    seed_registry(conn)
    conn.commit()
    assert isinstance(build_connector(conn, "jobs-arbeitnow-de"), ArbeitnowConnector)
    c = ArbeitnowConnector(_arbeitnow_client([]), now_fn=lambda: NOW)
    c.sleep = _no_wait([])
    res = run_source(conn, c, allow_pending=True, now_fn=lambda: NOW)
    assert res.status == "success" and res.new == 3, res.error
    rows = {r["employer_name"]: r for r in conn.execute("select employer_name, provider, board, employment_type from job_postings").fetchall()}
    assert set(rows) == {"Allica Bank", "spotixx GmbH", "ServiceHunter AG - quitt, quinn"} and rows["Allica Bank"]["provider"] == "arbeitnow"
    assert conn.execute("select cursor_value from source_cursors where source_id='jobs-arbeitnow-de'").fetchone()["cursor_value"] is None
    usage = conn.execute("select usage from monitoring_runs where id=%s", (res.run_id,)).fetchone()["usage"]
    assert usage["pages"] == 2 and usage["hits_seen"] == 9 and usage["kept_financial"] == 3
    for kind, rid, change in res.structured_changes:
        emit_job_event(conn, rid, change)
    ev = conn.execute("select event_type, attrs from events where subject_name_raw='Allica Bank'").fetchone()
    assert ev["event_type"] == "engineering_job_opened" and ev["attrs"]["role_of_subject"] == "bank"


def test_jobtech_postings_can_be_stored_its_epoch_timestamp_becomes_a_real_time(conn):
    from ri.ingest.pipeline import run_source
    from ri.sources.jobs_market import JobTechConnector
    from ri.sources.registry import seed_registry
    seed_registry(conn)
    conn.commit()
    page = JOBS["jobtech"]
    cl = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=page if r.url.params.get("q") == "bank" else {"hits": []})))
    res = run_source(conn, JobTechConnector(cl, queries=["bank"], now_fn=lambda: NOW), allow_pending=True, now_fn=lambda: NOW)
    assert res.status == "success" and res.new >= 1, res.error          # before the fix the epoch number was sent as text and the run failed
    row = conn.execute("select updated_at_source from job_postings where provider='jobtech' order by updated_at_source limit 1").fetchone()
    assert row["updated_at_source"].year == 2026


# ------------------------------------------------------------------------------------------------ robots.txt Content Signals
FINEXTRA_STYLE = ("# As a condition of accessing this website, you agree to abide by the following content signals ...\n\n"
                  "User-agent: *\nContent-Signal: search=yes,ai-train=no,use=reference\nAllow: /\n\nUser-agent: GPTBot\nDisallow: /\n")


def _robots_client(robots: str):
    feed = "<rss><channel><item><title>Bank selects a new core platform</title><guid>1</guid><link>https://news.example/1</link></item></channel></rss>"
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=robots if r.url.path == "/robots.txt" else feed)))


def test_content_signal_lines_are_parsed_per_group_and_globally():
    from ri.sources.webwatch import content_signals
    assert content_signals(FINEXTRA_STYLE) == {"search": "yes", "ai-train": "no", "use": "reference"}
    assert content_signals("Content-Signal: ai-input=no\nUser-agent: *\nContent-Signal: search=yes\nAllow: /") == {"ai-input": "no", "search": "yes"}
    assert content_signals("User-agent: GPTBot\nContent-Signal: ai-input=no\n\nUser-agent: *\nAllow: /") == {}       # someone else's group
    ours = "User-agent: CodestRevenueIntelligence\nContent-Signal: ai-input=yes\n\nUser-agent: *\nContent-Signal: ai-input=no\n"
    assert content_signals(ours) == {"ai-input": "yes"}                                                              # our own group wins over *
    assert content_signals("User-agent: *\nAllow: /\n\n# signals\nContent-Signal: AI-Input = No , search=yes\n") == {"ai-input": "no", "search": "yes"}


@pytest.mark.parametrize("robots,ok", [
    (FINEXTRA_STYLE, True),                                                                        # ai-train=no alone: we never train, so reading is fine
    ("User-agent: *\nContent-Signal: search=yes, ai-input=no\nAllow: /\n", False),
    ("Content-Signal: ai-input=no\n\nUser-agent: *\nAllow: /\n", False),                           # global line before any group
    ("User-agent: GPTBot\nContent-Signal: ai-input=no\n\nUser-agent: *\nAllow: /\n", True),
])
def test_ai_input_no_means_we_may_not_read_the_page(robots, ok):
    from ri.sources.webwatch import FeedConnector, RobotsCache
    rc = RobotsCache()
    cl = _robots_client(robots)
    assert asyncio.run(rc.allowed(cl, "https://news.example/feed")) is ok
    c = FeedConnector(_robots_client(robots), "https://news.example/feed", source_id="attention-x")
    if ok:
        assert len(asyncio.run(c.fetch(None, 5)).items) == 1
    else:
        with pytest.raises(PolicyBlocked, match="ai-input=no"):
            asyncio.run(c.fetch(None, 5))


# ------------------------------------------------------------------------------------------------ UK procurement: page pause and one retry after a short 429
def _ocds_client(answers: list, seen: list):
    rel = {"ocid": "ocds-b5fd17-aaaa1111", "date": "2026-11-20T10:00:00Z", "tag": ["tender"], "tender": {"title": "Core payments platform modernisation",
                                                                                                      "items": [{"classification": {"id": "72000000"}}]}}

    def handler(req):
        seen.append(str(req.url))
        a = answers.pop(0) if answers else 200
        if a == 200:
            return httpx.Response(200, json={"releases": [rel], "links": {}})
        return httpx.Response(429, headers={} if a is None else {"Retry-After": str(a)})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("cls_name", ["ContractsFinderConnector", "FindTenderConnector"])
def test_uk_connectors_pause_between_pages_and_wait_out_a_short_429_once(cls_name):
    import ri.sources.procurement as p
    seen, waits = [], []
    c = getattr(p, cls_name)(_ocds_client([5, 200], seen), now_fn=lambda: NOW)
    c.sleep = _no_wait(waits)
    b = asyncio.run(c.fetch("https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search?cursor=abc", 100))
    assert [i.external_id for i in b.items] == ["ocds-b5fd17-aaaa1111"]
    assert waits == [1.5, 5.0] and len(seen) == 2 and seen[0] == seen[1]          # the page pause, then the provider's Retry-After, then the same page again
    seen2, waits2 = [], []
    c2 = getattr(p, cls_name)(_ocds_client([None, 200], seen2), now_fn=lambda: NOW)
    c2.sleep = _no_wait(waits2)
    assert len(asyncio.run(c2.fetch(None, 100)).items) == 1 and waits2 == [10.0]    # no Retry-After header: a short default wait


@pytest.mark.parametrize("answers,expect_requests", [([120, 200], 1), ([5, 5, 200], 2)])
def test_uk_connectors_raise_on_a_long_wait_or_a_second_429(answers, expect_requests):
    from ri.sources.procurement import ContractsFinderConnector, FindTenderConnector
    for cls in (ContractsFinderConnector, FindTenderConnector):
        seen = []
        c = cls(_ocds_client(list(answers), seen), now_fn=lambda: NOW)
        c.sleep = _no_wait([])
        with pytest.raises(RateLimited):
            asyncio.run(c.fetch(None, 100))
        assert len(seen) == expect_requests


def test_retry_after_is_read_as_seconds_or_as_a_date():
    from email.utils import format_datetime
    from datetime import timedelta
    from ri.sources.http import retry_after_seconds
    assert retry_after_seconds("7") == 7.0 and retry_after_seconds(None) is None and retry_after_seconds("soon") is None
    when = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=20), usegmt=True)
    assert 10 <= retry_after_seconds(when) <= 21


# ------------------------------------------------------------------------------------------------ registry
def test_sources_yaml_has_no_duplicate_ids_and_every_connection_note_names_a_source():
    rules = Path(__file__).resolve().parents[1] / "rules"
    ids = [s["source_id"] for s in yaml.safe_load((rules / "sources.yaml").read_text(encoding="utf-8"))["sources"]]
    assert len(ids) == len(set(ids))
    notes = yaml.safe_load((rules / "connections.yaml").read_text(encoding="utf-8"))["connections"]
    assert set(notes) <= set(ids)


def test_seed_on_an_existing_database_picks_up_new_sources_addresses_and_a_corrected_domain(conn):
    from ri.sources.factory import build_connector
    from ri.sources.registry import seed_registry
    from ri.sources.webwatch import FeedConnector, PageWatchConnector
    seed_registry(conn)
    # the database as it was before this change
    conn.execute("update sources set canonical_domain='ebaclearing.eu', config='{\"note\": \"event programme address to be added\"}' where source_id='attention-ebaday'")
    conn.execute("update sources set collection_method='change_monitor', config='{\"note\": \"no feed found yet\"}' where source_id='attention-deutschestartups'")
    conn.execute("delete from source_health where source_id in ('jobs-arbeitnow-de','attention-fintechnewsch')")
    conn.execute("delete from sources where source_id in ('jobs-arbeitnow-de','attention-fintechnewsch')")
    # an operator's own address and a certified source are never changed
    conn.execute("update sources set canonical_domain='old.example', config='{\"url\": \"https://old.example/feed\", \"url_basis\": \"set by admin@x\"}' where source_id='attention-breakit'")
    conn.execute("update sources set canonical_domain='old.example', status='active' where source_id='attention-fintechglobal'")
    conn.commit()
    assert seed_registry(conn) == 2
    q = lambda sid: conn.execute("select canonical_domain, collection_method, status, config from sources where source_id=%s", (sid,)).fetchone()   # noqa: E731
    assert q("attention-ebaday")["canonical_domain"] == "abe-eba.eu" and q("attention-ebaday")["config"]["url"] == "https://www.abe-eba.eu/ebaday/"
    assert q("attention-deutschestartups")["collection_method"] == "feed" and q("attention-deutschestartups")["config"]["url"] == "https://www.deutsche-startups.de/feed/"
    assert q("attention-fintechnewsch")["status"] == "pending_connector_validation" and q("jobs-arbeitnow-de")["canonical_domain"] == "arbeitnow.com"
    assert q("attention-breakit")["canonical_domain"] == "old.example" and q("attention-breakit")["config"]["url"] == "https://old.example/feed"
    assert q("attention-fintechglobal")["canonical_domain"] == "old.example"
    assert isinstance(build_connector(conn, "attention-deutschestartups"), FeedConnector) and isinstance(build_connector(conn, "attention-ebaday"), PageWatchConnector)
    assert conn.execute("select count(*) c from sources where source_id='procurement-ezamowienia'").fetchone()["c"] == 1
