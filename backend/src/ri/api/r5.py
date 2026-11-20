"""R5 routes: playbook (plays, buyer committee, access, discovery), drafts, procurement tracking, outcomes, proof assets, metrics."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ..access import Principal, org_scope_sql
from ..audit import audit
from ..buyer import drafts, graph, plays
from ..db import q1, qa
from ..deal.progress import deal_view
from ..metrics import prometheus_text, quality_snapshot
from ..outcomes import cost_per_accepted, funnel, record
from ..readmodel import card, evidence_for
from .auth import principal_dep, require


class RoleIn(BaseModel):
    role: str
    person_id: str | None = None
    role_title: str | None = None
    role_is_verified: bool = False
    initiative_responsibility_verified: bool = False


class RouteIn(BaseModel):
    kind: str
    evidence: str
    verified: bool = False
    person_id: str | None = None
    intermediary: str | None = None


class DraftIn(BaseModel):
    person_id: str | None = None
    proof_asset_ids: list[str] = Field(default_factory=list)


class ProcIn(BaseModel):
    status: str = Field(pattern="^(open|in_progress|blocked|done|not_applicable)$")
    due_at: datetime | None = None
    owner: str | None = None
    notes: str | None = None


class OutcomeIn(BaseModel):
    kind: str
    crm_ref: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class ProofIn(BaseModel):
    title: str
    service_tags: list[str] = Field(default_factory=list)
    approved_audience: str
    client_name: str | None = None
    approval_owner: str | None = None
    approved_by: str | None = None
    source_doc: str | None = None
    restrictions: str | None = None


def register(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    def vis(conn, p: Principal, hid: str) -> dict[str, Any]:
        where, params = org_scope_sql(p, "h.organization_id")
        h = q1(conn, f"SELECT h.* FROM opportunity_hypotheses h WHERE h.id=%s AND ({where})", [hid, *params])      # noqa: S608
        if not h:
            raise HTTPException(404, "not found")
        return h

    @app.get("/api/hypotheses/{hid}/playbook")
    def playbook(hid: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            h = vis(conn, p, hid)
            play = plays.select_play(conn, h)
            c = card(conn, h, datetime.now(timezone.utc))
            facts = [e for e in evidence_for(conn, h["supporting_claim_ids"], 20) if e["anchor_verified"] or e["kind"] == "structured"]
            return {"play": {k: play.get(k) for k in ("id", "name", "primary_buyer", "objective", "disqualifiers", "reason")},
                    "discovery_questions": plays.discovery_questions(conn, h, play) if play.get("id") else [],
                    "committee": graph.committee(conn, hid), "access_routes": graph.ranked_access(conn, hid),
                    "procurement_tracking": qa(conn, "SELECT item, status, due_at, owner, notes FROM procurement_tracking WHERE hypothesis_id=%s ORDER BY item", (hid,)),
                    "deal": deal_view(conn, hid),
                    "meeting_brief": {"verified_facts": facts, "assumptions": [u["unknown"] for u in (h["critical_unknowns"] or [])],
                                      "buyer_confirmed": h["buyer_status"] == "buyer_confirmed" or h["external_demand_status"] == "buyer_confirmed",
                                      "counterevidence": c["counterevidence"], "next_action": c["next_action"]}}

    @app.post("/api/hypotheses/{hid}/stakeholders")
    def add_stakeholder(hid: str, body: RoleIn, request: Request, p: Principal = Depends(require("hypothesis.review"))):
        with tx(request) as conn:
            vis(conn, p, hid)
            try:
                return {"id": graph.add_role(conn, hid, body.role, actor=p.email, person_id=body.person_id, role_title=body.role_title, role_is_verified=body.role_is_verified,
                                             initiative_responsibility_verified=body.initiative_responsibility_verified)}
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

    @app.post("/api/hypotheses/{hid}/access-routes")
    def add_route(hid: str, body: RouteIn, request: Request, p: Principal = Depends(require("hypothesis.review"))):
        with tx(request) as conn:
            vis(conn, p, hid)
            try:
                return {"id": graph.add_access_route(conn, hid, body.kind, evidence=body.evidence, actor=p.email, verified=body.verified, person_id=body.person_id, intermediary=body.intermediary)}
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

    @app.post("/api/hypotheses/{hid}/draft")
    def make_draft(hid: str, body: DraftIn, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            vis(conn, p, hid)
            out = drafts.compose(conn, hid, actor=p.email, actor_crm_user=p.pipedrive_user_id, person_id=body.person_id, proof_asset_ids=body.proof_asset_ids)
            conn.execute("INSERT INTO product_events(user_id, event, hypothesis_id, detail) VALUES (%s,'outreach_draft_requested',%s,%s)", (p.user_id, hid, '{"status":"%s"}' % out["status"]))
            return out

    @app.put("/api/hypotheses/{hid}/procurement/{item}")
    def procurement(hid: str, item: str, body: ProcIn, request: Request, p: Principal = Depends(require("hypothesis.review"))):
        with tx(request) as conn:
            vis(conn, p, hid)
            try:
                conn.execute("INSERT INTO procurement_tracking(hypothesis_id, item, status, due_at, owner, notes, updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                             "ON CONFLICT (hypothesis_id, item) DO UPDATE SET status=EXCLUDED.status, due_at=EXCLUDED.due_at, owner=EXCLUDED.owner, notes=EXCLUDED.notes, updated_by=EXCLUDED.updated_by, updated_at=now()",
                             (hid, item, body.status, body.due_at, body.owner, body.notes, p.email))
            except Exception as exc:  # noqa: BLE001 - invalid enum/CHECK
                raise HTTPException(422, "invalid procurement item") from exc
            return {"ok": True}

    @app.post("/api/hypotheses/{hid}/outcomes")
    def outcome(hid: str, body: OutcomeIn, request: Request, p: Principal = Depends(require("hypothesis.review"))):
        with tx(request) as conn:
            vis(conn, p, hid)
            try:
                oid = record(conn, hid, body.kind, actor=p.email, crm_ref=body.crm_ref, detail=body.detail)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(422, "invalid outcome (late-funnel outcomes need a Pipedrive reference)") from exc
            conn.execute("INSERT INTO product_events(user_id, event, hypothesis_id, detail) VALUES (%s,%s,%s,'{}')", (p.user_id, body.kind, hid))
            return {"id": oid}

    @app.get("/api/proof-assets")
    def proof_list(request: Request, p: Principal = Depends(principal_dep)):
        with tx(request) as conn:
            if p.can("proof.manage"):
                rows = qa(conn, "SELECT * FROM proof_assets WHERE status='active' ORDER BY created_at DESC")
            elif p.can("draft.create"):
                rows = qa(conn, "SELECT id, title, service_tags, approved_audience, effective_date FROM proof_assets WHERE status='active' AND approved_audience='external_approved'")
            else:
                raise HTTPException(403, "missing permission")
            return {"items": [dict(r, id=str(r["id"])) for r in rows]}

    @app.post("/api/proof-assets")
    def proof_create(body: ProofIn, request: Request, p: Principal = Depends(require("proof.manage"))):
        with tx(request) as conn:
            if body.approved_audience == "external_approved" and not (body.approved_by and body.approval_owner):
                raise HTTPException(422, "external approval needs approval_owner and approved_by")
            row = conn.execute("INSERT INTO proof_assets(title, service_tags, approved_audience, client_name, approval_owner, approved_by, source_doc, restrictions) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                               (body.title, body.service_tags, body.approved_audience, body.client_name, body.approval_owner, body.approved_by, body.source_doc, body.restrictions)).fetchone()
            audit(conn, actor=p.email, operation="proof_asset_created", record_type="proof_asset", record_id=str(row["id"]), detail={"audience": body.approved_audience})
            return {"id": str(row["id"])}

    @app.get("/api/admin/funnel")
    def admin_funnel(request: Request, group_by: str = "route_type", p: Principal = Depends(require("admin.rules"))):
        with tx(request) as conn:
            try:
                return {**funnel(conn, group_by=group_by), "cost": cost_per_accepted(conn)}
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

    @app.get("/api/admin/metrics")
    def admin_metrics(request: Request, p: Principal = Depends(require("admin.rules"))):
        with tx(request) as conn:
            return quality_snapshot(conn)

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics(request: Request, p: Principal = Depends(require("admin.rules"))):
        with tx(request) as conn:
            return prometheus_text(conn)
