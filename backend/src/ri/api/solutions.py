"""What fits them best (company page) and the sales brief (person page).

Same checks as the company and person pages: `accounts.read` and the account's row scope for a company; `contacts.read` and the scope of the
account the person works at for a person (404, not 403, outside scope, so nothing about existence leaks). Reading a person's brief is
audited like any other read of personal data. Email addresses are never returned (solutions.py removes any address-like text).
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request

from ..access import Principal
from ..audit import audit
from ..solutions import person_brief, solution_fit
from .auth import require
from .profiles import _visible_org, _visible_person


def register_solutions(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    @app.get("/api/accounts/{oid}/solutions")
    def account_solutions(oid: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            o = _visible_org(conn, p, oid)
            return solution_fit(conn, str(o["id"]))

    @app.get("/api/people/{pid}/brief")
    def person_sales_brief(pid: str, request: Request, p: Principal = Depends(require("contacts.read"))):
        with tx(request) as conn:
            v = _visible_person(conn, p, pid)
            try:
                out = person_brief(conn, str(v["id"]))
            except LookupError:
                raise HTTPException(404, "not found") from None
            audit(conn, actor=p.email, operation="pii_read", record_type="person", record_id=str(v["id"]), detail={"view": "brief"})
            return out
