"""Findings in any language become English summaries with the original quote kept and translated; every finding gets a plain-English reason
why it could (or could not) be work for The Codest. Searches run per service and in the markets' own languages."""
import asyncio
import json

from ri.events.extract import XResult, clean_fit, process_document


def test_prompt_asks_for_english_summary_verbatim_original_quote_translation_and_fit():
    from pathlib import Path
    p = (Path(__file__).resolve().parents[1] / "prompts" / "extract_events_v1.txt").read_text()
    for needle in ("plain English", "VERBATIM in the document's original language", "anchor_en", "FIT FOR THE CODEST", "staff_augmentation", "Never invent a need"):
        assert needle in p


def test_fit_is_normalised_and_junk_is_dropped():
    assert clean_fit({"level": "STRONG", "service": "team_extension", "reason": "  Bank  migrates its core. "}, "DE") == {
        "language": "de", "fit": {"level": "strong", "service": "team_extension", "reason": "Bank migrates its core."}}
    assert clean_fit({"level": "great", "service": "x"}, "german") == {}
    assert clean_fit({"level": "possible", "service": "consulting", "reason": "r"}, None)["fit"]["service"] == "none"


class GermanExtractor:
    version = "fake:de"

    async def extract(self, *, title, text, published_at):
        return {"events": [{"event_type": "programme_announced", "subject_name": "Synthetische Bank AG", "event_date": None, "language": "de",
                            "summary": "Synthetische Bank AG is replacing its core banking system, with the migration starting in 2027.",
                            "fit": {"level": "strong", "service": "team_extension", "reason": "The bank is moving to a new core system. Migrations like this usually need extra engineering teams."},
                            "attrs": {"programme_domain": "core_banking", "role_of_subject": "bank"}, "other_entities": [],
                            "claims": [{"predicate": "replaces_core_system", "object": {}, "anchor": "ersetzt ihr Kernbankensystem",
                                        "anchor_en": "is replacing its core banking system"}]}]}


def test_a_german_article_keeps_the_german_quote_adds_the_english_one_and_the_reason(conn):
    conn.execute("insert into sources(source_id, display_name, source_tier, source_type, source_family, provider, status, collection_method) values ('de-news','DE','S3','news','attention','t','active','feed')")
    did = str(conn.execute("insert into source_documents(source_id, external_id, version_key, title, content_text) values ('de-news','1','v','Neues Kernbankensystem',"
                           "'Die Synthetische Bank AG ersetzt ihr Kernbankensystem. Die Migration beginnt 2027.') returning id").fetchone()["id"])
    out = process_document(conn, did, GermanExtractor())
    assert out["events"] == 1
    e = conn.execute("select summary, attrs from events").fetchone()
    assert e["summary"].startswith("Synthetische Bank AG is replacing") and e["attrs"]["language"] == "de"
    assert e["attrs"]["fit"]["level"] == "strong" and "engineering" in e["attrs"]["fit"]["reason"]
    c = conn.execute("select evidence_anchor, object_json from claims").fetchone()
    assert c["evidence_anchor"] == "ersetzt ihr Kernbankensystem" and c["object_json"]["anchor_en"] == "is replacing its core banking system"


def test_old_findings_get_a_reason_in_cheap_batches_and_unknown_ids_are_ignored(conn):
    from ri.events import fit
    ids = []
    for i in range(3):
        ids.append(str(conn.execute("insert into events(event_key, event_type, summary, extraction_model_version, verification_status, proven, lane, market_tier, pub_at) "
                                    "values (%s,'funding_announced',%s,'t','verified',true,'private','core', now()) returning id", (f"f{i}", f"Fintech {i} raised money to expand.")).fetchone()["id"]))
    seen = {}

    async def call(system, user):
        seen["user"] = user
        items = json.loads(user)["items"]
        reply = [{"id": it["id"], "level": "possible", "service": "staff_augmentation", "reason": "Growth money often means hiring engineers. The article does not say so."} for it in items]
        reply.append({"id": "00000000-0000-0000-0000-000000000000", "level": "strong", "service": "ai_services", "reason": "injected"})
        return json.dumps({"items": reply}), 500, 200, "fake"
    r = fit.assess(conn, call, lambda c: asyncio.run(c))
    assert r["assessed"] == 3 and "untrusted" in fit.SYSTEM
    assert {x["attrs"]["fit"]["level"] for x in conn.execute("select attrs from events").fetchall()} == {"possible"}
    assert fit.pending(conn) == []


def test_searches_cover_each_service_and_run_in_local_languages():
    from ri.discovery.plans import build_plans
    plans = build_plans()
    themes = {p.sector for p in plans}
    assert {"engineering-hiring", "ai-programme", "growth-funding", "market-launch", "core-banking"} <= themes
    langs = {p.language for p in plans}
    assert {"en", "de", "pl", "sv"} <= langs
    assert any("Kernbankensystem" in p.query for p in plans if p.language == "de")
    assert len({p.plan_id for p in plans}) == len(plans)
