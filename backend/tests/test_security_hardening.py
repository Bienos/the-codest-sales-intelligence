"""Regression tests for the 25 Nov 2026 security review: SSRF pinning, scope leaks, session lifetime, fail-closed dev auth, webhook robustness,
rate limiting and forged CRM evidence."""
import asyncio
import base64
import dataclasses
import socket
import time

import jwt
import pytest

from ri.api.app import RateLimiter
from ri.api.auth import MAX_SESSION_DAYS, TokenVerifier, issue_session, session_key
from ri.config import Settings
from ri.sources import safe
from ri.sources.base import PolicyBlocked
from ri.verify.fetch import assert_public
from test_r3_opportunity import env  # noqa: F401  (fixture)
from test_r4_api import SECRET, world  # noqa: F401  (fixture)


# ------------------------------------------------------------------ SSRF
@pytest.mark.parametrize("ip,ok", [("93.184.216.34", True), ("10.0.0.5", False), ("127.0.0.1", False), ("169.254.169.254", False),
                                   ("::ffff:10.0.0.1", False), ("::1", False), ("100.64.0.1", False), ("2606:4700::6810:84e5", True)])
def test_public_ip_classification(ip, ok):
    assert safe.is_public_ip(ip) is ok


@pytest.mark.parametrize("url", ["http://example.com:8080/x", "https://example.com:6379/", "ftp://example.com/", "https://user:pw@example.com/",
                                 "https://www.linkedin.com/in/someone"])
def test_assert_public_refuses_ports_schemes_credentials_and_social(url):
    with pytest.raises(PolicyBlocked):
        asyncio.run(assert_public(url))


def test_resolve_refuses_a_name_with_any_private_address(monkeypatch):
    async def fake(host, port, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)), (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", port))]

    async def run():
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake)
        await safe.resolve_public("rebind.example", 443)
    with pytest.raises(PolicyBlocked):
        asyncio.run(run())


def test_pinned_backend_connects_to_the_checked_address_not_the_name(monkeypatch):
    """DNS rebinding: the connection must go to the address that passed the check, never a second lookup."""
    seen = {}

    class Inner:
        async def connect_tcp(self, host, port, **kw):
            seen["host"], seen["port"] = host, port
            return "stream"

    async def fake(host, port, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    async def run():
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake)
        return await safe._PinnedBackend(Inner()).connect_tcp("news.example", 443)
    assert asyncio.run(run()) == "stream" and seen == {"host": "93.184.216.34", "port": 443}

    async def private(host, port, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]

    async def run_private():
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", private)
        return await safe._PinnedBackend(Inner()).connect_tcp("news.example", 443)
    with pytest.raises(PolicyBlocked):
        asyncio.run(run_private())
    with pytest.raises(PolicyBlocked):
        asyncio.run(safe._PinnedBackend(Inner()).connect_tcp("news.example", 5432))


def test_pinning_is_used_unless_an_egress_proxy_is_configured(monkeypatch):
    for v in safe._PROXY_VARS:
        monkeypatch.delenv(v, raising=False)
    t = safe.public_transport()
    assert t is not None and isinstance(t._pool._network_backend, safe._PinnedBackend)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
    assert safe.public_transport() is None


# ------------------------------------------------------------------ sessions and sign-in
def _settings(**kw):
    return Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET, session_secret="s" * 40, **kw)


def test_session_renewal_never_extends_past_the_original_sign_in():
    s = _settings()
    now = int(time.time())
    tok, exp = issue_session(s, "bdm@x", auth_time=now - (MAX_SESSION_DAYS * 86400 - 3600))
    assert exp <= now + 3600 + 5                                     # capped at sign-in + 7 days, not now + 12 hours
    assert TokenVerifier(s).session_claims(tok)["auth_time"] == now - (MAX_SESSION_DAYS * 86400 - 3600)
    with pytest.raises(Exception):
        issue_session(s, "bdm@x", auth_time=now - MAX_SESSION_DAYS * 86400 - 10)
    legacy = jwt.encode({"email": "bdm@x", "typ": "session", "exp": now + 3600}, session_key(s), algorithm="HS256")
    assert TokenVerifier(s).session_claims(legacy) is None           # sessions minted before the cap carry no sign-in time: refused


@pytest.mark.parametrize("env_name", ["prod", "Production", "staging", ""])
def test_dev_auth_fails_closed_outside_development_and_test(env_name):
    with pytest.raises(RuntimeError):
        Settings(env=env_name, auth_mode="dev", dev_auth_secret=SECRET).validate()
    Settings(env="test", auth_mode="dev", dev_auth_secret=SECRET).validate()


def test_kb_links_are_signed_with_a_secret_not_the_database_address():
    from ri import kb
    s = dataclasses.replace(_settings(), database_url="postgresql://u:p@db/ri")
    assert kb.link_secret(s) == "s" * 40
    tok = kb.sign_link(kb.link_secret(s), "icp", "u1")
    assert kb.verify_link(kb.link_secret(s), tok, "icp") and not kb.verify_link(s.database_url, tok, "icp")


# ------------------------------------------------------------------ rate limiting and webhooks
def test_rate_limiter_caps_each_caller_per_minute():
    r = RateLimiter(3)
    assert [r.allow("a", now=60.0) for _ in range(4)] == [True, True, True, False]
    assert r.allow("b", now=60.0) and r.allow("a", now=121.0)


def test_webhooks_reject_bad_shapes_and_non_ascii_credentials_without_a_500(world):
    c = world["c"]
    good = {"Authorization": "Basic " + base64.b64encode(b"pipedrive:whsec").decode()}
    weird = {"Authorization": "Basic " + base64.b64encode("pipedrive:żółw".encode()).decode()}
    assert c.post("/webhooks/pipedrive", json={"meta": {}}, headers=weird).status_code == 401
    assert c.post("/webhooks/pipedrive", json=[1, 2], headers=good).status_code == 400
    assert c.post("/webhooks/pipedrive", content=b"not json", headers={**good, "content-type": "application/json"}).status_code == 400
    big = b"{" + b" " * 1_100_000 + b"}"
    assert c.post("/webhooks/pipedrive", content=big, headers={**good, "content-type": "application/json"}).status_code == 413


# ------------------------------------------------------------------ scope
def test_adding_a_company_never_touches_an_account_outside_scope(world):
    c, h = world["c"], world["h"]
    with world["app"].state.db.tx() as conn:
        conn.execute("update organizations set canonical_domain='examplebank.example' where id=%s", (world["env"]["bank"],))
    r = c.post("/api/accounts/add", json={"website": "examplebank.example", "notes": "planted note"}, headers=h("bdm2"))
    assert r.status_code == 409 and "examplebank" not in r.text.lower()
    with world["app"].state.db.tx() as conn:
        assert conn.execute("select count(*) n from account_context where organization_id=%s", (world["env"]["bank"],)).fetchone()["n"] == 0
    assert c.post("/api/accounts/add", json={"website": "examplebank.example"}, headers=h("bdm")).status_code == 200      # the owner can


def test_search_does_not_show_claims_on_other_sellers_accounts(world):
    c, h = world["c"], world["h"]
    with world["app"].state.db.tx() as conn:
        conn.execute("insert into verification_requests(organization_id, url, origin, claimed_text, requested_by) values (%s,'https://examplebank.example/news','sheet_signal','Zebracore migration','bdm@x')",
                     (world["env"]["bank"],))
    seen = lambda who: [i["title"] for g in c.get("/api/search?q=zebracore", headers=h(who)).json()["groups"] if g["key"] == "verifications" for i in g["items"]]  # noqa: E731
    assert seen("bdm") == ["Zebracore migration"]
    assert seen("bdm2") == []


def test_late_stages_need_a_manager_and_a_real_crm_record(world):
    c, h = world["c"], world["h"]
    hid = world["hyps"]["prime_subcontract"]
    with world["app"].state.db.tx() as conn:
        conn.execute("update opportunity_hypotheses set state='actionable_hypothesis' where id=%s", (hid,))
    forged = {"new_state": "engaged", "evidence": {"kind": "crm_buyer_response", "crm_ref": "activity:999"}}
    assert c.post(f"/api/hypotheses/{hid}/transition", json=forged, headers=h("bdm")).status_code == 409
    with world["app"].state.db.tx() as conn:
        conn.execute("update opportunity_hypotheses set state='commercially_qualified' where id=%s", (hid,))
    late = {"new_state": "proposal_procurement", "evidence": {"kind": "crm_deal_stage", "stage": "Proposal/Shortlist"}}
    assert c.post(f"/api/hypotheses/{hid}/transition", json=late, headers=h("bdm")).status_code == 403


def test_people_from_other_companies_cannot_be_put_on_a_card(world):
    c, h = world["c"], world["h"]
    with world["app"].state.db.tx() as conn:
        stranger = str(conn.execute("insert into people(full_name) values ('Someone Elsewhere') returning id").fetchone()["id"])
    r = c.post(f"/api/hypotheses/{world['hyps']['prime_subcontract']}/stakeholders", json={"role": "economic_buyer", "person_id": stranger}, headers=h("bdm"))
    assert r.status_code == 422
