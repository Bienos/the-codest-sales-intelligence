"""Routes that make imported sheet data usable before anything is machine-verified:
prospect board (clearly labelled as unverified sheet content), data overview, and workbook upload/import status."""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from ..access import Principal, org_scope_sql
from ..audit import audit
from ..sheets.reconcile import VENDOR_WORDS, market_focus
from ..db import q1, qa
from ..jobs.queue import enqueue
from ..sheets.client import MAX_XLSX_BYTES, detect_workbook
from ..target_market import TARGET_REGION_ORDER, target_region, target_sql, workbook_company_sql
from .auth import require
from .search import all_like, tokens

_PRIORITY = {"p1": 0, "p2": 1, "p3": 2}


def _num(v: Any) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", str(v or ""))
    return float(m.group()) if m else None


def _date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _url(v: Any) -> str | None:
    v = str(v or "").strip()
    return v if v.lower().startswith(("http://", "https://")) else None      # never render javascript: or other schemes


def prospect_item(r: dict[str, Any], kind: str, can_contacts: bool, today: date) -> dict[str, Any]:
    o = r["original"]
    if kind == "priority_outreach":
        item = {"lane": "shortlist", "rank": int(_num(o.get("Rank")) or 999), "score": _num(o.get("Opportunity score /100")),
                "priority": None, "signal": o.get("Strongest buying signal"), "signal_type": None, "signal_date": o.get("Event date"),
                "source_url": _url(o.get("Primary source URL")), "confidence": o.get("Evidence confidence"),
                "why": o.get("Why this matters for The Codest (inference)"), "service_fit": o.get("The Codest capability"),
                "buyer_role": o.get("Position / buyer role"), "buyer_name": o.get("First decision-maker to approach"),
                "caveat": o.get("Qualification caveats"), "route": o.get("External engineering vendor evidence"),
                "opener": o.get("Suggested first outreach sentence")}
    else:
        item = {"lane": "signals", "rank": None, "score": _num(o.get("Buying Signal Score")), "priority": o.get("Signal Priority"),
                "signal": o.get("Verified Buying Signal"), "signal_type": o.get("Signal Type"), "signal_date": o.get("Signal Date"),
                "source_url": _url(o.get("Primary Source URL")), "confidence": o.get("Evidence Confidence"),
                "why": o.get("Potential Engineering Need"), "service_fit": o.get("The Codest Service Fit"),
                "buyer_role": o.get("Target Buyer Role"), "buyer_name": o.get("Named Decision-Maker"),
                "caveat": o.get("Research Notes"), "route": o.get("Procurement / Vendor Route"), "opener": o.get("Recommended Outreach Angle")}
    if not can_contacts:
        item["buyer_name"] = None
    d = _date(item["signal_date"])
    item["age_days"] = (today - d).days if d else None
    item["verified_by_platform"] = False
    return item


def prospects_for(conn, p: Principal, *, lane: str, q: str, limit: int, focus: str = "", target: bool = False, region: str = "", offset: int = 0, workbook: bool = False) -> list[dict[str, Any]]:
    today = datetime.now(timezone.utc).date()
    from ..market import sector_sql

    where, params = org_scope_sql(p, "o.id")
    eligibility = target_sql() if target else workbook_company_sql() if workbook else "TRUE"
    sector = sector_sql("o.segment")
    target_match = target_sql()
    match, search_args = all_like("concat_ws(' ', o.display_name, o.canonical_domain, o.segment, o.country_code, r.original->>'Strongest buying signal', r.original->>'Verified Buying Signal')", tokens(q) or [q.strip()]) if q.strip() else ("TRUE", [])
    rows = qa(conn,
        f"SELECT DISTINCT ON (r.organization_id, r.kind) r.organization_id, r.kind, r.original, o.display_name, o.country_code, o.segment, o.employees_estimate, o.employees_uncertain, {target_match} AS target_fit, "   # noqa: S608
        f"(SELECT count(*) FROM opportunity_hypotheses h WHERE h.organization_id=o.id AND h.state NOT IN ('superseded')) AS opps "
        f"FROM legacy_account_research r JOIN organizations o ON o.id=r.organization_id "
        f"WHERE r.kind IN ('priority_outreach','master_account') AND ({where}) AND {eligibility} AND ({match}) "
        f"AND (%s='' OR (o.entity_type<>'public_body' AND (({sector})='finance')=(%s='finance'))) "
        f"ORDER BY r.organization_id, r.kind, r.observed_at DESC", [*params, *search_args, focus, focus])
    can_contacts = p.can("contacts.read")
    by_org: dict[str, dict[str, Any]] = {}
    for r in rows:
        if region and target_region(r["country_code"]) != region:
            continue
        if (target or workbook) and lane != "all" and ("shortlist" if r["kind"] == "priority_outreach" else "signals") != lane:
            continue
        oid = str(r["organization_id"])
        it = prospect_item(r, r["kind"], can_contacts, today)
        if not it["signal"] and not it["source_url"]:
            continue                      # nothing to act on
        cur = by_org.get(oid)
        if cur and cur["lane"] == "shortlist":      # curated shortlist wins over the generic master row
            continue
        by_org[oid] = {"organization": {"id": oid, "name": r["display_name"], "country": r["country_code"], "segment": r["segment"], "employees": r["employees_estimate"], "employees_uncertain": r["employees_uncertain"], "region": target_region(r["country_code"])},
                       "has_opportunity": bool(r["opps"]), "target_fit": bool(r["target_fit"]), "market": market_focus(r["country_code"], r["segment"]),
                       "vendor_flag": bool(r["segment"] and VENDOR_WORDS.search(r["segment"])), **it}
    items = [i for i in by_org.values() if lane == "all" or i["lane"] == lane]
    items.sort(key=lambda i: (TARGET_REGION_ORDER.get(i["organization"]["region"], 99) if target or workbook else 0,
                              i["lane"] != "shortlist", i["rank"] if i["rank"] is not None else 999,
                              _PRIORITY.get(str(i["priority"] or "").lower(), 3), -(i["score"] or 0),
                              i["organization"]["name"].casefold(), i["organization"]["id"]))
    total = len(items)
    start = min(offset, max(0, ((total - 1) // limit) * limit))
    items = items[start:start + limit]
    urls = [i["source_url"] for i in items if i["source_url"]]
    scope = [i["organization"]["id"] for i in items]
    ver = {(str(v["organization_id"]), v["url"]): v for v in qa(conn, """
        SELECT v.organization_id, v.url, v.status, v.result,
            (SELECT count(DISTINCT e.id)
             FROM event_documents ed JOIN events e ON e.id=ed.event_id
             WHERE ed.document_id=v.document_id AND e.subject_org_id=v.organization_id
               AND e.verification_status='verified' AND e.lifecycle_status='active') AS matched_events
        FROM verification_requests v
        WHERE v.origin='sheet_signal' AND v.url=ANY(%s) AND v.organization_id=ANY(%s::uuid[])
        """, (urls, scope))} if urls else {}
    for i in items:
        v = ver.get((i["organization"]["id"], i["source_url"]))
        if v:
            status = v["status"]
            reason = (v["result"] or {}).get("reason")
            if status == "verified" and not v["matched_events"]:
                status, reason = "no_event", "The source has no verified event linked to this account."
            i["verification"] = {"status": status, "events": v["matched_events"], "reason": reason}
        else:
            i["verification"] = None
        i["_total"] = total
    return items


def register(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    @app.get("/api/prospects")
    def prospects(request: Request, lane: str = Query("all", pattern="^(all|shortlist|signals)$"), q: str = Query("", max_length=120), limit: int = Query(40, ge=1, le=200),
                  focus: str = Query("", pattern="^(|finance|other)$"), target: bool = False, region: str = Query("", pattern="^(|uk|dach|nordics|poland|europe|international)$"), page: int = Query(1, ge=1),
                  workbook: bool = False, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            items = prospects_for(conn, p, lane=lane, q=q, limit=limit, focus=focus, target=target, region=region, offset=(page - 1) * limit, workbook=workbook)
            total = items[0]["_total"] if items else 0
            for i in items:
                i.pop("_total", None)
            return {"items": items, "total": total, "page": min(page, max(1, (total + limit - 1) // limit)),
                    "note": "From your imported sheets. Not yet verified by this platform: confirm the source before acting."}

    @app.get("/api/overview")
    def overview(request: Request, target: bool = False, p: Principal = Depends(require("today.read"))):
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            focus = target_sql() if target else "TRUE"
            review = target_sql(review=True) if target else "FALSE"
            c = q1(conn, f"SELECT count(*) FILTER (WHERE {focus}) AS orgs, "  # noqa: S608
                         f"count(*) FILTER (WHERE o.in_master_list AND {focus}) AS master, "
                         f"count(*) FILTER (WHERE o.in_master_list) AS imported, "
                         f"count(*) FILTER (WHERE {review}) AS ownership_to_check "
                         f"FROM organizations o WHERE ({where})", params) or {}
            people = q1(conn, "SELECT count(*) AS n FROM employment") or {}
            run = q1(conn, "SELECT workbook_key, status, finished_at FROM import_runs WHERE status IN ('completed','paused_schema_drift') ORDER BY finished_at DESC NULLS LAST LIMIT 1")
            shortlist = q1(conn, "SELECT count(DISTINCT organization_id) AS n FROM legacy_account_research WHERE kind='priority_outreach'") or {}
            verified = q1(conn, f"SELECT count(*) AS n FROM opportunity_hypotheses h JOIN organizations o ON o.id=h.organization_id WHERE h.state NOT IN ('superseded') AND ({where}) AND {focus} AND (NOT %s OR h.route_type <> 'procurement')", [*params, target]) or {}
            src = q1(conn, "SELECT count(*) FILTER (WHERE s.status='active') AS live, count(*) AS total FROM sources s WHERE s.collection_method <> 'internal'") or {}
            vr = {r["status"]: r["n"] for r in qa(conn, "SELECT status, count(*) AS n FROM verification_requests GROUP BY status")}
            bud = {r["provider"]: {"cap": float(r["cap"]), "used": float(r["spent"]) + float(r["reserved"])} for r in qa(conn, "SELECT provider, cap, spent, reserved FROM provider_budgets WHERE kind='monthly' AND period=to_char(now() AT TIME ZONE 'UTC','YYYY-MM')")}
            return {"accounts": c.get("orgs", 0), "master_accounts": c.get("master", 0), "contacts": people.get("n", 0), "shortlist": shortlist.get("n", 0),
                    "verified_opportunities": verified.get("n", 0), "sources_live": src.get("live", 0), "sources_total": src.get("total", 0),
                    "last_import": run, "verifications": vr, "budgets": bud,
                    # "ownership_to_check" is the old name of the same number, kept for pages that still read it
                    **({"imported_accounts": c.get("imported", 0), "being_filled_in": c.get("ownership_to_check", 0),
                        "ownership_to_check": c.get("ownership_to_check", 0)} if target else {})}

    @app.post("/api/admin/import/upload")
    async def upload(request: Request, file: UploadFile = File(...), p: Principal = Depends(require("admin.sources"))):
        data = await file.read(MAX_XLSX_BYTES + 1)
        try:
            key = detect_workbook(data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        sha = hashlib.sha256(data).hexdigest()
        name = re.sub(r"[^\w .()\-]", "_", file.filename or "workbook.xlsx")[:120]
        with tx(request) as conn:
            up = conn.execute("INSERT INTO import_uploads(workbook_key, filename, sha256, size_bytes, content, uploaded_by) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                              (key, name, sha, len(data), data, p.email)).fetchone()
            jid, _ = enqueue(conn, "sheet_import_xlsx", {"upload_id": str(up["id"])}, idempotency_key=f"xlsx-upload:{up['id']}", max_attempts=1)
            conn.execute("UPDATE import_uploads SET job_id=%s WHERE id=%s", (jid, up["id"]))
            audit(conn, actor=p.email, operation="sheet_upload", record_type="import_upload", record_id=str(up["id"]), detail={"workbook": key, "bytes": len(data), "sha256": sha})
            return {"upload_id": str(up["id"]), "workbook": key, "status": "queued", "bytes": len(data)}

    @app.post("/api/admin/import/sync")
    def sync_from_google(request: Request, workbook: str = Query(..., pattern="^[AB]$"), p: Principal = Depends(require("admin.sources"))):
        st = request.app.state.settings
        if not (st.google_service_account_json or st.google_credentials_file):
            raise HTTPException(409, "Google Sheets is not connected yet. Set GOOGLE_SERVICE_ACCOUNT_JSON on the API and worker, share both workbooks with that service account, then press Sync again.")
        with tx(request) as conn:
            jid, created = enqueue(conn, "sheet_sync", {"workbook": workbook, "requested_by": p.email}, idempotency_key=f"sheet-sync:{workbook}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}", max_attempts=1)
            audit(conn, actor=p.email, operation="sheet_sync_requested", record_type="job", record_id=jid, detail={"workbook": workbook})
            return {"job_id": jid, "queued": created, "workbook": workbook}

    @app.get("/api/admin/imports")
    def imports(request: Request, p: Principal = Depends(require("admin.sources"))):
        st = request.app.state.settings
        sa_email = None
        if st.google_service_account_json:
            import json as _json
            try:
                sa_email = _json.loads(st.google_service_account_json).get("client_email")
            except ValueError:
                sa_email = None
        with tx(request) as conn:
            return {"google_sheets": {"configured": bool(st.google_service_account_json or st.google_credentials_file), "service_account_email": sa_email,
                                      "workbooks": {"A": "https://docs.google.com/spreadsheets/d/1J9vDQK3lQzZLP2tx2TkoHnQQQSFEA3-qRmXtoU4TX9Y", "B": "https://docs.google.com/spreadsheets/d/1wJKkovlp4oFGo8aj3ayRlAH-oXZ4O-RGDK8LZme9EMc"}},
                    "uploads": [dict(r, id=str(r["id"])) for r in qa(conn,
                "SELECT u.id, u.workbook_key, u.filename, u.size_bytes, u.status, u.uploaded_by, u.error, u.created_at, u.processed_at, r.stats "
                "FROM import_uploads u LEFT JOIN import_runs r ON r.id=u.import_run_id ORDER BY u.created_at DESC LIMIT 15")],
                "runs": [dict(r, id=str(r["id"])) for r in qa(conn, "SELECT id, workbook_key, status, started_at, finished_at, error FROM import_runs ORDER BY started_at DESC LIMIT 10")]}


ROLES = "^(bdm|sales_manager|research_analyst|marketing|delivery_expert|admin|compliance)$"


class UserIn(BaseModel):
    email: str = Field(min_length=5, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    display_name: str = Field(min_length=1, max_length=100)
    role: str = Field(pattern=ROLES)
    team: str = Field(default="default", max_length=60)
    pipedrive_user_id: int | None = None


class UserPatch(BaseModel):
    role: str | None = Field(default=None, pattern=ROLES)
    team: str | None = Field(default=None, max_length=60)
    active: bool | None = None
    pipedrive_user_id: int | None = None


def register_users(app: FastAPI) -> None:
    from fastapi import HTTPException as HE
    def tx(request: Request):
        return request.app.state.db.tx()

    @app.get("/api/admin/users")
    def list_users(request: Request, p: Principal = Depends(require("admin.users"))):
        with tx(request) as conn:
            return {"items": [dict(r, id=str(r["id"])) for r in qa(conn, "SELECT id, email, display_name, role, team, pipedrive_user_id, active, created_at FROM users ORDER BY active DESC, role, email")],
                    "domain": request.app.state.settings.allowed_email_domain}

    @app.post("/api/admin/users")
    def add_user(body: UserIn, request: Request, p: Principal = Depends(require("admin.users"))):
        email = body.email.strip().lower()
        dom = request.app.state.settings.allowed_email_domain
        if dom and not email.endswith("@" + dom):
            raise HE(400, f"only @{dom} addresses can be added")
        with tx(request) as conn:
            if q1(conn, "SELECT 1 FROM users WHERE email=%s", (email,)):
                raise HE(409, "that person already has an account")
            try:
                row = conn.execute("INSERT INTO users(email, display_name, role, team, pipedrive_user_id) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                                   (email, body.display_name.strip(), body.role, body.team.strip() or "default", body.pipedrive_user_id)).fetchone()
            except Exception as exc:    # noqa: BLE001  (unique pipedrive id)
                raise HE(409, "that Pipedrive user id is already linked to someone else") from exc
            audit(conn, actor=p.email, operation="user_add", record_type="user", record_id=str(row["id"]), after={"email": email, "role": body.role})
            return {"id": str(row["id"]), "email": email}

    @app.patch("/api/admin/users/{uid}")
    def edit_user(uid: str, body: UserPatch, request: Request, p: Principal = Depends(require("admin.users"))):
        with tx(request) as conn:
            u = q1(conn, "SELECT * FROM users WHERE id=%s", (uid,))
            if not u:
                raise HE(404, "not found")
            new_role = body.role or u["role"]
            new_active = u["active"] if body.active is None else body.active
            if str(u["id"]) == p.user_id and (new_role != "admin" or not new_active):
                raise HE(400, "you cannot remove your own admin access")
            if u["role"] == "admin" and u["active"] and (new_role != "admin" or not new_active):
                if (q1(conn, "SELECT count(*) AS n FROM users WHERE role='admin' AND active") or {"n": 0})["n"] <= 1:
                    raise HE(400, "at least one active administrator is required")
            conn.execute("UPDATE users SET role=%s, active=%s, team=coalesce(%s, team), pipedrive_user_id=coalesce(%s, pipedrive_user_id) WHERE id=%s",
                         (new_role, new_active, body.team, body.pipedrive_user_id, uid))
            audit(conn, actor=p.email, operation="user_edit", record_type="user", record_id=uid, before={"role": u["role"], "active": u["active"]}, after={"role": new_role, "active": new_active})
            return {"id": uid, "role": new_role, "active": new_active}
