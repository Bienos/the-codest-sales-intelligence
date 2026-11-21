"""Account research brief. The model is a FAKE; what matters is that unquoted or invented statements never survive."""
import asyncio
import json

import pytest

from ri.research.brief import build_sources, generate, user_prompt, verify_items
from test_r4_crm_notify import mk_user


def _org(conn):
    org = conn.execute("insert into organizations(display_name, country_code, segment, employees_estimate, canonical_domain, in_master_list) values ('Synthetic Brief Bank AG','DE','Bank',6000,'brief-bank.example',true) returning id").fetchone()["id"]
    conn.execute("insert into source_documents(source_id, external_id, version_key, canonical_url, title, content_text) "
                 "select 'corporate-ir-newsrooms','p1','v','https://brief-bank.example/news/core','Core platform news','Synthetic Brief Bank AG is replacing its core banking platform with a phased migration starting in January.' "
                 "where exists (select 1 from sources where source_id='corporate-ir-newsrooms')")
    conn.execute("insert into job_postings(provider, board, job_id, organization_id, employer_name, title, employment_type, open_status, last_seen_at, tech_keywords) values ('greenhouse','b','1',%s,'Synthetic Brief Bank AG','Senior Java Engineer, Payments','contract','open',now(),'{kafka,aws}')", (org,))
    conn.execute("insert into account_context(organization_id, kind, value, added_by) values (%s,'note','Met the CTO at a payments conference','bdm@x')", (org,))
    conn.commit()
    return str(org)


def _fake(items):
    async def call(system, user):
        return json.dumps(items), 1200, 300, "fake-model"
    return call


def test_sources_are_numbered_and_include_pages_roles_and_notes(conn):
    from ri.sources.registry import seed_registry
    seed_registry(conn)
    org = _org(conn)
    src, rec = build_sources(conn, org)
    kinds = [v["kind"] for v in src.values()]
    assert "company page" in kinds and "open roles" in kinds and "colleague note" in kinds and rec["employees"] == 6000
    assert "Senior Java Engineer" in user_prompt(src, rec)


def test_only_statements_whose_quote_is_really_in_the_source_survive(conn):
    from ri.sources.registry import seed_registry
    seed_registry(conn)
    org = _org(conn)
    src, _ = build_sources(conn, org)
    page = next(k for k, v in src.items() if v["kind"] == "company page")
    roles = next(k for k, v in src.items() if v["kind"] == "open roles")
    items = [
        {"statement": "It is replacing its core platform.", "source": page, "quote": "is replacing its core banking platform with a phased migration"},
        {"statement": "It has a 50 million euro budget.", "source": page, "quote": "a 50 million euro budget for the programme"},      # invented
        {"statement": "It hires Java engineers.", "source": roles, "quote": "senior java engineer, payments"},                       # case and spacing differences are fine
        {"statement": "Wrong source.", "source": "S99", "quote": "anything at all that is long enough"},
        {"statement": "Too short a quote.", "source": page, "quote": "core"},
    ]
    kept, dropped = verify_items(items, src)
    assert [k["statement"] for k in kept] == ["It is replacing its core platform.", "It hires Java engineers."] and dropped == 3
    assert kept[0]["source_url"] == "https://brief-bank.example/news/core"


def test_generate_builds_a_brief_from_a_model_reply_and_counts_what_it_dropped(conn):
    from ri.sources.registry import seed_registry
    seed_registry(conn)
    org = _org(conn)
    src, _ = build_sources(conn, org)
    page = next(k for k, v in src.items() if v["kind"] == "company page")
    reply = {"summary": [{"statement": "Core platform replacement.", "source": page, "quote": "replacing its core banking platform"}], "signals": [{"statement": "Invented signal", "source": page, "quote": "this text is not in the page at all"}],
             "hiring": [], "open_questions": ["Who owns delivery?", "Is delivery sourced externally?"], "icp_fit": {"read": "A bank of the right size.", "reasons": []}}
    out = generate(conn, org, _fake(reply), lambda coro: asyncio.run(coro))
    c = out["content"]
    assert len(c["summary"]) == 1 and c["signals"] == [] and out["dropped"] == 1 and c["open_questions"][0] == "Who owns delivery?" and out["model"] == "fake-model"


def test_a_company_with_nothing_stored_gets_an_honest_empty_brief_without_calling_the_model(conn):
    org = str(conn.execute("insert into organizations(display_name) values ('Synthetic Nothing Ltd') returning id").fetchone()["id"])
    conn.commit()
    called = []
    async def never(system, user):
        called.append(1)
        return "{}", 0, 0, "x"
    out = generate(conn, org, never, lambda coro: asyncio.run(coro))
    assert not called and out["content"]["summary"] == [] and "no stored page" in out["content"]["note"]


def test_brief_endpoints_queue_one_at_a_time_and_return_the_latest(conn, db_url):
    from fastapi.testclient import TestClient
    from ri.api.app import create_app
    from ri.api.auth import make_dev_token
    from ri.config import Settings
    from ri.db import Database
    from test_r7_verify_discovery_usage import SECRET
    mk_user(conn, "bdm@x", "bdm", 555)
    org = _org(conn)
    conn.commit()
    db = Database(db_url, max_size=2)
    try:
        c = TestClient(create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET), db=db))
        h = {"Authorization": f"Bearer {make_dev_token(SECRET, 'bdm@x')}"}
        r = c.post(f"/api/accounts/{org}/brief", headers=h)
        assert r.status_code == 200 and r.json()["status"] == "queued"
        assert c.post(f"/api/accounts/{org}/brief", headers=h).status_code == 409
        items = c.get(f"/api/accounts/{org}/briefs", headers=h).json()["items"]
        assert len(items) == 1 and items[0]["status"] == "queued"
        assert conn.execute("select count(*) c from jobs where kind='account_brief'").fetchone()["c"] == 1
    finally:
        db.close()


def test_the_brief_job_spends_budget_records_usage_and_falls_back_to_the_reading_model(conn, monkeypatch):
    import ri.sources.http as http
    from ri import budget, usage
    from ri.jobs.handlers import build_handlers
    from ri.jobs.queue import enqueue, run_one
    from ri.sources.registry import seed_registry
    seed_registry(conn)
    org = _org(conn)
    budget.set_cap(conn, "anthropic", "monthly", usage.period_now(), 1_000_000, "tokens")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("RI_RESEARCH_MODEL", "claude-sonnet-5-5")
    bid = str(conn.execute("insert into account_briefs(organization_id, requested_by) values (%s,'bdm@x') returning id", (org,)).fetchone()["id"])
    enqueue(conn, "account_brief", {"brief_id": bid}, idempotency_key=f"brief:{bid}", max_attempts=1)
    conn.commit()
    models = []

    async def fake_request(client, method, url, *, headers=None, json=None, **kw):
        models.append(json["model"])
        if json["model"] == "claude-sonnet-5-5":
            raise RuntimeError("synthetic 400 from the research model")
        page = [l for l in json["messages"][0]["content"].split("\n") if l.startswith("<source")][0].split('"')[1]
        return {"content": [{"type": "text", "text": '{"summary":[{"statement":"Core platform replacement.","source":"%s","quote":"replacing its core banking platform"}],"signals":[],"hiring":[],"open_questions":["Who owns delivery?"],"icp_fit":{"read":"Right size.","reasons":[]}}' % page}],
                "usage": {"input_tokens": 900, "output_tokens": 200}}
    monkeypatch.setattr(http, "request_json", fake_request)
    assert run_one(conn, "w", build_handlers(connector_factory=lambda sid: None)) == "completed"
    b = conn.execute("select status, model, dropped, content from account_briefs where id=%s", (bid,)).fetchone()
    assert b["status"] == "done" and b["model"] == "claude-haiku-5-5" and len(b["content"]["summary"]) == 1 and models == ["claude-sonnet-5-5", "claude-haiku-5-5"]
    u = conn.execute("select tokens_in, tokens_out, cost_usd, organization_id from provider_usage where operation='account_brief'").fetchone()
    assert (u["tokens_in"], u["tokens_out"]) == (900, 200) and float(u["cost_usd"]) == pytest.approx((900 * 0.10 + 200 * 0.50) / 1e6) and str(u["organization_id"]) == org
    bud = conn.execute("select spent, reserved from provider_budgets where provider='anthropic'").fetchone()
    assert float(bud["spent"]) == 1100 and float(bud["reserved"]) == 0
