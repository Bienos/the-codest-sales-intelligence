"""People database API: every contact from the sheets, Apollo, Pipedrive and Lemlist in one list, with the company's current buying signal,
who owns the account, and what has happened in outreach. Row scope follows the account; email addresses are never returned."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from ..access import Principal, org_scope_sql
from ..audit import audit
from ..db import q1, qa
from ..jobs.queue import enqueue
from ..market import region_for
from ..people import SYSTEM_LABEL, email_state, engagement
from ..signals.seller import leading_signals
from .auth import require
from .profiles import LIST_KEYS, committee_group, title_seniority
from .search import all_like, tokens
from .today import _fits as best_fits

ENGAGEMENT_LABEL = {"unsubscribed": "Unsubscribed", "bounced": "Bounced", "interested": "Interested", "replied": "Replied", "not_interested": "Not interested",
                    "clicked": "Clicked", "opened": "Opened", "sent": "Emailed", "pushed": "In a campaign", None: "Not contacted"}


def people_rows(conn, p: Principal, q: str = "") -> list[dict[str, Any]]:
    where, params = org_scope_sql(p, "o.id")
    terms = tokens(q)
    match, margs = all_like("concat_ws(' ', pe.full_name, e.title, o.display_name, o.canonical_domain)", terms) if terms else ("TRUE", [])
    rows = qa(conn, f"""
        SELECT pe.id, pe.full_name, pe.work_email, pe.email_verification_status, pe.suppressed, pe.suppression_reason, pe.linkedin_url IS NOT NULL AS has_linkedin, pe.photo_url,
               e.title, e.buyer_lane, e.verification_status AS role_status, e.source_link_id IS NOT NULL AS from_sheets,
               o.id AS org_id, o.display_name AS org_name, o.country_code, o.segment, o.logo_url AS org_logo_url, s.band, s.score, s.stopped,
               c.owner_id AS crm_owner_id, c.open_deals_count,
               (SELECT array_agg(DISTINCT ps.system) FROM person_sources ps WHERE ps.person_id=pe.id) AS systems,
               EXISTS (SELECT 1 FROM contact_candidates cc WHERE cc.person_id=pe.id) AS from_apollo,
               EXISTS (SELECT 1 FROM crm_persons cp WHERE cp.person_id=pe.id) AS in_pipedrive,
               (SELECT cp.owner_id FROM crm_persons cp WHERE cp.person_id=pe.id LIMIT 1) AS crm_person_owner,
               (SELECT array_agg(oe.status) FROM outbound_enrolments oe WHERE oe.person_id=pe.id) AS enrol_statuses,
               (SELECT array_agg(DISTINCT la.type) FROM lemlist_activities la WHERE la.person_id=pe.id) AS activity_types,
               (SELECT max(la.happened_at) FROM lemlist_activities la WHERE la.person_id=pe.id) AS last_activity_at,
               (SELECT count(*) FROM signal_campaign_members m WHERE m.person_id=pe.id AND m.included) AS campaigns
        FROM people pe
        JOIN LATERAL (SELECT * FROM employment e WHERE e.person_id=pe.id ORDER BY (e.valid_to IS NULL) DESC, e.observed_at DESC NULLS LAST LIMIT 1) e ON TRUE
        JOIN organizations o ON o.id=e.organization_id
        LEFT JOIN account_scores s ON s.organization_id=o.id
        LEFT JOIN crm_organizations c ON c.organization_id=o.id AND c.link_status='linked'
        WHERE pe.erased_at IS NULL AND ({where}) AND ({match})""", [*params, *margs])        # noqa: S608
    from ..people import ACTIVITY_STATUS
    out = []
    for r in rows:
        systems = set(r["systems"] or [])
        systems |= {"sheets"} if r["from_sheets"] else set()
        systems |= {"apollo"} if r["from_apollo"] else set()
        systems |= {"pipedrive"} if r["in_pipedrive"] else set()
        if r["enrol_statuses"] or r["activity_types"]:
            systems.add("lemlist")
        eng = engagement([*(r["enrol_statuses"] or []), *(ACTIVITY_STATUS.get(t) for t in (r["activity_types"] or []))])
        out.append({"id": str(r["id"]), "name": r["full_name"], "title": r["title"], "lane": r["buyer_lane"], "role_verified": r["role_status"] == "verified",
                    "email": email_state(r["work_email"], r["email_verification_status"]), "has_linkedin": r["has_linkedin"], "photo_url": r["photo_url"],
                    "suppressed": bool(r["suppressed"]), "suppression_reason": r["suppression_reason"] if r["suppressed"] else None,
                    "company": {"id": str(r["org_id"]), "name": r["org_name"], "country": r["country_code"], "region": region_for(r["country_code"]), "segment": r["segment"], "logo_url": r["org_logo_url"],
                                "band": r["band"] or "none", "score": float(r["score"]) if r["score"] is not None else None, "stopped": bool(r["stopped"])},
                    "owner": {"crm_owner_id": r["crm_owner_id"] or r["crm_person_owner"], "open_deals": r["open_deals_count"] or 0, "mine": bool(p.pipedrive_user_id and (r["crm_owner_id"] or r["crm_person_owner"]) == p.pipedrive_user_id)},
                    "sources": sorted(systems, key=list(SYSTEM_LABEL).index), "engagement": eng, "engagement_label": ENGAGEMENT_LABEL.get(eng, eng),
                    "last_activity_at": r["last_activity_at"].isoformat() if r["last_activity_at"] else None, "campaigns": r["campaigns"]})
    return out


def _safe_url(value: Any) -> str | None:
    u = str(value or "").strip()
    return u if re.match(r"^https?://[^\s]+$", u, re.I) else None


def page_extras(conn, items: list[dict[str, Any]]) -> None:
    """What the cards in the list need beyond the filters, added to ONE page of people (so it stays cheap whatever the size of the database):
    where each person sits in the buying committee (the same reading as the company and person pages), their LinkedIn address, and the
    company's best-fit service. Anything we cannot work out is simply left out; a card never shows a placeholder for it."""
    if not items:
        return
    ids = [i["id"] for i in items]
    pages = {str(r["id"]): r["linkedin_url"] for r in qa(conn, "SELECT id, linkedin_url FROM people WHERE id = ANY(%s::uuid[])", (ids,))}
    lead: dict[str, dict[str, str]] = {}
    exa: dict[str, dict[str, Any]] = {}
    for r in qa(conn, "SELECT person_id, system, detail FROM person_sources WHERE person_id = ANY(%s::uuid[]) AND system IN ('sheets','exa') ORDER BY first_seen_at", (ids,)):
        pid, detail = str(r["person_id"]), r["detail"] if isinstance(r["detail"], dict) else {}
        if r["system"] == "sheets":
            lead.setdefault(pid, {}).update({k: str(v).strip() for k, v in detail.items() if k in LIST_KEYS and str(v or "").strip() and str(v).strip() != "Unknown"})
        else:
            exa[pid] = detail
    fits = best_fits(conn, sorted({i["company"]["id"] for i in items}))
    for i in items:
        listed, found = lead.get(i["id"], {}), exa.get(i["id"], {})
        seniority = listed.get("seniority") or found.get("seniority") or title_seniority(i["title"])
        i["seniority"] = seniority
        i["committee"] = committee_group(i["title"], seniority, listed.get("persona") or found.get("persona"), listed.get("committee_role"))
        i["linkedin_url"] = _safe_url(pages.get(i["id"]))
        i["fit"] = fits.get(i["company"]["id"])


def _filter(rows, *, source: str, email: str, eng: str, region: str, signal: str, rule: str, owner: str, suppressed: bool) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not suppressed and r["suppressed"]:
            continue
        if source and source not in r["sources"]:
            continue
        if email == "any" and r["email"] == "none":            # every address we hold is usable; "any" means "has an email"
            continue
        if email and email != "any" and r["email"] != email:
            continue
        if eng == "not_contacted" and r["engagement"]:
            continue
        if eng and eng != "not_contacted" and r["engagement"] != eng:
            continue
        if region and r["company"]["region"] != region:
            continue
        if signal == "yes" and not r.get("signal"):
            continue
        if signal == "no" and r.get("signal"):
            continue
        if rule and (r.get("signal") or {}).get("rule_id") != rule:
            continue
        if owner == "mine" and not r["owner"]["mine"]:
            continue
        if owner == "unowned" and r["owner"]["crm_owner_id"]:
            continue
        out.append(r)
    return out


def register_people(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    @app.get("/api/people")
    def people(request: Request, q: str = Query("", max_length=120), source: str = Query("", pattern="^(|sheets|apollo|pipedrive|lemlist|manual|exa)$"),
               email: str = Query("", pattern="^(|any|verified|unverified|none)$"),
               engagement_: str = Query("", alias="engagement", pattern="^(|not_contacted|pushed|sent|opened|clicked|replied|interested|not_interested|bounced|unsubscribed)$"),
               region: str = Query("", pattern="^(|uk|dach|nordics|poland|europe|us|other|unknown)$"), signal: str = Query("", pattern="^(|yes|no)$"),
               rule: str = Query("", max_length=8), owner: str = Query("", pattern="^(|mine|unowned)$"), suppressed: bool = False,
               sort: str = Query("signal", pattern="^(signal|name|company|activity)$"), page: int = Query(1, ge=1), size: int = Query(50, ge=10, le=200),
               p: Principal = Depends(require("contacts.read"))):
        with tx(request) as conn:
            rows = people_rows(conn, p, q)
            sig = leading_signals(conn, sorted({r["company"]["id"] for r in rows}))
            for r in rows:
                s = sig.get(r["company"]["id"])
                r["signal"] = {"rule_id": s["rule_id"], "name": s["label"], "summary": s["summary"], "date": (s["happened_at"] or s["published_at"]), "tier": s["demand_tier"],
                               "source_url": s["source_url"], "source_name": s["source_name"]} if s else None
            filtered = _filter(rows, source=source, email=email, eng=engagement_, region=region, signal=signal, rule=rule, owner=owner, suppressed=suppressed)
            tier = {"D1": 0, "D2": 1, "D3": 2, "D4": 3}
            keys = {"signal": lambda r: (r["signal"] is None, tier.get((r["signal"] or {}).get("tier"), 9), -(r["company"]["score"] or 0), r["company"]["name"].lower(), r["name"].lower()),
                    "name": lambda r: r["name"].lower(), "company": lambda r: (r["company"]["name"].lower(), r["name"].lower()),
                    "activity": lambda r: (r["last_activity_at"] is None, -(datetime.fromisoformat(r["last_activity_at"]).timestamp() if r["last_activity_at"] else 0))}
            filtered.sort(key=keys[sort])
            total = len(filtered)
            items = filtered[(page - 1) * size: page * size]
            page_extras(conn, items)
            counts = {"total": len(rows), "with_signal": sum(1 for r in rows if r["signal"] and not r["suppressed"]),
                      "verified_email": sum(1 for r in rows if r["email"] == "verified" and not r["suppressed"]),
                      "with_email": sum(1 for r in rows if r["email"] != "none" and not r["suppressed"]),
                      "by_source": {k: sum(1 for r in rows if k in r["sources"]) for k in SYSTEM_LABEL},
                      "engaged": sum(1 for r in rows if r["engagement"] in ("replied", "interested")), "suppressed": sum(1 for r in rows if r["suppressed"])}
            audit(conn, actor=p.email, operation="pii_read", record_type="people_list", record_id=None, detail={"count": len(items), "q": q[:60]})
            return {"items": items, "total": total, "page": page, "size": size, "counts": counts, "sources": SYSTEM_LABEL, "engagement_labels": {k or "not_contacted": v for k, v in ENGAGEMENT_LABEL.items()}}

    @app.get("/api/people/sync-status")
    def sync_status(request: Request, p: Principal = Depends(require("contacts.read"))):
        s = request.app.state.settings
        with tx(request) as conn:
            st = {f"{r['system']}:{r['entity']}": r["watermark"] for r in qa(conn, "SELECT system, entity, watermark FROM sync_state")}
            last_job = {r["kind"]: {"status": r["status"], "at": r["updated_at"], "error": r["last_error"]} for r in qa(conn,
                        "SELECT DISTINCT ON (kind) kind, status, updated_at, last_error FROM jobs WHERE kind IN ('crm_sync','lemlist_sync') ORDER BY kind, created_at DESC")}
        return {"pipedrive": {"configured": bool(s.pipedrive_api_token and s.pipedrive_domain), "last_change_seen": st.get("pipedrive:persons"), "last_run": last_job.get("crm_sync"),
                              "how": "Set PIPEDRIVE_API_TOKEN and PIPEDRIVE_DOMAIN. Organisations, people and deals sync every hour; people are matched by email."},
                "lemlist": {"configured": bool(s.lemlist_api_key), "last_sync": st.get("lemlist:activities"), "last_run": last_job.get("lemlist_sync"),
                            "how": "Set LEMLIST_API_KEY. Opens, replies, bounces and unsubscribes are read back every hour; bounces and unsubscribes suppress the person everywhere."},
                "apollo": {"configured": bool(s.apollo_api_key), "how": "Set APOLLO_API_KEY. Use Find decision makers on an account; revealing an email needs an approval."},
                "sheets": {"configured": True, "how": "Upload the workbooks in Administration, Imports."},
                "can_sync": p.can("admin.jobs") or p.role == "sales_manager"}

    @app.post("/api/people/sync/{system}")
    def sync_now(system: str, request: Request, p: Principal = Depends(require("contacts.read"))):
        if not (p.can("admin.jobs") or p.role == "sales_manager"):
            raise HTTPException(403, "A manager or an administrator starts a sync.")
        s = request.app.state.settings
        kind = {"pipedrive": "crm_sync", "lemlist": "lemlist_sync"}.get(system)
        if not kind:
            raise HTTPException(404, "unknown system")
        if kind == "crm_sync" and not (s.pipedrive_api_token and s.pipedrive_domain):
            raise HTTPException(409, "Pipedrive is not connected yet. Set PIPEDRIVE_API_TOKEN and PIPEDRIVE_DOMAIN.")
        if kind == "lemlist_sync" and not s.lemlist_api_key:
            raise HTTPException(409, "Lemlist is not connected yet. Set LEMLIST_API_KEY.")
        with tx(request) as conn:
            jid, created = enqueue(conn, kind, {"by": p.email}, idempotency_key=f"{kind}:manual:{datetime.now(timezone.utc):%Y%m%d%H%M}", max_attempts=2)
            audit(conn, actor=p.email, operation=f"{kind}_requested", record_type="job", record_id=jid, detail={})
            return {"job_id": jid, "queued": created}

    @app.get("/api/people/{pid}")
    def person(pid: str, request: Request, p: Principal = Depends(require("contacts.read"))):
        with tx(request) as conn:
            where, params = org_scope_sql(p, "e.organization_id")
            r = q1(conn, f"SELECT pe.id FROM people pe JOIN employment e ON e.person_id=pe.id WHERE pe.id::text=%s AND pe.erased_at IS NULL AND ({where}) LIMIT 1", [pid, *params])   # noqa: S608
            if not r:
                raise HTTPException(404, "not found")
            sources = qa(conn, "SELECT system, external_id, first_seen_at, last_seen_at, detail FROM person_sources WHERE person_id=%s ORDER BY first_seen_at", (pid,))
            activity = qa(conn, "SELECT type, campaign_name, happened_at FROM lemlist_activities WHERE person_id=%s ORDER BY happened_at DESC NULLS LAST LIMIT 30", (pid,))
            enrol = qa(conn, "SELECT oe.status, oe.created_at, oe.last_event_at, coalesce(sc.name, 'Account sequence') AS campaign FROM outbound_enrolments oe "
                             "LEFT JOIN signal_campaigns sc ON sc.id=oe.signal_campaign_id WHERE oe.person_id=%s ORDER BY oe.created_at DESC", (pid,))
            audit(conn, actor=p.email, operation="pii_read", record_type="person", record_id=pid, detail={})
            return {"sources": [{**s, "label": SYSTEM_LABEL.get(s["system"], s["system"])} for s in sources], "activity": activity, "enrolments": enrol}
