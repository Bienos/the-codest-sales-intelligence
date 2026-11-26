"""More career systems (Workday, Personio, Teamtailor, Recruitee, Workable): detection from addresses and careers pages, the readers (fixtures with the shapes recorded
from the live services; company names are invented), pagination caps, closed adverts, classification, de-duplication, and clean failures. No test touches the network."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from ri.sources import ats_more
from ri.sources.account_scan import board_url, boards_in_links, discover, parse_page, run_scan, ats_source
from ri.sources.ats_more import (PersonioConnector, RecruiteeConnector, TeamtailorConnector, WorkableConnector, WorkdayConnector, detect_board,
                                 detect_unread_board, workday_job_id, workday_posted_at)
from ri.sources.base import NotFound, PolicyBlocked, RateLimited, SchemaDrift
from ri.sources.factory import ats_connectors
from ri.sources.registry import seed_registry

FIX = Path(__file__).parent / "fixtures" / "ats_more"
SHAPES = json.loads((FIX / "shapes.json").read_text())
PERSONIO = (FIX / "personio.xml").read_text()
TEAMTAILOR = (FIX / "teamtailor.rss").read_text()
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
WD_BOARD = "harbourline.wd3/Careers"


async def no_sleep(_seconds):
    return None


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def make(cls, board, handler, **kw):
    kw.setdefault("sleep", no_sleep)
    kw.setdefault("now_fn", lambda: NOW)
    kw.setdefault("employer_name", "Test Employer")
    c = cls(client(handler), board, **kw)
    if isinstance(c, WorkdayConnector):
        c.page_pause = 0
    return c


# ------------------------------------------------------------------------------------------------------------ detection
@pytest.mark.parametrize("url,expected", [
    # Workday: tenant, data centre and site, with or without a language prefix, deep links, the API address, upper case and a port
    ("https://harbourline.wd3.myworkdayjobs.com/en-US/Careers", ("workday", "harbourline.wd3/Careers")),
    ("https://harbourline.wd3.myworkdayjobs.com/Careers", ("workday", "harbourline.wd3/Careers")),
    ("https://harbourline.wd3.myworkdayjobs.com/de-DE/Careers/job/Frankfurt/Engineer_R12", ("workday", "harbourline.wd3/Careers")),
    ("https://HarbourLine.WD3.MyWorkdayJobs.com:443/Careers?q=java#x", ("workday", "harbourline.wd3/Careers")),
    ("http://harbourline.wd12.myworkdayjobs.com/fr/External_Career_Site", ("workday", "harbourline.wd12/External_Career_Site")),
    ("https://harbourline.wd103.myworkdayjobs.com/wday/cxs/harbourline/Careers/jobs", ("workday", "harbourline.wd103/Careers")),
    ("https://north-bridge.wd1.myworkdayjobs.com/NB_Careers/job/x/y_R1", ("workday", "north-bridge.wd1/NB_Careers")),
    # Workday look-alikes and pages that are not a career site
    ("https://harbourline.wd3.myworkdayjobs.com/", None),
    ("https://harbourline.wd3.myworkdayjobs.com/en-US", None),
    ("https://harbourline.wd3.myworkdayjobs.com/en-US/login", None),
    ("https://harbourline.wd3.myworkdayjobs.com/robots.txt", None),
    ("https://wd3.myworkdayjobs.com/Careers", None),
    ("https://www.myworkdayjobs.com/Careers", None),
    ("https://harbourline.wd3.myworkdayjobs-impl.com/Careers", None),
    ("https://harbourline.myworkdayjobs.com/Careers", None),
    ("https://evil.example/redirect?to=harbourline.wd3.myworkdayjobs.com/Careers", None),
    ("https://harbourline.wd3.myworkdayjobs.com.evil.example/Careers", None),
    # Personio (.de and .com), Teamtailor, Recruitee, Workable
    ("https://brightwater.jobs.personio.de/", ("personio", "brightwater")),
    ("https://brightwater.jobs.personio.com/job/7001?language=en", ("personio", "brightwater")),
    ("https://jobs.personio.de/", None),
    ("https://www.jobs.personio.de/", None),
    ("https://brightwater.jobs.personio.de.evil.example/", None),
    ("https://quillon.teamtailor.com/jobs/8786914-senior-backend-engineer", ("teamtailor", "quillon")),
    ("https://career.teamtailor.com/", ("teamtailor", "career")),
    ("https://www.teamtailor.com/en/", None),
    ("https://support.teamtailor.com/", None),
    ("https://marlow-credit.recruitee.com/o/backend-engineer", ("recruitee", "marlow-credit")),
    ("https://api.recruitee.com/", None),
    ("https://www.recruitee.com/", None),
    ("https://apply.workable.com/foxglove/", ("workable", "foxglove")),
    ("https://apply.workable.com/foxglove/j/A1B2C3D4E5/", ("workable", "foxglove")),
    ("https://apply.workable.com/Foxglove-Capital", ("workable", "foxglove-capital")),
    ("https://apply.workable.com/j/A1B2C3D4E5/", None),
    ("https://apply.workable.com/api/v1/widget/accounts/foxglove", None),
    ("https://apply.workable.com/", None),
    ("https://jobs.workable.com/search", None),
    ("https://www.workable.com/", None),
    # not addresses
    ("mailto:jobs@harbourline.example", None),
    ("javascript:void(0)", None),
    ("", None),
])
def test_detection_of_career_systems_from_addresses(url, expected):
    assert detect_board(url) == expected


def test_join_is_recognised_but_never_read():
    assert detect_unread_board("https://join.com/companies/alpenbank/12345-java-dev") == ("join", "alpenbank")
    assert detect_unread_board("https://alpenbank.join.com/") == ("join", "alpenbank")
    assert detect_unread_board("https://www.join.com/") is None and detect_unread_board("https://join.com/de/jobs") is None
    assert "join" not in ats_connectors() and detect_board("https://join.com/companies/alpenbank") is None


def test_board_addresses_and_source_ids():
    assert board_url("workday", WD_BOARD) == "https://harbourline.wd3.myworkdayjobs.com/Careers"
    assert board_url("personio", "brightwater") == "https://brightwater.jobs.personio.de"
    assert board_url("teamtailor", "quillon") == "https://quillon.teamtailor.com/jobs"
    assert board_url("recruitee", "marlow") == "https://marlow.recruitee.com" and board_url("workable", "foxglove") == "https://apply.workable.com/foxglove/"
    assert board_url("greenhouse", "acme") == "https://boards.greenhouse.io/acme"          # the old boards keep their addresses
    for prov, board in (("workday", WD_BOARD), ("personio", "brightwater"), ("teamtailor", "quillon"), ("recruitee", "marlow"), ("workable", "foxglove")):
        assert detect_board(board_url(prov, board)) == (prov, board)                       # an address we store is recognised again
        assert ats_source(prov) == f"ats-{prov}"


def test_every_reader_is_registered_with_the_scan():
    cls = ats_connectors()
    assert set(cls) >= {"greenhouse", "lever", "lever_eu", "ashby", "smartrecruiters", "workday", "personio", "teamtailor", "recruitee", "workable"}
    assert all(c.USES_ROBOTS for p, c in cls.items() if p in ats_more.MORE_CONNECTORS)


def test_careers_page_links_iframes_and_scripts_are_read_for_every_system():
    html = ('<a href="https://harbourline.wd3.myworkdayjobs.com/en-US/Careers">Search jobs</a>'
            '<iframe src="https://marlow.recruitee.com/"></iframe>'
            '<a href="/jobs/x">local</a><a href="https://brightwater.jobs.personio.de/?language=de">DE</a>'
            '<script src="https://apply.workable.com/foxglove/"></script>'
            '<a href="https://quillon.teamtailor.com/jobs">TT</a><a href="https://join.com/companies/alpenbank">JOIN</a>'
            '<a href="https://evil.example/?u=https://marlow2.recruitee.com/">trap</a>')
    f = parse_page(html, "https://bank.example/careers", "bank.example")
    assert set(f["ats"]) == {("workday", WD_BOARD), ("recruitee", "marlow"), ("personio", "brightwater"), ("workable", "foxglove"), ("teamtailor", "quillon")}
    assert f["unread"] == [("join", "alpenbank")]
    # the page we already read each scan gives the same answer without another request, and only for systems we can read
    links = ["https://harbourline.wd3.myworkdayjobs.com/en-US/Careers", "https://join.com/companies/alpenbank", "/relative", "https://boards.eu.greenhouse.io/x", "https://jobs.lever.co/acme"]
    got = boards_in_links(links, "https://bank.example/careers", set(ats_connectors()))
    assert got == [("workday", WD_BOARD), ("lever", "acme")]


def test_discover_follows_a_careers_page_to_the_companys_jobs_site_and_stops_at_four_pages():
    seen = []
    pages = {"https://bank.example/": '<a href="/careers">Careers</a>',
             "https://bank.example/careers": '<a href="https://jobs.bank.example/">Open roles</a><a href="https://careers.bank.example/x">More</a>',
             "https://jobs.bank.example/": '<a href="https://harbourline.wd3.myworkdayjobs.com/en-US/Careers">Search</a>'}

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(str(req.url))
        return httpx.Response(200, text=pages[str(req.url)]) if str(req.url) in pages else httpx.Response(404)
    out = run(discover(client(handler), "bank.example"))
    assert [e["board"] for e in out["endpoints"] if e["kind"] == "job_board"] == [WD_BOARD]
    assert "https://careers.bank.example/x" not in seen          # found a board on the second page: no need to read further

    chain = {f"https://bank.example/c{i}": "".join(f'<a href="https://bank.example/c{j}">c</a>' for j in range(i + 1, i + 3)) for i in range(0, 12)}
    chain["https://bank.example/"] = '<a href="/careers">Careers</a>'
    chain["https://bank.example/careers"] = '<a href="https://bank.example/careers/c0">x</a>'
    seen.clear()
    run(discover(client(lambda r: httpx.Response(404) if r.url.path == "/robots.txt" else (seen.append(str(r.url)) or httpx.Response(200, text=chain.get(str(r.url), "<html></html>")))), "bank.example"))
    import re
    assert len([u for u in seen if re.search(r"/(careers|c\d+)$", u)]) <= 4                      # never more than four careers pages, however long the chain


def test_discover_finds_a_workday_board_on_the_second_careers_page_and_reports_join():
    pages = {"https://bank.example/": '<a href="/careers">Careers</a><a href="https://careers.bank.example/">Careers UK</a>',
             "https://bank.example/careers": "<html>no links</html>",
             "https://careers.bank.example/": '<a href="https://harbourline.wd3.myworkdayjobs.com/en-US/Careers">Jobs</a><a href="https://join.com/companies/alpenbank">Swiss roles</a>'}

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=pages[str(req.url)]) if str(req.url) in pages else httpx.Response(404)
    out = run(discover(client(handler), "bank.example"))
    jobs = [e for e in out["endpoints"] if e["kind"] == "job_board"]
    assert jobs == [{"kind": "job_board", "url": "https://harbourline.wd3.myworkdayjobs.com/Careers", "provider": "workday", "board": WD_BOARD}]
    assert any("JOIN (alpenbank)" in n and "not read" in n for n in out["notes"])


# ------------------------------------------------------------------------------------------------------------ Workday
class Workday:
    """A fake Workday tenant. `total` postings, every third one an engineering role; 20 a page; `total` only on the first page, and an offset past the end wraps to page 1 (as live)."""

    def __init__(self, total=5, postings=None, *, wrap=True, drop_one=False, robots=None, detail_status=200, list_status=200, list_body=None):
        self.postings = postings if postings is not None else [
            {"title": f"Software Engineer {i}" if i % 3 == 0 else f"Branch Cashier {i}", "externalPath": f"/job/City/{'Software-Engineer' if i % 3 == 0 else 'Branch-Cashier'}-{i}_R{2000 + i}",
             "locationsText": "City", "postedOn": "Posted Today", "bulletFields": [f"R{2000 + i}"]} for i in range(total)]
        self.total, self.wrap, self.drop_one, self.robots, self.detail_status, self.list_status, self.list_body = len(self.postings), wrap, drop_one, robots, detail_status, list_status, list_body
        self.calls: list[tuple[str, str, int | None]] = []

    def __call__(self, req):
        u = req.url
        if u.path == "/robots.txt":
            return httpx.Response(200, text=self.robots) if self.robots else httpx.Response(404)
        assert u.host == "harbourline.wd3.myworkdayjobs.com"
        if req.method == "POST" and u.path == "/wday/cxs/harbourline/Careers/jobs":
            body = json.loads(req.content)
            assert body["limit"] <= 20 and body["searchText"] == "" and body["appliedFacets"] == {}
            self.calls.append(("POST", u.path, body["offset"]))
            if self.list_status != 200:
                return httpx.Response(self.list_status, json={"errorCode": "x", "httpStatus": self.list_status})
            if self.list_body is not None:
                return httpx.Response(200, content=self.list_body)
            off = body["offset"] if (body["offset"] < len(self.postings) or not self.wrap) else 0
            page = self.postings[off: off + body["limit"]]
            if self.drop_one and off == 20:
                page = page[1:]
            return httpx.Response(200, json={"total": self.total if off == 0 else 0, "jobPostings": page, "facets": [], "userAuthenticated": False})
        if req.method == "GET" and u.path.startswith("/wday/cxs/harbourline/Careers/job/"):
            self.calls.append(("GET", u.path, None))
            if self.detail_status != 200:
                return httpx.Response(self.detail_status, json={"httpStatus": self.detail_status}, headers={"Retry-After": "86400"} if self.detail_status == 429 else {})
            info = dict(SHAPES["workday_detail"]["jobPostingInfo"])
            return httpx.Response(200, json={**SHAPES["workday_detail"], "jobPostingInfo": info})
        return httpx.Response(404)


def test_workday_reads_every_page_once_keeps_only_engineering_and_asks_details_only_for_those():
    site = Workday(total=45)                                     # 3 pages: 0, 20, 40
    c = make(WorkdayConnector, WD_BOARD, site)
    b = run(c.fetch(None, 500))
    assert [o for m, _, o in site.calls if m == "POST"] == [0, 20, 40]          # never an offset past the end (it would wrap to page 1)
    assert b.complete_listing is True and b.warnings == [] and b.partial_result is False
    eng = [i for i in range(45) if i % 3 == 0]
    assert len(b.items) == len(eng) == 15
    assert {i.payload["_title"] for i in b.items} == {f"Software Engineer {i}" for i in eng}
    details = [p for m, p, _ in site.calls if m == "GET"]
    assert len(details) == 15 and all("Software-Engineer" in p for p in details)         # no detail call for a cashier
    assert b.quota_usage["pages"] == 3 and b.quota_usage["postings_seen"] == 45 and b.quota_usage["postings_kept"] == 15 and b.quota_usage["board_total"] == 45
    assert b.quota_usage["requests"] == 3 + 15


def test_workday_detail_calls_are_capped_but_every_engineering_posting_is_kept():
    site = Workday(total=45)
    c = make(WorkdayConnector, WD_BOARD, site)
    c.DETAIL_CAP = 4
    b = run(c.fetch(None, 500))
    assert len(b.items) == 15 and len([1 for m, _, _ in site.calls if m == "GET"]) == 4
    with_text = [i for i in b.items if i.payload["_tech"]]
    assert len(with_text) == 4                                                 # only those with a detail call have keywords
    assert b.complete_listing is True


def test_workday_page_cap_stops_the_read_says_so_and_never_claims_a_complete_listing():
    site = Workday(total=100)
    c = make(WorkdayConnector, WD_BOARD, site)
    c.MAX_PAGES = 2
    b = run(c.fetch(None, 500))
    assert [o for m, _, o in site.calls if m == "POST"] == [0, 20]
    assert b.complete_listing is False and b.partial_result is True and "40 of 100" in b.warnings[0] and "no advert is closed" in b.warnings[0]
    assert len(b.items) == len([i for i in range(40) if i % 3 == 0])


def test_workday_time_budget_stops_the_read_without_a_complete_listing():
    site = Workday(total=100)
    ticks = iter(range(0, 10_000, 100))                  # every look at the clock is 100 s later: the 170 s budget is gone after two looks
    c = make(WorkdayConnector, WD_BOARD, site, clock=lambda: next(ticks))
    b = run(c.fetch(None, 500))
    assert b.complete_listing is False and len([1 for m, _, _ in site.calls if m == "POST"]) < 5 and b.warnings


def test_workday_slows_down_for_the_rest_of_the_read_after_a_rate_limit_and_still_finishes():
    inner = Workday(total=100)
    state = {"refused": False}
    slept: list[float] = []

    async def sleep(s):
        slept.append(s)

    def handler(req):
        if req.method == "POST" and json.loads(req.content)["offset"] == 40 and not state["refused"]:
            state["refused"] = True
            return httpx.Response(429, headers={"Retry-After": "4"}, text="Too many requests")
        return inner(req)
    c = make(WorkdayConnector, WD_BOARD, handler, sleep=sleep)
    c.page_pause = 0.6
    b = run(c.fetch(None, 500))
    assert b.complete_listing is True and len(b.items) == len([i for i in range(100) if i % 3 == 0])
    assert 4.0 in slept and c.page_pause > 0.6 and slept.index(4.0) < len(slept) - 1          # waited as asked, then paced more slowly
    assert max(slept[slept.index(4.0) + 1:]) >= 1.5


def test_workday_reads_a_whole_board_over_one_connection_when_it_runs_on_the_safe_client(monkeypatch):
    """The safe client opens a fresh connection per request and Workday's network answers a burst of those with 429; a board read uses ONE connection,
    still opened by public_client (public addresses only), and the host is checked first."""
    from ri.sources import safe
    site = Workday(total=45)
    opened, checked = [], []

    def fake_public_client(timeout=30, **kw):
        opened.append(timeout)
        return httpx.AsyncClient(transport=httpx.MockTransport(site))

    async def fake_assert_public(url):
        checked.append(url)

    async def per_request_connection(self, *a, **k):
        raise AssertionError("a fresh connection per request would be used")
    monkeypatch.setattr(safe, "public_client", fake_public_client)
    monkeypatch.setattr("ri.verify.fetch.assert_public", fake_assert_public)
    monkeypatch.setattr(safe.SafeClient, "request", per_request_connection)
    c = WorkdayConnector(safe.SafeClient(timeout=30), WD_BOARD, employer_name="X", sleep=no_sleep, now_fn=lambda: NOW)
    c.page_pause = 0
    b = run(c.fetch(None, 500))
    assert b.complete_listing is True and len(b.items) == 15 and b.quota_usage["requests"] == 18          # 3 pages and 15 details
    assert opened == [30] and checked == ["https://harbourline.wd3.myworkdayjobs.com/"]
    assert c._http is c.client                                                                             # back to the shared client afterwards


def test_workday_slow_down_request_part_way_keeps_the_pages_read_and_closes_nothing():
    inner = Workday(total=100)

    def handler(req):
        if req.method == "POST" and json.loads(req.content)["offset"] >= 40:
            return httpx.Response(429, headers={"Retry-After": "86400"}, text="Too many requests")
        return inner(req)
    b = run(make(WorkdayConnector, WD_BOARD, handler).fetch(None, 500))
    assert b.complete_listing is False and "asked us to slow down after 2 pages (40 of 100" in b.warnings[0]
    assert len(b.items) == len([i for i in range(40) if i % 3 == 0])


def test_workday_a_list_that_changed_while_it_was_read_is_not_a_complete_listing():
    site = Workday(total=45, drop_one=True)              # the second page comes back one posting short (an advert was taken down mid-read)
    b = run(make(WorkdayConnector, WD_BOARD, site).fetch(None, 500))
    assert b.complete_listing is False and "changed while it was read" in b.warnings[0]


def test_workday_wrap_round_is_detected_when_the_total_is_wrong():
    site = Workday(total=60)
    site.total = 100                                     # the first page announces 100 but only 60 exist: offset 60 wraps to page 1, which adds nothing new
    b = run(make(WorkdayConnector, WD_BOARD, site).fetch(None, 500))
    assert b.complete_listing is False
    assert [o for m, _, o in site.calls if m == "POST"] == [0, 20, 40, 60]         # the wrapped page stopped the read
    assert len({i.external_id for i in b.items}) == len(b.items) == 20              # the wrapped page added no duplicates


def test_workday_an_empty_board_is_an_affirmative_empty_listing():
    b = run(make(WorkdayConnector, WD_BOARD, Workday(postings=[])).fetch(None, 500))
    assert b.items == [] and b.complete_listing is True and b.quota_usage["postings_seen"] == 0


def test_workday_canonical_record_has_the_same_shape_as_the_other_boards():
    first = SHAPES["workday_list"]
    site = Workday(postings=first["jobPostings"])
    c = make(WorkdayConnector, WD_BOARD, site, employer_name="Harbourline Bank plc")
    b = run(c.fetch(None, 500))
    by_id = {i.external_id: c.canonicalize(i) for i in b.items}
    assert set(by_id) == {"R1001", "R1002-1", "R1004"}                           # the relationship manager and the cashier are not engineering roles
    d = by_id["R1001"]
    s = d.structured
    assert d.kind == "job_posting" and s["provider"] == "workday" and s["board"] == WD_BOARD and s["job_id"] == "R1001" and s["open_status"] == "open"
    assert s["employer_name"] == "Harbourline Bank plc" and s["title"] == "Senior Java Engineer - Payments" and s["location"] == "London Canary Wharf"
    assert s["url"] == "https://harbourline.wd3.myworkdayjobs.com/Careers/job/London-Canary-Wharf/Senior-Java-Engineer---Payments_R1001"
    assert s["employment_type"] == "permanent" and s["posted_at"] == datetime(2026, 11, 26, tzinfo=timezone.utc)          # startDate from the detail
    assert {"java", "kotlin", "aws", "kafka", "kubernetes", "iso 20022"} <= set(s["tech_keywords"]) and "track()" not in (d.excerpt or "")
    assert d.title == s["title"] and d.canonical_url == s["url"] and d.version_key and d.external_id == "R1001"
    # a posting with no detail yet still gets a date from the list wording; a contractor is a contract
    r2 = by_id["R1002-1"].structured
    assert r2["job_id"] == "R1002-1" and r2["posted_at"] is not None
    assert by_id["R1004"].structured["employment_type"] == "contract"


def test_workday_ids_survive_a_title_edit_and_dates_follow_the_list_wording():
    assert workday_job_id("/job/London-Canary-Wharf/Senior-Java-Engineer---Payments_R1001") == "R1001"
    assert workday_job_id("/job/London/Senior-Java-Engineer-NEW-TITLE_R1001") == "R1001"
    assert workday_job_id("/job/X/Eng_R-294898-2") == "R-294898-2" and workday_job_id("/job/X/NoUnderscoreHere") == "NoUnderscoreHere"
    d = datetime(2026, 11, 26, tzinfo=timezone.utc)
    assert workday_posted_at("Posted Today", NOW) == d and workday_posted_at("Posted Yesterday", NOW) == datetime(2026, 11, 25, tzinfo=timezone.utc)
    assert workday_posted_at("Posted 3 Days Ago", NOW) == datetime(2026, 11, 23, tzinfo=timezone.utc)
    assert workday_posted_at("Posted 30+ Days Ago", NOW) == datetime(2026, 10, 27, tzinfo=timezone.utc) and workday_posted_at("Vor 3 Tagen veröffentlicht", NOW) is None


def test_workday_slow_down_request_on_a_detail_stops_the_details_but_keeps_the_postings():
    site = Workday(total=45, detail_status=429)
    c = make(WorkdayConnector, WD_BOARD, site)
    b = run(c.fetch(None, 500))
    assert len(b.items) == 15 and b.complete_listing is True
    assert len([1 for m, _, _ in site.calls if m == "GET"]) == 1              # one 429 and no more detail calls


def test_workday_adverts_already_stored_are_not_asked_for_again_and_keep_what_they_have():
    known = {"R2000": {"employment_type": "permanent", "tech_keywords": ["java"], "posted_at": datetime(2026, 11, 1, tzinfo=timezone.utc), "location": "Zürich", "url": "https://x.example/r2000"},
             "R2003": {"employment_type": "unknown", "tech_keywords": [], "posted_at": None, "location": None, "url": None}}          # stored, but never got its text: asked again
    site = Workday(total=10)
    c = make(WorkdayConnector, WD_BOARD, site, known=known)
    b = run(c.fetch(None, 500))
    asked = [p for m, p, _ in site.calls if m == "GET"]
    assert len(asked) == 3 and not any("_R2000" in p for p in asked) and any("_R2003" in p for p in asked)            # R2000 reused; R2003, R2006, R2009 asked
    kept = {i.external_id: i.payload for i in b.items}
    assert kept["R2000"]["_tech"] == ["java"] and kept["R2000"]["_employment"] == "permanent" and kept["R2000"]["_location"] == "Zürich" and kept["R2000"]["_url"] == "https://x.example/r2000"
    assert kept["R2000"]["_posted"] == datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert kept["R2003"]["_employment"] == "permanent" and kept["R2003"]["_tech"]
    assert b.quota_usage["details"] == 3 and b.quota_usage["details_reused"] == 1
    d = c.canonicalize(next(i for i in b.items if i.external_id == "R2000"))
    assert d.structured["tech_keywords"] == ["java"]


def test_workday_a_missing_detail_only_loses_keywords():
    site = Workday(total=6, detail_status=404)
    b = run(make(WorkdayConnector, WD_BOARD, site).fetch(None, 500))
    assert len(b.items) == 2 and all(i.payload["_tech"] == [] for i in b.items) and b.complete_listing is True


@pytest.mark.parametrize("site,exc", [
    (Workday(list_status=404), NotFound),                                # no such career site
    (Workday(list_status=422), NotFound),                                # no such tenant (Workday answers 422 with no body)
    (Workday(list_status=400), SchemaDrift),
    (Workday(list_status=500), Exception),                               # after the retries: a server error class, never a crash of the scan
    (Workday(list_body=b"<html>maintenance</html>"), SchemaDrift),       # not JSON
    (Workday(list_body=b'{"total": 3}'), SchemaDrift),                   # JSON without the list
    (Workday(list_body=b'{"total": "many", "jobPostings": []}'), SchemaDrift),
    (Workday(list_body=b'[]'), SchemaDrift),
])
def test_workday_failures_are_clean_connector_errors(site, exc):
    from ri.sources.base import ConnectorError
    c = make(WorkdayConnector, WD_BOARD, site)
    with pytest.raises(exc) as e:
        run(c.fetch(None, 500))
    assert isinstance(e.value, ConnectorError)
    hc = run(make(WorkdayConnector, WD_BOARD, site).health_check())
    assert hc.ok is False and hc.error_class


def test_workday_rate_limit_with_a_long_wait_is_raised_for_the_job_layer():
    def handler(req):
        return httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(429, headers={"Retry-After": "43971"}, text="Too many requests")
    with pytest.raises(RateLimited) as e:
        run(make(WorkdayConnector, WD_BOARD, handler).fetch(None, 500))
    assert e.value.retry_after == 43971


def test_workday_a_short_rate_limit_is_waited_out_once():
    state = {"n": 0}
    slept = []

    async def sleep(s):
        slept.append(s)
    inner = Workday(postings=[])

    def handler(req):
        if req.method == "POST" and state["n"] == 0:
            state["n"] += 1
            return httpx.Response(429, headers={"Retry-After": "3"})
        return inner(req)
    b = run(make(WorkdayConnector, WD_BOARD, handler, sleep=sleep).fetch(None, 500))
    assert b.complete_listing is True and slept == [3.0]


@pytest.mark.parametrize("robots,why", [
    ("User-agent: *\nDisallow: /wday/", "robots"),                                   # the API path itself
    ("User-agent: *\nDisallow: /Careers/", "robots"),                                # the career site's own path (that is how Workday tenants write it)
    ("User-agent: *\nContent-Signal: search=yes, ai-input=no\nAllow: /", "ai-input"),
])
def test_workday_obeys_robots_txt_and_ai_signals(robots, why):
    site = Workday(total=5, robots=robots)
    with pytest.raises(PolicyBlocked) as e:
        run(make(WorkdayConnector, WD_BOARD, site).fetch(None, 500))
    assert why in str(e.value) and not site.calls


def test_workday_robots_rules_for_other_sites_of_the_tenant_do_not_block_this_one():
    site = Workday(total=5, robots="Sitemap: https://x/siteMap.xml\n\nUser-agent: *\nAllow: /Careers/\nDisallow: /OtherSite/\nDisallow: /refreshFacet/")
    b = run(make(WorkdayConnector, WD_BOARD, site).fetch(None, 500))
    assert b.complete_listing is True


def test_a_board_name_can_never_change_the_host_we_call():
    for bad in ("evil.example/x#", "a.wd3.myworkdayjobs.com/x", "harbourline.wd3/Ca reers", "harbourline/Careers", "harbourline.wd3/../x", "x.wd3/"):
        with pytest.raises(ValueError):
            WorkdayConnector(client(lambda r: httpx.Response(404)), bad)
    for cls in (PersonioConnector, TeamtailorConnector, RecruiteeConnector, WorkableConnector):
        for bad in ("a.evil.example", "x/y", "x?y", "", "-x", "x y"):
            with pytest.raises(ValueError):
                cls(client(lambda r: httpx.Response(404)), bad)


# ------------------------------------------------------------------------------------------------------------ Personio
def personio_site(xml=PERSONIO, *, tlds=("de", "com"), status=200, robots=None, seen=None):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=robots) if robots else httpx.Response(404)
        if seen is not None:
            seen.append(str(req.url))
        tld = req.url.host.rsplit(".", 1)[-1]
        if req.url.path != "/xml" or tld not in tlds:
            return httpx.Response(404, text="<html>Page Not Found</html>", headers={"content-type": "text/html"})
        return httpx.Response(status, text=xml, headers={"content-type": "text/xml"})
    return handler


def test_personio_reads_the_public_feed_with_full_text_and_maps_employment_types():
    c = make(PersonioConnector, "brightwater", personio_site())
    b = run(c.fetch(None, 500))
    assert b.complete_listing is True and [i.external_id for i in b.items] == ["7001", "7002", "7003"] and b.quota_usage["postings"] == 3
    docs = {i.external_id: c.canonicalize(i) for i in b.items}
    s1, s2, s3 = (docs[k].structured for k in ("7001", "7002", "7003"))
    assert s1["provider"] == "personio" and s1["board"] == "brightwater" and s1["title"] == "Java Entwickler (m/w/d) Schadenssysteme" and s1["team"] == "IT & Digital"
    assert s1["location"] == "München" and s1["url"] == "https://brightwater.jobs.personio.de/job/7001" and s1["employment_type"] == "permanent"
    assert s1["posted_at"] == datetime(2026, 11, 13, 16, 21, 35, tzinfo=timezone.utc)
    assert {"java", "kafka", "kubernetes", "azure", "terraform"} <= set(s1["tech_keywords"]) and "x()" not in (docs["7001"].excerpt or "")
    assert s2["employment_type"] == "contract" and s3["employment_type"] == "internship"
    assert c.company_name == "Brightwater Versicherung AG"


def test_personio_falls_back_to_the_com_address_only_when_de_does_not_know_the_company():
    seen: list[str] = []
    b = run(make(PersonioConnector, "brightwater", personio_site(tlds=("com",), seen=seen)).fetch(None, 500))
    assert len(b.items) == 3 and seen == ["https://brightwater.jobs.personio.de/xml", "https://brightwater.jobs.personio.com/xml"]
    seen.clear()
    run(make(PersonioConnector, "brightwater", personio_site(seen=seen)).fetch(None, 500))
    assert seen == ["https://brightwater.jobs.personio.de/xml"]


@pytest.mark.parametrize("handler,exc", [
    (personio_site(tlds=()), NotFound),                                               # no such company on either address
    (personio_site(xml="<html><body>maintenance</body></html>"), SchemaDrift),         # not the feed
    (personio_site(xml="<workzag-jobs><position><id>1</id>"), SchemaDrift),             # cut off
    (personio_site(xml="<jobs/>"), SchemaDrift),                                       # wrong document
    (personio_site(xml=""), SchemaDrift),
    (personio_site(xml='<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><workzag-jobs>&b;</workzag-jobs>'), SchemaDrift),    # entity bomb
    (personio_site(status=403), Exception),
])
def test_personio_failures_are_clean_connector_errors(handler, exc):
    from ri.sources.base import ConnectorError
    with pytest.raises(exc) as e:
        run(make(PersonioConnector, "brightwater", handler).fetch(None, 500))
    assert isinstance(e.value, ConnectorError)


def test_personio_an_empty_feed_is_an_affirmative_empty_listing_and_robots_is_obeyed():
    b = run(make(PersonioConnector, "brightwater", personio_site(xml="<workzag-jobs></workzag-jobs>")).fetch(None, 500))
    assert b.items == [] and b.complete_listing is True
    with pytest.raises(PolicyBlocked):
        run(make(PersonioConnector, "brightwater", personio_site(robots="User-agent: *\nDisallow: /xml")).fetch(None, 500))


def test_personio_follows_a_redirect_by_hand_and_checks_robots_on_the_new_address():
    seen = []

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /") if req.url.host == "elsewhere.example" else httpx.Response(404)
        seen.append(str(req.url))
        return httpx.Response(301, headers={"location": "https://elsewhere.example/xml"})
    with pytest.raises(PolicyBlocked):
        run(make(PersonioConnector, "brightwater", handler).fetch(None, 500))
    assert seen == ["https://brightwater.jobs.personio.de/xml"]


# ------------------------------------------------------------------------------------------------------------ Teamtailor
def teamtailor_site(xml=TEAMTAILOR, status=200, robots=None):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=robots) if robots else httpx.Response(404)
        assert req.url.host == "quillon.teamtailor.com" and req.url.path == "/jobs.rss"
        if status != 200:
            return httpx.Response(status, text="<html>404 the page you are looking for could not be found</html>")
        return httpx.Response(status, text=xml, headers={"content-type": "application/rss+xml"})
    return handler


def test_teamtailor_reads_the_public_feed_and_its_location_elements():
    c = make(TeamtailorConnector, "quillon", teamtailor_site())
    b = run(c.fetch(None, 500))
    assert b.complete_listing is True and len(b.items) == 2 and c.company_name == "Quillon Pay"
    docs = {i.external_id: c.canonicalize(i) for i in b.items}
    a = docs["d3af95f8-ff4e-4b02-8671-0a7564ce307b"].structured
    assert a["provider"] == "teamtailor" and a["board"] == "quillon" and a["title"] == "Senior Backend Engineer" and a["team"] == "Engineering"
    assert a["location"] == "Stockholm, Sweden" and a["url"] == "https://quillon.teamtailor.com/jobs/8786914-senior-backend-engineer"
    assert a["posted_at"] == datetime(2026, 11, 24, 9, 33, 49, tzinfo=timezone.utc)
    assert {"golang", "kafka", "aws", "terraform", "kubernetes"} <= set(a["tech_keywords"])
    k = docs["aa11bb22-0000-4000-8000-000000000001"].structured
    assert k["employment_type"] == "contract" and k["location"] is None and k["team"] == "Compliance"


@pytest.mark.parametrize("status,xml,exc", [
    (404, "", NotFound), (200, "<html>no</html>", SchemaDrift), (200, "<rss version='2.0'></rss>", SchemaDrift), (200, "<feed xmlns='http://www.w3.org/2005/Atom'/>", SchemaDrift),
    (200, "<rss><channel><item><title>cut", SchemaDrift), (500, "", Exception),
])
def test_teamtailor_failures_are_clean_connector_errors(status, xml, exc):
    from ri.sources.base import ConnectorError
    with pytest.raises(exc) as e:
        run(make(TeamtailorConnector, "quillon", teamtailor_site(xml=xml or TEAMTAILOR, status=status)).fetch(None, 500))
    assert isinstance(e.value, ConnectorError)
    with pytest.raises(PolicyBlocked):
        run(make(TeamtailorConnector, "quillon", teamtailor_site(robots="User-agent: *\nDisallow: /jobs.rss")).fetch(None, 500))


# ------------------------------------------------------------------------------------------------------------ Recruitee
def json_site(body, host, status=200, robots=None):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=robots) if robots else httpx.Response(404)
        assert req.url.host == host
        return httpx.Response(status, json=body) if not isinstance(body, bytes) else httpx.Response(status, content=body)
    return handler


def test_recruitee_reads_published_offers_with_text_and_maps_employment_codes():
    c = make(RecruiteeConnector, "marlow", json_site(SHAPES["recruitee"], "marlow.recruitee.com"))
    b = run(c.fetch(None, 500))
    assert b.complete_listing is True and [i.external_id for i in b.items] == ["5001", "5002", "5003"]           # the draft is not an advert
    docs = {i.external_id: c.canonicalize(i).structured for i in b.items}
    a = docs["5001"]
    assert a["provider"] == "recruitee" and a["board"] == "marlow" and a["title"] == "Backend Engineer (Java)" and a["team"] == "Engineering"
    assert a["location"] == "Rotterdam, Zuid-Holland, Netherlands" and a["employment_type"] == "permanent"
    assert a["url"] == "https://careers.marlowcredit.example/o/backend-engineer-java" and a["posted_at"] == datetime(2026, 11, 24, 10, 10, 44, tzinfo=timezone.utc)
    assert a["updated_at_source"] == "2026-11-25 08:00:00 UTC" and {"kotlin", "kafka", "aws", "java", "terraform"} <= set(a["tech_keywords"])
    assert docs["5002"]["employment_type"] == "contract" and docs["5003"]["employment_type"] == "internship"
    assert docs["5003"]["url"] == "https://marlow.recruitee.com/o/summer-intern" and c.company_name == "Marlow Credit"


@pytest.mark.parametrize("status,body,exc", [
    (404, {"error": "Not Found"}, NotFound), (200, {"offers": "none"}, SchemaDrift), (200, {"jobs": []}, SchemaDrift), (200, [], SchemaDrift),
    (200, b"<html>blocked</html>", SchemaDrift), (429, {"error": "slow down"}, RateLimited), (403, {}, Exception),
])
def test_recruitee_failures_are_clean_connector_errors(status, body, exc):
    from ri.sources.base import ConnectorError
    h = json_site(body, "marlow.recruitee.com", status)
    if status == 429:
        def h(req, _b=body):  # noqa: E306
            return httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(429, headers={"Retry-After": "86400"}, json=_b)
    with pytest.raises(exc) as e:
        run(make(RecruiteeConnector, "marlow", h).fetch(None, 500))
    assert isinstance(e.value, ConnectorError)


def test_recruitee_an_empty_offer_list_is_an_affirmative_empty_listing():
    b = run(make(RecruiteeConnector, "tellent", json_site({"offers": []}, "tellent.recruitee.com")).fetch(None, 500))
    assert b.items == [] and b.complete_listing is True


# ------------------------------------------------------------------------------------------------------------ Workable
def test_workable_reads_the_widget_and_asks_for_the_texts():
    seen = []

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(str(req.url))
        return httpx.Response(200, json=SHAPES["workable"])
    c = make(WorkableConnector, "foxglove", handler)
    b = run(c.fetch(None, 500))
    assert seen == ["https://apply.workable.com/api/v1/widget/accounts/foxglove?details=true"]
    assert b.complete_listing is True and [i.external_id for i in b.items] == ["A1B2C3D4E5", "F6G7H8I9J0"] and c.company_name == "Foxglove Capital"
    docs = {i.external_id: c.canonicalize(i).structured for i in b.items}
    a = docs["A1B2C3D4E5"]
    assert a["provider"] == "workable" and a["board"] == "foxglove" and a["title"] == "Platform Engineer" and a["team"] == "Technology" and a["location"] == "Bristol, England, United Kingdom"
    assert a["url"] == "https://apply.workable.com/j/A1B2C3D4E5" and a["employment_type"] == "permanent" and a["posted_at"] == datetime(2026, 11, 20, tzinfo=timezone.utc)
    assert {"terraform", "kubernetes", "azure", "python"} <= set(a["tech_keywords"])
    assert docs["F6G7H8I9J0"]["employment_type"] == "contract" and docs["F6G7H8I9J0"]["tech_keywords"] == []


@pytest.mark.parametrize("status,body,exc", [(404, {"error": "x"}, NotFound), (200, {"name": "x"}, SchemaDrift), (200, {"jobs": {}}, SchemaDrift), (200, [1], SchemaDrift),
                                             (200, b"Too many requests. Please try again later.", SchemaDrift)])
def test_workable_failures_are_clean_connector_errors(status, body, exc):
    from ri.sources.base import ConnectorError
    with pytest.raises(exc) as e:
        run(make(WorkableConnector, "foxglove", json_site(body, "apply.workable.com", status)).fetch(None, 500))
    assert isinstance(e.value, ConnectorError)


def test_workable_a_blocked_network_answer_is_a_rate_limit_for_the_job_layer():
    """What the build sandbox saw from Workable's network on 26 Nov 2026: 429 with Retry-After of 12 hours."""
    def handler(req):
        return httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(429, headers={"Retry-After": "43971"}, text="Too many requests. Please try again later.")
    with pytest.raises(RateLimited):
        run(make(WorkableConnector, "foxglove", handler).fetch(None, 500))


def test_fetch_by_id_finds_a_listed_advert_and_none_for_a_missing_one():
    c = make(RecruiteeConnector, "marlow", json_site(SHAPES["recruitee"], "marlow.recruitee.com"))
    assert run(c.fetch_by_id("5001")).external_id == "5001" and run(c.fetch_by_id("999")) is None


# ------------------------------------------------------------------------------------------------------------ the account scan (database)
HOME = '<html><body><a href="/careers">Careers</a><a href="/newsroom/">News</a></body></html>'


def scan_site(workday: Workday | None = None, *, careers: str, extra=None):
    """bank.example with a careers page that links to the given systems; boards answer from fakes."""
    def handler(req):
        u = req.url
        if u.path == "/robots.txt":
            return httpx.Response(404)
        if u.host == "bank.example":
            return {"/": httpx.Response(200, text=HOME), "/careers": httpx.Response(200, text=careers), "/newsroom/": httpx.Response(200, text="<html><body>Newsroom of the bank</body></html>")}.get(u.path, httpx.Response(404))
        if u.host == "harbourline.wd3.myworkdayjobs.com" and workday is not None:
            return workday(req)
        if extra:
            return extra(req)
        return httpx.Response(404)
    return handler


def new_org(conn, name="Harbourline Bank plc"):
    org = conn.execute("INSERT INTO organizations(display_name, canonical_domain, country_code) VALUES (%s,'bank.example','GB') RETURNING id", (name,)).fetchone()["id"]
    conn.commit()
    return str(org)


@pytest.fixture()
def fast(monkeypatch):
    monkeypatch.setattr(WorkdayConnector, "PAGE_PAUSE", 0)


def events(conn, kind):
    return conn.execute("SELECT subject_org_id, summary, attrs FROM events WHERE event_type=%s ORDER BY summary", (kind,)).fetchall()


def test_the_new_source_rows_exist_and_the_migration_can_run_again(conn):
    seed_registry(conn)
    sql = (Path(__file__).parents[1] / "migrations" / "0050_career_systems.sql").read_text()
    conn.execute(sql)
    conn.execute(sql)                                                    # safe to re-run
    conn.commit()
    rows = {r["source_id"]: r for r in conn.execute("SELECT source_id, collection_method, status, signal_rule_ids, config FROM sources WHERE source_id LIKE 'ats-%'").fetchall()}
    assert {"ats-workday", "ats-personio", "ats-teamtailor", "ats-recruitee", "ats-workable"} <= set(rows)
    assert all(r["collection_method"] == "official_api" and r["status"] == "pending_connector_validation" and "CAP-01" in r["signal_rule_ids"] for p, r in rows.items() if p != "ats-lever" and p != "ats-ashby" and p != "ats-greenhouse")
    assert conn.execute("SELECT count(*) AS n FROM source_health WHERE source_id IN ('ats-personio','ats-teamtailor','ats-recruitee','ats-workable')").fetchone()["n"] == 4
    from ri.connections import directory
    d = {r["source_id"]: r for r in directory(conn)}
    assert all(d[s]["state"] == "per_account" for s in ("ats-workday", "ats-personio", "ats-teamtailor", "ats-recruitee", "ats-workable"))


def test_scan_finds_a_workday_board_reads_engineering_roles_and_closes_what_disappears(conn, fast):
    seed_registry(conn)
    org = new_org(conn)
    careers = '<html><body><a href="https://harbourline.wd3.myworkdayjobs.com/en-US/Careers">Search and apply</a></body></html>'
    first = Workday(postings=SHAPES["workday_list"]["jobPostings"])
    out = run_scan(conn, org, client=client(scan_site(first, careers=careers)), extractor=None, resolver=None)
    assert out["status"] == "ok", out
    ep = conn.execute("SELECT kind, url, provider, board, last_status, last_detail, docs_seen FROM account_endpoints WHERE kind='job_board'").fetchall()
    assert [(e["provider"], e["board"], e["url"], e["last_status"], e["docs_seen"]) for e in ep] == [("workday", WD_BOARD, "https://harbourline.wd3.myworkdayjobs.com/Careers", "ok", 3)]
    jobs = {r["job_id"]: r for r in conn.execute("SELECT * FROM job_postings WHERE provider='workday' ORDER BY job_id").fetchall()}
    assert set(jobs) == {"R1001", "R1002-1", "R1004"} and all(str(j["organization_id"]) == org and j["open_status"] == "open" and j["employer_name"] == "Harbourline Bank plc" for j in jobs.values())
    assert jobs["R1001"]["employment_type"] == "permanent" and jobs["R1004"]["employment_type"] == "contract" and "kafka" in jobs["R1001"]["tech_keywords"]
    docs = conn.execute("SELECT count(*) AS n FROM source_documents WHERE source_id='ats-workday'").fetchone()["n"]
    assert docs == 3
    opened = events(conn, "engineering_job_opened")
    contractor = events(conn, "contractor_job_opened")
    assert len(opened) == 2 and len(contractor) == 1 and str(opened[0]["subject_org_id"]) == org
    assert {e["attrs"]["provider"] for e in opened + contractor} == {"workday"} and {e["attrs"]["board"] for e in opened} == {WD_BOARD}
    cov = conn.execute("SELECT coverage_state FROM account_source_coverage WHERE source_family='jobs'").fetchone()
    assert cov["coverage_state"] == "monitored_healthy"

    # the same board again: nothing new, nothing duplicated, and no detail call for an advert that is already stored (its keywords are kept)
    gets = lambda site: len([1 for m, _, _ in site.calls if m == "GET"])      # noqa: E731
    before = gets(first)
    assert before == 3
    out2 = run_scan(conn, org, client=client(scan_site(first, careers=careers)), extractor=None, resolver=None)
    assert out2["job_changes"] == 0 and gets(first) == before
    assert "kafka" in conn.execute("SELECT tech_keywords FROM job_postings WHERE job_id='R1001'").fetchone()["tech_keywords"]
    assert conn.execute("SELECT count(*) AS n FROM job_postings").fetchone()["n"] == 3 and len(events(conn, "engineering_job_opened")) == 2

    # one advert is taken down: closed, with a closed event; the others stay open
    rest = [p for p in SHAPES["workday_list"]["jobPostings"] if "R1002" not in p["externalPath"]]
    out3 = run_scan(conn, org, client=client(scan_site(Workday(postings=rest), careers=careers)), extractor=None, resolver=None)
    assert out3["job_changes"] == 1
    st = {r["job_id"]: r["open_status"] for r in conn.execute("SELECT job_id, open_status FROM job_postings").fetchall()}
    assert st == {"R1001": "open", "R1002-1": "closed", "R1004": "open"}
    closed = events(conn, "job_closed")
    assert len(closed) == 1 and closed[0]["attrs"]["job_id"] == "R1002-1"

    # it comes back: reopened, same job, no duplicate row
    run_scan(conn, org, client=client(scan_site(first, careers=careers)), extractor=None, resolver=None)
    assert conn.execute("SELECT count(*) AS n FROM job_postings").fetchone()["n"] == 3
    assert conn.execute("SELECT open_status FROM job_postings WHERE job_id='R1002-1'").fetchone()["open_status"] == "open"


def test_a_half_read_workday_board_never_closes_adverts(conn, fast, monkeypatch):
    seed_registry(conn)
    org = new_org(conn)
    careers = '<a href="https://harbourline.wd3.myworkdayjobs.com/en-US/Careers">Jobs</a>'
    run_scan(conn, org, client=client(scan_site(Workday(postings=SHAPES["workday_list"]["jobPostings"]), careers=careers)), extractor=None, resolver=None)
    monkeypatch.setattr(WorkdayConnector, "MAX_PAGES", 1)
    big = Workday(total=60)                                    # a different, larger listing, cut off after one page: nothing may be closed
    out = run_scan(conn, org, client=client(scan_site(big, careers=careers)), extractor=None, resolver=None)
    assert out["status"] == "ok" and any("no advert is closed" in n for n in out["notes"])
    assert conn.execute("SELECT count(*) AS n FROM job_postings WHERE open_status='closed'").fetchone()["n"] == 0
    assert conn.execute("SELECT last_detail FROM account_endpoints WHERE kind='job_board'").fetchone()["last_detail"].startswith("Workday board harbourline.wd3/Careers: read the newest")


def test_a_posting_already_stored_is_updated_not_duplicated(conn, fast):
    seed_registry(conn)
    org = new_org(conn)
    conn.execute("INSERT INTO job_postings(provider, board, job_id, organization_id, employer_name, title, employment_type, open_status, url) "
                 "VALUES ('workday', %s, 'R1001', %s, 'Harbourline Bank plc', 'Senior Java Engineer - Payments', 'unknown', 'open', 'https://old.example/1')", (WD_BOARD, org))
    conn.commit()
    careers = '<a href="https://harbourline.wd3.myworkdayjobs.com/en-US/Careers">Jobs</a>'
    run_scan(conn, org, client=client(scan_site(Workday(postings=SHAPES["workday_list"]["jobPostings"]), careers=careers)), extractor=None, resolver=None)
    rows = conn.execute("SELECT job_id, url FROM job_postings WHERE provider='workday' ORDER BY job_id").fetchall()
    assert [r["job_id"] for r in rows] == ["R1001", "R1002-1", "R1004"]
    assert rows[0]["url"].startswith("https://harbourline.wd3.myworkdayjobs.com/Careers/job/")            # refreshed in place
    assert len([e for e in events(conn, "engineering_job_opened") if e["attrs"]["job_id"] == "R1001"]) == 0           # it was already known: no second "opened" finding


def test_an_account_scanned_before_workday_was_read_picks_up_the_board_from_its_careers_page(conn, fast):
    """Existing accounts already have their pages on file (no job board); the careers page is read on every scan, so the board is found without another request."""
    seed_registry(conn)
    org = new_org(conn)
    conn.execute("INSERT INTO account_endpoints(organization_id, kind, url) VALUES (%s,'careers','https://bank.example/careers')", (org,))
    conn.execute("INSERT INTO account_source_coverage(organization_id, source_family, coverage_state) VALUES (%s,'procurement','source_not_found')", (org,))
    conn.commit()
    careers = ('<html><body><p>Join us. We are hiring engineers across payments and core banking.</p>'
               '<a href="https://harbourline.wd3.myworkdayjobs.com/en-US/Careers">Search jobs</a><a href="https://join.com/companies/alpenbank">Swiss</a></body></html>')
    out = run_scan(conn, org, client=client(scan_site(Workday(postings=SHAPES["workday_list"]["jobPostings"]), careers=careers)), extractor=None, resolver=None)
    assert out["endpoints"] == 2 and out["scanned"] == 2 and out["status"] == "ok", out
    assert any("workday/harbourline.wd3/Careers found on the careers page" in n for n in out["notes"])
    assert conn.execute("SELECT count(*) AS n FROM job_postings WHERE provider='workday'").fetchone()["n"] == 3
    assert conn.execute("SELECT count(*) AS n FROM account_endpoints WHERE kind='job_board'").fetchone()["n"] == 1
    out2 = run_scan(conn, org, client=client(scan_site(Workday(postings=SHAPES["workday_list"]["jobPostings"]), careers=careers)), extractor=None, resolver=None)
    assert out2["endpoints"] == 2 and conn.execute("SELECT count(*) AS n FROM account_endpoints").fetchone()["n"] == 2          # not added twice


def test_at_most_three_boards_are_kept_per_account(conn, fast):
    seed_registry(conn)
    org = new_org(conn)
    conn.execute("INSERT INTO account_endpoints(organization_id, kind, url) VALUES (%s,'careers','https://bank.example/careers')", (org,))
    conn.execute("INSERT INTO account_source_coverage(organization_id, source_family, coverage_state) VALUES (%s,'procurement','source_not_found')", (org,))
    conn.commit()
    careers = "".join(f'<a href="https://b{i}.recruitee.com/">x</a>' for i in range(6)) + "<p>jobs</p>"
    run_scan(conn, org, client=client(scan_site(None, careers=careers, extra=lambda r: httpx.Response(404, json={"error": "Not Found"}))), extractor=None, resolver=None)
    assert conn.execute("SELECT count(*) AS n FROM account_endpoints WHERE kind='job_board'").fetchone()["n"] == 3


def test_a_board_that_fails_is_recorded_and_never_stops_the_rest_of_the_scan(conn, fast):
    seed_registry(conn)
    org = new_org(conn)
    careers = ('<a href="https://brightwater.jobs.personio.de/">Jobs</a><a href="https://quillon.teamtailor.com/jobs">TT</a>'
               '<a href="https://harbourline.wd3.myworkdayjobs.com/en-US/Careers">WD</a>')

    def extra(req):
        if req.url.host.startswith("brightwater"):
            return httpx.Response(200, text="<html>This is not the feed</html>")            # malformed: not XML we can read
        if req.url.host == "quillon.teamtailor.com":
            return httpx.Response(200, text=TEAMTAILOR)
        return httpx.Response(404)
    out = run_scan(conn, org, client=client(scan_site(Workday(list_status=404), careers=careers, extra=extra)), extractor=None, resolver=None)
    st = {(e["provider"]): (e["last_status"], e["last_detail"]) for e in conn.execute("SELECT provider, last_status, last_detail FROM account_endpoints WHERE kind='job_board'").fetchall()}
    assert st["personio"][0] == "failed" and st["personio"][1].startswith("SchemaDrift")
    assert st["workday"][0] == "failed" and st["workday"][1].startswith("NotFound")
    assert st["teamtailor"][0] == "ok"                                                     # the healthy board was still read
    assert out["status"] == "partially_checked" and conn.execute("SELECT count(*) AS n FROM job_postings WHERE provider='teamtailor'").fetchone()["n"] == 2
    assert conn.execute("SELECT coverage_state FROM account_source_coverage WHERE source_family='jobs'").fetchone()["coverage_state"] == "monitored_healthy"


def test_every_new_system_is_stored_under_its_own_source_and_creates_events(conn, fast):
    seed_registry(conn)
    org = new_org(conn)
    careers = ('<a href="https://brightwater.jobs.personio.de/">P</a><a href="https://quillon.teamtailor.com/jobs">T</a>'
               '<a href="https://marlow.recruitee.com/">R</a><a href="https://apply.workable.com/foxglove/">W</a>')

    def extra(req):
        h = req.url.host
        if h.startswith("brightwater"):
            return httpx.Response(200, text=PERSONIO)
        if h == "quillon.teamtailor.com":
            return httpx.Response(200, text=TEAMTAILOR)
        if h == "marlow.recruitee.com":
            return httpx.Response(200, json=SHAPES["recruitee"])
        if h == "apply.workable.com":
            return httpx.Response(200, json=SHAPES["workable"])
        return httpx.Response(404)
    # discovery keeps three boards; read the fourth through a second scan of the careers page's links
    out = run_scan(conn, org, client=client(scan_site(None, careers=careers, extra=extra)), extractor=None, resolver=None)
    assert out["status"] == "ok", out
    out = run_scan(conn, org, client=client(scan_site(None, careers=careers, extra=extra)), extractor=None, resolver=None)
    by_provider = {r["provider"]: r["n"] for r in conn.execute("SELECT provider, count(*) AS n FROM job_postings GROUP BY provider").fetchall()}
    assert by_provider.get("personio") == 3 and by_provider.get("teamtailor") == 2 and by_provider.get("recruitee") == 3
    src = {r["source_id"]: r["n"] for r in conn.execute("SELECT source_id, count(*) AS n FROM source_documents WHERE source_id LIKE 'ats-%' GROUP BY source_id").fetchall()}
    assert src.get("ats-personio") == 3 and src.get("ats-teamtailor") == 2 and src.get("ats-recruitee") == 3
    opened = {e["attrs"]["provider"] for e in events(conn, "engineering_job_opened") + events(conn, "contractor_job_opened")}
    assert {"personio", "teamtailor", "recruitee"} <= opened
    eng_titles = {e["attrs"]["title"] for e in events(conn, "engineering_job_opened")}
    assert "Java Entwickler (m/w/d) Schadenssysteme" in eng_titles and "Senior Backend Engineer" in eng_titles and "Backend Engineer (Java)" in eng_titles


def test_an_empty_feed_closes_nothing_the_first_time_and_closes_when_the_next_check_agrees(conn, fast):
    seed_registry(conn)
    org = new_org(conn)
    careers = '<a href="https://quillon.teamtailor.com/jobs">T</a>'
    state = {"xml": TEAMTAILOR}
    extra = lambda req: httpx.Response(200, text=state["xml"]) if req.url.host == "quillon.teamtailor.com" else httpx.Response(404)           # noqa: E731
    run_scan(conn, org, client=client(scan_site(None, careers=careers, extra=extra)), extractor=None, resolver=None)
    assert conn.execute("SELECT count(*) AS n FROM job_postings WHERE open_status='open'").fetchone()["n"] == 2
    state["xml"] = '<?xml version="1.0"?><rss version="2.0"><channel><title>Quillon Pay</title></channel></rss>'          # feed answers, but with no adverts
    out = run_scan(conn, org, client=client(scan_site(None, careers=careers, extra=extra)), extractor=None, resolver=None)
    assert conn.execute("SELECT count(*) AS n FROM job_postings WHERE open_status='open'").fetchone()["n"] == 2 and out["job_changes"] == 0
    assert conn.execute("SELECT last_detail FROM account_endpoints WHERE kind='job_board'").fetchone()["last_detail"].startswith("Listing was empty")
    out = run_scan(conn, org, client=client(scan_site(None, careers=careers, extra=extra)), extractor=None, resolver=None)
    assert out["job_changes"] == 2 and conn.execute("SELECT count(*) AS n FROM job_postings WHERE open_status='closed'").fetchone()["n"] == 2
    assert conn.execute("SELECT last_detail FROM account_endpoints WHERE kind='job_board'").fetchone()["last_detail"] is None
    # an advert missing from a feed that still lists others is closed at once (no second check needed)
    state["xml"] = TEAMTAILOR
    run_scan(conn, org, client=client(scan_site(None, careers=careers, extra=extra)), extractor=None, resolver=None)
    state["xml"] = TEAMTAILOR.replace("<guid>aa11bb22-0000-4000-8000-000000000001</guid>", "<guid>zz</guid>").replace("Interim Compliance Officer (contract)", "Other")
    out = run_scan(conn, org, client=client(scan_site(None, careers=careers, extra=extra)), extractor=None, resolver=None)
    assert out["job_changes"] >= 1
    assert conn.execute("SELECT open_status FROM job_postings WHERE job_id='aa11bb22-0000-4000-8000-000000000001'").fetchone()["open_status"] == "closed"


def test_the_old_readers_still_work_through_the_new_registry(conn):
    """Greenhouse is still found from a careers page and read exactly as before (same endpoint address, same source)."""
    seed_registry(conn)
    org = new_org(conn)
    careers = '<a href="https://boards.greenhouse.io/syntheticbank">Open roles</a>'
    gh = {"jobs": [{"id": 1, "title": "Senior Java Engineer, Payments", "location": {"name": "London"}, "updated_at": "2026-11-18T10:00:00Z", "first_published": "2026-11-10T10:00:00Z",
                    "absolute_url": "https://boards.greenhouse.io/syntheticbank/jobs/1", "content": "Kafka and AWS", "departments": [{"name": "Engineering"}]}]}
    extra = lambda req: httpx.Response(200, json=gh) if req.url.host == "boards-api.greenhouse.io" else httpx.Response(404)          # noqa: E731
    out = run_scan(conn, org, client=client(scan_site(None, careers=careers, extra=extra)), extractor=None, resolver=None)
    assert out["status"] == "ok"
    ep = conn.execute("SELECT url, provider, board FROM account_endpoints WHERE kind='job_board'").fetchone()
    assert (ep["url"], ep["provider"], ep["board"]) == ("https://boards.greenhouse.io/syntheticbank", "greenhouse", "syntheticbank")
    assert conn.execute("SELECT count(*) AS n FROM source_documents WHERE source_id='ats-greenhouse'").fetchone()["n"] == 1
