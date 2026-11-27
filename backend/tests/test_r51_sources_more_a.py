"""Round 8, helper A: more job and tender sources. Jobicy and Remote OK (remote engineering roles at financial employers), and the German
Bekanntmachungsservice open-data export (national tenders). Fixtures have the shapes recorded from the live services on 27 Nov 2026; every company, buyer and
notice in them is invented. No test touches the network."""
import asyncio
import io
import json
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from ri.sources.base import NotFound, PolicyBlocked, RateLimited, SchemaDrift
from ri.sources.jobs_market import (JobicyConnector, RemoteOkConnector, markets_in, remote_scope, screen_remote_job)
from ri.sources.registry import seed_registry
from ri.sources.tenders_more import GermanNoticeConnector, on_ted, parse_notice, pick_deadline, when

FIX = Path(__file__).parent / "fixtures" / "sources_more_a"
JOBICY = json.loads((FIX / "jobicy.json").read_text(encoding="utf-8"))
REMOTEOK = json.loads((FIX / "remoteok.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 27, 7, 0, tzinfo=timezone.utc)
ROBOTS_OK = "User-agent: *\nDisallow:\n"


def run(coro):
    return asyncio.run(coro)


def no_sleep(log: list):
    async def sleep(seconds):
        log.append(seconds)
    return sleep


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------------------------------------------------ remote scope and screening
@pytest.mark.parametrize("location,scope", [
    ("UK", "market"), ("UK Only", "market"), ("Ireland, Japan, Poland, UK, USA", "market"), ("Norway, Sweden", "market"), ("Berlin, Germany", "market"),
    ("Worldwide", "open"), ("Anywhere", "open"), ("Europe", "open"), ("EMEA", "open"), ("", "open"), (None, "open"), ("Bulgaria", "open"),
    ("USA", "other"), ("US Only", "other"), ("North America", "other"), ("APAC", "other"), ("Canada", "other"), ("Paris, France", "other"),
])
def test_remote_scope_keeps_our_markets_and_open_roles_and_drops_other_places(location, scope):
    assert remote_scope(location) == scope


def test_markets_in_names_every_market_of_ours_in_a_fixed_order():
    assert markets_in("Norway, Sweden") == ["SE", "NO"] and markets_in("Poland") == ["PL"] and markets_in("Worldwide") == []
    assert markets_in("Zürich, Switzerland and Wien") == ["CH", "AT"]


def test_screening_counts_every_drop_by_its_reason():
    n = {"hits_seen": 0, "dropped_agency": 0, "dropped_not_engineering": 0, "dropped_other_country": 0, "dropped_old": 0, "dropped_not_financial": 0}
    bank = "Harbourline Bank is a digital bank for small businesses and a payments platform."
    assert screen_remote_job("Harbourline Bank", "Senior Backend Engineer", bank, "UK", n) is True
    assert screen_remote_job("Harbourline Bank", "Marketing Manager", bank, "UK", n) is False
    assert screen_remote_job("Norfolk Staffing Group", "Backend Engineer", bank, "UK", n) is False
    assert screen_remote_job("Acme Recruitment", "Backend Engineer", "Our client is a bank. " + bank, "UK", n) is False
    assert screen_remote_job("Harbourline Bank", "Backend Engineer", bank, "USA", n) is False
    assert screen_remote_job("Meadowbrook Robotics", "Backend Engineer", "We build robots.", "Europe", n) is False
    assert n == {"hits_seen": 0, "dropped_agency": 2, "dropped_not_engineering": 1, "dropped_other_country": 1, "dropped_old": 0, "dropped_not_financial": 1}


# ------------------------------------------------------------------------------------------------------------ Jobicy
def jobicy_client(seen: list, robots: str = ROBOTS_OK, expire_tokens: bool = False):
    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        assert req.url.path == "/api/jobs" and req.headers["User-Agent"].startswith("CodestRevenueIntelligence/")
        if req.url.params.get("cursor"):
            if expire_tokens:
                return httpx.Response(400, json={"success": False, "message": "cursor expired"})
            assert req.url.params["cursor"] == "TOKEN_PAGE_2"
            return httpx.Response(200, json=JOBICY["page2"])
        return httpx.Response(200, json=JOBICY["page1"])
    return client(handler)


def jobicy(seen=None, **kw):
    c = JobicyConnector(jobicy_client(seen if seen is not None else [], **kw), now_fn=lambda: NOW)
    c.sleep = no_sleep([])
    return c


def test_jobicy_keeps_engineering_roles_at_financial_employers_open_to_our_markets():
    seen, waits = [], []
    c = JobicyConnector(jobicy_client(seen), now_fn=lambda: NOW)
    c.sleep = no_sleep(waits)
    b1 = run(c.fetch(None, 100))
    assert [i.external_id for i in b1.items] == ["9001", "9002"]
    assert b1.has_more and b1.complete_listing is False and b1.next_cursor.endswith(";next=TOKEN_PAGE_2")
    u = b1.quota_usage
    assert (u["hits_seen"], u["kept_financial"], u["dropped_not_financial"], u["dropped_not_engineering"], u["dropped_other_country"], u["dropped_agency"]) == (6, 2, 1, 1, 1, 1)
    b2 = run(c.fetch(b1.next_cursor, 100))
    assert [i.external_id for i in b2.items] == ["9007"] and b2.quota_usage["dropped_old"] == 1           # the November 10th advert is older than the window
    assert not b2.has_more and b2.next_cursor == f"since={NOW.isoformat(timespec='seconds')}"           # the next run starts from the newest and stops at what this run saw
    api = [r for r in seen if r.url.path == "/api/jobs"]
    assert api[0].url.params["count"] == "100" and api[0].url.params["industry"] == "engineering" and "cursor" not in api[0].url.params
    assert "europe" in api[0].url.params["geo"].split(",") and "uk" in api[0].url.params["geo"].split(",")
    assert api[1].url.params["cursor"] == "TOKEN_PAGE_2" and api[1].url.params["geo"] == api[0].url.params["geo"]          # the filters stay the same while paging
    assert waits == [2.0]                                                                                            # a pause before every page after the first
    assert sum(1 for r in seen if r.url.path == "/robots.txt") == 1


def test_jobicy_stops_at_what_the_last_run_already_saw_and_restarts_when_a_token_expired():
    c = jobicy()
    b = run(c.fetch("since=2026-11-26T05:00:00+00:00", 100))          # the previous run: only a day of overlap is read back
    assert [i.external_id for i in b.items] == ["9001", "9002"] and b.quota_usage["dropped_old"] == 3 and not b.has_more          # newest first: once an old advert shows up the list is done
    assert b.next_cursor == f"since={NOW.isoformat(timespec='seconds')}"
    older = run(jobicy().fetch("since=2026-11-26T05:00:00+00:00;next=TOKEN_PAGE_2", 100))
    assert older.items == [] and older.quota_usage["dropped_old"] == 2
    stale = jobicy(expire_tokens=True)
    b = run(stale.fetch("since=2026-11-20T00:00:00+00:00;next=OLD_TOKEN", 100))          # a token older than a day answers 400: start the list again, do not fail
    assert [i.external_id for i in b.items] == ["9001", "9002"] and b.next_cursor.endswith(";next=TOKEN_PAGE_2")


def test_jobicy_reads_only_the_window_and_never_closes_anything():
    c = jobicy()
    b = run(c.fetch(None, 100))
    assert b.complete_listing is False          # a job that left the seven-day list is not necessarily closed


def test_jobicy_stops_when_robots_txt_refuses_and_the_test_fails_cleanly():
    blocked = jobicy(robots="User-agent: *\nDisallow: /api/\n")
    with pytest.raises(PolicyBlocked):
        run(blocked.fetch(None, 100))
    assert run(blocked.health_check()).ok is False
    ai_no = jobicy(robots="User-agent: *\nContent-Signal: ai-input=no\nAllow: /\n")
    with pytest.raises(PolicyBlocked):
        run(ai_no.fetch(None, 100))


def test_jobicy_failures_are_classified_for_the_job_layer():
    def limited(req):
        return httpx.Response(200, text=ROBOTS_OK) if req.url.path == "/robots.txt" else httpx.Response(429, headers={"Retry-After": "600"})
    c = JobicyConnector(client(limited), now_fn=lambda: NOW)
    c.sleep = no_sleep([])
    with pytest.raises(RateLimited):
        run(c.fetch(None, 100))
    drift = JobicyConnector(client(lambda r: httpx.Response(200, text=ROBOTS_OK) if r.url.path == "/robots.txt" else httpx.Response(200, json={"hello": 1})), now_fn=lambda: NOW)
    assert run(drift.health_check()).error_class == "schema_drift"


def test_jobicy_postings_have_the_shape_of_the_other_market_boards_and_link_back_to_jobicy():
    from ri.sources.base import RawItem
    from ri.sources.jobs_market import JobTechConnector
    c = jobicy()
    b = run(c.fetch(None, 100))
    d = c.canonicalize(b.items[0])
    s = d.structured
    jt = JobTechConnector(httpx.AsyncClient(), now_fn=lambda: NOW)
    hit = json.loads((Path(__file__).parent / "fixtures" / "jobboards_sample.json").read_text(encoding="utf-8"))["jobtech"]["hits"][0]
    assert set(s) == set(jt.canonicalize(RawItem(str(hit["id"]), None, hit)).structured)
    assert d.kind == "job_posting" and s["provider"] == "jobicy" and s["board"] == "remote" and s["job_id"] == "9001" and s["employer_name"] == "Harbourline Bank"
    assert s["country"] == "GB" and s["employment_type"] == "permanent" and s["open_status"] == "open" and s["posted_at"].tzinfo is not None
    assert d.canonical_url == s["url"] == "https://jobicy.com/jobs/9001-senior-backend-engineer-java"          # the link back Jobicy asks for
    assert {"java", "kafka", "aws", "kubernetes"} <= set(s["tech_keywords"])
    second = c.canonicalize(b.items[1]).structured
    assert second["employer_name"] == "Quillon & Payments" and second["employment_type"] == "contract" and second["country"] is None          # entities decoded; "Europe" names no market
    norway = c.canonicalize(run(c.fetch(b.next_cursor, 100)).items[0]).structured
    assert norway["location"] == "Norway, Sweden" and norway["country"] is None                                # two markets: no single country


# ------------------------------------------------------------------------------------------------------------ Remote OK
def remoteok_client(seen: list, robots: str = ROBOTS_OK, body=None):
    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        assert req.url.path == "/api" and req.headers["User-Agent"].startswith("CodestRevenueIntelligence/")
        return httpx.Response(200, json=REMOTEOK if body is None else body)
    return client(handler)


def test_remoteok_keeps_engineering_roles_at_financial_employers_and_skips_the_terms_notice():
    seen = []
    c = RemoteOkConnector(remoteok_client(seen), now_fn=lambda: NOW)
    b = run(c.fetch(None, 100))
    assert [i.external_id for i in b.items] == ["5001", "5002"] and not b.has_more and b.next_cursor is None and b.complete_listing is False
    u = b.quota_usage
    assert (u["hits_seen"], u["kept_financial"], u["dropped_not_engineering"], u["dropped_other_country"], u["dropped_old"], u["dropped_not_financial"]) == (6, 2, 1, 1, 1, 1)
    assert sum(1 for r in seen if r.url.path == "/api") == 1 and sum(1 for r in seen if r.url.path == "/robots.txt") == 1


def test_remoteok_postings_link_to_remote_ok_and_never_use_the_apply_link():
    c = RemoteOkConnector(remoteok_client([]), now_fn=lambda: NOW)
    b = run(c.fetch(None, 100))
    d = c.canonicalize(b.items[0])
    s = d.structured
    assert s["provider"] == "remoteok" and s["board"] == "remote" and s["employer_name"] == "Harbourline Bank" and s["country"] == "GB"
    assert s["url"] == d.canonical_url == "https://remoteOK.com/remote-jobs/remote-staff-platform-engineer-harbourline-bank-5001" and "/l/" not in s["url"]
    assert s["posted_at"] == datetime(2026, 11, 26, 6, tzinfo=timezone.utc) and "java" in s["tech_keywords"]
    contract = c.canonicalize(b.items[1]).structured
    assert contract["employment_type"] == "contract" and contract["country"] is None


def test_remoteok_stops_when_robots_txt_refuses_or_the_shape_changes():
    blocked = RemoteOkConnector(remoteok_client([], robots="User-agent: *\nDisallow: /api\n"), now_fn=lambda: NOW)
    with pytest.raises(PolicyBlocked):
        run(blocked.fetch(None, 100))
    assert run(blocked.health_check()).ok is False
    changed = RemoteOkConnector(remoteok_client([], body={"jobs": []}), now_fn=lambda: NOW)
    assert run(changed.health_check()).error_class == "schema_drift"
    assert run(RemoteOkConnector(remoteok_client([]), now_fn=lambda: NOW).health_check()).ok is True


# ------------------------------------------------------------------------------------------------------------ the jobs through the pipeline
def test_jobicy_runs_through_the_pipeline_with_the_same_employer_matching_and_never_closes_a_job(conn):
    from ri.events.structured import emit_job_event
    from ri.identity.resolver import resolve
    from ri.ingest.pipeline import run_source
    seed_registry(conn)
    org = str(conn.execute("insert into organizations(display_name, country_code) values ('Harbourline Bank','GB') returning id").fetchone()["id"])
    conn.commit()
    resolver = lambda name: (lambda d: d.org_id if d.action == "auto_link" else None)(resolve(conn, name=name, allow_create=False)) if name else None      # noqa: E731  (the worker's matcher)
    c = jobicy()
    res = run_source(conn, c, resolve_org=resolver, allow_pending=True, now_fn=lambda: NOW)
    assert res.status == "success" and res.new == 3, res.error
    rows = {r["job_id"]: r for r in conn.execute("select job_id, employer_name, organization_id, open_status, provider, board from job_postings").fetchall()}
    assert set(rows) == {"9001", "9002", "9007"} and str(rows["9001"]["organization_id"]) == org and rows["9002"]["organization_id"] is None
    assert rows["9001"]["provider"] == "jobicy" and rows["9001"]["board"] == "remote"
    assert conn.execute("select cursor_value from source_cursors where source_id='jobs-jobicy-remote'").fetchone()["cursor_value"] == f"since={NOW.isoformat(timespec='seconds')}"
    usage = conn.execute("select usage from monitoring_runs where id=%s", (res.run_id,)).fetchone()["usage"]
    assert usage["pages"] == 2 and usage["hits_seen"] == 8 and usage["kept_financial"] == 3
    for kind, rid, change in res.structured_changes:
        emit_job_event(conn, rid, change)
    ev = conn.execute("select event_type, subject_org_id, attrs from events where subject_name_raw='Harbourline Bank'").fetchone()
    assert ev["event_type"] == "engineering_job_opened" and str(ev["subject_org_id"]) == org and ev["attrs"]["provider"] == "jobicy" and ev["attrs"]["role_of_subject"] == "bank"
    pay = conn.execute("select event_type, attrs from events where subject_name_raw='Quillon & Payments'").fetchone()
    assert pay["event_type"] == "contractor_job_opened" and pay["attrs"]["role_of_subject"] == "fintech"
    # a later run that no longer lists a job does not close it: the list is a seven-day window, not a full listing
    seen_later = JobicyConnector(client(lambda r: httpx.Response(200, text=ROBOTS_OK) if r.url.path == "/robots.txt" else httpx.Response(200, json={**JOBICY["page2"], "jobs": []})), now_fn=lambda: NOW)
    res2 = run_source(conn, seen_later, resolve_org=resolver, allow_pending=True, now_fn=lambda: NOW)
    assert res2.status == "success"
    assert {r["open_status"] for r in conn.execute("select open_status from job_postings").fetchall()} == {"open"}
    assert not [e for e in conn.execute("select 1 from events where event_type='job_closed'").fetchall()]


def test_remoteok_runs_through_the_pipeline(conn):
    from ri.ingest.pipeline import run_source
    seed_registry(conn)
    conn.commit()
    c = RemoteOkConnector(remoteok_client([]), now_fn=lambda: NOW)
    res = run_source(conn, c, allow_pending=True, now_fn=lambda: NOW)
    assert res.status == "success" and res.new == 2, res.error
    assert {r["job_id"] for r in conn.execute("select job_id from job_postings where provider='remoteok'").fetchall()} == {"5001", "5002"}


# ------------------------------------------------------------------------------------------------------------ Germany: notices
def day_zip(*names: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, name in enumerate(names):
            z.writestr(f"{i:03d}-{name}", (FIX / name).read_bytes())
    return buf.getvalue()


DAYS = {"2026-11-25": ("de_national_cn.xml", "de_eforms_cn.xml", "de_eu_notice.xml", "de_award.xml", "de_not_software.xml", "de_licence.xml", "de_closed.xml", "de_no_deadline.xml", "de_entity.xml"),
        "2026-11-26": ("de_eforms_correction.xml", "de_national_cancel.xml")}
API_DOC = {"info": {"title": "Bekanntmachungsservice: OpenData API"}, "paths": {"/api/notice-exports": {"get": {}}}}


def de_client(seen: list, robots_status: int = 404, robots: str = "", days=None, api_doc=None):
    days = DAYS if days is None else days

    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path == "/robots.txt":
            return httpx.Response(robots_status, text=robots or "<html>not found</html>")
        if req.url.path == "/documentation/api/opendata":
            return httpx.Response(200, json=API_DOC if api_doc is None else api_doc)
        assert req.url.host == "oeffentlichevergabe.de" and req.url.path == "/api/notice-exports"
        assert req.headers["Accept"] == "application/vnd.bekanntmachungsservice.eforms.zip+zip" and req.headers["User-Agent"].startswith("CodestRevenueIntelligence/")
        assert req.url.params["format"] == "eforms.zip" and "pubMonth" not in req.url.params
        day = req.url.params["pubDay"]
        return httpx.Response(200, content=day_zip(*days.get(day, ())), headers={"content-type": "application/vnd.bekanntmachungsservice.eforms.zip+zip"})
    return client(handler)


def german(seen=None, waits=None, **kw):
    c = GermanNoticeConnector(de_client(seen if seen is not None else [], **kw), now_fn=lambda: NOW, backfill_days=2)
    c.sleep = no_sleep(waits if waits is not None else [])
    return c


def test_german_day_keeps_national_software_notices_with_a_buyer_and_a_deadline():
    seen, waits = [], []
    c = german(seen, waits)
    b1 = run(c.fetch(None, 100))
    assert [i.payload["notice_id"] for i in b1.items] == ["26990001", "aaaa1111-0000-4000-8000-000000000001"]
    assert b1.has_more and b1.next_cursor == "day=2026-11-26" and b1.complete_listing is False
    u = b1.quota_usage
    assert u["notices_seen"] == 9 and u["kept"] == 2
    assert (u["dropped_on_ted"], u["dropped_not_competition"], u["dropped_not_software"], u["dropped_closed"], u["dropped_no_deadline"], u["dropped_unreadable"]) == (1, 1, 2, 1, 1, 1)
    b2 = run(c.fetch(b1.next_cursor, 100))
    assert sorted(i.payload["notice_id"] for i in b2.items) == ["26990001", "aaaa1111-0000-4000-8000-000000000002"]          # the correction and the withdrawal
    assert not b2.has_more and b2.next_cursor == "day=2026-11-27"
    assert waits == [2.0]                                                              # a pause between two days' downloads
    assert [r.url.params["pubDay"] for r in seen if r.url.path == "/api/notice-exports"] == ["2026-11-25", "2026-11-26"]
    assert sum(1 for r in seen if r.url.path == "/robots.txt") == 1


def test_german_service_only_has_days_up_to_yesterday():
    seen = []
    c = german(seen)
    b = run(c.fetch("day=2026-11-27", 100))          # today in Berlin: not available yet, so nothing is requested
    assert b.items == [] and not b.has_more and b.next_cursor == "day=2026-11-27" and not [r for r in seen if r.url.path == "/api/notice-exports"]
    late = GermanNoticeConnector(de_client([]), now_fn=lambda: datetime(2026, 11, 26, 23, 30, tzinfo=timezone.utc), backfill_days=1)          # 00:30 on the 27th in Berlin
    assert late.last_day(datetime(2026, 11, 26, 23, 30, tzinfo=timezone.utc)) == date(2026, 11, 26)
    assert late.last_day(datetime(2026, 11, 26, 22, 30, tzinfo=timezone.utc)) == date(2026, 11, 25)          # still the 26th in Berlin
    first = run(GermanNoticeConnector(de_client([]), now_fn=lambda: NOW, backfill_days=21).fetch(None, 100))
    assert first.next_cursor == "day=2026-11-07"          # the first run starts 21 days back (the 6th), one day per call


def test_german_notices_become_the_same_notice_record_as_the_other_tender_sources():
    c = german()
    b = run(c.fetch(None, 100))
    national, eforms = (c.canonicalize(i) for i in b.items)
    # the fields the notice tables are written from (ingest.pipeline.upsert_notice): the same set UK Find a Tender gives
    assert set(national.structured) == {"notice_id", "buyer_name", "title", "scope", "procedure", "service_codes", "country", "published_at", "amended_at", "deadline_at",
                                        "deadline_tz", "status", "official_url", "notice_kind", "value_if_reported"}
    s = national.structured
    assert national.kind == "procurement_notice" and s["notice_id"] == "26990001" and s["buyer_name"] == "Stadtwerke Nordfeld" and s["country"] == "DE" and s["status"] == "open"
    assert s["service_codes"] == ["72212000"] and s["deadline_at"] == datetime.fromisoformat("2099-12-15T10:00:00+01:00") and s["deadline_tz"] == "+01:00"
    assert s["official_url"] == "https://oeffentlichevergabe.de/api/notices/26990001?format=pdf" == national.canonical_url          # the notice itself, as the service publishes it
    assert "https://bieterportal.example.test/tender/26990001" in s["scope"] and s["notice_kind"] == "contract_notice" and s["published_at"].year == 2026
    e = eforms.structured
    assert e["notice_id"] == "ffff9999-0000-4000-8000-0000000000aa" and e["buyer_name"] == "Landesamt Beispielland für Digitales"      # the buyer comes from the organisations list
    assert e["deadline_at"] == datetime.fromisoformat("2099-12-10T09:30:00+01:00")                                                     # the first lot to close
    assert e["title"].startswith("Entwicklung eines Fachverfahrens") and e["procedure"] == "open" and e["amended_at"] is None
    assert eforms.external_id == "aaaa1111-0000-4000-8000-000000000001" and national.raw_meta["notice_type"] == "cn-standard"


def test_german_correction_moves_the_deadline_and_a_withdrawal_closes_the_notice(conn):
    from ri.events.structured import emit_procurement_events
    from ri.ingest.pipeline import run_source
    from ri.ingest.process import finish_event
    seed_registry(conn)
    conn.commit()
    c = german()
    res = run_source(conn, c, allow_pending=True, now_fn=lambda: NOW)
    assert res.status == "success" and (res.new, res.changed) == (3, 1), res.error          # the withdrawal is a new version of notice 26990001
    rows = {r["notice_id"]: r for r in conn.execute("select notice_id, source_id, buyer_name, status, deadline_at, country, service_codes, official_url, amended_at from procurement_notices").fetchall()}
    assert set(rows) == {"26990001", "ffff9999-0000-4000-8000-0000000000aa"} and {r["source_id"] for r in rows.values()} == {"procurement-de-bekanntmachung"}
    e, n = rows["ffff9999-0000-4000-8000-0000000000aa"], rows["26990001"]
    assert e["status"] == "amended" and e["deadline_at"] == datetime.fromisoformat("2099-12-20T09:30:00+01:00") and e["amended_at"] is not None          # the correction moved both lots by ten days
    assert n["status"] == "cancelled" and n["buyer_name"] == "Stadtwerke Nordfeld"
    assert conn.execute("select cursor_value from source_cursors where source_id='procurement-de-bekanntmachung'").fetchone()["cursor_value"] == "day=2026-11-27"
    usage = conn.execute("select usage from monitoring_runs where id=%s", (res.run_id,)).fetchone()["usage"]
    assert usage["pages"] == 2 and usage["notices_seen"] == 11 and usage["kept"] == 4 and usage["dropped_on_ted"] == 1
    kinds = [ch for _, _, ch in res.structured_changes]
    assert kinds.count("new") == 2 and any(k.startswith("amended:deadline") for k in kinds) and any("status" in k for k in kinds)
    for kind, rid, change in res.structured_changes:
        eid = emit_procurement_events(conn, rid, change)
        if eid:
            finish_event(conn, eid)
    evs = {r["attrs"]["notice_id"]: r for r in conn.execute("select event_type, attrs from events where attrs ? 'notice_id' order by first_observed_at").fetchall()}
    assert evs["ffff9999-0000-4000-8000-0000000000aa"]["attrs"]["relevant_services"] is True           # real software work: the Tenders page lists it
    # the Tenders page's own rule picks the open one and leaves the withdrawn one out
    from ri.tenders import open_relevant_sql
    rel, args = open_relevant_sql("n")
    listed = [r["notice_id"] for r in conn.execute(f"select n.notice_id from procurement_notices n where {rel}", args).fetchall()]          # noqa: S608
    assert listed == ["ffff9999-0000-4000-8000-0000000000aa"]


def test_german_connector_refuses_robots_unreadable_exports_and_oversized_exports():
    blocked = german(robots_status=200, robots="User-agent: *\nDisallow: /api/\n")
    with pytest.raises(PolicyBlocked):
        run(blocked.fetch(None, 100))
    assert run(blocked.health_check()).ok is False
    ai_no = german(robots_status=200, robots="User-agent: *\nContent-Signal: ai-input=no\nAllow: /\n")
    with pytest.raises(PolicyBlocked):
        run(ai_no.fetch(None, 100))

    def broken(req):
        return httpx.Response(404, text="<html>no</html>") if req.url.path == "/robots.txt" else httpx.Response(200, content=b"this is not a zip")
    with pytest.raises(SchemaDrift):
        run(GermanNoticeConnector(client(broken), now_fn=lambda: NOW, backfill_days=1).fetch(None, 100))

    def huge(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404, text="x")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("big.xml", b"<a>" + b"x" * 6_100_000 + b"</a>")
        return httpx.Response(200, content=buf.getvalue())
    with pytest.raises(SchemaDrift):
        run(GermanNoticeConnector(client(huge), now_fn=lambda: NOW, backfill_days=1).fetch(None, 100))

    def limited(req):
        return httpx.Response(404, text="x") if req.url.path == "/robots.txt" else httpx.Response(429, headers={"Retry-After": "900"})
    c = GermanNoticeConnector(client(limited), now_fn=lambda: NOW, backfill_days=1)
    c.sleep = no_sleep([])
    with pytest.raises(RateLimited):
        run(c.fetch(None, 100))

    def missing(req):
        return httpx.Response(404, text="x") if req.url.path == "/robots.txt" else httpx.Response(404, json={"error": "not found"})
    with pytest.raises(NotFound):
        run(GermanNoticeConnector(client(missing), now_fn=lambda: NOW, backfill_days=1).fetch(None, 100))


def test_german_health_check_reads_the_small_api_description_not_a_days_export():
    seen = []
    c = german(seen)
    assert run(c.health_check()).ok is True
    assert [r.url.path for r in seen if r.url.path != "/robots.txt"] == ["/documentation/api/opendata"]
    bad = german(api_doc={"paths": {}})
    assert run(bad.health_check()).error_class == "schema_drift"


def test_german_helpers_read_dates_notice_origin_and_deadlines():
    assert when("2099-12-08+01:00", "12:00:00+01:00") == datetime.fromisoformat("2099-12-08T12:00:00+01:00")
    assert when("2026-07-01Z", "10:00:00Z") == datetime(2026, 7, 1, 10, tzinfo=timezone.utc)
    assert when("2026-01-15", "10:00:00").utcoffset().total_seconds() == 3600          # no offset: German local time (winter)
    assert when("2026-07-15", "10:00:00").utcoffset().total_seconds() == 7200          # and summer time
    assert when("not a date") is None and when(None) is None
    assert on_ted({"subtypes": ["16"], "regime": None}) and on_ted({"subtypes": [], "regime": "32014L0025"}) and not on_ted({"subtypes": [], "regime": "de-vol"})
    assert not on_ted({"subtypes": [], "regime": None})
    a, b = datetime(2026, 12, 1, tzinfo=timezone.utc), datetime(2026, 12, 9, tzinfo=timezone.utc)
    assert pick_deadline([b, a], datetime(2026, 11, 27, tzinfo=timezone.utc)) == a and pick_deadline([b, a], datetime(2026, 12, 5, tzinfo=timezone.utc)) == b
    assert pick_deadline([b, a], datetime(2027, 1, 1, tzinfo=timezone.utc)) == b and pick_deadline([], NOW) is None
    assert parse_notice(b"<not-xml") is None and parse_notice((FIX / "de_entity.xml").read_bytes()) is None


# ------------------------------------------------------------------------------------------------------------ registry and wiring
NEW = ("jobs-jobicy-remote", "jobs-remoteok", "procurement-de-bekanntmachung")


def test_new_sources_wait_for_the_daily_test_and_that_test_may_switch_them_on(conn):
    from ri.sources.autovalidate import policy
    from ri.sources.factory import build_connector
    from ri.sources.registry import get
    seed_registry(conn)
    conn.commit()
    kinds = {"jobs-jobicy-remote": JobicyConnector, "jobs-remoteok": RemoteOkConnector, "procurement-de-bekanntmachung": GermanNoticeConnector}
    for sid in NEW:
        src = get(conn, sid)
        assert src["status"] == "pending_connector_validation" and src["collection_method"] == "official_api" and src["auth_mode"] == "none" and src["licence_notes"]
        assert policy(src, None)["eligible"] is True, sid          # public and keyless, and nothing in the notes asks a person to check first
        c = build_connector(conn, sid)
        assert isinstance(c, kinds[sid]) and c.describe().source_id == sid
    assert "link back" in get(conn, "jobs-remoteok")["licence_notes"] and "CC0" in get(conn, "procurement-de-bekanntmachung")["licence_notes"]
    assert get(conn, "procurement-de-bekanntmachung")["source_family"] == "procurement" and get(conn, "jobs-jobicy-remote")["source_family"] == "jobs"


def test_job_findings_of_the_new_boards_count_for_their_sources():
    from ri.api.admin_ops import JOB_PROVIDER_SOURCE
    assert JOB_PROVIDER_SOURCE["jobicy"] == "jobs-jobicy-remote" and JOB_PROVIDER_SOURCE["remoteok"] == "jobs-remoteok"
