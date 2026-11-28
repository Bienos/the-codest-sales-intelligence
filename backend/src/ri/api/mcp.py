"""The app's remote MCP server: people connect their own Claude (claude.ai, Desktop, mobile, Claude Code) or ChatGPT and ask about the app's data
with their own subscription. Streamable HTTP transport, stateless, one JSON response per request (no server-sent events needed: every tool
answers in one go). Sign-in is OAuth 2.1 against the app itself (api/oauth.py); every call acts as the person who connected, read-only.

Add it in Claude: Settings > Connectors > Add custom connector > https://<app>/mcp. In ChatGPT: Settings > Apps & Connectors (developer mode) >
Create > the same address, authentication OAuth. Both register themselves and send the person through the app's Google sign-in and consent page."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from ..assistant import tools as T
from ..db import jb
from .oauth import LOOPBACK, SCOPE, issuer, principal_for_token, redirect_hosts

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "the-codest-revenue-intelligence", "title": "The Codest Revenue Intelligence", "version": "1.0.0"}


def _rpc_error(mid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _unauthorized(request: Request, had_token: bool) -> Response:
    meta = issuer(request) + "/.well-known/oauth-protected-resource/mcp"
    challenge = f'Bearer resource_metadata="{meta}", scope="{SCOPE}"' + (', error="invalid_token", error_description="the token is unknown, expired or revoked"' if had_token else "")
    return JSONResponse({"error": "invalid_token" if had_token else "unauthorized", "error_description": "connect this app again to sign in"},
                        status_code=401, headers={"WWW-Authenticate": challenge})


def _origin_ok(request: Request) -> bool:
    """DNS-rebinding guard from the transport spec: a browser Origin must be this app, a known assistant, or the person's own machine."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    host = (urlsplit(origin).hostname or "").lower()
    hosts, any_https = redirect_hosts()
    own = (urlsplit(issuer(request)).hostname or "").lower()
    return host == own or host in LOOPBACK or host in hosts or any(host.endswith("." + x) for x in hosts) or any_https


def register(app: FastAPI) -> None:
    @app.get("/mcp", include_in_schema=False)
    def mcp_get():
        return Response(status_code=405, headers={"Allow": "POST"})       # no server-initiated stream: every answer comes back on the POST

    @app.delete("/mcp", include_in_schema=False)
    def mcp_delete():
        return Response(status_code=405, headers={"Allow": "POST"})       # stateless: there is no session to end

    @app.post("/mcp", include_in_schema=False)
    async def mcp_post(request: Request):
        if not _origin_ok(request):
            return JSONResponse(_rpc_error(None, -32600, "origin not allowed"), status_code=403)
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        who = principal_for_token(request, token) if token else None
        if not who:
            return _unauthorized(request, bool(token))
        principal, grant = who
        try:
            msg = json.loads(await request.body() or b"null")
        except ValueError:
            return JSONResponse(_rpc_error(None, -32700, "parse error"), status_code=400)
        if isinstance(msg, list):
            return JSONResponse(_rpc_error(None, -32600, "batches are not supported; send one message per request"), status_code=400)
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return JSONResponse(_rpc_error(None, -32600, "invalid request"), status_code=400)
        method, mid = msg.get("method"), msg.get("id")
        if method is None or mid is None:                     # a notification or a response from the client: nothing to answer
            return Response(status_code=202)
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            return JSONResponse(_rpc_error(mid, -32602, "params must be an object"))

        if method == "initialize":
            asked = str(params.get("protocolVersion") or "")
            version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return JSONResponse({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": version, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO,
                "instructions": T.INSTRUCTIONS + f" You are signed in as {principal.email} ({principal.role})."}})
        if method == "ping":
            return JSONResponse({"jsonrpc": "2.0", "id": mid, "result": {}})
        if method == "tools/list":
            return JSONResponse({"jsonrpc": "2.0", "id": mid, "result": {"tools": [t.spec() for t in T.tools_for(request.app, principal)]}})
        if method == "tools/call":
            name = str(params.get("name") or "")
            args = params.get("arguments") or {}
            ctx = T.ToolContext(request.app, principal, "mcp:" + str(grant["client_name"])[:60], issuer(request))
            text, is_error = await T.call_tool(ctx, name, args)
            with request.app.state.db.tx() as conn:
                conn.execute("INSERT INTO product_events(user_id, event, detail) VALUES (%s,'ai_tool_call',%s)",
                             (principal.user_id, jb({"via": ctx.via, "tool": name, "ok": not is_error, "reads": ctx.calls[:10]})))
            return JSONResponse({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": is_error}})
        if method in {"resources/list", "resources/templates/list", "prompts/list"}:
            key = {"resources/list": "resources", "resources/templates/list": "resourceTemplates", "prompts/list": "prompts"}[method]
            return JSONResponse({"jsonrpc": "2.0", "id": mid, "result": {key: []}})
        return JSONResponse(_rpc_error(mid, -32601, f"method not found: {method}"))
