"""Apollo discovery and approved reveals, the regulatory deadline calendar and the weekly precision review. Provider is a fake: no credit is ever spent for real."""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from ri import approvals as appr
from ri import budget, usage
from ri.config import Settings
from ri.enrichment import contacts
from ri.enrichment.providers import EnrichedPerson, PersonCandidate
from test_r4_crm_notify import mk_user


class FakeApollo:
    provider_id = "apollo"

    def __init__(self, email="cto@example.invalid", status="verified", fail=False):
        self.calls, self.email, self.status, self.fail = [], email, status, fail

    def estimate_cost(self, op):
        from ri.enrichment.providers import CostEstimate
        return CostEstimate(0.0 if op == "search_people" else 1.0)

    async def search_people(self, *, company_domain, titles, limit=5):
        self.calls.append(("search", company_domain))
        return [PersonCandidate("a1", "Ada Lovelace", "CTO", "X", "https://linkedin.example/ada"), PersonCandidate("a2", "Bo Peep", "Head of Payments", "X")]

    async def enrich_person(self, external_id):
        self.calls.append(("enrich", external_id))
        if self.fail:
            raise RuntimeError("down")
        return EnrichedPerson(external_id, "Ada Lovelace", "CTO", self.email, self.status, "apollo")


def S(**kw):
    return Settings(env="development", auth_mode="dev", dev_auth_secret="x" * 32, **kw)


def org(conn):
    o = str(conn.execute("insert into organizations(display_name, canonical_domain) values ('Synthetic Reveal Bank','reveal.example.invalid') returning id").fetchone()["id"])
    conn.commit()
    return o


def test_search_is_free_and_idempotent_and_never_stores_an_email(conn):
    o, fake = org(conn), FakeApollo()
    assert contacts.find_candidates(conn, fake, o, by="bdm@x") == {"found": 2, "added": 2}
    assert contacts.find_candidates(conn, fake, o, by="bdm@x")["added"] == 0
    assert all(c[0] == "search" for c in fake.calls)
    assert conn.execute("select count(*) n from people where work_email is not null").fetchone()["n"] == 0
    no_domain = str(conn.execute("insert into organizations(display_name) values ('No Site Ltd') returning id").fetchone()["id"])
    with pytest.raises(appr.ApprovalError, match="website"):
        contacts.find_candidates(conn, fake, no_domain, by="x")


def test_reveal_needs_price_switch_cap_and_approval_and_is_single_use(conn):
    o, fake = org(conn), FakeApollo()
    contacts.find_candidates(conn, fake, o, by="bdm@x")
    cid = str(conn.execute("select id from contact_candidates where external_id='a1'").fetchone()["id"])
    with pytest.raises(appr.ApprovalError, match="price"):
        contacts.request_reveal(conn, cid, by="bdm@x", settings=S())
    st = S(apollo_credits_per_reveal=1.0)
    r = contacts.request_reveal(conn, cid, by="bdm@x", settings=st)
    assert contacts.request_reveal(conn, cid, by="bdm@x", settings=st)["approval_id"] == r["approval_id"]      # same request, not a second one
    mk_user(conn, "boss@x", "admin")
    with pytest.raises(appr.ApprovalError, match="cannot approve their own request"):
        appr.decide(conn, r["approval_id"], approver="bdm@x", approver_role="admin", approve=True)
    appr.decide(conn, r["approval_id"], approver="boss@x", approver_role="admin", approve=True)
    with pytest.raises(appr.ApprovalError, match="switched off"):
        contacts.execute_reveal(conn, r["approval_id"], fake, st)
    on = S(apollo_credits_per_reveal=1.0, paid_enrichment_enabled=True)
    with pytest.raises(appr.ApprovalError, match="credit left"):
        contacts.execute_reveal(conn, r["approval_id"], fake, on)                      # no cap set: zero by default
    assert not [c for c in fake.calls if c[0] == "enrich"]
    budget.set_cap(conn, "apollo", "contact_enrichment", usage.period_now(), 5, "credits")
    out = contacts.execute_reveal(conn, r["approval_id"], fake, on)
    assert out["email_status"] == "Verified"
    p = conn.execute("select work_email, email_verification_status from people where full_name='Ada Lovelace'").fetchone()
    assert p["work_email"] == "cto@example.invalid" and p["email_verification_status"] == "Verified"
    assert float(conn.execute("select spent from provider_budgets where provider='apollo'").fetchone()["spent"]) == 1.0
    with pytest.raises(appr.ApprovalError):
        contacts.execute_reveal(conn, r["approval_id"], fake, on)                      # used once
    assert len([c for c in fake.calls if c[0] == "enrich"]) == 1


def test_a_changed_price_voids_the_approval_and_an_unverified_email_is_labelled_so(conn):
    o, fake = org(conn), FakeApollo(status="guessed")
    contacts.find_candidates(conn, fake, o, by="bdm@x")
    cid = str(conn.execute("select id from contact_candidates where external_id='a1'").fetchone()["id"])
    r = contacts.request_reveal(conn, cid, by="bdm@x", settings=S(apollo_credits_per_reveal=1.0))
    mk_user(conn, "boss@x", "admin")
    appr.decide(conn, r["approval_id"], approver="boss@x", approver_role="admin", approve=True)
    budget.set_cap(conn, "apollo", "contact_enrichment", usage.period_now(), 50, "credits")
    with pytest.raises(appr.ApprovalError, match="price changed"):
        contacts.execute_reveal(conn, r["approval_id"], fake, S(apollo_credits_per_reveal=3.0, paid_enrichment_enabled=True))
    out = contacts.execute_reveal(conn, r["approval_id"], fake, S(apollo_credits_per_reveal=1.0, paid_enrichment_enabled=True))
    assert out["email_status"] == "Found — Unverified"


def test_a_provider_failure_releases_the_credit_and_marks_the_approval_failed(conn):
    o, fake = org(conn), FakeApollo(fail=True)
    contacts.find_candidates(conn, fake, o, by="bdm@x")
    cid = str(conn.execute("select id from contact_candidates where external_id='a1'").fetchone()["id"])
    st = S(apollo_credits_per_reveal=1.0, paid_enrichment_enabled=True)
    r = contacts.request_reveal(conn, cid, by="bdm@x", settings=st)
    mk_user(conn, "boss@x", "admin")
    appr.decide(conn, r["approval_id"], approver="boss@x", approver_role="admin", approve=True)
    budget.set_cap(conn, "apollo", "contact_enrichment", usage.period_now(), 5, "credits")
    with pytest.raises(RuntimeError):
        contacts.execute_reveal(conn, r["approval_id"], fake, st)
    assert float(conn.execute("select spent+reserved as t from provider_budgets where provider='apollo'").fetchone()["t"]) == 0
    assert conn.execute("select status from approvals where id=%s", (r["approval_id"],)).fetchone()["status"] == "failed"


def test_deadlines_need_a_source_quote_and_a_second_person_before_sellers_see_them(conn, db_url):
    from fastapi.testclient import TestClient
    from ri.api.app import create_app
    from ri.api.auth import make_dev_token
    from ri.db import Database
    from test_r7_verify_discovery_usage import SECRET
    mk_user(conn, "analyst@x", "research_analyst")
    mk_user(conn, "boss@x", "admin")
    mk_user(conn, "bdm@x", "bdm", 5)
    conn.commit()
    db = Database(db_url, max_size=2)
    try:
        c = TestClient(create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET), db=db))
        h = lambda e: {"Authorization": f"Bearer {make_dev_token(SECRET, e)}"}      # noqa: E731
        body = {"title": "Synthetic regime applies to banks", "regime": "TESTREG", "deadline_date": "2027-01-17", "applies_to": "banks", "region": "EU",
                "source_url": "https://regulator.example.invalid/page", "source_quote": "The regime applies from 17 January 2027 to all banks."}
        assert c.post("/api/deadlines", headers=h("analyst@x"), json={**body, "source_url": "http://insecure.example"}).status_code == 422
        assert c.post("/api/deadlines", headers=h("analyst@x"), json={**body, "source_quote": "It applies from 17 January soon, says the page."}).status_code == 422      # no year
        did = c.post("/api/deadlines", headers=h("analyst@x"), json=body).json()["id"]
        assert c.get("/api/deadlines", headers=h("bdm@x")).json()["items"] == []                       # proposed dates are hidden from sellers
        assert c.post(f"/api/deadlines/{did}/decision", headers=h("bdm@x"), json={"decision": "approve"}).status_code == 403
        assert c.post(f"/api/deadlines/{did}/decision", headers=h("boss@x"), json={"decision": "approve"}).json()["status"] == "approved"
        seen = c.get("/api/deadlines", headers=h("bdm@x")).json()["items"]
        assert len(seen) == 1 and seen[0]["source_quote"].startswith("The regime")
        assert c.post(f"/api/deadlines/{did}/decision", headers=h("boss@x"), json={"decision": "retire"}).json()["status"] == "retired"
        assert c.get("/api/deadlines", headers=h("bdm@x")).json()["items"] == []
        own = c.post("/api/deadlines", headers=h("boss@x"), json=body).json()["id"]
        assert c.post(f"/api/deadlines/{own}/decision", headers=h("boss@x"), json={"decision": "approve"}).status_code == 409       # not your own
        assert c.get("/api/admin/precision", headers=h("boss@x")).json()["window_days"] == 90
        assert c.get("/api/admin/precision", headers=h("bdm@x")).status_code == 403
    finally:
        db.close()


def test_precision_review_flags_only_rules_with_enough_dismissals_and_alerts_once_a_week(conn):
    from ri import precision
    from ri.signals.baseline import import_baseline
    from ri.signals.persist import sync_rule_versions
    import_baseline(conn)
    sync_rule_versions(conn)
    uid = mk_user(conn, "bdm@x", "bdm", 5)
    o = str(conn.execute("insert into organizations(display_name) values ('Synthetic Precision AG') returning id").fetchone()["id"])
    # twelve initiatives each with one COM-01 signal and a dismissed hypothesis; two with COM-02 and an accepted one
    def make(rule, decision, i):
        ev = str(conn.execute("insert into events(event_key, event_type, summary, extraction_model_version, verification_status) values (%s,'funding_announced','s','t','verified') returning id", (f"k{rule}{i}",)).fetchone()["id"])
        ini = str(conn.execute("insert into initiatives(owner_org_id, title, stage) values (%s,%s,'announced') returning id", (o, f"I{rule}{i}")).fetchone()["id"])
        conn.execute("insert into initiative_events(initiative_id, event_id, link_strength) values (%s,%s,'confirmed_link')", (ini, ev))
        conn.execute("insert into signals(event_id, rule_id, rule_version, organization_id, initiative_id, strength, demand_tier, verification_status, effective_from, expires_at, reason_code) values (%s,%s,'1.0.0',%s,%s,2,'D4','verified', now(), now() + interval '60 days', 'r')", (ev, rule, o, ini))
        hy = str(conn.execute("insert into opportunity_hypotheses(initiative_id, route_type, counterparty_name, organization_id) values (%s,'direct','x',%s) returning id", (ini, o)).fetchone()["id"])
        conn.execute("insert into hypothesis_user_state(hypothesis_id, user_id, decision, reason, fingerprint) values (%s,%s,%s,%s,'f')", (hy, uid, decision, "not_relevant" if decision == "dismissed" else None))
    try:
        for i in range(12):
            make("COM-01", "dismissed", i)
        for i in range(2):
            make("COM-02", "accepted", i)
    except Exception as exc:       # schema detail drift must be visible, not hidden
        raise
    rv = precision.review(conn)
    r = {x["rule_id"]: x for x in rv["rules"]}
    assert r["COM-01"]["flag"] is True and r["COM-01"]["top_dismiss_reason"] == "not_relevant"
    assert r["COM-02"]["flag"] is False and r["COM-02"]["enough_data"] is False

    class Sink:
        sent = []

        def send(self, rcp, text):
            self.sent.append(text)
    monday = datetime(2026, 11, 23, 8, 2, tzinfo=timezone.utc)
    assert precision.weekly_review_step(conn, S(), datetime(2026, 11, 24, 8, 2, tzinfo=timezone.utc), Sink()) == {}          # not Monday
    out = precision.weekly_review_step(conn, S(), monday, Sink())
    assert out["precision_alerted"] and "COM-01" in Sink.sent[0]
    assert precision.weekly_review_step(conn, S(), monday, Sink()) == {} and len(Sink.sent) == 1
