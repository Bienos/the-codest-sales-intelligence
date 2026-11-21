"""Routes that make imported sheet data usable before anything is machine-verified:
prospect board (clearly labelled as unverified sheet content), data overview, and workbook upload/import status."""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile

from ..access import Principal, org_scope_sql
from ..audit import audit
from ..db import jb, q1, qa
from ..jobs.queue import enqueue
from ..sheets.client import MAX_XLSX_BYTES, detect_workbook
from .auth import principal_dep, require

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


def register(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    @app.get("/api/prospects")
    def prospects(request: Request, lane: str = Query("all", pattern="^(all|shortlist|signals)$"), q: str = "", limit: int = Query(40, le=200),
                  p: Principal = Depends(require("accounts.read"))):
        today = datetime.now(timezone.utc).date()
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            rows = qa(conn,
                f"SELECT DISTINCT ON (r.organization_id, r.kind) r.organization_id, r.kind, r.original, o.display_name, o.country_code, o.segment, o.employees_estimate, "   # noqa: S608
                f"(SELECT count(*) FROM opportunity_hypotheses h WHERE h.organization_id=o.id AND h.state NOT IN ('superseded')) AS opps "
                f"FROM legacy_account_research r JOIN organizations o ON o.id=r.organization_id "
                f"WHERE r.kind IN ('priority_outreach','master_account') AND ({where}) AND (%s='' OR o.display_name ILIKE '%%'||%s||'%%') "
                f"ORDER BY r.organization_id, r.kind, r.observed_at DESC", [*params, q, q])
            can_contacts = p.can("contacts.read")
            by_org: dict[str, dict[str, Any]] = {}
            for r in rows:
                oid = str(r["organization_id"])
                it = prospect_item(r, r["kind"], can_contacts, today)
                if not it["signal"] and not it["source_url"]:
                    continue                      # nothing to act on
                cur = by_org.get(oid)
                if cur and cur["lane"] == "shortlist":      # curated shortlist wins over the generic master row
                    continue
                by_org[oid] = {"organization": {"id": oid, "name": r["display_name"], "country": r["country_code"], "segment": r["segment"], "employees": r["employees_estimate"]},
                               "has_opportunity": bool(r["opps"]), **it}
            items = [i for i in by_org.values() if lane == "all" or i["lane"] == lane]
            items.sort(key=lambda i: (i["lane"] != "shortlist", i["rank"] if i["rank"] is not None else 999,
                                      _PRIORITY.get(str(i["priority"] or "").lower(), 3), -(i["score"] or 0)))
            return {"items": items[:limit], "total": len(items),
                    "note": "From your imported sheets. Not yet verified by this platform: confirm the source before acting."}

    @app.get("/api/overview")
    def overview(request: Request, p: Principal = Depends(require("today.read"))):
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            c = q1(conn, f"SELECT count(*) AS orgs, count(*) FILTER (WHERE o.in_master_list) AS master FROM organizations o WHERE ({where})", params) or {}   # noqa: S608
            people = q1(conn, "SELECT count(*) AS n FROM employment") or {}
            run = q1(conn, "SELECT workbook_key, status, finished_at FROM import_runs WHERE status IN ('completed','paused_schema_drift') ORDER BY finished_at DESC NULLS LAST LIMIT 1")
            shortlist = q1(conn, "SELECT count(DISTINCT organization_id) AS n FROM legacy_account_research WHERE kind='priority_outreach'") or {}
            verified = q1(conn, "SELECT count(*) AS n FROM opportunity_hypotheses WHERE state NOT IN ('superseded')") or {}
            src = q1(conn, "SELECT count(*) FILTER (WHERE state IN ('healthy','ok','active')) AS live, count(*) AS total FROM source_health") or {}
            return {"accounts": c.get("orgs", 0), "master_accounts": c.get("master", 0), "contacts": people.get("n", 0), "shortlist": shortlist.get("n", 0),
                    "verified_opportunities": verified.get("n", 0), "sources_live": src.get("live", 0), "sources_total": src.get("total", 0),
                    "last_import": run}

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

    @app.get("/api/admin/imports")
    def imports(request: Request, p: Principal = Depends(require("admin.sources"))):
        with tx(request) as conn:
            return {"uploads": [dict(r, id=str(r["id"])) for r in qa(conn,
                "SELECT u.id, u.workbook_key, u.filename, u.size_bytes, u.status, u.uploaded_by, u.error, u.created_at, u.processed_at, r.stats "
                "FROM import_uploads u LEFT JOIN import_runs r ON r.id=u.import_run_id ORDER BY u.created_at DESC LIMIT 15")],
                "runs": [dict(r, id=str(r["id"])) for r in qa(conn, "SELECT id, workbook_key, status, started_at, finished_at, error FROM import_runs ORDER BY started_at DESC LIMIT 10")]}
