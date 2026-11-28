"""Client book (managers: team-wide money) and campaign results (anyone who runs campaigns; the per-sender split is for managers)."""
from __future__ import annotations

from fastapi import Depends, FastAPI, Query, Request

from ..access import Principal
from .auth import require


def register(app: FastAPI) -> None:
    @app.get("/api/clients")
    def get_clients(request: Request, p: Principal = Depends(require("forecast.read"))):
        from ..clients import book
        with request.app.state.db.tx() as conn:
            return book(conn, request.app.state.settings)

    @app.get("/api/campaign-results")
    def get_campaign_results(request: Request, days: int = Query(90, ge=7, le=365), p: Principal = Depends(require("draft.create"))):
        from ..campaign_results import results
        with request.app.state.db.tx() as conn:
            out = results(conn, days=days, by_sender=p.can("team.read") or p.can("forecast.read"))
            return {**out, "can": {"senders": "senders" in out}}
