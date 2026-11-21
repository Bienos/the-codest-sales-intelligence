"""Budget-gated model extraction (spec 14 §5/§8): reserve tokens BEFORE the call, settle with actual usage, release on failure.
Cap defaults to 0 => the model cannot run until someone sets RI_ANTHROPIC_MONTHLY_TOKEN_CAP. Units are TOKENS, so no provider price is assumed."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .. import budget, usage
from ..db import Conn


class BudgetedExtractor:
    provider, kind = "anthropic", "monthly"

    def __init__(self, inner: Any, conn: Conn, *, price_in: float | None = None, price_out: float | None = None):
        self.inner, self.conn = inner, conn
        self.version = inner.version
        self.price_in, self.price_out = price_in, price_out
        self.ctx: dict[str, Any] = {}       # who this call is for (document, verification request, organisation): set by the caller

    @staticmethod
    def period() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    async def extract(self, *, title, text, published_at):
        est = float(min(len(text), 30000) // 3 + getattr(self.inner, "max_tokens_budget", 4000) + 1500)      # input chars/3 + max output + system prompt
        key = f"ai:{uuid.uuid4()}"
        budget.reserve(self.conn, self.provider, self.kind, self.period(), est, key=key, ref_type="document", purpose="event extraction")   # raises BudgetExceeded
        try:
            out = await self.inner.extract(title=title, text=text, published_at=published_at)
        except Exception:
            budget.release(self.conn, self.provider, self.kind, self.period(), est, key=key)
            raise
        actual = float(getattr(self.inner, "last_usage_tokens", 0) or est)
        budget.settle(self.conn, self.provider, self.kind, self.period(), est, actual, key=key)
        tin, tout = int(getattr(self.inner, "last_tokens_in", 0)), int(getattr(self.inner, "last_tokens_out", 0))
        cost, basis = usage.model_cost(tin, tout, self.price_in, self.price_out)
        usage.record(self.conn, provider="anthropic", operation="extract", units=actual, unit="tokens", tokens_in=tin or None, tokens_out=tout or None,
                     cost_usd=cost, cost_basis=basis, detail={"model": getattr(self.inner, "model", None), "estimated_reservation": est, "token_count_reported": bool(tin or tout)}, **self.ctx)
        return out
