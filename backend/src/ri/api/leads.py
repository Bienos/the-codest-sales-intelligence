"""The Accounts list: every company, filterable by region, ICP, segment, signal band and origin, with the account score beside it.

With a ``scope`` the list is the sales workspace: one list of companies, each in one state (see ``target_market``): target accounts
(``target``), companies still being filled in by Exa (``review``) and the rest of the lead list and workbooks (``other``, not a fit, with
the reason). The response counts every state for the current filters, so the page can show the switch with its numbers. Each company row
carries the strongest signal from the lead list (its source, and whether the source was checked), so workbook leads live on their company
instead of in a second list."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..access import Principal, org_scope_sql
from ..audit import audit
from ..db import jb, q1, qa
from ..jobs.queue import enqueue
from ..market import REGIONS, region_for, sector_sql
from ..sheets.reconcile import icp_band
from ..signals.seller import leading_signals
from ..target_market import (
    OWNERSHIP_VALUES,
    TARGET_REGION_ORDER,
    TARGET_REGIONS,
    account_workspace_sql,
    missing_facts,
    not_fit_reason,
    state_sql,
    target_region,
)
from .auth import require
from .search import all_like, tokens

_LIST_RANK = {"P1": 0, "P2": 1, "P3": 2}
# Verified signals first; among equals (and while nothing is verified yet) the lead list's own priority and score decide.
SORTS = {"score": lambda r: (-(float(r["score"] or 0)), _LIST_RANK.get(r["list_priority"] or "", 9), -(float(r["list_score"] or 0)), r["name"].lower()), "name": lambda r: r["name"].lower(), "size": lambda r: (-(r["employees"] or 0), r["name"].lower()),
         "recent": lambda r: (r["last_signal_at"] is None, -(r["last_signal_at"].timestamp() if r["last_signal_at"] else 0), r["name"].lower()),
         # the lead list's own ranking: priority first, then its score; companies the list does not rank go last
         "priority": lambda r: (_LIST_RANK.get(r["list_priority"] or "", 9), -(float(r["list_score"] or -1)), r["name"].lower()),
         "list_score": lambda r: (r["list_score"] is None, -(float(r["list_score"] or 0)), _LIST_RANK.get(r["list_priority"] or "", 9), r["name"].lower())}
# The same chain the account list shows as a verified signal (signals.seller.leading_signals): an active, verified signal on a
# verified event, backed by a supported, anchored claim with a web source. Used to filter on "has a verified signal".
_VERIFIED_SIGNAL = """EXISTS (SELECT 1 FROM signals vs JOIN events ve ON ve.id=vs.event_id AND ve.subject_org_id=vs.organization_id
        JOIN event_claims vec ON vec.event_id=ve.id AND vec.claim_id=ANY(vs.evidence_claim_ids) JOIN claims vc ON vc.id=vec.claim_id
        LEFT JOIN source_documents vd ON vd.id=vc.source_document_id
        WHERE vs.organization_id=o.id AND vs.status='active' AND vs.verification_status='verified' AND (vs.expires_at IS NULL OR vs.expires_at>now())
        AND ve.verification_status='verified' AND ve.lifecycle_status='active' AND NOT ve.is_historical_replay AND (NOT %s OR ve.lane <> 'public')
        AND vc.status='supported' AND vc.anchor_verified AND vc.claim_kind IN ('quote','structured') AND (vc.valid_to IS NULL OR vc.valid_to>now())
        AND coalesce(vd.canonical_url, vc.object_json->>'official_url', vc.object_json->>'url') ~* '^https?://')"""
ORIGIN_LABEL = {"list": "Your lists", "added": "Added by a person", "discovered": "Found by the platform"}
STATE_LABEL = {"target": "Target account", "review": "Still being filled in", "other": "Not a fit"}


def _origin(r: dict[str, Any]) -> str:
    if r["in_master_list"]:
        return "list"
    return "added" if (r["icp_note"] or "").startswith("Added by") else "discovered"


# The strongest signal the lead list gives for a company: the outreach shortlist's row first, then the master list's row.
_LIST_ROWS = "r.kind IN ('priority_outreach','master_account')"
_LIST_ORDER = "(r.kind='priority_outreach') DESC, r.observed_at DESC"
_SIGNAL_TEXT = "nullif(coalesce(r.original->>'Strongest buying signal', r.original->>'Strongest Buying Signal', r.original->>'Verified Buying Signal'), '')"
# one pass over the workbook rows (a per-company subquery scanned the table once per company)
_LIST_SIGNALS_CTE = (f"WITH ls AS (SELECT DISTINCT ON (r.organization_id) r.organization_id, {_SIGNAL_TEXT} AS signal FROM legacy_account_research r "
                     f"WHERE {_LIST_ROWS} AND {_SIGNAL_TEXT} IS NOT NULL ORDER BY r.organization_id, {_LIST_ORDER}) ")
STATES = ("target", "review", "other")


def _rows(conn, p: Principal, q: str, scope: str = "", *, every_state: bool = False) -> list[dict[str, Any]]:
    """Companies matching the search. With a scope: the sales workspace, each row with its `state`; only that state's rows unless
    `every_state` (the caller counts the states, then keeps one)."""
    where, params = org_scope_sql(p, "o.id")
    focus = account_workspace_sql() if scope else "TRUE"
    terms = tokens(q) or ([q.strip()] if q.strip() else [])
    match, search_args = all_like("concat_ws(' ', o.display_name, o.canonical_domain, o.segment, o.country_code, ls.signal)", terms) if terms else ("TRUE", [])
    rows = qa(conn,
        f"{_LIST_SIGNALS_CTE}SELECT {sector_sql('o.segment')} AS sector, o.id, o.display_name AS name, o.country_code, o.segment, o.employees_estimate AS employees, o.employees_uncertain, o.in_master_list, o.canonical_domain, o.logo_url, o.entity_type, o.company_ownership, "      # noqa: S608
        f"o.company_profile_source_url, o.facts_checked_at, o.facts_status, o.facts_source->'_last' AS facts_last, "
        f"o.created_at, s.score, s.band, s.groups, s.signal_count, s.stopped, s.last_signal_at, s.top, c.status AS candidate_status, c.icp_note, "
        f"o.list_priority, o.list_score, o.list_eligibility, ls.signal AS list_signal, "
        f"{state_sql() if scope else 'NULL'} AS state, "
        f"(SELECT count(DISTINCT e.person_id) FROM employment e JOIN people pe ON pe.id=e.person_id AND pe.erased_at IS NULL WHERE e.organization_id=o.id) AS people, "
        f"(SELECT count(DISTINCT e.person_id) FROM employment e JOIN people pe ON pe.id=e.person_id AND pe.erased_at IS NULL AND pe.work_email IS NOT NULL AND NOT pe.suppressed "
        f"WHERE e.organization_id=o.id) AS people_with_email, "
        f"EXISTS (SELECT 1 FROM legacy_account_research r WHERE r.organization_id=o.id AND r.kind='priority_outreach') AS shortlist, "
        f"EXISTS (SELECT 1 FROM watchlist w WHERE w.organization_id=o.id AND w.user_email=%s) AS watched, "
        f"{_VERIFIED_SIGNAL} AS has_signal "
        f"FROM organizations o LEFT JOIN account_scores s ON s.organization_id=o.id LEFT JOIN new_company_candidates c ON c.organization_id=o.id "
        f"LEFT JOIN ls ON ls.organization_id=o.id "
        f"WHERE ({where}) AND {focus} AND ({match})", [p.email, bool(scope), *params, *search_args])
    out = []
    for r in rows:
        if scope and not every_state and r["state"] != scope:
            continue
        icp = icp_band(r["employees"], bool(r["employees_uncertain"]), r["segment"])
        row = {**r, "id": str(r["id"]), "region": (target_region if scope else region_for)(r["country_code"]), "icp": icp["band"], "boundary_uncertain": icp["boundary_uncertain"], "origin": _origin(r),
               "band": r["band"] or "none", "score": float(r["score"]) if r["score"] is not None else None, "groups": list(r["groups"] or []),
               "list_score": float(r["list_score"]) if r["list_score"] is not None else None}
        row["missing"] = missing_facts(row) if r["state"] == "review" else []
        row["not_fit"] = not_fit_reason(row) if r["state"] == "other" else None
        out.append(row)
    return out


def _list_leads(conn, ids: list[str], can_contacts: bool) -> dict[str, dict[str, Any]]:
    """For the companies on one page: the lead list's strongest signal, its source, and whether that source was checked (and how)."""
    from .lists import prospect_item
    if not ids:
        return {}
    today = datetime.now(timezone.utc).date()
    out: dict[str, dict[str, Any]] = {}
    for r in qa(conn, f"SELECT r.organization_id, r.kind, r.original FROM legacy_account_research r WHERE r.organization_id = ANY(%s::uuid[]) AND {_LIST_ROWS} "
                      f"ORDER BY r.organization_id, {_LIST_ORDER}", (ids,)):      # noqa: S608
        oid = str(r["organization_id"])
        if oid in out:
            continue
        it = prospect_item(r, r["kind"], can_contacts, today)
        it["signal"] = it["signal"] or (r["original"] or {}).get("Strongest Buying Signal")      # the older list layout's column
        if it["signal"] or it["source_url"]:
            out[oid] = {k: it[k] for k in ("signal", "signal_type", "signal_date", "source_url", "why", "lane", "age_days")}
    urls = [v["source_url"] for v in out.values() if v["source_url"]]
    checks = {(str(v["organization_id"]), v["url"]): v for v in qa(conn, """
        SELECT v.organization_id, v.url, v.status,
            (SELECT count(DISTINCT e.id) FROM event_documents ed JOIN events e ON e.id=ed.event_id
             WHERE ed.document_id=v.document_id AND e.subject_org_id=v.organization_id
               AND e.verification_status='verified' AND e.lifecycle_status='active') AS matched_events
        FROM verification_requests v WHERE v.origin='sheet_signal' AND v.url=ANY(%s) AND v.organization_id=ANY(%s::uuid[])""", (urls, list(out)))} if urls else {}
    for oid, lead in out.items():
        v = checks.get((oid, lead["source_url"]))
        status = v["status"] if v else None
        if status == "verified" and not v["matched_events"]:
            status = "no_event"
        lead["check"] = status
    return out


def _filter(rows, *, region, icp, segment, band, origin, signal_days, shortlist, public, watched=False, lane="", focus="", qualification="",
            priority="", has_email=False, has_signal=False):
    now = datetime.now(timezone.utc)
    cut = now - timedelta(days=signal_days) if signal_days else None
    out = []
    for r in rows:
        if region and r["region"] != region:
            continue
        if icp and r["icp"] != icp:
            continue
        if segment and (r["segment"] or "unknown") != segment:
            continue
        if band and r["band"] != band:
            continue
        if origin and r["origin"] != origin:
            continue
        if cut and not (r["last_signal_at"] and r["last_signal_at"] >= cut):
            continue
        if shortlist and not r["shortlist"]:
            continue
        if watched and not r["watched"]:
            continue
        if qualification and r["candidate_status"] != qualification:
            continue
        if focus and r["sector"] != focus:
            continue
        if priority and r["list_priority"] != priority:
            continue
        if has_email and not r["people_with_email"]:
            continue
        if has_signal and not r["has_signal"]:
            continue
        if lane == "public":
            if r["entity_type"] != "public_body" or r["candidate_status"] == "rejected":
                continue
        elif not public and (r["entity_type"] == "public_body" or r["candidate_status"] == "rejected"):
            continue
        out.append(r)
    return out


def _facets(rows) -> dict[str, Any]:
    def count(key, fn=lambda r: r):
        d: dict[str, int] = {}
        for r in rows:
            k = fn(r) or "unknown"
            d[k] = d.get(k, 0) + 1
        return d
    return {"region": count("region", lambda r: r["region"]), "icp": count("icp", lambda r: r["icp"]), "segment": count("segment", lambda r: r["segment"]),
            "band": count("band", lambda r: r["band"]), "origin": count("origin", lambda r: r["origin"]),
            "priority": count("priority", lambda r: r["list_priority"] if r["list_priority"] in _LIST_RANK else "none"),
            "has_email": sum(1 for r in rows if r["people_with_email"]), "has_signal": sum(1 for r in rows if r["has_signal"])}


def _order(rows: list[dict[str, Any]], sort: str, scope: str, by_region: bool) -> None:
    """Sort in place. The target and review lists were shown region by region, so they keep that order unless `by_region` is off."""
    rows.sort(key=lambda row: (TARGET_REGION_ORDER.get(row["region"], 99), SORTS[sort](row)) if scope and by_region else SORTS[sort](row))


class ScanSelectedIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=25)
    with_search: bool = False


def register_leads(app: FastAPI) -> None:
    register_qualify(app)
    register_company_profile(app)
    def tx(request: Request):
        return request.app.state.db.tx()

    Params = dict(q=Query("", max_length=120), region=Query("", pattern="^(|uk|dach|nordics|poland|europe|international|us|other|unknown)$"),
                  icp=Query("", max_length=40), segment=Query("", max_length=60), band=Query("", pattern="^(|hot|warm|watch|none)$"),
                  origin=Query("", pattern="^(|list|added|discovered)$"), watched=Query(False), signal_days=Query(0, ge=0, le=365), shortlist=Query(False), public=Query(False), lane=Query("", pattern="^(|public)$"), focus=Query("", pattern="^(|finance|other|unclassified)$"),
                  qualification=Query("", pattern="^(|pending_qualification)$"), sort=Query("score", pattern="^(score|name|size|recent|priority|list_score)$"), scope=Query("", pattern="^(|target|review|other)$"),
                  priority=Query("", pattern="^(|P1|P2|P3)$"), has_email=Query(False), has_signal=Query(False), by_region=Query(True))

    @app.get("/api/leads")
    def leads(request: Request, page: int = Query(1, ge=1), size: int = Query(50, ge=10, le=200), q: str = Params["q"], region: str = Params["region"], icp: str = Params["icp"],
              segment: str = Params["segment"], band: str = Params["band"], origin: str = Params["origin"], signal_days: int = Params["signal_days"],
              shortlist: bool = Params["shortlist"], public: bool = Params["public"], lane: str = Params["lane"], watched: bool = Params["watched"], sort: str = Params["sort"], scope: str = Params["scope"], focus: str = Params["focus"], qualification: str = Params["qualification"],
              priority: str = Params["priority"], has_email: bool = Params["has_email"], has_signal: bool = Params["has_signal"], by_region: bool = Params["by_region"], p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            everything = _rows(conn, p, q, scope, every_state=True)
            matching = _filter(everything, region=region, icp=icp, segment=segment, band=band, origin=origin, signal_days=signal_days, shortlist=shortlist, public=public, watched=watched, lane=lane, focus=focus, qualification=qualification,
                               priority=priority, has_email=has_email, has_signal=has_signal)
            # how many companies each state has for these filters: the page's switch, and "3 more in Still being filled in" when a search finds nothing here
            counts = {k: sum(1 for r in matching if r["state"] == k) for k in STATES} if scope else None
            allrows = [r for r in everything if r["state"] == scope] if scope else everything
            rows = [r for r in matching if r["state"] == scope] if scope else matching
            _order(rows, sort, scope, by_region)
            total = len(rows)
            page = min(page, max(1, (total + size - 1) // size)) if scope else page
            items = rows[(page - 1) * size: page * size]
            evidence = leading_signals(conn, [r["id"] for r in items], commercial_only=bool(scope))
            list_leads = _list_leads(conn, [r["id"] for r in items], p.can("contacts.read")) if scope else {}
        for r in items:
            r["buying_signal"] = evidence.get(r["id"])
            r["list_lead"] = list_leads.get(r["id"])
            r["last_signal_at"] = r["last_signal_at"].isoformat() if r["last_signal_at"] else None
            r["created_at"] = r["created_at"].isoformat()
            r["facts_checked_at"] = r["facts_checked_at"].isoformat() if r["facts_checked_at"] else None
            r.pop("icp_note", None)
            r.pop("company_profile_source_url", None)
        return {"items": items, "total": total, "page": page, "size": size, "counts": counts,
                "facets": _facets(_filter(allrows, region="", icp="", segment="", band="", origin="", signal_days=0, shortlist=False, public=public, lane=lane, focus=focus)),
                "regions": [{"key": k, "label": l} for k, l in (TARGET_REGIONS if scope else [(k, l) for k, l, _ in REGIONS])], "origins": ORIGIN_LABEL}

    @app.get("/api/company-facts/status")
    def company_facts_status(request: Request, p: Principal = Depends(require("accounts.read"))):
        """The fill-in with Exa, for the Accounts page: how many companies it is working through and how many it looked at this week."""
        from ..research.company_facts import status
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            st = status(conn, where, params)
        st["last_checked_at"] = st["last_checked_at"].isoformat() if st["last_checked_at"] else None
        return st

    @app.get("/api/review-companies")
    def review_companies(request: Request, p: Principal = Depends(require("research.request"))):
        from .intel import org_urls

        with tx(request) as conn:
            rows = _filter(_rows(conn, p, "", "review"), region="", icp="", segment="", band="", origin="",
                           signal_days=0, shortlist=False, public=False, lane="")
            rows.sort(key=lambda row: (row["last_signal_at"] is None,
                                      -(row["last_signal_at"].timestamp() if row["last_signal_at"] else 0),
                                      row["name"].lower()))
            items = rows[:10]
            signals = leading_signals(conn, [row["id"] for row in items], commercial_only=True)
            result = []
            for row in items:
                signal = signals.get(row["id"])
                if signal:
                    evidence = {"summary": signal["summary"], "url": signal["source_url"], "verified": True}
                else:
                    links = org_urls(conn, row["id"])
                    url, claim = next(iter(links.items()), (None, None))
                    evidence = {"summary": claim["claimed_text"], "url": url, "verified": False} if claim else None
                result.append({"id": row["id"], "name": row["name"], "region": row["region"],
                               "country": row["country_code"], "signal": evidence})
            return {"items": result, "total": len(rows)}

    @app.get("/api/leads.csv")
    def leads_csv(request: Request, q: str = Params["q"], region: str = Params["region"], icp: str = Params["icp"], segment: str = Params["segment"], band: str = Params["band"],
                  origin: str = Params["origin"], signal_days: int = Params["signal_days"], shortlist: bool = Params["shortlist"], public: bool = Params["public"], lane: str = Params["lane"],
                  sort: str = Params["sort"], scope: str = Params["scope"], watched: bool = Params["watched"], focus: str = Params["focus"], qualification: str = Params["qualification"],
                  priority: str = Params["priority"], has_email: bool = Params["has_email"], has_signal: bool = Params["has_signal"], by_region: bool = Params["by_region"], p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            rows = _filter(_rows(conn, p, q, scope), region=region, icp=icp, segment=segment, band=band, origin=origin, signal_days=signal_days, shortlist=shortlist, public=public, lane=lane, watched=watched, focus=focus, qualification=qualification,
                           priority=priority, has_email=has_email, has_signal=has_signal)
            _order(rows, sort, scope, by_region)
            rows = rows[:5000]
            audit(conn, actor=p.email, operation="leads_exported", record_type="organization", record_id="export", detail={"rows": len(rows)})
        safe = lambda v: ("'" + v) if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v      # noqa: E731  spreadsheet formula injection
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Company", "Website", "Country", "Region", "Segment", "Employees", "ICP band", "Score", "Band", "Signal groups", "Last signal", "Origin",
                    "Status", "List priority", "List score", "People", "People with email"])
        for r in rows:
            w.writerow([safe(x) for x in [r["name"], r["canonical_domain"] or "", r["country_code"] or "", r["region"], r["segment"] or "", r["employees"] or "", r["icp"],
                                          r["score"] if r["score"] is not None else "", r["band"], " ".join(r["groups"]), r["last_signal_at"].date().isoformat() if r["last_signal_at"] else "", ORIGIN_LABEL[r["origin"]],
                                          STATE_LABEL.get(r["state"] or "", ""),
                                          r["list_priority"] or "", r["list_score"] if r["list_score"] is not None else "", r["people"], r["people_with_email"]]])
        return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="leads.csv"'})

    @app.post("/api/leads/scan")
    def scan_selected(body: ScanSelectedIn, request: Request, p: Principal = Depends(require("research.request"))):
        from ..db import q1
        from ..discovery.plans import ensure_account_plan
        from .intel import DAILY_SCAN_LIMIT
        with tx(request) as conn:
            used = (q1(conn, "SELECT count(*) AS n FROM audit_events WHERE actor=%s AND operation IN ('account_scan_requested','company_added') AND at > now() - interval '24 hours'", (p.email,)) or {"n": 0})["n"]
            if used + len(body.ids) > DAILY_SCAN_LIMIT:
                raise HTTPException(429, f"That would pass the daily limit of {DAILY_SCAN_LIMIT} scans per person ({used} used).")
            where, params = org_scope_sql(p, "o.id")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
            n = 0
            for oid in body.ids:
                org = q1(conn, f"SELECT o.id, o.display_name, o.country_code FROM organizations o WHERE o.id=%s AND ({where})", [oid, *params])      # noqa: S608
                if not org:
                    continue
                enqueue(conn, "account_scan", {"organization_id": oid}, idempotency_key=f"account-scan:{oid}:{stamp}", max_attempts=2)
                if body.with_search:
                    pid = ensure_account_plan(conn, oid, org["display_name"], org["country_code"])
                    enqueue(conn, "discovery_run", {"plan_ids": [pid], "requested_by": p.email}, idempotency_key=f"account-search:{oid}:{stamp}", max_attempts=1)
                audit(conn, actor=p.email, operation="account_scan_requested", record_type="organization", record_id=oid, detail={"bulk": True})
                n += 1
            return {"queued": n}


    @app.post("/api/accounts/{oid}/watch")
    def watch(oid: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        from ..db import q1
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            if not q1(conn, f"SELECT 1 FROM organizations o WHERE o.id=%s AND ({where})", [oid, *params]):      # noqa: S608
                raise HTTPException(404, "not found")
            if (q1(conn, "SELECT count(*) AS n FROM watchlist WHERE user_email=%s", (p.email,)) or {"n": 0})["n"] >= 100:
                raise HTTPException(429, "You can watch up to 100 accounts. Remove one first.")
            conn.execute("INSERT INTO watchlist(organization_id, user_email) VALUES (%s,%s) ON CONFLICT DO NOTHING", (oid, p.email))
            audit(conn, actor=p.email, operation="watch_added", record_type="organization", record_id=oid, detail={})
            return {"watched": True}

    @app.delete("/api/accounts/{oid}/watch")
    def unwatch(oid: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            conn.execute("DELETE FROM watchlist WHERE organization_id=%s AND user_email=%s", (oid, p.email))
            return {"watched": False}


class QualifyIn(BaseModel):
    decision: str = Field(pattern="^(qualify|reject)$")
    note: str | None = Field(default=None, max_length=300)


def register_qualify(app: FastAPI) -> None:
    @app.post("/api/leads/{oid}/qualify")
    def qualify(oid: str, body: QualifyIn, request: Request, p: Principal = Depends(require("research.request"))):
        """A person decides whether a candidate company is a real lead. Nothing about the company's data changes: only its qualification status."""
        from ..db import q1
        with request.app.state.db.tx() as conn:
            where, params = org_scope_sql(p, "o.id")
            row = q1(conn, f"SELECT c.status FROM organizations o JOIN new_company_candidates c ON c.organization_id=o.id WHERE o.id=%s AND ({where})", [oid, *params])      # noqa: S608
            if not row:
                raise HTTPException(404, "not a candidate company, or not visible to you")
            status = "qualified" if body.decision == "qualify" else "rejected"
            note = f"{'Qualified' if status == 'qualified' else 'Rejected'} by {p.email}" + (f": {body.note.strip()}" if body.note and body.note.strip() else ".")
            conn.execute("UPDATE new_company_candidates SET status=%s, icp_note=%s WHERE organization_id=%s", (status, note, oid))
            audit(conn, actor=p.email, operation=f"candidate_{status}", record_type="organization", record_id=oid, detail={"note": body.note})
            return {"status": status}


class CompanyProfileIn(BaseModel):
    ownership: str = Field(pattern="^(" + "|".join(OWNERSHIP_VALUES) + ")$")
    source_url: str | None = Field(default=None, max_length=1000)
    country_code: str | None = Field(default=None, pattern="^[A-Z]{2}$")
    segment: str | None = Field(default=None, max_length=80)
    employees: int | None = Field(default=None, gt=0, le=2147483647)
    employees_uncertain: bool = False


def register_company_profile(app: FastAPI) -> None:
    @app.put("/api/accounts/{oid}/company-profile")
    def save_company_profile(oid: str, body: CompanyProfileIn, request: Request, p: Principal = Depends(require("research.request"))):
        source = (body.source_url or "").strip() or None
        if source:
            try:
                u = urlsplit(source)
                valid = u.scheme in {"http", "https"} and u.hostname and not (u.username or u.password or any(c.isspace() for c in source))
            except ValueError:
                valid = False
            if not valid:
                raise HTTPException(422, "Enter a valid link to the ownership source.")
        if body.ownership != "unknown" and not source:
            raise HTTPException(422, "Add a source link before confirming ownership.")
        with request.app.state.db.tx() as conn:
            where, params = org_scope_sql(p, "o.id")
            old = q1(conn, f"SELECT company_ownership, company_profile_source_url, entity_type, country_code, segment, employees_estimate, employees_uncertain FROM organizations o WHERE o.id=%s AND ({where}) FOR UPDATE", [oid, *params])  # noqa: S608
            if not old:
                raise HTTPException(404, "Company not found.")
            if old["entity_type"] == "public_body":
                raise HTTPException(422, "This organization is a public body.")
            country = "GB" if body.country_code == "UK" else body.country_code
            profile = {"company_ownership": body.ownership, "company_profile_source_url": source,
                       "country_code": country or old["country_code"], "segment": (body.segment or "").strip() or old["segment"],
                       "employees_estimate": body.employees or old["employees_estimate"],
                       "employees_uncertain": body.employees_uncertain if body.employees else old["employees_uncertain"]}
            # who set each fact: a person's value replaces what Exa filled in, and Exa never writes over it
            changed = {f: col for f, col in (("ownership", "company_ownership"), ("country", "country_code"), ("sector", "segment"),
                                             ("employees", "employees_estimate")) if profile[col] != old[col]}
            stamp = datetime.now(timezone.utc).isoformat()
            by_person = {f: {"by": "person", "who": p.email, "value": profile[col], "url": source if f == "ownership" else None, "at": stamp}
                         for f, col in changed.items()}
            conn.execute("UPDATE organizations SET company_ownership=%(company_ownership)s, company_profile_source_url=%(company_profile_source_url)s, "
                         "country_code=%(country_code)s, segment=%(segment)s, employees_estimate=%(employees_estimate)s, employees_uncertain=%(employees_uncertain)s, "
                         "employees_basis=CASE WHEN employees_estimate IS DISTINCT FROM %(employees_estimate)s THEN 'manual_review' ELSE employees_basis END, "
                         "facts_source = facts_source || %(by_person)s, updated_at=now() WHERE id=%(id)s", {**profile, "id": oid, "by_person": jb(by_person)})
            audit(conn, actor=p.email, operation="company_profile_updated", record_type="organization", record_id=oid, before=old, after=profile)
            updated = q1(conn, f"""SELECT coalesce({state_sql()}, 'other') AS target_qualification FROM organizations o WHERE o.id=%s""", (oid,))
            return {**profile, **(updated or {})}
