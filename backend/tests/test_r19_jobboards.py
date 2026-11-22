"""Job boards: SmartRecruiters and Sweden's JobTech (fixtures are recorded from the live services), the keyword fix, name-checked board guessing, and market-level employers."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

FIX = json.loads((Path(__file__).parent / "fixtures" / "jobboards_sample.json").read_text())


def test_go_the_word_is_not_go_the_language():
    from ri.sources.ats import tech_keywords
    assert "golang" not in tech_keywords("We want you to go further and go the extra mile. Python and Kafka.")
    assert "golang" in tech_keywords("Senior Go developer, AWS, Kafka")
    assert "golang" in tech_keywords("Backend (Go) and Kubernetes")
    assert "golang" in tech_keywords("Stack: Python, Go and Terraform")


def _smartrecruiters_client():
    detail = FIX["smartrecruiters_detail"]

    def handler(req):
        if req.url.path.endswith("/postings"):
            return httpx.Response(200, json=FIX["smartrecruiters_list"])
        return httpx.Response(200, json=detail)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_smartrecruiters_reads_pages_details_and_only_a_full_listing_is_complete():
    from ri.sources.ats import SmartRecruitersConnector
    c = SmartRecruitersConnector(_smartrecruiters_client(), "BoschGroup", employer_name="Bosch")
    b = asyncio.run(c.fetch(None, 500))
    assert len(b.items) == 3 and b.complete_listing is True and b.quota_usage["postings"] == 3
    d = c.canonicalize(b.items[0])
    s = d.structured
    assert s["provider"] == "smartrecruiters" and s["board"] == "BoschGroup" and s["open_status"] == "open" and d.canonical_url.startswith("https://jobs.smartrecruiters.com/BoschGroup/")
    assert s["employment_type"] in {"permanent", "contract", "internship", "unknown"} and s["posted_at"] is not None
    short = dict(FIX["smartrecruiters_list"], totalFound=500)          # the board says 500 but we only received 3: not complete, so nothing may be closed
    cl = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=short)))
    c2 = SmartRecruitersConnector(cl, "BoschGroup")
    c2.DETAIL_CAP = 0
    b2 = asyncio.run(c2.fetch(None, 500))
    assert b2.complete_listing is False


def test_jobtech_keeps_only_financial_employers_and_maps_swedish_employment_labels():
    from ri.sources.jobs_market import JobTechConnector, employment_from_label
    page = FIX["jobtech"]

    def handler(req):
        q = dict(req.url.params)
        assert q["occupation-field"] == "apaJ_2ja_LuF" and "published-after" in q
        return httpx.Response(200, json=page if q["q"] == "bank" else {"total": {"value": 0}, "hits": []})
    c = JobTechConnector(httpx.AsyncClient(transport=httpx.MockTransport(handler)), queries=["bank", "fintech"], now_fn=lambda: datetime(2026, 11, 22, tzinfo=timezone.utc))
    b = asyncio.run(c.fetch(None, 500))
    names = {c.canonicalize(i).structured["employer_name"] for i in b.items}
    assert "Svea Bank AB" in names and b.complete_listing is False and b.next_cursor.startswith("since:2026-11-22")
    assert b.quota_usage["hits_seen"] >= b.quota_usage["kept_financial"] >= 1          # what was seen versus kept is recorded
    d = c.canonicalize(b.items[0])
    assert d.structured["provider"] == "jobtech" and d.structured["board"] == "se" and d.structured["country"] == "SE" and d.kind == "job_posting"
    assert employment_from_label("Vanlig anställning", "x") == "permanent" and employment_from_label("Tidsbegränsad anställning", "x") == "contract"
    assert employment_from_label("Behovsanställning", "x") == "freelance"


def test_a_guessed_board_is_used_only_when_its_own_company_name_matches():
    from ri.sources.account_scan import name_matches, probe_boards
    assert name_matches("Svea Bank AB", "Svea Bank") and name_matches("Robert Bosch GmbH", "Bosch Group") and name_matches("Stripe, Inc.", "Stripe")
    assert not name_matches("Apex Capital Partners Ltd", "Apex Fintech Solutions") and not name_matches("Delta Bank AG", "Delta Air Lines")

    def handler(req):
        if "greenhouse" in req.url.host:
            return httpx.Response(200, json={"name": "Apex Fintech Solutions"})          # a different company with the same short name
        return httpx.Response(200, json={"content": [{"company": {"name": "Acme Payments"}}]})
    cl = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert asyncio.run(probe_boards(cl, "apex.com", "Apex Capital Partners Ltd")) == []
    assert asyncio.run(probe_boards(cl, "acme.com", "Acme Payments Ltd")) == [("smartrecruiters", "acme")]


def test_smartrecruiters_links_on_a_careers_page_are_detected():
    from ri.sources.account_scan import parse_page
    html = '<a href="https://careers.smartrecruiters.com/BoschGroup/">Jobs</a><a href="https://jobs.lever.co/acme">x</a>'
    assert ("smartrecruiters", "BoschGroup") in parse_page(html, "https://x.com/careers", "x.com")["ats"]


def test_market_jobs_link_the_employer_by_name_and_a_financial_employer_can_become_a_candidate(conn):
    from ri.ingest.pipeline import run_source
    from ri.events.structured import emit_job_event, employer_role
    from ri.sources.base import FetchBatch, RawItem, SourceDescriptor, SourceDocumentDraft
    conn.execute("insert into sources(source_id, display_name, source_tier, source_type, source_family, provider, status, collection_method) values ('jobs-x','x','S2','jobs','jobs','t','active','official_api')")
    known = str(conn.execute("insert into organizations(display_name, country_code) values ('Svea Bank AB','SE') returning id").fetchone()["id"])

    class Market:
        def describe(self):
            return SourceDescriptor("jobs-x", "X", "official_api", "S2", "v")

        async def fetch(self, cursor, limit):
            return FetchBatch([RawItem("1", None, {"e": "Svea Bank AB"}), RawItem("2", None, {"e": "Nordea Bank Abp"})], None, False, datetime.now(timezone.utc))

        def canonicalize(self, item):
            rec = {"provider": "jobtech", "board": "se", "job_id": item.external_id, "employer_name": item.payload["e"], "title": "Senior Backend Engineer", "employment_type": "contract",
                   "posted_at": datetime(2026, 11, 20, tzinfo=timezone.utc), "url": "https://example.invalid/j", "tech_keywords": ["java", "kafka"], "open_status": "open"}
            return SourceDocumentDraft(item.external_id, "v", rec["url"], rec["title"], rec["posted_at"], None, "x", structured=rec, kind="job_posting")
    resolve = lambda name: known if name == "Svea Bank AB" else None      # noqa: E731
    res = run_source(conn, Market(), resolve_org=resolve)
    rows = {r["employer_name"]: r for r in conn.execute("select employer_name, organization_id from job_postings").fetchall()}
    assert str(rows["Svea Bank AB"]["organization_id"]) == known and rows["Nordea Bank Abp"]["organization_id"] is None
    for kind, rid, change in res.structured_changes:
        emit_job_event(conn, rid, change)
    ev = conn.execute("select subject_org_id, attrs from events where subject_name_raw='Nordea Bank Abp'").fetchone()
    assert ev["subject_org_id"] is None and ev["attrs"]["role_of_subject"] == "bank"
    assert employer_role("Agile Resources AB") is None
