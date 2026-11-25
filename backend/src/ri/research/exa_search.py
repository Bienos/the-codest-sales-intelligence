"""Budget-gated Exa searches for on-demand research (a company profile, a person profile, finding decision makers).

Every search reserves money from the monthly Exa cap BEFORE it is sent, settles with the cost Exa reports in its own answer
(`costDollars.total`), and is released when the call fails. When the cap is used up `budget.reserve` raises BudgetExceeded and the
caller stops cleanly with what it already has. Search results are untrusted text: they are only ever passed to the model as data.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from .. import budget, usage
from ..db import Conn
from ..sources.base import ConnectorError

EXA_SEARCH = "https://api.exa.ai/search"
EST_COST = 0.02          # reservation per search with page text (Exa bills about $0.005 per search plus $0.001 per page of text); settled to the reported cost
Post = Callable[[dict[str, Any]], Awaitable[Any]]

STOP_REASON = {
    "auth": "Exa refused the API key. Check EXA_API_KEY.",
    "access_denied": "Exa refused the request. Check EXA_API_KEY and the network.",
    "provider_billing": "The Exa account is out of credit.",
    "rate_limited": "Exa is rate limiting us. Try again in a few minutes.",
    "schema_drift": "Exa did not accept the request.",
}


class ExaStopped(Exception):
    """Exa refused or failed: research stops with a plain reason (no retry loop inside a button press)."""

    def __init__(self, msg: str, error_class: str = "transient_network"):
        super().__init__(msg)
        self.error_class = error_class


def exa_post(api_key: str) -> Post:
    import httpx

    from ..sources.http import request_json

    async def post(body: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=60) as c:       # one client per call: an AsyncClient is bound to the loop that first used it
            return await request_json(c, "POST", EXA_SEARCH, json=body, headers={"x-api-key": api_key}, retries=1)
    return post


def since(days: int, now: datetime | None = None) -> str:
    return ((now or datetime.now(timezone.utc)) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000Z")


def text_contents(max_chars: int) -> dict[str, Any]:
    return {"text": {"maxCharacters": max_chars}}


def _clean(s: Any, cap: int) -> str:
    return re.sub(r"[ \t]+", " ", str(s or "")).strip()[:cap]


def normalise_result(r: dict[str, Any], purpose: str, text_cap: int = 2500) -> dict[str, Any] | None:
    url = r.get("url")
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        return None
    pub = r.get("publishedDate")
    return {"url": url, "title": _clean(r.get("title"), 300) or None, "published": pub[:10] if isinstance(pub, str) and re.match(r"\d{4}-\d{2}-\d{2}", pub) else None,
            "author": _clean(r.get("author"), 120) or None, "text": _clean(r.get("text") or " ".join(r.get("highlights") or []), text_cap), "purpose": purpose}


class ExaSearcher:
    def __init__(self, conn: Conn, *, post: Post, operation: str, ref_type: str, ref_id: str, org_id: str | None = None,
                 est_cost: float = EST_COST, log_query: bool = True, run: Callable[[Any], Any] = asyncio.run):
        self.conn, self.post, self.operation, self.ref_type, self.ref_id, self.org_id = conn, post, operation, ref_type, ref_id, org_id
        self.est, self.log_query, self.run = est_cost, log_query, run
        self.calls, self.cost = 0, 0.0
        self.log: list[dict[str, Any]] = []

    def search(self, purpose: str, body: dict[str, Any], *, text_cap: int = 2500) -> list[dict[str, Any]]:
        """Raises BudgetExceeded before anything is sent when the cap is used up; ExaStopped when Exa refuses."""
        period = usage.period_now()
        key = f"exa:{self.ref_type}:{self.ref_id}:{purpose}:{uuid.uuid4()}"
        budget.reserve(self.conn, "exa", "monthly", period, self.est, key=key, ref_type=self.ref_type, ref_id=self.ref_id, purpose=f"{self.operation} {purpose}")
        self.conn.commit()
        query = body.get("query") if self.log_query else None
        try:
            data = self.run(self.post(body))
        except ConnectorError as exc:
            budget.release(self.conn, "exa", "monthly", period, self.est, key=key)
            usage.record(self.conn, provider="exa", operation=self.operation, units=0, unit="searches", query=query, organization_id=self.org_id,
                         status=f"error:{exc.error_class}", detail={"purpose": purpose})
            self.conn.commit()
            self.log.append({"purpose": purpose, "results": 0, "error": exc.error_class})
            raise ExaStopped(STOP_REASON.get(exc.error_class, f"Exa did not answer ({exc.error_class})."), exc.error_class) from exc
        except Exception:
            budget.release(self.conn, "exa", "monthly", period, self.est, key=key)
            self.conn.commit()
            raise
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            budget.release(self.conn, "exa", "monthly", period, self.est, key=key)
            self.conn.commit()
            raise ExaStopped("Exa answered in a format we do not recognise.", "schema_drift")
        cd = data.get("costDollars")
        reported = cd.get("total") if isinstance(cd, dict) else None
        actual = float(reported) if isinstance(reported, (int, float)) else self.est
        budget.settle(self.conn, "exa", "monthly", period, self.est, actual, key=key)
        results = [x for x in (normalise_result(r, purpose, text_cap) for r in data["results"] if isinstance(r, dict)) if x]
        usage.record(self.conn, provider="exa", operation=self.operation, units=1, unit="searches", cost_usd=actual,
                     cost_basis="reported" if reported is not None else "estimated", query=query, organization_id=self.org_id,
                     detail={"purpose": purpose, "results": len(results), "category": body.get("category"), "num_requested": body.get("numResults")})
        self.conn.commit()
        self.calls += 1
        self.cost += actual
        self.log.append({"purpose": purpose, "results": len(results)})
        return results


def number_sources(results: list[dict[str, Any]], prefix: str = "W", limit: int = 24) -> dict[str, dict[str, Any]]:
    """Deduplicate by address (first search wins) and number the pages W1, W2, ... in the order they were found."""
    out: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for r in results:
        k = r["url"].split("#")[0].rstrip("/").lower()
        if k in seen or not (r.get("text") or r.get("title")):
            continue
        seen.add(k)
        out[f"{prefix}{len(out) + 1}"] = r
        if len(out) >= limit:
            break
    return out


def source_list(src: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """What is stored and shown: address, title, date and why it was searched. Never the page text."""
    return [{"id": k, "url": v["url"], "title": v.get("title"), "published": v.get("published"), "purpose": v.get("purpose")} for k, v in src.items()]


def worker_can(conn: Any) -> dict[str, bool]:
    """What the worker that runs research reported in its last heartbeat (the keys live on the worker, not necessarily on the web app)."""
    if conn is None:
        return {}
    row = conn.execute("SELECT detail FROM system_heartbeats WHERE component='worker-interactive' AND at > now() - interval '15 minutes'").fetchone()
    can = (row or {}).get("detail", {}).get("can") if row else None
    return can if isinstance(can, dict) else {}


def configured(st: Any, conn: Any = None) -> dict[str, Any]:
    """Research is ready when this process has the keys, or when the worker that runs research says it has them."""
    can = worker_can(conn)
    exa = bool(getattr(st, "exa_api_key", None)) or bool(can.get("exa"))
    cap = float(getattr(st, "exa_monthly_cap_usd", 0) or 0) or (1.0 if can.get("exa") else 0.0)
    ai = bool(getattr(st, "anthropic_api_key", None)) or bool(can.get("ai"))
    missing = [x for x, ok in (("EXA_API_KEY", exa), ("RI_EXA_MONTHLY_CAP_USD", cap > 0), ("ANTHROPIC_API_KEY", ai)) if not ok]
    return {"exa": exa and cap > 0, "ai": ai, "ready": not missing, "missing": missing,
            "note": None if not missing else "Research with Exa is not set up in this environment. It needs " + ", ".join(missing) + " on the API and the worker."}
