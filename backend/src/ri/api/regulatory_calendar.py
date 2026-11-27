"""Regulatory deadline calendar pages (src/ri/regulatory_calendar.py, rules/regulatory_calendar.yaml).

* ``GET /api/regulatory-calendar``: every deadline (adopted, provisional and possible; past ones flagged), each with how many of our target
  accounts it applies to and a few of their names. Accounts follow the person's row scope, like every other account list.
* ``GET /api/accounts/{oid}/deadlines``: the deadlines that apply to one company, by its country and kind of firm (404 outside the scope).
* ``GET /api/calendar/events?from=YYYY-MM-DD&to=YYYY-MM-DD&layers=regulatory,tenders&per_day=4``: the Calendar page's month view, what
  falls on each day of the range (src/ri/calendar_events.py). Each layer follows the permission and row scope of the page it comes from.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from .. import calendar_events as ce
from .. import regulatory_calendar as rc
from ..access import Principal, org_scope_sql
from ..db import q1
from .auth import principal_dep, require


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

    @app.get("/api/calendar/events")
    def calendar_events(request: Request, start: date = Query(..., alias="from"), end: date = Query(..., alias="to"),
                        layers: str = Query(",".join(ce.LAYERS), pattern=r"^[a-z]+(,[a-z]+)*$", max_length=60),
                        per_day: int = Query(ce.PER_DAY, ge=0, le=ce.MAX_PER_DAY), p: Principal = Depends(principal_dep)):
        """What falls on each day from `from` to `to` (both included, at most ce.MAX_SPAN_DAYS days), in the asked layers the person may see."""
        asked = list(dict.fromkeys(layers.split(",")))
        if any(x not in ce.LAYERS for x in asked):
            raise HTTPException(422, f"layers: choose from {', '.join(ce.LAYERS)}")
        if end < start or (end - start).days + 1 > ce.MAX_SPAN_DAYS:
            raise HTTPException(422, f"the range must start before it ends and cover at most {ce.MAX_SPAN_DAYS} days")
        if not any(ce.allowed(p, x) for x in ce.LAYERS):
            raise HTTPException(403, "missing permission accounts.read")
        with request.app.state.db.tx() as conn:
            return ce.events(conn, p, start, end, asked, per_day=per_day)
