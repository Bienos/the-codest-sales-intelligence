"""Exa discovery run. Hard-capped: every search reserves budget first; the real cost comes from Exa's own response (costDollars.total)
and is what gets recorded. Hits become verification requests (never events) and are capped per run."""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from .. import budget, usage
from ..db import Conn, q1, qa
from ..jobs.queue import enqueue
from ..sources.base import BudgetExceeded, ConnectorError
from ..sources.exa import ExaClient, SearchPlan
from ..target_market import TARGET_REGIONS, target_region, target_sql
from ..verify.fetch import SOCIAL
from ..verify.run import _host, _under

DEFAULT_EST = 0.01        # conservative reservation per search when no estimate is configured; actual cost replaces it on settle
PER_PLAN_TOP = 3



# Search only buyer-controlled domains. A cap keeps each regional query focused.
_RFP_REGIONS = {label: code for code, label in TARGET_REGIONS} | {"UK": "uk"}
_RFP_CLOSED = re.compile(r"\b(contract award(?: notice)?|tender result|closed tender|cancelled tender|"
                         r"vergabebekanntmachung|tilldelningsbeslut|wynik postępowania|ogłoszenie o wyniku)\b", re.I)
_RFP_JOBS = re.compile(r"/(?:careers?|jobs|vacancies|karriere|kariera)(?:/|$)", re.I)


def private_rfp_domains(conn: Conn, geography: str) -> dict[str, Any]:
    target = target_sql()
    rows = qa(conn, f"""
        SELECT o.id, o.canonical_domain, o.country_code, {target} AS confirmed
        FROM organizations o
        WHERE ({target} OR {target_sql(review=True)}) AND o.canonical_domain IS NOT NULL
        ORDER BY confirmed DESC, EXISTS (SELECT 1 FROM account_endpoints ep
                  WHERE ep.organization_id=o.id AND ep.kind='procurement' AND ep.last_status='ok') DESC,
                  o.in_master_list DESC, o.display_name, o.id
        """)
    region = _RFP_REGIONS.get(geography)
    domains = {}
    for row in rows:
        if target_region(row["country_code"]) != region:
            continue
        host = _host("https://" + row["canonical_domain"])
        if not host or "." not in host:
            continue
        # A shared group domain does not establish the buyer's legal identity.
        domains.setdefault(host, []).append(row["id"])
    return {host: ids[0] if len(ids) == 1 else None for host, ids in list(domains.items())[:20]}


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
             "(%s::text[] IS NOT NULL OR last_run_at IS NULL OR last_run_at < %s) ORDER BY last_run_at NULLS FIRST, (theme='private-rfp') DESC, plan_id",
             (plan_ids, plan_ids, plan_ids, plan_ids, now - timedelta(days=interval_days)))
    out: dict[str, Any] = {"searches": 0, "results": 0, "requests_created": 0, "cost_usd": 0.0, "stopped": None}
    attempted = 0
    for p in due:
        if attempted >= max_searches:
            break
        private_rfp = p["theme"] == "private-rfp"
        if out["requests_created"] >= max_new_requests:
            out["stopped"] = "per-run request limit reached"
            break
        domains = private_rfp_domains(conn, p["geography"]) if private_rfp else {}
        if private_rfp and not domains:
            out["rfp_regions_without_buyer_domains"] = sorted(set(out.get("rfp_regions_without_buyer_domains", [])) | {p["geography"]})
            conn.execute("UPDATE discovery_plans SET last_run_at=%s, last_error=%s WHERE plan_id=%s",
                         (now, "No eligible buyer websites for this region", p["plan_id"]))
            conn.commit()
            continue
        plan = SearchPlan(p["signal_rule_id"], p["theme"], p["geography"], p["language"], "news_and_press", p["recency_days"],
                          p["query"], include_domains=tuple(domains))
        key = f"exa:{p['plan_id']}:{uuid.uuid4()}"
        try:
            budget.reserve(conn, "exa", "monthly", _period(), est, key=key, ref_type="discovery_plan", ref_id=p["plan_id"], purpose=f"search {p['theme']} {p['geography']}")
        except BudgetExceeded:
            conn.commit()
            out["stopped"] = "monthly Exa budget reached"
            break
        attempted += 1
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
            org_id = p.get("organization_id")
            if private_rfp:
                try:
                    parsed = urlparse(url)
                    host = parsed.hostname or ""
                    domain = next((d for d in sorted(domains, key=len, reverse=True) if _under(host, d)), None)
                    buyer_link = parsed.scheme in {"https", "http"} and not parsed.username and not parsed.password
                except ValueError:
                    buyer_link, domain = False, None
                if not buyer_link or domain is None or _RFP_JOBS.search(parsed.path) or _RFP_CLOSED.search(it.payload.get("title") or ""):
                    out["rfp_results_filtered"] = out.get("rfp_results_filtered", 0) + 1
                    continue
                org_id = domains[domain]
            if q1(conn, "SELECT 1 FROM verification_requests WHERE url=%s UNION ALL SELECT 1 FROM source_documents WHERE canonical_url=%s LIMIT 1", (url, url)):
                continue                                 # already seen: never pay to read the same page twice
            host = (urlparse(url).hostname or "").lower()
            if any(host == d or host.endswith("." + d) for d in SOCIAL):
                continue
            row = conn.execute(
                "INSERT INTO verification_requests(organization_id, url, origin, claimed_text, claimed_date, plan_id, requested_by) VALUES (%s,%s,'exa_discovery',%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING id", (org_id, url, (it.payload.get("title") or "")[:300], it.payload.get("published"), p["plan_id"], requested_by)).fetchone()
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
