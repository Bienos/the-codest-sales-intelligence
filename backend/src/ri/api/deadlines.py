"""Regulatory deadline calendar. A person supplies every date with an official source link and the sentence that states it; another step (an approver) confirms it.
Nothing is invented or inferred by the platform: only approved dates are shown to sellers."""
from __future__ import annotations

from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ..access import Principal
from ..audit import audit
from ..db import q1, qa
from ..precision import review
from .auth import require


class DeadlineIn(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    regime: str = Field(min_length=2, max_length=60)
    deadline_date: date
    applies_to: str | None = Field(None, max_length=200)
    region: str | None = Field(None, max_length=40)
    source_url: str = Field(pattern=r"^https://[^\s]+$", max_length=500)
    source_quote: str = Field(min_length=15, max_length=600)


class DecideIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject|retire)$")


def register_deadlines(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    @app.get("/api/deadlines")
    def deadlines(request: Request, all: bool = False, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            show_all = all and p.can("admin.rules")
            rows = qa(conn, "SELECT id, title, regime, deadline_date, applies_to, region, source_url, source_quote, status, proposed_by, decided_by, (deadline_date - current_date) AS days_left FROM regulatory_deadlines "
                            "WHERE (%s OR (status='approved' AND deadline_date >= current_date)) ORDER BY deadline_date LIMIT 200", (show_all,))
            return {"items": [dict(r, id=str(r["id"])) for r in rows]}

    @app.post("/api/deadlines")
    def propose(body: DeadlineIn, request: Request, p: Principal = Depends(require("research.request"))):
        if str(body.deadline_date.year) not in body.source_quote and body.deadline_date.strftime("%d.%m.%Y") not in body.source_quote:
            raise HTTPException(422, "The quoted sentence must contain the year of the deadline, so a reviewer can check the date against it.")
        with tx(request) as conn:
            row = conn.execute("INSERT INTO regulatory_deadlines(title, regime, deadline_date, applies_to, region, source_url, source_quote, proposed_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                               (body.title.strip(), body.regime.strip(), body.deadline_date, body.applies_to, body.region, body.source_url, body.source_quote.strip(), p.email)).fetchone()
            audit(conn, actor=p.email, operation="deadline_proposed", record_type="regulatory_deadline", record_id=str(row["id"]), detail={})  # type: ignore[index]
            return {"id": str(row["id"]), "status": "proposed"}  # type: ignore[index]

    @app.post("/api/deadlines/{did}/decision")
    def decide(did: str, body: DecideIn, request: Request, p: Principal = Depends(require("admin.rules"))):
        with tx(request) as conn:
            d = q1(conn, "SELECT status, proposed_by FROM regulatory_deadlines WHERE id=%s", (did,))
            if not d:
                raise HTTPException(404, "not found")
            if body.decision == "approve" and d["proposed_by"] == p.email:
                raise HTTPException(409, "Another person must approve a date you proposed.")
            status = {"approve": "approved", "reject": "rejected", "retire": "retired"}[body.decision]
            if (body.decision in ("approve", "reject") and d["status"] != "proposed") or (body.decision == "retire" and d["status"] != "approved"):
                raise HTTPException(409, f"This deadline is {d['status']}.")
            conn.execute("UPDATE regulatory_deadlines SET status=%s, decided_by=%s, decided_at=now() WHERE id=%s", (status, p.email, did))
            audit(conn, actor=p.email, operation=f"deadline_{status}", record_type="regulatory_deadline", record_id=did, detail={})
            return {"status": status}

    @app.get("/api/admin/precision")
    def precision(request: Request, days: int = 90, p: Principal = Depends(require("admin.rules"))):
        with tx(request) as conn:
            return review(conn, max(7, min(days, 365)))
