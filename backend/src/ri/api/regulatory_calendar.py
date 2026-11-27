"""Regulatory deadline calendar pages (src/ri/regulatory_calendar.py, rules/regulatory_calendar.yaml).

* ``GET /api/regulatory-calendar``: every deadline (adopted, provisional and possible; past ones flagged), each with how many of our target
  accounts it applies to and a few of their names. Accounts follow the person's row scope, like every other account list.
* ``GET /api/accounts/{oid}/deadlines``: the deadlines that apply to one company, by its country and kind of firm (404 outside the scope).
"""
from __future__ import annotations

import uuid

from fastapi import Depends, FastAPI, HTTPException, Request

from .. import regulatory_calendar as rc
from ..access import Principal, org_scope_sql
from ..db import q1
from .auth import require


def _uuid(v: str) -> str:
    try:
        return str(uuid.UUID(v))
    except ValueError:
        raise HTTPException(404, "not found") from None


def register_regulatory_calendar(app: FastAPI) -> None:
    @app.get("/api/regulatory-calendar")
    def regulatory_calendar(request: Request, p: Principal = Depends(require("accounts.read"))):
        where, args = org_scope_sql(p, "o.id")
        with request.app.state.db.tx() as conn:
            return rc.calendar(conn, where, args)

    @app.get("/api/accounts/{oid}/deadlines")
    def account_deadlines(oid: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        where, args = org_scope_sql(p, "o.id")
        with request.app.state.db.tx() as conn:
            org = q1(conn, f"SELECT o.id::text AS id, o.display_name, o.country_code, o.segment, o.list_icp_segment FROM organizations o WHERE o.id=%s AND ({where})",   # noqa: S608
                     [_uuid(oid), *args])
            if not org:
                raise HTTPException(404, "not found")        # 404, not 403: do not reveal what exists outside the caller's scope
            return rc.for_account(conn, org)
