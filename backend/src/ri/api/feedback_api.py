"""Was this useful? (src/ri/feedback.py)

* ``POST /api/feedback``: the caller's answer for one Today or Buying signals item, by its key; a reason is required when it was not useful.
* ``DELETE /api/feedback?key=...``: take the answer back.
* ``GET /api/feedback/mine?keys=k1,k2``: the caller's own answers for the items on a page.
* ``GET /api/feedback/by-rule?days=90``: per rule, how often sellers found it useful and the reasons against it (the Library shows it).
An item outside the caller's scope answers 404, like the item itself would.
"""
from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import feedback as fb
from ..access import Principal
from .auth import require


class FeedbackIn(BaseModel):
    key: str = Field(min_length=5, max_length=200)
    useful: bool
    reason: Literal["wrong_company", "out_of_date", "not_our_work", "ghost_job", "already_talking", "no_real_need", "other"] | None = None
    note: str | None = Field(default=None, max_length=300)
    shown_on: Literal["today", "signals", "account", "feed"] = "today"


def register_feedback(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    @app.post("/api/feedback")
    def give(body: FeedbackIn, request: Request, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            try:
                return fb.vote(conn, p, body.key, useful=body.useful, reason=body.reason, note=body.note, shown_on=body.shown_on)
            except fb.FeedbackError as exc:
                raise HTTPException(404 if str(exc) == "unknown item" else 422, str(exc)) from exc

    @app.delete("/api/feedback")
    def take_back(request: Request, key: str = Query(min_length=5, max_length=200), p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            fb.undo(conn, p, key)
            return {"key": key, "useful": None}

    @app.get("/api/feedback/mine")
    def my_answers(request: Request, keys: str = Query("", max_length=20000), p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            return {"items": fb.mine(conn, p, [k for k in keys.split(",") if k][:200])}

    @app.get("/api/feedback/by-rule")
    def by_rule(request: Request, days: int = Query(90, ge=7, le=365), p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            return {"days": days, "items": fb.by_rule(conn, days), "reasons": fb.REASONS}
