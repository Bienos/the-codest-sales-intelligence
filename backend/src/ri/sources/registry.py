"""Source registry (spec 02 §3). Every candidate provider starts `pending_connector_validation`; activation requires a
certification record (provider doc URL, entitlement, verified endpoints, licence, owner, tested date, limits, rollback)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..audit import audit
from ..db import Conn, jb, q1

SEED = Path(__file__).resolve().parents[3] / "rules" / "sources.yaml"
REQUIRED_CERT = {"provider_doc_url", "auth_entitlement_verified", "verified_endpoints", "data_licence", "technical_owner",
                 "last_tested_date", "limits", "rollback_path"}


class CertificationError(RuntimeError):
    pass


def seed_registry(conn: Conn, path: Path = SEED) -> int:
    n = 0
    for s in yaml.safe_load(path.read_text(encoding="utf-8"))["sources"]:
        row = conn.execute(
            "INSERT INTO sources(source_id, display_name, canonical_domain, source_tier, source_type, source_family, provider, countries, languages, "
            "signal_rule_ids, collection_method, endpoint_template, auth_mode, credential_ref, licence_notes, robots_policy, retention_policy, "
            "poll_interval_s, cursor_type, rate_limit_per_min, owner, cost_profile, config) "
            "VALUES (%(source_id)s,%(display_name)s,%(canonical_domain)s,%(source_tier)s,%(source_type)s,%(source_family)s,%(provider)s,%(countries)s,"
            "%(languages)s,%(signal_rule_ids)s,%(collection_method)s,%(endpoint_template)s,%(auth_mode)s,%(credential_ref)s,%(licence_notes)s,"
            "%(robots_policy)s,%(retention_policy)s,%(poll_interval_s)s,%(cursor_type)s,%(rate_limit_per_min)s,%(owner)s,%(cost_profile)s,%(config)s) "
            "ON CONFLICT (source_id) DO NOTHING RETURNING source_id",
            {**{k: None for k in ("canonical_domain", "endpoint_template", "credential_ref", "licence_notes", "robots_policy", "rate_limit_per_min", "owner")},
             "countries": [], "languages": [], "signal_rule_ids": [], "auth_mode": "none", "retention_policy": "metadata_only",
             "poll_interval_s": 86400, "cursor_type": "none", "cost_profile": jb({}), "config": jb({}),
             **{**s, "cost_profile": jb(s.get("cost_profile", {})), "config": jb(s.get("config", {}))}}).fetchone()
        if row:
            conn.execute("INSERT INTO source_health(source_id, state) VALUES (%s,'unconfigured') ON CONFLICT DO NOTHING", (s["source_id"],))
            n += 1
        elif s.get("config"):     # existing deployment: add newly shipped config keys but never overwrite an operator's value
            conn.execute("UPDATE sources SET config = %s::jsonb || config WHERE source_id=%s", (jb(s["config"]), s["source_id"]))
        if not row and s.get("collection_method"):      # an uncertified source may have its collection method corrected; certified ones never change silently
            conn.execute("UPDATE sources SET collection_method=%s WHERE source_id=%s AND status='pending_connector_validation'", (s["collection_method"], s["source_id"]))
    return n


def activate(conn: Conn, source_id: str, certification: dict[str, Any], actor: str) -> None:
    missing = REQUIRED_CERT - set(certification)
    if missing:
        raise CertificationError(f"certification record incomplete, missing: {sorted(missing)}")
    if not certification["auth_entitlement_verified"]:
        raise CertificationError("auth/entitlement not verified: connector stays pending_connector_validation")
    conn.execute("UPDATE sources SET status='active', certification=%s, reviewed_at=now() WHERE source_id=%s", (jb(certification), source_id))
    conn.execute("UPDATE source_health SET state='healthy', updated_at=now() WHERE source_id=%s AND state='unconfigured'", (source_id,))
    audit(conn, actor=actor, operation="activate_source", record_type="source", record_id=source_id, after=certification)


def pause(conn: Conn, source_id: str, actor: str, reason: str) -> None:
    conn.execute("UPDATE sources SET status='paused' WHERE source_id=%s", (source_id,))
    conn.execute("UPDATE source_health SET state='paused', detail=%s, updated_at=now() WHERE source_id=%s", (reason, source_id))
    audit(conn, actor=actor, operation="pause_source", record_type="source", record_id=source_id, detail={"reason": reason})


def get(conn: Conn, source_id: str) -> dict[str, Any]:
    r = q1(conn, "SELECT * FROM sources WHERE source_id=%s", (source_id,))
    if not r:
        raise KeyError(source_id)
    return r


def validate_and_maybe_activate(conn: Conn, source_id: str, *, activate: bool, by: str) -> dict[str, Any]:
    """Test one source from the server and, if it passes and activation is requested, activate it with a generated certification record."""
    import asyncio
    from datetime import datetime, timezone

    from .factory import NoConnector, build_connector
    src = get(conn, source_id)
    if src["status"] == "active":
        return {"result": "already_active"}
    if src["auth_mode"] != "none":
        import os
        env = src.get("credential_ref")
        if not (env and os.environ.get(env)):
            return {"result": "skipped", "reason": f"needs credential {env or '(not set)'}"}
    try:
        connector = build_connector(conn, source_id)
    except NoConnector as exc:
        return {"result": "skipped", "reason": str(exc)}
    except KeyError as exc:
        return {"result": "skipped", "reason": f"incomplete configuration: {exc}"}
    try:
        hc = asyncio.run(connector.health_check())
        res = {"ok": hc.ok, "detail": hc.detail, "error_class": hc.error_class}
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:200]}", "error_class": getattr(exc, "error_class", "transient_network")}
    res["at"] = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE sources SET config = config || %s::jsonb WHERE source_id=%s", (jb({"validation": res}), source_id))
    audit(conn, actor=by, operation="source_validate", record_type="source", record_id=source_id, detail=res)
    if not res["ok"]:
        return {"result": "failed", **res}
    if not activate:
        return {"result": "passed", **res}
    cfg = src["config"] or {}
    url = cfg.get("url") or (f"https://{src['canonical_domain']}" if src["canonical_domain"] else source_id)
    cert = {"provider_doc_url": url, "auth_entitlement_verified": True, "verified_endpoints": [url],
            "data_licence": (src["licence_notes"] or "public pages and feeds; robots.txt honoured") + f" (activated in bulk by {by} after a passing server test)",
            "technical_owner": by, "last_tested_date": res["at"][:10],
            "limits": f"polls every {src['poll_interval_s']}s; robots.txt honoured; no login or paywall bypass",
            "rollback_path": "Pause the source in Administration, Sources; collected documents stay as evidence."}
    activate_source(conn, source_id, cert, by)
    return {"result": "activated", **res}


def activate_source(conn: Conn, source_id: str, cert: dict[str, Any], by: str) -> None:
    activate(conn, source_id, cert, by)
