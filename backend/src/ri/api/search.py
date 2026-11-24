"""Global keyword search plus the signal and URL library. One box finds accounts (and what the sheets say about them), signal rules,
sources and feeds, events, fetched documents, verification claims and the two knowledge-base documents."""
from __future__ import annotations

import re
from typing import Any

import hashlib

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse

from .. import kb
from ..access import Principal, org_scope_sql
from ..db import qa
from ..target_market import target_sql
from .auth import principal_dep, require


def tokens(q: str) -> list[str]:
    return [t for t in re.split(r"\s+", q.strip()) if len(t) >= 2][:5]


def like(t: str) -> str:
    return "%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def all_like(expr: str, toks: list[str]) -> tuple[str, list[str]]:
    return " AND ".join(f"{expr} ILIKE %s ESCAPE '\\'" for _ in toks), [like(t) for t in toks]


def snippet(text: str, toks: list[str], width: int = 230) -> str:
    text = re.sub(r"\s+", " ", text or "")
    i = min([j for j in (text.casefold().find(t.casefold()) for t in toks) if j >= 0], default=0)
    a = max(0, i - width // 3)
    return ("…" if a else "") + text[a:a + width] + ("…" if a + width < len(text) else "")


def register(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    @app.get("/api/search")
    def search(request: Request, q: str = Query("", max_length=120), target: bool = False, p: Principal = Depends(principal_dep)):
        toks = tokens(q)
        if not toks:
            return {"query": q, "groups": [], "total": 0}
        if not (p.can("today.read") or p.can("accounts.read") or p.can("sources.read")):
            raise HTTPException(403, "no access")
        groups: list[dict[str, Any]] = []
        focus = target_sql() if target else "TRUE"
        with tx(request) as conn:
            if p.can("accounts.read"):
                where, params = org_scope_sql(p, "o.id")
                nm, nmp = all_like("concat_ws(' ', o.display_name, o.canonical_domain, o.segment)", toks)
                tx_, txp = all_like("r.original::text", toks)
                rows = qa(conn,
                    f"WITH hits AS (SELECT DISTINCT ON (r.organization_id) r.organization_id, r.original FROM legacy_account_research r WHERE {tx_} "     # noqa: S608
                    f"ORDER BY r.organization_id, (r.kind='priority_outreach') DESC) "
                    f"SELECT o.id, o.display_name, o.country_code, o.segment, h.original AS hit FROM organizations o LEFT JOIN hits h ON h.organization_id=o.id "
                    f"WHERE ({where}) AND {focus} AND (h.organization_id IS NOT NULL OR ({nm})) ORDER BY (({nm})) DESC, o.in_master_list DESC, o.display_name LIMIT 25",
                    [*txp, *params, *nmp, *nmp])
                items = []
                for r in rows:
                    o = r["hit"] or {}
                    field = next((k for k, v in o.items() if isinstance(v, str) and any(t.casefold() in v.casefold() for t in toks)), None)
                    items.append({"title": r["display_name"], "meta": " · ".join(x for x in (r["country_code"], r["segment"]) if x), "snippet": snippet(str(o.get(field, "")), toks) if field else "",
                                  "field": field, "link": f"/accounts/{r['id']}"})
                groups.append({"key": "accounts", "label": "Accounts and what your sheets say", "items": items})
                ev_w, ev_p = all_like("e.summary", toks)
                ew, ep = org_scope_sql(p, "e.subject_org_id")
                evs = qa(conn, f"SELECT e.id, e.summary, e.event_type, coalesce(e.event_at, e.pub_at) AS at, o.display_name FROM events e LEFT JOIN organizations o ON o.id=e.subject_org_id "    # noqa: S608
                              f"WHERE {ev_w} AND {focus} AND (e.subject_org_id IS NULL OR ({ew})) AND (NOT %s OR (e.lane <> 'public' AND e.verification_status = 'verified')) ORDER BY at DESC NULLS LAST LIMIT 10", [*ev_p, *ep, target])
                groups.append({"key": "events", "label": "Verified events", "items": [{"title": e["summary"], "meta": f"{e['event_type']} · {e['display_name'] or 'unmatched'}", "snippet": "", "link": "/explorer?q=" + toks[0]} for e in evs]})
                if not target:
                    dw, dp = all_like("(coalesce(d.title,'') || ' ' || coalesce(d.canonical_url,''))", toks)
                    docs = qa(conn, f"SELECT d.title, d.canonical_url, d.fetched_at, s.display_name FROM source_documents d JOIN sources s USING (source_id) WHERE {dw} ORDER BY d.fetched_at DESC LIMIT 10", dp)   # noqa: S608
                    groups.append({"key": "documents", "label": "Pages the platform has read", "items": [{"title": d["title"] or d["canonical_url"], "meta": d["display_name"], "snippet": "", "url": d["canonical_url"]} for d in docs]})
            if not target and (p.can("sources.read") or p.can("research.request")):
                vw, vp = all_like("(coalesce(v.claimed_text,'') || ' ' || v.url)", toks)
                vs = qa(conn, f"SELECT v.url, v.claimed_text, v.status, o.display_name FROM verification_requests v LEFT JOIN organizations o ON o.id=v.organization_id WHERE {vw} ORDER BY v.created_at DESC LIMIT 10", vp)    # noqa: S608
                groups.append({"key": "verifications", "label": "Claims being verified", "items": [{"title": v["claimed_text"] or v["url"], "meta": f"{v['display_name'] or 'unmatched'} · {v['status']}", "snippet": "", "url": v["url"]} for v in vs]})
            sg_w, sg_p = all_like("(id || ' ' || coalesce(grp,'') || ' ' || signal || ' ' || coalesce(how_to_spot,'') || ' ' || coalesce(who_to_contact,'') || ' ' || coalesce(check_it_is_real,'') || ' ' || coalesce(window_note,''))", toks)
            sigs = qa(conn, f"SELECT id AS rule_id, grp, signal, strength, how_to_spot, who_to_contact, check_it_is_real FROM legacy_signals WHERE {sg_w} ORDER BY id LIMIT 15", sg_p)    # noqa: S608
            groups.append({"key": "signals", "label": "Signals (the 64 rules)", "items": [{"title": f"{s['rule_id']}  {s['signal']}", "meta": f"{s['grp']} · strength {s['strength']}", "snippet": snippet(" ".join(filter(None, [s["how_to_spot"], s["who_to_contact"], s["check_it_is_real"]])), toks), "link": "/library?tab=signals&q=" + toks[0]} for s in sigs]})
            sw, sp_ = all_like("(s.display_name || ' ' || coalesce(s.provider,'') || ' ' || coalesce(s.canonical_domain,'') || ' ' || s.source_id || ' ' || coalesce(s.config->>'url','') || ' ' || array_to_string(s.signal_rule_ids,' ') || ' ' || coalesce(s.licence_notes,''))", toks)
            srcs = qa(conn, f"SELECT s.source_id, s.display_name, s.source_tier, s.status, s.config->>'url' AS url, s.canonical_domain FROM sources s WHERE {sw} ORDER BY s.source_tier, s.display_name LIMIT 15", sp_)    # noqa: S608
            kbh = kb.search(toks, conn=conn)
            groups.append({"key": "sources", "label": "Sources, URLs and feeds", "items": [{"title": s["display_name"], "meta": f"{s['source_tier']} · {s['status']}", "snippet": s["url"] or s["canonical_domain"] or "", "link": "/library?tab=sources&q=" + toks[0]} for s in srcs]})
        groups.append({"key": "kb", "label": "Knowledge base", "items": [{"title": h["doc_title"], "meta": "", "snippet": snippet(h["text"], toks, 260), "link": f"/knowledge/{h['doc']}"} for h in kbh]})
        groups = [g for g in groups if g["items"]]
        return {"query": q, "groups": groups, "total": sum(len(g["items"]) for g in groups)}

    # ------------------------------------------------------------ library
    @app.get("/api/library/signals")
    def lib_signals(request: Request, q: str = Query("", max_length=120), group: str = "", status: str = "", p: Principal = Depends(require("today.read"))):
        toks = tokens(q)
        w, wp = all_like("(l.id || ' ' || coalesce(l.grp,'') || ' ' || l.signal || ' ' || coalesce(l.how_to_spot,'') || ' ' || coalesce(l.who_to_contact,'') || ' ' || coalesce(l.check_it_is_real,''))", toks) if toks else ("TRUE", [])
        with tx(request) as conn:
            rows = qa(conn,
                f"SELECT l.id AS rule_id, l.grp, l.signal, l.strength, l.fresh_for_days, l.window_note, l.how_to_spot, l.who_to_contact, l.check_it_is_real, l.pairs_best_with, l.stop_effect, "     # noqa: S608
                f"coalesce(v.status,'defined_not_activated') AS status, v.version, "
                f"(SELECT coalesce(array_agg(s.display_name ORDER BY s.display_name), '{{}}') FROM sources s WHERE l.id = ANY(s.signal_rule_ids)) AS fed_by "
                f"FROM legacy_signals l LEFT JOIN LATERAL (SELECT status, version FROM signal_rule_versions r WHERE r.rule_id=l.id ORDER BY created_at DESC LIMIT 1) v ON TRUE "
                f"WHERE ({w}) AND (%s='' OR l.grp=%s) AND (%s='' OR coalesce(v.status,'defined_not_activated')=%s) ORDER BY l.ordinal", [*wp, group, group, status, status])
            groups = [r["grp"] for r in qa(conn, "SELECT DISTINCT grp, min(ordinal) AS o FROM legacy_signals GROUP BY grp ORDER BY o")]
            return {"items": rows, "groups": groups}

    @app.get("/api/library/urls")
    def lib_urls(request: Request, q: str = Query("", max_length=120), limit: int = Query(80, le=300), p: Principal = Depends(require("accounts.read"))):
        toks = tokens(q)
        with tx(request) as conn:
            where, params = org_scope_sql(p, "o.id")
            uw, up = all_like("v.value", toks) if toks else ("TRUE", [])
            rows = qa(conn,
                f"SELECT DISTINCT ON (v.value) v.value AS url, v.key AS field, o.display_name, o.id, substring(v.value from '^https?://(?:www\\.)?([^/]+)') AS domain "   # noqa: S608
                f"FROM legacy_account_research r JOIN organizations o ON o.id=r.organization_id, LATERAL jsonb_each_text(r.original) v "
                f"WHERE v.value ~* '^https?://' AND v.key NOT ILIKE '%%linkedin%%' AND v.value NOT ILIKE '%%linkedin.com%%' AND ({where}) AND ({uw}) ORDER BY v.value LIMIT %s", [*params, *up, limit * 3])
            domains: dict[str, int] = {}
            for r in rows:
                domains[r["domain"]] = domains.get(r["domain"], 0) + 1
            items = [{"url": r["url"], "domain": r["domain"], "field": r["field"], "organization": r["display_name"], "organization_id": str(r["id"])} for r in rows][:limit]
            top = sorted(domains.items(), key=lambda kv: -kv[1])[:25]
            feeds = qa(conn, "SELECT s.source_id, s.display_name, s.source_tier, s.status, s.collection_method, s.config->>'url' AS url, s.config->>'url_basis' AS basis, s.canonical_domain "
                             "FROM sources s WHERE s.config->>'url' IS NOT NULL ORDER BY s.source_tier, s.display_name")
            return {"items": items, "domains": [{"domain": d, "count": c} for d, c in top], "feeds": feeds, "linkedin_excluded": True}

    # ------------------------------------------------------------ knowledge base (signed, short-lived links)
    @app.get("/api/kb")
    def kb_list(request: Request, p: Principal = Depends(require("today.read"))):
        with tx(request) as conn:
            return {"items": [{"id": k, "title": v["title"], "blurb": v["blurb"], "source": v["source"], "available": kb.available(k, conn), "meta": kb.stored(conn, k)} for k, v in kb.DOCS.items()]}

    @app.post("/api/kb/{doc_id}/link")
    def kb_link(doc_id: str, request: Request, p: Principal = Depends(require("today.read"))):
        with tx(request) as conn:
            ok = kb.available(doc_id, conn)
        if not ok:
            raise HTTPException(404, "document not loaded")
        return {"url": f"/kb/view/{doc_id}?t=" + kb.sign_link(request.app.state.settings.database_url, doc_id, p.user_id), "expires_in": kb.LINK_TTL_S}

    @app.get("/kb/view/{doc_id}", response_class=HTMLResponse, include_in_schema=False)
    def kb_view(doc_id: str, t: str, request: Request):
        with tx(request) as conn:
            ok = kb.available(doc_id, conn)
            doc = kb.read_doc(doc_id, conn) if ok else ""
        if not ok or not kb.verify_link(request.app.state.settings.database_url, t, doc_id):
            raise HTTPException(404, "not found")
        return HTMLResponse(doc, headers={"Content-Security-Policy": kb.KB_CSP, "X-Frame-Options": "SAMEORIGIN", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def register_kb_admin(app: FastAPI) -> None:
    from ..audit import audit

    def tx(request: Request):
        return request.app.state.db.tx()

    @app.post("/api/admin/kb/{doc_id}")
    async def kb_upload(doc_id: str, request: Request, file: UploadFile = File(...), p: Principal = Depends(require("admin.sources"))):
        if doc_id not in kb.DOCS:
            raise HTTPException(404, "unknown document")
        raw = await file.read(2_500_001)
        if len(raw) > 2_500_000:
            raise HTTPException(400, "file is larger than 2.5 MB")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(400, "file must be UTF-8 HTML") from exc
        if "<html" not in text[:2000].lower() and "<!doctype html" not in text[:200].lower():
            raise HTTPException(400, "this does not look like an HTML document")
        sha = hashlib.sha256(raw).hexdigest()
        with tx(request) as conn:
            conn.execute("INSERT INTO kb_documents(doc_id, html, sha256, size_bytes, uploaded_by) VALUES (%s,%s,%s,%s,%s) "
                         "ON CONFLICT (doc_id) DO UPDATE SET html=EXCLUDED.html, sha256=EXCLUDED.sha256, size_bytes=EXCLUDED.size_bytes, uploaded_by=EXCLUDED.uploaded_by, uploaded_at=now()",
                         (doc_id, text, sha, len(raw), p.email))
            audit(conn, actor=p.email, operation="kb_upload", record_type="kb_document", record_id=doc_id, detail={"bytes": len(raw), "sha256": sha})
        return {"doc_id": doc_id, "bytes": len(raw), "sha256": sha}
