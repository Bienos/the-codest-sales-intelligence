"""Review follow-ups: UK tender feed parameters, documents never lost when the model is unavailable, multilingual triage, Today lanes."""
import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from ri.ingest.pipeline import RunResult
from ri.ingest.process import process_run
from ri.sources.base import BillingError


def test_find_a_tender_never_sends_a_comma_list_always_sends_a_window_and_keeps_only_software():
    from ri.sources.procurement import FindTenderConnector
    seen = []
    rel = lambda ocid, title, tag, cpv: {"ocid": ocid, "date": "2026-11-21T10:00:00Z", "tag": [tag], "tender": {"title": title, "items": [{"classification": {"id": cpv}}]}}      # noqa: E731
    pkg = {"releases": [rel("a", "Data platform development", "tender", "72000000"), rel("b", "Road resurfacing", "tender", "45000000"),
                        rel("c", "Software licences", "planning", "48000000")], "links": {}}

    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, json=pkg)
    c = FindTenderConnector(httpx.AsyncClient(transport=httpx.MockTransport(handler)), now_fn=lambda: datetime(2026, 11, 22, tzinfo=timezone.utc))
    b = asyncio.run(c.fetch(None, 100))
    assert "stages" not in seen[0] and seen[0]["updatedFrom"] == "2026-11-08T00:00:00"
    assert [i.external_id for i in b.items] == ["a"]          # road works dropped, planning notice dropped


def _doc(conn, source_id, title, text="Bank selects a new payments platform and will migrate its core system."):
    conn.execute("insert into sources(source_id, display_name, source_tier, source_type, source_family, provider, status, collection_method) values (%s,%s,'S3','news','attention','test','active','feed') on conflict do nothing", (source_id, source_id))
    return str(conn.execute("insert into source_documents(source_id, external_id, version_key, title, content_text, raw_meta) values (%s,%s,'v',%s,%s,'{}') returning id",
                            (source_id, title, title, text)).fetchone()["id"])


class OutOfCredit:
    max_tokens_budget = 100

    def extract(self, *a, **k):
        raise BillingError("provider billing: credit balance is too low")


def test_new_documents_are_queued_when_the_model_is_missing_or_out_of_credit_never_lost(conn):
    a = _doc(conn, "attention-x", "Bank A selects new core platform")
    b = _doc(conn, "attention-x", "Bank B launches instant payments programme")
    process_run(conn, RunResult("r1", "success", new_doc_ids=[a, b]), extractor=None)
    pend = lambda: {r["id"] for r in conn.execute("select id::text from source_documents where raw_meta->>'pending_extraction'='true'").fetchall()}      # noqa: E731
    assert pend() == {a, b}
    import ri.ingest.process as proc
    real = proc.process_document
    proc.process_document = lambda *a, **k: (_ for _ in ()).throw(BillingError("provider billing: credit balance is too low"))
    try:
        out = process_run(conn, RunResult("r2", "success", new_doc_ids=[]), extractor=OutOfCredit())
    finally:
        proc.process_document = real
    assert out["deferred_provider"] == "provider_billing" and pend() == {a, b}          # still queued for the next run


def test_triage_reads_polish_german_nordic_and_funding_news_and_old_skips_are_rechecked(conn):
    from ri.events.triage import looks_relevant, retriage
    assert looks_relevant("Bank Pekao wybiera nowy system płatności", "")
    assert looks_relevant("Commerzbank startet neue Zahlungsplattform", "")
    assert looks_relevant("Nordea väljer ny betalningsplattform", "")
    assert looks_relevant("Fintech raises €20m Series A to expand", "")
    assert not looks_relevant("Quarterly results show growth", "")
    d = _doc(conn, "attention-pl", "mBank wybiera nowy system płatności", "mBank wybiera dostawcę nowego systemu płatności.")
    conn.execute("update source_documents set raw_meta='{\"triage\":\"skipped_not_relevant\"}' where id=%s", (d,))
    stay = _doc(conn, "attention-pl", "Wyniki kwartalne", "Wyniki kwartalne są dobre.")
    conn.execute("update source_documents set raw_meta='{\"triage\":\"skipped_not_relevant\"}' where id=%s", (stay,))
    assert retriage(conn) == 1
    assert conn.execute("select raw_meta->>'pending_extraction' p from source_documents where id=%s", (d,)).fetchone()["p"] == "true"
    assert retriage(conn) == 0          # the irrelevant one is checked once per word-list version, not every tick


def test_today_keeps_tenders_in_their_own_capped_lane_and_private_work_first(conn):
    from ri.access import Principal
    from ri.readmodel import PUBLIC_TENDER_LIMIT, _pre_section
    rel = set()
    base = {"priority": "urgent", "state": "verified", "blockers": [], "initiative_id": "i", "in_master_list": False}
    assert _pre_section({**base, "route_type": "procurement"}, rel) == "public_tenders"
    assert _pre_section({**base, "route_type": "direct"}, rel) == "critical"
    assert _pre_section({**base, "route_type": "direct", "priority": "medium"}, rel) == "new_discoveries"
    assert PUBLIC_TENDER_LIMIT == 5


def test_every_run_records_what_it_saw_and_the_canary_names_sources_that_never_return_anything(conn):
    from datetime import datetime, timezone
    from ri import canary
    from ri.ingest.pipeline import run_source
    from ri.sources.base import FetchBatch, SourceDescriptor
    conn.execute("insert into sources(source_id, display_name, source_tier, source_type, source_family, provider, status, collection_method) values ('silent-x','x','S1','procurement','procurement','t','active','official_api')")

    class Nothing:
        def describe(self):
            return SourceDescriptor("silent-x", "X", "official_api", "S1", "v")

        async def fetch(self, cursor, limit):
            return FetchBatch([], None, False, datetime.now(timezone.utc), quota_usage={"requests": 1, "releases_seen": 100, "releases_kept": 0})
    for _ in range(3):
        run_source(conn, Nothing())
    u = conn.execute("select usage from monitoring_runs where source_id='silent-x' order by started_at desc limit 1").fetchone()["usage"]
    assert u["pages"] == 1 and u["items"] == 0 and u["releases_seen"] == 100 and u["releases_kept"] == 0
    assert "silent_sources" in {b["code"] for b in canary.run_checks(conn)}


def test_a_run_that_stops_part_way_still_turns_what_it_stored_into_events_then_retries(conn):
    from datetime import datetime, timezone
    from ri.jobs.handlers import build_handlers
    from ri.jobs.queue import enqueue, run_one
    from ri.sources.base import FetchBatch, RateLimited, RawItem, SourceDescriptor, SourceDocumentDraft
    conn.execute("insert into sources(source_id, display_name, source_tier, source_type, source_family, provider, status, collection_method) values ('proc-x','x','S1','procurement','procurement','t','active','official_api')")

    class TwoPagesThenLimited:
        calls = 0

        def describe(self):
            return SourceDescriptor("proc-x", "X", "official_api", "S1", "v")

        async def fetch(self, cursor, limit):
            TwoPagesThenLimited.calls += 1
            if TwoPagesThenLimited.calls == 2:
                raise RateLimited("429 rate limited", retry_after=60)
            return FetchBatch([RawItem("n1", None, {})], "http://next", True, datetime.now(timezone.utc))

        def canonicalize(self, item):
            rec = {"notice_id": "N-1", "buyer_name": "Synthetic Buyer Ltd", "title": "Software development framework", "service_codes": ["72000000"], "country": "GB",
                   "published_at": datetime(2026, 11, 20, tzinfo=timezone.utc), "deadline_at": datetime(2026, 12, 20, tzinfo=timezone.utc), "deadline_tz": "Z", "status": "open",
                   "official_url": "https://example.invalid/n1", "notice_kind": "contract_notice"}
            return SourceDocumentDraft("n1", "v1", "https://example.invalid/n1", rec["title"], rec["published_at"], None, rec["title"], structured=rec, kind="procurement_notice")
    enqueue(conn, "source_run", {"source_id": "proc-x"}, idempotency_key="px")
    conn.commit()
    st = run_one(conn, "w", build_handlers(connector_factory=lambda sid: TwoPagesThenLimited()))
    assert st == "retryable_failed"
    assert conn.execute("select count(*) n from events where attrs->>'notice_id'='N-1'").fetchone()["n"] == 1


def test_notices_left_without_events_are_backfilled_once(conn):
    from ri.events.structured import backfill_procurement_events
    conn.execute("insert into sources(source_id, display_name, source_tier, source_type, source_family, provider, status, collection_method) values ('proc-y','y','S1','procurement','procurement','t','active','official_api')")
    conn.execute("insert into procurement_notices(source_id, notice_id, buyer_name, title, status, service_codes, country, official_url) values ('proc-y','N-9','Buyer','Data platform build','open','{72000000}','GB','https://example.invalid/9')")
    assert backfill_procurement_events(conn) == 1
    assert backfill_procurement_events(conn) == 0


def test_automatic_discovery_follows_the_saved_exa_budget_not_the_key(conn):
    from ri import budget, usage
    from ri.scheduler import exa_budget_set
    assert exa_budget_set(conn) is False
    budget.set_cap(conn, "exa", "monthly", usage.period_now(), 0, "usd")
    assert exa_budget_set(conn) is False
    budget.set_cap(conn, "exa", "monthly", usage.period_now(), 20, "usd")
    assert exa_budget_set(conn) is True


def test_lemlist_activity_we_do_not_use_is_not_stored(conn):
    from ri.outbound import enrol
    assert enrol.record_event(conn, {"type": "emailsOpened", "campaignId": "c", "leadEmail": "a@example.invalid", "_id": "1"}) == {"recorded": False, "reason": "ignored type"}
    assert conn.execute("select count(*) n from outbound_events").fetchone()["n"] == 0


# ---------------------------------------------------------------- tender relevance and lane (review of real production tenders)
import pytest as _pt


@_pt.mark.parametrize("title,codes,expected", [
    ("Zakup, dostawa i wdrożenie interaktywnych eksponatów edukacyjnych", ["39162000", "32322000", "48190000", "72263000"], False),      # museum exhibits
    ("Ekspertyza specjalistyczna tzw. tabulatury pelplińskiej", ["72312000", "79131000"], False),                                    # data entry, not engineering
    ("BEZ BARIER DO KARIER (sprzęt sportowy, meble, klimatyzacja do sal)", ["37440000", "48000000", "48190000"], False),
    ("Usługa wsparcia technicznego dla oprogramowania ManageEngine", ["72253200"], False),                                           # vendor product support
    ("Dzierżawa i serwis drukarek oraz urządzeń wielofunkcyjnych", ["30121300", "48000000"], False),
    ("Supply of laptops", ["30213100"], False),
    ("Dostawa i wdrożenie systemu klasy DLP", ["48000000", "72263000"], True),
    ("Aktualizacja oprogramowania DSpace i integracja z oprogramowaniem ScienceCloud", ["72262000"], True),
    ("Enterprise Resource Planning (ERP) Solution", ["72000000", "48000000"], True),
    ("Core banking platform migration and integration services", [], True),
    ("Digitalisierungskonzept", ["48000000"], True),
])
def test_only_real_software_work_counts_as_a_relevant_tender(title, codes, expected):
    from ri.events.classify import services_relevant
    assert services_relevant(title, None, codes) is expected


def test_tenders_are_always_public_and_schools_courts_hospitals_central_banks_are_public_bodies():
    from ri.market import is_public_body, lane_for
    assert lane_for("Hydro-Tech Sp. z o.o.", None, "procurement_published") == "public"
    assert lane_for("Hydro-Tech Sp. z o.o.", None, "negative_cancellation", from_tender=True) == "public"
    for n in ("PAŃSTWOWA UCZELNIA ZAWODOWA W SUWAŁKACH", "Zespół Szkół im. A. Średniawskiego", "SPECJALISTYCZNY PSYCHIATRYCZNY ZESPÓŁ OPIEKI ZDROWOTNEJ W ŁODZI",
              "Sąd Okręgowy w Gorzowie Wielkopolskim", "Central Bank of Ireland", "MINISTRSTVO ZA NOTRANJE ZADEVE IN JAVNO UPRAVO", "Kasa Rolniczego Ubezpieczenia Społecznego Centrala"):
        assert is_public_body(n), n
    assert lane_for("Nordea Bank Abp") == "private"


def test_old_tender_events_are_rechecked_once_and_wrongly_matched_signals_are_withdrawn(conn):
    from ri.events.structured import recheck_procurement_relevance
    ev = str(conn.execute("insert into events(event_key, event_type, summary, extraction_model_version, verification_status, attrs) values ('t-goods','procurement_published','x','t','verified',"
                          "'{\"notice_id\":\"N1\",\"scope\":\"Dzierżawa i serwis drukarek\",\"service_codes\":[\"30121300\",\"48000000\"],\"relevant_services\":true}') returning id").fetchone()["id"])
    from ri.signals.baseline import import_baseline
    import_baseline(conn)
    conn.execute("insert into signals(event_id, rule_id, rule_version, strength, demand_tier, verification_status, effective_from, reason_code) values (%s,'SRC-01','1',3,'D1','verified',now(),'r')", (ev,))
    out = recheck_procurement_relevance(conn)
    assert out == {"tenders_rechecked": 1, "tender_signals_dropped": 1}
    assert recheck_procurement_relevance(conn)["tenders_rechecked"] == 0
    assert conn.execute("select status from signals").fetchone()["status"] == "superseded"
