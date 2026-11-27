"""Per-user Lemlist sync: one person's own API key, their campaigns and the activity of leads we know.

The shared, server-wide Lemlist key and its sync (`ri.people.sync_lemlist`) are untouched; this reads the same API with the key a person connected
themselves and writes only `contact_interactions` rows (channel 'lemlist', visibility 'team'): sent, opened, clicked, replied, bounced and so on, for
leads whose address matches a person in our database. Nobody who is erased, suppressed, unsubscribed or bounced gets a row.

Incremental: for every campaign the cursor remembers the newest event seen; the next run reads newest-first and stops at the first page that is
older than that (with a two hour overlap). A first run reads at most MAX_PAGES pages of PAGE events per campaign, so very old history is not pulled.
Each page is committed, every row is upserted under a stable key (campaign + lead + event + time), so a repeated or interrupted run never duplicates."""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .. import vault
from ..db import Conn, qa
from ..jobs.queue import JobError
from ..outbound.lemlist import LemlistClient, LemlistError
from . import store

HORIZON_DAYS = 540          # how far back a first read goes (about 18 months); older events are simply not fetched. Nothing stored is ever deleted.

PAGE = 100
MAX_PAGES = 20                  # per campaign and run (2,000 newest events)
MAX_CAMPAIGNS = 200
TIME_BUDGET_S = 240.0           # a job's lease is five minutes: stop cleanly before it ends, the next hourly run carries on
OVERLAP = timedelta(hours=2)

MSG_REJECTED = "Lemlist did not accept your API key. Open Connections and connect Lemlist again."
MSG_UNREACHABLE = "Lemlist could not be reached just now. It will be tried again at the next hourly sync."
MSG_VAULT = "The saved Lemlist key could not be opened on the server. Open Connections and connect Lemlist again."

# What Lemlist calls an activity -> the plain event we show, and who did it (out = we sent, in = they did something, None = neither)
EVENTS: dict[str, tuple[str, str | None]] = {
    "emailsSent": ("Sent", "out"), "emailsOpened": ("Opened", None), "emailsClicked": ("Clicked", None), "emailsReplied": ("Replied", "in"),
    "emailsBounced": ("Bounced", None), "emailsUnsubscribed": ("Unsubscribed", None), "emailsInterested": ("Marked interested", None),
    "interested": ("Marked interested", None), "emailsNotInterested": ("Marked not interested", None), "notInterested": ("Marked not interested", None),
    "emailsSendFailed": ("Could not be sent", None), "linkedinSent": ("LinkedIn message sent", "out"), "linkedinReplied": ("LinkedIn reply", "in"),
    "linkedinInviteDone": ("LinkedIn invitation sent", "out"), "linkedinInviteAccepted": ("LinkedIn invitation accepted", "in"),
}


def make_client(api_key: str) -> Any:
    """The Lemlist client for one person's key (tests replace this with a fake)."""
    return LemlistClient(api_key)


def list_campaigns(client: Any) -> list[dict[str, Any]]:
    """Every campaign the key can see. Lemlist answers with a list or, in its newer form, with {"campaigns": [...]}."""
    req = getattr(client, "_req", None)
    raw = req("GET", "/campaigns") if callable(req) else client.campaigns()
    if isinstance(raw, dict):
        raw = raw.get("campaigns") or raw.get("data") or []
    return [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []


def _when(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        dt = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def event_key(campaign_id: str, lead: str, kind: str, when: datetime) -> str:
    return f"{campaign_id}:{lead}:{kind}:{when:%Y%m%dT%H%M%S}"


def _lead_ref(a: dict[str, Any], email: str) -> str:
    return str(a.get("leadId") or a.get("_leadId") or "") or hashlib.sha1(email.encode()).hexdigest()[:12]          # noqa: S324 - a short stable label, not a secret


def _people(conn: Conn, emails: list[str]) -> dict[str, dict[str, Any]]:
    """Our people for these addresses (case-insensitive), minus anyone erased, suppressed, unsubscribed or bounced, with their current company."""
    rows = qa(conn, "SELECT DISTINCT ON (lower(work_email)) id, lower(work_email) AS email, suppressed FROM people "
                    "WHERE lower(work_email) = ANY(%s) AND erased_at IS NULL ORDER BY lower(work_email), created_at", (emails,))
    blocked = {r["e"] for r in qa(conn, "SELECT lower(trim(email)) AS e FROM outbound_enrolments WHERE status IN ('unsubscribed','bounced') AND lower(trim(email)) = ANY(%s)", (emails,))}
    people = {r["email"]: {"id": str(r["id"])} for r in rows if not r["suppressed"] and r["email"] not in blocked}
    if people:
        orgs = {str(r["person_id"]): str(r["organization_id"]) for r in qa(conn,
                "SELECT DISTINCT ON (person_id) person_id, organization_id FROM employment WHERE person_id = ANY(%s::uuid[]) "
                "ORDER BY person_id, (valid_to IS NULL) DESC, observed_at DESC NULLS LAST", ([p["id"] for p in people.values()],))}
        for p in people.values():
            p["org"] = orgs.get(p["id"])
    return people


def _shared(conn: Conn, people: list[str], times: list[datetime]) -> set[tuple[str, str, str, datetime]]:
    """Emails the shared Lemlist sync has already put on these people's timelines (ri.outbound.email_timeline), as (person, event, campaign,
    second): the same email is never added twice, whoever's key reads it."""
    if not people or not times:
        return set()
    rows = qa(conn, "SELECT person_id, meta->>'type' AS kind, coalesce(meta->>'campaign_id', '') AS cid, date_trunc('second', occurred_at) AS at "
                    "FROM contact_interactions WHERE channel='lemlist' AND meta ? 'activity_id' AND person_id = ANY(%s::uuid[]) "
                    "AND occurred_at >= %s AND occurred_at < %s", (people, min(times) - timedelta(seconds=1), max(times) + timedelta(seconds=1)))
    return {(str(r["person_id"]), r["kind"], r["cid"], r["at"]) for r in rows}


def _campaign(conn: Conn, client: Any, user_id: str, camp: dict[str, Any], since: datetime | None, horizon: datetime, stats: dict[str, int]) -> datetime | None:
    """One campaign's activity, newest first. Returns the newest event time seen (the new cursor)."""
    cid = str(camp.get("_id") or camp.get("id") or "")
    if not cid:
        return None
    name = str(camp.get("name") or "").strip() or "Lemlist campaign"
    newest: datetime | None = None
    for page in range(MAX_PAGES):
        batch = client.activities(offset=page * PAGE, limit=PAGE, campaign_id=cid)
        if not batch:
            break
        reached_old, events = False, []
        for a in batch:
            if not isinstance(a, dict):
                continue
            when = _when(a.get("createdAt") or a.get("date"))
            email = str(a.get("leadEmail") or a.get("email") or "").strip().lower()
            if when is None or "@" not in email:
                continue
            newest = when if newest is None or when > newest else newest
            if when < horizon or (since is not None and when < since - OVERLAP):
                reached_old = True
                continue
            events.append((a, when, email))
        stats["events_seen"] += len(events)
        known = _people(conn, sorted({e for _, _, e in events})) if events else {}
        shared = _shared(conn, [p["id"] for p in known.values()], [w for _, w, _ in events])
        for a, when, email in events:
            kind = str(a.get("type") or "")
            if kind not in EVENTS:
                continue
            person = known.get(email)
            if not person:
                stats["not_ours"] += 1
                continue
            if (person["id"], kind, cid, when.replace(microsecond=0)) in shared:
                stats["known"] += 1                                # already on the timeline from the shared Lemlist sync, with its words
                continue
            text, direction = EVENTS[kind]
            new = store.upsert_interaction(conn, user_id=user_id, channel="lemlist", external_id=event_key(cid, _lead_ref(a, email), kind, when), occurred_at=when,
                                           visibility="team", person_id=person["id"], organization_id=person.get("org"), direction=direction,
                                           title=str(a.get("campaignName") or "").strip() or name, gist=text, meta={"campaign_id": cid, "type": kind})
            stats["new" if new else "known"] += 1
        conn.commit()                                              # each page stands on its own: an interrupted run loses nothing
        if reached_old or len(batch) < PAGE:
            break
    return newest


def _fail(conn: Conn, user_id: str, message: str, cursor: dict[str, Any] | None = None) -> None:
    store.record_sync(conn, user_id, "lemlist", datetime.now(timezone.utc), cursor=cursor, error=message)
    conn.commit()


def run(conn: Conn, job: dict[str, Any], *, settings) -> dict[str, Any]:
    """Sync one person's Lemlist campaigns. Never raises for a problem the person must fix (their key was refused): it marks the connection
    'needs attention' with a plain message. A temporary problem (Lemlist or the network) is retried by the queue, and shown after the last attempt."""
    user_id = str((job.get("payload") or {}).get("user_id") or "")
    row = store.get(conn, user_id, "lemlist") if user_id else None
    if not row or row["status"] == "disconnected" or not row.get("secret_enc"):
        return {"skipped": "not connected"}
    try:
        api_key = store.secret(conn, getattr(settings, "vault_key", None), user_id, "lemlist")
    except vault.VaultError:
        _fail(conn, user_id, MSG_VAULT)
        return {"error": "the saved key could not be opened"}
    if not api_key:
        return {"skipped": "no key"}

    now = datetime.now(timezone.utc)
    horizon = now - timedelta(days=HORIZON_DAYS)
    cursors: dict[str, str] = dict((row.get("cursor") or {}).get("campaigns") or {})
    stats = {"campaigns": 0, "events_seen": 0, "new": 0, "known": 0, "not_ours": 0}
    deadline = time.monotonic() + TIME_BUDGET_S
    try:
        client = make_client(api_key)
        campaigns = list_campaigns(client)[:MAX_CAMPAIGNS]
        for camp in campaigns:
            if time.monotonic() > deadline:
                stats["out_of_time"] = 1                              # the remaining campaigns keep their cursors and are read next hour
                break
            cid = str(camp.get("_id") or camp.get("id") or "")
            newest = _campaign(conn, client, user_id, camp, _when(cursors.get(cid)), horizon, stats)
            stats["campaigns"] += 1
            if cid and newest is not None:
                prior = _when(cursors.get(cid))
                cursors[cid] = max(newest, prior).isoformat() if prior else newest.isoformat()
    except LemlistError as exc:
        conn.rollback()
        if exc.status in (401, 403):
            _fail(conn, user_id, MSG_REJECTED)
            return {"error": "key refused", **stats}
        return _temporary(conn, job, user_id, f"Lemlist answered with an error ({exc.status or 'unknown'})", cursors)
    except httpx.HTTPError as exc:
        conn.rollback()
        return _temporary(conn, job, user_id, f"Lemlist could not be reached ({type(exc).__name__})", cursors)
    store.record_sync(conn, user_id, "lemlist", now, cursor={"campaigns": cursors})
    conn.commit()
    return stats


def _temporary(conn: Conn, job: dict[str, Any], user_id: str, why: str, cursors: dict[str, str]) -> dict[str, Any]:
    if int(job.get("attempts") or 0) < int(job.get("max_attempts") or 1):
        raise JobError(why, "transient_network")                         # the queue tries again with a pause
    _fail(conn, user_id, MSG_UNREACHABLE, {"campaigns": cursors})        # last attempt: the person sees that the sync is struggling
    return {"error": "temporary problem", "detail": why}
