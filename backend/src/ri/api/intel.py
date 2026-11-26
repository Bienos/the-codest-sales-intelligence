"""Verification requests, discovery controls, usage tracker and source library."""
from __future__ import annotations

import calendar
from uuid import UUID
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import usage
from ..access import Principal, org_scope_sql
from ..audit import audit
from ..readmodel import coverage_health, story_sources
from ..db import q1, qa
from ..jobs.queue import enqueue
from ..target_market import account_workspace_sql, target_sql
from .auth import principal_dep, require

RUN_WAITING = ("scheduled", "claimed", "fetching", "parsing", "verifying", "committing")     # a job that has not finished yet
URL_KEYS = ("Primary source URL", "Primary Source URL", "Secondary Source URL", "Source URL", "Source link", "Signal Source URL", "Supplier evidence URL", "Existing source URL", "Second Signal Source URL", "Third Signal Source URL", "Matched Signal Source URL", "Evidence URL")


class VerifyIn(BaseModel):
    organization_id: str
    url: str | None = Field(default=None, max_length=1000)


class BatchIn(BaseModel):
    lane: str = Field(default="shortlist", pattern="^(all|shortlist|signals)$")
    limit: int = Field(default=10, ge=1, le=25)
    focus: str = Field(default="", pattern="^(|finance|other)$")
    target: bool = False
    workbook: bool = False
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    q: str = Field(default="", max_length=120)
    region: str = Field(default="", pattern="^(|uk|dach|nordics|poland|europe|international)$")


class OpenedIn(BaseModel):
    organization_id: str | None = None
    hypothesis_id: str | None = None
    where: str = Field(default="evidence", pattern="^(evidence|prospect)$")


class RunIn(BaseModel):
    plan_ids: list[str] | None = Field(default=None, max_length=40)


def org_urls(conn, org_id: str) -> dict[str, dict[str, Any]]:
    """URLs the sheets cite for this account (the only pages a seller may ask the platform to verify), with the claim text."""
    found: dict[str, dict[str, Any]] = {}
    for r in qa(conn, "SELECT kind, original FROM legacy_account_research WHERE organization_id=%s", (org_id,)):
        o = r["original"] or {}
        claim = o.get("Strongest buying signal") or o.get("Verified Buying Signal") or o.get("Verified event") or o.get("Buying / tech signal") or o.get("Latest Buying Signal (2026 verified)")
        date = o.get("Event date") or o.get("Signal Date") or o.get("Signal date") or o.get("Verified Signal Date")
        for k in URL_KEYS:
            u = str(o.get(k) or "").strip()
            if u.lower().startswith(("http://", "https://")):
                found.setdefault(u, {"claimed_text": (claim or "")[:300], "claimed_date": str(date or "")[:40]})
    return found


def queue_verification(conn, *, org_id: str | None, url: str, origin: str, claimed: dict[str, Any], by: str) -> dict[str, Any]:
    row = conn.execute(
        "INSERT INTO verification_requests(organization_id, url, origin, claimed_text, claimed_date, requested_by) VALUES (%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT DO NOTHING RETURNING id", (org_id, url, origin, claimed.get("claimed_text"), claimed.get("claimed_date"), by)).fetchone()
    if not row:
        if origin == "sheet_signal":
            ex = q1(conn, "SELECT id, status FROM verification_requests WHERE url=%s AND origin=%s AND organization_id IS NOT DISTINCT FROM %s", (url, origin, org_id))
        else:
            ex = q1(conn, "SELECT id, status FROM verification_requests WHERE url=%s AND origin=%s", (url, origin))
        return {"id": str(ex["id"]), "status": ex["status"], "duplicate": True}
    jid, _ = enqueue(conn, "verify_url", {"request_id": str(row["id"])}, idempotency_key=f"verify:{row['id']}", max_attempts=3)
    conn.execute("UPDATE verification_requests SET job_id=%s WHERE id=%s", (jid, row["id"]))
    return {"id": str(row["id"]), "status": "queued", "duplicate": False}


def register(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    # ------------------------------------------------------------ instrumentation (spec 01 section 8)
    @app.post("/api/telemetry/source-opened")
    def source_opened(body: OpenedIn, request: Request, p: Principal = Depends(require("today.read"))):
        from ..db import jb
        with tx(request) as conn:
            org, hyp = None, None
            if body.hypothesis_id:
                where, params = org_scope_sql(p, "h.organization_id")
                h = q1(conn, f"SELECT h.id, h.organization_id FROM opportunity_hypotheses h WHERE h.id=%s AND ({where})", [body.hypothesis_id, *params])      # noqa: S608
                if h:
                    hyp, org = str(h["id"]), str(h["organization_id"])
            elif body.organization_id:
                where, params = org_scope_sql(p, "o.id")
                if q1(conn, f"SELECT 1 FROM organizations o WHERE o.id=%s AND ({where})", [body.organization_id, *params]):      # noqa: S608
                    org = body.organization_id
            if org or hyp:
                conn.execute("INSERT INTO product_events(user_id, event, hypothesis_id, organization_id, detail) VALUES (%s,'source_opened',%s,%s,%s)", (p.user_id, hyp, org, jb({"where": body.where})))
            return {"ok": True}

    # ------------------------------------------------------------ verification
    @app.post("/api/prospects/verify")
    def verify_one(body: VerifyIn, request: Request, p: Principal = Depends(require("research.request"))):
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            if not q1(conn, f"SELECT 1 FROM organizations o WHERE o.id=%s AND ({where})", [body.organization_id, *params]):      # noqa: S608
                raise HTTPException(404, "not found")
            urls = org_urls(conn, body.organization_id)
            url = body.url or next(iter(urls), None)
            if not url or url not in urls:
                raise HTTPException(400, "only a source URL cited in your sheets for this account can be verified")
            out = queue_verification(conn, org_id=body.organization_id, url=url, origin="sheet_signal", claimed=urls[url], by=p.email)
            audit(conn, actor=p.email, operation="verification_requested", record_type="verification_request", record_id=out["id"], detail={"url": url})
            return out

    @app.post("/api/prospects/verify-batch")
    def verify_batch(body: BatchIn, request: Request, p: Principal = Depends(require("research.request"))):
        from .lists import prospects_for      # shared ranking
        with tx(request) as conn:
            limit = body.page_size if body.target or body.workbook else 100
            items = prospects_for(conn, p, lane=body.lane, q=body.q, limit=limit, focus=body.focus, target=body.target, region=body.region, offset=(body.page - 1) * limit, workbook=body.workbook)
            queued = skipped = 0
            for it in items:
                if queued >= body.limit:
                    break
                if not it["source_url"] or it.get("verification"):
                    skipped += 1
                    continue
                urls = org_urls(conn, it["organization"]["id"])
                if it["source_url"] not in urls:
                    skipped += 1
                    continue
                queue_verification(conn, org_id=it["organization"]["id"], url=it["source_url"], origin="sheet_signal", claimed=urls[it["source_url"]], by=p.email)
                queued += 1
            audit(conn, actor=p.email, operation="verification_batch_requested", record_type="verification_request", record_id=None, detail={"lane": body.lane, "queued": queued})
            return {"queued": queued, "skipped": skipped}

    @app.post("/api/verifications/{vid}/retry")
    def retry(vid: str, request: Request, p: Principal = Depends(require("research.request"))):
        with tx(request) as conn:
            r = q1(conn, "SELECT * FROM verification_requests WHERE id=%s FOR UPDATE", (vid,))
            if not r or r["status"] not in {"waiting_budget", "failed"}:
                raise HTTPException(404, "nothing to retry")
            if r["organization_id"]:
                where, params = org_scope_sql(p, "o.id")
                if not q1(conn, f"SELECT 1 FROM organizations o WHERE o.id=%s AND ({where})", [str(r["organization_id"]), *params]):      # noqa: S608
                    raise HTTPException(404, "not found")
            live_job = q1(conn, """SELECT id FROM jobs WHERE id=%s AND status IN
                           ('scheduled','claimed','fetching','parsing','verifying','committing','retryable_failed')""", (r["job_id"],))
            if live_job:
                return {"id": vid, "status": r["status"], "already_scheduled": True}
            conn.execute("UPDATE verification_requests SET status='queued', updated_at=now() WHERE id=%s", (vid,))
            jid, _ = enqueue(conn, "verify_url", {"request_id": vid}, idempotency_key=f"verify:{vid}:retry:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}", max_attempts=3)
            conn.execute("UPDATE verification_requests SET job_id=%s WHERE id=%s", (jid, vid))
            return {"id": vid, "status": "queued"}

    @app.get("/api/verifications")
    def list_verifications(request: Request, status: str = "", origin: str = "", limit: int = Query(50, le=200), p: Principal = Depends(require("sources.read"))):
        with tx(request) as conn:
            rows = qa(conn,
                "SELECT v.id, v.url, v.origin, v.claimed_text, v.status, v.events_created, v.result, v.requested_by, v.created_at, v.updated_at, v.plan_id, o.display_name AS organization "
                "FROM verification_requests v LEFT JOIN organizations o ON o.id=v.organization_id WHERE (%s='' OR v.status=%s) AND (%s='' OR v.origin=%s) ORDER BY v.created_at DESC LIMIT %s",
                (status, status, origin, origin, limit))
            counts = {r["status"]: r["n"] for r in qa(conn, "SELECT status, count(*) AS n FROM verification_requests GROUP BY status")}
            queue = q1(conn, """
                SELECT count(*) FILTER (WHERE v.status IN ('queued','fetching','extracting','waiting_budget')) AS pending,
                       min(v.created_at) FILTER (WHERE v.status IN ('queued','fetching','extracting','waiting_budget')) AS oldest_pending,
                       max(v.updated_at) FILTER (WHERE v.status='verified') AS last_verified,
                       count(*) FILTER (WHERE v.status='waiting_budget') AS waiting_budget,
                       count(*) FILTER (WHERE v.status='queued' AND (j.last_error_class='provider_billing'
                          OR v.result->>'error_class'='provider_billing')) AS provider_billing,
                       count(*) FILTER (WHERE v.status='queued' AND j.status='permanent_failed'
                          AND v.result->>'error_class'='provider_billing') AS held_for_recovery,
                       min(j.run_after) FILTER (WHERE v.status='queued' AND j.status='retryable_failed'
                          AND j.last_error_class='provider_billing') AS next_provider_retry
                FROM verification_requests v LEFT JOIN jobs j ON j.id=v.job_id
            """)
            docs = q1(conn, """SELECT count(*) AS pending, min(fetched_at) AS oldest_pending
                                 FROM source_documents WHERE raw_meta->>'pending_extraction'='true' AND content_text IS NOT NULL""")
            return {"items": [dict(r, id=str(r["id"])) for r in rows], "counts": counts, "queue": queue, "documents": docs}

    # ------------------------------------------------------------ discovery
    @app.get("/api/admin/discovery")
    def discovery(request: Request, p: Principal = Depends(require("admin.costs"))):
        s = request.app.state.settings
        with tx(request) as conn:
            plans = qa(conn, "SELECT * FROM discovery_plans ORDER BY theme, geography")
            b = q1(conn, "SELECT cap, reserved, spent FROM provider_budgets WHERE provider='exa' AND kind='monthly' AND period=%s", (usage.period_now(),))
            return {"configured": bool(s.exa_api_key) if hasattr(s, "exa_api_key") else False, "key_present_on_api": bool(s.exa_api_key), "cap_usd": s.exa_monthly_cap_usd,
                    "interval_days": s.discovery_interval_days, "max_searches_per_run": s.discovery_max_searches_per_run,
                    "max_new_requests_per_run": s.discovery_max_new_requests_per_run, "budget": b, "plans": plans,
                    "note": "Hits are queued for verification against their original page. A search result is never evidence by itself."}

    @app.post("/api/admin/discovery/run")
    def discovery_run(body: RunIn, request: Request, p: Principal = Depends(require("admin.costs"))):
        with tx(request) as conn:
            from ..discovery.plans import sync_plans
            sync_plans(conn)
            jid, created = enqueue(conn, "discovery_run", {"plan_ids": body.plan_ids, "requested_by": p.email},
                                   idempotency_key=f"discovery-manual:{p.user_id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}", max_attempts=1)
            audit(conn, actor=p.email, operation="discovery_run_requested", record_type="job", record_id=jid, detail={"plans": body.plan_ids})
            return {"job_id": jid, "queued": created}

    @app.patch("/api/admin/discovery/plans/{pid}")
    def toggle_plan(pid: str, enabled: bool, request: Request, p: Principal = Depends(require("admin.costs"))):
        with tx(request) as conn:
            if not q1(conn, "UPDATE discovery_plans SET enabled=%s WHERE plan_id=%s RETURNING plan_id", (enabled, pid)):
                raise HTTPException(404, "not found")
            audit(conn, actor=p.email, operation="discovery_plan_toggle", record_type="discovery_plan", record_id=pid, detail={"enabled": enabled})
            return {"plan_id": pid, "enabled": enabled}

    # ------------------------------------------------------------ usage tracker
    @app.get("/api/admin/usage")
    def usage_summary(request: Request, p: Principal = Depends(require("admin.costs"))):
        s = request.app.state.settings
        now = datetime.now(timezone.utc)
        period = usage.period_now()
        days_in = calendar.monthrange(now.year, now.month)[1]
        with tx(request) as conn:
            budgets = {r["provider"]: r for r in qa(conn, "SELECT provider, cap, reserved, spent, unit FROM provider_budgets WHERE kind='monthly' AND period=%s", (period,))}
            months = qa(conn, "SELECT period, provider, count(*) AS calls, coalesce(sum(units),0) AS units, sum(cost_usd) AS cost_usd, coalesce(sum(tokens_in),0) AS tokens_in, coalesce(sum(tokens_out),0) AS tokens_out, "
                              "count(*) FILTER (WHERE status<>'ok') AS errors FROM provider_usage GROUP BY period, provider ORDER BY period DESC, provider LIMIT 24")
            daily = qa(conn, "SELECT (at AT TIME ZONE 'UTC')::date AS day, provider, coalesce(sum(units),0) AS units, sum(cost_usd) AS cost_usd, count(*) AS calls FROM provider_usage WHERE period=%s GROUP BY 1,2 ORDER BY 1", (period,))
            by_source = qa(conn, "SELECT coalesce(u.source_id,'(unattributed)') AS source_id, u.provider, count(*) AS calls, coalesce(sum(u.units),0) AS units, sum(u.cost_usd) AS cost_usd, s.display_name "
                                 "FROM provider_usage u LEFT JOIN sources s ON s.source_id=u.source_id WHERE u.period=%s GROUP BY 1,2,s.display_name ORDER BY units DESC LIMIT 30", (period,))
            by_plan = qa(conn, "SELECT u.plan_id, max(u.query) AS query, count(*) AS searches, sum(u.cost_usd) AS cost_usd, coalesce(sum((u.detail->>'results')::int),0) AS results, p.theme, p.geography "
                               "FROM provider_usage u LEFT JOIN discovery_plans p ON p.plan_id=u.plan_id WHERE u.period=%s AND u.provider='exa' AND u.plan_id IS NOT NULL GROUP BY u.plan_id, p.theme, p.geography ORDER BY searches DESC LIMIT 40", (period,))
            outcomes = qa(conn, "SELECT status, count(*) AS n FROM verification_requests GROUP BY status")
            accepted = q1(conn, "SELECT count(*) FILTER (WHERE status='verified') AS verified, count(*) AS total FROM verification_requests") or {}
            cost_per_verified = None
            tot = q1(conn, "SELECT sum(cost_usd) AS c FROM provider_usage WHERE cost_usd IS NOT NULL") or {}
            if accepted.get("verified") and tot.get("c") is not None:
                cost_per_verified = float(tot["c"]) / accepted["verified"]
        elapsed = max(now.day - 1 + now.hour / 24, 0.5)
        forecast: dict[str, Any] = {}
        for prov, b in budgets.items():
            spent = float(b["spent"])
            proj = spent / elapsed * days_in
            forecast[prov] = {"spent": spent, "projected_month_end": round(proj, 4), "cap": float(b["cap"]), "will_exceed": bool(b["cap"] and proj > float(b["cap"]))}
        pin, pout = s.anthropic_price_in_per_mtok, s.anthropic_price_out_per_mtok
        cur = next((m for m in months if m["period"] == period and m["provider"] == "anthropic"), None)
        mix_in = (float(cur["tokens_in"]) / max(float(cur["tokens_in"]) + float(cur["tokens_out"]), 1)) if cur and (cur["tokens_in"] or cur["tokens_out"]) else 0.85
        blended = (mix_in * pin + (1 - mix_in) * pout) / 1_000_000 if pin is not None and pout is not None else None
        cap_tokens = float(budgets["anthropic"]["cap"]) if "anthropic" in budgets else 0.0
        return {"period": period, "anthropic_cap_usd_estimate": round(cap_tokens * blended, 2) if blended is not None else None,
                "anthropic_usd_per_million_tokens_blended": round(blended * 1_000_000, 3) if blended is not None else None, "budgets": budgets, "months": months, "daily": daily, "by_source": by_source, "by_plan": by_plan, "forecast": forecast,
                "verification_outcomes": outcomes, "cost_per_verified_event_usd": cost_per_verified,
                "prices": {"anthropic_in_per_mtok": s.anthropic_price_in_per_mtok, "anthropic_out_per_mtok": s.anthropic_price_out_per_mtok},
                "notes": ["Exa cost is the amount Exa reports on each response.", "Model usage is counted in tokens and shown in dollars at list price (default: Claude Haiku 5.5, $0.10 in / $0.50 out per million tokens). Change with RI_ANTHROPIC_PRICE_IN_PER_MTOK and _OUT_."]}

    @app.get("/api/admin/usage/ledger")
    def usage_ledger(request: Request, provider: str = "", source: str = "", status: str = "", limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                     p: Principal = Depends(require("admin.costs"))):
        with tx(request) as conn:
            rows = qa(conn,
                "SELECT u.id, u.at, u.provider, u.operation, u.units, u.unit, u.tokens_in, u.tokens_out, u.cost_usd, u.cost_basis, u.source_id, u.plan_id, u.query, u.status, u.detail, "
                "o.display_name AS organization, d.canonical_url AS document_url, d.title AS document_title "
                "FROM provider_usage u LEFT JOIN organizations o ON o.id=u.organization_id LEFT JOIN source_documents d ON d.id=u.document_id "
                "WHERE (%s='' OR u.provider=%s) AND (%s='' OR u.source_id=%s) AND (%s='' OR u.status=%s) ORDER BY u.at DESC LIMIT %s OFFSET %s",
                (provider, provider, source, source, status, status, limit, offset))
            total = (q1(conn, "SELECT count(*) AS n FROM provider_usage WHERE (%s='' OR provider=%s) AND (%s='' OR source_id=%s) AND (%s='' OR status=%s)", (provider, provider, source, source, status, status)) or {"n": 0})["n"]
            return {"items": rows, "total": total}

    # ------------------------------------------------------------ source library
    @app.get("/api/admin/library")
    def library(request: Request, p: Principal = Depends(require("today.read"))):
        with tx(request) as conn:
            return {"items": library_items(conn)}


# ---------------------------------------------------------------------- what each source brought (the Library's "What it brought" line)

BROUGHT_DAYS = 30                       # the window of "pages read" and "findings"
LITTLE_MIN_PAGES = 20                   # a source that read this many pages in the window and found nothing about a target account ...
LITTLE_RATIO_PAGES = 100                # ... or at least this many pages with fewer than one such finding per 100 pages, "finds little for our accounts"
# Not judged by "findings about our accounts": tenders come from public buyers that are not accounts by nature; search, page checks and internal
# sources are used on request, not read in bulk.
NOT_JUDGED_FAMILIES = ("procurement", "discovery", "verification", "internal", "registry")
NOT_JUDGED_METHODS = ("internal", "search")
JOB_EVENT_TYPES = ("engineering_job_opened", "contractor_job_opened", "job_closed")
# A job finding names the board it came from (attrs.provider), not a stored page. Same mapping as Administration, Data health; any other
# career system "x" counts for the source "ats-x" when the registry has one.
JOB_PROVIDER_SOURCE = {"arbeitnow": "jobs-arbeitnow-de", "jobtech": "jobs-jobtech-se"}


def yield_note(src: dict[str, Any]) -> str | None:
    """One quiet label for a source row: 'paused_after_review' (paused by a source review, which keeps its plain reason as `review_note` in the
    source's config) or 'finds_little' (read many pages, found little about our target accounts). Nothing for a healthy source, a source nobody
    judges by this (tenders, search) or one with too little evidence. A pause by a person is just "Paused", with the reason when one was written."""
    status = src.get("status")
    if status == "paused":
        return "paused_after_review" if src.get("review_note") else None
    if status not in ("active", "degraded"):
        return None
    if src.get("source_family") in NOT_JUDGED_FAMILIES or src.get("collection_method") in NOT_JUDGED_METHODS:
        return None
    pages, target = int(src.get("pages_30d") or 0), int(src.get("findings_target_30d") or 0)
    if (pages >= LITTLE_MIN_PAGES and target == 0) or (pages >= LITTLE_RATIO_PAGES and target * 100 < pages):
        return "finds_little"
    return None


def library_items(conn, now: datetime | None = None) -> list[dict[str, Any]]:
    """Every source with its setup, health and what it has read and found. A fixed number of grouped queries however many sources there are
    (a correlated sub-query per source made this page take half a second on average and up to five seconds). Findings "about our accounts"
    use the one definition of a target account (``target_sql``). Every key the page used before is kept; the 30-day figures are additions."""
    from ..connections import monitoring_mode

    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=BROUGHT_DAYS)
    rows = qa(conn,
        "SELECT s.source_id, s.display_name, s.source_tier, s.source_type, s.source_family, s.provider, s.countries, s.signal_rule_ids, s.collection_method, s.auth_mode, "
        "s.retention_policy, s.licence_notes, s.poll_interval_s, s.status, s.cost_profile, s.canonical_domain, s.config->>'url' AS url, s.config->>'url_basis' AS url_basis, s.config->'validation' AS validation, "
        "s.config->>'review_note' AS review_note, "
        "h.state AS health, h.last_success_at, h.last_attempt_at, h.consecutive_failures, h.error_class, h.detail AS health_detail "
        "FROM sources s LEFT JOIN source_health h USING (source_id) ORDER BY s.source_tier, s.source_family, s.display_name")
    docs = {r["source_id"]: r for r in qa(conn, "SELECT source_id, count(*) AS documents, count(*) FILTER (WHERE fetched_at >= %s) AS pages_30d FROM source_documents GROUP BY source_id", (since,))}
    # findings per source (through the pages they were read from): all time, last 30 days, and last 30 days about a target account
    found = {r["source_id"]: r for r in qa(conn,
        f"WITH tgt AS MATERIALIZED (SELECT o.id FROM organizations o WHERE {target_sql('o')}) "  # noqa: S608
        "SELECT d.source_id, count(DISTINCT e.id) AS events, "
        "count(DISTINCT e.id) FILTER (WHERE e.first_observed_at >= %(since)s AND NOT e.is_historical_replay) AS findings_30d, "
        "count(DISTINCT e.id) FILTER (WHERE e.first_observed_at >= %(since)s AND NOT e.is_historical_replay AND t.id IS NOT NULL) AS findings_target_30d "
        "FROM source_documents d JOIN event_documents ed ON ed.document_id=d.id JOIN events e ON e.id=ed.event_id LEFT JOIN tgt t ON t.id=e.subject_org_id "
        "GROUP BY d.source_id", {"since": since})}
    # job findings name their board instead of a stored page; count the ones no page carries (so none is counted twice)
    jobs: dict[str, dict[str, int]] = {}
    for r in qa(conn,
        f"WITH tgt AS MATERIALIZED (SELECT o.id FROM organizations o WHERE {target_sql('o')}) "  # noqa: S608
        "SELECT e.attrs->>'provider' AS provider, count(*) AS findings_30d, count(*) FILTER (WHERE t.id IS NOT NULL) AS findings_target_30d "
        "FROM events e LEFT JOIN tgt t ON t.id=e.subject_org_id "
        "WHERE e.event_type = ANY(%(types)s) AND e.first_observed_at >= %(since)s AND NOT e.is_historical_replay "
        "AND NOT EXISTS (SELECT 1 FROM event_documents x WHERE x.event_id=e.id) GROUP BY 1", {"types": list(JOB_EVENT_TYPES), "since": since}):
        if r["provider"]:
            cur = jobs.setdefault(JOB_PROVIDER_SOURCE.get(r["provider"]) or f"ats-{r['provider']}".replace("lever_eu", "lever"), {"findings_30d": 0, "findings_target_30d": 0})
            cur["findings_30d"] += int(r["findings_30d"]); cur["findings_target_30d"] += int(r["findings_target_30d"])
    buying = {r["source_id"]: int(r["n"]) for r in qa(conn,
        "SELECT d.source_id, count(DISTINCT e.id) AS n FROM source_documents d JOIN event_documents ed ON ed.document_id=d.id "
        "JOIN events e ON e.id=ed.event_id JOIN signals bs ON bs.event_id=e.id AND bs.organization_id=e.subject_org_id "
        "WHERE e.verification_status='verified' AND e.proven AND e.lifecycle_status='active' AND NOT e.is_historical_replay "
        "AND coalesce(e.event_at,e.pub_at) >= %s AND bs.status='active' AND bs.verification_status='verified' AND (bs.expires_at IS NULL OR bs.expires_at>%s) "
        "GROUP BY d.source_id", (since, now))}
    spend = {r["source_id"]: r for r in qa(conn, "SELECT source_id, coalesce(sum(cost_usd),0) AS cost_usd_month, coalesce(sum(units),0) AS units_month "
                                                 "FROM provider_usage WHERE period=%s AND source_id IS NOT NULL GROUP BY source_id", (usage.period_now(),))}
    out: list[dict[str, Any]] = []
    for row in rows:
        sid = row["source_id"]
        d, f, j, sp = docs.get(sid) or {}, found.get(sid) or {}, jobs.get(sid) or {}, spend.get(sid) or {}
        pause = (row["review_note"] or (row["health_detail"] if row["health"] == "paused" else None)) if row["status"] == "paused" else None
        row.update(
            documents=int(d.get("documents") or 0), events=int(f.get("events") or 0), recent_buying_findings=buying.get(sid, 0),
            cost_usd_month=sp.get("cost_usd_month") or 0, units_month=sp.get("units_month") or 0,
            pages_30d=int(d.get("pages_30d") or 0), findings_30d=int(f.get("findings_30d") or 0) + j.get("findings_30d", 0),
            findings_target_30d=int(f.get("findings_target_30d") or 0) + j.get("findings_target_30d", 0),
            pause_reason=(pause or "").strip() or None, review_note=(row["review_note"] or "").strip() or None)
        row["yield_note"] = yield_note(row)
        row["monitoring_mode"] = monitoring_mode(row)
        out.append(row)
    return out


def register_pipeline(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    def age(ts) -> int | None:
        return int((datetime.now(timezone.utc) - ts).total_seconds()) if ts else None

    @app.get("/api/admin/pipeline")
    def pipeline(request: Request, p: Principal = Depends(require("admin.jobs"))):
        s = request.app.state.settings
        with tx(request) as conn:
            hb = {r["component"]: r for r in qa(conn, "SELECT component, at, detail FROM system_heartbeats")}
            jobs = {r["status"]: r["n"] for r in qa(conn, "SELECT status, count(*) AS n FROM jobs GROUP BY status")}
            oldest = q1(conn, "SELECT min(created_at) AS t FROM jobs WHERE status IN ('scheduled','retryable_failed') AND run_after <= now()") or {}
            last_job = q1(conn, "SELECT max(updated_at) AS t FROM jobs WHERE status='completed'") or {}
            src = q1(conn, "SELECT count(*) FILTER (WHERE status='active') AS active, count(*) FILTER (WHERE status='pending_connector_validation' AND collection_method<>'internal') AS pending, "
                           "count(*) FILTER (WHERE collection_method<>'internal') AS total, count(*) FILTER (WHERE config->>'url' IS NOT NULL AND status<>'active' AND collection_method<>'internal') AS ready_to_test FROM sources") or {}
            failing = qa(conn, "SELECT s.display_name, h.error_class, h.consecutive_failures FROM sources s JOIN source_health h USING (source_id) WHERE s.status IN ('active','degraded') AND h.state IN ('degraded','failed','blocked') LIMIT 10")
            disc = q1(conn, "SELECT max(last_run_at) AS last_run, count(*) FILTER (WHERE enabled AND (last_run_at IS NULL OR last_run_at < now() - make_interval(days => %s))) AS due, count(*) AS plans, "
                            "count(*) FILTER (WHERE theme='account_watch') AS accounts FROM discovery_plans", (s.discovery_interval_days,)) or {}
            ver = {r["status"]: r["n"] for r in qa(conn, "SELECT status, count(*) AS n FROM verification_requests GROUP BY status")}
            ev = q1(conn, "SELECT count(*) AS n, count(*) FILTER (WHERE first_observed_at > now() - interval '7 days') AS week FROM events") or {}
            ini = q1(conn, "SELECT count(*) AS n FROM initiatives WHERE status='active'") or {}
            hyp = q1(conn, "SELECT count(*) AS n, count(*) FILTER (WHERE last_checked_at IS NOT NULL) AS scored FROM opportunity_hypotheses WHERE state<>'superseded'") or {}
            dig = q1(conn, "SELECT max(created_at) AS t FROM notifications_sent WHERE status='sent'") or {}
            imp = q1(conn, "SELECT workbook_key, status, finished_at FROM import_runs WHERE status IN ('completed','paused_schema_drift') ORDER BY finished_at DESC NULLS LAST LIMIT 1")
            bud = {r["provider"]: r for r in qa(conn, "SELECT provider, cap, spent, reserved FROM provider_budgets WHERE kind='monthly' AND period=%s", (usage.period_now(),))}
            fn = q1(conn, "SELECT (SELECT count(*) FROM source_documents WHERE fetched_at > now() - interval '7 days') AS read, "
                          "(SELECT count(*) FROM source_documents WHERE fetched_at > now() - interval '7 days' AND coalesce(raw_meta->>'triage','') <> 'skipped_not_relevant') AS relevant, "
                          "(SELECT count(*) FROM events WHERE first_observed_at > now() - interval '7 days') AS events, "
                          "(SELECT count(*) FROM events WHERE first_observed_at > now() - interval '7 days' AND proven) AS proven, "
                          "(SELECT count(*) FROM events WHERE first_observed_at > now() - interval '7 days' AND subject_org_id IS NOT NULL) AS linked, "
                          "(SELECT count(*) FROM signals s JOIN events e ON e.id=s.event_id WHERE e.first_observed_at > now() - interval '7 days') AS signals, "
                          "(SELECT count(*) FROM opportunity_hypotheses WHERE state<>'superseded') AS cards, "
                          "(SELECT count(*) FROM organizations) AS accounts, (SELECT count(*) FROM new_company_candidates WHERE status='pending_qualification') AS candidates") or {}
        sch, wrk = hb.get("scheduler"), hb.get("worker")
        funnel = [{"key": k, "label": l, "n": int(fn.get(k) or 0), "note": n} for k, l, n in [
            ("read", "Pages and items read", "Everything the sources returned in the last 7 days."),
            ("relevant", "Relevant to our work", "Passed the cheap relevance check, so the model reads them."),
            ("events", "Events extracted", "Atomic facts, each with a quote from the page."),
            ("proven", "Proven", "Quoted or official, and not stale. True whether or not the company is one of ours."),
            ("linked", "Linked to an account", "About a company in our database. Without an account nothing can become a signal."),
            ("signals", "Became a signal", "Matched one of the active rules."),
            ("cards", "Opportunity cards", "All current cards, from every signal.")]]

        def fresh(h, limit):
            return "never" if not h else "ok" if age(h["at"]) < limit else "stale"
        digest_cfg = bool(s.slack_webhook_url)
        stages = [
            {"key": "scheduler", "label": "Scheduler", "mode": "automatic", "state": fresh(sch, 900), "last": sch["at"] if sch else None, "note": "Runs every 5 minutes. Decides which sources are due, queues the daily discovery run, expires old signals and re-scores moved programmes."},
            {"key": "worker", "label": "Worker", "mode": "automatic", "state": fresh(wrk, 300), "last": wrk["at"] if wrk else None, "note": f"Takes jobs from the queue within seconds. Queue: {jobs.get('scheduled', 0)} waiting, {jobs.get('retryable_failed', 0)} retrying, {jobs.get('permanent_failed', 0)} failed. Last job finished {('%d min ago' % (age(last_job.get('t')) // 60)) if last_job.get('t') else 'never'}."},
            {"key": "sources", "label": "Source watching", "mode": "automatic once a source is live", "state": "ok" if src.get("active") else "waiting",
             "note": f"{src.get('active', 0)} of {src.get('total', 0)} sources are live. {src.get('ready_to_test', 0)} have an address and are waiting for you to test and activate them in Administration, Sources.", "detail": [dict(f) for f in failing]},
            {"key": "discovery", "label": "Exa discovery", "mode": "automatic", "state": "ok" if (s.exa_api_key and s.exa_monthly_cap_usd > 0) else "off", "last": disc.get("last_run"),
             "note": f"{disc.get('plans', 0)} search plans ({disc.get('accounts', 0)} watch one priority account each, weekly), {disc.get('due', 0)} due now. Runs once a day within the monthly cap of ${s.exa_monthly_cap_usd:g}. Hits are only leads: each is queued for verification."},
            {"key": "verification", "label": "Verification", "mode": "automatic after a trigger", "state": "ok",
             "note": "Fetches the original page, extracts quoted events, keeps only what the page proves. Triggered by a seller, or by a discovery hit. " + (f"{ver.get('waiting_budget', 0)} waiting for model budget." if ver.get("waiting_budget") else ""), "counts": ver},
            {"key": "opportunities", "label": "Events to opportunities", "mode": "automatic", "state": "ok",
             "note": f"{ev.get('n', 0)} verified events ({ev.get('week', 0)} this week), {ini.get('n', 0)} active programmes, {hyp.get('scored', 0)} scored opportunities. Each verified event updates its programme and re-scores it with no manual step."},
            {"key": "digest", "label": "Daily digest", "mode": "automatic" if digest_cfg else "off", "state": "ok" if digest_cfg else "off", "last": dig.get("t"),
             "note": "Sent to Slack when something material changes; never repeats an unchanged item." if digest_cfg else "Not switched on yet. It needs a Slack webhook added to the scheduler."},
            {"key": "sheets", "label": "Sheet import", "mode": "manual", "state": "ok" if imp else "waiting", "last": imp["finished_at"] if imp else None,
             "note": "You upload the .xlsx exports in Administration, Sources & data, Workbooks. Hourly automatic sync needs a read-only Google service account."},
            {"key": "pipedrive", "label": "Pipedrive", "mode": "off", "state": "off", "note": "Not connected. Without it there is no ownership data, so every account is open to every seller, and no CRM changes can be made."},
        ]
        nexts = []
        if not src.get("active"):
            nexts.append("Test and activate the newsroom and feed sources (Administration, Sources). Until one is live, only discovery and manual verification feed the platform.")
        if not imp:
            nexts.append("Upload the two workbooks (Administration, Sources & data, Workbooks).")
        if not wrk or fresh(wrk, 300) != "ok":
            nexts.append("The worker has not checked in recently. Check the worker service on Render.")
        if not digest_cfg:
            nexts.append("Optional: set the Slack webhook to get the daily digest.")
        return {"stages": stages, "queue": jobs, "oldest_waiting_s": age(oldest.get("t")), "budgets": {k: {"cap": float(v["cap"]), "used": float(v["spent"]) + float(v["reserved"])} for k, v in bud.items()}, "next_steps": nexts, "funnel": funnel, "accounts": int(fn.get("accounts") or 0), "candidates": int(fn.get("candidates") or 0),
                "lanes": {"background": hb.get("worker") and {"at": hb["worker"]["at"]}, "interactive": hb.get("worker-interactive") and {"at": hb["worker-interactive"]["at"]}}}


VIEWS = {
    "accounts": "(e.verification_status='verified' AND e.subject_org_id IS NOT NULL AND e.lane IS DISTINCT FROM 'public' AND e.event_type !~ '^procurement_' AND o.entity_type<>'public_body')",
    "market": "(e.proven AND e.subject_org_id IS NULL AND e.lane='private' AND e.market_tier='core')",
    "rest": "(e.proven AND e.subject_org_id IS NULL AND e.lane='private' AND e.market_tier IN ('secondary','unknown'))",
    "public": "(e.proven AND e.lane='public' AND e.market_tier IN ('core','secondary'))",
}


FINDING_DISMISS_REASONS = ("not_relevant", "wrong_entity", "stale", "duplicate")
_HAPPENED = "coalesce(e.event_at,e.pub_at,e.first_observed_at)"


class FindingDismissIn(BaseModel):
    reason: str = Field(default="not_relevant", pattern="^(" + "|".join(FINDING_DISMISS_REASONS) + ")$")


def finding_dismissals(conn, p: Principal) -> dict[str, str]:
    """Findings this person marked "not relevant" (event id -> reason). The latest answer wins, so "show it again" undoes it."""
    rows = qa(conn, "SELECT DISTINCT ON (target_id) target_id, judgement, reason FROM feedback WHERE target_type='finding' AND reviewer=%s "
                    "ORDER BY target_id, created_at DESC, id", (p.email,))
    return {r["target_id"]: r["reason"] or "not_relevant" for r in rows if r["judgement"] == "not_relevant"}


def finding_kinds(conn, p: Principal, *, days: int, region: str, qualification: str, organizations: list[str] | None,
                  hidden: list[str] | None = None) -> list[dict]:
    """The buying signals behind the findings in this window, most frequent first, for the filter chips on All findings. It counts the
    same checked, active news about the same companies as the list (one per story), leaving out tenders, which have their own page,
    and the findings in `hidden` (the ones the person marked not relevant)."""
    from ..events.classify import non_engineering_title_sql_pattern
    from ..feed import STORY_KEY_SQL
    from ..findings import _EVIDENCE, _SIGNAL
    from ..market import REGIONS
    where, args = org_scope_sql(p, "o.id")
    members = {key: sorted(countries) for key, _, countries in REGIONS if key != "us"}
    region_clause, region_args = "TRUE", []
    if region == "international":
        region_clause, region_args = "NOT (o.country_code=ANY(%s))", [sorted({c for countries in members.values() for c in countries})]
    elif region:
        region_clause, region_args = "o.country_code=ANY(%s)", [members[region]]
    target, review = target_sql(), target_sql(review=True)
    eligible = review if qualification == "review" else f"({target} OR {review})" if qualification == "all" else target
    rows = qa(conn, f"""
        SELECT s.rule_id, coalesce(max(ls.signal), s.rule_id) AS name, count(DISTINCT {STORY_KEY_SQL}) AS findings, max({_HAPPENED}) AS latest
        FROM events e JOIN organizations o ON o.id=e.subject_org_id JOIN signals s ON {_SIGNAL}
        LEFT JOIN legacy_signals ls ON ls.id=s.rule_id
        WHERE ({where}) AND {eligible} AND ({region_clause}) AND (%s::uuid[] IS NULL OR o.id=ANY(%s::uuid[]))
        AND e.verification_status='verified' AND e.lifecycle_status='active' AND NOT e.is_historical_replay
        AND e.lane IS DISTINCT FROM 'public' AND e.event_type NOT LIKE 'procurement_%%'
        AND e.event_type NOT IN ('crm_buyer_response','relationship_changed') AND s.rule_id NOT LIKE 'NEG-%%'
        AND (e.event_type NOT IN ('engineering_job_opened','contractor_job_opened')
             OR (coalesce(e.attrs->>'title','') !~* %s AND (NOT (e.attrs ? 'job_id') OR EXISTS (SELECT 1 FROM job_postings j
                 WHERE j.provider=e.attrs->>'provider' AND j.board=e.attrs->>'board' AND j.job_id=e.attrs->>'job_id' AND j.open_status='open'))))
        AND NOT (e.id=ANY(%s::uuid[]))
        AND {_HAPPENED} > now() - make_interval(days => %s) AND {_HAPPENED} <= now() AND {_EVIDENCE}
        GROUP BY s.rule_id ORDER BY findings DESC, s.rule_id
        """, [*args, *region_args, organizations, organizations, non_engineering_title_sql_pattern(), hidden or [], days])       # noqa: S608
    return [{"rule_id": r["rule_id"], "name": r["name"], "findings": r["findings"], "latest": r["latest"]} for r in rows]


def register_feed(app: FastAPI) -> None:
    from .. import feed as fd
    from ..market import sector_sql
    from ..readmodel import evidence_for
    from ..sheets.reconcile import icp_band, market_focus

    def tx(request: Request):
        return request.app.state.db.tx()

    @app.get("/api/findings")
    def verified_findings(request: Request, scope: str = Query("archive", pattern="^(today|archive|account)$"),
                          organization: UUID | None = None, event: UUID | None = None, q: str = Query("", max_length=120),
                          days: int = Query(30, ge=1, le=365), page: int = Query(1, ge=1, le=1000),
                          limit: int = Query(20, ge=1, le=50), kind: str = Query("", pattern="^(|tender|signal)$"),
                          region: str = Query("", pattern="^(|uk|dach|nordics|poland|europe|international)$"),
                          qualification: str = Query("target", pattern="^(target|review|all)$"),
                          rule: str = Query("", pattern=r"^(|[A-Z]{3}-\d{2})$"),
                          watched: bool = False, hidden: bool = False,
                          p: Principal = Depends(principal_dep)):
        """Checked news about target companies, newest first. On Today and All findings, findings the person marked "not relevant" are
        left out unless `hidden` is set (each item says `dismissed`); `watched` keeps the companies on their watchlist. All findings
        (archive, no company) also gets `kinds`, the buying signals behind the findings in the window, for its filter chips."""
        from ..findings import account_leads, findings, rfp_research

        permission = {"today": "today.read", "archive": "explorer.read", "account": "accounts.read"}[scope]
        if not p.can(permission):
            raise HTTPException(403, f"missing permission {permission}")
        if (scope == "account") != (organization is not None):
            raise HTTPException(422, "Select a company for the account view.")
        with tx(request) as conn:
            leads = []
            if organization is not None:
                where, args = org_scope_sql(p)
                eligible = account_workspace_sql(research=p.can("research.request"))
                if not q1(conn, f"SELECT o.id FROM organizations o WHERE o.id=%s AND ({where}) AND {eligible}", [organization, *args]):
                    raise HTTPException(404, "not found")
                leads = account_leads(conn, organization)
            elif scope == "archive" and p.can("research.request"):
                leads = rfp_research(conn, p)
            qualify = "target" if scope == "today" else "all" if organization or event else qualification
            mine = [str(r["organization_id"]) for r in qa(conn, "SELECT organization_id FROM watchlist WHERE user_email=%s", (p.email,))]
            only_watched = watched and organization is None and event is None
            if only_watched:
                # the watchlist holds at most 100 companies: read each one that has checked news in the window, then merge by date
                active = [str(r["organization_id"]) for r in qa(conn, f"""SELECT DISTINCT e.subject_org_id AS organization_id FROM events e
                          WHERE e.subject_org_id=ANY(%s::uuid[]) AND e.verification_status='verified' AND e.lifecycle_status='active'
                          AND NOT e.is_historical_replay AND {_HAPPENED} > now() - make_interval(days => %s)""", (mine, days))]   # noqa: S608
                merged: list[dict] = []
                for org in active:
                    merged += findings(conn, p, scope=scope, days=days, limit=page * limit, page=1, kind=kind, region=region,
                                       organization_id=UUID(org), search=q, rule=rule, qualification=qualify)
                merged.sort(key=lambda it: str(it["id"]))
                merged.sort(key=lambda it: it["happened"], reverse=True)
                items = merged[(page - 1) * limit:page * limit + 1]
            else:
                items = findings(conn, p, scope=scope, days=365 if event else days, limit=limit, page=page, kind=kind, region=region,
                                 organization_id=organization, event=event, search=q, rule=rule, qualification=qualify)
            has_more = len(items) > limit
            items = items[:limit]
            dismissed = finding_dismissals(conn, p) if scope != "account" else {}
            watching = set(mine)
            for it in items:
                it["watched"] = str(it["organization"]["id"]) in watching
                it["dismissed"] = next((dismissed[str(e)] for e in it["event_ids"] if str(e) in dismissed), None)
            if scope != "account" and not hidden and event is None:
                items = [it for it in items if not it["dismissed"]]
            out = {"items": items, "page": page, "has_more": has_more, "leads": leads, "coverage": coverage_health(conn, p),
                   "watchlist": len(mine), "hidden_count": len(dismissed)}
            if scope == "archive" and organization is None:
                out["kinds"] = finding_kinds(conn, p, days=days, region=region, qualification=qualify, organizations=mine if only_watched else None,
                                             hidden=None if hidden else list(dismissed))
            return out

    @app.post("/api/findings/{event_id}/dismiss")
    def dismiss_finding(event_id: UUID, body: FindingDismissIn, request: Request, p: Principal = Depends(principal_dep)):
        """Mark a finding "not relevant" for this person: it leaves their Today and All findings. Kept as feedback, so it also shows
        which sources and signals bring noise."""
        return _finding_answer(request, p, event_id, "not_relevant", body.reason)

    @app.delete("/api/findings/{event_id}/dismiss")
    def restore_finding(event_id: UUID, request: Request, p: Principal = Depends(principal_dep)):
        """Undo "not relevant": the finding shows again."""
        return _finding_answer(request, p, event_id, "restored", None)

    def _finding_answer(request: Request, p: Principal, event_id: UUID, judgement: str, reason: str | None) -> dict[str, Any]:
        from ..db import jb
        if not (p.can("explorer.read") or p.can("today.read")):
            raise HTTPException(403, "missing permission explorer.read")
        with tx(request) as conn:
            where, args = org_scope_sql(p, "o.id")
            found = q1(conn, f"SELECT e.id, e.subject_org_id FROM events e JOIN organizations o ON o.id=e.subject_org_id WHERE e.id=%s AND ({where})",   # noqa: S608
                       [event_id, *args])
            if not found:
                raise HTTPException(404, "not found")       # 404, not 403: do not reveal findings outside the person's accounts
            conn.execute("INSERT INTO feedback(target_type, target_id, reviewer, judgement, reason) VALUES ('finding',%s,%s,%s,%s)",
                         (str(event_id), p.email, judgement, reason))
            conn.execute("INSERT INTO product_events(user_id, event, organization_id, detail) VALUES (%s,%s,%s,%s)",
                         (p.user_id, "finding_dismissed" if judgement == "not_relevant" else "finding_restored", found["subject_org_id"],
                          jb({"event_id": str(event_id), "reason": reason})))
        return {"dismissed": judgement == "not_relevant", "reason": reason}

    @app.get("/api/tenders")
    def tenders(request: Request, q: str = Query("", max_length=120), region: str = Query("", pattern="^(|core|uk|dach|nordics|poland|europe|international)$"),
                buyer: str = Query("", pattern="^(|finance|public)$"), min_days: int = Query(3, ge=0, le=60), page: int = Query(1, ge=1, le=200), limit: int = Query(30, ge=1, le=100),
                fit: str = Query("", pattern="^(|strong|possible)$"),
                work_type: str = Query("", pattern="^(|software_development|maintenance_support|consulting|licences_products|infrastructure_hardware|data_ai|testing_qa|other)$"),
                max_days: int = Query(0, ge=0, le=365), min_value: float = Query(0, ge=0, le=1e12), sort: str = Query("deadline", pattern="^(deadline|fit|value|newest)$"),
                sector: str = Query("", pattern="^(|icp|finance|banking|financial_services|other_icp|not_icp)$"),
                group: str = Query("", pattern="^(|icp|all|banks|insurers|payments|lending|markets|other)$"),
                country: str = Query("", pattern="^(|[A-Z]{2})$"), status: str = Query("", pattern="^(|new|changed)$"),
                p: Principal = Depends(principal_dep)):
        """Open tenders for software work from every official source, banks first, then other financial services, the other ICP sectors and
        everyone else; soonest deadline first within each. `sector=icp` keeps our ICP sectors (the app's default view); no sector means every
        buyer. `region=core` keeps our markets (UK, DACH, Nordics, Poland); no region means every region. Each ICP tender carries its plain
        English and Polish summary once the summary job has written it; `q` also searches the summaries. Values are also given in euros
        (`value_eur`, fixed rates in `eur_rates`); `min_value` is in euros. `fit`, `work_type`, `max_days`, `min_value`, `sort` and `sector`
        are explained in ri.tenders.open_tenders.
        `group` (the page's buyer chips) replaces `sector`: icp (banks, insurers, payments, lenders, market institutions), all, or one chip;
        with it come `country`, `status` (new, changed), CPV codes in `q`, and a live count for every chip and filter (ri.tender_sector.icp_view)."""
        from ..tender_sector import icp_view
        from ..tender_summary import count_pending, job_waiting
        from ..tenders import TENDER_SOURCES, open_tenders
        if not (p.can("today.read") or p.can("explorer.read")):
            raise HTTPException(403, "missing permission today.read")
        with tx(request) as conn:
            if group:
                out = icp_view(conn, p, q=q, view=group, region=region, country=country, min_days=min_days, max_days=max_days, min_value=min_value,
                               fit=fit, work_type=work_type, status=status, sort=sort, page=page, limit=limit)
            else:
                out = open_tenders(conn, p, q=q, region=region, buyer=buyer, min_days=min_days, limit=limit, offset=(page - 1) * limit,
                                   fit=fit, work_type=work_type, max_days=max_days, min_value=min_value, sort=sort, sector=sector)
            summaries = {"waiting": count_pending(conn), "running": job_waiting(conn)}
            sources = qa(conn, "SELECT s.source_id, s.display_name, s.status, h.last_success_at FROM sources s LEFT JOIN source_health h USING (source_id) "
                               "WHERE s.source_family='procurement' AND s.collection_method='official_api' AND s.source_id = ANY(%s) ORDER BY s.display_name",
                         (list(TENDER_SOURCES),))
            # a check asked for in the last half hour that has not finished yet (an older one is not shown: no worker may be picking it up)
            refreshing = q1(conn, "SELECT 1 FROM jobs WHERE kind='source_run' AND status = ANY(%s) AND payload->>'source_id' = ANY(%s) "
                                  "AND created_at > now() - interval '30 minutes' LIMIT 1", (list(RUN_WAITING), list(TENDER_SOURCES))) is not None
        last = max((s["last_success_at"] for s in sources if s["last_success_at"]), default=None)
        return {**out, "page": page, "sources": sources, "last_updated": last, "refreshing": refreshing, "summaries": summaries}

    @app.post("/api/tenders/summarise")
    def summarise_tenders(request: Request, p: Principal = Depends(require("admin.sources"))):
        """Write the plain-language summaries for waiting tenders now instead of waiting for the two-hourly run. The run is capped and
        budget-gated like the scheduled one; while a run is waiting or running nothing new is queued, and repeated clicks within the same
        five minutes queue nothing new."""
        from ..tender_sector import classify_open
        from ..tender_summary import RUN_CAP, WAITING_JOB, count_pending
        now = datetime.now(timezone.utc)
        window = f"{now:%Y%m%d%H}{now.minute // 5:02d}"
        with tx(request) as conn:
            classify_open(conn)             # only ICP notices (and the ones the rules cannot tell) wait for a summary
            waiting = count_pending(conn)
            running = q1(conn, "SELECT id FROM jobs WHERE kind='tender_summaries' AND status = ANY(%s) ORDER BY created_at LIMIT 1", (list(WAITING_JOB),))
            if running:
                jid, created = str(running["id"]), False
            elif waiting == 0:
                jid, created = None, False
            else:
                jid, created = enqueue(conn, "tender_summaries", {"cap": RUN_CAP, "requested_by": p.email}, idempotency_key=f"tender-summaries:manual:{window}",
                                       max_attempts=2, correlation_id=f"tenders-summarise-{window}")
            audit(conn, actor=p.email, operation="tender_summaries_requested", record_type="source_family", record_id="procurement",
                  detail={"queued": created, "job_id": jid, "waiting": waiting})
        return {"queued": created, "already_running": bool(running) or (jid is not None and not created), "job_id": jid, "waiting": waiting, "cap": RUN_CAP}

    @app.post("/api/tenders/refresh")
    def refresh_tenders(request: Request, p: Principal = Depends(require("admin.sources"))):
        """Check the switched-on tender sources now instead of waiting for the daily run. A source whose check is already waiting or
        running is not queued again, and repeated clicks within the same five minutes queue nothing new."""
        from ..scheduler import SCHEDULED_STATUSES
        from ..tenders import TENDER_SOURCES
        now = datetime.now(timezone.utc)
        window = f"{now:%Y%m%d%H}{now.minute // 5:02d}"
        with tx(request) as conn:
            active = [r["source_id"] for r in qa(conn, "SELECT source_id FROM sources WHERE source_id = ANY(%s) AND status = ANY(%s) ORDER BY source_id",
                                                 (list(TENDER_SOURCES), list(SCHEDULED_STATUSES)))]
            waiting = {r["source_id"] for r in qa(conn, "SELECT DISTINCT payload->>'source_id' AS source_id FROM jobs WHERE kind='source_run' AND status = ANY(%s) "
                                                        "AND payload->>'source_id' = ANY(%s)", (list(RUN_WAITING), active))}
            queued, jobs = [], []
            for sid in active:
                if sid in waiting:
                    continue
                jid, created = enqueue(conn, "source_run", {"source_id": sid}, idempotency_key=f"source_run:{sid}:manual:{window}", correlation_id=f"tenders-refresh-{window}")
                if created:
                    queued.append(sid)
                    jobs.append(jid)
                else:
                    waiting.add(sid)
            audit(conn, actor=p.email, operation="tenders_refresh_requested", record_type="source_family", record_id="procurement",
                  detail={"queued": queued, "already_waiting": sorted(waiting), "job_ids": jobs})
        return {"queued": queued, "already_waiting": sorted(waiting), "not_switched_on": [s for s in TENDER_SOURCES if s not in active]}

    @app.get("/api/feed")
    def market_feed(request: Request, q: str = Query("", max_length=120), days: int = Query(60, ge=1, le=365), play: str = Query("", max_length=8), rule: str = Query("", max_length=8),
                    market: str = Query("", pattern="^(|core)$"), accounts_only: bool = True, view: str = Query("accounts", pattern="^(accounts|market|rest|public)$"), limit: int = Query(40, ge=1, le=100), fit_only: bool = False, focus: str = Query("", pattern="^(|finance|other|unclassified)$"), target: bool = False, p: Principal = Depends(require("explorer.read"))):
        from .search import all_like, tokens
        toks = tokens(q)
        rule_ids = fd.rules_for_play(play) if play else ([rule] if rule else [])
        sector = sector_sql("o.segment", "CASE WHEN e.proven THEN e.attrs->>'role_of_subject' END")
        active = "s.status='active' AND s.verification_status='verified' AND (s.expires_at IS NULL OR s.expires_at>now()) AND s.organization_id=e.subject_org_id"
        with tx(request) as conn:
            ew, ep = org_scope_sql(p, "e.subject_org_id")
            eligibility = f"{target_sql()} AND e.lane <> 'public'" if target else "TRUE"
            tw, tp = all_like("(e.summary || ' ' || coalesce(o.display_name,''))", toks) if toks else ("TRUE", [])
            rows = qa(conn,
                f"WITH candidates AS (SELECT e.id, {fd.STORY_KEY_SQL} AS story_key, "
                f"coalesce(e.event_at, e.pub_at, e.first_observed_at) AS happened FROM events e LEFT JOIN organizations o ON o.id=e.subject_org_id "
                f"WHERE {VIEWS[view]} AND {eligibility} AND e.lifecycle_status='active' AND NOT e.is_historical_replay AND coalesce(e.event_at, e.pub_at, e.first_observed_at) > now() - make_interval(days => %s) "
                f"AND (e.subject_org_id IS NULL OR ({ew})) AND ({tw}) AND (NOT %s OR e.subject_org_id IS NOT NULL) "
                f"AND (%s='' OR %s='public' OR ({sector})=%s) "
                f"AND (%s::text[] IS NULL OR EXISTS (SELECT 1 FROM signals s WHERE s.event_id=e.id AND ({active}) AND s.rule_id = ANY(%s))) "
                f"AND (NOT %s OR e.attrs->'fit'->>'level' IN ('strong','possible'))), "
                f"stories AS (SELECT (array_agg(id ORDER BY happened ASC, id))[1] AS representative, array_agg(id) AS event_ids "
                f"FROM candidates GROUP BY story_key) "
                f"SELECT st.event_ids, e.id, e.event_type, e.summary, e.event_at, e.pub_at, e.first_observed_at, e.verification_status, e.is_historical_replay, o.id AS org_id, o.display_name, o.country_code, o.segment, "   # noqa: S608
                f"o.employees_estimate, o.employees_uncertain, o.in_master_list, e.proven, e.market_tier, e.lane, e.market_country, {sector} AS sector, "
                f"coalesce((SELECT pn.buyer_name FROM event_documents ed JOIN procurement_notices pn ON pn.document_id=ed.document_id WHERE ed.event_id=e.id LIMIT 1), e.subject_name_raw) AS buyer_name, "
                f"(SELECT coalesce(jsonb_agg(jsonb_build_object('rule_id', s.rule_id, 'demand_tier', s.demand_tier, 'strength', s.strength, 'status', s.status)), '[]'::jsonb) FROM signals s WHERE s.event_id=ANY(st.event_ids) AND ({active})) AS sigs, "
                f"(SELECT coalesce(array_agg(DISTINCT ec.claim_id), '{{}}') FROM event_claims ec WHERE ec.event_id=ANY(st.event_ids)) AS claim_ids, "
                f"(SELECT jsonb_build_object('id', i.id, 'title', i.title, 'stage', i.stage) FROM initiative_events ie JOIN initiatives i ON i.id=ie.initiative_id WHERE ie.event_id=e.id LIMIT 1) AS programme, "
                f"coalesce(e.event_at, e.pub_at, e.first_observed_at) AS happened, e.attrs->'fit' AS ai_fit, e.attrs->>'language' AS lang, e.attrs->>'summary_en' AS summary_en "
                f"FROM stories st JOIN events e ON e.id=st.representative LEFT JOIN organizations o ON o.id=e.subject_org_id "
                f"ORDER BY happened DESC NULLS LAST LIMIT %s", [days, *ep, *tp, accounts_only and view == "accounts", focus, view, focus, rule_ids or None, rule_ids or None, fit_only, limit * 2])
            items = []
            for r in rows:
                mk = market_focus(r["country_code"], r["segment"])
                if market == "core" and mk["fit"] != "core":
                    continue
                by_rule = {}
                for signal in sorted(r["sigs"], key=lambda x: (x["demand_tier"], -x["strength"])):
                    by_rule.setdefault(signal["rule_id"], signal)
                sig_ids = list(by_rule)
                ev = evidence_for(conn, list(r["claim_ids"]), limit=2)
                sources = story_sources(conn, r["event_ids"])
                items.append({"id": str(r["id"]), "type": r["event_type"], "summary": r["summary"], "happened": r["happened"], "event_at": r["event_at"], "published": r["pub_at"],
                              "first_seen": r["first_observed_at"],
                              "organization": {"id": str(r["org_id"]), "name": r["display_name"], "country": r["country_code"], "segment": r["segment"], "employees": r["employees_estimate"],
                                               "in_master_list": r["in_master_list"], "icp": icp_band(r["employees_estimate"], bool(r["employees_uncertain"]), r["segment"])["band"], "market": mk} if r["org_id"] else None,
                              "signals": [{**x, "name": fd._load()["rules"].get(x["rule_id"], {}).get("id")} for x in by_rule.values()], "fit": fd.fit_for(sig_ids), "questions": fd.rule_questions(sig_ids),
                              "evidence": ev, "story_sources": sources, "programme": r["programme"],
                              "contact_hint": fd.contact_hint(r["event_type"], sig_ids, buyer_unclear=r["org_id"] is None), "why": r["ai_fit"], "language": r["lang"], "summary_en": r["summary_en"],
                              "scope": {"sector": r["sector"], "proven": r["proven"], "linked": bool(r["org_id"]), "tier": r["market_tier"], "lane": r["lane"], "country": r["market_country"], "buyer": r["buyer_name"]}})
                if len(items) >= limit:
                    break
            names = {x["id"]: x["signal"] for x in qa(conn, "SELECT id, signal FROM legacy_signals")}
            for it in items:
                for s in it["signals"]:
                    s["name"] = names.get(s["rule_id"], s["rule_id"])
            cov = coverage_health(conn, p)
            vw, vp = org_scope_sql(p, "o.id")
            # leads about an account follow the account's row scope; market-wide leads (no account) are shared
            lead_scope = f"{target_sql()} AND ({vw})" if target else f"(v.organization_id IS NULL OR ({vw}))"
            leads = qa(conn, "SELECT v.url, v.claimed_text, v.status, v.created_at, v.plan_id, v.result FROM verification_requests v "
                       f"LEFT JOIN organizations o ON o.id=v.organization_id WHERE {lead_scope} "
                       "AND v.origin IN ('exa_discovery','seller_miss') AND v.status IN ('queued','fetching','extracting','waiting_budget','no_event','blocked','failed') "
                       "ORDER BY v.created_at DESC LIMIT 12", vp)
            totals = counts = {}
            if not target:
                totals = q1(conn, "SELECT count(*) FILTER (WHERE verification_status='verified') AS verified, count(*) FILTER (WHERE verification_status<>'verified') AS unverified, "
                                  "count(*) FILTER (WHERE subject_org_id IS NULL AND verification_status='verified' AND coalesce(event_at, pub_at, first_observed_at) > now() - make_interval(days => %s)) AS market_wide FROM events", (days,)) or {}
                counts = q1(conn, "SELECT " + ", ".join(
                    f"count(DISTINCT {fd.STORY_KEY_SQL}) FILTER (WHERE {w}" + (f" AND (%s='' OR ({sector})=%s)" if k != "public" else "") + f") AS {k}"
                    for k, w in VIEWS.items()
                ) + f" FROM events e LEFT JOIN organizations o ON o.id=e.subject_org_id WHERE e.lifecycle_status='active' AND NOT e.is_historical_replay "
                    f"AND coalesce(e.event_at,e.pub_at,e.first_observed_at)>now()-make_interval(days=>%s) "
                    f"AND (e.subject_org_id IS NULL OR ({ew}))", [focus, focus] * (len(VIEWS)-1) + [days, *ep]) or {}
        return {"view": view, "view_counts": counts, "items": items, "count": len(items), "leads": [dict(x, reason=(x["result"] or {}).get("reason")) for x in leads], "coverage": cov, "totals": totals,
                "plays": [{"id": i, "name": n} for i, n in fd.play_ids()],
                "note": "Findings include claims checked against collected source text. Searches and sheet signals that have not been checked are listed as leads, never as findings."}


class CampaignIn(BaseModel):
    role: str | None = Field(default=None, max_length=80)


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")


class AddCompanyIn(BaseModel):
    website: str = Field(min_length=3, max_length=200)
    name: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=40)
    links: list[str] = Field(default_factory=list, max_length=10)
    notes: str | None = Field(default=None, max_length=2000)


DAILY_SCAN_LIMIT = 60          # per person per day: the monthly budgets still apply on top


def normalise_domain(raw: str) -> str | None:
    import re
    d = raw.strip().lower()
    d = re.sub(r"^[a-z]+://", "", d).split("/")[0].split("?")[0].split("#")[0].split("@")[-1]
    d = d.removeprefix("www.").rstrip(".")
    if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}", d) or d in {"localhost"} or d.endswith((".local", ".internal", ".invalid")):
        return None
    return d


class ScanManyIn(BaseModel):
    finance_only: bool = False
    limit: int = Field(25, ge=1, le=300)
    with_search: bool = True


def register_scan(app: FastAPI) -> None:
    """On-demand signal capture. Nothing here runs on a timer: somebody presses the button."""
    from ..discovery.plans import ensure_account_plan, priority_accounts

    def tx(request: Request):
        return request.app.state.db.tx()

    def _queue_one(conn, org: dict, by: str, with_search: bool, stamp: str, rediscover: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {}
        jid, created = enqueue(conn, "account_scan", {"organization_id": str(org["id"]), "rediscover": rediscover}, idempotency_key=f"account-scan:{org['id']}:{stamp}", max_attempts=2)
        out["scan_job"] = jid
        if with_search:
            pid = ensure_account_plan(conn, str(org["id"]), org["display_name"], org.get("country_code"))
            sid, _ = enqueue(conn, "discovery_run", {"plan_ids": [pid], "requested_by": by}, idempotency_key=f"account-search:{org['id']}:{stamp}", max_attempts=1)
            out["search_job"] = sid
        return out

    @app.post("/api/accounts/{oid}/scan")
    def scan_account(oid: str, request: Request, with_search: bool = True, rediscover: bool = False, p: Principal = Depends(require("research.request"))):
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            org = q1(conn, f"SELECT o.id, o.display_name, o.country_code, o.canonical_domain FROM organizations o WHERE o.id=%s AND ({where})", [oid, *params])     # noqa: S608
            if not org:
                raise HTTPException(404, "not found")
            _quota(conn, p.email)
            res = _queue_one(conn, org, p.email, with_search, datetime.now(timezone.utc).strftime("%Y%m%d%H%M"), rediscover)
            audit(conn, actor=p.email, operation="account_scan_requested", record_type="organization", record_id=oid, detail={"with_search": with_search})
            return {**res, "website": org["canonical_domain"], "note": "Reads the account's own news, press, careers and job-board pages, and runs one search for it. Results appear in the feed as they are verified."}

    def _quota(conn, email: str) -> None:
        n = (q1(conn, "SELECT count(*) AS n FROM audit_events WHERE actor=%s AND operation IN ('account_scan_requested','company_added') AND at > now() - interval '24 hours'", (email,)) or {"n": 0})["n"]
        if n >= DAILY_SCAN_LIMIT:
            raise HTTPException(429, f"Daily limit reached ({DAILY_SCAN_LIMIT} scans or additions per person). It resets 24 hours after the first one.")

    @app.post("/api/accounts/add")
    def add_company(body: AddCompanyIn, request: Request, p: Principal = Depends(require("research.request"))):
        domain = normalise_domain(body.website)
        if not domain:
            raise HTTPException(400, "That does not look like a company website. Use an address such as example.com.")
        links = []
        for ln in body.links:
            ln = ln.strip()
            if ln and ln.lower().startswith(("http://", "https://")) and len(ln) < 400:
                links.append(ln)
        with tx(request) as conn:
            _quota(conn, p.email)
            existing = q1(conn, "SELECT id, display_name FROM organizations WHERE canonical_domain=%s ORDER BY in_master_list DESC LIMIT 1", (domain,))
            if existing:
                where, params = org_scope_sql(p, "o.id")
                if not q1(conn, f"SELECT 1 FROM organizations o WHERE o.id=%s AND ({where})", [existing["id"], *params]):     # noqa: S608
                    # never attach notes or queue work on an account the caller cannot see, and do not reveal whose it is
                    raise HTTPException(409, "This company is already tracked and is not in your accounts. Ask your manager to share it with you.")
                oid, name, created = str(existing["id"]), existing["display_name"], False
            else:
                name = (body.name or "").strip() or domain.split(".")[0].replace("-", " ").title()
                row = conn.execute("INSERT INTO organizations(display_name, canonical_domain, in_master_list) VALUES (%s,%s,false) RETURNING id", (name, domain)).fetchone()
                oid, created = str(row["id"]), True    # type: ignore[index]
                conn.execute("INSERT INTO new_company_candidates(organization_id, icp_note) VALUES (%s,%s)", (oid, f"Added by {p.email}. Not yet qualified."))
            for kind, val in [*[("link", x) for x in links], *([("note", body.notes.strip())] if body.notes and body.notes.strip() else []), *([("region", body.region.strip())] if body.region and body.region.strip() else [])]:
                conn.execute("INSERT INTO account_context(organization_id, kind, value, added_by) VALUES (%s,%s,%s,%s)", (oid, kind, val, p.email))
            jid, _ = enqueue(conn, "account_scan", {"organization_id": oid, "rediscover": True}, idempotency_key=f"account-scan:{oid}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}", max_attempts=2)
            pid = ensure_account_plan(conn, oid, name, None)
            enqueue(conn, "discovery_run", {"plan_ids": [pid], "requested_by": p.email}, idempotency_key=f"account-search:{oid}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}", max_attempts=1)
            audit(conn, actor=p.email, operation="company_added", record_type="organization", record_id=oid, detail={"domain": domain, "created": created, "links": len(links)})
            return {"id": oid, "name": name, "domain": domain, "created": created, "scan_job": jid,
                    "note": "Reading the company's own site, news, careers page and job board, plus one web search. LinkedIn and other social links are saved for context and are not scraped."}

    @app.post("/api/accounts/{oid}/brief")
    def request_brief(oid: str, request: Request, p: Principal = Depends(require("research.request"))):
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            if not q1(conn, f"SELECT 1 FROM organizations o WHERE o.id=%s AND ({where})", [oid, *params]):     # noqa: S608
                raise HTTPException(404, "not found")
            if q1(conn, "SELECT 1 FROM account_briefs WHERE organization_id=%s AND status IN ('queued','running') AND created_at > now() - interval '15 minutes'", (oid,)):
                raise HTTPException(409, "A brief for this company is already being written.")
            _quota(conn, p.email)
            row = conn.execute("INSERT INTO account_briefs(organization_id, requested_by) VALUES (%s,%s) RETURNING id", (oid, p.email)).fetchone()
            bid = str(row["id"])     # type: ignore[index]
            enqueue(conn, "account_brief", {"brief_id": bid}, idempotency_key=f"brief:{bid}", max_attempts=1)
            audit(conn, actor=p.email, operation="account_scan_requested", record_type="organization", record_id=oid, detail={"brief": True})
            return {"brief_id": bid, "status": "queued"}

    @app.get("/api/accounts/{oid}/briefs")
    def list_briefs(oid: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            if not q1(conn, f"SELECT 1 FROM organizations o WHERE o.id=%s AND ({where})", [oid, *params]):     # noqa: S608
                raise HTTPException(404, "not found")
            rows = qa(conn, "SELECT id, status, model, content, dropped, error, requested_by, created_at, finished_at FROM account_briefs WHERE organization_id=%s ORDER BY created_at DESC LIMIT 5", (oid,))
            return {"items": [dict(r, id=str(r["id"])) for r in rows]}

    @app.post("/api/accounts/{oid}/campaign")
    def request_campaign(oid: str, body: CampaignIn, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            if not q1(conn, f"SELECT 1 FROM organizations o WHERE o.id=%s AND ({where})", [oid, *params]):     # noqa: S608
                raise HTTPException(404, "not found")
            if q1(conn, "SELECT 1 FROM campaign_drafts WHERE organization_id=%s AND status IN ('queued','running') AND created_at > now() - interval '15 minutes'", (oid,)):
                raise HTTPException(409, "A draft for this company is already being written.")
            _quota(conn, p.email)
            row = conn.execute("INSERT INTO campaign_drafts(organization_id, requested_by, role) VALUES (%s,%s,%s) RETURNING id", (oid, p.email, (body.role or "").strip() or None)).fetchone()
            did = str(row["id"])     # type: ignore[index]
            enqueue(conn, "campaign_draft", {"draft_id": did}, idempotency_key=f"draft:{did}", max_attempts=1)
            audit(conn, actor=p.email, operation="account_scan_requested", record_type="organization", record_id=oid, detail={"campaign": True})
            return {"draft_id": did, "status": "queued", "note": "Drafts only. Nothing is sent from this system."}

    @app.get("/api/accounts/{oid}/campaigns")
    def list_campaigns(oid: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            if not q1(conn, f"SELECT 1 FROM organizations o WHERE o.id=%s AND ({where})", [oid, *params]):     # noqa: S608
                raise HTTPException(404, "not found")
            rows = qa(conn, "SELECT id, status, role, steps, review, rounds, model, error, requested_by, decided_by, decided_at, created_at, finished_at FROM campaign_drafts WHERE organization_id=%s ORDER BY created_at DESC LIMIT 5", (oid,))
            crm = q1(conn, "SELECT owner_id FROM crm_organizations WHERE organization_id=%s AND link_status='linked'", (oid,))
            return {"items": [dict(r, id=str(r["id"])) for r in rows], "ownership_checked": bool(crm), "note": "Ownership in Pipedrive is not checked until Pipedrive is connected. Check it before anything is sent."}

    @app.post("/api/campaigns/{did}/decision")
    def decide_campaign(did: str, body: DecisionIn, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            d = q1(conn, "SELECT organization_id, status, review FROM campaign_drafts WHERE id=%s", (did,))
            if not d:
                raise HTTPException(404, "not found")
            where, params = org_scope_sql(p, "o.id")
            if not q1(conn, f"SELECT 1 FROM organizations o WHERE o.id=%s AND ({where})", [str(d["organization_id"]), *params]):     # noqa: S608
                raise HTTPException(404, "not found")
            if body.decision == "approve" and d["status"] != "draft":
                raise HTTPException(409, "Only a draft that passed the brand review can be approved. Fix or rewrite it first.")
            status = "approved" if body.decision == "approve" else "rejected"
            conn.execute("UPDATE campaign_drafts SET status=%s, decided_by=%s, decided_at=now() WHERE id=%s", (status, p.email, did))
            audit(conn, actor=p.email, operation=f"campaign_{status}", record_type="campaign_draft", record_id=did, detail={})
            return {"status": status, "note": "Approved means a person has read it. "}

    @app.get("/api/accounts/{oid}/context")
    def account_context(oid: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            if not q1(conn, f"SELECT 1 FROM organizations o WHERE o.id=%s AND ({where})", [oid, *params]):     # noqa: S608
                raise HTTPException(404, "not found")
            return {"items": qa(conn, "SELECT kind, value, added_by, created_at FROM account_context WHERE organization_id=%s ORDER BY created_at", (oid,))}

    @app.get("/api/admin/source-pilot")
    def source_pilot(request: Request, p: Principal = Depends(require("admin.sources"))):
        with tx(request) as conn:
            from ..discovery.plans import finance_pilot_accounts
            accounts = finance_pilot_accounts(conn)
            items = []
            for account in accounts:
                oid = account["organization_id"]
                endpoints = qa(conn, """SELECT kind, url, last_scanned_at, last_success_at, last_status, last_detail, docs_seen
                    FROM account_endpoints WHERE organization_id=%s ORDER BY kind, url""", (oid,))
                recent = qa(conn, """SELECT e.id::text AS event_id, e.summary, coalesce(e.event_at,e.pub_at) AS date,
                       (SELECT min(s.demand_tier) FROM signals s WHERE s.event_id=e.id AND s.organization_id=e.subject_org_id
                        AND s.status='active' AND s.verification_status='verified'
                        AND (s.expires_at IS NULL OR s.expires_at>now())) AS demand_tier
                    FROM events e WHERE e.subject_org_id=%s AND e.verification_status='verified' AND e.proven
                    AND NOT e.is_historical_replay AND e.lifecycle_status='active' AND e.lane IS DISTINCT FROM 'public'
                    AND coalesce(e.event_at,e.pub_at) >= now() - interval '30 days'
                    ORDER BY demand_tier NULLS LAST, date DESC, e.id LIMIT 5""", (oid,))
                for finding in recent:
                    finding["sources"] = story_sources(conn, [finding["event_id"]])
                scan = q1(conn, """SELECT status, updated_at, result->>'status' AS outcome, result, last_error
                    FROM jobs WHERE kind='account_scan' AND correlation_id='finance-pilot'
                      AND payload->>'organization_id'=%s
                    ORDER BY created_at DESC LIMIT 1""", (str(oid),))
                from ..target_market import target_region
                items.append({"id": str(oid), "name": account["display_name"], "segment": account["segment"],
                              "region": target_region(account["country_code"]),
                              "endpoints": endpoints, "recent": recent, "scan": scan, "confirmed": account["confirmed"]})
        return {"items": items}

    @app.post("/api/admin/scan-accounts")
    def scan_many(body: ScanManyIn, request: Request, p: Principal = Depends(require("admin.costs"))):
        with tx(request) as conn:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H")
            accounts = priority_accounts(conn, body.limit, finance_only=body.finance_only)
            n = 0
            for a in accounts:
                _queue_one(conn, {"id": a["organization_id"], "display_name": a["display_name"], "country_code": a["country_code"]}, p.email, body.with_search, stamp)
                n += 1
            audit(conn, actor=p.email, operation="account_scan_batch_requested", record_type="organization", record_id="batch", detail={"count": n, "with_search": body.with_search, "finance_only": body.finance_only})
            return {"queued": n, "note": "Priority accounts first: your shortlist, then P1 and P2 by signal score."}

    @app.get("/api/accounts/{oid}/endpoints")
    def account_endpoints(oid: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            if not q1(conn, f"SELECT 1 FROM organizations o WHERE o.id=%s AND ({where})", [oid, *params]):     # noqa: S608
                raise HTTPException(404, "not found")
            rows = qa(conn, "SELECT kind, url, provider, board, found_at, last_scanned_at, last_success_at, last_status, last_detail, docs_seen FROM account_endpoints WHERE organization_id=%s ORDER BY kind, url", (oid,))
            return {"items": rows}


def register_connections(app: FastAPI) -> None:
    @app.get("/api/admin/connections")
    def connections(request: Request, p: Principal = Depends(require("sources.read"))):
        from ..connections import LABELS, directory

        with request.app.state.db.tx() as conn:
            rows = directory(conn)
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["state"]] = counts.get(r["state"], 0) + 1
        return {"items": rows, "counts": counts, "labels": LABELS}
