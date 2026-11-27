"""Every Lemlist email with a person (our campaign emails, their replies, our answers sent from this app) as one row on the person's
Conversations timeline (`contact_interactions`, channel 'lemlist', visibility 'team'), so the company page shows the history too.

Written by the hourly Lemlist sync, when someone opens a person's emails, and after an answer is sent. Idempotent per Lemlist activity (or
per answer): `lemlist_activities.timeline_at` / `lemlist_replies.timeline_at` say an email is on the timeline, and are cleared when its
words change, so the row is refreshed, never added twice.

Round 8's personal Lemlist sync (ri.connections.lemlist_sync) writes campaign events for whoever connected their own key, keyed by
campaign + lead + event + time. The same email is never on the timeline twice: before writing, a row for the same person, campaign, event
and second (or one already naming this activity) is looked for under ANY owner; when round 8 wrote it, that row is completed with the words
and marked with the activity id, and round 8 leaves marked rows alone from then on.

Whose row it is (`user_id`, shown as "by" on the timeline): the colleague whose mailbox sent the email (same address, else the same name as
Lemlist's sender), else the owner of the person's company, else the first administrator (who set up the shared Lemlist key). Nobody who is
erased, suppressed, unsubscribed or bounced gets a row (round 8's rule); their emails stay on their own page."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..db import Conn, jb, q1, qa
from .signature import full_text

BATCH = 300
GIST = 600


def _same_text(a: str | None, b: str | None) -> bool:
    from .replies import _same
    return _same(a, b)


class _Owners:
    """Resolves (and remembers, for one run) which colleague a row belongs to."""

    def __init__(self, conn: Conn):
        self.conn = conn
        self.cache: dict[tuple[str, str], str | None] = {}

    def _one(self, sql: str, params: tuple[Any, ...]) -> str | None:
        rows = qa(self.conn, sql, params)
        return str(rows[0]["id"]) if len(rows) == 1 else None

    def by_sender(self, email: str | None, name: str | None) -> str | None:
        key = ((email or "").lower(), (name or "").lower())
        if key not in self.cache:
            uid = self._one("SELECT id FROM users WHERE lower(email)=%s LIMIT 2", (key[0],)) if key[0] else None
            if not uid and key[1].strip():
                uid = self._one("SELECT id FROM users WHERE lower(regexp_replace(display_name, '\\s+', ' ', 'g')) = lower(regexp_replace(%s, '\\s+', ' ', 'g')) "
                                "ORDER BY active DESC LIMIT 2", (name,))
            self.cache[key] = uid
        return self.cache[key]

    def fallback(self, org_id: str | None) -> str | None:
        key = ("org", org_id or "")
        if key not in self.cache:
            r = q1(self.conn, "SELECT owner_user_id FROM organizations WHERE id=%s AND owner_user_id IS NOT NULL", (org_id,)) if org_id else None
            if not r:
                r = q1(self.conn, "SELECT id AS owner_user_id FROM users WHERE role='admin' AND active ORDER BY created_at LIMIT 1")
            self.cache[key] = str(r["owner_user_id"]) if r else None
        return self.cache[key]


def _org(conn: Conn, person_id: str) -> str | None:
    r = q1(conn, "SELECT organization_id FROM employment WHERE person_id=%s ORDER BY (valid_to IS NULL) DESC, observed_at DESC NULLS LAST LIMIT 1", (person_id,))
    return str(r["organization_id"]) if r else None


def _existing(conn: Conn, person_id: str, kind: str, campaign_id: str | None, at: datetime, activity_id: str) -> dict[str, Any] | None:
    """A timeline row for this email under any owner: one that names the activity, else round 8's row for the same event and second."""
    return q1(conn, """SELECT id, meta FROM contact_interactions WHERE channel='lemlist' AND person_id=%s
                       AND (meta->>'activity_id' = %s
                            OR (meta->>'type' = %s AND coalesce(meta->>'campaign_id', '') = %s
                                AND occurred_at >= date_trunc('second', %s::timestamptz) AND occurred_at < date_trunc('second', %s::timestamptz) + interval '1 second'))
                       ORDER BY (meta ? 'activity_id') DESC, created_at LIMIT 1""",
              (person_id, activity_id, kind, campaign_id or "", at, at))


def _write(conn: Conn, *, owner: str | None, person_id: str, org_id: str | None, external_id: str, at: datetime, direction: str, title: str | None,
           gist: str | None, meta: dict[str, Any], kind: str, campaign_id: str | None, activity_id: str | None) -> str:
    """'new', 'updated' or 'no_owner'."""
    title, gist = (title or "")[:300] or None, (gist or "")[:GIST] or None
    found = _existing(conn, person_id, kind, campaign_id, at, activity_id) if activity_id else \
        q1(conn, "SELECT id, meta FROM contact_interactions WHERE channel='lemlist' AND external_id=%s LIMIT 1", (external_id,))
    if found:
        conn.execute("UPDATE contact_interactions SET title=coalesce(%s, title), gist=coalesce(%s, gist), meta = meta || %s, visibility='team', "
                     "organization_id=coalesce(organization_id, %s), direction=coalesce(direction, %s) WHERE id=%s",
                     (title, gist, jb(meta), org_id, direction, found["id"]))
        return "updated"
    if not owner:
        return "no_owner"
    row = conn.execute(
        "INSERT INTO contact_interactions(user_id, channel, external_id, person_id, organization_id, direction, occurred_at, title, gist, visibility, meta) "
        "VALUES (%s,'lemlist',%s,%s,%s,%s,%s,%s,%s,'team',%s) ON CONFLICT (user_id, channel, external_id) DO UPDATE SET "
        "title=EXCLUDED.title, gist=EXCLUDED.gist, meta=contact_interactions.meta || EXCLUDED.meta RETURNING (xmax = 0) AS inserted",
        (owner, external_id, person_id, org_id, direction, at, title, gist, jb(meta))).fetchone()
    return "new" if row and row["inserted"] else "updated"


_ELIGIBLE = """pe.erased_at IS NULL AND NOT pe.suppressed AND NOT EXISTS (
                 SELECT 1 FROM outbound_enrolments en WHERE lower(trim(en.email)) = lower(pe.work_email) AND en.status IN ('unsubscribed','bounced'))"""


def record_timeline(conn: Conn, *, person_ids: list[str] | None = None, limit: int = BATCH) -> dict[str, int]:
    """Write the emails not yet on the timeline (or whose words changed), of everyone or of these people. Commits once at the end; safe to
    repeat, also by two requests at once (the same owner and key: the unique key keeps one row)."""
    scope, params = ("AND la.person_id = ANY(%s::uuid[])", [person_ids]) if person_ids is not None else ("", [])
    acts = qa(conn, f"""SELECT la.id, la.type, la.person_id, la.campaign_id, la.campaign_name, la.happened_at, la.subject, la.body_text, la.body_source,
                               la.send_user_email, la.send_user_name, la.sequence_step
                        FROM lemlist_activities la JOIN people pe ON pe.id = la.person_id
                        WHERE la.type IN ('emailsSent','emailsReplied') AND la.timeline_at IS NULL AND la.happened_at IS NOT NULL AND {_ELIGIBLE} {scope}
                        ORDER BY la.happened_at DESC LIMIT %s""", [*params, limit])                                       # noqa: S608
    answers = qa(conn, f"""SELECT r.id, r.person_id, r.body_text, r.signature_text, r.created_by, coalesce(r.sent_at, r.created_at) AS at, la.subject, la.campaign_id, la.campaign_name
                           FROM lemlist_replies r JOIN people pe ON pe.id = r.person_id LEFT JOIN lemlist_activities la ON la.id = r.reply_to_activity_id
                           WHERE r.status = 'sent' AND r.timeline_at IS NULL AND {_ELIGIBLE} {scope.replace('la.person_id', 'r.person_id')}
                           ORDER BY r.created_at DESC LIMIT %s""", [*params, limit])                                    # noqa: S608
    stats = {"new": 0, "updated": 0, "no_owner": 0, "skipped": 0}
    owners = _Owners(conn)
    orgs: dict[str, str | None] = {}
    senders: dict[tuple[str, str | None], dict[str, Any] | None] = {}
    # our answers from here, as typed and as sent (with the signature): Lemlist sends them back as campaign-less emails
    mine: dict[str, list[str]] = {}
    for r in answers:
        mine.setdefault(str(r["person_id"]), []).extend([r["body_text"], full_text(r["body_text"], r["signature_text"])])
    for r in qa(conn, "SELECT person_id, body_text, signature_text FROM lemlist_replies WHERE status='sent' AND person_id = ANY(%s::uuid[])",
                (list({str(a["person_id"]) for a in acts}),)) if acts else []:
        mine.setdefault(str(r["person_id"]), []).extend([r["body_text"], full_text(r["body_text"], r["signature_text"])])
    for a in acts:
        pid, aid = str(a["person_id"]), str(a["id"])
        if a["type"] == "emailsSent" and any(_same_text(a["body_text"], t) for t in mine.get(pid, [])):
            conn.execute("UPDATE lemlist_activities SET timeline_at=now() WHERE id=%s", (aid,))      # our app answer read back: its own row says it
            stats["skipped"] += 1
            continue
        if pid not in orgs:
            orgs[pid] = _org(conn, pid)
        email, name = a["send_user_email"], a["send_user_name"]
        if a["type"] == "emailsReplied" and not email:              # their reply: the colleague whose campaign email they answered
            key = (pid, a["campaign_id"])
            if key not in senders:
                senders[key] = q1(conn, "SELECT send_user_email, send_user_name FROM lemlist_activities WHERE person_id=%s AND type='emailsSent' "
                                        "AND campaign_id IS NOT DISTINCT FROM %s AND send_user_email IS NOT NULL ORDER BY happened_at DESC LIMIT 1",
                                  (pid, a["campaign_id"]))
            s = senders[key] or {}
            email, name = s.get("send_user_email"), s.get("send_user_name")
        owner = owners.by_sender(email, name) or owners.fallback(orgs[pid])
        title = a["subject"] or a["campaign_name"]
        gist = a["body_text"] or (a["campaign_name"] if a["subject"] and a["campaign_name"] else None)
        step = a["sequence_step"] + 1 if isinstance(a["sequence_step"], int) else None
        meta = {"campaign_id": a["campaign_id"], "type": a["type"], "activity_id": aid, "source": "lemlist_shared", "step": step,
                "sender_email": email, "sender_name": name}
        res = _write(conn, owner=owner, person_id=pid, org_id=orgs[pid], external_id=f"lemlist:{aid}", at=a["happened_at"],
                     direction="out" if a["type"] == "emailsSent" else "in", title=title, gist=gist, meta={k: v for k, v in meta.items() if v is not None},
                     kind=a["type"], campaign_id=a["campaign_id"], activity_id=aid)
        stats[res] += 1
        if res != "no_owner":
            conn.execute("UPDATE lemlist_activities SET timeline_at=now() WHERE id=%s", (aid,))
    for r in answers:
        pid = str(r["person_id"])
        if pid not in orgs:
            orgs[pid] = _org(conn, pid)
        owner = owners.by_sender(r["created_by"], None) or owners.fallback(orgs[pid])
        meta = {"campaign_id": r["campaign_id"], "type": "answer", "answer_id": str(r["id"]), "source": "lemlist_app"}
        res = _write(conn, owner=owner, person_id=pid, org_id=orgs[pid], external_id=f"lemlist-answer:{r['id']}", at=r["at"], direction="out",
                     title=r["subject"] or r["campaign_name"], gist=r["body_text"], meta={k: v for k, v in meta.items() if v is not None},
                     kind="answer", campaign_id=r["campaign_id"], activity_id=None)
        stats[res] += 1
        if res != "no_owner":
            conn.execute("UPDATE lemlist_replies SET timeline_at=now() WHERE id=%s", (r["id"],))
    conn.commit()
    return stats
