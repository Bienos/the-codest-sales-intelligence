"""Company page and person page API: Exa research (company and person), finding decision makers with Exa, the people at an account,
our relationship with the company, who owns it, and tenders where it is the buyer.

Row scope follows the account (org_scope_sql) on every route; a person is visible through the account they work at. Email addresses are
never returned, only whether one exists and whether it was verified. Every read of personal data is audited. Research costs money, so
starting it needs `research.request`, one run at a time per subject, a short cool-down and a daily limit per person.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ..access import Principal, org_scope_sql
from ..audit import audit
from ..db import q1, qa
from ..enrichment.exa_people import seniority as seniority_from_title
from ..jobs.queue import enqueue
from ..people import ACTIVITY_STATUS, SYSTEM_LABEL, email_state, engagement
from ..research.exa_search import configured
from .auth import require

DAILY_RESEARCH_LIMIT = 40          # research runs (company, person, people search) one person may start per 24 hours
COOLDOWN_MINUTES = 60              # a finished profile is not redone within this window (it would cost money for the same answer)
RESEARCH_OPS = ("exa_company_research", "exa_person_research", "exa_people_search")
RELATIONSHIP = ("none", "prospect", "in_talks", "past_client", "client")
ASSIGNABLE_ROLES = ("bdm", "sales_manager", "admin")
SENIORITY_ORDER = {"Executive": 0, "C-level": 0, "VP": 1, "Director / Head": 2, "Head / Director": 2, "Manager": 3, "Manager / Lead": 3, "Specialist / Lead": 4}
LIST_KEYS = ("seniority", "committee_role", "persona", "priority", "angle", "signal")
# Where someone sits in the buying committee, for grouping people on the company page. Read from the lead list's committee role and persona,
# the seniority and the job title; procurement first (a procurement director is not the budget holder), then the top of the organisation.
COMMITTEE = ("decision", "technical", "procurement", "influencer")
_PROC = re.compile(r"procure|purchas|sourcing|supplier|vendor|tprm|contracting|category manag", re.I)
_TOP = re.compile(r"\b(chief|c[etiodfsx]o|ciso|founder|president|managing director|board|vorstand|prezes|vp|vice[- ]president|svp|evp|general manager)\b", re.I)
_TECH = re.compile(r"engineer|technolog|technical|architect|develop|software|platform|delivery|\bit\b|data|\bai\b|cloud|devops|infrastructure|security|\bqa\b|test", re.I)


def title_seniority(title: str | None) -> str | None:
    """Seniority read from a job title (C-level, VP, Head / Director, Manager / Lead), when the lead list does not give one."""
    return seniority_from_title(title)


def committee_group(title: str | None, seniority: str | None = None, persona: str | None = None, committee_role: str | None = None) -> str:
    role, per, t = committee_role or "", persona or "", title or ""
    if _PROC.search(role) or _PROC.search(per) or _PROC.search(t):
        return "procurement"
    if seniority in ("Executive", "C-level", "VP") or _TOP.search(t):
        return "decision"
    if re.search(r"influenc", role, re.I) and not re.search(r"sponsor", role, re.I):
        return "influencer"
    if _TECH.search(role) or _TECH.search(per) or _TECH.search(t):
        return "technical"
    return "influencer"


ENGAGEMENT_LABEL = {"unsubscribed": "Unsubscribed", "bounced": "Bounced", "interested": "Interested", "replied": "Replied", "not_interested": "Not interested",
                    "clicked": "Clicked", "opened": "Opened", "sent": "Emailed", "pushed": "In a campaign", None: "Not contacted"}


class RelationshipIn(BaseModel):
    status: Literal["none", "prospect", "in_talks", "past_client", "client"] | None = None
    note: str | None = Field(default=None, max_length=600)


class OwnerIn(BaseModel):
    user_id: str | None = None


def _uuid(v: str) -> str:
    try:
        return str(uuid.UUID(v))
    except ValueError:
        raise HTTPException(404, "not found") from None


def _visible_org(conn, p: Principal, oid: str) -> dict[str, Any]:
    oid = _uuid(oid)
    where, params = org_scope_sql(p, "o.id")
    o = q1(conn, f"SELECT o.id, o.display_name, o.canonical_domain, o.owner_user_id FROM organizations o WHERE o.id=%s AND ({where})", [oid, *params])     # noqa: S608
    if not o:
        raise HTTPException(404, "not found")       # 404, not 403: do not reveal what exists outside the caller's scope
    return o


def _visible_person(conn, p: Principal, pid: str) -> dict[str, Any]:
    pid = _uuid(pid)
    where, params = org_scope_sql(p, "e.organization_id")
    r = q1(conn, f"SELECT pe.id, e.organization_id FROM people pe JOIN employment e ON e.person_id=pe.id WHERE pe.id=%s AND pe.erased_at IS NULL AND ({where}) "   # noqa: S608
                 "ORDER BY (e.valid_to IS NULL) DESC, e.observed_at DESC NULLS LAST LIMIT 1", [pid, *params])
    if not r:
        raise HTTPException(404, "not found")
    return r


def _quota(conn, p: Principal) -> None:
    n = (q1(conn, "SELECT count(*) AS n FROM audit_events WHERE actor=%s AND operation = ANY(%s) AND at > now() - interval '24 hours'", (p.email, list(RESEARCH_OPS))) or {"n": 0})["n"]
    if n >= DAILY_RESEARCH_LIMIT:
        raise HTTPException(429, f"Daily research limit reached ({DAILY_RESEARCH_LIMIT} per person). It resets 24 hours after the first one.")


def _setup(request: Request, conn: Any = None) -> dict[str, Any]:
    return configured(request.app.state.settings, conn)


def _run_row(r: dict[str, Any] | None) -> dict[str, Any] | None:
    if not r:
        return None
    out = dict(r, id=str(r["id"]))
    if "exa_cost_usd" in out and out["exa_cost_usd"] is not None:
        out["exa_cost_usd"] = float(out["exa_cost_usd"])
    for k in ("person_id", "organization_id"):
        if out.get(k) is not None:
            out[k] = str(out[k])
    return out


def _research_state(conn, table: str, col: str, rid: str, setup: dict[str, Any]) -> dict[str, Any]:
    cols = "id, status, content, sources, searches, exa_cost_usd, model, dropped, error, requested_by, created_at, finished_at"
    latest = q1(conn, f"SELECT {cols} FROM {table} WHERE {col}=%s ORDER BY created_at DESC LIMIT 1", (rid,))      # noqa: S608 - fixed identifiers
    done = latest if latest and latest["status"] == "done" else q1(conn, f"SELECT {cols} FROM {table} WHERE {col}=%s AND status='done' ORDER BY created_at DESC LIMIT 1", (rid,))  # noqa: S608
    running = bool(latest and latest["status"] in ("queued", "running"))
    return {"profile": _run_row(done), "latest": _run_row({k: v for k, v in latest.items() if k not in ("content", "sources")}) if latest else None,
            "running": running, "setup": setup}


def _start_research(conn, p: Principal, *, table: str, col: str, rid: str, org_id: str | None, kind: str, op: str, record_type: str, setup: dict[str, Any]) -> dict[str, Any]:
    if not setup["ready"]:
        raise HTTPException(409, setup["note"])
    if q1(conn, f"SELECT 1 FROM {table} WHERE {col}=%s AND status IN ('queued','running') AND created_at > now() - interval '15 minutes'", (rid,)):   # noqa: S608
        raise HTTPException(409, "Research is already running. It takes about a minute.")
    recent = q1(conn, f"SELECT finished_at FROM {table} WHERE {col}=%s AND status='done' AND finished_at > now() - make_interval(mins => %s) ORDER BY finished_at DESC LIMIT 1",   # noqa: S608
                (rid, COOLDOWN_MINUTES))
    if recent:
        raise HTTPException(409, f"This was researched less than {COOLDOWN_MINUTES} minutes ago. The profile below is current.")
    _quota(conn, p)
    if table == "person_profiles":
        row = conn.execute("INSERT INTO person_profiles(person_id, organization_id, requested_by) VALUES (%s,%s,%s) RETURNING id", (rid, org_id, p.email)).fetchone()
    else:
        row = conn.execute("INSERT INTO account_profiles(organization_id, requested_by) VALUES (%s,%s) RETURNING id", (rid, p.email)).fetchone()
    new_id = str(row["id"])      # type: ignore[index]
    enqueue(conn, kind, {"profile_id": new_id}, idempotency_key=f"{kind}:{new_id}", max_attempts=1)
    audit(conn, actor=p.email, operation=op, record_type=record_type, record_id=rid, detail={"profile_id": new_id})
    return {"profile_id": new_id, "status": "queued"}


# ------------------------------------------------------------------ relationship
def relationship_facts(conn, oid: str) -> dict[str, Any]:
    """What our own data says about the relationship: lead list, people we know, outreach, campaigns, deals."""
    o = q1(conn, "SELECT in_master_list, list_account_id, list_eligibility, list_priority, list_score, relationship, relationship_note, relationship_set_by, relationship_set_at, "
                 "owner_user_id, owner_assigned_by, owner_assigned_at FROM organizations WHERE id=%s", (oid,))
    people = q1(conn, "SELECT count(DISTINCT pe.id) AS n, count(DISTINCT pe.id) FILTER (WHERE pe.work_email IS NOT NULL) AS with_email, "
                      "count(DISTINCT pe.id) FILTER (WHERE pe.email_verification_status='Verified' AND pe.work_email IS NOT NULL) AS verified, "
                      "count(DISTINCT pe.id) FILTER (WHERE pe.suppressed) AS suppressed FROM employment e JOIN people pe ON pe.id=e.person_id "
                      "WHERE e.organization_id=%s AND pe.erased_at IS NULL", (oid,)) or {}
    enrol = {r["status"]: {"n": r["n"], "last": r["last"]} for r in qa(conn, "SELECT status, count(*) AS n, max(coalesce(last_event_at, created_at)) AS last FROM outbound_enrolments "
                                                                          "WHERE organization_id=%s GROUP BY status", (oid,))}
    act = {r["type"]: {"n": r["n"], "last": r["last"]} for r in qa(conn, "SELECT la.type, count(DISTINCT la.id) AS n, max(la.happened_at) AS last FROM lemlist_activities la "
                                                                      "JOIN employment e ON e.person_id=la.person_id WHERE e.organization_id=%s GROUP BY la.type", (oid,))}
    campaigns = qa(conn, "SELECT DISTINCT sc.id, sc.name, sc.status FROM signal_campaign_members m JOIN signal_campaigns sc ON sc.id=m.campaign_id "
                         "WHERE m.organization_id=%s AND m.included ORDER BY sc.name LIMIT 10", (oid,))
    drafts = q1(conn, "SELECT count(*) AS n, count(*) FILTER (WHERE status='approved') AS approved FROM campaign_drafts WHERE organization_id=%s", (oid,)) or {"n": 0, "approved": 0}
    crm = q1(conn, "SELECT c.open_deals_count, (SELECT count(*) FROM crm_deals d WHERE d.crm_org_id=c.crm_id AND d.status='won') AS won, "
                   "(SELECT count(*) FROM crm_deals d WHERE d.crm_org_id=c.crm_id AND d.status='lost') AS lost FROM crm_organizations c "
                   "WHERE c.organization_id=%s AND c.link_status='linked' LIMIT 1", (oid,))
    statuses = [*enrol.keys(), *(ACTIVITY_STATUS.get(t) for t in act)]
    eng = engagement(statuses)
    contacted = bool(enrol) or any(t in act for t in ("emailsSent", "emailsOpened", "emailsClicked", "emailsReplied", "emailsBounced"))
    lasts = [x["last"] for x in [*enrol.values(), *act.values()] if x["last"]]
    won = int(crm["won"]) if crm else 0
    open_deals = int(crm["open_deals_count"] or 0) if crm else 0
    if won:
        suggested, why = "past_client", "A deal with this company was won before."
    elif open_deals or eng in ("interested", "replied"):
        suggested, why = "in_talks", "Someone there replied to our outreach." if eng in ("interested", "replied") else "There is an open deal."
    elif contacted:
        suggested, why = "prospect", "We have contacted people there, with no reply yet."
    elif o and o["in_master_list"]:
        suggested, why = "prospect", "It is on your lead list but has not been contacted."
    else:
        suggested, why = "none", "Nothing in our data shows any contact with this company."
    return {
        "recorded": {"status": o["relationship"], "note": o["relationship_note"], "set_by": o["relationship_set_by"], "set_at": o["relationship_set_at"]} if o else None,
        "suggested": suggested, "suggested_why": why,
        "lead_list": {"on_list": bool(o and (o["in_master_list"] or o["list_account_id"])), "account_id": o["list_account_id"] if o else None,
                      "eligibility": o["list_eligibility"] if o else None, "priority": o["list_priority"] if o else None,
                      "score": float(o["list_score"]) if o and o["list_score"] is not None else None},
        "people": {k: int(people.get(k) or 0) for k in ("n", "with_email", "verified", "suppressed")},
        "outreach": {"contacted": contacted, "engagement": eng, "engagement_label": ENGAGEMENT_LABEL.get(eng, eng), "last_at": max(lasts) if lasts else None,
                     "enrolments": sum(x["n"] for x in enrol.values()), "replies": sum(act.get(t, {}).get("n", 0) for t in ("emailsReplied", "emailsInterested", "interested")),
                     "bounces": act.get("emailsBounced", {}).get("n", 0) + enrol.get("bounced", {}).get("n", 0),
                     "unsubscribes": act.get("emailsUnsubscribed", {}).get("n", 0) + enrol.get("unsubscribed", {}).get("n", 0)},
        "campaigns": [dict(c, id=str(c["id"])) for c in campaigns], "drafts": {"n": int(drafts["n"]), "approved": int(drafts["approved"])},
        "deals": {"open": open_deals, "won": won, "lost": int(crm["lost"]) if crm else 0, "linked": bool(crm)},
    }


def _owner(conn, o: dict[str, Any]) -> dict[str, Any] | None:
    uid = o.get("owner_user_id")
    if not uid:
        return None
    u = q1(conn, "SELECT id, display_name, email, role, active FROM users WHERE id=%s", (uid,))
    return {"user_id": str(uid), "name": u["display_name"] if u else None, "email": u["email"] if u else None, "active": bool(u and u["active"]),
            "assigned_by": o.get("owner_assigned_by"), "assigned_at": o.get("owner_assigned_at")}


def account_people(conn, oid: str) -> list[dict[str, Any]]:
    rows = qa(conn, """
        SELECT DISTINCT ON (pe.id) pe.id, pe.full_name, pe.linkedin_url, pe.photo_url, pe.work_email, pe.email_verification_status, pe.suppressed, pe.suppression_reason,
               e.title, e.buyer_lane, e.verification_status AS role_status, e.evidence_url, e.source_link_id IS NOT NULL AS from_sheets,
               (SELECT coalesce(jsonb_agg(jsonb_build_object('system', ps.system, 'detail', ps.detail) ORDER BY ps.first_seen_at), '[]'::jsonb)
                  FROM person_sources ps WHERE ps.person_id=pe.id) AS sources,
               (SELECT array_agg(DISTINCT la.type) FROM lemlist_activities la WHERE la.person_id=pe.id) AS activity_types,
               (SELECT array_agg(oe.status) FROM outbound_enrolments oe WHERE oe.person_id=pe.id) AS enrol_statuses,
               (SELECT max(la.happened_at) FROM lemlist_activities la WHERE la.person_id=pe.id) AS last_activity_at,
               (SELECT max(pp.finished_at) FROM person_profiles pp WHERE pp.person_id=pe.id AND pp.status='done') AS researched_at
        FROM employment e JOIN people pe ON pe.id=e.person_id
        WHERE e.organization_id=%s AND pe.erased_at IS NULL
        ORDER BY pe.id, (e.valid_to IS NULL) DESC, e.observed_at DESC NULLS LAST""", (oid,))
    out = []
    for r in rows:
        systems = {s["system"] for s in r["sources"]} | ({"sheets"} if r["from_sheets"] else set())
        lead: dict[str, str] = {}
        for s in r["sources"]:
            if s["system"] == "sheets":
                lead.update({k: str(v).strip() for k, v in (s["detail"] or {}).items() if k in LIST_KEYS and str(v or "").strip() and str(v).strip() != "Unknown"})
        exa = next((s["detail"] for s in r["sources"] if s["system"] == "exa"), None)
        eng = engagement([*(r["enrol_statuses"] or []), *(ACTIVITY_STATUS.get(t) for t in (r["activity_types"] or []))])
        if r["enrol_statuses"] or r["activity_types"]:
            systems.add("lemlist")
        seniority = lead.get("seniority") or (exa or {}).get("seniority") or title_seniority(r["title"])
        persona = lead.get("persona") or (exa or {}).get("persona")
        out.append({"id": str(r["id"]), "name": r["full_name"], "title": r["title"], "lane": r["buyer_lane"], "role_confirmed": r["role_status"] == "verified",
                    "linkedin_url": r["linkedin_url"], "photo_url": r["photo_url"], "email": email_state(r["work_email"], r["email_verification_status"]), "has_email": bool(r["work_email"]),
                    "suppressed": bool(r["suppressed"]), "suppression_reason": r["suppression_reason"] if r["suppressed"] else None,
                    "lead_list": lead or None, "seniority": seniority, "persona": persona,
                    "committee": committee_group(r["title"], seniority, persona, lead.get("committee_role")),
                    "sources": sorted(systems, key=lambda s: list(SYSTEM_LABEL).index(s) if s in SYSTEM_LABEL else 99),
                    "engagement": eng, "engagement_label": ENGAGEMENT_LABEL.get(eng, eng), "last_activity_at": r["last_activity_at"], "researched_at": r["researched_at"]})
    prio = {"P1": 0, "P2": 1, "P3": 2}
    out.sort(key=lambda x: (x["suppressed"], prio.get((x["lead_list"] or {}).get("priority", ""), 5), SENIORITY_ORDER.get(x["seniority"] or "", 6),
                            0 if x["has_email"] else 1, x["name"].casefold()))      # every address we hold is usable: having one is what counts
    return out


def register_profiles(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    # ------------------------------------------------------------------ company research
    @app.get("/api/accounts/{oid}/research")
    def company_research(oid: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            o = _visible_org(conn, p, oid)
            logo = q1(conn, "SELECT logo_url, logo_checked_at FROM organizations WHERE id=%s", (o["id"],)) or {}
            return {**_research_state(conn, "account_profiles", "organization_id", str(o["id"]), _setup(request, conn)), "can_run": p.can("research.request"),
                    "logo_url": logo.get("logo_url"), "logo_checked_at": logo.get("logo_checked_at")}

    @app.post("/api/accounts/{oid}/research")
    def start_company_research(oid: str, request: Request, p: Principal = Depends(require("research.request"))):
        with tx(request) as conn:
            o = _visible_org(conn, p, oid)
            return _start_research(conn, p, table="account_profiles", col="organization_id", rid=str(o["id"]), org_id=str(o["id"]), kind="account_profile",
                                   op="exa_company_research", record_type="organization", setup=_setup(request, conn))

    # ------------------------------------------------------------------ people at the account
    @app.get("/api/accounts/{oid}/people")
    def people_at_account(oid: str, request: Request, p: Principal = Depends(require("contacts.read"))):
        with tx(request) as conn:
            o = _visible_org(conn, p, oid)
            items = account_people(conn, str(o["id"]))
            audit(conn, actor=p.email, operation="pii_read", record_type="organization_people", record_id=str(o["id"]), detail={"count": len(items)})
            return {"items": items, "sources": SYSTEM_LABEL, "can_research": p.can("research.request")}

    @app.get("/api/accounts/{oid}/people-search")
    def people_search_state(oid: str, request: Request, p: Principal = Depends(require("contacts.read"))):
        with tx(request) as conn:
            o = _visible_org(conn, p, oid)
            s = q1(conn, "SELECT id, status, searches, exa_cost_usd, found, new_candidates, already_known, skipped, error, requested_by, created_at, finished_at "
                         "FROM people_searches WHERE organization_id=%s ORDER BY created_at DESC LIMIT 1", (o["id"],))
            items = qa(conn, "SELECT id, name, title, persona, seniority, linkedin_url, photo_url, snippet, company_match, status, person_id, created_at FROM people_candidates "
                             "WHERE organization_id=%s AND status IN ('found','already_known','added') ORDER BY (status='found') DESC, company_match, created_at DESC LIMIT 60", (o["id"],))
            if items:
                audit(conn, actor=p.email, operation="pii_read", record_type="people_candidates", record_id=str(o["id"]), detail={"count": len(items)})
            ready = _setup(request, conn)["exa"]
            return {"search": _run_row(s), "running": bool(s and s["status"] in ("queued", "running")),
                    "items": [{**_run_row(i), "committee": committee_group(i["title"], i["seniority"], i["persona"])} for i in items],
                    "setup": {"ready": ready,
                              "note": None if ready else "Finding people with Exa is not set up in this environment. It needs EXA_API_KEY and RI_EXA_MONTHLY_CAP_USD on the worker."},
                    "can_run": p.can("research.request")}

    @app.post("/api/accounts/{oid}/people-search")
    def start_people_search(oid: str, request: Request, p: Principal = Depends(require("research.request"))):
        if not p.can("contacts.read"):
            raise HTTPException(403, "missing permission contacts.read")
        with tx(request) as conn:
            if not _setup(request, conn)["exa"]:
                raise HTTPException(409, "Finding people with Exa is not set up in this environment. It needs EXA_API_KEY and RI_EXA_MONTHLY_CAP_USD on the worker.")
            o = _visible_org(conn, p, oid)
            if q1(conn, "SELECT 1 FROM people_searches WHERE organization_id=%s AND status IN ('queued','running') AND created_at > now() - interval '15 minutes'", (o["id"],)):
                raise HTTPException(409, "A search for this company is already running.")
            _quota(conn, p)
            sid = str(conn.execute("INSERT INTO people_searches(organization_id, requested_by) VALUES (%s,%s) RETURNING id", (o["id"], p.email)).fetchone()["id"])   # type: ignore[index]
            enqueue(conn, "people_search", {"search_id": sid}, idempotency_key=f"people_search:{sid}", max_attempts=1)
            audit(conn, actor=p.email, operation="exa_people_search", record_type="organization", record_id=str(o["id"]), detail={"search_id": sid})
            return {"search_id": sid, "status": "queued"}

    def _candidate(conn, p: Principal, cid: str) -> dict[str, Any]:
        c = q1(conn, "SELECT id, organization_id, status FROM people_candidates WHERE id=%s", (_uuid(cid),))
        if not c:
            raise HTTPException(404, "not found")
        _visible_org(conn, p, str(c["organization_id"]))
        return c

    @app.post("/api/people-candidates/{cid}/add")
    def add_candidate(cid: str, request: Request, p: Principal = Depends(require("contacts.read"))):
        from ..enrichment.exa_people import add_candidate as add
        with tx(request) as conn:
            c = _candidate(conn, p, cid)
            if c["status"] == "dismissed":
                raise HTTPException(409, "This person was dismissed. Run the search again to bring them back.")
            try:
                r = add(conn, str(c["id"]), by=p.email)
            except LookupError as exc:
                raise HTTPException(409, str(exc)) from exc
            audit(conn, actor=p.email, operation="person_added_from_exa", record_type="person", record_id=r["person_id"], detail={"candidate_id": str(c["id"]), "created": r["created"]})
            return r

    @app.post("/api/people-candidates/{cid}/dismiss")
    def dismiss_candidate(cid: str, request: Request, p: Principal = Depends(require("contacts.read"))):
        with tx(request) as conn:
            c = _candidate(conn, p, cid)
            conn.execute("UPDATE people_candidates SET status='dismissed', decided_by=%s, updated_at=now() WHERE id=%s AND status='found'", (p.email, c["id"]))
            return {"status": "dismissed"}

    # ------------------------------------------------------------------ person page
    @app.get("/api/people/{pid}/profile")
    def person_detail(pid: str, request: Request, p: Principal = Depends(require("contacts.read"))):
        with tx(request) as conn:
            v = _visible_person(conn, p, pid)
            pid = str(v["id"])
            pe = q1(conn, "SELECT id, full_name, linkedin_url, photo_url, work_email, email_verification_status, suppressed, suppression_reason, created_at FROM people WHERE id=%s", (pid,))
            where, params = org_scope_sql(p, "o.id")
            jobs = qa(conn, f"SELECT e.title, e.buyer_lane, e.verification_status, e.evidence_url, e.valid_from, e.valid_to, e.observed_at, o.id AS org_id, o.display_name, "   # noqa: S608
                            f"o.canonical_domain, o.country_code, o.segment, o.logo_url FROM employment e JOIN organizations o ON o.id=e.organization_id WHERE e.person_id=%s AND ({where}) "
                            "ORDER BY (e.valid_to IS NULL) DESC, e.observed_at DESC NULLS LAST", [pid, *params])
            sources = qa(conn, "SELECT system, external_id, first_seen_at, last_seen_at, detail FROM person_sources WHERE person_id=%s ORDER BY first_seen_at", (pid,))
            lead: dict[str, str] = {}
            for s in sources:
                if s["system"] == "sheets":
                    lead.update({k: str(x).strip() for k, x in (s["detail"] or {}).items() if k in (*LIST_KEYS, "eligibility") and str(x or "").strip() and str(x).strip() != "Unknown"})
            activity = qa(conn, "SELECT type, campaign_name, happened_at FROM lemlist_activities WHERE person_id=%s ORDER BY happened_at DESC NULLS LAST LIMIT 30", (pid,))
            enrol = qa(conn, "SELECT oe.status, oe.created_at, oe.last_event_at, coalesce(sc.name, 'Account sequence') AS campaign, oe.signal_campaign_id, oe.campaign_draft_id "
                             "FROM outbound_enrolments oe LEFT JOIN signal_campaigns sc ON sc.id=oe.signal_campaign_id WHERE oe.person_id=%s ORDER BY oe.created_at DESC", (pid,))
            members = qa(conn, "SELECT sc.id, sc.name, sc.status, m.included, m.exclusion, m.status AS member_status FROM signal_campaign_members m "
                               "JOIN signal_campaigns sc ON sc.id=m.campaign_id WHERE m.person_id=%s ORDER BY sc.created_at DESC LIMIT 20", (pid,))
            research = _research_state(conn, "person_profiles", "person_id", pid, _setup(request, conn))
            company, colleagues = None, []
            if jobs:
                oid = str(jobs[0]["org_id"])
                o = q1(conn, "SELECT employees_estimate, employees_uncertain, list_priority, relationship, logo_url FROM organizations WHERE id=%s", (oid,)) or {}
                about = q1(conn, "SELECT content->'about'->>'text' AS about, finished_at FROM account_profiles WHERE organization_id=%s AND status='done' "
                                 "ORDER BY created_at DESC LIMIT 1", (oid,))
                from ..signals.seller import leading_signals
                sig = leading_signals(conn, [oid]).get(oid)
                company = {"id": oid, "about": about["about"] if about else None, "researched_at": about["finished_at"] if about else None,
                           "employees": o.get("employees_estimate"), "employees_uncertain": bool(o.get("employees_uncertain")), "list_priority": o.get("list_priority"),
                           "relationship": o.get("relationship"), "logo_url": o.get("logo_url"),
                           "signal": {"rule_id": sig["rule_id"], "label": sig["label"], "summary": sig["summary"], "date": sig["happened_at"] or sig["published_at"],
                                      "source_url": sig["source_url"]} if sig else None}
                colleagues = [{k: x[k] for k in ("id", "name", "title", "committee", "has_email", "seniority", "suppressed", "photo_url")}
                              for x in account_people(conn, oid) if x["id"] != pid][:12]
            ll_lead = lead or {}
            eng = engagement([*(e["status"] for e in enrol), *(ACTIVITY_STATUS.get(a["type"]) for a in activity)])
            audit(conn, actor=p.email, operation="pii_read", record_type="person", record_id=pid, detail={"view": "profile"})
            return {
                "person": {"id": pid, "name": pe["full_name"], "linkedin_url": pe["linkedin_url"], "photo_url": pe["photo_url"], "email": email_state(pe["work_email"], pe["email_verification_status"]),
                           "has_email": bool(pe["work_email"]), "seniority": ll_lead.get("seniority") or title_seniority(jobs[0]["title"] if jobs else None),
                           "committee": committee_group(jobs[0]["title"] if jobs else None, ll_lead.get("seniority"), ll_lead.get("persona"), ll_lead.get("committee_role")),
                           "suppressed": bool(pe["suppressed"]), "suppression_reason": pe["suppression_reason"] if pe["suppressed"] else None, "created_at": pe["created_at"]},
                "employment": [{"title": j["title"], "lane": j["buyer_lane"], "role_confirmed": j["verification_status"] == "verified", "evidence_url": j["evidence_url"],
                                "current": j["valid_to"] is None, "valid_from": j["valid_from"], "valid_to": j["valid_to"],
                                "company": {"id": str(j["org_id"]), "name": j["display_name"], "domain": j["canonical_domain"], "country": j["country_code"], "segment": j["segment"],
                                            "logo_url": j["logo_url"]}} for j in jobs],
                "lead_list": lead or None,
                "sources": [{"system": s["system"], "label": SYSTEM_LABEL.get(s["system"], s["system"]), "first_seen_at": s["first_seen_at"], "last_seen_at": s["last_seen_at"]} for s in sources],
                "outreach": {"engagement": eng, "engagement_label": ENGAGEMENT_LABEL.get(eng, eng), "activity": activity,
                             "enrolments": [{k: (str(x) if k.endswith("_id") and x else x) for k, x in e.items()} for e in enrol],
                             "campaigns": [dict(m, id=str(m["id"])) for m in members]},
                "research": research, "can_research": p.can("research.request"), "company": company, "colleagues": colleagues,
            }

    @app.post("/api/people/{pid}/research")
    def start_person_research(pid: str, request: Request, p: Principal = Depends(require("research.request"))):
        if not p.can("contacts.read"):
            raise HTTPException(403, "missing permission contacts.read")
        with tx(request) as conn:
            v = _visible_person(conn, p, pid)
            return _start_research(conn, p, table="person_profiles", col="person_id", rid=str(v["id"]), org_id=str(v["organization_id"]), kind="person_profile",
                                   op="exa_person_research", record_type="person", setup=_setup(request, conn))

    # ------------------------------------------------------------------ relationship and owner
    @app.get("/api/accounts/{oid}/relationship")
    def relationship(oid: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            o = _visible_org(conn, p, oid)
            facts = relationship_facts(conn, str(o["id"]))
            full = q1(conn, "SELECT owner_user_id, owner_assigned_by, owner_assigned_at FROM organizations WHERE id=%s", (o["id"],))
            team = qa(conn, "SELECT id, display_name, email, role FROM users WHERE active AND role = ANY(%s) ORDER BY display_name", (list(ASSIGNABLE_ROLES),))
            return {**facts, "owner": _owner(conn, full or {}), "can_edit": p.can("research.request"),
                    "can_assign_others": p.role in ("sales_manager", "admin"), "me": p.user_id,
                    "team": [dict(u, id=str(u["id"])) for u in team]}

    @app.put("/api/accounts/{oid}/relationship")
    def set_relationship(oid: str, body: RelationshipIn, request: Request, p: Principal = Depends(require("research.request"))):
        with tx(request) as conn:
            o = _visible_org(conn, p, oid)
            note = (body.note or "").strip() or None
            conn.execute("UPDATE organizations SET relationship=%s, relationship_note=%s, relationship_set_by=%s, relationship_set_at=now() WHERE id=%s",
                         (body.status, note, p.email, o["id"]))
            audit(conn, actor=p.email, operation="account_relationship_set", record_type="organization", record_id=str(o["id"]), detail={"status": body.status, "note": bool(note)})
            return {"status": body.status, "note": note}

    @app.put("/api/accounts/{oid}/owner")
    def set_owner(oid: str, body: OwnerIn, request: Request, p: Principal = Depends(require("research.request"))):
        with tx(request) as conn:
            o = _visible_org(conn, p, oid)
            manager = p.role in ("sales_manager", "admin")
            current = str(o["owner_user_id"]) if o["owner_user_id"] else None
            target = _uuid(body.user_id) if body.user_id else None
            if not manager:
                if target and target != p.user_id:
                    raise HTTPException(403, "Only a sales manager or an administrator assigns an account to someone else. You can take it yourself.")
                if current and current != p.user_id:
                    raise HTTPException(403, "This account belongs to a colleague. Ask a sales manager to reassign it.")
            if target and not q1(conn, "SELECT 1 FROM users WHERE id=%s AND active AND role = ANY(%s)", (target, list(ASSIGNABLE_ROLES))):
                raise HTTPException(400, "That person cannot own accounts.")
            conn.execute("UPDATE organizations SET owner_user_id=%s, owner_assigned_by=%s, owner_assigned_at=now() WHERE id=%s", (target, p.email, o["id"]))
            audit(conn, actor=p.email, operation="account_owner_set", record_type="organization", record_id=str(o["id"]), detail={"from": current, "to": target})
            full = q1(conn, "SELECT owner_user_id, owner_assigned_by, owner_assigned_at FROM organizations WHERE id=%s", (o["id"],))
            return {"owner": _owner(conn, full or {})}

    # ------------------------------------------------------------------ tenders where the company is the buyer
    @app.get("/api/accounts/{oid}/tenders")
    def account_tenders(oid: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            o = _visible_org(conn, p, oid)
            notices = qa(conn, "SELECT id, source_id, notice_id, title, status, procedure, country, published_at, deadline_at, official_url, value_if_reported "
                               "FROM procurement_notices WHERE buyer_org_id=%s ORDER BY coalesce(deadline_at, published_at) DESC NULLS LAST LIMIT 30", (o["id"],))
            awards = qa(conn, "SELECT notice_id, title, winners, awarded_on, expected_end, end_basis, official_url FROM awarded_contracts "
                              "WHERE buyer_org_id=%s ORDER BY awarded_on DESC NULLS LAST LIMIT 20", (o["id"],))
            return {"notices": [dict(n, id=str(n["id"]), value_if_reported=float(n["value_if_reported"]) if n["value_if_reported"] is not None else None) for n in notices],
                    "awards": awards}
