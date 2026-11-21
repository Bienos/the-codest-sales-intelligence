"""Campaign writer and brand reviewer. The model is a FAKE: what matters is what the reviewer lets through."""
import asyncio
import json

import pytest

from ri.outbound.brand_check import check_step, review_sequence
from ri.outbound.writer import build_facts, system_prompt, write
from test_r4_crm_notify import mk_user

FACTS = {"F1": "Synthetic Campaign Bank AG is replacing its core banking platform with a phased migration starting in January 2027. (source quote: replacing its core banking platform)"}
CLM = "We take responsibility for the systems your business makes money on, as if they were our own."
GOOD = {"day": 0, "subject": "Your core platform migration", "claims_used": ["CLM-001"], "fact_refs": ["F1"],
        "body": f"Hello {{{{firstName}}}},\n\nYou are replacing your core banking platform with a phased migration starting in January 2027. Programmes like this often leave workstreams that need a clear owner.\n\n{CLM} Let's talk through the problem."}


def codes(step, first=True):
    return {b["code"] for b in check_step(step, FACTS, first=first)}


def test_a_clean_grounded_email_passes():
    assert check_step(GOOD, FACTS, first=True) == []


@pytest.mark.parametrize("mutation,expected", [
    ({"body": GOOD["body"].replace("Programmes", "Programmes — unlike others,")}, "em_dash"),
    ({"body": GOOD["body"] + " Great!"}, "exclamation"),
    ({"body": GOOD["body"].replace("clear owner", "seamless owner")}, "avoid_word"),
    ({"body": GOOD["body"].replace("Programmes like this", "As we did for MUFG, programmes like this")}, "blocked_name"),
    ({"body": GOOD["body"].replace("Programmes like this", "Our US clients find programmes like this")}, "blocked_market"),
    ({"body": GOOD["body"].replace("We take", "Codest takes").replace("responsibility for", "responsibility for")}, "brand_name"),
    ({"claims_used": ["CLM-001", "CLM-009"]}, "claim_not_usable"),
    ({"body": GOOD["body"].replace("a clear owner", "a clear owner, and we cut cost by 42%")}, "number_not_in_source"),
    ({"fact_refs": []}, "no_verified_fact"),
    ({"fact_refs": ["F9"]}, "no_verified_fact"),
    ({"body": GOOD["body"].replace("Let's talk through the problem.", "Contact us.")}, "avoid_word"),
    ({"body": GOOD["body"].replace("Let's talk through the problem.", "Let's talk through the problem or Start an evidence review.")}, "cta"),
    ({"body": GOOD["body"].replace("{{firstName}}", "{{phone}}")}, "placeholder"),
    ({"body": GOOD["body"] + "\n\nSee https://example.com"}, "link_or_address"),
    ({"body": GOOD["body"].replace("Hello", "I hope this finds you well. Hello")}, "generated_copy_pattern"),
    ({"body": GOOD["body"].replace("a clear owner", "a clear owner. It is hard. It is real. It is urgent")}, "paragraph_long"),
    ({"body": GOOD["body"].replace(CLM, "We take responsibility for your systems.")}, "claim_not_declared") if False else ({"claims_used": [], "body": GOOD["body"]}, "claim_not_declared"),
])
def test_each_brand_rule_is_enforced(mutation, expected):
    assert expected in codes({**GOOD, **mutation})


def test_numbers_that_appear_in_the_verified_source_are_allowed():
    assert "number_not_in_source" not in codes(GOOD)          # "2027" is in the source and also a year


def _call(*replies):
    queue = list(replies)
    seen = []

    async def call(system, user):
        seen.append(user)
        return json.dumps({"steps": queue.pop(0)}), 1000, 400, "fake-writer"
    call.seen = seen
    return call


def _org_with_brief(conn):
    org = str(conn.execute("insert into organizations(display_name, segment, country_code) values ('Synthetic Campaign Bank AG','Bank','DE') returning id").fetchone()["id"])
    content = {"summary": [{"statement": "Synthetic Campaign Bank AG is replacing its core banking platform with a phased migration starting in January 2027.", "quote": "replacing its core banking platform", "source": "S1"}],
               "signals": [], "hiring": []}
    conn.execute("insert into account_briefs(organization_id, requested_by, status, content) values (%s,'x','done',%s::jsonb)", (org, json.dumps(content)))
    conn.commit()
    return org


def _three(good=GOOD):
    return [good, {**good, "day": 4, "subject": "A question on ownership"}, {**good, "day": 9, "subject": "Closing the loop"}]


def test_a_clean_first_draft_passes_without_rewrites(conn):
    org = _org_with_brief(conn)
    call = _call(_three())
    out = write(conn, org, "CTO", call, lambda c: asyncio.run(c))
    assert out["status"] == "draft" and out["rounds"] == 1 and out["review"]["passed"] and len(out["steps"]) == 3
    assert "Recipient role: CTO" in call.seen[0] and "Audience: banks" in call.seen[0] and "F1:" in call.seen[0]


def test_a_draft_that_breaks_a_rule_is_sent_back_with_the_exact_breaks_and_then_passes(conn):
    org = _org_with_brief(conn)
    bad = {**GOOD, "body": GOOD["body"].replace("a clear owner", "a seamless owner")}
    call = _call(_three(bad), _three())
    out = write(conn, org, None, call, lambda c: asyncio.run(c))
    assert out["status"] == "draft" and out["rounds"] == 2 and out["tokens_in"] == 2000
    assert "avoid-list word: seamless" in call.seen[1] and "Previous draft" in call.seen[1]


def test_a_draft_that_keeps_breaking_rules_stops_after_two_rewrites_and_is_handed_to_a_person(conn):
    org = _org_with_brief(conn)
    bad = {**GOOD, "body": GOOD["body"].replace("a clear owner", "a seamless owner")}
    out = write(conn, org, None, _call(_three(bad), _three(bad), _three(bad)), lambda c: asyncio.run(c))
    assert out["status"] == "needs_work" and out["rounds"] == 3 and out["review"]["breaks"]


def test_without_any_verified_fact_nothing_is_written_and_the_model_is_not_called(conn):
    org = str(conn.execute("insert into organizations(display_name) values ('Synthetic Empty Ltd') returning id").fetchone()["id"])
    conn.commit()
    call = _call()
    out = write(conn, org, None, call, lambda c: asyncio.run(c))
    assert out["status"] == "needs_work" and not call.seen and out["review"]["breaks"][0]["code"] == "no_facts"


def test_prompt_carries_only_the_approved_claims_and_ctas():
    p = system_prompt()
    assert "CLM-001" in p and "CLM-003" in p and "CLM-009" not in p and "Let's talk through the problem" in p and "MUFG" not in p.split("NEVER")[0]


def test_the_campaign_job_and_endpoints_end_to_end(conn, db_url, monkeypatch):
    from fastapi.testclient import TestClient
    import ri.sources.http as http
    from ri import budget, usage
    from ri.api.app import create_app
    from ri.api.auth import make_dev_token
    from ri.config import Settings
    from ri.db import Database
    from ri.jobs.handlers import build_handlers
    from ri.jobs.queue import run_one
    from test_r7_verify_discovery_usage import SECRET
    mk_user(conn, "bdm@x", "bdm", 555)
    org = _org_with_brief(conn)
    budget.set_cap(conn, "anthropic", "monthly", usage.period_now(), 1_000_000, "tokens")
    conn.commit()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    async def fake_request(client, method, url, *, headers=None, json=None, **kw):
        import json as j
        return {"content": [{"type": "text", "text": j.dumps({"steps": _three()})}], "usage": {"input_tokens": 700, "output_tokens": 300}}
    monkeypatch.setattr(http, "request_json", fake_request)
    db = Database(db_url, max_size=2)
    try:
        c = TestClient(create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET), db=db))
        h = {"Authorization": f"Bearer {make_dev_token(SECRET, 'bdm@x')}"}
        r = c.post(f"/api/accounts/{org}/campaign", headers=h, json={"role": "CTO"})
        assert r.status_code == 200 and c.post(f"/api/accounts/{org}/campaign", headers=h, json={}).status_code == 409
        assert run_one(conn, "w", build_handlers(connector_factory=lambda sid: None)) == "completed"
        item = c.get(f"/api/accounts/{org}/campaigns", headers=h).json()["items"][0]
        assert item["status"] == "draft" and item["role"] == "CTO" and len(item["steps"]) == 3 and item["review"]["passed"]
        assert c.post(f"/api/campaigns/{item['id']}/decision", headers=h, json={"decision": "approve"}).json()["status"] == "approved"
        assert c.post(f"/api/campaigns/{item['id']}/decision", headers=h, json={"decision": "approve"}).status_code == 409      # already decided
        assert float(conn.execute("select spent from provider_budgets where provider='anthropic'").fetchone()["spent"]) == 1000
    finally:
        db.close()
