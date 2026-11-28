"""People's own Claude or ChatGPT (MCP server + OAuth 2.1) and the in-app assistant. The model is a FAKE (no network); names are invented.

What must hold: discovery documents say what the MCP spec and the Claude/ChatGPT connector docs require; only known assistant hosts or the person's
own computer can receive a sign-in; PKCE S256, single-use codes and rotated refresh tokens; every tool reads AS the person (role and row scope)
and only reads; disconnecting ends access at once; the in-app assistant runs tools and stays inside its own token allowance."""
import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from test_r4_crm_notify import mk_user

from ri import vault
from ri.api.app import create_app
from ri.api.auth import issue_internal, make_dev_token
from ri.config import Settings
from ri.db import Database, q1

SECRET = "s" * 40
BASE = "https://ri.example.test"
CLAUDE_CB = "https://claude.ai/api/mcp/auth_callback"


def settings(**kw):
    return Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET, public_url=BASE, **kw)


@pytest.fixture()
def env(conn, db_url, monkeypatch):
    monkeypatch.delenv("RI_MCP_REDIRECT_HOSTS", raising=False)
    monkeypatch.setenv("RI_ASSISTANT_MONTHLY_TOKEN_CAP", "100000")
    users = {"ann": mk_user(conn, "ann@x", "bdm", 555), "bob": mk_user(conn, "bob@x", "bdm", 666), "mia": mk_user(conn, "mia@x", "marketing")}
    mine = conn.execute("INSERT INTO organizations(display_name, country_code, segment) VALUES ('Orchard Bank','GB','bank') RETURNING id").fetchone()["id"]
    bobs = conn.execute("INSERT INTO organizations(display_name, country_code, segment) VALUES ('Orchard Lending','GB','bank') RETURNING id").fetchone()["id"]
    conn.execute("INSERT INTO crm_organizations(crm_id, organization_id, owner_id, name, link_status) VALUES (1, %s, 555, 'Orchard Bank', 'linked'), "
                 "(2, %s, 666, 'Orchard Lending', 'linked')", (mine, bobs))
    conn.commit()
    db = Database(db_url, max_size=4)
    app = create_app(settings(vault_key=vault.new_key(), anthropic_api_key="sk-ant-company-000000000000"), db=db)
    hdr = lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"}  # noqa: E731
    yield {"c": TestClient(app), "app": app, "h": hdr, "u": users, "mine": str(mine), "bobs": str(bobs), "conn": conn}
    db.close()


def pkce():
    v = secrets.token_urlsafe(48)
    return v, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


def register(c, uris=(CLAUDE_CB,), **kw):
    return c.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": list(uris), **kw})


def connect(env, who="ann", redirect=CLAUDE_CB):
    """The whole sign-in, as Claude does it: register, authorize, consent (as `who`), exchange the code."""
    c = env["c"]
    cid = register(c, [redirect]).json()["client_id"]
    verifier, challenge = pkce()
    q = {"response_type": "code", "client_id": cid, "redirect_uri": redirect, "code_challenge": challenge, "code_challenge_method": "S256",
         "state": "st-1", "scope": "ri.read", "resource": BASE + "/mcp"}
    r = c.get("/oauth/authorize", params=q, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/connect/authorize?")
    passed = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    assert passed["redirect_uri"] == redirect and passed["code_challenge"] == challenge
    a = c.post("/api/oauth/approve", headers=env["h"](who), json={**{k: passed[k] for k in ("client_id", "redirect_uri", "code_challenge", "state", "resource")}, "approve": True})
    assert a.status_code == 200, a.text
    back = urlsplit(a.json()["redirect"])
    got = parse_qs(back.query)
    assert f"{back.scheme}://{back.netloc}{back.path}" == redirect and got["state"] == ["st-1"] and got["iss"] == [BASE]
    t = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": got["code"][0], "redirect_uri": redirect, "client_id": cid,
                                     "code_verifier": verifier, "resource": BASE + "/mcp"})
    assert t.status_code == 200, t.text
    return cid, t.json()


def rpc(env, token, method, params=None, mid=1):
    r = env["c"].post("/mcp", headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
                      json={"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})
    return r


def call(env, token, name, args=None):
    r = rpc(env, token, "tools/call", {"name": name, "arguments": args or {}})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    return res["content"][0]["text"], res["isError"]


# ------------------------------------------------------------------ discovery
def test_discovery_documents_say_what_claude_and_chatgpt_need(env):
    c = env["c"]
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        prm = c.get(path).json()
        assert prm["resource"] == BASE + "/mcp" and prm["authorization_servers"] == [BASE] and prm["scopes_supported"] == ["ri.read"]
    asm = c.get("/.well-known/oauth-authorization-server").json()
    assert asm["issuer"] == BASE and asm["code_challenge_methods_supported"] == ["S256"]
    assert asm["registration_endpoint"] == BASE + "/oauth/register" and asm["token_endpoint"] == BASE + "/oauth/token"
    assert "none" in asm["token_endpoint_auth_methods_supported"] and set(asm["grant_types_supported"]) == {"authorization_code", "refresh_token"}


def test_mcp_without_a_token_answers_401_pointing_at_the_metadata(env):
    r = env["c"].post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r.status_code == 401
    w = r.headers["www-authenticate"]
    assert w.startswith("Bearer ") and f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"' in w and 'scope="ri.read"' in w
    bad = env["c"].post("/mcp", headers={"Authorization": "Bearer rimcp_at_nope"}, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert bad.status_code == 401 and 'error="invalid_token"' in bad.headers["www-authenticate"]
    # an app session token is not an MCP token (no token passthrough, no audience confusion)
    assert env["c"].post("/mcp", headers=env["h"]("ann"), json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    assert env["c"].get("/mcp").status_code == 405


# ------------------------------------------------------------------ registration and authorization
def test_registration_only_accepts_assistant_hosts_and_the_persons_own_computer(env):
    c = env["c"]
    assert register(c, ["https://evil.example/cb"]).status_code == 400
    assert register(c, ["http://claude.ai/api/mcp/auth_callback"]).status_code == 400            # plain http only for loopback
    ok = register(c, ["https://chatgpt.com/connector_platform_oauth_redirect"])
    assert ok.status_code == 201 and ok.json()["client_id"].startswith("ri_") and "client_secret" not in ok.json()
    assert register(c, ["http://localhost/callback", "http://127.0.0.1/callback"]).status_code == 201
    confidential = register(c, [CLAUDE_CB], token_endpoint_auth_method="client_secret_post").json()
    assert confidential["client_secret"].startswith("ris_")


def test_authorize_refuses_unknown_clients_and_unregistered_return_addresses(env):
    c = env["c"]
    _, ch = pkce()
    assert c.get("/oauth/authorize", params={"client_id": "ri_nope", "redirect_uri": CLAUDE_CB}, follow_redirects=False).status_code == 400
    cid = register(c).json()["client_id"]
    r = c.get("/oauth/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": "https://claude.ai/other", "code_challenge": ch,
                                          "code_challenge_method": "S256"}, follow_redirects=False)
    assert r.status_code == 400                                      # never redirect to an address the client did not register
    r = c.get("/oauth/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": CLAUDE_CB, "state": "x"}, follow_redirects=False)
    assert r.status_code == 302 and "error=invalid_request" in r.headers["location"] and "state=x" in r.headers["location"]    # PKCE is required
    r = c.get("/oauth/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": CLAUDE_CB, "code_challenge": ch, "code_challenge_method": "S256",
                                          "resource": "https://other.example/mcp"}, follow_redirects=False)
    assert "error=invalid_target" in r.headers["location"]


def test_loopback_redirects_match_on_any_port_like_claude_code_needs(env):
    cid, tok = connect(env, redirect="http://localhost:53682/callback")
    assert tok["access_token"].startswith("rimcp_at_")
    c = env["c"]
    cid2 = register(c, ["http://127.0.0.1/callback"]).json()["client_id"]
    info = c.get("/api/oauth/request", params={"client_id": cid2, "redirect_uri": "http://127.0.0.1:61000/callback"}, headers=env["h"]("ann"))
    assert info.status_code == 200 and info.json()["redirect"]["loopback"] is True
    assert c.get("/api/oauth/request", params={"client_id": cid2, "redirect_uri": "http://127.0.0.1:61000/elsewhere"}, headers=env["h"]("ann")).status_code == 400


def test_the_consent_page_names_the_app_the_destination_and_the_person(env):
    c = env["c"]
    cid = register(c).json()["client_id"]
    assert c.get("/api/oauth/request", params={"client_id": cid, "redirect_uri": CLAUDE_CB}).status_code == 401        # signed in first
    info = c.get("/api/oauth/request", params={"client_id": cid, "redirect_uri": CLAUDE_CB}, headers=env["h"]("ann")).json()
    assert info["client_name"] == "Claude" and info["redirect"] == {"host": "claude.ai", "known_as": "Claude", "loopback": False}
    assert info["email"] == "ann@x" and info["server"] == BASE + "/mcp" and info["already_connected"] is False


def test_declining_sends_access_denied_back_and_issues_nothing(env):
    c = env["c"]
    cid = register(c).json()["client_id"]
    _, ch = pkce()
    r = c.post("/api/oauth/approve", headers=env["h"]("ann"), json={"client_id": cid, "redirect_uri": CLAUDE_CB, "code_challenge": ch, "state": "s", "approve": False})
    assert "error=access_denied" in r.json()["redirect"] and "state=s" in r.json()["redirect"]
    assert q1(env["conn"], "SELECT count(*) AS n FROM oauth_grants")["n"] == 0


# ------------------------------------------------------------------ token endpoint
def test_pkce_is_checked_and_a_code_works_once(env):
    c = env["c"]
    cid = register(c).json()["client_id"]
    verifier, ch = pkce()
    a = c.post("/api/oauth/approve", headers=env["h"]("ann"), json={"client_id": cid, "redirect_uri": CLAUDE_CB, "code_challenge": ch, "approve": True})
    code = parse_qs(urlsplit(a.json()["redirect"]).query)["code"][0]
    wrong = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid, "code_verifier": "x" * 50, "redirect_uri": CLAUDE_CB})
    assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_grant"
    again = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid, "code_verifier": verifier, "redirect_uri": CLAUDE_CB})
    assert again.status_code == 400                                   # spent by the failed attempt: codes are single use
    assert q1(env["conn"], "SELECT revoked_reason FROM oauth_grants")["revoked_reason"] == "code reused"
    assert c.post("/oauth/token", data={"grant_type": "client_credentials", "client_id": cid}).json()["error"] == "unsupported_grant_type"
    assert c.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": "ri_nope"}).status_code == 401


def test_refresh_tokens_rotate_and_a_stolen_old_one_ends_the_connection(env, monkeypatch):
    from ri.api import oauth
    cid, tok = connect(env)
    c = env["c"]
    r1 = c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid})
    assert r1.status_code == 200 and r1.json()["refresh_token"] != tok["refresh_token"]
    assert rpc(env, r1.json()["access_token"], "ping").status_code == 200
    # the old refresh token again, after the grace window: someone else has it, so everything from this approval stops working
    monkeypatch.setattr(oauth, "REFRESH_REUSE_GRACE", oauth.timedelta(seconds=-1))
    reuse = c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid})
    assert reuse.status_code == 400 and reuse.json()["error"] == "invalid_grant"
    assert rpc(env, r1.json()["access_token"], "ping").status_code == 401
    assert c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": r1.json()["refresh_token"], "client_id": cid}).status_code == 400


def test_confidential_clients_must_present_their_secret(env):
    c = env["c"]
    reg = register(c, token_endpoint_auth_method="client_secret_basic").json()
    verifier, ch = pkce()
    a = c.post("/api/oauth/approve", headers=env["h"]("ann"), json={"client_id": reg["client_id"], "redirect_uri": CLAUDE_CB, "code_challenge": ch, "approve": True})
    code = parse_qs(urlsplit(a.json()["redirect"]).query)["code"][0]
    body = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier, "redirect_uri": CLAUDE_CB}
    assert c.post("/oauth/token", data={**body, "client_id": reg["client_id"]}).status_code == 401
    basic = base64.b64encode(f"{reg['client_id']}:{reg['client_secret']}".encode()).decode()
    assert c.post("/oauth/token", data=body, headers={"Authorization": "Basic " + basic}).status_code == 200


# ------------------------------------------------------------------ the MCP server
def test_initialize_and_list_tools_follow_the_protocol(env):
    _, tok = connect(env)
    r = rpc(env, tok["access_token"], "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude-ai", "version": "1"}})
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-06-18" and res["capabilities"]["tools"] == {"listChanged": False} and "ann@x" in res["instructions"]
    assert rpc(env, tok["access_token"], "initialize", {"protocolVersion": "1999-01-01"}).json()["result"]["protocolVersion"] == "2025-11-25"
    note = env["c"].post("/mcp", headers={"Authorization": f"Bearer {tok['access_token']}"}, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert note.status_code == 202
    tools = rpc(env, tok["access_token"], "tools/list").json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"search", "fetch", "today", "list_accounts", "get_account", "buying_signals", "list_endpoints", "api_get"} <= names
    assert all(t["annotations"]["readOnlyHint"] is True and t["inputSchema"]["type"] == "object" for t in tools)
    acc = next(t for t in tools if t["name"] == "list_accounts")
    assert {"q", "limit"} <= set(acc["inputSchema"]["properties"])          # parameters come from the route itself
    assert rpc(env, tok["access_token"], "nope/nope").json()["error"]["code"] == -32601


def test_tools_read_as_the_person_with_their_row_scope(env):
    _, ann = connect(env, "ann")
    text, err = call(env, ann["access_token"], "whoami")
    assert not err and json.loads(text)["email"] == "ann@x"
    text, err = call(env, ann["access_token"], "list_accounts", {"q": "orchard"})
    names = [a["display_name"] for a in json.loads(text)["items"]] if "items" in json.loads(text) else text
    assert "Orchard Bank" in str(names) and "Orchard Lending" not in str(names)          # Bob's account stays Bob's
    text, err = call(env, ann["access_token"], "get_account", {"account_id": env["bobs"]})
    assert err and "not found" in text
    text, err = call(env, ann["access_token"], "get_account", {"account_id": env["mine"], "include": ["people"]})
    assert not err and "Orchard Bank" in text and BASE + f"/accounts/{env['mine']}" in text
    text, err = call(env, ann["access_token"], "search", {"query": "orchard"})
    res = json.loads(text)["results"]
    assert any(r["id"] == f"account:{env['mine']}" and r["url"] == f"{BASE}/accounts/{env['mine']}" for r in res)
    assert not any(env["bobs"] in str(r["id"]) for r in res)
    text, err = call(env, ann["access_token"], "fetch", {"id": f"account:{env['mine']}"})
    assert not err and json.loads(text)["title"] == "Orchard Bank"


def test_a_role_without_account_access_gets_neither_the_tools_nor_the_data(env):
    _, mia = connect(env, "mia")
    names = {t["name"] for t in rpc(env, mia["access_token"], "tools/list").json()["result"]["tools"]}
    assert "list_accounts" not in names and "get_account" not in names and "competitors" in names
    text, err = call(env, mia["access_token"], "api_get", {"path": "/api/accounts"})
    assert err and "cannot read" in text
    text, err = call(env, mia["access_token"], "list_accounts")
    assert err and "unknown tool" in text


def test_api_get_reaches_only_listed_json_endpoints(env):
    _, ann = connect(env)
    tok = ann["access_token"]
    eps = json.loads(call(env, tok, "list_endpoints")[0])["endpoints"]
    paths = {e["path"] for e in eps}
    assert "/api/accounts/{oid}/tenders" in paths and "/api/leads.csv" not in paths and "/api/admin/users" not in paths and "/api/admin/costs" not in paths
    assert call(env, tok, "api_get", {"path": "/api/leads.csv"})[1] is True
    assert call(env, tok, "api_get", {"path": "/api/../healthz"})[1] is True
    assert call(env, tok, "api_get", {"path": "/api/me?x=1"})[1] is True
    text, err = call(env, tok, "api_get", {"path": f"/api/accounts/{env['mine']}/tenders"})
    assert not err


def test_the_internal_token_only_reads(env):
    app = env["app"]
    tok = issue_internal(app.state.internal_key, "ann@x", "test")
    assert env["c"].get("/api/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    assert env["c"].post("/api/auth/session", headers={"Authorization": f"Bearer {tok}"}, json={}).status_code == 403   # no swap for a real session
    forged = issue_internal(b"k" * 32, "ann@x", "test")
    assert env["c"].get("/api/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_disconnecting_in_the_app_ends_access_at_once(env):
    _, tok = connect(env)
    c = env["c"]
    apps = c.get("/api/me/ai-apps", headers=env["h"]("ann")).json()
    assert apps["mcp_url"] == BASE + "/mcp" and [a["name"] for a in apps["items"]] == ["Claude"] and apps["items"][0]["where"]["known_as"] == "Claude"
    assert c.get("/api/me/ai-apps", headers=env["h"]("bob")).json()["items"] == []
    gid = apps["items"][0]["id"]
    assert c.delete(f"/api/me/ai-apps/{gid}", headers=env["h"]("bob")).status_code == 404            # only your own
    assert c.delete(f"/api/me/ai-apps/{gid}", headers=env["h"]("ann")).status_code == 200
    assert rpc(env, tok["access_token"], "ping").status_code == 401
    assert c.get("/api/me/ai-apps", headers=env["h"]("ann")).json()["items"] == []


def test_a_deactivated_person_loses_access(env):
    _, tok = connect(env)
    env["conn"].execute("UPDATE users SET active=false WHERE email='ann@x'")
    env["conn"].commit()
    assert rpc(env, tok["access_token"], "ping").status_code == 401


def test_tool_calls_are_recorded_for_the_person(env):
    _, tok = connect(env)
    call(env, tok["access_token"], "whoami")
    ev = q1(env["conn"], "SELECT event, detail FROM product_events WHERE event='ai_tool_call'")
    assert ev["detail"]["via"] == "mcp:Claude" and ev["detail"]["tool"] == "whoami" and ev["detail"]["ok"] is True


# ------------------------------------------------------------------ the in-app assistant
class FakeModel:
    """First asks to search, then answers. Records every request body."""

    def __init__(self):
        self.bodies = []

    async def __call__(self, key, body):
        self.bodies.append((key, json.loads(json.dumps(body))))
        if len(self.bodies) == 1:
            return {"stop_reason": "tool_use", "usage": {"input_tokens": 900, "output_tokens": 40},
                    "content": [{"type": "text", "text": "Looking."}, {"type": "tool_use", "id": "tu_1", "name": "search", "input": {"query": "orchard"}}]}
        return {"stop_reason": "end_turn", "usage": {"input_tokens": 1500, "output_tokens": 80}, "content": [{"type": "text", "text": "You own **Orchard Bank**."}]}


def ask(env, who="ann", q="What do we know about Orchard?"):
    r = env["c"].post("/api/assistant/chat", headers=env["h"](who), json={"messages": [{"role": "user", "content": q}]})
    return r, [json.loads(line) for line in r.text.splitlines() if line.strip()]


def test_the_assistant_looks_things_up_as_the_person_and_answers(env):
    fake = FakeModel()
    env["app"].state.assistant_post = fake
    r, evs = ask(env)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    assert [e["type"] for e in evs] == ["tool", "answer", "done"]
    assert evs[0]["name"] == "search" and evs[1]["text"] == "You own **Orchard Bank**." and evs[2]["personal_key"] is False
    key, second = fake.bodies[1]
    assert key == "sk-ant-company-000000000000"
    result = second["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and "Orchard Bank" in result["content"] and "Orchard Lending" not in result["content"]
    assert "ann@x" in second["system"][0]["text"]
    b = q1(env["conn"], "SELECT spent, reserved FROM provider_budgets WHERE provider='anthropic' AND kind='assistant'")
    assert float(b["spent"]) == 900 + 40 + 1500 + 80 and float(b["reserved"]) == 0
    assert q1(env["conn"], "SELECT count(*) AS n FROM provider_usage WHERE operation='assistant_chat'")["n"] == 2


def test_the_assistant_stops_at_its_own_allowance(env, monkeypatch):
    monkeypatch.setenv("RI_ASSISTANT_MONTHLY_TOKEN_CAP", "1000")
    env["app"].state.assistant_post = FakeModel()
    _, evs = ask(env)
    assert evs[-1]["type"] == "error" and "allowance" in evs[-1]["message"]
    st = env["c"].get("/api/assistant/status", headers=env["h"]("ann")).json()
    assert st["company"]["configured"] is True and st["source"] == "company"


def test_a_personal_key_is_sealed_used_instead_of_the_company_key_and_removable(env):
    c, app = env["c"], env["app"]

    async def accept(k):
        return None if k.endswith("good") else "Anthropic did not accept this key."
    app.state.assistant_check_key = accept
    assert c.put("/api/me/ai-key", headers=env["h"]("ann"), json={"api_key": "not-a-key-0000000000000"}).status_code == 400
    assert c.put("/api/me/ai-key", headers=env["h"]("ann"), json={"api_key": "sk-ant-api03-abcdefghijk-bad"}).status_code == 400
    ok = c.put("/api/me/ai-key", headers=env["h"]("ann"), json={"api_key": "sk-ant-api03-abcdefghijk-good"})
    assert ok.status_code == 200 and ok.json()["hint"] == "...good"
    raw = q1(env["conn"], "SELECT secret_enc FROM ai_personal_keys")["secret_enc"]
    assert b"sk-ant" not in bytes(raw)
    st = c.get("/api/assistant/status", headers=env["h"]("ann")).json()
    assert st["source"] == "personal" and st["personal_key"]["hint"] == "...good" and "sk-ant" not in json.dumps(st)
    fake = FakeModel()
    app.state.assistant_post = fake
    _, evs = ask(env)
    assert fake.bodies[0][0] == "sk-ant-api03-abcdefghijk-good" and evs[-1]["personal_key"] is True
    assert q1(env["conn"], "SELECT count(*) AS n FROM provider_budgets WHERE kind='assistant'")["n"] == 0      # the company allowance is untouched
    assert c.get("/api/assistant/status", headers=env["h"]("bob")).json()["source"] == "company"                 # Bob does not get Ann's key
    assert c.delete("/api/me/ai-key", headers=env["h"]("ann")).status_code == 200
    assert c.get("/api/assistant/status", headers=env["h"]("ann")).json()["source"] == "company"


def test_the_assistant_answers_even_when_it_keeps_asking_for_tools(env):
    class Greedy:
        n = 0

        async def __call__(self, key, body):
            Greedy.n += 1
            if body.get("tool_choice", {}).get("type") == "none":
                return {"stop_reason": "end_turn", "usage": {}, "content": [{"type": "text", "text": "Here is what I found."}]}
            return {"stop_reason": "tool_use", "usage": {}, "content": [{"type": "tool_use", "id": f"t{Greedy.n}", "name": "whoami", "input": {}}]}
    env["app"].state.assistant_post = Greedy()
    _, evs = ask(env)
    assert evs[-2] == {"type": "answer", "text": "Here is what I found."} and Greedy.n == 9
