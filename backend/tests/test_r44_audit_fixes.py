"""Round-4 audit fixes: the AI fit check survives long or broken answers, the same article from a sister feed is not paid for twice,
and the re-matcher resolves a repeated company name once. The model is a FAKE (no network). All names are invented."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from ri import budget
from ri.events import fit
from ri.events.budgeted import BudgetedExtractor
from ri.ingest.pipeline import RunResult, upsert_document
from ri.ingest.process import process_run
from ri.signals.baseline import import_baseline
from ri.signals.persist import sync_rule_versions
from ri.sources import registry
from ri.sources.base import SourceDocumentDraft
from test_r1_events import TEXT, good_payload

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ the AI fit check
def _findings(conn, n, prefix="h"):
    return [str(conn.execute("insert into events(event_key, event_type, summary, extraction_model_version, verification_status, proven, lane, market_tier, pub_at) "
                             "values (%s,'funding_announced',%s,'t','verified',true,'private','core', now()) returning id", (f"{prefix}{i}", f"Fintech {i} raised money.")).fetchone()["id"])
            for i in range(n)]


def _answer(items):
    return json.dumps({"items": [{"id": it["id"], "level": "possible", "service": "team_extension", "reason": "Growth money often means hiring.",
                                  "summary_en": "", "summary_pl": f"Fintech {i} pozyskał środki."} for i, it in enumerate(items)]})


def test_the_fit_check_asks_for_ten_at_a_time_in_a_fixed_format():
    assert fit.BATCH == 10
    props = fit.SCHEMA["properties"]["items"]["items"]
    assert set(props["required"]) == set(props["properties"]) and props["additionalProperties"] is False      # every field always present
    assert props["properties"]["level"]["enum"] == ["strong", "possible", "none"]
    assert fit.SCHEMA["additionalProperties"] is False


def test_a_cut_off_answer_splits_the_batch_in_halves_and_every_finding_still_gets_its_reason(conn):
    _findings(conn, 4)
    asked = []

    async def call(system, user):
        items = json.loads(user)["items"]
        asked.append(len(items))
        if len(items) > 2:
            raise ValueError("the answer was cut off at the length limit (4000 tokens)")
        return _answer(items), 100, 100, "fake"
    r = fit.assess(conn, call, lambda c: asyncio.run(c))
    assert asked == [4, 2, 2]                                       # one long call, then two halves: not four single calls
    assert r["assessed"] == 4 and r["skipped"] == 0
    assert fit.pending(conn) == []
    assert conn.execute("select count(*) c from events where attrs ? 'summary_pl'").fetchone()["c"] == 4


def test_an_answer_that_is_not_valid_json_is_retried_in_smaller_pieces_and_one_hopeless_finding_is_skipped(conn):
    ids = _findings(conn, 3, "j")
    bad = ids[2]

    async def call(system, user):
        items = json.loads(user)["items"]
        if any(it["id"] == bad for it in items):
            return '{"items": [{"id": "x", "reason": "a "quoted" word"}]}', 10, 10, "fake"           # unescaped quotes: broken JSON
        return _answer(items), 10, 10, "fake"
    r = fit.assess(conn, call, lambda c: asyncio.run(c))
    assert r["assessed"] == 2 and r["skipped"] == 1
    skipped = conn.execute("select attrs from events where id=%s", (bad,)).fetchone()["attrs"]
    assert "fit_skipped" in skipped
    assert fit.pending(conn) == []                                  # the hopeless one no longer blocks the queue


def test_other_errors_still_fail_loudly(conn):
    import pytest
    _findings(conn, 2, "k")

    async def call(system, user):
        raise ValueError("the model declined to answer")
    with pytest.raises(ValueError):
        fit.assess(conn, call, lambda c: asyncio.run(c))


# ------------------------------------------------------------------ the same article from a sister feed
class Inner:
    version = "fake/1"; max_tokens_budget = 1000; last_usage_tokens = 700

    def __init__(self, payload):
        self.payload, self.calls = payload, 0

    async def extract(self, *, title, text, published_at):
        self.calls += 1
        return self.payload


def _world(conn):
    import_baseline(conn); sync_rule_versions(conn); registry.seed_registry(conn)
    org = str(conn.execute("insert into organizations(display_name,country_code) values ('Example Bank','GB') returning id").fetchone()["id"])
    other = conn.execute("select source_id from sources where source_id<>'vendor-temenos-news' order by source_id limit 1").fetchone()["source_id"]
    conn.execute("update sources set status='active', retention_policy='full_text' where source_id in ('vendor-temenos-news', %s)", (other,))
    budget.set_cap(conn, "anthropic", "monthly", BudgetedExtractor.period(), 1_000_000, "tokens")      # without a cap the model is never called
    conn.commit()
    return org, other


def _doc(conn, source_id, url, version="v1", text=TEXT):
    d = SourceDocumentDraft(f"{source_id}-{version}", version, url, "Example Bank selects Temenos", NOW - timedelta(days=3), text, text[:200])
    return upsert_document(conn, source_id, d, "full_text")[0]


def test_the_same_article_from_a_sister_feed_is_linked_not_read_again(conn):
    org, other = _world(conn)
    inner = Inner(good_payload())
    ex = BudgetedExtractor(inner, conn)
    url = "https://e.invalid/news/example-bank-temenos"
    d1 = _doc(conn, "vendor-temenos-news", url)
    process_run(conn, RunResult("r1", "success", new_doc_ids=[d1]), extractor=ex, resolve_org=lambda n: org, now=NOW)
    assert inner.calls == 1
    events_before = conn.execute("select count(*) c from events").fetchone()["c"]
    d2 = _doc(conn, other, url)
    out = process_run(conn, RunResult("r2", "success", new_doc_ids=[d2]), extractor=ex, resolve_org=lambda n: org, now=NOW)
    assert inner.calls == 1 and out["same_article"] == 1                                              # not paid for twice
    assert conn.execute("select count(*) c from events").fetchone()["c"] == events_before             # and no duplicate finding
    assert conn.execute("select count(*) c from event_documents where document_id=%s", (d2,)).fetchone()["c"] >= 1     # but the finding now cites both feeds
    meta = conn.execute("select raw_meta from source_documents where id=%s", (d2,)).fetchone()["raw_meta"]
    assert meta["triage"] == "same_article_as_another_source" and "pending_extraction" not in meta


def test_a_changed_page_of_the_same_source_is_still_read_again(conn):
    org, _ = _world(conn)
    inner = Inner(good_payload())
    ex = BudgetedExtractor(inner, conn)
    url = "https://e.invalid/news/example-bank-temenos"
    d1 = _doc(conn, "vendor-temenos-news", url)
    process_run(conn, RunResult("r1", "success", new_doc_ids=[d1]), extractor=ex, resolve_org=lambda n: org, now=NOW)
    d2 = _doc(conn, "vendor-temenos-news", url, version="v2", text=TEXT + " Update: the migration now starts in 2027.")
    assert d2 != d1
    out = process_run(conn, RunResult("r2", "success", changed_doc_ids=[d2]), extractor=ex, resolve_org=lambda n: org, now=NOW)
    assert inner.calls == 2 and "same_article" not in out                                             # a new version of the page is new text


# ------------------------------------------------------------------ the re-matcher
def test_a_repeated_company_name_is_looked_up_once_per_run(conn, monkeypatch):
    from ri.events import relink
    from ri.identity.resolver import Decision
    for i, name in enumerate(["Acme Harbour Bank", "ACME HARBOUR BANK ", "acme harbour bank", "Other Council"]):
        conn.execute("insert into events(event_key,event_type,subject_org_id,subject_name_raw,summary,attrs,other_entity_roles,extraction_model_version,verification_status,independent_origin_group) "
                     "values (%s,'vendor_selected',null,%s,'x','{}','[]','t','verified',%s)", (f"k-m{i}", name, f"g-m{i}"))
    conn.execute("insert into organizations(display_name,country_code) values ('Newcomer Bank','GB')")
    conn.commit()
    seen: list[str] = []

    def fake_resolve(c, name, allow_create):
        seen.append(name)
        return Decision(action="review")
    monkeypatch.setattr(relink, "resolve", fake_resolve)
    out = relink.relink_unmatched(conn)
    assert out == {"checked": 4, "linked": 0} and len(seen) == 2                                     # two distinct names, four findings
    assert conn.execute("select count(*) c from events where attrs ? 'relink_checked_at'").fetchone()["c"] == 4
