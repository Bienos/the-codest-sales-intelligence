"""Budget-gated model extraction (spec 14 §5/§8): reserve tokens BEFORE the call, settle with actual usage, release on failure.
Cap defaults to 0 => the model cannot run until someone sets RI_ANTHROPIC_MONTHLY_TOKEN_CAP. Units are TOKENS, so no provider price is assumed."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .. import budget
from ..db import Conn


class BudgetedExtractor:
    provider, kind = "anthropic", "monthly"

    def __init__(self, inner: Any, conn: Conn):
        self.inner, self.conn = inner, conn
        self.version = inner.version

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
        budget.settle(self.conn, self.provider, self.kind, self.period(), est, float(getattr(self.inner, "last_usage_tokens", 0) or est), key=key)
        return out
