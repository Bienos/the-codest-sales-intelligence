"""One budget-gated model call: reserve tokens first, settle with the real count, release on failure, record usage. Tries the preferred model, then the fallback."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from .. import budget, usage
from ..config import LIST_PRICES, Settings
from ..db import Conn


def make_call(conn: Conn, st: Settings, *, org_id: str, operation: str, key_prefix: str, models: list[str], max_tokens: int = 4000) -> Callable[[str, str], Awaitable[tuple[str, int, int, str]]]:
    import httpx

    from ..sources import http as http_mod
    n = {"i": 0}

    async def call(system: str, user: str) -> tuple[str, int, int, str]:
        n["i"] += 1
        key = f"{key_prefix}:{n['i']}"
        est = float(len(system + user) // 3 + max_tokens + 1000)
        period = usage.period_now()
        budget.reserve(conn, "anthropic", "monthly", period, est, key=key, ref_type=operation, purpose=operation)
        conn.commit()
        hdrs = {"x-api-key": st.anthropic_api_key, "anthropic-version": "2023-06-01"}
        last: Exception | None = None
        for model in dict.fromkeys(models):
            try:
                async with httpx.AsyncClient(timeout=180) as c:
                    data: Any = await http_mod.request_json(c, "POST", "https://api.anthropic.com/v1/messages", headers=hdrs,
                                                            json={"model": model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]})
                text = "".join(x.get("text", "") for x in data.get("content", []) if x.get("type") == "text")
                if not text.strip():
                    raise ValueError("empty model output")
                u = data.get("usage") or {}
                tin, tout = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
                pin, pout = LIST_PRICES.get(model, (st.anthropic_price_in_per_mtok, st.anthropic_price_out_per_mtok))
                cost, basis = usage.model_cost(tin, tout, pin, pout)
                budget.settle(conn, "anthropic", "monthly", period, est, float(tin + tout), key=key)
                usage.record(conn, provider="anthropic", operation=operation, units=float(tin + tout), unit="tokens", tokens_in=tin, tokens_out=tout, cost_usd=cost, cost_basis=basis,
                             organization_id=org_id, detail={"model": model})
                conn.commit()
                return text, tin, tout, model
            except Exception as exc:  # noqa: BLE001 - try the fallback model once
                last = exc
        budget.release(conn, "anthropic", "monthly", period, est, key=key)
        conn.commit()
        raise last  # type: ignore[misc]
    return call
