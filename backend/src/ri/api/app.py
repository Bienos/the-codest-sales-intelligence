"""HTTP API. Every business route enforces permission AND row scope server-side. Writes to external systems are only
ever *proposals*; execution goes through the approval executor (separate function, separate credential)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
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
from ..signals.seller import leading_signals
from ..feed import STORY_KEY_SQL
from ..readmodel import card, coverage_health, evidence_for, fingerprint, today
from ..research import planner
from ..target_market import account_workspace_sql, target_sql
from .auth import TokenVerifier, principal_dep, require

CSP = ("default-src 'self'; script-src 'self' https://accounts.google.com/gsi/client; style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style; "
       "img-src 'self' data: https:; font-src 'self'; connect-src 'self' https://accounts.google.com/gsi/; frame-src 'self' https://accounts.google.com/gsi/; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

WEBHOOK_MAX_BYTES = 1_000_000
JSON_MAX_BYTES = 2_000_000


class RedactSecretsInAccessLog:
    """logging.Filter: hides secrets that some providers can only send in the URL path (Lemlist webhooks), and signed link tokens."""

    import re as _re
    _PAT = _re.compile(r"(/webhooks/lemlist/)[^\s/?\"]+|([?&]t=)[^\s&\"]+")

    def filter(self, record) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(self._PAT.sub(lambda m: (m.group(1) or m.group(2)) + "[redacted]", a) if isinstance(a, str) else a for a in record.args)
        return True


class RateLimiter:
    """Fixed one-minute windows per caller, in memory (one API process). Not a security boundary on its own; it caps floods."""

    def __init__(self, per_minute: int):
        import threading
        self.per_minute, self._lock, self._hits = per_minute, threading.Lock(), {}

    def allow(self, key: str, now: float | None = None) -> bool:
        import time
        if self.per_minute <= 0:
            return True
        window = int((now or time.time()) // 60)
        with self._lock:
            if len(self._hits) > 10_000:
                self._hits = {k: v for k, v in self._hits.items() if v[0] == window}
            w, n = self._hits.get(key, (window, 0))
            n = n + 1 if w == window else 1
            self._hits[key] = (window, n)
            return n <= self.per_minute


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


LIST_PROFILE_KEYS = ("Account ID", "Region", "Industry", "ICP Segment", "Account Priority", "Opportunity Score", "Strongest Buying Signal",
                     "Signal Date", "Signal Strength", "Buying Stage", "Potential Opportunity", "The Codest Service Fit", "Procurement Route",
                     "Next Action", "Research Status", "Coverage Gap", "Eligibility", "Last Researched", "Primary Source URL")


def create_app(settings: Settings, *, db: Database | None = None, verifier: TokenVerifier | None = None, writer_factory=None) -> FastAPI:
    app = FastAPI(title="Codest Revenue Intelligence", version="0.1.0", docs_url=None if settings.env == "production" else "/api/docs")
    app.state.settings = settings
    app.state.db = db or Database(settings.database_url)
    app.state.verifier = verifier or TokenVerifier(settings)
    app.state.writer_factory = writer_factory
    app.state.limiter = RateLimiter(settings.api_rate_per_minute)

    @app.middleware("http")
    async def body_limit(request: Request, call_next):
        """Webhooks and JSON calls are small; refuse oversized bodies before they are read (uploads have their own limit)."""
        from fastapi.responses import JSONResponse
        cl = request.headers.get("content-length")
        path = request.url.path
        limit = WEBHOOK_MAX_BYTES if path.startswith("/webhooks/") else None if path.startswith("/api/admin/kb/") or path == "/api/admin/import/upload" else JSON_MAX_BYTES
        if limit and cl and cl.isdigit() and int(cl) > limit:
            return JSONResponse({"detail": "request body too large"}, status_code=413)
        return await call_next(request)

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        """Per-caller ceiling on API calls, so one person or a leaked token cannot flood the API or burn the shared budgets. Generous for people."""
        from fastapi.responses import JSONResponse
        if request.url.path.startswith(("/api/", "/webhooks/")) and request.url.path not in {"/api/config"}:
            key = (request.headers.get("authorization") or "")[-32:] or (request.client.host if request.client else "anon")
            if not app.state.limiter.allow(key):
                return JSONResponse({"detail": "Too many requests. Wait a minute and try again."}, status_code=429, headers={"Retry-After": "60"})
        return await call_next(request)

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
        return {"auth_mode": settings.auth_mode, "google_client_id": settings.google_client_id, "env": settings.env, "allowed_domain": settings.allowed_email_domain}

    @app.post("/api/auth/session")
    def start_session(request: Request, p: Principal = Depends(principal_dep)):
        """Exchange a valid Google (or current session) token for a fresh 12-hour session."""
        from .auth import issue_session
        presented = request.headers.get("authorization", "")[7:]
        prior = request.app.state.verifier.session_claims(presented) if hasattr(request.app.state.verifier, "session_claims") else None
        # renewing a session keeps the original sign-in time, so a stolen session cannot keep itself alive forever
        token, exp = issue_session(request.app.state.settings, p.email, auth_time=int(prior["auth_time"]) if prior else None)
        return {"token": token, "expires_at": exp}

    @app.get("/api/me")
    def me(p: Principal = Depends(principal_dep)):
        return {"email": p.email, "role": p.role, "team": p.team, "permissions": sorted(p.perms), "pipedrive_user_id": p.pipedrive_user_id}

    # ------------------------------------------------------------------ Today
    @app.get("/api/today")
    def get_today(request: Request, focus: str = Query("", pattern="^(|finance|other|public)$"), target: bool = False, p: Principal = Depends(require("today.read"))):
        with tx(request) as conn:
            out = today(conn, p, focus=focus, target=target)
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
            cur = visible_hyp(conn, p, hid)
            forward = cur["state"] in states.ORDER and body.new_state in states.ORDER and states.ORDER.index(body.new_state) > states.ORDER.index(cur["state"])
            if forward and body.new_state in states.LATE_STATES and p.role not in {"sales_manager", "admin"}:
                raise HTTPException(403, "A manager records proposal and contract stages.")
            if forward and body.new_state in states.CRM_EVIDENCE_STATES and body.evidence.get("crm_ref") \
                    and not states.crm_ref_exists(conn, str(cur["organization_id"]), body.evidence["crm_ref"]):
                raise HTTPException(409, "The Pipedrive reference was not found for this account. Use activity:<id> or deal:<id> from Pipedrive.")
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
    def accounts(request: Request, q: str = Query("", max_length=120), limit: int = Query(50, le=200), p: Principal = Depends(require("accounts.read"))):
        from .search import all_like, tokens
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            toks = tokens(q) or ([q.strip()] if q.strip() else [])
            match, margs = all_like("concat_ws(' ', o.display_name, o.canonical_domain)", toks) if toks else ("TRUE", [])
            return {"items": [dict(r, id=str(r["id"])) for r in qa(conn,
                f"SELECT o.id, o.display_name, o.country_code, o.segment, o.in_master_list, o.employees_estimate, o.employees_basis, o.employees_uncertain, c.owner_id AS crm_owner_id, "   # noqa: S608
                f"(SELECT count(*) FROM initiatives i WHERE i.owner_org_id=o.id AND i.status='active') AS initiatives FROM organizations o LEFT JOIN crm_organizations c ON c.organization_id=o.id AND c.link_status='linked' "
                f"WHERE ({where}) AND ({match}) ORDER BY o.in_master_list DESC, o.display_name LIMIT %s", [*params, *margs, limit])]}

    @app.get("/api/accounts/{oid}")
    def account(oid: str, request: Request, target: bool = False, p: Principal = Depends(require("accounts.read"))):
        from . import account_page as page          # the company page's "why now" and repeat-free opportunities
        try:
            uuid.UUID(oid)
        except ValueError:
            raise HTTPException(404, "not found") from None
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            focus = account_workspace_sql(research=p.can("research.request")) if target else "TRUE"
            o = q1(conn, f"SELECT o.*, CASE WHEN {target_sql()} THEN 'target' WHEN {target_sql(review=True)} THEN 'review' ELSE 'other' END AS target_qualification FROM organizations o WHERE o.id=%s AND ({where}) AND {focus}", [oid, *params])       # noqa: S608
            if not o:
                raise HTTPException(404, "not found")
            crm = q1(conn, "SELECT crm_id, owner_id, open_deals_count, synced_at FROM crm_organizations WHERE organization_id=%s AND link_status='linked'", (oid,))
            hyps = qa(conn, "SELECT h.*, i.title AS initiative_title FROM opportunity_hypotheses h JOIN initiatives i ON i.id=h.initiative_id WHERE h.organization_id=%s AND h.state NOT IN ('superseded') AND (NOT %s OR h.route_type <> 'procurement') ORDER BY h.score_total DESC, h.created_at", (oid, target))
            inits = qa(conn, "SELECT id, title, title_is_official, stage, programme_domain, technology_tags, research_gaps, budget_verified, last_material_event_at, next_milestone_at FROM initiatives WHERE owner_org_id=%s AND status='active'", (oid,))
            sources = qa(conn, "SELECT sheet_title, source_file_id, row_number FROM account_source_links WHERE organization_id=%s", (oid,))
            # what the lead list itself says about the account (unverified, labelled as such on screen)
            lp = q1(conn, "SELECT original FROM legacy_account_research WHERE organization_id=%s AND kind='master_account' ORDER BY observed_at DESC LIMIT 1", (oid,))
            list_profile = {k: v for k, v in (lp["original"] if lp else {}).items() if k in LIST_PROFILE_KEYS and str(v or "").strip()}
            list_signals = page.dedupe_list_signals([
                {"event": r["original"].get("Verified event"), "date": r["original"].get("Event date"), "category": r["original"].get("Signal type"),
                 "source_url": r["original"].get("Source URL") if str(r["original"].get("Source URL") or "").lower().startswith(("http://", "https://")) else None,
                 "confidence": r["original"].get("Evidence confidence"), "implication": r["original"].get("Potential engineering relevance")}
                for r in qa(conn, "SELECT original FROM legacy_account_research WHERE organization_id=%s AND kind='buying_signal_log' "
                                  "ORDER BY original->>'Event date' DESC NULLS LAST, observed_at DESC LIMIT 40", (oid,))])     # the list logs one story several times
            signals = qa(conn, f"SELECT {STORY_KEY_SQL} AS story_key, s.rule_id, s.demand_tier, s.strength, CASE WHEN s.status='active' AND (s.expires_at IS NULL OR s.expires_at>now()) AND e.lifecycle_status='active' THEN 'active' ELSE 'historical' END AS status, s.effective_from, s.expires_at, e.id::text AS event_id, e.summary, e.event_at, e.pub_at, e.first_observed_at, e.verified_at, s.evidence_claim_ids FROM signals s JOIN events e ON e.id=s.event_id AND e.subject_org_id=s.organization_id WHERE s.organization_id=%s AND (NOT %s OR e.lane <> 'public') ORDER BY s.effective_from DESC", (oid, target))
            for signal in signals:
                signal["evidence"] = evidence_for(conn, list(signal.pop("evidence_claim_ids") or []))
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
            if score and score.get("top"):
                names = {x["id"]: x["signal"] for x in qa(conn, "SELECT id, signal FROM legacy_signals WHERE id = ANY(%s)", ([t["rule_id"] for t in score["top"]],))}
                score["top"] = [{**t, "name": names.get(t["rule_id"])} for t in score["top"]]      # sellers see what the signal means, not just its code
            now = datetime.now(timezone.utc)
            lead = leading_signals(conn, [oid], commercial_only=target).get(oid)
            opportunities = page.opportunity_cards(conn, hyps, now)          # one card per programme: its routes and same-kind repeats folded in
            profile = q1(conn, "SELECT content FROM account_profiles WHERE organization_id=%s AND status='done' ORDER BY created_at DESC LIMIT 1", (oid,))
            open_tenders = qa(conn, "SELECT title, deadline_at, published_at, official_url FROM procurement_notices WHERE buyer_org_id=%s AND status IN ('open','amended') "
                                    "AND (deadline_at IS NULL OR deadline_at > now()) ORDER BY deadline_at NULLS LAST LIMIT 3", (oid,))
            why = page.why_now(lead_signal=lead, signals=signals, list_signals=list_signals, recent=((profile or {}).get("content") or {}).get("recent") or [],
                               tenders=open_tenders, list_profile=list_profile, opportunities=opportunities, now=now)
            return {"account": dict(o, id=str(o["id"])), "list_profile": list_profile, "list_signals": list_signals, "buying_signal": lead, "score": score, "crm": crm, "ownership_verified": bool(crm and crm["owner_id"]), "source_rows": sources,
                    "opportunities": opportunities, "why_now": why, "initiatives": [dict(i, id=str(i["id"])) for i in inits], "signals": signals, "suppliers": suppliers,
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
                 limit: int = Query(50, le=200), target: bool = False, p: Principal = Depends(require("explorer.read"))):
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            cov = coverage_health(conn, p)
            focus = target_sql() if target else "TRUE"
            if entity == "companies":
                sql = ("SELECT o.id, o.display_name, o.country_code, o.segment, o.in_master_list, o.employees_estimate FROM organizations o WHERE (" + where + f") AND {focus} "
                       "AND (%s='' OR ri_fold(o.display_name) LIKE '%%'||ri_fold(%s)||'%%') AND (%s::text IS NULL OR o.country_code=%s) AND (%s::bool IS NULL OR o.in_master_list=%s) ORDER BY o.display_name LIMIT %s")
                rows = qa(conn, sql, [*params, q, q, country, country, existing, existing, limit]); basis = "alphabetical (ICP fit not applied)"
            elif entity == "procurement":
                if target:
                    raise HTTPException(404, "Not found.")
                rows = qa(conn, "SELECT n.notice_id, n.buyer_name, n.title, n.status, n.deadline_at, n.deadline_tz, n.last_status_check_at, n.official_url FROM procurement_notices n "
                                "LEFT JOIN organizations o ON o.id=n.buyer_org_id WHERE (n.buyer_org_id IS NULL OR (" + where + ")) AND (%s='' OR ri_fold(n.title) LIKE '%%'||ri_fold(%s)||'%%') "
                                "ORDER BY (n.status IN ('open','amended')) DESC, n.deadline_at LIMIT %s", [*params, q, q, limit]); basis = "open notices first, then deadline"
            elif entity == "events":
                rows = qa(conn, "SELECT e.id, e.event_type, e.summary, e.event_at, e.pub_at, e.first_observed_at, e.verification_status, e.is_historical_replay FROM events e LEFT JOIN organizations o ON o.id=e.subject_org_id "
                                "WHERE (e.subject_org_id IS NULL OR (" + where + f")) AND {focus} AND (NOT %s OR e.lane <> 'public') AND (%s='' OR ri_fold(e.summary) LIKE '%%'||ri_fold(%s)||'%%') ORDER BY coalesce(e.event_at, e.pub_at) DESC NULLS LAST LIMIT %s", [*params, target, q, q, limit]); basis = "freshness (event date, publication date shown separately)"
            else:
                rows = qa(conn, "SELECT h.id, o.display_name, h.route_type, h.counterparty_name, h.priority, h.state, h.score_total, h.evidence_confidence, h.access_status, h.external_demand_status, i.title, i.stage "
                                "FROM opportunity_hypotheses h JOIN organizations o ON o.id=h.organization_id JOIN initiatives i ON i.id=h.initiative_id WHERE (" + where + f") AND {focus} AND (NOT %s OR h.route_type <> 'procurement') "
                                "AND (%s='' OR ri_fold(o.display_name) LIKE '%%'||ri_fold(%s)||'%%' OR ri_fold(i.title) LIKE '%%'||ri_fold(%s)||'%%') AND (%s::text IS NULL OR o.country_code=%s) AND (%s::bool IS NULL OR o.in_master_list=%s) "
                                "AND (%s::text IS NULL OR i.stage=%s) AND (%s::text IS NULL OR h.evidence_confidence=%s) AND (%s::bool IS NULL OR (h.priority='blocked')=%s) "
                                "AND (%s::text IS NULL OR EXISTS (SELECT 1 FROM signals s JOIN initiative_events ie ON ie.event_id=s.event_id WHERE ie.initiative_id=i.id AND s.rule_id LIKE %s||'-%%')) "
                                "ORDER BY h.score_total DESC LIMIT %s", [*params, target, q, q, q, country, country, existing, existing, stage, stage, confidence, confidence, blocked, blocked, signal_family, signal_family, limit])
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

    def approval_in_scope(conn, p: Principal, aid: str, *, executing: bool = False) -> dict[str, Any]:
        """A person may only decide or run an approval for accounts they can see; bulk or unknown targets need a manager or an administrator."""
        a = q1(conn, "SELECT * FROM approvals WHERE id::text=%s", (aid,))
        if not a:
            raise HTTPException(404, "not found")
        senior = p.role in {"sales_manager", "admin"}
        if executing and not senior and p.email not in {a["requested_by"], a["approver"]}:
            raise HTTPException(403, "Only the requester, the approver, a manager or an administrator can run this approval.")
        orgs = appr.approval_org_ids(conn, a)
        if orgs is None or (a["kind"] == "signal_campaign_push" and not senior):
            if not senior:
                raise HTTPException(403, "A manager or an administrator must decide this approval.")
            return a
        where, params = org_scope_sql(p, "o.id")
        visible = {str(r["id"]) for r in qa(conn, f"SELECT o.id FROM organizations o WHERE o.id = ANY(%s::uuid[]) AND ({where})", [orgs, *params])}      # noqa: S608
        if set(orgs) - visible:
            raise HTTPException(404, "not found")       # same answer as a missing approval: do not reveal accounts outside scope
        return a

    @app.post("/api/approvals/{aid}/decision")
    def approval_decision(aid: str, body: ApprovalDecisionIn, request: Request, p: Principal = Depends(require("approvals.decide"))):
        with tx(request) as conn:
            approval_in_scope(conn, p, aid)
            try:
                a = appr.decide(conn, aid, approver=p.email, approver_role=p.role, approve=body.approve)
            except appr.ApprovalError as exc:
                raise HTTPException(409, str(exc)) from exc
            return {"status": a["status"]}

    @app.post("/api/approvals/{aid}/execute")
    def approval_execute(aid: str, request: Request, p: Principal = Depends(require("approvals.decide"))):
        with tx(request) as conn:
            kind = approval_in_scope(conn, p, aid, executing=True)["kind"]
            if kind == "sequence_enrolment":
                from ..outbound import enrol as enrol_mod
                from ..outbound.lemlist import LemlistClient, LemlistError
                try:
                    client = LemlistClient(settings.lemlist_api_key) if settings.lemlist_api_key else None
                    return enrol_mod.execute_enrolment(conn, aid, client, settings)
                except (appr.ApprovalError, LemlistError, ConnectorBlocked) as exc:
                    raise HTTPException(409, str(exc)) from exc
            if kind == "signal_campaign_push":
                from ..outbound import signal_campaigns as sc_mod
                from ..outbound.lemlist import LemlistClient, LemlistError
                try:
                    client = LemlistClient(settings.lemlist_api_key) if settings.lemlist_api_key else None
                    return sc_mod.execute_push(conn, aid, client, settings)
                except (appr.ApprovalError, LemlistError, ConnectorBlocked) as exc:
                    raise HTTPException(409, str(exc)) from exc
            if kind == "contact_enrichment":
                from ..enrichment import contacts as contacts_mod
                try:
                    return contacts_mod.execute_reveal(conn, aid, contacts_mod.make_provider(settings), settings)
                except (appr.ApprovalError, ConnectorBlocked) as exc:
                    raise HTTPException(409, str(exc)) from exc
                except httpx.HTTPError as exc:
                    raise HTTPException(502, f"Apollo could not be reached ({type(exc).__name__}). The approval is spent only if the call went through; check Approvals.") from exc
            if not settings.crm_writes_enabled:
                raise HTTPException(409, "CRM writes are disabled in this environment")
            wf = request.app.state.writer_factory
            if wf is None:
                raise HTTPException(503, "CRM write credential not configured (integration blocked)")
            try:
                res = pd.execute_approved(conn, aid, wf(), settings)
            except (appr.ApprovalError, ConnectorBlocked) as exc:
                raise HTTPException(409, str(exc)) from exc
            except httpx.HTTPError as exc:
                raise HTTPException(502, f"Pipedrive could not be reached or refused the write ({type(exc).__name__}). The approval stays retryable if the outcome was unclear.") from exc
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
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "body is not JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "expected a JSON object")
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
        from ..identity.review import ReviewError, apply_decision        # the same code path the automatic matcher uses
        with tx(request) as conn:
            try:
                r = apply_decision(conn, rid, body.decision, actor=p.email, note=body.note, organization_id=body.organization_id, resolved_by="person")
            except ReviewError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return {"status": r["status"]}

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
    def admin_audit(request: Request, operation: str | None = None, actor: str | None = None, record_type: str | None = None, view: str = "all", since: str = "",
                    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), p: Principal = Depends(require("audit.read"))):
        from .admin_ops import audit_log          # filters, totals and record names (view=changes hides personal-data reads and bulk-load rows)
        with tx(request) as conn:
            return audit_log(conn, view=view, operation=operation or "", actor=actor or "", record_type=record_type or "", since=since, limit=limit, offset=offset)

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
    from . import deadlines as deadlines_api
    deadlines_api.register_deadlines(app)
    from . import renewals_api
    renewals_api.register(app)
    from . import outreach as outreach_api
    outreach_api.register_outreach(app, settings)
    from . import leads as leads_api
    leads_api.register_leads(app)
    from . import search as search_api
    search_api.register(app)
    search_api.register_kb_admin(app)
    from . import sources_admin
    sources_admin.register(app)
    from . import people as people_api
    people_api.register_people(app)
    from . import campaigns as campaigns_api
    campaigns_api.register_campaigns(app, settings)
    from .buying_signals import register_buying_signals; register_buying_signals(app)      # noqa: E702
    from .refresh import register_refresh; register_refresh(app)      # noqa: E702  (Buying signals: how fresh, and "Refresh now")
    from .connections_api import register_connections; register_connections(app, settings)      # noqa: E702  (each person's own Gmail, Lemlist and Bluedot connections)
    from .connections_gmail import register_gmail; register_gmail(app, settings)      # noqa: E702
    from .conversations import register_conversations; register_conversations(app)      # noqa: E702  (the shared timeline of conversations per person and company)
    from .profiles import register_profiles; register_profiles(app)      # noqa: E702  (company research, people, relationship, owner)
    from .admin_ops import register as register_admin_ops; register_admin_ops(app)      # noqa: E702  (automatic matching, source checks, failures, usage)
    from .today import register_today; register_today(app)      # noqa: E702  (Today: who to contact today, and why)
    from .solutions import register_solutions; register_solutions(app)      # noqa: E702  (what fits them best; the person's sales brief)
    return app
