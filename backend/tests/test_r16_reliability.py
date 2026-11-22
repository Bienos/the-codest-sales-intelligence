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
