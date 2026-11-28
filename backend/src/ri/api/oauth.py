"""The app as an OAuth 2.1 authorization server for its own MCP server, so people can connect their own Claude or ChatGPT.

What the MCP authorization spec (2025-11-25) and Claude's and ChatGPT's connector docs ask for:
  - Protected resource metadata (RFC 9728) at /.well-known/oauth-protected-resource[/mcp], and a 401 with `WWW-Authenticate: Bearer resource_metadata=...`
  - Authorization server metadata (RFC 8414) at /.well-known/oauth-authorization-server, with PKCE S256 advertised
  - Dynamic Client Registration (RFC 7591) at /oauth/register (Claude and ChatGPT register themselves; nobody pastes a client id)
  - Authorization code + PKCE S256 only, `resource` (RFC 8707) checked and the tokens bound to this MCP server
  - Refresh tokens rotated on every use; a spent one presented again revokes the whole grant
  - /oauth/token takes application/x-www-form-urlencoded

Sign-in is the app's usual Google sign-in: /oauth/authorize hands over to the SPA's consent page (/connect/authorize), which asks the signed-in person
to approve the client and shows where the code will be sent. Redirects are limited to known assistant hosts (claude.ai, claude.com, chatgpt.com,
openai.com) and the person's own computer (Claude Code and similar), unless RI_MCP_REDIRECT_HOSTS says otherwise. Tokens are stored as hashes."""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from ..access import Principal, principal_from_row
from ..audit import audit
from ..db import q1, qa
from .auth import principal_dep

SCOPE = "ri.read"
ACCESS_TTL = timedelta(hours=1)
CODE_TTL = timedelta(minutes=5)
REFRESH_REUSE_GRACE = timedelta(seconds=30)        # two parallel refreshes with the same token: the second just fails, nothing is revoked
DEFAULT_REDIRECT_HOSTS = ("claude.ai", "claude.com", "chatgpt.com", "chat.openai.com", "platform.openai.com")
LOOPBACK = {"localhost", "127.0.0.1", "[::1]", "::1"}
KNOWN_CLIENTS = {"claude.ai": "Claude", "claude.com": "Claude", "chatgpt.com": "ChatGPT", "chat.openai.com": "ChatGPT", "platform.openai.com": "OpenAI"}


def refresh_ttl() -> timedelta:
    return timedelta(days=max(1, min(90, int(os.environ.get("RI_MCP_REFRESH_DAYS", "30") or 30))))


def redirect_hosts() -> tuple[set[str], bool]:
    """Allowed redirect hosts and whether any https host is allowed ('*')."""
    raw = os.environ.get("RI_MCP_REDIRECT_HOSTS")
    hosts = [h.strip().lower() for h in (raw.split(",") if raw is not None else DEFAULT_REDIRECT_HOSTS) if h.strip()]
    return set(hosts) - {"*"}, "*" in hosts


def h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def now() -> datetime:
    return datetime.now(UTC)


def issuer(request: Request) -> str:
    s = request.app.state.settings
    return (s.public_url or str(request.base_url)).rstrip("/")


def mcp_resource(request: Request) -> str:
    return issuer(request) + "/mcp"


def _norm_resource(r: str) -> str:
    try:
        u = urlsplit(r.strip())
    except ValueError:
        return ""
    return f"{u.scheme.lower()}://{u.netloc.lower()}{u.path.rstrip('/')}"


def resource_ok(request: Request, given: str | None) -> bool:
    """RFC 8707: the client names the MCP server it wants a token for. Accept this server's MCP URL (or its origin); refuse anything else."""
    if not given:
        return True                     # older clients send none; the token is still bound to this server
    g = _norm_resource(given)
    return g in {_norm_resource(mcp_resource(request)), _norm_resource(issuer(request))}


def redirect_allowed(uri: str) -> bool:
    try:
        u = urlsplit(uri)
    except ValueError:
        return False
    if u.fragment or not u.hostname:
        return False
    host = u.hostname.lower()
    if u.scheme == "http":
        return host in LOOPBACK
    if u.scheme != "https":
        return False
    hosts, any_https = redirect_hosts()
    return any_https or host in hosts or host in LOOPBACK


def redirect_matches(registered: list[str], given: str) -> bool:
    """Exact match, except that a loopback redirect may use any port (RFC 8252 section 7.3; Claude Code picks a port per session)."""
    if given in registered:
        return True
    try:
        g = urlsplit(given)
    except ValueError:
        return False
    if (g.hostname or "").lower() not in LOOPBACK:
        return False
    for r in registered:
        u = urlsplit(r)
        if (u.hostname or "").lower() in LOOPBACK and u.scheme == g.scheme and u.path == g.path and u.query == g.query:
            return True
    return False


def describe_redirect(uri: str) -> dict[str, Any]:
    host = (urlsplit(uri).hostname or "").lower()
    return {"host": host, "known_as": KNOWN_CLIENTS.get(host), "loopback": host in LOOPBACK}


def oauth_error(error: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=status, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def _page(title: str, text: str, status: int = 400) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>{html.escape(title)}</title>"
                        f"<body style='font:16px system-ui;max-width:36rem;margin:3rem auto;padding:0 16px'><h1>{html.escape(title)}</h1><p>{html.escape(text)}</p>",
                        status_code=status)


class ApproveIn(BaseModel):
    client_id: str = Field(max_length=200)
    redirect_uri: str = Field(max_length=2000)
    code_challenge: str = Field(min_length=43, max_length=128)
    code_challenge_method: str = "S256"
    state: str | None = Field(None, max_length=2000)
    scope: str | None = Field(None, max_length=500)
    resource: str | None = Field(None, max_length=2000)
    approve: bool


def _client(conn, client_id: str) -> dict[str, Any] | None:
    return q1(conn, "SELECT * FROM oauth_clients WHERE client_id=%s", (client_id,))


def _issue_tokens(conn, grant_id: str) -> dict[str, Any]:
    access, refresh = "rimcp_at_" + secrets.token_urlsafe(32), "rimcp_rt_" + secrets.token_urlsafe(40)
    t = now()
    conn.execute("INSERT INTO oauth_tokens(token_hash, grant_id, kind, expires_at) VALUES (%s,%s,'access',%s), (%s,%s,'refresh',%s)",
                 (h(access), grant_id, t + ACCESS_TTL, h(refresh), grant_id, t + refresh_ttl()))
    conn.execute("UPDATE oauth_grants SET last_used_at=now() WHERE id=%s", (grant_id,))
    conn.execute("DELETE FROM oauth_tokens WHERE grant_id=%s AND expires_at < now() - interval '1 day'", (grant_id,))      # keep the table small
    return {"access_token": access, "token_type": "Bearer", "expires_in": int(ACCESS_TTL.total_seconds()), "refresh_token": refresh, "scope": SCOPE}


def principal_for_token(request: Request, token: str) -> tuple[Principal, dict[str, Any]] | None:
    """The person behind an MCP access token, or None (unknown, expired, revoked, other audience, or the person is no longer active)."""
    if not token.startswith("rimcp_at_"):
        return None
    with request.app.state.db.tx() as conn:
        row = q1(conn, "SELECT g.id AS grant_id, g.resource, g.client_id, c.client_name, u.* FROM oauth_tokens t JOIN oauth_grants g ON g.id=t.grant_id "
                       "JOIN oauth_clients c ON c.client_id=g.client_id JOIN users u ON u.id=g.user_id "
                       "WHERE t.token_hash=%s AND t.kind='access' AND t.expires_at > now() AND g.revoked_at IS NULL AND u.active", (h(token),))
        if not row or _norm_resource(row["resource"]) != _norm_resource(mcp_resource(request)):
            return None
        dom = request.app.state.settings.allowed_email_domain
        if dom and not str(row["email"]).lower().endswith("@" + dom):
            return None
        conn.execute("UPDATE oauth_grants SET last_used_at=now() WHERE id=%s AND (last_used_at IS NULL OR last_used_at < now() - interval '5 minutes')", (row["grant_id"],))
    return principal_from_row(row), {"grant_id": str(row["grant_id"]), "client_name": row["client_name"], "client_id": row["client_id"]}


def register(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    # ------------------------------------------------------------------ discovery
    def resource_metadata(request: Request) -> dict[str, Any]:
        return {"resource": mcp_resource(request), "authorization_servers": [issuer(request)], "scopes_supported": [SCOPE],
                "bearer_methods_supported": ["header"], "resource_name": "The Codest Revenue Intelligence",
                "resource_documentation": issuer(request) + "/connections"}

    @app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    def prm(request: Request):
        return resource_metadata(request)

    @app.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
    def prm_mcp(request: Request):
        return resource_metadata(request)

    def as_metadata(request: Request) -> dict[str, Any]:
        base = issuer(request)
        return {"issuer": base, "authorization_endpoint": base + "/oauth/authorize", "token_endpoint": base + "/oauth/token",
                "registration_endpoint": base + "/oauth/register", "revocation_endpoint": base + "/oauth/revoke",
                "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"], "scopes_supported": [SCOPE],
                "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
                "revocation_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
                "service_documentation": base + "/connections"}

    @app.get("/.well-known/oauth-authorization-server", include_in_schema=False)
    def asm(request: Request):
        return as_metadata(request)

    @app.get("/.well-known/oauth-authorization-server/mcp", include_in_schema=False)
    def asm_mcp(request: Request):
        return as_metadata(request)

    # ------------------------------------------------------------------ dynamic client registration (RFC 7591)
    @app.post("/oauth/register", include_in_schema=False)
    async def register_client(request: Request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return oauth_error("invalid_client_metadata", "send the client metadata as JSON")
        if not isinstance(body, dict):
            return oauth_error("invalid_client_metadata", "send the client metadata as a JSON object")
        uris = body.get("redirect_uris")
        if not isinstance(uris, list) or not uris or len(uris) > 10 or not all(isinstance(u, str) and len(u) <= 2000 for u in uris):
            return oauth_error("invalid_redirect_uri", "redirect_uris must list 1 to 10 addresses")
        bad = [u for u in uris if not redirect_allowed(u)]
        if bad:
            return oauth_error("invalid_redirect_uri", f"this server only sends sign-ins back to Claude, ChatGPT or your own computer, not to {bad[0][:200]}")
        method = body.get("token_endpoint_auth_method") or "none"
        if method not in {"none", "client_secret_post", "client_secret_basic"}:
            return oauth_error("invalid_client_metadata", "token_endpoint_auth_method must be none, client_secret_post or client_secret_basic")
        grants = body.get("grant_types") or ["authorization_code", "refresh_token"]
        if not isinstance(grants, list) or "authorization_code" not in grants:
            return oauth_error("invalid_client_metadata", "grant_types must include authorization_code")
        name = str(body.get("client_name") or "").strip()[:120] or (describe_redirect(uris[0])["known_as"] or "An AI assistant")
        client_uri = body.get("client_uri") if isinstance(body.get("client_uri"), str) and str(body.get("client_uri")).startswith("https://") else None
        cid = "ri_" + secrets.token_urlsafe(18)
        secret = "ris_" + secrets.token_urlsafe(32) if method != "none" else None
        with tx(request) as conn:
            if q1(conn, "SELECT count(*) AS n FROM oauth_clients WHERE created_at > now() - interval '1 hour'")["n"] > 500:      # registration flood
                return oauth_error("temporarily_unavailable", "too many registrations; try again later", 429)
            conn.execute("INSERT INTO oauth_clients(client_id, client_secret_hash, client_name, redirect_uris, token_endpoint_auth_method, client_uri) "
                         "VALUES (%s,%s,%s,%s,%s,%s)", (cid, h(secret) if secret else None, name, uris, method, client_uri))
        out = {"client_id": cid, "client_id_issued_at": int(now().timestamp()), "client_name": name, "redirect_uris": uris,
               "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "token_endpoint_auth_method": method, "scope": SCOPE}
        if secret:
            out |= {"client_secret": secret, "client_secret_expires_at": 0}
        return JSONResponse(out, status_code=201, headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------------ authorization
    @app.get("/oauth/authorize", include_in_schema=False)
    def authorize(request: Request):
        qp = request.query_params
        cid, ruri = qp.get("client_id", ""), qp.get("redirect_uri", "")
        with tx(request) as conn:
            c = _client(conn, cid) if cid else None
        if not c:
            return _page("Unknown app", "This sign-in link names an app that has not registered with The Codest Revenue Intelligence. Start the connection again from Claude or ChatGPT.")
        if not ruri and len(c["redirect_uris"]) == 1:
            ruri = c["redirect_uris"][0]
        if not ruri or not redirect_matches(list(c["redirect_uris"]), ruri):
            return _page("Wrong return address", "This sign-in link would send you to an address the app did not register, so it was stopped.")

        def back(error: str, desc: str):
            q = {"error": error, "error_description": desc} | ({"state": qp["state"]} if qp.get("state") else {}) | {"iss": issuer(request)}
            return RedirectResponse(ruri + ("&" if "?" in ruri else "?") + urlencode(q), status_code=302)
        if qp.get("response_type") != "code":
            return back("unsupported_response_type", "only response_type=code is supported")
        if not qp.get("code_challenge") or qp.get("code_challenge_method", "plain") != "S256":
            return back("invalid_request", "PKCE with code_challenge_method=S256 is required")
        if not resource_ok(request, qp.get("resource")):
            return back("invalid_target", "this server only issues tokens for its own MCP endpoint")
        keep = {k: qp[k] for k in ("client_id", "redirect_uri", "state", "code_challenge", "code_challenge_method", "scope", "resource") if qp.get(k)}
        keep["redirect_uri"] = ruri
        return RedirectResponse("/connect/authorize?" + urlencode(keep), status_code=302)

    @app.get("/api/oauth/request")
    def describe_request(request: Request, client_id: str, redirect_uri: str, p: Principal = Depends(principal_dep)):
        """What the consent page shows: which app, where the sign-in goes, and as whom."""
        with tx(request) as conn:
            c = _client(conn, client_id)
            if not c or not redirect_matches(list(c["redirect_uris"]), redirect_uri):
                raise HTTPException(400, "This connection request is not valid any more. Start again from Claude or ChatGPT.")
            earlier = q1(conn, "SELECT count(*) AS n FROM oauth_grants WHERE user_id=%s AND client_id=%s AND revoked_at IS NULL", (p.user_id, client_id))["n"]
        return {"client_name": c["client_name"], "redirect": describe_redirect(redirect_uri), "email": p.email, "role": p.role,
                "scope": SCOPE, "already_connected": earlier > 0, "server": mcp_resource(request)}

    @app.post("/api/oauth/approve")
    def approve(body: ApproveIn, request: Request, p: Principal = Depends(principal_dep)):
        with tx(request) as conn:
            c = _client(conn, body.client_id)
            if not c or not redirect_matches(list(c["redirect_uris"]), body.redirect_uri):
                raise HTTPException(400, "This connection request is not valid any more. Start again from Claude or ChatGPT.")
            sep = "&" if "?" in body.redirect_uri else "?"
            st = {"state": body.state} if body.state else {}
            if not body.approve:
                audit(conn, actor=p.email, operation="ai_connection_declined", record_type="oauth_client", record_id=c["client_id"], detail={"client": c["client_name"]})
                return {"redirect": body.redirect_uri + sep + urlencode({"error": "access_denied", "error_description": "the person declined"} | st | {"iss": issuer(request)})}
            if body.code_challenge_method != "S256":
                raise HTTPException(400, "PKCE S256 is required")
            if not resource_ok(request, body.resource):
                raise HTTPException(400, "this server only issues tokens for its own MCP endpoint")
            g = q1(conn, "INSERT INTO oauth_grants(user_id, client_id, scope, resource) VALUES (%s,%s,%s,%s) RETURNING id",
                   (p.user_id, c["client_id"], SCOPE, mcp_resource(request)))
            code = secrets.token_urlsafe(32)
            conn.execute("INSERT INTO oauth_codes(code_hash, grant_id, redirect_uri, code_challenge, expires_at) VALUES (%s,%s,%s,%s,%s)",
                         (h(code), g["id"], body.redirect_uri, body.code_challenge, now() + CODE_TTL))
            audit(conn, actor=p.email, operation="ai_connection_approved", record_type="oauth_grant", record_id=str(g["id"]),
                  detail={"client": c["client_name"], "redirect_host": describe_redirect(body.redirect_uri)["host"]})
        return {"redirect": body.redirect_uri + sep + urlencode({"code": code} | st | {"iss": issuer(request)})}

    # ------------------------------------------------------------------ token endpoint
    async def form(request: Request) -> dict[str, str]:
        ctype = request.headers.get("content-type", "")
        if "json" in ctype:
            try:
                j = await request.json()
                return {k: str(v) for k, v in j.items()} if isinstance(j, dict) else {}
            except Exception:  # noqa: BLE001
                return {}
        f = await request.form()
        return {k: str(v) for k, v in f.items()}

    def client_auth(conn, request: Request, f: dict[str, str]) -> dict[str, Any] | None:
        cid, secret = f.get("client_id", ""), f.get("client_secret")
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("basic "):
            try:
                from urllib.parse import unquote
                u, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
                cid, secret = unquote(u), unquote(pw)
            except Exception:  # noqa: BLE001
                return None
        c = _client(conn, cid) if cid else None
        if not c:
            return None
        if c["client_secret_hash"]:
            if not secret or not hmac.compare_digest(c["client_secret_hash"], h(secret)):
                return None
        return c

    @app.post("/oauth/token", include_in_schema=False)
    async def token(request: Request):
        f = await form(request)
        gt = f.get("grant_type")
        with tx(request) as conn:
            c = client_auth(conn, request, f)
            if not c:
                return oauth_error("invalid_client", "unknown client or wrong client credentials", 401)
            if gt == "authorization_code":
                code, verifier = f.get("code", ""), f.get("code_verifier", "")
                row = q1(conn, "UPDATE oauth_codes SET used_at=now() WHERE code_hash=%s AND used_at IS NULL RETURNING *", (h(code),)) if code else None
                if not row:
                    used = q1(conn, "SELECT grant_id FROM oauth_codes WHERE code_hash=%s", (h(code),)) if code else None
                    if used:          # a code replayed: whoever holds the tokens from it may not be the person (OAuth 2.1 section 4.1.3)
                        conn.execute("UPDATE oauth_grants SET revoked_at=now(), revoked_reason='code reused' WHERE id=%s AND revoked_at IS NULL", (used["grant_id"],))
                    return oauth_error("invalid_grant", "the code is unknown, used or expired")
                g = q1(conn, "SELECT * FROM oauth_grants WHERE id=%s", (row["grant_id"],))
                if row["expires_at"] < now() or not g or g["revoked_at"] or g["client_id"] != c["client_id"]:
                    return oauth_error("invalid_grant", "the code is unknown, used or expired")
                if f.get("redirect_uri") and f["redirect_uri"] != row["redirect_uri"]:
                    return oauth_error("invalid_grant", "redirect_uri does not match the authorization request")
                if not (43 <= len(verifier) <= 128):
                    return oauth_error("invalid_grant", "code_verifier is missing or the wrong length")
                calc = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
                if not hmac.compare_digest(calc, row["code_challenge"]):
                    return oauth_error("invalid_grant", "PKCE verification failed")
                if not resource_ok(request, f.get("resource")):
                    return oauth_error("invalid_target", "this server only issues tokens for its own MCP endpoint")
                conn.execute("UPDATE oauth_clients SET last_used_at=now() WHERE client_id=%s", (c["client_id"],))
                return JSONResponse(_issue_tokens(conn, str(g["id"])), headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
            if gt == "refresh_token":
                rt = f.get("refresh_token", "")
                row = q1(conn, "SELECT t.*, g.client_id, g.revoked_at, u.active FROM oauth_tokens t JOIN oauth_grants g ON g.id=t.grant_id JOIN users u ON u.id=g.user_id "
                               "WHERE t.token_hash=%s AND t.kind='refresh'", (h(rt),)) if rt else None
                if not row or row["client_id"] != c["client_id"] or row["revoked_at"] or not row["active"] or row["expires_at"] < now():
                    return oauth_error("invalid_grant", "the refresh token is not valid; connect again")
                if not resource_ok(request, f.get("resource")):
                    return oauth_error("invalid_target", "this server only issues tokens for its own MCP endpoint")
                spent = q1(conn, "UPDATE oauth_tokens SET used_at=now() WHERE token_hash=%s AND used_at IS NULL RETURNING token_hash", (h(rt),))
                if not spent:
                    if row["used_at"] and now() - row["used_at"] > REFRESH_REUSE_GRACE:
                        conn.execute("UPDATE oauth_grants SET revoked_at=now(), revoked_reason='refresh token reused' WHERE id=%s AND revoked_at IS NULL", (row["grant_id"],))
                    return oauth_error("invalid_grant", "the refresh token was already used; connect again")
                return JSONResponse(_issue_tokens(conn, str(row["grant_id"])), headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
        return oauth_error("unsupported_grant_type", "grant_type must be authorization_code or refresh_token")

    @app.post("/oauth/revoke", include_in_schema=False)
    async def revoke(request: Request):
        """RFC 7009: revoking either token ends the whole connection (the person connects again to get a new one)."""
        f = await form(request)
        with tx(request) as conn:
            c = client_auth(conn, request, f)
            if not c:
                return oauth_error("invalid_client", "unknown client or wrong client credentials", 401)
            row = q1(conn, "SELECT t.grant_id FROM oauth_tokens t JOIN oauth_grants g ON g.id=t.grant_id WHERE t.token_hash=%s AND g.client_id=%s",
                     (h(f.get("token", "")), c["client_id"]))
            if row:
                conn.execute("UPDATE oauth_grants SET revoked_at=now(), revoked_reason='revoked by the app' WHERE id=%s AND revoked_at IS NULL", (row["grant_id"],))
        return JSONResponse({}, status_code=200)

    # ------------------------------------------------------------------ the person's own connected assistants
    @app.get("/api/me/ai-apps")
    def my_ai_apps(request: Request, p: Principal = Depends(principal_dep)):
        """The AI assistants (Claude, ChatGPT, ...) this person has connected to the app, and the address to connect a new one."""
        with tx(request) as conn:
            rows = qa(conn, "SELECT g.id, c.client_name, c.redirect_uris, g.created_at, g.last_used_at FROM oauth_grants g JOIN oauth_clients c USING (client_id) "
                            "WHERE g.user_id=%s AND g.revoked_at IS NULL AND EXISTS (SELECT 1 FROM oauth_tokens t WHERE t.grant_id=g.id AND t.kind='refresh' AND t.expires_at > now() AND t.used_at IS NULL) "
                            "ORDER BY coalesce(g.last_used_at, g.created_at) DESC", (p.user_id,))
        return {"mcp_url": mcp_resource(request), "items": [{"id": str(r["id"]), "name": r["client_name"], "where": describe_redirect(r["redirect_uris"][0]),
                                                              "connected_at": r["created_at"], "last_used_at": r["last_used_at"]} for r in rows]}

    @app.delete("/api/me/ai-apps/{gid}")
    def disconnect_ai_app(gid: str, request: Request, p: Principal = Depends(principal_dep)):
        with tx(request) as conn:
            row = q1(conn, "UPDATE oauth_grants SET revoked_at=now(), revoked_reason='disconnected by the person' WHERE id::text=%s AND user_id=%s AND revoked_at IS NULL RETURNING id",
                     (gid, p.user_id))
            if not row:
                raise HTTPException(404, "not found")
            audit(conn, actor=p.email, operation="ai_connection_revoked", record_type="oauth_grant", record_id=str(row["id"]))
        return {"ok": True}
