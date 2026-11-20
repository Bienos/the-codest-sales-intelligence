import pytest
from datetime import datetime, timedelta, timezone

from ri import budget
from ri.events.budgeted import BudgetedExtractor
from ri.ingest.pipeline import RunResult, upsert_document
from ri.ingest.process import process_run
from ri.signals.baseline import import_baseline
from ri.signals.persist import sync_rule_versions
from ri.sources import registry
from ri.sources.base import BudgetExceeded, SourceDocumentDraft
from test_r1_events import TEXT, good_payload

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


class Inner:
    version = "fake/1"; max_tokens_budget = 1000; last_usage_tokens = 700
    def __init__(self, payload, fail=False): self.payload, self.fail, self.calls = payload, fail, 0
    async def extract(self, *, title, text, published_at):
        self.calls += 1
        if self.fail: raise TimeoutError("api down")
        return self.payload


@pytest.fixture()
def world(conn):
    import_baseline(conn); sync_rule_versions(conn); registry.seed_registry(conn)
    org = str(conn.execute("insert into organizations(display_name,country_code) values ('Example Bank','GB') returning id").fetchone()["id"])
    conn.execute("update sources set status='active', retention_policy='full_text' where source_id='vendor-temenos-news'")
    conn.commit()
    return org


def doc(conn, ext="d1"):
    d = SourceDocumentDraft(ext, ext, f"https://e.invalid/{ext}", "Example Bank selects Temenos", NOW - timedelta(days=3), TEXT, TEXT[:200])
    return upsert_document(conn, "vendor-temenos-news", d, "full_text")[0]


def test_zero_cap_blocks_model_and_document_is_deferred_not_lost(conn, world):
    inner = Inner(good_payload())
    d = doc(conn)
    res = RunResult("r", "success", new_doc_ids=[d])
    out = process_run(conn, res, extractor=BudgetedExtractor(inner, conn), resolve_org=lambda n: world, now=NOW)
    assert inner.calls == 0 and out["deferred_budget"] == 1                         # no cap => the model was never called
    row = conn.execute("select raw_meta->>'pending_extraction' p, content_text from source_documents where id=%s", (d,)).fetchone()
    assert row["p"] == "true" and row["content_text"]                                # queued with its text retained
    # budget arrives -> next run picks the pending document up without re-fetching
    budget.set_cap(conn, "anthropic", "monthly", BudgetedExtractor.period(), 100000, "tokens")
    out2 = process_run(conn, RunResult("r2", "success"), extractor=BudgetedExtractor(inner, conn), resolve_org=lambda n: world, now=NOW)
    assert inner.calls == 1 and conn.execute("select count(*) c from events").fetchone()["c"] == 1
    assert conn.execute("select raw_meta->>'pending_extraction' p from source_documents where id=%s", (d,)).fetchone()["p"] is None


def test_actual_usage_settled_failure_releases_reservation(conn, world):
    p = BudgetedExtractor.period()
    budget.set_cap(conn, "anthropic", "monthly", p, 100000, "tokens")
    d = doc(conn)
    process_run(conn, RunResult("r", "success", new_doc_ids=[d]), extractor=BudgetedExtractor(Inner(good_payload()), conn), resolve_org=lambda n: world, now=NOW)
    r = conn.execute("select spent, reserved from provider_budgets where provider='anthropic'").fetchone()
    assert float(r["spent"]) == 700.0 and float(r["reserved"]) == 0.0                 # billed on actual tokens, not the estimate
    d2 = doc(conn, "d2")
    with pytest.raises(TimeoutError):
        import asyncio
        asyncio.run(BudgetedExtractor(Inner({}, fail=True), conn).extract(title="t", text=TEXT, published_at=None))
    assert float(conn.execute("select reserved from provider_budgets where provider='anthropic'").fetchone()["reserved"]) == 0.0   # released on failure


def test_hard_cap_enforced_per_call(conn, world):
    budget.set_cap(conn, "anthropic", "monthly", BudgetedExtractor.period(), 100, "tokens")       # below one call's estimate
    import asyncio
    with pytest.raises(BudgetExceeded):
        asyncio.run(BudgetedExtractor(Inner(good_payload()), conn).extract(title="t", text=TEXT, published_at=None))


def test_bad_model_output_dead_letters_one_document_and_run_continues(conn, world):
    budget.set_cap(conn, "anthropic", "monthly", BudgetedExtractor.period(), 100000, "tokens")
    d1, d2 = doc(conn, "a"), doc(conn, "b")
    class Flaky(Inner):
        async def extract(self, **kw):
            self.calls += 1
            return {"nope": 1} if self.calls == 1 else self.payload
    out = process_run(conn, RunResult("r", "success", new_doc_ids=[d1, d2]), extractor=BudgetedExtractor(Flaky(good_payload()), conn), resolve_org=lambda n: world, now=NOW)
    assert out["extraction_failed"] == 1 and conn.execute("select count(*) c from events").fetchone()["c"] == 1
    assert conn.execute("select error_class from dead_letters where kind='extract_document'").fetchone()["error_class"] == "invalid_payload"
