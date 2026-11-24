"""The Leads database: every company, filterable by region, ICP, segment, signal band and origin, with the account score beside it."""
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
from ..db import q1, qa
from ..jobs.queue import enqueue
from ..market import REGIONS, region_for, sector_sql
from ..signals.seller import leading_signals
from ..sheets.reconcile import icp_band
from ..target_market import TARGET_REGION_ORDER, TARGET_REGIONS, target_region, target_sql
from .auth import require
from .search import all_like, tokens

SORTS = {"score": lambda r: (-(float(r["score"] or 0)), r["name"].lower()), "name": lambda r: r["name"].lower(), "size": lambda r: (-(r["employees"] or 0), r["name"].lower()),
         "recent": lambda r: (r["last_signal_at"] is None, -(r["last_signal_at"].timestamp() if r["last_signal_at"] else 0), r["name"].lower())}
ORIGIN_LABEL = {"list": "Your lists", "added": "Added by a person", "discovered": "Found by the platform"}


def _origin(r: dict[str, Any]) -> str:
    if r["in_master_list"]:
        return "list"
    return "added" if (r["icp_note"] or "").startswith("Added by") else "discovered"


def _rows(conn, p: Principal, q: str, scope: str = "") -> list[dict[str, Any]]:
    where, params = org_scope_sql(p, "o.id")
    focus = target_sql(review=scope == "review") if scope else "TRUE"
    terms = tokens(q) or ([q.strip()] if q.strip() else [])
    match, search_args = all_like("concat_ws(' ', o.display_name, o.canonical_domain, o.segment, o.country_code)", terms) if terms else ("TRUE", [])
    rows = qa(conn,
        f"SELECT {sector_sql('o.segment')} AS sector, o.id, o.display_name AS name, o.country_code, o.segment, o.employees_estimate AS employees, o.employees_uncertain, o.in_master_list, o.canonical_domain, o.entity_type, o.company_ownership, "      # noqa: S608
        f"o.created_at, s.score, s.band, s.groups, s.signal_count, s.stopped, s.last_signal_at, s.top, c.status AS candidate_status, c.icp_note, "
        f"EXISTS (SELECT 1 FROM legacy_account_research r WHERE r.organization_id=o.id AND r.kind='priority_outreach') AS shortlist, "
        f"EXISTS (SELECT 1 FROM watchlist w WHERE w.organization_id=o.id AND w.user_email=%s) AS watched "
        f"FROM organizations o LEFT JOIN account_scores s ON s.organization_id=o.id LEFT JOIN new_company_candidates c ON c.organization_id=o.id "
        f"WHERE ({where}) AND {focus} AND ({match})", [p.email, *params, *search_args])
    out = []
    for r in rows:
        icp = icp_band(r["employees"], bool(r["employees_uncertain"]), r["segment"])
        out.append({**r, "id": str(r["id"]), "region": (target_region if scope else region_for)(r["country_code"]), "icp": icp["band"], "boundary_uncertain": icp["boundary_uncertain"], "origin": _origin(r),
                    "band": r["band"] or "none", "score": float(r["score"]) if r["score"] is not None else None, "groups": list(r["groups"] or [])})
    return out


def _filter(rows, *, region, icp, segment, band, origin, signal_days, shortlist, public, watched=False, lane="", focus="", qualification=""):
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
            "band": count("band", lambda r: r["band"]), "origin": count("origin", lambda r: r["origin"])}


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
                  qualification=Query("", pattern="^(|pending_qualification)$"), sort=Query("score", pattern="^(score|name|size|recent)$"), scope=Query("", pattern="^(|target|review)$"))

    @app.get("/api/leads")
    def leads(request: Request, page: int = Query(1, ge=1), size: int = Query(50, ge=10, le=200), q: str = Params["q"], region: str = Params["region"], icp: str = Params["icp"],
              segment: str = Params["segment"], band: str = Params["band"], origin: str = Params["origin"], signal_days: int = Params["signal_days"],
              shortlist: bool = Params["shortlist"], public: bool = Params["public"], lane: str = Params["lane"], watched: bool = Params["watched"], sort: str = Params["sort"], scope: str = Params["scope"], focus: str = Params["focus"], qualification: str = Params["qualification"], p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            allrows = _rows(conn, p, q, scope)
            rows = _filter(allrows, region=region, icp=icp, segment=segment, band=band, origin=origin, signal_days=signal_days, shortlist=shortlist, public=public, watched=watched, lane=lane, focus=focus, qualification=qualification)
            rows.sort(key=lambda row: (TARGET_REGION_ORDER.get(row["region"], 99), SORTS[sort](row)) if scope else SORTS[sort](row))
            total = len(rows)
            page = min(page, max(1, (total + size - 1) // size)) if scope else page
            items = rows[(page - 1) * size: page * size]
            evidence = leading_signals(conn, [r["id"] for r in items], commercial_only=bool(scope))
        for r in items:
            r["buying_signal"] = evidence.get(r["id"])
            r["last_signal_at"] = r["last_signal_at"].isoformat() if r["last_signal_at"] else None
            r["created_at"] = r["created_at"].isoformat()
            r.pop("icp_note", None)
        return {"items": items, "total": total, "page": page, "size": size, "facets": _facets(_filter(allrows, region="", icp="", segment="", band="", origin="", signal_days=0, shortlist=False, public=public, lane=lane, focus=focus)),
                "regions": [{"key": k, "label": l} for k, l in (TARGET_REGIONS if scope else [(k, l) for k, l, _ in REGIONS])], "origins": ORIGIN_LABEL}

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
                  sort: str = Params["sort"], scope: str = Params["scope"], watched: bool = Params["watched"], focus: str = Params["focus"], qualification: str = Params["qualification"], p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            rows = _filter(_rows(conn, p, q, scope), region=region, icp=icp, segment=segment, band=band, origin=origin, signal_days=signal_days, shortlist=shortlist, public=public, lane=lane, watched=watched, focus=focus, qualification=qualification)
            rows.sort(key=lambda row: (TARGET_REGION_ORDER.get(row["region"], 99), SORTS[sort](row)) if scope else SORTS[sort](row))
            rows = rows[:5000]
            audit(conn, actor=p.email, operation="leads_exported", record_type="organization", record_id="export", detail={"rows": len(rows)})
        safe = lambda v: ("'" + v) if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v      # noqa: E731  spreadsheet formula injection
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Company", "Website", "Country", "Region", "Segment", "Employees", "ICP band", "Score", "Band", "Signal groups", "Last signal", "Origin"])
        for r in rows:
            w.writerow([safe(x) for x in [r["name"], r["canonical_domain"] or "", r["country_code"] or "", r["region"], r["segment"] or "", r["employees"] or "", r["icp"],
                                          r["score"] if r["score"] is not None else "", r["band"], " ".join(r["groups"]), r["last_signal_at"].date().isoformat() if r["last_signal_at"] else "", ORIGIN_LABEL[r["origin"]]]])
        return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="leads.csv"'})

    @app.post("/api/leads/scan")
    def scan_selected(body: ScanSelectedIn, request: Request, p: Principal = Depends(require("research.request"))):
        from ..discovery.plans import ensure_account_plan
        from .intel import DAILY_SCAN_LIMIT
        from ..db import q1
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
    ownership: str = Field(pattern="^(unknown|private|listed|state_owned)$")
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
            conn.execute("UPDATE organizations SET company_ownership=%(company_ownership)s, company_profile_source_url=%(company_profile_source_url)s, "
                         "country_code=%(country_code)s, segment=%(segment)s, employees_estimate=%(employees_estimate)s, employees_uncertain=%(employees_uncertain)s, "
                         "employees_basis=CASE WHEN employees_estimate IS DISTINCT FROM %(employees_estimate)s THEN 'manual_review' ELSE employees_basis END, "
                         "updated_at=now() WHERE id=%(id)s", {**profile, "id": oid})
            audit(conn, actor=p.email, operation="company_profile_updated", record_type="organization", record_id=oid, before=old, after=profile)
            updated = q1(conn, f"""SELECT CASE WHEN {target_sql()} THEN 'target' WHEN {target_sql(review=True)} THEN 'review'
                                ELSE 'other' END AS target_qualification FROM organizations o WHERE o.id=%s""", (oid,))
            return {**profile, **(updated or {})}
