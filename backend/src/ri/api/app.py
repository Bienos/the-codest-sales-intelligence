"""HTTP API. Every business route enforces permission AND row scope server-side. Writes to external systems are only
ever *proposals*; execution goes through the approval executor (separate function, separate credential)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import approvals as appr
from ..access import Principal, org_scope_sql
from ..audit import audit
from ..config import Settings
from ..crm import pipedrive as pd
from ..db import Database, jb, q1, qa
from ..jobs import queue
from ..opportunity import engine, states
from ..sheets.client import ConnectorBlocked
from ..readmodel import card, coverage_health, evidence_for, fingerprint, today
from ..research import planner
from .auth import TokenVerifier, principal_dep, require

CSP = ("default-src 'self'; script-src 'self' https://accounts.google.com/gsi/client; style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style; "
       "img-src 'self' data: https:; font-src 'self'; connect-src 'self' https://accounts.google.com/gsi/; frame-src 'self' https://accounts.google.com/gsi/; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

DISMISS_REASONS = {"not_relevant", "wrong_entity", "stale", "duplicate", "no_external_demand", "not_commercially_relevant", "owned_elsewhere", "other"}


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(accepted|dismissed|monitor)$")
    reason: str | None = None
    note: str | None = None


class TransitionIn(BaseModel):
    new_state: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class ProposeIn(BaseModel):
    hypothesis_id: str
    subject: str = Field(min_length=3, max_length=200)
    note: str = ""
    due_date: str | None = None
    justification: str = Field(min_length=3)


class ApprovalDecisionIn(BaseModel):
    approve: bool


class DeliveryIn(BaseModel):
    feasible: bool
    note: str = Field(min_length=3)


class IdentityResolveIn(BaseModel):
    decision: str = Field(pattern="^(link|new|reject)$")
    organization_id: str | None = None
    note: str = Field(min_length=3)


def create_app(settings: Settings, *, db: Database | None = None, verifier: TokenVerifier | None = None, writer_factory=None) -> FastAPI:
    app = FastAPI(title="Codest Revenue Intelligence", version="0.1.0", docs_url=None if settings.env == "production" else "/api/docs")
    app.state.settings = settings
    app.state.db = db or Database(settings.database_url)
    app.state.verifier = verifier or TokenVerifier(settings)
    app.state.writer_factory = writer_factory

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin-allow-popups")     # Google Sign-In popup needs this relaxed value
        if request.url.path.startswith(("/api/", "/webhooks/", "/metrics")):
            h.setdefault("Cache-Control", "no-store")                             # never cache personal or commercial data
        else:
            h.setdefault("Content-Security-Policy", CSP)
        if settings.env == "production":
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp

    def tx(request: Request):
        return request.app.state.db.tx()

    def event(conn, p: Principal, name: str, hyp: str | None = None, org: str | None = None, **detail):
        conn.execute("INSERT INTO product_events(user_id, event, hypothesis_id, organization_id, detail) VALUES (%s,%s,%s,%s,%s)", (p.user_id, name, hyp, org, jb(detail)))

    def visible_hyp(conn, p: Principal, hid: str) -> dict[str, Any]:
        where, params = org_scope_sql(p, "h.organization_id")
        h = q1(conn, f"SELECT h.* FROM opportunity_hypotheses h WHERE h.id=%s AND ({where})", [hid, *params])      # noqa: S608
        if not h:
            raise HTTPException(404, "not found")       # 404, not 403: do not reveal existence outside scope
        return h

    # ------------------------------------------------------------------ meta
    @app.get("/brand/logo", include_in_schema=False)
    async def brand_logo():
        from fastapi.responses import Response
        from ..brand import fetch_logo
        data, ctype = await fetch_logo()
        if not data:
            raise HTTPException(404, "logo not available")
        return Response(data, media_type=ctype, headers={"Cache-Control": "public, max-age=86400", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox", "X-Content-Type-Options": "nosniff"})

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/readyz")
    def readyz(request: Request):
        with tx(request) as conn:
            conn.execute("SELECT 1")
        return {"ok": True}

    @app.get("/api/config")
    def public_config():
        """Public, non-secret bootstrap for the SPA (Google OAuth client IDs are public by design)."""
        return {"auth_mode": settings.auth_mode, "google_client_id": settings.google_client_id, "env": settings.env}

    @app.post("/api/auth/session")
    def start_session(request: Request, p: Principal = Depends(principal_dep)):
        """Exchange a valid Google (or current session) token for a fresh 12-hour session."""
        from .auth import issue_session
        token, exp = issue_session(request.app.state.settings, p.email)
        return {"token": token, "expires_at": exp}

    @app.get("/api/me")
    def me(p: Principal = Depends(principal_dep)):
        return {"email": p.email, "role": p.role, "team": p.team, "permissions": sorted(p.perms), "pipedrive_user_id": p.pipedrive_user_id}

    # ------------------------------------------------------------------ Today
    @app.get("/api/today")
    def get_today(request: Request, p: Principal = Depends(require("today.read"))):
        with tx(request) as conn:
            out = today(conn, p)
            for sec in out["sections"].values():
                for c in sec:
                    event(conn, p, "recommendation_presented", c["hypothesis_id"], c["account"]["id"], priority=c["priority"])
            return out

    @app.post("/api/hypotheses/{hid}/decision")
    def decide(hid: str, body: DecisionIn, request: Request, p: Principal = Depends(require("hypothesis.review"))):
        with tx(request) as conn:
            h = visible_hyp(conn, p, hid)
            if body.decision == "dismissed" and (not body.reason or body.reason not in DISMISS_REASONS):
                raise HTTPException(422, f"dismissal needs a reason from {sorted(DISMISS_REASONS)}")
            fp = fingerprint(conn, h)
            conn.execute("INSERT INTO hypothesis_user_state(hypothesis_id, user_id, decision, reason, note, fingerprint) VALUES (%s,%s,%s,%s,%s,%s) "
                         "ON CONFLICT (hypothesis_id, user_id) DO UPDATE SET decision=EXCLUDED.decision, reason=EXCLUDED.reason, note=EXCLUDED.note, fingerprint=EXCLUDED.fingerprint, decided_at=now()",
                         (hid, p.user_id, body.decision, body.reason, body.note, fp))
            conn.execute("INSERT INTO feedback(target_type, target_id, reviewer, judgement, reason, note) VALUES ('hypothesis',%s,%s,%s,%s,%s)", (hid, p.email, body.decision, body.reason, body.note))
            event(conn, p, "accepted" if body.decision == "accepted" else "dismissed", hid, str(h["organization_id"]), reason=body.reason)
            return {"ok": True, "fingerprint": fp}

    @app.post("/api/hypotheses/{hid}/research")
    def request_research(hid: str, request: Request, p: Principal = Depends(require("research.request"))):
        with tx(request) as conn:
            h = visible_hyp(conn, p, hid)
            step, why = planner.plan_next(conn, hid, remaining_budget=0.0)       # default budgets are zero: free/existing-data steps only until a cap is configured
            event(conn, p, "research_requested", hid, str(h["organization_id"]), outcome=why)
            return {"planned": bool(step), "reason": why, "step": ({"question": step.question, "method": step.method, "decision_effect": step.decision_effect, "max_cost": step.max_cost} if step else None)}

    @app.post("/api/hypotheses/{hid}/transition")
    def do_transition(hid: str, body: TransitionIn, request: Request, p: Principal = Depends(require("hypothesis.review"))):
        with tx(request) as conn:
            visible_hyp(conn, p, hid)
            try:
                h = states.transition(conn, hid, body.new_state, actor=p.email, evidence=body.evidence, reason=body.reason)
            except states.TransitionError as exc:
                raise HTTPException(409, str(exc)) from exc
            return {"state": h["state"]}

    @app.post("/api/hypotheses/{hid}/delivery-validation")
    def delivery(hid: str, body: DeliveryIn, request: Request, p: Principal = Depends(require("delivery.validate"))):
        with tx(request) as conn:
            h = visible_hyp(conn, p, hid)
            gates = dict(h["gates"] or {})
            gates["G7"] = {"status": "pass" if body.feasible else "fail", "reason": body.note, "validated_by": p.email, "reason_internal": True}
            conn.execute("UPDATE opportunity_hypotheses SET gates=%s WHERE id=%s", (jb(gates), hid))
            audit(conn, actor=p.email, operation="delivery_validation", record_type="opportunity_hypothesis", record_id=hid, detail={"feasible": body.feasible})
            engine.evaluate(conn, hid, requester_crm_user=p.pipedrive_user_id)
            return {"ok": True}

    # ------------------------------------------------------------------ Accounts
    @app.get("/api/accounts")
    def accounts(request: Request, q: str = "", limit: int = Query(50, le=200), p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            return {"items": [dict(r, id=str(r["id"])) for r in qa(conn,
                f"SELECT o.id, o.display_name, o.country_code, o.segment, o.in_master_list, o.employees_estimate, o.employees_basis, o.employees_uncertain, c.owner_id AS crm_owner_id, "   # noqa: S608
                f"(SELECT count(*) FROM initiatives i WHERE i.owner_org_id=o.id AND i.status='active') AS initiatives FROM organizations o LEFT JOIN crm_organizations c ON c.organization_id=o.id AND c.link_status='linked' "
                f"WHERE ({where}) AND (%s='' OR o.display_name ILIKE '%%'||%s||'%%') ORDER BY o.in_master_list DESC, o.display_name LIMIT %s", [*params, q, q, limit])]}

    @app.get("/api/accounts/{oid}")
    def account(oid: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            o = q1(conn, f"SELECT o.* FROM organizations o WHERE o.id=%s AND ({where})", [oid, *params])       # noqa: S608
            if not o:
                raise HTTPException(404, "not found")
            crm = q1(conn, "SELECT crm_id, owner_id, open_deals_count, synced_at FROM crm_organizations WHERE organization_id=%s AND link_status='linked'", (oid,))
            hyps = qa(conn, "SELECT h.* FROM opportunity_hypotheses h WHERE h.organization_id=%s AND h.state NOT IN ('superseded') ORDER BY h.score_total DESC", (oid,))
            inits = qa(conn, "SELECT id, title, title_is_official, stage, programme_domain, technology_tags, research_gaps, budget_verified, last_material_event_at, next_milestone_at FROM initiatives WHERE owner_org_id=%s AND status='active'", (oid,))
            sources = qa(conn, "SELECT sheet_title, source_file_id, row_number FROM account_source_links WHERE organization_id=%s", (oid,))
            signals = qa(conn, "SELECT s.rule_id, s.demand_tier, s.strength, s.status, s.effective_from, s.expires_at, e.summary, e.event_at, e.pub_at, e.first_observed_at, e.verified_at FROM signals s JOIN events e ON e.id=s.event_id WHERE s.organization_id=%s ORDER BY s.effective_from DESC LIMIT 50", (oid,))
            suppliers = qa(conn, "SELECT supplier_name, supplier_role, scope_text, relation, exclusivity, status FROM supplier_edges WHERE buyer_org_id=%s", (oid,))
            routes = qa(conn, "SELECT r.route_type, r.counterparty_name, r.access_status, r.restriction FROM procurement_routes r JOIN initiatives i ON i.id=r.initiative_id WHERE i.owner_org_id=%s", (oid,))
            coverage = qa(conn, "SELECT source_family, coverage_state, source_ids, last_success_at, note FROM account_source_coverage WHERE organization_id=%s", (oid,))
            buyers: list[dict[str, Any]] = []
            if p.can("contacts.read"):
                for b in qa(conn, "SELECT p.id, p.full_name, p.suppressed, p.email_verification_status, e.title, e.buyer_lane, e.verification_status FROM employment e JOIN people p ON p.id=e.person_id WHERE e.organization_id=%s AND p.erased_at IS NULL", (oid,)):
                    buyers.append({"id": str(b["id"]), "name": b["full_name"], "title": b["title"], "lane": b["buyer_lane"], "role_verified": b["verification_status"], "suppressed": b["suppressed"],
                                   "email_status": b["email_verification_status"]})      # addresses themselves are never returned here
                audit(conn, actor=p.email, operation="pii_read", record_type="organization", record_id=oid, detail={"count": len(buyers)})
            score = q1(conn, "SELECT score, band, groups, signal_count, top, stopped, downgraded, last_signal_at FROM account_scores WHERE organization_id=%s", (oid,))
            now = datetime.now(timezone.utc)
            return {"account": dict(o, id=str(o["id"])), "score": score, "crm": crm, "ownership_verified": bool(crm and crm["owner_id"]), "source_rows": sources,
                    "opportunities": [card(conn, h, now) for h in hyps], "initiatives": [dict(i, id=str(i["id"])) for i in inits], "signals": signals, "suppliers": suppliers,
                    "routes": routes, "buyers": buyers, "coverage": coverage, "source_coverage_note": "Only source families listed above are monitored; missing families mean visibility unknown, not 'no activity'."}

    @app.get("/api/hypotheses/{hid}")
    def hypothesis(hid: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            h = visible_hyp(conn, p, hid)
            c = card(conn, h, datetime.now(timezone.utc))
            trace = qa(conn, "SELECT prior_state, new_state, actor, reason, at FROM opportunity_transitions WHERE hypothesis_id=%s ORDER BY id", (hid,))
            tasks = qa(conn, "SELECT research_question, method, status, stop_reason, estimated_cost FROM research_tasks WHERE target_type='hypothesis' AND target_id=%s ORDER BY created_at", (hid,))
            return {**c, "gates": h["gates"], "score_breakdown": h["score_json"], "score_note": "Prioritisation weights are provisional hypotheses, NOT win probabilities.",
                    "review": h["review_json"], "why_this_recommendation": {"transitions": trace, "research": tasks, "scoring_rule_version": h["scoring_rule_version"],
                                                                           "initiative_revision": h["initiative_revision"], "evidence": evidence_for(conn, h["supporting_claim_ids"], 20)}}

    # ------------------------------------------------------------------ Explorer
    @app.get("/api/explorer")
    def explorer(request: Request, entity: str = Query("opportunities", pattern="^(opportunities|companies|procurement|events)$"), q: str = "", country: str | None = None,
                 existing: bool | None = None, stage: str | None = None, confidence: str | None = None, signal_family: str | None = None, blocked: bool | None = None,
                 limit: int = Query(50, le=200), p: Principal = Depends(require("explorer.read"))):
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            cov = coverage_health(conn, p)
            if entity == "companies":
                sql = ("SELECT o.id, o.display_name, o.country_code, o.segment, o.in_master_list, o.employees_estimate FROM organizations o WHERE (" + where + ") "
                       "AND (%s='' OR o.display_name ILIKE '%%'||%s||'%%') AND (%s::text IS NULL OR o.country_code=%s) AND (%s::bool IS NULL OR o.in_master_list=%s) ORDER BY o.display_name LIMIT %s")
                rows = qa(conn, sql, [*params, q, q, country, country, existing, existing, limit]); basis = "alphabetical (ICP fit not applied)"
            elif entity == "procurement":
                rows = qa(conn, "SELECT n.notice_id, n.buyer_name, n.title, n.status, n.deadline_at, n.deadline_tz, n.last_status_check_at, n.official_url FROM procurement_notices n "
                                "LEFT JOIN organizations o ON o.id=n.buyer_org_id WHERE (n.buyer_org_id IS NULL OR (" + where + ")) AND (%s='' OR n.title ILIKE '%%'||%s||'%%') "
                                "ORDER BY (n.status IN ('open','amended')) DESC, n.deadline_at LIMIT %s", [*params, q, q, limit]); basis = "open notices first, then deadline"
            elif entity == "events":
                rows = qa(conn, "SELECT e.id, e.event_type, e.summary, e.event_at, e.pub_at, e.first_observed_at, e.verification_status, e.is_historical_replay FROM events e LEFT JOIN organizations o ON o.id=e.subject_org_id "
                                "WHERE (e.subject_org_id IS NULL OR (" + where + ")) AND (%s='' OR e.summary ILIKE '%%'||%s||'%%') ORDER BY coalesce(e.event_at, e.pub_at) DESC NULLS LAST LIMIT %s", [*params, q, q, limit]); basis = "freshness (event date, publication date shown separately)"
            else:
                rows = qa(conn, "SELECT h.id, o.display_name, h.route_type, h.counterparty_name, h.priority, h.state, h.score_total, h.evidence_confidence, h.access_status, h.external_demand_status, i.title, i.stage "
                                "FROM opportunity_hypotheses h JOIN organizations o ON o.id=h.organization_id JOIN initiatives i ON i.id=h.initiative_id WHERE (" + where + ") "
                                "AND (%s='' OR o.display_name ILIKE '%%'||%s||'%%' OR i.title ILIKE '%%'||%s||'%%') AND (%s::text IS NULL OR o.country_code=%s) AND (%s::bool IS NULL OR o.in_master_list=%s) "
                                "AND (%s::text IS NULL OR i.stage=%s) AND (%s::text IS NULL OR h.evidence_confidence=%s) AND (%s::bool IS NULL OR (h.priority='blocked')=%s) "
                                "AND (%s::text IS NULL OR EXISTS (SELECT 1 FROM signals s JOIN initiative_events ie ON ie.event_id=s.event_id WHERE ie.initiative_id=i.id AND s.rule_id LIKE %s||'-%%')) "
                                "ORDER BY h.score_total DESC LIMIT %s", [*params, q, q, q, country, country, existing, existing, stage, stage, confidence, confidence, blocked, blocked, signal_family, signal_family, limit])
                basis = "opportunity prioritisation score (provisional weights; not a probability). Confidence is a separate column."
            return {"entity": entity, "ranking_basis": basis, "count": len(rows), "items": [{k: (str(v) if k == "id" else v) for k, v in r.items()} for r in rows],
                    "coverage": cov, "coverage_note": "Empty results are NOT proof of no market activity: see coverage."}

    # ------------------------------------------------------------------ CRM proposals / approvals
    @app.post("/api/crm/propose-activity")
    def propose(body: ProposeIn, request: Request, p: Principal = Depends(require("crm.propose"))):
        with tx(request) as conn:
            h = visible_hyp(conn, p, body.hypothesis_id)
            crm = q1(conn, "SELECT crm_id, owner_id FROM crm_organizations WHERE organization_id=%s AND link_status='linked'", (h["organization_id"],))
            if not crm:
                raise HTTPException(409, "account is not linked to Pipedrive: ownership cannot be checked, CRM proposals are disabled for it")
            if crm["owner_id"] and p.pipedrive_user_id != crm["owner_id"] and p.role != "sales_manager":
                raise HTTPException(403, "account is owned by another seller: route to the owner (non-circumvention)")
            payload = {"subject": body.subject, "type": "task", "note": body.note, "due_date": body.due_date}
            a = appr.request(conn, kind="crm_write", target_system="pipedrive", target_id=str(crm["crm_id"]), operation="create_activity", payload=payload, requested_by=p.email,
                             justification=body.justification, idempotency_key=f"crm-activity:{body.hypothesis_id}:{appr.payload_hash(payload)[:16]}", ttl_hours=settings.approval_ttl_hours)
            event(conn, p, "approved_action_requested", body.hypothesis_id, str(h["organization_id"]))
            return {"approval_id": str(a["id"]), "status": a["status"], "diff": {"target": f"Pipedrive organisation {crm['crm_id']}", "operation": "create_activity", "payload": payload},
                    "expires_at": a["expires_at"]}

    @app.get("/api/approvals")
    def list_approvals(request: Request, p: Principal = Depends(principal_dep)):
        if not (p.can("approvals.read_own") or p.can("approvals.read_all")):
            raise HTTPException(403, "missing permission")
        with tx(request) as conn:
            rows = qa(conn, "SELECT id, kind, target_system, target_id, operation, payload, requested_by, approver, status, created_at, expires_at, justification FROM approvals "
                            "WHERE (%s OR requested_by=%s OR approver=%s) ORDER BY created_at DESC LIMIT 100", (p.can("approvals.read_all"), p.email, p.email))
            return {"items": [dict(r, id=str(r["id"])) for r in rows]}

    @app.post("/api/approvals/{aid}/decision")
    def approval_decision(aid: str, body: ApprovalDecisionIn, request: Request, p: Principal = Depends(require("approvals.decide"))):
        with tx(request) as conn:
            try:
                a = appr.decide(conn, aid, approver=p.email, approver_role=p.role, approve=body.approve)
            except appr.ApprovalError as exc:
                raise HTTPException(409, str(exc)) from exc
            return {"status": a["status"]}

    @app.post("/api/approvals/{aid}/execute")
    def approval_execute(aid: str, request: Request, p: Principal = Depends(require("approvals.decide"))):
        with tx(request) as conn:
            kind = (q1(conn, "SELECT kind FROM approvals WHERE id=%s", (aid,)) or {}).get("kind")
            if kind == "sequence_enrolment":
                from ..outbound import enrol as enrol_mod
                from ..outbound.lemlist import LemlistClient, LemlistError
                try:
                    client = LemlistClient(settings.lemlist_api_key) if settings.lemlist_api_key else None
                    return enrol_mod.execute_enrolment(conn, aid, client, settings)
                except (appr.ApprovalError, LemlistError, ConnectorBlocked) as exc:
                    raise HTTPException(409, str(exc)) from exc
            if not settings.crm_writes_enabled:
                raise HTTPException(409, "CRM writes are disabled in this environment")
            wf = request.app.state.writer_factory
            if wf is None:
                raise HTTPException(503, "CRM write credential not configured (integration blocked)")
            try:
                res = pd.execute_approved(conn, aid, wf(), settings)
            except appr.ApprovalError as exc:
                raise HTTPException(409, str(exc)) from exc
            return {"status": res.status, "remote_id": res.remote_id, "reconciled": res.reconciled}

    # ------------------------------------------------------------------ Webhooks
    @app.post("/webhooks/pipedrive")
    async def pipedrive_webhook(request: Request):
        if not settings.pipedrive_webhook_secret:
            raise HTTPException(503, "webhook secret not configured")
        if not pd.verify_basic_auth(request.headers.get("authorization"), "pipedrive", settings.pipedrive_webhook_secret):
            with tx(request) as conn:
                conn.execute("INSERT INTO audit_events(actor, operation, record_type) VALUES ('webhook','auth_failed','pipedrive_webhook')")
            raise HTTPException(401, "bad credentials")
        body = await request.json()
        with tx(request) as conn:
            _, created = pd.receive_webhook(conn, body)
        return {"accepted": True, "duplicate": not created}          # acknowledged immediately; processed asynchronously

    # ------------------------------------------------------------------ Administration
    @app.get("/api/admin/sources")
    def admin_sources(request: Request, p: Principal = Depends(require("sources.read"))):
        with tx(request) as conn:
            return {"items": qa(conn, "SELECT s.source_id, s.display_name, s.source_tier, s.source_family, s.status, s.poll_interval_s, h.state AS health, h.last_success_at, h.consecutive_failures, h.missed_due_runs, h.error_class, h.detail, "
                                      "s.certification IS NOT NULL AS certified, s.collection_method, s.canonical_domain, s.config->>'url' AS url, s.config->>'url_basis' AS url_basis, s.config->'validation' AS validation, (SELECT count(*) FROM monitoring_runs r WHERE r.source_id=s.source_id AND r.status IN ('failed','partial') AND r.started_at > now()-interval '7 days') AS failures_7d "
                                      "FROM sources s LEFT JOIN source_health h USING (source_id) ORDER BY s.source_family, s.source_id")}

    @app.get("/api/admin/identity-reviews")
    def identity_reviews(request: Request, p: Principal = Depends(require("identity.manage"))):
        with tx(request) as conn:
            rows = qa(conn, "SELECT r.id, r.raw_company_name, r.reason, r.candidate_org_ids, r.status, r.features, l.sheet_title, l.row_number FROM identity_reviews r LEFT JOIN account_source_links l ON l.id=r.source_link_id WHERE r.status='open' ORDER BY r.created_at LIMIT 200")
            for r in rows:
                r["candidates"] = [dict(c, id=str(c["id"])) for c in qa(conn, "SELECT id, display_name, country_code, canonical_domain FROM organizations WHERE id = ANY(%s)", (r["candidate_org_ids"],))]
            return {"items": [dict(r, id=str(r["id"]), candidate_org_ids=[str(x) for x in r["candidate_org_ids"]]) for r in rows]}

    @app.post("/api/admin/identity-reviews/{rid}/resolve")
    def resolve_identity(rid: str, body: IdentityResolveIn, request: Request, p: Principal = Depends(require("identity.manage"))):
        with tx(request) as conn:
            r = q1(conn, "SELECT * FROM identity_reviews WHERE id=%s AND status='open'", (rid,))
            if not r:
                raise HTTPException(404, "review not found")
            if body.decision == "link":
                if not body.organization_id or body.organization_id not in [str(x) for x in r["candidate_org_ids"]]:
                    raise HTTPException(422, "organization_id must be one of the candidates")
                conn.execute("UPDATE account_source_links SET organization_id=%s WHERE id=%s", (body.organization_id, r["source_link_id"]))
                status = "resolved_link"
            elif body.decision == "new":
                oid = conn.execute("INSERT INTO organizations(display_name, in_master_list) VALUES (%s,true) RETURNING id", (r["raw_company_name"],)).fetchone()["id"]
                conn.execute("UPDATE account_source_links SET organization_id=%s WHERE id=%s", (oid, r["source_link_id"]))
                body.organization_id, status = str(oid), "resolved_new"
            else:
                status = "resolved_reject"
            conn.execute("UPDATE identity_reviews SET status=%s, resolved_org_id=%s, reviewer=%s, reviewer_note=%s, resolved_at=now() WHERE id=%s", (status, body.organization_id, p.email, body.note, rid))
            audit(conn, actor=p.email, operation="identity_review_resolved", record_type="identity_review", record_id=rid, detail={"decision": body.decision, "note": body.note})
            return {"status": status}

    @app.get("/api/admin/jobs")
    def admin_jobs(request: Request, p: Principal = Depends(require("admin.jobs"))):
        with tx(request) as conn:
            return {"by_status": qa(conn, "SELECT status, count(*) n FROM jobs GROUP BY status ORDER BY status"),
                    "dead_letters": [dict(d, id=str(d["id"])) for d in qa(conn, "SELECT id, kind, error_class, error, created_at, replayed_at FROM dead_letters ORDER BY created_at DESC LIMIT 100")]}

    @app.post("/api/admin/dead-letters/{did}/replay")
    def replay(did: str, request: Request, p: Principal = Depends(require("admin.jobs"))):
        with tx(request) as conn:
            try:
                return {"job_id": queue.replay_dead_letter(conn, did, p.email)}
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc

    @app.get("/api/admin/costs")
    def admin_costs(request: Request, p: Principal = Depends(require("admin.costs"))):
        with tx(request) as conn:
            return {"budgets": qa(conn, "SELECT provider, kind, period, cap, reserved, spent, unit FROM provider_budgets ORDER BY provider, kind, period"),
                    "recent_ledger": qa(conn, "SELECT provider, kind, entry, amount, purpose, created_at FROM budget_ledger ORDER BY id DESC LIMIT 100"),
                    "paid_enrichment_enabled": settings.paid_enrichment_enabled,
                    "note": "Default caps are zero: no paid provider call runs unless a cap AND (for contact data) an approval exist."}

    @app.get("/api/admin/rules")
    def admin_rules(request: Request, p: Principal = Depends(require("admin.rules"))):
        with tx(request) as conn:
            return {"items": qa(conn, "SELECT l.id, l.grp, l.signal, l.strength, l.fresh_for_days, v.version, v.status FROM legacy_signals l LEFT JOIN signal_rule_versions v ON v.rule_id=l.id ORDER BY l.ordinal"),
                    "summary": qa(conn, "SELECT status, count(*) n FROM signal_rule_versions GROUP BY status")}

    @app.get("/api/admin/audit")
    def admin_audit(request: Request, operation: str | None = None, limit: int = Query(100, le=500), p: Principal = Depends(require("audit.read"))):
        with tx(request) as conn:
            return {"items": qa(conn, "SELECT id, at, actor, operation, record_type, record_id, approval_id, detail FROM audit_events WHERE (%s::text IS NULL OR operation=%s) ORDER BY id DESC LIMIT %s", (operation, operation, limit))}

    @app.get("/api/admin/quality")
    def quality(request: Request, p: Principal = Depends(require("admin.rules"))):
        with tx(request) as conn:
            return {"decisions": qa(conn, "SELECT judgement, reason, count(*) n FROM feedback WHERE target_type='hypothesis' GROUP BY judgement, reason ORDER BY n DESC"),
                    "events_by_verification": qa(conn, "SELECT verification_status, count(*) n FROM events WHERE NOT is_historical_replay GROUP BY 1"),
                    "open_identity_reviews": q1(conn, "SELECT count(*) n FROM identity_reviews WHERE status='open'")["n"],  # type: ignore[index]
                    "quarantined_webhooks": q1(conn, "SELECT count(*) n FROM crm_webhook_inbox WHERE status='quarantined'")["n"]}  # type: ignore[index]

    from . import r5
    r5.register(app)
    from . import lists
    lists.register(app)
    lists.register_users(app)
    from . import intel
    intel.register(app)
    intel.register_pipeline(app)
    intel.register_feed(app)
    intel.register_scan(app)
    intel.register_connections(app)
    from . import outreach as outreach_api
    outreach_api.register_outreach(app, settings)
    from . import leads as leads_api
    leads_api.register_leads(app)
    from . import search as search_api
    search_api.register(app)
    search_api.register_kb_admin(app)
    from . import sources_admin
    sources_admin.register(app)
    return app
