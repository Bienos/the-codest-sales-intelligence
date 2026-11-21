"""Admin controls for page/feed sources: set the URL, test it from the server, activate with a certification record, pause.

Safety: HTTPS only; the host must be the source's own canonical domain (or a subdomain); IP literals and ports are refused, so this cannot be
used to make the server fetch arbitrary or internal addresses. robots.txt is enforced by the connectors. Activation requires a passing test.
"""
from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ..access import Principal
from ..audit import audit
from ..db import jb, q1
from ..sources import registry
from ..sources.webwatch import FeedConnector, PageWatchConnector
from .auth import require

VALIDATION_MAX_AGE = timedelta(hours=24)


class ConfigIn(BaseModel):
    url: str = Field(min_length=12, max_length=500)
    selector: str | None = Field(default=None, max_length=80)


class ActivateIn(BaseModel):
    licence_note: str = Field(min_length=10, max_length=500)
    confirm_permitted: bool


class BulkIn(BaseModel):
    activate: bool = False


class PauseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


def check_url(url: str, canonical_domain: str | None) -> str:
    u = urlparse(url.strip())
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host:
        raise HTTPException(400, "URL must start with https://")
    if u.username or u.password or u.port not in (None, 443):
        raise HTTPException(400, "URL must not contain credentials or a custom port")
    try:
        ipaddress.ip_address(host)
        raise HTTPException(400, "IP addresses are not allowed")
    except ValueError:
        pass
    if not canonical_domain:
        raise HTTPException(400, "this source has no registered domain; it cannot take a page URL")
    cd = canonical_domain.lower()
    if not (host == cd or host.endswith("." + cd)):
        raise HTTPException(400, f"URL must be on {cd} (or a subdomain)")
    return url.strip()


def register(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    def load(conn, sid: str) -> dict[str, Any]:
        s = q1(conn, "SELECT * FROM sources WHERE source_id=%s", (sid,))
        if not s:
            raise HTTPException(404, "source not found")
        if s["collection_method"] not in {"feed", "change_monitor"}:
            raise HTTPException(400, "only feed and page-monitor sources are configured here")
        return s

    @app.put("/api/admin/sources/{sid}/config")
    def set_config(sid: str, body: ConfigIn, request: Request, p: Principal = Depends(require("admin.sources"))):
        with tx(request) as conn:
            s = load(conn, sid)
            url = check_url(body.url, s["canonical_domain"])
            cfg = {k: v for k, v in (s["config"] or {}).items() if k not in {"validation"}}
            cfg.update({"url": url, "url_basis": f"set by {p.email}"})
            if body.selector:
                cfg["selector"] = body.selector
            conn.execute("UPDATE sources SET config=%s, status=CASE WHEN status='active' THEN 'paused' ELSE status END WHERE source_id=%s", (jb(cfg), sid))
            audit(conn, actor=p.email, operation="source_set_url", record_type="source", record_id=sid, after={"url": url})
            return {"source_id": sid, "url": url, "needs_validation": True}

    @app.post("/api/admin/sources/{sid}/validate")
    async def validate(sid: str, request: Request, p: Principal = Depends(require("admin.sources"))):
        with tx(request) as conn:
            s = load(conn, sid)
        url = (s["config"] or {}).get("url")
        if not url:
            raise HTTPException(400, "set a URL first")
        check_url(url, s["canonical_domain"])
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            conn_cls = FeedConnector if s["collection_method"] == "feed" else PageWatchConnector
            kw = {} if s["collection_method"] == "feed" else {"selector": (s["config"] or {}).get("selector")}
            try:
                hc = await conn_cls(client, url, source_id=sid, **kw).health_check()
                result = {"ok": hc.ok, "detail": hc.detail, "error_class": hc.error_class}
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:200]}", "error_class": getattr(exc, "error_class", "transient_network")}
        result["at"] = datetime.now(timezone.utc).isoformat()
        with tx(request) as conn:
            conn.execute("UPDATE sources SET config = config || %s::jsonb WHERE source_id=%s", (jb({"validation": result}), sid))
            audit(conn, actor=p.email, operation="source_validate", record_type="source", record_id=sid, detail=result)
        return result

    @app.post("/api/admin/sources/{sid}/activate")
    def activate(sid: str, body: ActivateIn, request: Request, p: Principal = Depends(require("admin.sources"))):
        if not body.confirm_permitted:
            raise HTTPException(400, "confirm that monitoring this page is permitted")
        with tx(request) as conn:
            s = load(conn, sid)
            cfg = s["config"] or {}
            v = cfg.get("validation") or {}
            try:
                fresh = v.get("ok") and datetime.now(timezone.utc) - datetime.fromisoformat(v["at"]) < VALIDATION_MAX_AGE
            except (KeyError, ValueError):
                fresh = False
            if not fresh:
                raise HTTPException(400, "run a passing validation within the last 24 hours first")
            if s["auth_mode"] != "none":
                raise HTTPException(400, "this source needs credentials and cannot be activated from here")
            cert = {"provider_doc_url": cfg["url"], "auth_entitlement_verified": True, "verified_endpoints": [cfg["url"]],
                    "data_licence": body.licence_note, "technical_owner": p.email, "last_tested_date": v["at"][:10],
                    "limits": f"polls every {s['poll_interval_s']}s; robots.txt honoured; no login or paywall bypass",
                    "rollback_path": "Pause the source in Administration, Sources; collected documents stay as evidence."}
            try:
                registry.activate(conn, sid, cert, p.email)
            except registry.CertificationError as exc:
                raise HTTPException(400, str(exc)) from exc
            return {"source_id": sid, "status": "active"}

    @app.post("/api/admin/sources/validate-all")
    def validate_all(body: BulkIn, request: Request, p: Principal = Depends(require("admin.sources"))):
        from datetime import datetime as _dt
        from ..jobs.queue import enqueue
        with tx(request) as conn:
            jid, created = enqueue(conn, "sources_validate_all", {"activate": body.activate, "by": p.email}, idempotency_key=f"validate-all:{p.user_id}:{_dt.now(timezone.utc).strftime('%Y%m%d%H%M')}", max_attempts=1)
            audit(conn, actor=p.email, operation="sources_validate_all_requested", record_type="job", record_id=jid, detail={"activate": body.activate})
            return {"job_id": jid, "queued": created}

    @app.post("/api/admin/sources/{sid}/pause")
    def pause(sid: str, body: PauseIn, request: Request, p: Principal = Depends(require("admin.sources"))):
        with tx(request) as conn:
            load(conn, sid)
            registry.pause(conn, sid, p.email, body.reason)
            return {"source_id": sid, "status": "paused"}
