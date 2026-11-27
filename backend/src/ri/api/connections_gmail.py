"""Connecting a person's own Gmail (read-only) with Google's authorisation-code flow.

POST /api/me/connections/gmail/start      -> {"url": <Google consent address>} for the signed-in person.
GET  /api/me/connections/gmail/callback   -> where Google sends the browser back. It carries NO bearer token (it is a plain browser navigation),
                                             so it is authenticated by the signed `state` alone: a 10-minute, tamper-proof token naming the person
                                             who pressed "Connect". It swaps the code for tokens, stores the refresh token sealed (`store.connect`),
                                             writes an audit entry, queues a first sync and sends the browser to /connections?connected=gmail
                                             (or /connections?error=<plain reason>). No token ever appears in an address, a response or a log.
The scope is exactly gmail.readonly (plus openid email). The settings, sync and disconnect routes belong to api/connections_api.py; its disconnect
calls `gmail_api.revoke_token_sync` (or `revoke_token`) to ask Google to forget the token first."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import vault
from ..access import Principal
from ..audit import audit
from ..connections import gmail_api, gmail_sync, store
from ..db import q1
from .auth import principal_dep, session_key

log = logging.getLogger("ri.connections.gmail")

STATE_TTL_S = 600
CALLBACK_PATH = "/api/me/connections/gmail/callback"

MSG_LINK = "That connection link has expired or is not valid. Please connect Gmail again from the Connections page."
MSG_DENIED = "You chose not to allow access, so Gmail was not connected."
MSG_GOOGLE_STOPPED = "Google did not finish the connection. Please try again."
MSG_REFUSED = "Google refused the connection. Please try again."
MSG_SETTINGS = "Google did not accept this app's settings. An administrator needs to check the Google client and the redirect address."
MSG_UNREACHABLE = "Google could not be reached. Please try again in a minute."
MSG_NO_SCOPE = "Gmail read access was not allowed, so nothing was connected. Tick the Gmail box on Google's screen and try again."
MSG_NO_REFRESH = "Google did not give lasting access. Remove this app under your Google account's security settings, then connect again."
MSG_NO_ADDRESS = "Google did not say which address was connected. Please try again."
MSG_NO_USER = "This account cannot connect Gmail."
MSG_VAULT = "Connections are not set up yet: an administrator needs to set the server's encryption key."
MSG_SECRET = "Gmail is not set up yet: an administrator needs to add the Google client secret."
MSG_CLIENT = "Gmail is not set up yet: Google sign-in is not configured on the server."
MSG_SESSIONS = "Gmail cannot be connected yet: sign-in sessions are not configured on the server."


# ---------------------------------------------------------------- the signed `state`
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _state_key(settings: Any) -> bytes | None:
    base = session_key(settings)                  # derived from the session secret; a separate purpose string keeps a state from being usable elsewhere
    return hmac.new(base.encode(), b"gmail-oauth-state-v1", hashlib.sha256).digest() if base else None


def make_state(settings: Any, user_id: str, redirect_uri: str, *, now: float | None = None) -> str:
    """A signed token naming the person and the exact redirect address, valid for 10 minutes."""
    key = _state_key(settings)
    if not key:
        raise HTTPException(409, MSG_SESSIONS)
    payload = {"p": "gmail", "u": user_id, "r": redirect_uri, "x": int((now if now is not None else time.time()) + STATE_TTL_S), "n": secrets.token_urlsafe(6)}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_b64(hmac.new(key, body.encode(), hashlib.sha256).digest())}"


def read_state(settings: Any, state: str, *, now: float | None = None) -> dict[str, Any] | None:
    """The payload of a genuine, unexpired state; None for anything else (never raises)."""
    key = _state_key(settings)
    if not key or not isinstance(state, str) or state.count(".") != 1:
        return None
    body, sig = state.split(".")
    try:
        if not hmac.compare_digest(_unb64(sig), hmac.new(key, body.encode(), hashlib.sha256).digest()):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("p") != "gmail" or not isinstance(payload.get("u"), str) or not isinstance(payload.get("r"), str):
        return None
    if not isinstance(payload.get("x"), int) or payload["x"] < (now if now is not None else time.time()):
        return None
    return payload


# ---------------------------------------------------------------- the redirect address
def public_base(request: Request, settings: Any) -> str:
    """RI_PUBLIC_URL when set, else this request's scheme and host (honouring X-Forwarded-Proto / X-Forwarded-Host behind a proxy)."""
    configured = (getattr(settings, "public_url", None) or os.environ.get("RI_PUBLIC_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip().lower()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).split(",")[0].strip()
    if proto not in ("http", "https"):
        proto = request.url.scheme
    if not re.fullmatch(r"[A-Za-z0-9.\-]+(:\d{1,5})?|\[[0-9A-Fa-f:.]+\](:\d{1,5})?", host):
        host = request.url.netloc
    return f"{proto}://{host}"


def redirect_uri(request: Request, settings: Any) -> str:
    return public_base(request, settings) + CALLBACK_PATH


def _setup_problem(settings: Any) -> str | None:
    """A plain sentence when Gmail cannot be connected on this server yet, else None."""
    if not vault.available(settings.vault_key):
        return MSG_VAULT
    if not settings.google_client_id:
        return MSG_CLIENT
    if not settings.google_client_secret:
        return MSG_SECRET
    if not session_key(settings):
        return MSG_SESSIONS
    return None


def _back(error: str | None = None) -> RedirectResponse:
    return RedirectResponse("/connections?" + (urlencode({"error": error}) if error else "connected=gmail"), status_code=302)


def register_gmail(app, settings) -> None:
    @app.post("/api/me/connections/gmail/start")
    def gmail_start(request: Request, p: Principal = Depends(principal_dep)):
        problem = _setup_problem(settings)
        if problem:
            raise HTTPException(409, problem)
        uri = redirect_uri(request, settings)
        return {"url": gmail_api.consent_url(settings.google_client_id, uri, make_state(settings, p.user_id, uri), login_hint=p.email)}

    @app.get(CALLBACK_PATH, include_in_schema=False)
    def gmail_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        claims = read_state(settings, state[:2000])
        if not claims:
            return _back(MSG_LINK)
        uid = claims["u"]
        with request.app.state.db.tx() as conn:
            user = q1(conn, "SELECT email, active FROM users WHERE id::text=%s", (uid,))
        if not user or not user["active"]:
            return _back(MSG_NO_USER)
        if error:                                                   # Google sends ?error=access_denied when the person pressed "Cancel" or "Deny"
            return _back(MSG_DENIED if error == "access_denied" else MSG_GOOGLE_STOPPED)
        problem = _setup_problem(settings)
        if problem:
            return _back(problem)                                   # checked before the code is used, so it is not wasted
        if not code:
            return _back(MSG_GOOGLE_STOPPED)
        try:
            tokens = gmail_api.exchange_code(code[:2048], client_id=settings.google_client_id, client_secret=settings.google_client_secret, redirect_uri=claims["r"])
        except gmail_api.GmailUnavailable:
            return _back(MSG_UNREACHABLE)
        except gmail_api.GmailError as exc:
            log.warning("gmail connect: Google refused the code (%s)", exc.reason or "no reason given")
            return _back(MSG_SETTINGS if exc.reason in ("invalid_client", "unauthorized_client", "redirect_uri_mismatch") else MSG_REFUSED)
        granted = str(tokens.get("scope") or "")
        if gmail_api.GMAIL_SCOPE not in granted.split():
            return _back(MSG_NO_SCOPE)
        refresh = tokens.get("refresh_token")
        if not isinstance(refresh, str) or not refresh:
            return _back(MSG_NO_REFRESH)
        address = gmail_api.account_address(tokens, settings.google_client_id)
        if not address:
            return _back(MSG_NO_ADDRESS)
        try:
            with request.app.state.db.tx() as conn:
                store.connect(conn, settings.vault_key, uid, "gmail", secret=refresh, label=address, scopes=" ".join(granted.split())[:300])
                audit(conn, actor=user["email"], operation="connection_connected", record_type="user_connection", record_id=f"{uid}:gmail",
                      detail={"provider": "gmail", "account": address})
                gmail_sync.queue_sync(conn, uid)
        except vault.VaultError:
            return _back(MSG_VAULT)
        return _back()
