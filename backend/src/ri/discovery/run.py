"""Exa discovery run. Hard-capped: every search reserves budget first; the real cost comes from Exa's own response (costDollars.total)
and is what gets recorded. Hits become verification requests (never events) and are capped per run."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from .. import budget, usage
from ..db import Conn, q1, qa
from ..jobs.queue import enqueue
from ..sources.base import BudgetExceeded, ConnectorError
from ..sources.exa import ExaClient, SearchPlan
from ..verify.fetch import SOCIAL

DEFAULT_EST = 0.01        # conservative reservation per search when no estimate is configured; actual cost replaces it on settle
PER_PLAN_TOP = 3


def _period() -> str:
    return usage.period_now()


async def _search(exa: ExaClient, plan: SearchPlan):
    return await exa.search(plan, num_results=8)


def run_discovery(conn: Conn, exa: ExaClient, *, max_searches: int, max_new_requests: int, interval_days: int, est_cost: float | None,
                  plan_ids: list[str] | None = None, now: datetime | None = None, requested_by: str = "system:discovery") -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    est = est_cost if est_cost is not None else DEFAULT_EST
    due = qa(conn,
             "SELECT * FROM discovery_plans WHERE enabled AND (%s::text[] IS NULL OR plan_id = ANY(%s)) AND (%s::text[] IS NOT NULL OR theme <> 'account_watch') AND "
             "(%s::text[] IS NOT NULL OR last_run_at IS NULL OR last_run_at < %s) ORDER BY last_run_at NULLS FIRST, (theme='private-rfp') DESC, plan_id LIMIT %s",
             (plan_ids, plan_ids, plan_ids, plan_ids, now - timedelta(days=interval_days), max_searches))
    out: dict[str, Any] = {"searches": 0, "results": 0, "requests_created": 0, "cost_usd": 0.0, "stopped": None}
    for p in due:
        if out["requests_created"] >= max_new_requests:
            out["stopped"] = "per-run request limit reached"
            break
        plan = SearchPlan(p["signal_rule_id"], p["theme"], p["geography"], p["language"], "news_and_press", p["recency_days"], p["query"])
        key = f"exa:{p['plan_id']}:{uuid.uuid4()}"
        try:
            budget.reserve(conn, "exa", "monthly", _period(), est, key=key, ref_type="discovery_plan", ref_id=p["plan_id"], purpose=f"search {p['theme']} {p['geography']}")
        except BudgetExceeded:
            conn.commit()
            out["stopped"] = "monthly Exa budget reached"
            break
        try:
            items, meta = asyncio.run(_search(exa, plan))
        except ConnectorError as exc:
            budget.release(conn, "exa", "monthly", _period(), est, key=key)
            conn.execute("UPDATE discovery_plans SET last_error=%s WHERE plan_id=%s", (f"{exc.error_class}: {exc}"[:300], p["plan_id"]))
            usage.record(conn, provider="exa", operation="search", units=0, unit="searches", plan_id=p["plan_id"], query=p["query"], status=f"error:{exc.error_class}", source_id="discovery-exa")
            conn.commit()
            if exc.error_class in {"auth", "access_denied"}:
                out["stopped"] = f"Exa refused the request ({exc.error_class}); check the API key"
                break
            continue
        reported = meta.get("reported_cost")
        actual = float(reported) if reported is not None else est
        budget.settle(conn, "exa", "monthly", _period(), est, actual, key=key)
        usage.record(conn, provider="exa", operation="search", units=1, unit="searches", cost_usd=actual, cost_basis="reported" if reported is not None else "estimated",
                     source_id="discovery-exa", plan_id=p["plan_id"], query=p["query"], detail={"results": len(items), "num_requested": 8})
        created = 0
        for it in items[:PER_PLAN_TOP * 3]:
            if created >= PER_PLAN_TOP or out["requests_created"] + created >= max_new_requests:
                break
            url = it.payload["url"]
            if q1(conn, "SELECT 1 FROM verification_requests WHERE url=%s UNION ALL SELECT 1 FROM source_documents WHERE canonical_url=%s LIMIT 1", (url, url)):
                continue                                 # already seen: never pay to read the same page twice
            host = (urlparse(url).hostname or "").lower()
            if any(host == d or host.endswith("." + d) for d in SOCIAL):
                continue
            row = conn.execute(
                "INSERT INTO verification_requests(organization_id, url, origin, claimed_text, claimed_date, plan_id, requested_by) VALUES (%s,%s,'exa_discovery',%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING id", (p.get("organization_id"), url, (it.payload.get("title") or "")[:300], it.payload.get("published"), p["plan_id"], requested_by)).fetchone()
            if row:
                jid, _ = enqueue(conn, "verify_url", {"request_id": str(row["id"])}, idempotency_key=f"verify:{row['id']}", max_attempts=3)
                conn.execute("UPDATE verification_requests SET job_id=%s WHERE id=%s", (jid, row["id"]))
                created += 1
        conn.execute("UPDATE discovery_plans SET last_run_at=%s, runs=runs+1, results_total=results_total+%s, requests_created=requests_created+%s, cost_total_usd=cost_total_usd+%s, last_error=NULL WHERE plan_id=%s",
                     (now, len(items), created, actual, p["plan_id"]))
        conn.commit()
        out["searches"] += 1
        out["results"] += len(items)
        out["requests_created"] += created
        out["cost_usd"] = round(out["cost_usd"] + actual, 6)
    return out


def due_count(conn: Conn, interval_days: int, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    r = q1(conn, "SELECT count(*) AS n FROM discovery_plans WHERE enabled AND theme <> 'account_watch' AND (last_run_at IS NULL OR last_run_at < %s)", (now - timedelta(days=interval_days),))
    return int(r["n"]) if r else 0
