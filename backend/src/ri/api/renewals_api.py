from __future__ import annotations

from fastapi import Depends, FastAPI, Query, Request

from ..access import Principal
from .auth import require

COUNTRIES = "^(GB|DE|AT|CH|SE|DK|NO|FI|PL)$"


def register(app: FastAPI) -> None:
    @app.get("/api/renewals")
    def renewals(request: Request, months: int = Query(12, ge=1, le=36), country: str | None = Query(None, pattern=COUNTRIES), q: str | None = Query(None, max_length=80),
                 stated_only: bool = False, p: Principal = Depends(require("accounts.read"))):
        from ..renewals import ending_soon
        with request.app.state.db.tx() as conn:
            items = ending_soon(conn, months=months, country=country, q=q or None, stated_only=stated_only)
            total = conn.execute("SELECT count(*) AS n, min(awarded_on) AS since FROM awarded_contracts WHERE relevant").fetchone()
            return {"items": items, "months": months, "contracts_known": total["n"], "records_since": total["since"].isoformat() if total["since"] else None}

    @app.get("/api/renewals/incumbents")
    def incumbents(request: Request, country: str | None = Query(None, pattern=COUNTRIES), p: Principal = Depends(require("accounts.read"))):
        from ..renewals import incumbents as inc
        with request.app.state.db.tx() as conn:
            rows = inc(conn, country=country)
            return {"items": [{**r, "latest": r["latest"].isoformat() if r["latest"] else None, "countries": list(r["countries"])} for r in rows]}
