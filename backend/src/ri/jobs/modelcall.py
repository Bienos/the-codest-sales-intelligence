"""One budget-gated model call: reserve tokens first, settle with the real count, release on failure, record usage. Tries the preferred model, then the fallback.

With `schema`, the answer is constrained to that JSON schema (structured output), so it always parses: the long research answers copy
excerpts from web pages full of quotes and accents, and free-written JSON broke on them. A model that refuses the schema (HTTP 400) is asked
once more without it, and the caller's tolerant parser takes over."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from .. import budget, usage
from ..config import LIST_PRICES, Settings
from ..db import Conn


def make_call(conn: Conn, st: Settings, *, org_id: str | None, operation: str, key_prefix: str, models: list[str], max_tokens: int = 4000,
              schema: dict[str, Any] | None = None) -> Callable[[str, str], Awaitable[tuple[str, int, int, str]]]:
    import httpx

    from ..sources import http as http_mod
    from ..sources.base import SchemaDrift
    n = {"i": 0}

    async def call(system: str, user: str) -> tuple[str, int, int, str]:
        n["i"] += 1
        key = f"{key_prefix}:{n['i']}"
        est = float(len(system + user) // 3 + max_tokens + 1000)
        period = usage.period_now()
        budget.reserve(conn, "anthropic", "monthly", period, est, key=key, ref_type=operation, purpose=operation)
        conn.commit()
        hdrs = {"x-api-key": st.anthropic_api_key, "anthropic-version": "2023-06-01"}

        async def post(c: Any, model: str, structured: bool) -> Any:
            body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]}
            if structured:
                body["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
            return await http_mod.request_json(c, "POST", "https://api.anthropic.com/v1/messages", headers=hdrs, json=body)

        last: Exception | None = None
        for model in dict.fromkeys(models):
            try:
                async with httpx.AsyncClient(timeout=240) as c:
                    try:
                        data: Any = await post(c, model, schema is not None)
                    except SchemaDrift as exc:
                        if schema is None or exc.status != 400:
                            raise
                        data = await post(c, model, False)          # this model would not take the schema: a plain answer, parsed tolerantly
                stop = data.get("stop_reason")
                text = "".join(x.get("text", "") for x in data.get("content", []) if x.get("type") == "text")
                if stop == "refusal":
                    raise ValueError("the model declined to answer")
                if stop == "max_tokens":
                    raise ValueError(f"the answer was cut off at the length limit ({max_tokens} tokens)")
                if not text.strip():
                    raise ValueError(f"empty model output (stop_reason: {stop})")
                u = data.get("usage") or {}
                tin, tout = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
                pin, pout = LIST_PRICES.get(model, (st.anthropic_price_in_per_mtok, st.anthropic_price_out_per_mtok))
                cost, basis = usage.model_cost(tin, tout, pin, pout)
                budget.settle(conn, "anthropic", "monthly", period, est, float(tin + tout), key=key)
                usage.record(conn, provider="anthropic", operation=operation, units=float(tin + tout), unit="tokens", tokens_in=tin, tokens_out=tout, cost_usd=cost, cost_basis=basis,
                             organization_id=org_id, detail={"model": model, "structured": schema is not None})
                conn.commit()
                return text, tin, tout, model
            except Exception as exc:  # noqa: BLE001 - try the fallback model once
                last = exc
        budget.release(conn, "anthropic", "monthly", period, est, key=key)
        conn.commit()
        raise last  # type: ignore[misc]
    return call
