"""Signal campaigns API: pick buying signals, review the audience and the copy, ask a manager to approve, then push to Lemlist (not launched).

Every campaign is in one of four stages, worked out from its status and the Lemlist activity read back every hour:
draft -> approval -> lemlist (created there, waiting for Launch) -> running (emails going out) -> done (everyone has had every email,
or has replied, bounced or opted out). Read-only; nothing here calls Lemlist."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..access import Principal, org_scope_sql
from ..audit import audit
from ..config import Settings
from ..db import jb, q1, qa
from ..outbound import signal_campaigns as sc
from ..people import ACTIVITY_STATUS, engagement
from .auth import require

FINAL = {"replied", "interested", "not_interested", "bounced", "unsubscribed"}       # Lemlist stops writing to this person
SENT = {"sent", "opened", "clicked", "replied", "interested", "not_interested", "bounced"}
REPLIED = {"replied", "interested", "not_interested"}
_CRM_WORDS = re.compile(r"\s*\bin Pipedrive\b", re.I)


class CreateIn(BaseModel):
    rule_ids: list[str] = Field(min_length=1, max_length=8)
    name: str | None = Field(default=None, max_length=120)
    days: int = Field(default=90, ge=7, le=365)
    region: str = Field(default="", pattern="^(|uk|dach|nordics|poland|europe|us|other)$")
    per_account: int = Field(default=2, ge=1, le=5)


class StepIn(BaseModel):
    day: int = Field(ge=0, le=60)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=3000)
    claims_used: list[str] = Field(default_factory=list, max_length=5)


class StepsIn(BaseModel):
    steps: list[StepIn] = Field(min_length=1, max_length=6)


class PreviewIn(BaseModel):
    rule_ids: list[str] = Field(min_length=1, max_length=8)
    days: int = Field(default=90, ge=7, le=365)
    region: str = Field(default="", pattern="^(|uk|dach|nordics|poland|europe|us|other)$")
    per_account: int = Field(default=2, ge=1, le=5)


class MemberIn(BaseModel):
    included: bool | None = None
    hook: str | None = Field(default=None, min_length=10, max_length=400)


def _status(c: dict[str, Any], approval: dict[str, Any] | None) -> str:
    if c["status"] == "pending_approval" and approval:
        return {"approved": "approved", "rejected": "rejected", "expired": "expired", "executing": "pushing", "failed": "push_failed"}.get(approval["status"], "pending_approval")
    return c["status"]


def plain_reason(text: str | None) -> str | None:
    """Exclusion reasons are shown to sellers: no CRM product names."""
    return _CRM_WORDS.sub("", text) if text else text


def _states(r: dict[str, Any]) -> set[str]:
    st = {ACTIVITY_STATUS[t] for t in (r.get("types") or []) if t in ACTIVITY_STATUS}
    return st | ({r["status"]} if r.get("status") not in (None, "pushed", "failed") else set())


def engagement_rows(conn, campaign_ids: list[str]) -> list[dict[str, Any]]:
    """One row per person sent to Lemlist: the enrolment status plus every activity Lemlist reported for that address in that campaign."""
    if not campaign_ids:
        return []
    return qa(conn, """
        SELECT oe.signal_campaign_id::text AS cid, oe.person_id::text AS person_id, oe.status,
               coalesce(array_agg(DISTINCT la.type) FILTER (WHERE la.type IS NOT NULL), '{}') AS types,
               count(DISTINCT la.id) FILTER (WHERE la.type='emailsSent') AS sends,
               min(la.happened_at) FILTER (WHERE la.type='emailsSent') AS first_sent_at,
               max(coalesce(la.happened_at, oe.last_event_at)) AS last_activity_at
        FROM outbound_enrolments oe
        LEFT JOIN lemlist_activities la ON lower(la.email)=lower(oe.email) AND la.campaign_id=oe.lemlist_campaign_id
        WHERE oe.signal_campaign_id = ANY(%s::uuid[])
        GROUP BY oe.id""", (campaign_ids,))


def summarise(rows: list[dict[str, Any]], steps: int) -> dict[str, Any]:
    """Counts of people, not of emails: sent means at least one email went out to that person."""
    out: dict[str, Any] = {"in_lemlist": 0, "sent": 0, "opened": 0, "replied": 0, "interested": 0, "bounced": 0, "unsubscribed": 0, "finished": 0,
                           "first_sent_at": None, "last_activity_at": None, "done": False}
    for r in rows:
        if r["status"] == "failed":
            continue
        st = _states(r)
        out["in_lemlist"] += 1
        out["sent"] += bool(st & SENT)
        out["opened"] += bool(st & {"opened", "clicked"})
        out["replied"] += bool(st & REPLIED)
        out["interested"] += "interested" in st
        out["bounced"] += "bounced" in st
        out["unsubscribed"] += "unsubscribed" in st
        out["finished"] += bool(st & FINAL) or int(r.get("sends") or 0) >= max(steps, 1)
        for k, pick in (("first_sent_at", min), ("last_activity_at", max)):
            if r.get(k):
                out[k] = pick(out[k], r[k]) if out[k] else r[k]
    out["done"] = out["in_lemlist"] > 0 and out["finished"] == out["in_lemlist"]
    return out


def stage(status: str, results: dict[str, Any]) -> str:
    """Draft -> approval -> lemlist -> running -> done. An approved campaign's next step is Lemlist, so it already sits there."""
    if status in ("pending_approval", "rejected", "expired"):
        return "approval"
    if status in ("approved", "pushing", "push_failed"):
        return "lemlist"
    if status == "pushed":
        return "done" if results.get("done") else "running" if results.get("sent") else "lemlist"
    return status          # draft, archived


def plan_audience(conn, p: Principal, rule_ids: list[str], *, days: int, region: str, per_account: int) -> dict[str, Any]:
    """What `build_audience` would include for these choices, without saving anything: the same companies, rules and order of people.
    Lets the new-campaign form show the list before a draft exists."""
    accounts = sc.signal_accounts(conn, p, rule_ids, days=days, region=region)
    items: list[dict[str, Any]] = []
    for a in accounts[:sc.MAX_MEMBERS]:
        org = a["organization_id"]
        reason: str | None
        if a["stopped"]:
            reason = "This account has a stop signal (for example a hiring freeze or an exclusive supplier)."
        else:
            blocker = sc.ownership_check(conn, org, p.pipedrive_user_id)
            reason = blocker["detail"] if blocker else None
        plan = sc.plan_contacts(conn, org)
        people = sorted(plan["eligible"], key=lambda x: (sc._rank(x["title"], a["rule_id"]), not x.get("verified"), x["name"]))[:per_account] if not reason else []
        if not people and not reason:
            bits = [f"{plan['manual_review']} need a manual check" if plan["manual_review"] else "",
                    f"{plan['suppressed']} opted out" if plan["suppressed"] else "", f"{plan['already_enrolled']} already in a sequence" if plan["already_enrolled"] else ""]
            reason = "No contact with an email address" + (f" ({', '.join(b for b in bits if b)})" if any(bits) else "") + ". Find decision makers on the account first."
        items.append({"organization_id": org, "company": a["display_name"], "country": a["country_code"],
                      "signal": {"rule_id": a["rule_id"], "name": a["rule_name"], "date": a["date"].isoformat() if a["date"] else None,
                                 "summary": a["summary"], "source_url": a["source_url"]},
                      "hook": sc.hook_for({"summary": a["summary"], "date": a["date"]}).replace("{{companyName}}", a["display_name"] or ""),
                      "people": [{"name": x["name"], "title": x["title"]} for x in people], "reason": plain_reason(reason)})
    items.sort(key=lambda i: (not i["people"], -(datetime.fromisoformat(i["signal"]["date"]).timestamp() if i["signal"]["date"] else 0), i["company"] or ""))
    return {"companies": len(accounts), "capped": len(accounts) > sc.MAX_MEMBERS, "reachable": sum(1 for i in items if i["people"]),
            "people": sum(len(i["people"]) for i in items), "left_out": sum(1 for i in items if not i["people"]), "items": items}


def register_campaigns(app: FastAPI, settings: Settings) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    def load(conn, cid: str, p: Principal) -> dict[str, Any]:
        c = q1(conn, "SELECT * FROM signal_campaigns WHERE id::text=%s", (cid,))
        if not c or not (c["created_by"] == p.email or p.can("approvals.read_all")):
            raise HTTPException(404, "not found")
        return c

    def editable(c: dict[str, Any], p: Principal) -> None:
        if c["created_by"] != p.email and p.role not in {"sales_manager", "admin"}:
            raise HTTPException(403, "Only the person who created the campaign or a manager can change it.")
        if c["status"] != "draft":
            raise HTTPException(409, "Only a draft can be changed. Move it back to draft first.")

    @app.get("/api/signal-campaigns/options")
    def options(request: Request, days: int = Query(90, ge=7, le=365), target: bool = False, p: Principal = Depends(require("accounts.read"))):
        with tx(request) as conn:
            return {"items": sc.signal_options(conn, p, days, target=target), "days": days,
                    "note": "Counts are companies you can see with the signal live and checked in this window. Reachable means at least one person there has an email address."}

    @app.get("/api/signal-campaigns")
    def list_campaigns(request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            rows = qa(conn, """SELECT c.id, c.name, c.rule_ids, c.status, c.filters, c.created_by, c.created_at, c.updated_at, c.pushed_at, c.lemlist_campaign_id,
                       c.review, jsonb_array_length(c.steps) AS steps_n, a.status AS approval_status, a.approver, a.requested_by, a.expires_at,
                       count(m.*) FILTER (WHERE NOT m.included AND m.person_id IS NOT NULL) AS people_left_out,
                       count(DISTINCT m.organization_id) AS accounts_with_signal,
                       count(m.*) FILTER (WHERE m.included AND m.person_id IS NOT NULL) AS people,
                       count(DISTINCT m.organization_id) FILTER (WHERE m.included AND m.person_id IS NOT NULL) AS accounts,
                       count(DISTINCT m.organization_id) FILTER (WHERE NOT m.included AND m.person_id IS NULL) AS accounts_without_contact,
                       (SELECT count(*) FROM outbound_enrolments oe WHERE oe.signal_campaign_id=c.id AND oe.status IN ('replied','interested')) AS replies
                FROM signal_campaigns c LEFT JOIN approvals a ON a.id=c.approval_id LEFT JOIN signal_campaign_members m ON m.campaign_id=c.id
                WHERE c.status <> 'archived' AND (c.created_by=%s OR %s) GROUP BY c.id, a.id ORDER BY c.updated_at DESC LIMIT 100""",
                      (p.email, p.can("approvals.read_all")))
            by_campaign: dict[str, list[dict[str, Any]]] = {}
            for e in engagement_rows(conn, [str(r["id"]) for r in rows if r["status"] == "pushed"]):
                by_campaign.setdefault(e["cid"], []).append(e)
            items = []
            for r in rows:
                cid, review = str(r["id"]), r.pop("review") or {}
                st = _status(r, {"status": r["approval_status"]} if r["approval_status"] else None)
                res = summarise(by_campaign.get(cid, []), int(r["steps_n"] or 0))
                issues = len(review.get("leads_with_issues") or []) + len((review.get("template") or {}).get("breaks") or [])
                items.append(dict(r, id=cid, status=st, stage=stage(st, res), results=res, review_passed=bool(review.get("passed")), issues=issues,
                                  can_approve=st == "pending_approval" and p.role in {"sales_manager", "admin"} and r["requested_by"] != p.email))
            return {"items": items, "outbound_enabled": settings.outbound_enabled, "lemlist_configured": bool(settings.lemlist_api_key)}

    @app.post("/api/signal-campaigns/preview")
    def preview_audience(body: PreviewIn, request: Request, p: Principal = Depends(require("draft.create"))):
        """The list a new campaign would start with. Saves nothing and calls nothing outside the database."""
        with tx(request) as conn:
            known = {r["rule_id"] for r in qa(conn, "SELECT rule_id FROM signal_rule_versions WHERE status='active' AND rule_id = ANY(%s)", (body.rule_ids,))}
            unknown = sorted(set(body.rule_ids) - known)
            if unknown:
                raise HTTPException(422, f"Not an active signal: {', '.join(unknown)}")
            return plan_audience(conn, p, body.rule_ids, days=body.days, region=body.region, per_account=body.per_account)

    @app.post("/api/signal-campaigns")
    def create(body: CreateIn, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            known = {r["rule_id"] for r in qa(conn, "SELECT rule_id FROM signal_rule_versions WHERE status='active' AND rule_id = ANY(%s)", (body.rule_ids,))}
            unknown = sorted(set(body.rule_ids) - known)
            if unknown:
                raise HTTPException(422, f"Not an active signal: {', '.join(unknown)}")
            names = {r["id"]: r["signal"] for r in qa(conn, "SELECT id, signal FROM legacy_signals WHERE id = ANY(%s)", (body.rule_ids,))}
            short = lambda label: re.split(r"\s*[(:;,]", label, maxsplit=1)[0].strip()      # noqa: E731  ("Funding round (Series B+ ...)" -> "Funding round")
            name = (body.name or "").strip() or " + ".join(short(names[r]) for r in body.rule_ids)
            filters = {"days": body.days, "region": body.region, "per_account": body.per_account}
            row = conn.execute("INSERT INTO signal_campaigns(name, rule_ids, filters, steps, created_by) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                               (name[:120], body.rule_ids, jb(filters), jb(sc.DEFAULT_STEPS), p.email)).fetchone()
            cid = str(row["id"])      # type: ignore[index]
            stats = sc.build_audience(conn, cid, p)
            sc.review_campaign(conn, cid)
            audit(conn, actor=p.email, operation="signal_campaign_created", record_type="signal_campaign", record_id=cid, detail={"rules": body.rule_ids, **stats})
            return {"id": cid, **stats}

    @app.get("/api/signal-campaigns/{cid}")
    def detail(cid: str, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            c = load(conn, cid, p)
            where, params = org_scope_sql(p, "o.id")
            members = qa(conn, f"""SELECT m.id, m.organization_id, m.person_id, m.included, m.exclusion, m.signal, m.variables, m.status,
                       o.display_name AS company, o.country_code, pe.full_name, e.title,
                       (SELECT oe.status FROM outbound_enrolments oe WHERE oe.signal_campaign_id=m.campaign_id AND oe.person_id=m.person_id LIMIT 1) AS outcome
                FROM signal_campaign_members m JOIN organizations o ON o.id=m.organization_id
                LEFT JOIN people pe ON pe.id=m.person_id
                LEFT JOIN LATERAL (SELECT title FROM employment WHERE person_id=m.person_id AND organization_id=m.organization_id LIMIT 1) e ON TRUE
                WHERE m.campaign_id=%s AND ({where}) ORDER BY m.included DESC, o.display_name, pe.full_name""", [cid, *params])      # noqa: S608
            approval = q1(conn, """SELECT id, status, requested_by, approver, approved_at, created_at, expires_at, executed_at, result,
                                          payload->>'campaign_name' AS lemlist_name, jsonb_array_length(coalesce(payload->'members', '[]'::jsonb)) AS approved_people
                                   FROM approvals WHERE id=%s""", (c["approval_id"],)) if c["approval_id"] else None
            sample = next((m for m in members if m["included"] and m["person_id"]), None)
            status = _status(c, approval)
            engaged = engagement_rows(conn, [cid])
            by_person = {e["person_id"]: e for e in engaged}
            results = summarise(engaged, len(c["steps"] or []))
            senior = p.role in {"sales_manager", "admin"}
            result = (approval or {}).get("result") or {}
            lemlist = None
            if approval and (c["status"] == "pushed" or result):
                lemlist = {"campaign_name": approval["lemlist_name"] or f"RI signals: {c['name']}", "lemlist_campaign_id": c["lemlist_campaign_id"] or result.get("lemlist_campaign_id"),
                           "pushed_at": c["pushed_at"], "leads": result.get("leads"), "skipped_existing": result.get("skipped_existing"),
                           "dropped_since_approval": result.get("dropped_since_approval"), "error": result.get("error"), "emails": len(c["steps"] or [])}
            people = [m for m in members if m["person_id"]]
            orgs_in = {m["organization_id"] for m in people if m["included"]}
            return {"campaign": {**{k: c[k] for k in ("name", "rule_ids", "filters", "steps", "review", "created_by", "created_at", "updated_at", "pushed_at", "lemlist_campaign_id")},
                                 "id": cid, "status": status, "stage": stage(status, results)},
                    "approval": {k: v for k, v in dict(approval, id=str(approval["id"])).items() if k != "result"} | {"error": result.get("error")} if approval else None,
                    "results": results, "lemlist": lemlist,
                    "summary": {"companies_with_signal": len({m["organization_id"] for m in members}), "companies_in": len(orgs_in),
                                "companies_without_people": len({m["organization_id"] for m in members if not m["person_id"]}),
                                "people_in": sum(1 for m in people if m["included"]), "people_left_out": sum(1 for m in people if not m["included"])},
                    "members": [{"id": str(m["id"]), "organization_id": str(m["organization_id"]), "person_id": str(m["person_id"]) if m["person_id"] else None,
                                 "included": m["included"], "exclusion": plain_reason(m["exclusion"]), "signal": m["signal"], "company": m["company"], "country": m["country_code"],
                                 "name": m["full_name"], "title": m["title"], "hook": (m["variables"] or {}).get("signalHook"), "status": m["outcome"] or m["status"],
                                 "engagement": engagement(_states(by_person[str(m["person_id"])]) | {"pushed"}) if str(m["person_id"]) in by_person else None,
                                 "emails_sent": int(by_person[str(m["person_id"])]["sends"] or 0) if str(m["person_id"]) in by_person else 0} for m in members],
                    "preview": sc.preview(c["steps"], sample["variables"]) if sample else sc.preview(c["steps"], {}),
                    "placeholders": sorted(sc.CAMPAIGN_PLACEHOLDERS), "outbound_enabled": settings.outbound_enabled, "lemlist_configured": bool(settings.lemlist_api_key),
                    "can_edit": c["status"] == "draft" and (c["created_by"] == p.email or senior),
                    "can_change": c["created_by"] == p.email or senior,          # move back to draft, archive
                    "can_approve": bool(approval) and status == "pending_approval" and senior and (approval or {}).get("requested_by") != p.email,
                    "can_send": bool(approval) and status == "approved" and p.can("approvals.decide")
                                and (senior or p.email in {(approval or {}).get("requested_by"), (approval or {}).get("approver")})}

    @app.put("/api/signal-campaigns/{cid}/steps")
    def set_steps(cid: str, body: StepsIn, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            c = load(conn, cid, p)
            editable(c, p)
            steps = [{**s.model_dump(), "fact_refs": ["signal"]} for s in sorted(body.steps, key=lambda s: s.day)]
            conn.execute("UPDATE signal_campaigns SET steps=%s, updated_at=now() WHERE id=%s", (jb(steps), cid))
            review = sc.review_campaign(conn, cid)
            audit(conn, actor=p.email, operation="signal_campaign_copy_changed", record_type="signal_campaign", record_id=cid, detail={"passed": review["passed"]})
            return {"review": review}

    @app.post("/api/signal-campaigns/{cid}/rebuild")
    def rebuild(cid: str, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            c = load(conn, cid, p)
            editable(c, p)
            stats = sc.build_audience(conn, cid, p)
            return {**stats, "review": sc.review_campaign(conn, cid)}

    @app.patch("/api/signal-campaigns/{cid}/members/{mid}")
    def member(cid: str, mid: str, body: MemberIn, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            c = load(conn, cid, p)
            editable(c, p)
            m = q1(conn, "SELECT id, person_id, variables FROM signal_campaign_members WHERE id::text=%s AND campaign_id=%s", (mid, cid))
            if not m or not m["person_id"]:
                raise HTTPException(404, "not found")
            if body.included is not None:
                conn.execute("UPDATE signal_campaign_members SET included=%s WHERE id=%s", (body.included, m["id"]))
            if body.hook:
                conn.execute("UPDATE signal_campaign_members SET variables = variables || %s::jsonb WHERE id=%s", (jb({"signalHook": body.hook.strip()}), m["id"]))
            return {"review": sc.review_campaign(conn, cid)}

    @app.post("/api/signal-campaigns/{cid}/request-approval")
    def request_approval(cid: str, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            c = load(conn, cid, p)
            editable(c, p)
            try:
                return sc.request_push(conn, cid, p, settings)
            except sc.CampaignError as exc:
                raise HTTPException(409, str(exc)) from exc

    @app.post("/api/signal-campaigns/{cid}/withdraw")
    def withdraw(cid: str, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            c = load(conn, cid, p)
            if c["created_by"] != p.email and p.role not in {"sales_manager", "admin"}:
                raise HTTPException(403, "Only the person who created the campaign or a manager can change it.")
            try:
                sc.withdraw(conn, cid, p.email)
            except sc.CampaignError as exc:
                raise HTTPException(409, str(exc)) from exc
            return {"status": "draft"}

    @app.post("/api/signal-campaigns/{cid}/archive")
    def archive(cid: str, request: Request, p: Principal = Depends(require("draft.create"))):
        with tx(request) as conn:
            c = load(conn, cid, p)
            if c["created_by"] != p.email and p.role not in {"sales_manager", "admin"}:
                raise HTTPException(403, "Only the person who created the campaign or a manager can change it.")
            if c["status"] == "pending_approval":
                raise HTTPException(409, "Withdraw the approval request first.")
            conn.execute("UPDATE signal_campaigns SET status='archived', updated_at=now() WHERE id=%s", (cid,))
            return {"status": "archived"}
