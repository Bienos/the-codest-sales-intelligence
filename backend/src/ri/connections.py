"""Directory of connections: one row per registered source with a plain state, what is needed, and where to get it."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .db import Conn, qa

PATH = Path(__file__).resolve().parents[2] / "rules" / "connections.yaml"
LABELS = {"live": "Live", "built_in": "Built in", "per_account": "Runs per account", "needs_address": "Needs an address", "blocked_by_site": "Blocked by the site",
          "needs_credential": "Needs a key or contract", "not_built": "Not built", "deferred": "Switched off until you approve", "needs_retest": "Needs a retest", "testing": "Not yet tested"}

DEFAULT_TODO = {"live": "Nothing to do. It is being read on its schedule.", "testing": "Not tested yet. Press Test all sources in Administration, Sources: if the server can read it, it switches on.",
                "needs_address": "Needs a page address before it can be read.", "needs_retest": "Failed its last test or is degraded. Run Test all sources again."}


@lru_cache(maxsize=1)
def _notes() -> dict[str, dict[str, Any]]:
    return (yaml.safe_load(PATH.read_text(encoding="utf-8")) or {}).get("connections", {})


def classify(src: dict[str, Any], health: dict[str, Any] | None) -> str:
    note = _notes().get(src["source_id"], {})
    if src["status"] in ("active", "degraded"):
        return "live" if src["status"] == "active" else "needs_retest"
    if note.get("state"):
        return note["state"]
    cfg = src.get("config") or {}
    if src["collection_method"] in ("feed", "change_monitor") and not cfg.get("url"):
        return "needs_address"
    if src.get("auth_mode") == "api_key":
        return "needs_credential"
    return "testing"


def monitoring_mode(src: dict[str, Any]) -> str:
    note = _notes().get(src["source_id"], {})
    if note.get("state") == "per_account":
        return "account"
    if src["source_family"] == "registry":
        return "identity"
    if src["collection_method"] in ("feed", "change_monitor", "api", "official_api") and src["poll_interval_s"]:
        return "scheduled"
    return "on_demand"


def directory(conn: Conn) -> list[dict[str, Any]]:
    docs = {r["source_id"]: r for r in qa(conn, "SELECT source_id, count(*) AS docs, max(fetched_at) AS last_doc FROM source_documents GROUP BY source_id")}
    health = {r["source_id"]: r for r in qa(conn, "SELECT source_id, state, last_success_at, last_attempt_at, error_class, detail FROM source_health")}
    out = []
    for s in qa(conn, "SELECT source_id, display_name, source_family, source_type, source_tier, countries, collection_method, status, auth_mode, config, endpoint_template, canonical_domain FROM sources ORDER BY source_family, display_name"):
        h = health.get(s["source_id"])
        state = classify(s, h)
        note = _notes().get(s["source_id"], {})
        cfg = s.get("config") or {}
        out.append({"source_id": s["source_id"], "name": s["display_name"], "family": s["source_family"], "type": s["source_type"], "tier": s["source_tier"], "countries": s["countries"] or [],
                    "method": s["collection_method"], "state": state, "state_label": LABELS[state], "address": cfg.get("url") or s["endpoint_template"],
                    "what_to_do": note.get("todo") or DEFAULT_TODO.get(state) or (h or {}).get("detail"), "where_to_get": note.get("where"),
                    "documents": (docs.get(s["source_id"]) or {}).get("docs", 0), "last_document_at": (docs.get(s["source_id"]) or {}).get("last_doc"),
                    "last_success_at": (h or {}).get("last_success_at"), "last_error": (h or {}).get("detail") if state != "live" else None})
    return out
