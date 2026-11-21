"""Verification requests, discovery controls, usage tracker and source library."""
from __future__ import annotations

import calendar
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import usage
from ..access import Principal, org_scope_sql
from ..audit import audit
from ..readmodel import coverage_health
from ..db import q1, qa
from ..jobs.queue import enqueue
from .auth import require

URL_KEYS = ("Primary source URL", "Primary Source URL", "Secondary Source URL", "Source URL", "Source link", "Signal Source URL", "Supplier evidence URL", "Existing source URL", "Second Signal Source URL", "Third Signal Source URL", "Matched Signal Source URL", "Evidence URL")


class VerifyIn(BaseModel):
    organization_id: str
    url: str | None = Field(default=None, max_length=1000)


class BatchIn(BaseModel):
    lane: str = Field(default="shortlist", pattern="^(shortlist|signals)$")
    limit: int = Field(default=10, ge=1, le=25)


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
        "ON CONFLICT (url, origin) DO NOTHING RETURNING id", (org_id, url, origin, claimed.get("claimed_text"), claimed.get("claimed_date"), by)).fetchone()
    if not row:
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
            items = prospects_for(conn, p, lane=body.lane, q="", limit=100)
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
            r = q1(conn, "SELECT * FROM verification_requests WHERE id=%s", (vid,))
            if not r or r["status"] not in {"waiting_budget", "failed"}:
                raise HTTPException(404, "nothing to retry")
            if r["organization_id"]:
                where, params = org_scope_sql(p, "o.id")
                if not q1(conn, f"SELECT 1 FROM organizations o WHERE o.id=%s AND ({where})", [str(r["organization_id"]), *params]):      # noqa: S608
                    raise HTTPException(404, "not found")
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
            return {"items": [dict(r, id=str(r["id"])) for r in rows], "counts": counts}

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
        return {"period": period, "budgets": budgets, "months": months, "daily": daily, "by_source": by_source, "by_plan": by_plan, "forecast": forecast,
                "verification_outcomes": outcomes, "cost_per_verified_event_usd": cost_per_verified,
                "prices": {"anthropic_in_per_mtok": s.anthropic_price_in_per_mtok, "anthropic_out_per_mtok": s.anthropic_price_out_per_mtok},
                "notes": ["Exa cost is the amount Exa reports on each response.", "Model usage is counted in tokens. Dollars appear only when list prices are configured (RI_ANTHROPIC_PRICE_IN_PER_MTOK and _OUT_)."]}

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
            rows = qa(conn,
                "SELECT s.source_id, s.display_name, s.source_tier, s.source_type, s.source_family, s.provider, s.countries, s.signal_rule_ids, s.collection_method, s.auth_mode, "
                "s.retention_policy, s.licence_notes, s.poll_interval_s, s.status, s.cost_profile, s.canonical_domain, s.config->>'url' AS url, s.config->>'url_basis' AS url_basis, s.config->'validation' AS validation, h.state AS health, h.last_success_at, h.consecutive_failures, "
                "(SELECT count(*) FROM source_documents d WHERE d.source_id=s.source_id) AS documents, "
                "(SELECT count(DISTINCT ed.event_id) FROM source_documents d JOIN event_documents ed ON ed.document_id=d.id WHERE d.source_id=s.source_id) AS events, "
                "(SELECT coalesce(sum(cost_usd),0) FROM provider_usage u WHERE u.source_id=s.source_id AND u.period=%s) AS cost_usd_month, "
                "(SELECT coalesce(sum(units),0) FROM provider_usage u WHERE u.source_id=s.source_id AND u.period=%s) AS units_month "
                "FROM sources s LEFT JOIN source_health h USING (source_id) ORDER BY s.source_tier, s.source_family, s.display_name", (usage.period_now(), usage.period_now()))
            return {"items": rows}


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
        sch, wrk = hb.get("scheduler"), hb.get("worker")

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
             "note": "Sent to Slack when something material changes; never repeats an unchanged item." if digest_cfg else "Not configured. Set SLACK_WEBHOOK_URL, RI_BASE_URL and RI_LINK_SECRET on the scheduler to switch it on."},
            {"key": "sheets", "label": "Sheet import", "mode": "manual", "state": "ok" if imp else "waiting", "last": imp["finished_at"] if imp else None,
             "note": "You upload the .xlsx exports in Administration, Imports. Hourly automatic sync needs a read-only Google service account."},
            {"key": "pipedrive", "label": "Pipedrive", "mode": "off", "state": "off", "note": "Not connected. Without it there is no ownership data, so every account is open to every seller, and no CRM changes can be made."},
        ]
        nexts = []
        if not src.get("active"):
            nexts.append("Test and activate the newsroom and feed sources (Administration, Sources). Until one is live, only discovery and manual verification feed the platform.")
        if not imp:
            nexts.append("Upload the two workbooks (Administration, Imports).")
        if not wrk or fresh(wrk, 300) != "ok":
            nexts.append("The worker has not checked in recently. Check the worker service on Render.")
        if not digest_cfg:
            nexts.append("Optional: set the Slack webhook to get the daily digest.")
        return {"stages": stages, "queue": jobs, "oldest_waiting_s": age(oldest.get("t")), "budgets": {k: {"cap": float(v["cap"]), "used": float(v["spent"]) + float(v["reserved"])} for k, v in bud.items()}, "next_steps": nexts}


def register_feed(app: FastAPI) -> None:
    from .. import feed as fd
    from ..readmodel import evidence_for
    from ..sheets.reconcile import icp_band, market_focus

    def tx(request: Request):
        return request.app.state.db.tx()

    @app.get("/api/feed")
    def market_feed(request: Request, q: str = Query("", max_length=120), days: int = Query(60, ge=1, le=365), play: str = Query("", max_length=8), rule: str = Query("", max_length=8),
                    market: str = Query("", pattern="^(|core)$"), accounts_only: bool = True, limit: int = Query(40, le=100), p: Principal = Depends(require("explorer.read"))):
        from .search import all_like, tokens
        toks = tokens(q)
        rule_ids = fd.rules_for_play(play) if play else ([rule] if rule else [])
        with tx(request) as conn:
            ew, ep = org_scope_sql(p, "e.subject_org_id")
            tw, tp = all_like("(e.summary || ' ' || coalesce(o.display_name,''))", toks) if toks else ("TRUE", [])
            rows = qa(conn,
                f"SELECT e.id, e.event_type, e.summary, e.event_at, e.pub_at, e.first_observed_at, e.verification_status, e.is_historical_replay, o.id AS org_id, o.display_name, o.country_code, o.segment, "   # noqa: S608
                f"o.employees_estimate, o.employees_uncertain, o.in_master_list, "
                f"(SELECT coalesce(jsonb_agg(jsonb_build_object('rule_id', s.rule_id, 'demand_tier', s.demand_tier, 'strength', s.strength, 'status', s.status)), '[]'::jsonb) FROM signals s WHERE s.event_id=e.id) AS sigs, "
                f"(SELECT coalesce(array_agg(ec.claim_id), '{{}}') FROM event_claims ec WHERE ec.event_id=e.id) AS claim_ids, "
                f"(SELECT jsonb_build_object('id', i.id, 'title', i.title, 'stage', i.stage) FROM initiative_events ie JOIN initiatives i ON i.id=ie.initiative_id WHERE ie.event_id=e.id LIMIT 1) AS programme, "
                f"coalesce(e.event_at, e.pub_at, e.first_observed_at) AS happened "
                f"FROM events e LEFT JOIN organizations o ON o.id=e.subject_org_id "
                f"WHERE e.verification_status='verified' AND NOT e.is_historical_replay AND coalesce(e.event_at, e.pub_at, e.first_observed_at) > now() - make_interval(days => %s) "
                f"AND (e.subject_org_id IS NULL OR ({ew})) AND ({tw}) AND (NOT %s OR e.subject_org_id IS NOT NULL) "
                f"AND (%s::text[] IS NULL OR EXISTS (SELECT 1 FROM signals s WHERE s.event_id=e.id AND s.rule_id = ANY(%s))) "
                f"ORDER BY happened DESC NULLS LAST LIMIT %s", [days, *ep, *tp, accounts_only, rule_ids or None, rule_ids or None, limit * 2])
            items = []
            for r in rows:
                mk = market_focus(r["country_code"], r["segment"])
                if market == "core" and mk["fit"] != "core":
                    continue
                sig_ids = [x["rule_id"] for x in r["sigs"] if x["status"] == "active"]
                ev = evidence_for(conn, list(r["claim_ids"]), limit=2) if r["claim_ids"] else []
                items.append({"id": str(r["id"]), "type": r["event_type"], "summary": r["summary"], "happened": r["happened"], "event_at": r["event_at"], "published": r["pub_at"],
                              "first_seen": r["first_observed_at"],
                              "organization": {"id": str(r["org_id"]), "name": r["display_name"], "country": r["country_code"], "segment": r["segment"], "employees": r["employees_estimate"],
                                               "in_master_list": r["in_master_list"], "icp": icp_band(r["employees_estimate"], bool(r["employees_uncertain"]), r["segment"])["band"], "market": mk} if r["org_id"] else None,
                              "signals": [{**x, "name": fd._load()["rules"].get(x["rule_id"], {}).get("id")} for x in r["sigs"]], "fit": fd.fit_for(sig_ids), "questions": fd.rule_questions(sig_ids),
                              "evidence": ev, "programme": r["programme"]})
                if len(items) >= limit:
                    break
            names = {x["id"]: x["signal"] for x in qa(conn, "SELECT id, signal FROM legacy_signals")}
            for it in items:
                for s in it["signals"]:
                    s["name"] = names.get(s["rule_id"], s["rule_id"])
            leads = qa(conn, "SELECT v.url, v.claimed_text, v.status, v.created_at, v.plan_id, v.result FROM verification_requests v WHERE v.origin='exa_discovery' AND v.status IN ('queued','fetching','extracting','waiting_budget','no_event','blocked','failed') ORDER BY v.created_at DESC LIMIT 12")
            cov = coverage_health(conn, p)
            totals = q1(conn, "SELECT count(*) FILTER (WHERE verification_status='verified') AS verified, count(*) FILTER (WHERE verification_status<>'verified') AS unverified, "
                              "count(*) FILTER (WHERE subject_org_id IS NULL AND verification_status='verified' AND coalesce(event_at, pub_at, first_observed_at) > now() - make_interval(days => %s)) AS market_wide FROM events", (days,)) or {}
        return {"items": items, "count": len(items), "leads": [dict(x, reason=(x["result"] or {}).get("reason")) for x in leads], "coverage": cov, "totals": totals,
                "plays": [{"id": i, "name": n} for i, n in fd.play_ids()],
                "note": "Only events whose claims quote the original page appear here. Searches and sheet signals that have not been checked are listed as leads, never as findings."}
