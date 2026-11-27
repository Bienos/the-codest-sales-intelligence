"""Each person's own connections (Gmail, Lemlist, Bluedot): connect, look, disconnect, share settings, "sync now", and Bluedot's webhook.

A person only ever reaches THEIR OWN rows: the user id always comes from the signed-in principal, never from the path or the body. Credentials go
through `ri.connections.store` and `ri.vault` only (sealed at rest, never returned, never logged, never written to the audit trail).
Gmail's own sign-in (start and callback) is in connections_gmail.py."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import Body, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from .. import vault
from ..access import Principal
from ..audit import audit
from ..config import Settings
from ..connections import bluedot, lemlist_sync, store
from ..db import q1
from ..jobs.queue import enqueue
from ..outbound.lemlist import LemlistError
from .auth import principal_dep

VIEW_FIELDS = ("provider", "status", "account_label", "connected_at", "last_sync_at", "last_error", "settings", "has_webhook", "stored")
NAMES = {"gmail": "Gmail", "lemlist": "Lemlist", "bluedot": "Bluedot"}
IN_FLIGHT = ("scheduled", "retryable_failed", "claimed", "fetching", "parsing", "verifying", "committing")
MAX_WEBHOOK_BYTES = 1_000_000
NO_VAULT = "Personal connections are not switched on yet: the server has no key to keep credentials safe (an administrator sets RI_VAULT_KEY)."


class DisconnectIn(BaseModel):
    delete_data: bool = False


class SettingsIn(BaseModel):
    share_email_gist: bool | None = None


def _view(item: dict[str, Any]) -> dict[str, Any]:
    """The connection as the page sees it: only the contract's fields, none of them a secret."""
    return {k: item.get(k) for k in VIEW_FIELDS}


def public_base_url(request: Request) -> str:
    """RI_PUBLIC_URL when set, else the scheme and host the request came in on (behind a proxy: X-Forwarded-Proto and X-Forwarded-Host)."""
    configured = (os.environ.get("RI_PUBLIC_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    scheme = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip().lower()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).split(",")[0].strip()
    if scheme not in ("http", "https") or not re.fullmatch(r"[A-Za-z0-9.\-]+(:\d{1,5})?|\[[0-9A-Fa-f:]+\](:\d{1,5})?", host):
        scheme, host = "https", request.url.netloc
    return f"{scheme}://{host}"


def _provider(provider: str) -> str:
    if provider not in store.PROVIDERS:
        raise HTTPException(404, "not found")
    return provider


def _vault_ready(settings: Settings) -> bool:
    return vault.available(settings.vault_key)


def _queue_sync(conn, user_id: str, provider: str, *, manual: bool) -> bool:
    """One sync per person and provider at a time; a repeated press within the minute is the same job."""
    kind = store.SYNC_JOB[provider]
    if q1(conn, "SELECT 1 FROM jobs WHERE kind=%s AND payload->>'user_id'=%s AND status = ANY(%s) LIMIT 1", (kind, user_id, list(IN_FLIGHT))):
        return False
    now = datetime.now(timezone.utc)
    _, created = enqueue(conn, kind, {"user_id": user_id}, idempotency_key=f"usersync:{provider}:{user_id}:{'manual' if manual else 'first'}:{now:%Y%m%d%H%M}",
                         max_attempts=2, correlation_id="manual-connections")
    return created


def _revoke_gmail(settings: Settings, token: str | None) -> None:
    """Best effort: ask Google to cancel the refresh token. The module is the Gmail helper's; without it (or if Google cannot be reached) we only forget the token."""
    if not token:
        return
    try:
        from ..connections.gmail_api import revoke_token
    except ImportError:
        return
    try:
        revoke_token(token)
    except Exception:  # noqa: BLE001 - never block a disconnect, never put the token or Google's answer anywhere
        pass


def register_connections(app: FastAPI, settings: Settings) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    @app.get("/api/me/connections")
    def my_connections(request: Request, p: Principal = Depends(principal_dep)):
        with tx(request) as conn:
            items = [_view(i) for i in store.list_public(conn, p.user_id)]
        ready = _vault_ready(settings)
        return {"vault_ready": ready, "gmail_ready": bool(ready and settings.google_client_id and settings.google_client_secret), "items": items}

    # ------------------------------------------------------------------ Lemlist
    @app.post("/api/me/connections/lemlist")
    def connect_lemlist(request: Request, body: dict[str, Any] = Body(default_factory=dict), p: Principal = Depends(principal_dep)):
        if not _vault_ready(settings):
            raise HTTPException(409, NO_VAULT)
        key = body.get("api_key")
        key = key.strip() if isinstance(key, str) else ""
        if not (8 <= len(key) <= 300) or re.search(r"\s", key):
            raise HTTPException(400, "Paste your Lemlist API key (Lemlist: Settings, Integrations, API).")
        try:
            team = lemlist_sync.make_client(key).team()
        except LemlistError as exc:
            if exc.status in (401, 403):
                raise HTTPException(400, "Lemlist did not accept that API key. Check it and try again.") from None
            raise HTTPException(502, "Lemlist could not be reached just now. Try again in a minute.") from None
        except httpx.HTTPError:
            raise HTTPException(502, "Lemlist could not be reached just now. Try again in a minute.") from None
        label = (str(team.get("name") or team.get("_id") or "").strip() if isinstance(team, dict) else "") or "Lemlist"
        with tx(request) as conn:
            pub = store.connect(conn, settings.vault_key, p.user_id, "lemlist", secret=key, label=label[:120])
            audit(conn, actor=p.email, operation="connection_connected", record_type="user_connection", record_id=f"{p.user_id}:lemlist", detail={"provider": "lemlist", "label": label[:120]})
            _queue_sync(conn, p.user_id, "lemlist", manual=False)
            return _view(dict(pub, stored=_stored(conn, p.user_id, "lemlist")))

    # ------------------------------------------------------------------ Bluedot
    @app.post("/api/me/connections/bluedot/token")
    def bluedot_token(request: Request, p: Principal = Depends(principal_dep)):
        token = store.new_webhook_token()            # shown once: only its hash is stored. Creating a new one stops the old address working.
        with tx(request) as conn:
            existed = bool((store.get(conn, p.user_id, "bluedot") or {}).get("webhook_token_hash"))
            pub = store.connect(conn, settings.vault_key, p.user_id, "bluedot", webhook_token=token, label="Bluedot")
            audit(conn, actor=p.email, operation="connection_connected", record_type="user_connection", record_id=f"{p.user_id}:bluedot",
                  detail={"provider": "bluedot", "rotated": existed})
            return {"webhook_url": f"{public_base_url(request)}/webhooks/bluedot/{token}", "connection": _view(dict(pub, stored=_stored(conn, p.user_id, "bluedot")))}

    @app.post("/webhooks/bluedot/{token}")
    async def bluedot_webhook(token: str, request: Request):
        with tx(request) as conn:
            row = store.by_webhook_token(conn, token) if 8 <= len(token) <= 200 else None
        if not row:
            raise HTTPException(404, "not found")
        raw = await request.body()
        note: str | None = None
        if len(raw) > MAX_WEBHOOK_BYTES:                     # the middleware checks the declared size; this covers a body sent in chunks
            payload, note = {"_too_large": True, "bytes": len(raw)}, "the delivery was too large to read, so only its size was kept"
        else:
            try:
                payload = json.loads(raw.decode("utf-8")) if raw.strip() else {}
            except (ValueError, RecursionError):
                payload, note = {"_not_json": True, "text": raw[:2000].decode("utf-8", "replace")}, bluedot.NOTE_NOT_JSON
        with tx(request) as conn:
            # re-check inside the same transaction as the write: a token disconnected a moment ago must not store anything
            row = store.by_webhook_token(conn, token)
            if not row:
                raise HTTPException(404, "not found")
            if note:
                bluedot.store_raw(conn, str(row["user_id"]), payload, handled=False, note=note)
            else:
                bluedot.handle_delivery(conn, str(row["user_id"]), payload)
        return {"ok": True}

    @app.get("/api/me/connections/bluedot/events")
    def bluedot_events(request: Request, p: Principal = Depends(principal_dep)):
        with tx(request) as conn:
            return {"items": bluedot.recent_events(conn, p.user_id)}

    # ------------------------------------------------------------------ all providers
    @app.post("/api/me/connections/{provider}/disconnect")
    def disconnect(provider: str, request: Request, body: DisconnectIn = Body(default_factory=DisconnectIn), p: Principal = Depends(principal_dep)):
        provider = _provider(provider)
        token = None
        if provider == "gmail" and _vault_ready(settings):
            with tx(request) as conn:                           # read the token first: disconnecting forgets it
                try:
                    token = store.secret(conn, settings.vault_key, p.user_id, "gmail")
                except vault.VaultError:
                    token = None
            _revoke_gmail(settings, token)
            token = None
        with tx(request) as conn:
            deleted = store.disconnect(conn, p.user_id, provider, delete_data=body.delete_data)
            if provider == "bluedot" and body.delete_data:
                conn.execute("DELETE FROM connection_events WHERE user_id=%s AND provider='bluedot'", (p.user_id,))
            audit(conn, actor=p.email, operation="connection_disconnected", record_type="user_connection", record_id=f"{p.user_id}:{provider}",
                  detail={"provider": provider, "delete_data": body.delete_data, "deleted": deleted})
        return {"ok": True, "deleted": deleted}

    @app.patch("/api/me/connections/{provider}/settings")
    def change_settings(provider: str, body: SettingsIn, request: Request, p: Principal = Depends(principal_dep)):
        provider = _provider(provider)
        if provider != "gmail":
            raise HTTPException(400, f"{NAMES[provider]} has no settings to change.")
        if body.share_email_gist is None:
            raise HTTPException(400, "Nothing to change.")
        with tx(request) as conn:
            pub = store.update_settings(conn, p.user_id, "gmail", {"share_email_gist": body.share_email_gist})
            if not pub:
                raise HTTPException(404, "Gmail is not connected.")
            visibility = store.email_visibility(pub["settings"])
            rows = conn.execute("UPDATE contact_interactions SET visibility=%s WHERE user_id=%s AND channel='email' AND visibility<>%s",
                                (visibility, p.user_id, visibility)).rowcount       # the same switch covers the emails already stored
            audit(conn, actor=p.email, operation="email_gist_sharing_changed", record_type="user_connection", record_id=f"{p.user_id}:gmail",
                  detail={"share_email_gist": bool(body.share_email_gist), "visibility": visibility, "rows_changed": rows})
            return _view(dict(pub, stored=_stored(conn, p.user_id, "gmail")))

    @app.post("/api/me/connections/{provider}/sync")
    def sync_now(provider: str, request: Request, p: Principal = Depends(principal_dep)):
        provider = _provider(provider)
        if provider not in store.SYNC_JOB:
            raise HTTPException(400, f"{NAMES[provider]} sends its meetings by itself; there is nothing to sync.")
        if not _vault_ready(settings):
            raise HTTPException(409, NO_VAULT)
        with tx(request) as conn:
            row = store.get(conn, p.user_id, provider)
            if not row or row["status"] == "disconnected" or not row.get("secret_enc"):
                raise HTTPException(409, f"Connect {NAMES[provider]} first.")
            return {"queued": _queue_sync(conn, p.user_id, provider, manual=True)}


def _stored(conn, user_id: str, provider: str) -> int:
    r = q1(conn, "SELECT count(*) AS n FROM contact_interactions WHERE user_id=%s AND channel=%s", (user_id, store.CHANNEL[provider]))
    return int(r["n"]) if r else 0
