"""The shared timeline of conversations with a person and with a company: emails (Gmail), campaign events (Lemlist) and meetings (Bluedot).

Everybody who may see the person or the company sees THAT a contact happened: who from our side, which day, which direction. What was said
(an email's subject and snippet) is shown only when the row is shared with the team ('team'); an owner-only row ('owner') shows its title and gist to
its owner and to nobody else, administrators included: the text is withheld in the SQL itself, so it never reaches the API process for anyone else.
Row scope and permissions are those of the person and company pages. Erased and suppressed people return nothing."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from ..access import Principal
from ..audit import audit
from ..db import q1, qa
from .auth import require
from .profiles import _visible_org, _visible_person

MAX_ITEMS = 100
EMPTY_SUMMARY = {"last_contact_at": None, "last_contact_by": None, "emails": 0, "meetings": 0, "campaign_events": 0}

# A meeting is stored once for every attendee we know (external_id '<meeting>:<person>'): on a company page they are one meeting.
MEETING_KEY = "regexp_replace(ci.external_id, ':[^:]*$', '')"


def _timeline(conn, p: Principal, where: str, params: list[Any], *, collapse_meetings: bool) -> dict[str, Any]:
    """The summary and the newest rows for one scope. `where` filters contact_interactions (alias ci) joined to the people (alias pe) it names."""
    base = ("FROM contact_interactions ci JOIN users u ON u.id=ci.user_id LEFT JOIN people pe ON pe.id=ci.person_id "
            f"WHERE ({where}) AND (pe.id IS NULL OR (pe.erased_at IS NULL AND NOT pe.suppressed))")
    counts = q1(conn, "SELECT count(*) FILTER (WHERE ci.channel='email') AS emails, "
                      f"count(DISTINCT (ci.user_id, {MEETING_KEY})) FILTER (WHERE ci.channel='meeting') AS meetings, "
                      f"count(*) FILTER (WHERE ci.channel='lemlist') AS campaign_events {base}", params) or {}           # noqa: S608
    # Rows are read newest first with their text already withheld for anybody but the owner of an owner-only row.
    rows = qa(conn, f"""SELECT ci.id, ci.channel, ci.occurred_at, ci.direction, ci.user_id, ci.external_id, u.display_name AS owner_name, u.email AS owner_email,
                       (ci.visibility = 'owner' AND ci.user_id <> %s) AS detail_hidden,
                       CASE WHEN ci.visibility = 'team' OR ci.user_id = %s THEN ci.title END AS title,
                       CASE WHEN ci.visibility = 'team' OR ci.user_id = %s THEN ci.gist END AS gist,
                       pe.id AS person_id, pe.full_name AS person_name
                       {base} ORDER BY ci.occurred_at DESC, ci.id LIMIT %s""",                                            # noqa: S608
              [p.user_id, p.user_id, p.user_id, *params, MAX_ITEMS * (3 if collapse_meetings else 1)])
    items: list[dict[str, Any]] = []
    meetings: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        person = {"id": str(r["person_id"]), "name": r["person_name"]} if r["person_id"] else None
        if collapse_meetings and r["channel"] == "meeting":
            key = (str(r["user_id"]), str(r["external_id"]).rsplit(":", 1)[0])
            if key in meetings:
                if person and all(x["id"] != person["id"] for x in meetings[key]["people"]):
                    meetings[key]["people"].append(person)
                continue
        item = {"id": str(r["id"]), "channel": r["channel"], "occurred_at": r["occurred_at"].isoformat(), "direction": r["direction"], "title": r["title"], "gist": r["gist"],
                "owner": {"name": r["owner_name"], "email": r["owner_email"]}, "mine": str(r["user_id"]) == p.user_id, "detail_hidden": bool(r["detail_hidden"]), "person": person}
        if collapse_meetings and r["channel"] == "meeting":
            item["people"] = [person] if person else []
            meetings[(str(r["user_id"]), str(r["external_id"]).rsplit(":", 1)[0])] = item
        items.append(item)
    items = items[:MAX_ITEMS]
    summary = dict(EMPTY_SUMMARY, emails=int(counts.get("emails") or 0), meetings=int(counts.get("meetings") or 0), campaign_events=int(counts.get("campaign_events") or 0))
    if items:
        summary["last_contact_at"] = items[0]["occurred_at"]
        summary["last_contact_by"] = items[0]["owner"]
    return {"summary": summary, "items": items}


def register_conversations(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    @app.get("/api/people/{pid}/conversations")
    def person_conversations(pid: str, request: Request, p: Principal = Depends(require("contacts.read"))):
        with tx(request) as conn:
            v = _visible_person(conn, p, pid)                 # same row scope as the person page; an erased person is "not found"
            pid = str(v["id"])
            out = _timeline(conn, p, "ci.person_id = %s", [pid], collapse_meetings=False)
            audit(conn, actor=p.email, operation="pii_read", record_type="person", record_id=pid, detail={"view": "conversations", "count": len(out["items"])})
            return out

    @app.get("/api/accounts/{oid}/conversations")
    def account_conversations(oid: str, request: Request, p: Principal = Depends(require("accounts.read"))):
        if not p.can("contacts.read"):                        # the timeline names people: it needs the same permission as the people list
            raise HTTPException(403, "missing permission contacts.read")
        with tx(request) as conn:
            o = _visible_org(conn, p, oid)
            oid = str(o["id"])
            # Rows matched to the company itself, and rows of people who work there and have not been matched to another company.
            staff = [str(r["person_id"]) for r in qa(conn, "SELECT DISTINCT e.person_id FROM employment e JOIN people pe ON pe.id=e.person_id "
                                                           "WHERE e.organization_id=%s AND pe.erased_at IS NULL AND NOT pe.suppressed", (oid,))]
            out = _timeline(conn, p, "ci.organization_id = %s OR (ci.organization_id IS NULL AND ci.person_id = ANY(%s::uuid[]))", [oid, staff], collapse_meetings=True)
            audit(conn, actor=p.email, operation="pii_read", record_type="organization_conversations", record_id=oid, detail={"count": len(out["items"])})
            return out
