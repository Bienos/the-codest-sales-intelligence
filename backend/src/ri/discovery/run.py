"""Exa discovery run. Hard-capped: every search reserves budget first; the real cost comes from Exa's own response (costDollars.total)
and is what gets recorded. Hits become verification requests (never events) and are capped per run."""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote, urlparse

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
COVERAGE_MAX_BACKLOG = 150      # no account searches while this many pages from web search still wait for their check (the reader is stopped or far behind)



# Search only buyer-controlled domains. A cap keeps each regional query focused.
_RFP_REGIONS = {label: code for code, label in TARGET_REGIONS} | {"UK": "uk"}
_RFP_CLOSED = re.compile(r"\b(contract award(?: notice)?|tender result|closed tender|cancelled tender|"
                         r"vergabebekanntmachung|tilldelningsbeslut|wynik postępowania|ogłoszenie o wyniku)\b", re.I)
_RFP_JOBS = re.compile(r"(?:^|[./_-])(?:careers?|jobs?|vacancies|karriere|kariera)(?:[./_-]|$)", re.I)
_RFP_CUES = re.compile(
    r"\b(?:rf[piq]|tenders?|procurement|purchasing|suppliers?|bids?|quotations?|"
    r"ausschreib\w*|vergab\w*|lieferant\w*|upphandl\w*|anskaffels\w*|udbud|"
    r"anbud|hankin\w*|tarjouspyynt\w*|przetarg\w*|ofert\w*|postępowan\w*|"
    r"dostawc\w*|zam[oó]wieni\w*|appel d.offres|marchés publics|leveranciers?)\b", re.I,
)


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



def _buyer_rfp_site(url: str, title: str, domains: dict[str, Any]) -> str | None:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
    except ValueError:
        return None
    if parsed.scheme not in {"https", "http"} or parsed.username or parsed.password:
        return None
    domain = next((d for d in sorted(domains, key=len, reverse=True) if _under(host, d)), None)
    path = unquote(parsed.path)
    if not domain or _RFP_JOBS.search(f"{host}{path}") or _RFP_CLOSED.search(title):
        return None
    return domain if _RFP_CUES.search(f"{host} {path.replace('-', ' ').replace('_', ' ')} {title}") else None


def _period() -> str:
    return usage.period_now()


async def _search(exa: ExaClient, plan: SearchPlan):
    return await exa.search(plan, num_results=8)


def _config(overrides: dict[str, Any]) -> dict[str, Any]:
    """The coverage settings: what the caller passed, else the environment's (Settings, with the RI_DISCOVERY_COVERAGE_* overrides)."""
    from ..config import Settings
    try:
        s = Settings.from_env(require_auth=False)
    except Exception:  # noqa: BLE001 - a bad environment must not stop the searches: built-in defaults
        s = Settings()
    base = {"coverage_searches": s.discovery_coverage_searches_per_run, "coverage_max_new_requests": s.discovery_coverage_max_new_requests_per_run,
            "coverage_cycle_days": s.discovery_coverage_cycle_days, "auto_budget_share": s.discovery_auto_budget_share}
    return {k: (overrides[k] if overrides.get(k) is not None else v) for k, v in base.items()}


def _share_stop(conn: Conn, share: float, est: float) -> str | None:
    """Why an AUTOMATIC search must not run now (None: it may). Automatic searches never take this month's Exa spend past `share` of the cap, so
    sellers' own research buttons, company research and people search always keep the rest. A search the cap itself cannot hold is left to
    budget.reserve, which refuses it with the usual message."""
    b = q1(conn, "SELECT cap, reserved, spent FROM provider_budgets WHERE provider='exa' AND kind='monthly' AND period=%s", (_period(),))
    if not b or float(b["cap"]) <= 0:
        return None
    cap, used = float(b["cap"]), float(b["reserved"]) + float(b["spent"])
    if used + est > cap or used + est <= cap * share:
        return None
    return f"automatic web searches stop at {share * 100:g}% of this month's Exa budget; the rest is kept for sellers' research"


def _coverage_hold(conn: Conn) -> str | None:
    """Why account searches should wait: a page found now could not be read. Searching costs Exa money, so never search into a stopped reader."""
    from ..verify.run import RESERVE_FOR_SELLERS
    b = q1(conn, "SELECT cap, reserved, spent FROM provider_budgets WHERE provider='anthropic' AND kind='monthly' AND period=%s", (_period(),))
    if not b or float(b["cap"]) <= 0:
        return "AI reading is not set up, so pages found now could not be read"
    if (float(b["cap"]) - float(b["reserved"]) - float(b["spent"])) / float(b["cap"]) < RESERVE_FOR_SELLERS:
        return "AI reading of web search pages is paused for this month (70% of the AI limit is used)"
    behind = int((q1(conn, "SELECT count(*) AS n FROM verification_requests WHERE origin='exa_discovery' AND status IN ('queued','fetching','extracting','waiting_budget')") or {"n": 0})["n"])
    if behind > COVERAGE_MAX_BACKLOG:
        return f"{behind} pages found by web search are still waiting to be checked"
    return None


def _search_plan(conn: Conn, exa: ExaClient, p: dict, plan: SearchPlan, *, est: float, now: datetime, room: int, domains: dict[str, Any],
                 share: float | None, requested_by: str) -> dict[str, Any]:
    """One saved search: reserve, search, record the real cost, queue the new pages for the page check. The result says what happened:
    attempted (money was reserved), searched, created (new page checks), results, cost, filtered (buyer-site filter), stop (why the whole run must end)."""
    r: dict[str, Any] = {"attempted": False, "searched": False, "created": 0, "results": 0, "cost": 0.0, "filtered": 0, "stop": None}
    if share is not None:
        r["stop"] = _share_stop(conn, share, est)
        if r["stop"]:
            return r
    key = f"exa:{p['plan_id']}:{uuid.uuid4()}"
    try:
        budget.reserve(conn, "exa", "monthly", _period(), est, key=key, ref_type="discovery_plan", ref_id=p["plan_id"], purpose=f"search {p['theme']} {p['geography']}")
    except BudgetExceeded:
        conn.commit()
        r["stop"] = "monthly Exa budget reached"
        return r
    r["attempted"] = True
    org_id = p.get("organization_id")
    try:
        items, meta = asyncio.run(_search(exa, plan))
    except ConnectorError as exc:
        budget.release(conn, "exa", "monthly", _period(), est, key=key)
        conn.execute("UPDATE discovery_plans SET last_error=%s WHERE plan_id=%s", (f"{exc.error_class}: {exc}"[:300], p["plan_id"]))
        usage.record(conn, provider="exa", operation="search", units=0, unit="searches", plan_id=p["plan_id"], query=p["query"], status=f"error:{exc.error_class}", source_id="discovery-exa",
                     organization_id=str(org_id) if org_id else None)
        conn.commit()
        if exc.error_class in {"auth", "access_denied"}:
            r["stop"] = f"Exa refused the request ({exc.error_class}); check the API key"
        return r
    reported = meta.get("reported_cost")
    actual = float(reported) if reported is not None else est
    budget.settle(conn, "exa", "monthly", _period(), est, actual, key=key)
    usage.record(conn, provider="exa", operation="search", units=1, unit="searches", cost_usd=actual, cost_basis="reported" if reported is not None else "estimated",
                 source_id="discovery-exa", plan_id=p["plan_id"], query=p["query"], organization_id=str(org_id) if org_id else None,
                 detail={"results": len(items), "num_requested": 8})
    private_rfp = p["theme"] == "private-rfp"
    created = 0
    for it in items[:PER_PLAN_TOP * 3]:
        if created >= PER_PLAN_TOP or created >= room:
            break
        url = it.payload["url"]
        row_org = org_id
        if private_rfp:
            domain = _buyer_rfp_site(url, it.payload.get("title") or "", domains)
            if domain is None:
                r["filtered"] += 1
                continue
            row_org = domains[domain]
        if q1(conn, "SELECT 1 FROM verification_requests WHERE url=%s UNION ALL SELECT 1 FROM source_documents WHERE canonical_url=%s LIMIT 1", (url, url)):
            continue                                 # already seen: never pay to read the same page twice
        host = (urlparse(url).hostname or "").lower()
        if any(host == d or host.endswith("." + d) for d in SOCIAL):
            continue
        row = conn.execute(
            "INSERT INTO verification_requests(organization_id, url, origin, claimed_text, claimed_date, plan_id, requested_by) VALUES (%s,%s,'exa_discovery',%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING RETURNING id", (row_org, url, (it.payload.get("title") or "")[:300], it.payload.get("published"), p["plan_id"], requested_by)).fetchone()
        if row:
            jid, _ = enqueue(conn, "verify_url", {"request_id": str(row["id"])}, idempotency_key=f"verify:{row['id']}", max_attempts=3)
            conn.execute("UPDATE verification_requests SET job_id=%s WHERE id=%s", (jid, row["id"]))
            created += 1
    conn.execute("UPDATE discovery_plans SET last_run_at=%s, runs=runs+1, results_total=results_total+%s, requests_created=requests_created+%s, cost_total_usd=cost_total_usd+%s, last_error=NULL WHERE plan_id=%s",
                 (now, len(items), created, actual, p["plan_id"]))
    conn.commit()
    r.update(searched=True, created=created, results=len(items), cost=actual)
    return r


def run_discovery(conn: Conn, exa: ExaClient, *, max_searches: int, max_new_requests: int, interval_days: int, est_cost: float | None,
                  plan_ids: list[str] | None = None, now: datetime | None = None, requested_by: str = "system:discovery",
                  coverage_searches: int | None = None, coverage_max_new_requests: int | None = None, coverage_cycle_days: int | None = None,
                  auto_budget_share: float | None = None, max_seconds: float | None = 240.0) -> dict[str, Any]:
    """The daily run. Two passes: the general saved searches that are due (every `interval_days`), then ONE search for each target account
    that has no finding in the last 90 days (never-searched first, then the oldest; see plans.py for the money arithmetic). A run that names
    `plan_ids` (a person pressed a button) runs only those, with the full monthly cap; every other run is automatic and stops at
    `auto_budget_share` (60%) of the monthly Exa cap. `max_seconds` keeps the job inside its five-minute lease; what is left continues at the next run."""
    from .plans import COVERAGE_THEME, coverage_waiting
    now = now or datetime.now(timezone.utc)
    est = est_cost if est_cost is not None else DEFAULT_EST
    automatic = plan_ids is None
    cfg = _config({"coverage_searches": coverage_searches, "coverage_max_new_requests": coverage_max_new_requests,
                   "coverage_cycle_days": coverage_cycle_days, "auto_budget_share": auto_budget_share})
    share = float(cfg["auto_budget_share"]) if automatic else None
    deadline = time.monotonic() + max_seconds if max_seconds is not None else None
    due = qa(conn,
             "SELECT * FROM discovery_plans WHERE enabled AND (%s::text[] IS NULL OR plan_id = ANY(%s)) AND (%s::text[] IS NOT NULL OR theme NOT IN ('account_watch', %s)) AND "
             "(%s::text[] IS NOT NULL OR last_run_at IS NULL OR last_run_at < %s) ORDER BY last_run_at NULLS FIRST, (theme='private-rfp') DESC, plan_id",
             (plan_ids, plan_ids, plan_ids, COVERAGE_THEME, plan_ids, now - timedelta(days=interval_days)))
    out: dict[str, Any] = {"searches": 0, "results": 0, "requests_created": 0, "cost_usd": 0.0, "stopped": None}
    attempted = 0

    def add(res: dict[str, Any]) -> None:
        out["searches"] += int(res["searched"])
        out["results"] += res["results"]
        out["requests_created"] += res["created"]
        out["cost_usd"] = round(out["cost_usd"] + res["cost"], 6)
        if res["filtered"]:
            out["rfp_results_filtered"] = out.get("rfp_results_filtered", 0) + res["filtered"]

    for p in due:
        if attempted >= max_searches:
            break
        if deadline is not None and time.monotonic() > deadline:
            out["stopped"] = "time limit for this run reached; the rest continues at the next run"
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
        res = _search_plan(conn, exa, p, plan, est=est, now=now, room=max_new_requests - out["requests_created"], domains=domains, share=share, requested_by=requested_by)
        attempted += int(res["attempted"])
        add(res)
        if res["stop"]:
            out["stopped"] = res["stop"]
            break

    if automatic and cfg["coverage_searches"] > 0:
        coverage_pass(conn, exa, out, est=est, now=now, share=share, cycle_days=int(cfg["coverage_cycle_days"]), searches=int(cfg["coverage_searches"]),
                      max_new=int(cfg["coverage_max_new_requests"]), deadline=deadline, add=add, requested_by=requested_by)
        left = coverage_waiting(conn, now=now, cycle_days=int(cfg["coverage_cycle_days"]))
        n = out.get("accounts_searched", 0)
        out["accounts_still_without_a_search"] = left
        out["summary"] = f"Searched {n} {'account' if n == 1 else 'accounts'}; {left} {'account' if left == 1 else 'accounts'} still without a search."
    return out


def coverage_pass(conn: Conn, exa: ExaClient, out: dict[str, Any], *, est: float, now: datetime, share: float | None, cycle_days: int, searches: int,
                  max_new: int, deadline: float | None, add, requested_by: str) -> None:
    """One search for each of the next target accounts without a recent finding. Adds its figures to `out`; the general searches' stop reasons
    (budget, key, 60% share) also end this pass, because the cause is the same."""
    from .plans import (
        COVERAGE_MIN_WINDOW_DAYS,
        COVERAGE_WINDOW_DAYS,
        coverage_candidates,
        ensure_coverage_plans,
    )
    out["accounts_searched"] = 0
    if out["stopped"] and out["stopped"] != "per-run request limit reached":
        return
    hold = _coverage_hold(conn)
    if hold:
        out["coverage_waiting_because"] = hold
        return
    created = 0
    try:
        for c in coverage_candidates(conn, now=now, cycle_days=cycle_days, limit=searches):
            if deadline is not None and time.monotonic() > deadline:
                out["stopped"] = "time limit for this run reached; the rest continues at the next run"
                return
            if created >= max_new:
                out["stopped"] = out["stopped"] or "per-run request limit reached"
                return
            plans = ensure_coverage_plans(conn, c["organization_id"], c["display_name"], c["country_code"])
            if not plans:
                continue
            p = plans[0]                                 # the one run longest ago: one search per account per cycle, the languages alternate
            # a later search only needs to cover the time since the last one (plus a week), so its eight results are fresh pages, not the ones already seen
            window = COVERAGE_WINDOW_DAYS if c["last_search"] is None else max(COVERAGE_MIN_WINDOW_DAYS, min(COVERAGE_WINDOW_DAYS, (now - c["last_search"]).days + 7))
            plan = SearchPlan(p["signal_rule_id"], p["theme"], p["geography"], p["language"], "news_and_press", window, p["query"])
            res = _search_plan(conn, exa, p, plan, est=est, now=now, room=max_new - created, domains={}, share=share, requested_by=requested_by)
            add(res)
            created += res["created"]
            out["accounts_searched"] += int(res["searched"])
            if res["stop"]:
                out["stopped"] = res["stop"]
                return
    finally:
        conn.commit()                                    # the saved searches made for accounts the run stopped before reaching are kept too


def due_count(conn: Conn, interval_days: int, now: datetime | None = None) -> int:
    """How much automatic search work is waiting: general saved searches that are due, else (only when none is) 1 if any target account without a
    recent finding is still waiting for its search. Asked every five minutes, so the account check stops at the first account it finds."""
    from .plans import COVERAGE_THEME, coverage_waiting
    now = now or datetime.now(timezone.utc)
    r = q1(conn, "SELECT count(*) AS n FROM discovery_plans WHERE enabled AND theme NOT IN ('account_watch', %s) AND (last_run_at IS NULL OR last_run_at < %s)", (COVERAGE_THEME, now - timedelta(days=interval_days)))
    n = int(r["n"]) if r else 0
    if n == 0:
        cfg = _config({})
        if cfg["coverage_searches"] > 0:
            n = coverage_waiting(conn, now=now, cycle_days=int(cfg["coverage_cycle_days"]), at_least_one=True)
    return n
