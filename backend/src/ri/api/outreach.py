"""Outreach and integrations API: who can be contacted, approval-gated enrolment, Lemlist webhook, Pipedrive deal proposals, integration status and tests.
Nothing here sends anything by itself. Sending needs a two-person approval AND the RI_OUTBOUND_ENABLED switch."""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .. import approvals as appr
from ..access import Principal, org_scope_sql
from ..audit import audit
from ..config import Settings
from ..db import q1, qa
from ..enrichment import contacts as contacts_mod
from ..outbound import enrol
from ..outbound.lemlist import LemlistClient, LemlistError
from .auth import require


class EnrolIn(BaseModel):
    person_ids: list[str] = Field(min_length=1, max_length=25)


class CapIn(BaseModel):
    credits: float = Field(ge=0, le=100000)


class DealIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    justification: str = Field(min_length=3, max_length=500)


def _visible(conn, p: Principal, oid: str) -> dict[str, Any]:
    where, params = org_scope_sql(p, "o.id")
    row = q1(conn, f"SELECT o.id, o.display_name FROM organizations o WHERE o.id=%s AND ({where})", [oid, *params])     # noqa: S608
    if not row:
        raise HTTPException(404, "not found")
    return row


def integration_status(s: Settings) -> list[dict[str, Any]]:
    def item(key: str, name: str, configured: bool, what: str, how: str, switch: str | None = None, **extra: Any) -> dict[str, Any]:
        return {"key": key, "name": name, "configured": configured, "what": what, "how": how, "switch": switch, **extra}

    return [
        item("lemlist", "Lemlist", bool(s.lemlist_api_key), "Cold sequences. The platform creates the campaign and its leads; a person presses Launch in Lemlist.",
             "Lemlist: Settings, Integrations, API. Set LEMLIST_API_KEY on the API and worker.", "RI_OUTBOUND_ENABLED", switch_on=s.outbound_enabled, testable=True),
        item("lemlist_webhook", "Lemlist replies", bool(s.lemlist_webhook_secret), "Reply, bounce and unsubscribe events flow back here.",
             "Set RI_LEMLIST_WEBHOOK_SECRET to a long random string, then register https://<this app>/webhooks/lemlist/<secret> in Lemlist (Settings, Integrations, Webhooks)."),
        item("pipedrive", "Pipedrive", bool(s.pipedrive_api_token and s.pipedrive_domain), "Account ownership checks, open deals, and approved deals, notes and activities.",
             "Pipedrive: Settings, Personal preferences, API. Set PIPEDRIVE_API_TOKEN and PIPEDRIVE_DOMAIN (the part before .pipedrive.com).", "RI_CRM_WRITES_ENABLED",
             switch_on=s.crm_writes_enabled, testable=True, domain=s.pipedrive_domain),
        item("apollo", "Apollo", bool(s.apollo_api_key), "Find decision makers and reveal verified emails. Paid reveals need a credit cap and an approval.",
             "Set APOLLO_API_KEY. Paid reveals stay off until RI_PAID_ENRICHMENT_ENABLED is true and a monthly cap is set.", "RI_PAID_ENRICHMENT_ENABLED",
             switch_on=s.paid_enrichment_enabled, testable=True),
        item("anthropic", "Anthropic", bool(s.anthropic_api_key), "Reads pages, writes research briefs and outreach drafts under a token cap.", "ANTHROPIC_API_KEY is set on the API and worker."),
        item("google", "Google Sheets", bool(s.google_service_account_json or s.google_credentials_file), "Live sync of the two workbooks.",
             "Share both sheets with the service account and set GOOGLE_SERVICE_ACCOUNT_JSON. Or upload the xlsx files under Imports."),
        item("slack", "Slack alerts", bool(getattr(s, "slack_webhook_url", None)), "Alerts when a source fails or the cap is close.", "api.slack.com/apps, Incoming Webhooks, then set SLACK_WEBHOOK_URL."),
    ]


def test_integration(key: str, s: Settings) -> dict[str, Any]:
    """Cheap, read-only calls. A pass proves the key works, nothing more."""
    try:
        if key == "lemlist":
            if not s.lemlist_api_key:
                return {"ok": False, "detail": "LEMLIST_API_KEY is not set"}
            t = LemlistClient(s.lemlist_api_key).team()
            return {"ok": True, "detail": f"Connected to team {t.get('name') or t.get('_id') or ''}".strip()}
        if key == "pipedrive":
            if not (s.pipedrive_api_token and s.pipedrive_domain):
                return {"ok": False, "detail": "PIPEDRIVE_API_TOKEN or PIPEDRIVE_DOMAIN is not set"}
            r = httpx.get(f"https://{s.pipedrive_domain}.pipedrive.com/api/v1/users/me", headers={"x-api-token": s.pipedrive_api_token}, timeout=20)
            return {"ok": r.status_code == 200, "detail": "Connected" if r.status_code == 200 else f"Pipedrive answered {r.status_code}"}
        if key == "apollo":
            if not s.apollo_api_key:
                return {"ok": False, "detail": "APOLLO_API_KEY is not set"}
            r = httpx.get("https://api.apollo.io/api/v1/auth/health", headers={"x-api-key": s.apollo_api_key}, timeout=20)
            return {"ok": r.status_code == 200, "detail": "Key accepted" if r.status_code == 200 else f"Apollo answered {r.status_code}"}
    except LemlistError as exc:
        return {"ok": False, "detail": str(exc)}
    except httpx.HTTPError as exc:
        return {"ok": False, "detail": f"Could not reach the service ({type(exc).__name__})"}
    return {"ok": False, "detail": "no test for this integration"}


def register_outreach(app: FastAPI, settings: Settings) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    @app.get("/api/accounts/{oid}/contacts-plan")
    def contacts_plan(oid: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            _visible(conn, p, oid)
            return enrol.plan_contacts(conn, oid)

    @app.post("/api/campaigns/{did}/enrol")
    def enrol_request(did: str, body: EnrolIn, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            d = q1(conn, "SELECT organization_id FROM campaign_drafts WHERE id=%s", (did,))
            if not d:
                raise HTTPException(404, "not found")
            _visible(conn, p, str(d["organization_id"]))
            try:
                return enrol.request_enrolment(conn, did, body.person_ids, by=p.email, requester_crm_user=p.pipedrive_user_id)
            except appr.ApprovalError as exc:
                raise HTTPException(409, str(exc)) from exc

    @app.get("/api/campaigns/{did}/enrolments")
    def enrolments(did: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            d = q1(conn, "SELECT organization_id FROM campaign_drafts WHERE id=%s", (did,))
            if not d:
                raise HTTPException(404, "not found")
            _visible(conn, p, str(d["organization_id"]))
            rows = qa(conn, "SELECT e.id, e.email, e.status, e.last_event_at, e.created_at, pe.full_name FROM outbound_enrolments e LEFT JOIN people pe ON pe.id=e.person_id "
                            "WHERE e.campaign_draft_id=%s ORDER BY e.created_at", (did,))
            return {"items": [dict(r, id=str(r["id"])) for r in rows], "outbound_enabled": settings.outbound_enabled, "lemlist_configured": bool(settings.lemlist_api_key)}

    @app.post("/webhooks/lemlist/{secret}")
    async def lemlist_webhook(secret: str, request: Request):
        if not settings.lemlist_webhook_secret:
            raise HTTPException(503, "webhook secret not configured")
        if not enrol.verify_webhook_secret(secret, settings.lemlist_webhook_secret):
            with tx(request) as conn:
                conn.execute("INSERT INTO audit_events(actor, operation, record_type) VALUES ('webhook','auth_failed','lemlist_webhook')")
            raise HTTPException(401, "bad credentials")
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "body is not JSON") from exc
        events = body if isinstance(body, list) else [body]
        out = []
        from ..people import apply_lemlist_activity
        with tx(request) as conn:
            for ev in events[:50]:
                if isinstance(ev, dict):
                    out.append(apply_lemlist_activity(conn, ev))      # stores the activity for the people database and updates the enrolment
        return {"accepted": True, "results": out}

    @app.post("/api/accounts/{oid}/crm/propose-deal")
    def propose_deal(oid: str, body: DealIn, request: Request, p: Principal = Depends(require("crm.propose"))):
        with tx(request) as conn:
            _visible(conn, p, oid)
            crm = q1(conn, "SELECT crm_id, owner_id FROM crm_organizations WHERE organization_id=%s AND link_status='linked'", (oid,))
            if not crm:
                raise HTTPException(409, "This account is not linked to Pipedrive, so ownership cannot be checked. Link it first.")
            if crm["owner_id"] and p.pipedrive_user_id != crm["owner_id"] and p.role != "sales_manager":
                raise HTTPException(403, "This account is owned by another seller: route it to the owner.")
            payload = {"title": body.title.strip(), "pipeline_id": settings.pipedrive_pipeline_id, "stage_id": settings.pipedrive_stage_id}
            a = appr.request(conn, kind="crm_write", target_system="pipedrive", target_id=str(crm["crm_id"]), operation="create_deal", payload=payload, requested_by=p.email,
                             justification=body.justification, idempotency_key=f"crm-deal:{oid}:{appr.payload_hash(payload)[:16]}", ttl_hours=settings.approval_ttl_hours)
            return {"approval_id": str(a["id"]), "status": a["status"], "diff": {"target": f"Pipedrive organisation {crm['crm_id']}", "operation": "create_deal", "payload": payload}}

    @app.get("/api/admin/integrations")
    def integrations(request: Request, p: Principal = Depends(require("admin.costs"))):
        from .. import usage
        items = integration_status(settings)
        with tx(request) as conn:
            row = q1(conn, "SELECT cap, spent, reserved FROM provider_budgets WHERE provider='apollo' AND kind='contact_enrichment' AND period=%s", (usage.period_now(),))
        with tx(request) as conn:
            blocked = q1(conn, "SELECT max(updated_at) AS at FROM jobs WHERE last_error_class='provider_billing' AND updated_at > now() - interval '12 hours'")
        for i in items:
            if i["key"] == "anthropic" and blocked and blocked["at"]:
                i["blocked_reason"] = "Anthropic reports the credit balance is too low. Reading and writing are paused until it is topped up in the Anthropic console."
            if i["key"] == "apollo":
                i["credits_per_reveal"] = settings.apollo_credits_per_reveal
                i["cap"] = float(row["cap"]) if row else 0.0
                i["spent"] = float(row["spent"]) if row else 0.0
        return {"items": items}

    @app.post("/api/admin/integrations/apollo/cap")
    def apollo_cap(body: CapIn, request: Request, p: Principal = Depends(require("admin.costs"))):
        from .. import budget, usage
        with tx(request) as conn:
            budget.set_cap(conn, "apollo", "contact_enrichment", usage.period_now(), body.credits, "credits")
            audit(conn, actor=p.email, operation="apollo_cap_set", record_type="integration", record_id="apollo", detail={"credits": body.credits})
        return {"cap": body.credits}

    @app.post("/api/accounts/{oid}/find-contacts")
    def find_contacts(oid: str, request: Request, p: Principal = Depends(require("research.request"))):
        if not settings.apollo_api_key:
            raise HTTPException(409, "Apollo is not connected yet. Add APOLLO_API_KEY under Integrations.")
        with tx(request) as conn:
            _visible(conn, p, oid)
            try:
                return contacts_mod.find_candidates(conn, contacts_mod.make_provider(settings), oid, by=p.email)
            except appr.ApprovalError as exc:
                raise HTTPException(409, str(exc)) from exc
            except httpx.HTTPError as exc:
                raise HTTPException(502, f"Apollo could not be reached ({type(exc).__name__})") from exc

    @app.get("/api/accounts/{oid}/contact-candidates")
    def contact_candidates(oid: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            _visible(conn, p, oid)
            return {"items": contacts_mod.candidates_for(conn, oid), "apollo_configured": bool(settings.apollo_api_key), "reveal_price": settings.apollo_credits_per_reveal,
                    "paid_enrichment_enabled": settings.paid_enrichment_enabled}

    @app.post("/api/contact-candidates/{cid}/reveal")
    def reveal_request(cid: str, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            c = q1(conn, "SELECT organization_id FROM contact_candidates WHERE id=%s", (cid,))
            if not c:
                raise HTTPException(404, "not found")
            _visible(conn, p, str(c["organization_id"]))
            try:
                return contacts_mod.request_reveal(conn, cid, by=p.email, settings=settings)
            except appr.ApprovalError as exc:
                raise HTTPException(409, str(exc)) from exc

    @app.post("/api/contact-candidates/{cid}/dismiss")
    def dismiss_candidate(cid: str, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            c = q1(conn, "SELECT organization_id FROM contact_candidates WHERE id=%s", (cid,))
            if not c:
                raise HTTPException(404, "not found")
            _visible(conn, p, str(c["organization_id"]))
            conn.execute("UPDATE contact_candidates SET status='dismissed' WHERE id=%s AND status='found'", (cid,))
            return {"status": "dismissed"}

    @app.post("/api/admin/integrations/{key}/test")
    def integrations_test(key: str, request: Request, p: Principal = Depends(require("admin.costs"))):
        res = test_integration(key, settings)
        with tx(request) as conn:
            audit(conn, actor=p.email, operation="integration_tested", record_type="integration", record_id=key, detail={"ok": res["ok"]})
        return res
