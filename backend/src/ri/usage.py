"""Provider usage tracker: one row per metered call (spec 14 §8 'usage traceable to a source/query/initiative/action').
Costs are never invented: Exa reports its own cost per call; for models, dollars appear only when list prices are configured."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .db import Conn, jb, q1


def period_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def record(conn: Conn, *, provider: str, operation: str, units: float = 0, unit: str = "units", tokens_in: int | None = None,
           tokens_out: int | None = None, cost_usd: float | None = None, cost_basis: str = "unmetered", source_id: str | None = None,
           plan_id: str | None = None, query: str | None = None, organization_id: str | None = None, hypothesis_id: str | None = None,
           document_id: str | None = None, request_id: str | None = None, status: str = "ok", detail: dict[str, Any] | None = None) -> None:
    if document_id and not (source_id and organization_id):
        d = q1(conn, "SELECT source_id FROM source_documents WHERE id=%s", (document_id,))
        source_id = source_id or (d["source_id"] if d else None)
    conn.execute(
        "INSERT INTO provider_usage(period, provider, operation, units, unit, tokens_in, tokens_out, cost_usd, cost_basis, source_id, plan_id, query, "
        "organization_id, hypothesis_id, document_id, request_id, status, detail) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (period_now(), provider, operation, units, unit, tokens_in, tokens_out, cost_usd, cost_basis, source_id, plan_id, query,
         organization_id, hypothesis_id, document_id, request_id, status, jb(detail or {})))


def model_cost(tokens_in: int, tokens_out: int, price_in_per_mtok: float | None, price_out_per_mtok: float | None) -> tuple[float | None, str]:
    """Dollar estimate only when BOTH list prices are configured; otherwise unknown (tokens remain the authoritative unit)."""
    if price_in_per_mtok is None or price_out_per_mtok is None:
        return None, "unmetered"
    return (tokens_in * price_in_per_mtok + tokens_out * price_out_per_mtok) / 1_000_000, "estimated"
