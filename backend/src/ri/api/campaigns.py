"""Signal campaigns API: pick buying signals, review the audience and the copy, ask a manager to approve, then push to Lemlist (not launched)."""
from __future__ import annotations

import re
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..access import Principal, org_scope_sql
from ..audit import audit
from ..config import Settings
from ..db import jb, q1, qa
from ..outbound import signal_campaigns as sc
from .auth import require


class CreateIn(BaseModel):
    rule_ids: list[str] = Field(min_length=1, max_length=8)
    name: str | None = Field(default=None, max_length=120)
    days: int = Field(default=90, ge=7, le=365)
    region: str = Field(default="", pattern="^(|uk|dach|nordics|poland|europe|us|other)$")
    per_account: int = Field(default=2, ge=1, le=5)


class StepIn(BaseModel):
    day: int = Field(ge=0, le=60)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=3000)
    claims_used: list[str] = Field(default_factory=list, max_length=5)


class StepsIn(BaseModel):
    steps: list[StepIn] = Field(min_length=1, max_length=6)


class MemberIn(BaseModel):
    included: bool | None = None
    hook: str | None = Field(default=None, min_length=10, max_length=400)


def _status(c: dict[str, Any], approval: dict[str, Any] | None) -> str:
    if c["status"] == "pending_approval" and approval:
        return {"approved": "approved", "rejected": "rejected", "expired": "expired", "executing": "pushing", "failed": "push_failed"}.get(approval["status"], "pending_approval")
    return c["status"]


def register_campaigns(app: FastAPI, settings: Settings) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    def load(conn, cid: str, p: Principal) -> dict[str, Any]:
        c = q1(conn, "SELECT * FROM signal_campaigns WHERE id::text=%s", (cid,))
        if not c or not (c["created_by"] == p.email or p.can("approvals.read_all")):
            raise HTTPException(404, "not found")
        return c

    def editable(c: dict[str, Any], p: Principal) -> None:
        if c["created_by"] != p.email and p.role not in {"sales_manager", "admin"}:
            raise HTTPException(403, "Only the person who created the campaign or a manager can change it.")
        if c["status"] != "draft":
            raise HTTPException(409, "Only a draft can be changed. Move it back to draft first.")

    @app.get("/api/signal-campaigns/options")
    def options(request: Request, days: int = Query(90, ge=7, le=365), target: bool = False, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            return {"items": sc.signal_options(conn, p, days, target=target), "days": days,
                    "note": "Counts are accounts you can see with the signal live and verified in this window. Reachable means at least one contact has an email address (verified or not)."}

    @app.get("/api/signal-campaigns")
    def list_campaigns(request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            rows = qa(conn, """SELECT c.id, c.name, c.rule_ids, c.status, c.filters, c.created_by, c.created_at, c.updated_at, c.pushed_at, c.lemlist_campaign_id,
                       a.status AS approval_status, a.approver,
                       count(m.*) FILTER (WHERE m.included AND m.person_id IS NOT NULL) AS people,
                       count(DISTINCT m.organization_id) FILTER (WHERE m.included AND m.person_id IS NOT NULL) AS accounts,
                       count(DISTINCT m.organization_id) FILTER (WHERE NOT m.included AND m.person_id IS NULL) AS accounts_without_contact,
                       (SELECT count(*) FROM outbound_enrolments oe WHERE oe.signal_campaign_id=c.id AND oe.status IN ('replied','interested')) AS replies
                FROM signal_campaigns c LEFT JOIN approvals a ON a.id=c.approval_id LEFT JOIN signal_campaign_members m ON m.campaign_id=c.id
                WHERE c.status <> 'archived' AND (c.created_by=%s OR %s) GROUP BY c.id, a.status, a.approver ORDER BY c.updated_at DESC LIMIT 100""",
                      (p.email, p.can("approvals.read_all")))
            return {"items": [dict(r, id=str(r["id"]), status=_status(r, {"status": r["approval_status"]} if r["approval_status"] else None)) for r in rows],
                    "outbound_enabled": settings.outbound_enabled, "lemlist_configured": bool(settings.lemlist_api_key)}

    @app.post("/api/signal-campaigns")
    def create(body: CreateIn, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            known = {r["rule_id"] for r in qa(conn, "SELECT rule_id FROM signal_rule_versions WHERE status='active' AND rule_id = ANY(%s)", (body.rule_ids,))}
            unknown = sorted(set(body.rule_ids) - known)
            if unknown:
                raise HTTPException(422, f"Not an active signal: {', '.join(unknown)}")
            names = {r["id"]: r["signal"] for r in qa(conn, "SELECT id, signal FROM legacy_signals WHERE id = ANY(%s)", (body.rule_ids,))}
            short = lambda label: re.split(r"\s*[(:;,]", label, maxsplit=1)[0].strip()      # noqa: E731  ("Funding round (Series B+ ...)" -> "Funding round")
            name = (body.name or "").strip() or " + ".join(short(names[r]) for r in body.rule_ids)
            filters = {"days": body.days, "region": body.region, "per_account": body.per_account}
            row = conn.execute("INSERT INTO signal_campaigns(name, rule_ids, filters, steps, created_by) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                               (name[:120], body.rule_ids, jb(filters), jb(sc.DEFAULT_STEPS), p.email)).fetchone()
            cid = str(row["id"])      # type: ignore[index]
            stats = sc.build_audience(conn, cid, p)
            sc.review_campaign(conn, cid)
            audit(conn, actor=p.email, operation="signal_campaign_created", record_type="signal_campaign", record_id=cid, detail={"rules": body.rule_ids, **stats})
            return {"id": cid, **stats}

    @app.get("/api/signal-campaigns/{cid}")
    def detail(cid: str, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            c = load(conn, cid, p)
            where, params = org_scope_sql(p, "o.id")
            members = qa(conn, f"""SELECT m.id, m.organization_id, m.person_id, m.included, m.exclusion, m.signal, m.variables, m.status,
                       o.display_name AS company, o.country_code, pe.full_name, e.title,
                       (SELECT oe.status FROM outbound_enrolments oe WHERE oe.signal_campaign_id=m.campaign_id AND oe.person_id=m.person_id LIMIT 1) AS outcome
                FROM signal_campaign_members m JOIN organizations o ON o.id=m.organization_id
                LEFT JOIN people pe ON pe.id=m.person_id
                LEFT JOIN LATERAL (SELECT title FROM employment WHERE person_id=m.person_id AND organization_id=m.organization_id LIMIT 1) e ON TRUE
                WHERE m.campaign_id=%s AND ({where}) ORDER BY m.included DESC, o.display_name, pe.full_name""", [cid, *params])      # noqa: S608
            approval = q1(conn, "SELECT id, status, approver, approved_at, result FROM approvals WHERE id=%s", (c["approval_id"],)) if c["approval_id"] else None
            sample = next((m for m in members if m["included"] and m["person_id"]), None)
            return {"campaign": {**{k: c[k] for k in ("name", "rule_ids", "filters", "steps", "review", "created_by", "created_at", "updated_at", "pushed_at", "lemlist_campaign_id")},
                                 "id": cid, "status": _status(c, approval)},
                    "approval": dict(approval, id=str(approval["id"])) if approval else None,
                    "members": [{"id": str(m["id"]), "organization_id": str(m["organization_id"]), "person_id": str(m["person_id"]) if m["person_id"] else None,
                                 "included": m["included"], "exclusion": m["exclusion"], "signal": m["signal"], "company": m["company"], "country": m["country_code"],
                                 "name": m["full_name"], "title": m["title"], "hook": (m["variables"] or {}).get("signalHook"), "status": m["outcome"] or m["status"]} for m in members],
                    "preview": sc.preview(c["steps"], sample["variables"]) if sample else sc.preview(c["steps"], {}),
                    "placeholders": sorted(sc.CAMPAIGN_PLACEHOLDERS), "outbound_enabled": settings.outbound_enabled, "lemlist_configured": bool(settings.lemlist_api_key),
                    "can_edit": c["status"] == "draft" and (c["created_by"] == p.email or p.role in {"sales_manager", "admin"})}

    @app.put("/api/signal-campaigns/{cid}/steps")
    def set_steps(cid: str, body: StepsIn, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            c = load(conn, cid, p)
            editable(c, p)
            steps = [{**s.model_dump(), "fact_refs": ["signal"]} for s in sorted(body.steps, key=lambda s: s.day)]
            conn.execute("UPDATE signal_campaigns SET steps=%s, updated_at=now() WHERE id=%s", (jb(steps), cid))
            review = sc.review_campaign(conn, cid)
            audit(conn, actor=p.email, operation="signal_campaign_copy_changed", record_type="signal_campaign", record_id=cid, detail={"passed": review["passed"]})
            return {"review": review}

    @app.post("/api/signal-campaigns/{cid}/rebuild")
    def rebuild(cid: str, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            c = load(conn, cid, p)
            editable(c, p)
            stats = sc.build_audience(conn, cid, p)
            return {**stats, "review": sc.review_campaign(conn, cid)}

    @app.patch("/api/signal-campaigns/{cid}/members/{mid}")
    def member(cid: str, mid: str, body: MemberIn, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            c = load(conn, cid, p)
            editable(c, p)
            m = q1(conn, "SELECT id, person_id, variables FROM signal_campaign_members WHERE id::text=%s AND campaign_id=%s", (mid, cid))
            if not m or not m["person_id"]:
                raise HTTPException(404, "not found")
            if body.included is not None:
                conn.execute("UPDATE signal_campaign_members SET included=%s WHERE id=%s", (body.included, m["id"]))
            if body.hook:
                conn.execute("UPDATE signal_campaign_members SET variables = variables || %s::jsonb WHERE id=%s", (jb({"signalHook": body.hook.strip()}), m["id"]))
            return {"review": sc.review_campaign(conn, cid)}

    @app.post("/api/signal-campaigns/{cid}/request-approval")
    def request_approval(cid: str, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            c = load(conn, cid, p)
            editable(c, p)
            try:
                return sc.request_push(conn, cid, p, settings)
            except sc.CampaignError as exc:
                raise HTTPException(409, str(exc)) from exc

    @app.post("/api/signal-campaigns/{cid}/withdraw")
    def withdraw(cid: str, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            c = load(conn, cid, p)
            if c["created_by"] != p.email and p.role not in {"sales_manager", "admin"}:
                raise HTTPException(403, "Only the person who created the campaign or a manager can change it.")
            try:
                sc.withdraw(conn, cid, p.email)
            except sc.CampaignError as exc:
                raise HTTPException(409, str(exc)) from exc
            return {"status": "draft"}

    @app.post("/api/signal-campaigns/{cid}/archive")
    def archive(cid: str, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            c = load(conn, cid, p)
            if c["created_by"] != p.email and p.role not in {"sales_manager", "admin"}:
                raise HTTPException(403, "Only the person who created the campaign or a manager can change it.")
            if c["status"] == "pending_approval":
                raise HTTPException(409, "Withdraw the approval request first.")
            conn.execute("UPDATE signal_campaigns SET status='archived', updated_at=now() WHERE id=%s", (cid,))
            return {"status": "archived"}
