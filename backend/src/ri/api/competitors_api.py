"""Competitor tracker API. Reading needs competitors.read; changing the research needs competitors.manage (marketing, managers, analysts, admins);
naming a competitor on a deal needs competitors.record and the deal's account must be one the person may see. Every change is audited.
Where-we-meet-them lists only accounts inside the person's row scope; head-to-head returns counts, never deal names."""
from __future__ import annotations

import re
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import competitors as ct
from ..access import Principal, org_scope_sql
from ..audit import audit
from .auth import require

DATE = r"^\d{4}(-\d{2}(-\d{2})?)?$"


class MoveIn(BaseModel):
    date: str = Field(pattern=DATE)
    type: str = Field(pattern="^(" + "|".join(ct.MOVE_TYPES) + ")$")
    headline: str = Field(min_length=5, max_length=300)
    detail: str | None = Field(None, max_length=2000)
    sowhat: str | None = Field(None, max_length=1000)
    source: str = Field(pattern=r"^https://", max_length=1000)
    grade: str = Field(pattern="^(official|reported|estimate)$")
    impact: int = Field(ge=1, le=3)
    confirm: bool = False


class ReviewIn(BaseModel):
    status: str = Field(pattern="^(confirmed|dismissed)$")
    impact: int | None = Field(None, ge=1, le=3)
    sowhat: str | None = Field(None, max_length=1000)


class DealCompetitorIn(BaseModel):
    competitor: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,59}$")
    role: str = Field("unknown", pattern="^(" + "|".join(ct.ROLES) + ")$")
    reason: str | None = Field(None, max_length=300)


class CompetitorIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    origin: str = Field(pattern="^(PL|UA|EU)$")
    url: str = Field(pattern=r"^https://", max_length=300)


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "firm"


def register(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    @app.get("/api/competitors")
    def tracker(request: Request, lang: str = Query("en", pattern="^(en|pl)$"), p: Principal = Depends(require("competitors.read"))):
        scope, args = org_scope_sql(p, "o.id")
        with tx(request) as conn:
            data = ct.tracker(conn, scope, args, lang=lang)
            data["can"] = {"manage": p.can("competitors.manage"), "record": p.can("competitors.record")}
            if p.can("competitors.manage") and not data.get("empty"):
                data["waiting"] = conn.execute("SELECT count(*) AS n FROM competitor_moves WHERE status='candidate'").fetchone()["n"]
            return data

    @app.get("/api/competitors/{key}/presence")
    def presence(key: str, request: Request, p: Principal = Depends(require("competitors.read"))):
        scope, args = org_scope_sql(p, "o.id")
        with tx(request) as conn:
            row = conn.execute("SELECT key, name, profile FROM competitors WHERE key=%s", (key,)).fetchone()
            if not row:
                raise HTTPException(404, "no such competitor")
            pres = ct.presence(conn, [{"key": row["key"], "name": row["name"], **row["profile"]}], scope, args, key=key)
            return pres[key]

    @app.post("/api/competitors")
    def add(body: CompetitorIn, request: Request, p: Principal = Depends(require("competitors.manage"))):
        key = _key(body.name)
        with tx(request) as conn:
            if conn.execute("SELECT 1 FROM competitors WHERE key=%s", (key,)).fetchone():
                raise HTTPException(409, "this firm is already tracked")
            ct.add_competitor(conn, key=key, name=body.name.strip(), origin=body.origin, url=body.url, actor=p.email)
            audit(conn, actor=p.email, operation="competitor_added", record_type="competitor", record_id=key, after=body.model_dump())
        return {"key": key}

    @app.patch("/api/competitors/{key}")
    def update(key: str, body: dict[str, Any], request: Request, p: Principal = Depends(require("competitors.manage"))):
        if len(str(body)) > 50_000:
            raise HTTPException(413, "too large")
        with tx(request) as conn:
            try:
                diff = ct.update_profile(conn, key, body, p.email)
            except KeyError:
                raise HTTPException(404, "no such competitor")
            except ct.InvalidChange as e:
                raise HTTPException(422, str(e))
            audit(conn, actor=p.email, operation="competitor_updated", record_type="competitor", record_id=key, before=diff["before"], after=diff["after"])
        return {"ok": True}

    @app.post("/api/competitors/{key}/moves")
    def add_move(key: str, body: MoveIn, request: Request, p: Principal = Depends(require("competitors.read"))):
        """Anyone who can read may suggest a move (it waits as a candidate); only competitors.manage may confirm it straight away."""
        confirm = body.confirm and p.can("competitors.manage")
        with tx(request) as conn:
            if not conn.execute("SELECT 1 FROM competitors WHERE key=%s", (key,)).fetchone():
                raise HTTPException(404, "no such competitor")
            try:
                mid = ct.add_move(conn, key, body.model_dump(), p.email, confirm=confirm)
            except ct.InvalidChange as e:
                raise HTTPException(422, str(e))
            audit(conn, actor=p.email, operation="competitor_move_added", record_type="competitor_move", record_id=mid, after={**body.model_dump(), "confirmed": confirm})
        return {"id": mid, "status": "confirmed" if confirm else "candidate"}

    @app.get("/api/competitors/moves/waiting")
    def waiting(request: Request, p: Principal = Depends(require("competitors.manage"))):
        with tx(request) as conn:
            return {"items": ct.moves_queue(conn)}

    @app.post("/api/competitors/moves/{move_id}/review")
    def review(move_id: str, body: ReviewIn, request: Request, p: Principal = Depends(require("competitors.manage"))):
        if not re.fullmatch(r"[0-9a-f-]{36}", move_id):
            raise HTTPException(404, "no such move")
        with tx(request) as conn:
            try:
                diff = ct.review_move(conn, move_id, status=body.status, actor=p.email, impact=body.impact, so_what=body.sowhat)
            except KeyError:
                raise HTTPException(404, "no such move")
            except ct.InvalidChange as e:
                raise HTTPException(422, str(e))
            audit(conn, actor=p.email, operation="competitor_move_reviewed", record_type="competitor_move", record_id=move_id, before=diff["before"], after=diff["after"])
        return {"ok": True}

    @app.get("/api/competitors/deals")
    def deals(request: Request, q: str | None = Query(None, max_length=80), p: Principal = Depends(require("competitors.record"))):
        """Deals this person may see, with the competitors already named on them: the list sellers pick from on the Head-to-head tab."""
        scope, args = org_scope_sql(p, "co.organization_id")
        with tx(request) as conn:
            rows = conn.execute(
                f"SELECT d.crm_id, d.title, d.status, d.stage_name, coalesce(array_agg(dc.competitor_key) FILTER (WHERE dc.competitor_key IS NOT NULL), '{{}}') AS named "
                f"FROM crm_deals d LEFT JOIN crm_organizations co ON co.crm_id=d.crm_org_id LEFT JOIN deal_competitors dc ON dc.crm_deal_id=d.crm_id "
                f"WHERE ({scope}) AND (%s::text IS NULL OR d.title ILIKE '%%' || %s || '%%') GROUP BY d.crm_id ORDER BY d.update_time DESC NULLS LAST LIMIT 50",
                [*args, q, q]).fetchall()
            return {"items": [{"id": r["crm_id"], "title": r["title"], "status": r["status"], "stage": r["stage_name"], "competitors": list(r["named"])} for r in rows]}

    @app.put("/api/competitors/deals/{crm_deal_id}")
    def record(crm_deal_id: int, body: DealCompetitorIn, request: Request, p: Principal = Depends(require("competitors.record"))):
        scope, args = org_scope_sql(p, "co.organization_id")
        with tx(request) as conn:
            if not conn.execute(f"SELECT 1 FROM crm_deals d LEFT JOIN crm_organizations co ON co.crm_id=d.crm_org_id WHERE d.crm_id=%s AND ({scope})",
                                [crm_deal_id, *args]).fetchone():
                raise HTTPException(404, "no such deal in your accounts")
            if not conn.execute("SELECT 1 FROM competitors WHERE key=%s AND NOT is_us", (body.competitor,)).fetchone():
                raise HTTPException(422, "not a tracked competitor")
            ct.record_deal_competitor(conn, crm_deal_id, body.competitor, body.role, body.reason, p.email)
            audit(conn, actor=p.email, operation="deal_competitor_recorded", record_type="crm_deal", record_id=str(crm_deal_id), after=body.model_dump())
        return {"ok": True}

    @app.delete("/api/competitors/deals/{crm_deal_id}/{competitor}")
    def unrecord(crm_deal_id: int, competitor: str, request: Request, p: Principal = Depends(require("competitors.record"))):
        scope, args = org_scope_sql(p, "co.organization_id")
        with tx(request) as conn:
            if not conn.execute(f"SELECT 1 FROM crm_deals d LEFT JOIN crm_organizations co ON co.crm_id=d.crm_org_id WHERE d.crm_id=%s AND ({scope})",
                                [crm_deal_id, *args]).fetchone():
                raise HTTPException(404, "no such deal in your accounts")
            conn.execute("DELETE FROM deal_competitors WHERE crm_deal_id=%s AND competitor_key=%s", (crm_deal_id, competitor))
            audit(conn, actor=p.email, operation="deal_competitor_removed", record_type="crm_deal", record_id=str(crm_deal_id), after={"competitor": competitor})
        return {"ok": True}
