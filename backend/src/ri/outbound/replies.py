"""Replies to our Lemlist campaigns: what people wrote back, the thread with them, and answering in that same thread.

Where the words come from (Lemlist's documented API, developer.lemlist.com):
  * the activity record itself: an `emailsReplied` activity carries `text` (plain), `html` and `messagePreview` in the webhook payload, which
    "mirrors the underlying activity record", and the same record is read from GET /activities (version v2, type=emailsReplied);
  * the inbox thread: GET /inbox/{contactId} returns every email with the contact (our campaign emails and their replies) with `message` (HTML,
    the quoted history included), `subject`, `sequenceStep` (counted from 0) and the sender ids and name.
The hourly Lemlist sync stores what the feed carries and then asks for the replies still without words (`fill_reply_bodies`); opening a person's
thread asks again for that person (`refresh_person`). Each email keeps its own words only (see email_text: the quoted history is cut off), and
the email as Lemlist gave it is kept beside them so the words can be cleaned again (`reclean`, run by the hourly sync: idempotent, never deletes).

The person's thread (`thread`) is read from our database only: every email on its own, oldest first, with its sender, step, campaign and the
opens and clicks that followed it, so the history stays on the person's page even if Lemlist changes or the campaign is deleted.

Answering (POST /inbox/email) needs the sender user, the sender mailbox and its address (the mailbox that sent the campaign) and the contact or lead;
`replyToActivityId` threads it. Those ids are taken only from what Lemlist told us about this conversation, never guessed. A reply goes out only
when the outbound switch (RI_OUTBOUND_ENABLED) is on, only to someone who wrote to us, never to an erased or suppressed person, once per draft
(the browser's key), and every attempt is audited with its length only, never its words."""
from __future__ import annotations

import hashlib
import html as htmllib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..audit import audit
from ..db import Conn, q1, qa
from .email_text import (  # noqa: F401
    CLEAN_VERSION,
    MAX_BODY,
    MAX_RAW,
    Body,
    clean,
    extract,
    extract_body,
    html_to_text,
    looks_html,
    strip_history,
    tidy,
)
from . import signature as sig
from .lemlist import LemlistError

log = logging.getLogger(__name__)

MAX_REPLY = 5000                # characters a reply written here may have
FEED_PAGES = 3                  # pages of 100 replies read from the feed per hourly run
THREADS_PER_RUN = 25            # inbox threads read per hourly run
LEAD_LOOKUPS_PER_RUN = 10       # replies whose Lemlist contact is looked up by address per run
MAX_ATTEMPTS = 6                # after this many hourly tries without words, the reply says plainly that the text is not available
RECHECK = timedelta(hours=6)    # how long the hourly sync waits before asking Lemlist again about the same reply
REFRESH_EVERY = timedelta(minutes=5)   # opening the same thread again within this time does not ask Lemlist again
DUPLICATE_WINDOW = timedelta(minutes=10)
THREAD_LIMIT = 60
RECLEAN_BATCH = 500             # emails cleaned again per run (the words only: local, no Lemlist call)
REFETCH_PER_RUN = 25            # inbox threads read again per run to get the email as Lemlist gave it, once per conversation
REPLIED, SENT = "emailsReplied", "emailsSent"
ENGAGEMENT = ("emailsOpened", "emailsClicked", "emailsBounced")

_ID = re.compile(r"^[A-Za-z0-9_-]{3,80}$")
LEMLIST_HOME = "https://app.lemlist.com/"


def _id(v: Any) -> str | None:
    s = str(v).strip() if isinstance(v, (str, int)) else ""
    return s if _ID.match(s) else None


def _email(v: Any) -> str | None:
    s = str(v).strip() if isinstance(v, str) else ""
    return s[:320] if "@" in s and " " not in s else None


def _short(v: Any, n: int = 300) -> str | None:
    s = re.sub(r"\s+", " ", str(v)).strip() if isinstance(v, str) else ""
    return s[:n] or None


def _step(v: Any) -> int | None:
    """Lemlist's `sequenceStep` as it sends it (counted from 0), only when it is a plain small number."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int) or (isinstance(v, str) and v.strip().isdigit()):
        n = int(v)
        return n if 0 <= n < 100 else None
    return None


def _words(cur: dict[str, Any], b: Body, source: str) -> tuple[str | None, str | None, str | None, str | None]:
    """(text, source, raw, fmt) to keep for one email, given what is stored and what has just come. A preview never replaces full words; an
    HTML original is not replaced by a plain-text one (its quote containers cut the history better); the words always follow the original kept."""
    text, src, raw, fmt = cur.get("body_text"), cur.get("body_source"), cur.get("body_raw"), cur.get("body_raw_format")
    if b.raw is not None and b.text:
        if raw and fmt == "html" and b.fmt != "html":
            again = clean(raw, "html")
            return (again or text), src, raw, fmt
        return b.text, source, b.raw, b.fmt
    if b.text and b.preview and (text is None or src == "preview"):
        return b.text, "preview", raw, fmt
    return text, src, raw, fmt


def remember(conn: Conn, activity_id: str, a: dict[str, Any], source: str) -> bool:
    """Keep the words, the email as Lemlist gave it, the step and the thread ids of one stored activity. Additive: a known value is only
    replaced by a new one, never by nothing; full words are never replaced by a preview; words cleaned by an older version are kept in
    body_text_original when they are replaced. Changed words are written to the person's timeline again. True when the activity exists."""
    cur = q1(conn, "SELECT body_text, body_source, body_raw, body_raw_format, body_clean_version FROM lemlist_activities WHERE id=%s", (activity_id,))
    if not cur:
        return False
    text, src, raw, fmt = _words(cur, extract(a), source)
    changed = text != cur["body_text"]
    keep_old = changed and cur["body_text"] is not None and cur["body_source"] != "preview" and cur["body_clean_version"] is None
    version = CLEAN_VERSION if raw is not None else cur["body_clean_version"]
    conn.execute(
        """UPDATE lemlist_activities SET body_text=%(text)s, body_source=%(src)s, body_raw=%(raw)s, body_raw_format=%(fmt)s, body_clean_version=%(ver)s,
             body_text_original = CASE WHEN %(keep)s THEN coalesce(body_text_original, body_text) ELSE body_text_original END,
             timeline_at = CASE WHEN %(changed)s THEN NULL ELSE timeline_at END,
             subject = coalesce(%(subject)s, subject), message_id = coalesce(%(mid)s, message_id), lemlist_team_id = coalesce(%(team)s, lemlist_team_id),
             lemlist_contact_id = coalesce(%(contact)s, lemlist_contact_id), lemlist_lead_id = coalesce(%(lead)s, lemlist_lead_id),
             send_user_id = coalesce(%(uid)s, send_user_id), send_user_email = coalesce(%(uemail)s, send_user_email),
             send_user_mailbox_id = coalesce(%(mbox)s, send_user_mailbox_id), send_user_name = coalesce(%(uname)s, send_user_name),
             sequence_step = coalesce(%(step)s, sequence_step), step_id = coalesce(%(step_id)s, step_id),
             campaign_id = coalesce(campaign_id, %(cid)s), campaign_name = coalesce(campaign_name, %(cname)s)
           WHERE id = %(id)s""",
        {"id": activity_id, "text": text, "src": src, "raw": raw, "fmt": fmt, "ver": version, "keep": keep_old, "changed": changed,
         "subject": _short(a.get("subject")), "mid": _short(a.get("messageId"), 500),
         "team": _id(a.get("teamId")), "contact": _id(a.get("contactId")), "lead": _id(a.get("leadId")), "uid": _id(a.get("sendUserId")),
         "uemail": _email(a.get("sendUserEmail")), "mbox": _id(a.get("sendUserMailboxId")), "uname": _short(a.get("sendUserName"), 120),
         "step": _step(a.get("sequenceStep")), "step_id": _id(a.get("stepId")), "cid": _id(a.get("campaignId")), "cname": _short(a.get("campaignName"), 300)})
    return True


def reclean(conn: Conn, client: Any = None, *, person_ids: list[str] | None = None, limit: int = RECLEAN_BATCH) -> dict[str, Any]:
    """Clean again the words of emails stored by an older cleaning (the replies of round 9 still carried our own campaign email under an
    Outlook header). Idempotent and never deletes:
      1. local, no Lemlist call: the words are derived again from the email as Lemlist gave it, or, for emails stored before it was kept,
         from the words first stored (kept in body_text_original). `body_text` is overwritten only with a different, non-empty result;
      2. with a client: the inbox thread of each conversation whose reply has no original kept is read again once (read-only), which keeps
         the original and its words (store_thread). Marked per email with raw_refetched_at, so it happens once.
    A Lemlist problem stops step 2 and is reported; the next run carries on."""
    scope, params = ("AND person_id = ANY(%s::uuid[])", [person_ids]) if person_ids is not None else ("", [])
    rows = qa(conn, f"""SELECT id, body_text, body_raw, body_raw_format, body_text_original FROM lemlist_activities
                        WHERE type IN ('emailsSent','emailsReplied') AND body_text IS NOT NULL AND body_source IS DISTINCT FROM 'preview'
                          AND (body_clean_version IS NULL OR body_clean_version < %s) {scope}
                        ORDER BY happened_at DESC NULLS LAST LIMIT %s""", [CLEAN_VERSION, *params, limit])          # noqa: S608
    out: dict[str, Any] = {"checked": len(rows), "changed": 0}
    for r in rows:
        new = clean(r["body_raw"], r["body_raw_format"] or "text") if r["body_raw"] else clean(r["body_text_original"] or r["body_text"], "text")
        changed = bool(new) and new != r["body_text"]
        conn.execute("""UPDATE lemlist_activities SET body_clean_version=%s,
                          body_text_original = CASE WHEN %s THEN coalesce(body_text_original, body_text) ELSE body_text_original END,
                          body_text = CASE WHEN %s THEN %s ELSE body_text END, timeline_at = CASE WHEN %s THEN NULL ELSE timeline_at END
                        WHERE id=%s""", (CLEAN_VERSION, changed and not r["body_raw"], changed, new, changed, r["id"]))
        out["changed"] += int(changed)
    conn.commit()
    if client is None:
        return out
    contacts = [str(r["c"]) for r in qa(conn, f"""SELECT lemlist_contact_id AS c, max(happened_at) AS last FROM lemlist_activities
                                                   WHERE type = 'emailsReplied' AND body_raw IS NULL AND raw_refetched_at IS NULL AND lemlist_contact_id IS NOT NULL {scope}
                                                   GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT %s""", [*params, REFETCH_PER_RUN])]   # noqa: S608
    out["threads"] = 0
    try:
        for c in contacts:
            store_thread(conn, client, c)
            conn.execute("UPDATE lemlist_activities SET raw_refetched_at=now() WHERE lemlist_contact_id=%s AND raw_refetched_at IS NULL", (c,))
            conn.commit()
            out["threads"] += 1
    except (LemlistError, httpx.HTTPError) as exc:
        conn.rollback()
        log.warning("lemlist reclean: %s", type(exc).__name__)
        out["error"] = f"Lemlist could not be read ({getattr(exc, 'status', None) or type(exc).__name__})"
    return out


# --------------------------------------------------------------------------------------------------- reading from Lemlist
def store_thread(conn: Conn, client: Any, contact_id: str) -> int:
    """Read one contact's inbox thread and keep the words of every email in it. An email the hourly feed has not brought yet is stored
    the way the feed would have stored it first (so the person is linked and a reply is tracked). Returns how many emails were kept."""
    from ..people import apply_lemlist_activity
    n = 0
    for m in client.contact_messages(contact_id):
        aid, typ = str(m.get("_id") or ""), str(m.get("type") or "")
        if typ not in (SENT, REPLIED) or not _id(aid):
            continue
        if not q1(conn, "SELECT 1 FROM lemlist_activities WHERE id=%s", (aid,)):
            apply_lemlist_activity(conn, {**m, "leadEmail": m.get("leadEmail") or m.get("fromEmail")})
        n += int(remember(conn, aid, {**m, "contactId": m.get("contactId") or contact_id}, "inbox"))
    return n


def _contact_from_lead(client: Any, email: str) -> str | None:
    try:
        leads = client.leads_by_email(email)
    except LemlistError:
        return None
    for lead in leads or []:
        c = _id(lead.get("contactId")) if isinstance(lead, dict) else None
        if c:
            return c
    return None


def _pending(conn: Conn, where: str, params: list[Any], limit: int) -> list[dict[str, Any]]:
    return qa(conn, f"""SELECT id, email, lemlist_contact_id FROM lemlist_activities
                        WHERE type = 'emailsReplied' AND (body_text IS NULL OR body_source = 'preview' OR send_user_mailbox_id IS NULL) AND ({where})
                        ORDER BY happened_at DESC NULLS LAST LIMIT %s""", [*params, limit])          # noqa: S608


def _fill(conn: Conn, client: Any, rows: list[dict[str, Any]], *, feed: bool, read: set[str] | None = None) -> dict[str, int]:
    """Ask Lemlist for the words of these replies: the replies feed first (ids and, usually, the text), then each contact's inbox thread.
    The contacts whose thread was read are added to `read`."""
    stats = {"asked": len(rows), "from_feed": 0, "threads": 0, "kept": 0}
    read = read if read is not None else set()
    want = {str(r["id"]) for r in rows}
    if feed:
        seen: set[str] = set()
        for page in range(FEED_PAGES):
            batch = client.reply_activities(offset=page * 100, limit=100)
            for a in batch:
                aid = str(a.get("_id") or "")
                if aid in want and remember(conn, aid, a, "activity"):
                    stats["from_feed"] += 1
                    seen.add(aid)
            conn.commit()
            if len(batch) < 100 or seen >= want:
                break
    still = _pending(conn, "id = ANY(%s)", [list(want)], len(want))
    lookups = 0
    for r in still:                                   # no contact id from the feed: Lemlist's lead record (GET /leads/{email}) names it
        if not r["lemlist_contact_id"] and lookups < LEAD_LOOKUPS_PER_RUN and "@" in (r["email"] or ""):
            lookups += 1
            c = _contact_from_lead(client, r["email"])
            if c:
                conn.execute("UPDATE lemlist_activities SET lemlist_contact_id=%s WHERE id=%s AND lemlist_contact_id IS NULL", (c, r["id"]))
                r["lemlist_contact_id"] = c
    contacts = list(dict.fromkeys(str(r["lemlist_contact_id"]) for r in still if r["lemlist_contact_id"]))
    for c in contacts[:THREADS_PER_RUN]:
        stats["kept"] += store_thread(conn, client, c)
        stats["threads"] += 1
        read.add(c)
        conn.commit()
    conn.execute("UPDATE lemlist_activities SET body_checked_at=now(), body_attempts=body_attempts+1 WHERE id = ANY(%s)", (list(want),))
    conn.commit()
    return stats


def fill_reply_bodies(conn: Conn, client: Any, *, now: datetime | None = None, limit: int = 40) -> dict[str, Any]:
    """The hourly step after the activity sync: replies without words (or without the ids needed to answer) are asked about again, each at
    most every RECHECK and MAX_ATTEMPTS times. Read-only towards Lemlist. A Lemlist problem is reported, never raised: the activity sync
    has already been saved."""
    now = now or datetime.now(timezone.utc)
    rows = _pending(conn, "body_attempts < %s AND (body_checked_at IS NULL OR body_checked_at < %s)", [MAX_ATTEMPTS, now - RECHECK], limit)
    if not rows:
        return {"asked": 0}
    try:
        return _fill(conn, client, rows, feed=True)
    except (LemlistError, httpx.HTTPError) as exc:
        conn.rollback()
        log.warning("lemlist reply text: %s", type(exc).__name__)
        return {"asked": len(rows), "error": f"Lemlist could not be read ({getattr(exc, 'status', None) or type(exc).__name__})"}


def refresh_person(conn: Conn, client: Any, person_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """When someone opens a person's thread: read that person's Lemlist conversation again (at most once every REFRESH_EVERY), so the words
    of their replies and of our campaign emails are there. Read-only towards Lemlist."""
    now = now or datetime.now(timezone.utc)
    rows = qa(conn, "SELECT id, email, lemlist_contact_id, body_checked_at FROM lemlist_activities WHERE person_id=%s AND type='emailsReplied' "
                    "ORDER BY happened_at DESC NULLS LAST LIMIT 10", (person_id,))
    if not rows or all(r["body_checked_at"] and r["body_checked_at"] > now - REFRESH_EVERY for r in rows):
        return {"refreshed": False}
    read: set[str] = set()
    try:
        if not any(r["lemlist_contact_id"] for r in rows):
            _fill(conn, client, rows, feed=True, read=read)     # finds the contact (and usually the words) in the replies feed
        contacts = [str(r["c"]) for r in qa(conn, "SELECT DISTINCT lemlist_contact_id AS c FROM lemlist_activities WHERE person_id=%s AND lemlist_contact_id IS NOT NULL LIMIT 3",
                                            (person_id,))]
        kept = 0
        for c in contacts:
            if c not in read:
                kept += store_thread(conn, client, c)
                conn.execute("UPDATE lemlist_activities SET raw_refetched_at=coalesce(raw_refetched_at, now()) WHERE lemlist_contact_id=%s", (c,))
                conn.commit()
        conn.execute("UPDATE lemlist_activities SET body_checked_at=now() WHERE person_id=%s AND type='emailsReplied'", (person_id,))
        conn.commit()
    except (LemlistError, httpx.HTTPError) as exc:
        conn.rollback()
        temporary = not isinstance(exc, LemlistError) or (exc.status or 500) >= 500 or exc.status == 429
        return {"refreshed": False, "error": "Lemlist could not be reached just now. The text is fetched again within the hour." if temporary
                else "Lemlist did not let us read this conversation."}
    return {"refreshed": True, "threads": len(contacts), "kept": kept}


# --------------------------------------------------------------------------------------------------- what the app shows
def lemlist_url(team_id: str | None, contact_id: str | None) -> str | None:
    """The conversation in the Lemlist inbox, in the form Lemlist's own guide links to. Only when both ids are known."""
    t, c = _id(team_id), _id(contact_id)
    return f"https://app.lemlist.com/teams/{t}/inbox/list/myConversations/contacts/{c}" if t and c else None


def _text_state(r: dict[str, Any]) -> str:
    if r.get("body_text"):
        return "preview" if r.get("body_source") == "preview" else "full"
    return "pending" if int(r.get("body_attempts") or 0) < MAX_ATTEMPTS else "missing"


def _sender(conn: Conn, r: dict[str, Any]) -> dict[str, Any] | None:
    """The mailbox to answer from: the one Lemlist names on the reply, else the one that sent this person the campaign email in the same
    campaign (both come from Lemlist; nothing is guessed). None when Lemlist has not told us."""
    if r.get("send_user_id") and r.get("send_user_email") and r.get("send_user_mailbox_id"):
        return {"user_id": r["send_user_id"], "email": r["send_user_email"], "mailbox_id": r["send_user_mailbox_id"], "name": r.get("send_user_name")}
    s = q1(conn, "SELECT send_user_id, send_user_email, send_user_mailbox_id, send_user_name FROM lemlist_activities WHERE person_id=%s AND type='emailsSent' "
                 "AND campaign_id IS NOT DISTINCT FROM %s AND send_user_id IS NOT NULL AND send_user_email IS NOT NULL AND send_user_mailbox_id IS NOT NULL "
                 "ORDER BY happened_at DESC NULLS LAST LIMIT 1", (r.get("person_id"), r.get("campaign_id")))
    return {"user_id": s["send_user_id"], "email": s["send_user_email"], "mailbox_id": s["send_user_mailbox_id"], "name": s["send_user_name"]} if s else None


def _contact(conn: Conn, r: dict[str, Any]) -> tuple[str | None, str | None]:
    if r.get("lemlist_contact_id") or r.get("lemlist_lead_id"):
        return r.get("lemlist_contact_id"), r.get("lemlist_lead_id")
    s = q1(conn, "SELECT lemlist_contact_id, lemlist_lead_id FROM lemlist_activities WHERE person_id=%s AND (lemlist_contact_id IS NOT NULL OR lemlist_lead_id IS NOT NULL) "
                 "ORDER BY happened_at DESC NULLS LAST LIMIT 1", (r.get("person_id"),))
    return (s["lemlist_contact_id"], s["lemlist_lead_id"]) if s else (None, None)


def do_not_contact(conn: Conn, person: dict[str, Any]) -> bool:
    """Suppressed in the people database, or unsubscribed/bounced on any campaign enrolment."""
    if person.get("suppressed") or person.get("erased_at"):
        return True
    email = (person.get("work_email") or "").strip().lower()
    return bool(email and q1(conn, "SELECT 1 FROM outbound_enrolments WHERE lower(trim(email))=%s AND status IN ('unsubscribed','bounced') LIMIT 1", (email,)))


BLOCKED = {
    "do_not_contact": "This person is on the do-not-contact list, so nothing can be sent to them.",
    "no_activity_id": "Lemlist has not given us the id of this email, so a reply cannot be threaded from here. Answer in Lemlist.",
    "no_mailbox": "Lemlist has not told us which mailbox sent the campaign, so a reply cannot be sent from here yet. Answer in Lemlist.",
    "no_contact": "Lemlist has not told us who this contact is, so a reply cannot be sent from here yet. Answer in Lemlist.",
}


def blocked_code(conn: Conn, r: dict[str, Any], person: dict[str, Any]) -> str | None:
    """Why this reply cannot be answered from here (a key of BLOCKED), or None. The outbound switch and permissions are checked separately."""
    if do_not_contact(conn, person):
        return "do_not_contact"
    if not str(r.get("id") or "").startswith("act_"):
        return "no_activity_id"
    if not _sender(conn, r):
        return "no_mailbox"
    if _contact(conn, r) == (None, None):
        return "no_contact"
    return None


_ACT_COLS = ("la.id, la.type, la.person_id, la.email, la.campaign_id, la.campaign_name, la.happened_at, la.subject, la.body_text, la.body_source, la.body_attempts, "
             "la.lemlist_team_id, la.lemlist_contact_id, la.lemlist_lead_id, la.send_user_id, la.send_user_email, la.send_user_mailbox_id, la.send_user_name, "
             "la.sequence_step, la.step_id, la.body_clean_version, la.body_text_original, la.body_raw_format, "
             f"CASE WHEN coalesce(la.body_clean_version, 0) < {CLEAN_VERSION} THEN la.body_raw END AS body_raw")   # the original only when it is needed


def words(r: dict[str, Any]) -> str | None:
    """The words to show. Words stored by an older cleaning are cleaned again on the way out (the hourly sync stores the result), so a reply
    never shows our own earlier emails underneath, even before that run."""
    text = r.get("body_text")
    if not text or r.get("body_source") == "preview" or (r.get("body_clean_version") or 0) >= CLEAN_VERSION:
        return text
    again = clean(r["body_raw"], r.get("body_raw_format") or "text") if r.get("body_raw") else clean(r.get("body_text_original") or text, "text")
    return again or text


def _reply_view(conn: Conn, r: dict[str, Any], person: dict[str, Any]) -> dict[str, Any]:
    sender = _sender(conn, r)
    contact, _ = _contact(conn, r)
    answered = q1(conn, "SELECT status, created_at, sent_at, created_by FROM lemlist_replies WHERE reply_to_activity_id=%s AND status IN ('sent','sending','unknown') "
                        "ORDER BY created_at DESC LIMIT 1", (r["id"],))
    # the server works with one Lemlist key, so one team: any activity that names it gives the link (the link only, never a sender)
    team = r.get("lemlist_team_id") or (q1(conn, "SELECT lemlist_team_id FROM lemlist_activities WHERE lemlist_team_id IS NOT NULL LIMIT 1") or {}).get("lemlist_team_id")
    return {"activity_id": r["id"], "at": r["happened_at"], "campaign": r.get("campaign_name"), "subject": r.get("subject"),
            "text": words(r), "text_state": _text_state(r), "lemlist_url": lemlist_url(team, contact),
            "sender": {"name": sender.get("name"), "email": sender["email"]} if sender else None,
            # who the answer goes to: the address that wrote to us (Lemlist answers the contact of this thread), else the one we hold
            "to": {"name": person.get("full_name"), "email": _email(r.get("email")) or _email(person.get("work_email"))},
            "blocked": blocked_code(conn, r, person),
            "answered": {"status": answered["status"], "at": answered["sent_at"] or answered["created_at"], "by": answered["created_by"]} if answered else None}


def latest_replies(conn: Conn, person_ids: list[str]) -> dict[str, dict[str, Any]]:
    """For Today: each person's newest reply to our campaign, with its words and whether it can be answered from here."""
    if not person_ids:
        return {}
    rows = qa(conn, f"""SELECT DISTINCT ON (la.person_id) {_ACT_COLS}, pe.full_name, pe.suppressed, pe.erased_at, pe.work_email
                        FROM lemlist_activities la JOIN people pe ON pe.id=la.person_id
                        WHERE la.person_id = ANY(%s::uuid[]) AND la.type = 'emailsReplied'
                        ORDER BY la.person_id, la.happened_at DESC NULLS LAST""", (person_ids,))          # noqa: S608
    return {str(r["person_id"]): _reply_view(conn, r, r) for r in rows}


def _same(a: str | None, b: str | None) -> bool:
    norm = lambda s: re.sub(r"\W+", " ", (s or "").lower()).strip()[:120]      # noqa: E731
    return bool(norm(a)) and norm(a) == norm(b)


def _ours(a: str | None, o: dict[str, Any]) -> bool:
    """An email Lemlist sent back is our own answer from here: the same words, with or without the signature appended to them."""
    return _same(a, o.get("body_text")) or bool(o.get("signature_text")) and _same(a, sig.full_text(o.get("body_text"), o.get("signature_text")))


def step_fields(a: dict[str, Any]) -> tuple[int | None, str | None]:
    """(sequenceStep as Lemlist sends it, counted from 0; stepId) of an activity, for storing with any activity type."""
    return _step(a.get("sequenceStep")), _id(a.get("stepId"))


def _campaign_names(conn: Conn, acts: list[dict[str, Any]]) -> dict[str, str]:
    """Campaign names we hold for these campaigns (an inbox message names the campaign id only; another activity of it names it)."""
    names = {str(a["campaign_id"]): a["campaign_name"] for a in acts if a.get("campaign_id") and a.get("campaign_name")}
    missing = list({str(a["campaign_id"]) for a in acts if a.get("campaign_id")} - set(names))
    if missing:
        for r in qa(conn, "SELECT DISTINCT ON (campaign_id) campaign_id, campaign_name FROM lemlist_activities WHERE campaign_id = ANY(%s) AND campaign_name IS NOT NULL "
                          "ORDER BY campaign_id, happened_at DESC NULLS LAST", (missing,)):
            names[str(r["campaign_id"])] = r["campaign_name"]
    return names


def _step_totals(conn: Conn, campaign_ids: list[str]) -> dict[str, int]:
    """How many emails each campaign's sequence has, as far as we know: the steps of a campaign started from this app, else the furthest
    step Lemlist has sent anyone in it. Used for "Step 2 of 3"; a campaign we know nothing more about shows "Step 2"."""
    ids = list(dict.fromkeys(c for c in campaign_ids if c))
    if not ids:
        return {}
    out = {str(r["campaign_id"]): int(r["n"]) + 1 for r in qa(conn, "SELECT campaign_id, max(sequence_step) AS n FROM lemlist_activities WHERE type='emailsSent' "
                                                                  "AND sequence_step IS NOT NULL AND campaign_id = ANY(%s) GROUP BY 1", (ids,))}
    for r in qa(conn, "SELECT lemlist_campaign_id AS c, jsonb_array_length(steps) AS n FROM signal_campaigns WHERE lemlist_campaign_id = ANY(%s) "
                      "AND jsonb_typeof(steps) = 'array'", (ids,)):
        out[str(r["c"])] = max(out.get(str(r["c"]), 0), int(r["n"] or 0))
    return {k: v for k, v in out.items() if v > 0}


def _engagement(sent: list[dict[str, Any]], events: list[dict[str, Any]]) -> None:
    """Put each open, click and bounce under the campaign email it belongs to: the email of the same step (Lemlist's step id, else its step
    number) in the same campaign; when the step is not known, the last email of that campaign sent before it happened. An event before any
    email we hold stays in the summary only."""
    by_campaign: dict[str, list[dict[str, Any]]] = {}
    for item in sent:
        by_campaign.setdefault(item["campaign_id"], []).append(item)
    for e in events:
        cands = by_campaign.get(str(e.get("campaign_id") or ""))
        if not cands:
            continue
        target = next((c for c in cands if e.get("step_id") and c["step_id"] == e["step_id"]), None) \
            or next((c for c in cands if e.get("sequence_step") is not None and c["sequence_step"] == e["sequence_step"]), None) \
            or next((c for c in reversed(cands) if c["at"] and e["happened_at"] and c["at"] <= e["happened_at"]), None)
        if target is None:
            continue
        g = target["engagement"]
        key = {"emailsOpened": "opens", "emailsClicked": "clicks", "emailsBounced": "bounces"}[e["type"]]
        g[key] += 1
        last = f"last_{key[:-1]}"
        if e["happened_at"] and (g[last] is None or e["happened_at"] > g[last]):
            g[last] = e["happened_at"]


def settle_people(conn: Conn, person_ids: list[str], *, limit: int = THREAD_LIMIT) -> None:
    """When a person's emails or a Conversations timeline are opened: the words cleaned by the latest version and every email on the
    timeline. Local and idempotent (no Lemlist call); a problem here never stops the page from showing what is stored."""
    if not person_ids:
        return
    try:
        from .email_timeline import record_timeline
        reclean(conn, None, person_ids=person_ids, limit=limit)
        record_timeline(conn, person_ids=person_ids, limit=limit)
    except Exception:                                              # noqa: BLE001
        conn.rollback()
        log.exception("lemlist settle people")


def settle_person(conn: Conn, person_id: str) -> None:
    settle_people(conn, [person_id])


def thread(conn: Conn, person: dict[str, Any]) -> dict[str, Any]:
    """Every email between a person and us, each on its own and oldest first: our campaign emails (sender mailbox, step, campaign, and the
    opens, clicks and bounces that followed each one), their replies (their own words only), and the answers sent from this app; a short
    summary on top, and the reply the box answers (their newest). Read from our database only."""
    pid = str(person["id"])
    acts = list(reversed(qa(conn, f"SELECT {_ACT_COLS} FROM lemlist_activities la WHERE la.person_id=%s AND la.type IN ('emailsSent','emailsReplied') "
                                  "ORDER BY la.happened_at DESC NULLS LAST LIMIT %s", (pid, THREAD_LIMIT))))           # noqa: S608
    events = qa(conn, "SELECT type, campaign_id, happened_at, sequence_step, step_id FROM lemlist_activities WHERE person_id=%s AND type = ANY(%s) "
                      "ORDER BY happened_at NULLS FIRST LIMIT 2000", (pid, list(ENGAGEMENT)))
    ours = qa(conn, "SELECT r.id, r.reply_to_activity_id, r.body_text, r.signature_text, r.status, r.error, r.created_by, r.created_at, r.sent_at, "
                    "r.send_user_email, la.send_user_name AS mailbox_name, u.display_name AS by_name, la.subject AS re_subject FROM lemlist_replies r "
                    "LEFT JOIN users u ON lower(u.email)=lower(r.created_by) LEFT JOIN lemlist_activities la ON la.id=r.reply_to_activity_id "
                    "WHERE r.person_id=%s ORDER BY r.created_at DESC LIMIT 30", (pid,))
    names = _campaign_names(conn, acts)
    totals = _step_totals(conn, [str(a["campaign_id"]) for a in acts if a.get("campaign_id")])
    name = person.get("full_name")
    items: list[dict[str, Any]] = []
    for o in ours:
        items.append({"id": str(o["id"]), "kind": "answer", "via": "app", "at": o["sent_at"] or o["created_at"], "subject": o["re_subject"],
                      "text": o["body_text"], "signature": o["signature_text"], "text_state": "full", "status": o["status"],
                      "error": o["error"] if o["status"] in ("failed", "unknown") else None, "by": o["created_by"], "by_name": o["by_name"],
                      "from": {"name": o["mailbox_name"] or o["by_name"], "email": o["send_user_email"]}, "to": {"name": name},
                      "reply_to": o["reply_to_activity_id"]})
    sent: list[dict[str, Any]] = []
    for a in acts:
        mine = a["type"] == SENT
        if mine and any(_ours(a.get("body_text"), o) for o in ours):
            continue                                               # our own answer, read back from the Lemlist inbox: shown once, from our record
        cid = str(a["campaign_id"]) if a.get("campaign_id") else None
        step = a.get("sequence_step")
        item = {"id": a["id"], "kind": "sent" if mine else "reply", "at": a["happened_at"], "campaign": names.get(cid or "") or a.get("campaign_name"),
                "campaign_id": cid, "step": step + 1 if isinstance(step, int) else None, "steps": totals.get(cid or "") if mine else None,
                "subject": a.get("subject"), "text": words(a),
                "text_state": _text_state(a) if not mine else ("full" if a.get("body_text") else "missing")}
        if mine:
            item.update({"via": "campaign" if cid else "lemlist", "from": {"name": a.get("send_user_name"), "email": a.get("send_user_email")}, "to": {"name": name},
                         "engagement": {"opens": 0, "last_open": None, "clicks": 0, "last_click": None, "bounces": 0, "last_bounce": None},
                         "sequence_step": step, "step_id": a.get("step_id")})
            if cid:
                sent.append(item)
        else:
            item.update({"via": "campaign" if cid else "lemlist", "from": {"name": name, "email": None},
                         "to": {"name": a.get("send_user_name"), "email": a.get("send_user_email")} if a.get("send_user_email") else None})
        items.append(item)
    _engagement(sent, events)
    for item in sent:
        item.pop("sequence_step", None)
        item.pop("step_id", None)
    items.sort(key=lambda x: x["at"] or datetime.min.replace(tzinfo=timezone.utc))
    latest = next((a for a in reversed(acts) if a["type"] == REPLIED), None)
    for item in items:
        item["newest"] = bool(latest) and item["id"] == latest["id"]
    campaign = [i for i in items if i["kind"] == "sent" and i["via"] == "campaign"]
    answers = [i for i in items if (i["kind"] == "answer" and i["status"] == "sent") or (i["kind"] == "sent" and i["via"] == "lemlist")]
    replies = [i for i in items if i["kind"] == "reply"]
    counts = {t: sum(1 for e in events if e["type"] == t) for t in ENGAGEMENT}
    summary = {"sent": len(campaign), "first_sent": campaign[0]["at"] if campaign else None, "last_sent": campaign[-1]["at"] if campaign else None,
               "opens": counts["emailsOpened"], "clicks": counts["emailsClicked"], "bounced": counts["emailsBounced"] > 0,
               "replies": len(replies), "last_reply": replies[-1]["at"] if replies else None,
               "answers": len(answers), "last_answer": answers[-1]["at"] if answers else None}
    return {"items": items, "summary": summary, "reply": _reply_view(conn, latest, person) if latest else None}


# --------------------------------------------------------------------------------------------------- sending
class ReplyError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


OFF = "Sending is switched off by the administrator. Nothing was sent."
UNKNOWN = "Lemlist did not confirm whether the email went out. Check the conversation in Lemlist before you send it again."


def make_client(settings: Any) -> Any:
    """The server-wide Lemlist client (the key that ran the campaigns), for a request someone is waiting on: a short timeout and one retry
    after a 429 (a send is never retried after a server error). Tests replace this with a fake."""
    from .lemlist import LemlistClient
    key = getattr(settings, "lemlist_api_key", None)
    return LemlistClient(key, http=httpx.Client(timeout=15), retries=1) if key else None


def to_html(text: str) -> str:
    """Plain text to the simple HTML Lemlist sends: paragraphs on blank lines, line breaks kept, everything escaped."""
    paras = [p for p in re.split(r"\n{2,}", text.strip()) if p.strip()]
    return "".join(f"<p>{'<br>'.join(htmllib.escape(line) for line in p.split(chr(10)))}</p>" for p in paras)


def _lemlist_words(exc: LemlistError) -> tuple[str, str]:
    """(status, plain message) for a refused send. 'failed' means Lemlist said no, so nothing went out and the same draft may be sent
    again; 'unknown' means we cannot tell."""
    s, m = exc.status, str(exc).lower()
    if s is None or s >= 500:
        return "unknown", UNKNOWN
    if s == 429:
        return "failed", "Lemlist is busy just now. Nothing was sent. Try again in a minute."
    if s in (401, 403):
        return "failed", "Lemlist refused the server's key. Nothing was sent. Ask the administrator to check the Lemlist connection."
    if "alias" in m:
        return "failed", "Lemlist refused the sender address: it no longer belongs to the mailbox that sent the campaign. Nothing was sent. Answer in Lemlist."
    if "mailbox" in m or "senduser" in m:
        return "failed", "The mailbox that sent the campaign is no longer connected in Lemlist. Nothing was sent. Answer in Lemlist."
    if "replytoactivityid" in m:
        return "failed", "Lemlist could not find the email you are answering. Nothing was sent."
    if "contact" in m or "lead" in m:
        return "failed", "Lemlist no longer knows this contact. Nothing was sent."
    return "failed", "Lemlist did not accept the email, so nothing was sent."


def send_reply(conn: Conn, *, actor: str, user_id: str, person: dict[str, Any], activity_id: str, text: str, key: str, settings: Any,
               client: Any = None) -> dict[str, Any]:
    """Answer one reply in its Lemlist thread. The caller has checked the permission and that the person is visible to the actor.
    Raises ReplyError with plain words; every refusal after the switch check and every attempt is audited (length only)."""
    pid = str(person["id"])
    r = q1(conn, f"SELECT {_ACT_COLS} FROM lemlist_activities la WHERE la.id=%s", (activity_id,))          # noqa: S608
    if not r or r["type"] != REPLIED or str(r["person_id"]) != pid:
        raise ReplyError(404, "This email is not a reply from this person.")
    if not getattr(settings, "outbound_enabled", False):
        audit(conn, actor=actor, operation="lemlist_reply_refused", record_type="person", record_id=pid, detail={"activity_id": activity_id, "reason": "switched_off"})
        raise ReplyError(403, OFF)
    body = (text or "").replace("\r\n", "\n").strip()
    if not body:
        raise ReplyError(400, "Write a few words before you send.")
    if len(body) > MAX_REPLY:
        raise ReplyError(400, f"The reply is too long. Keep it under {MAX_REPLY} characters.")
    why = blocked_code(conn, r, person)
    if why:
        audit(conn, actor=actor, operation="lemlist_reply_refused", record_type="person", record_id=pid, detail={"activity_id": activity_id, "reason": why})
        raise ReplyError(409, BLOCKED[why])
    full_key = f"{user_id}:{key}"
    prior = q1(conn, "SELECT * FROM lemlist_replies WHERE idempotency_key=%s", (full_key,))
    if prior:
        if prior["reply_to_activity_id"] != activity_id:
            raise ReplyError(409, "This draft belongs to another conversation. Reload the page and write it again.")
        if prior["status"] == "sent":
            return {"status": "sent", "id": str(prior["id"]), "repeat": True, "sent_at": prior["sent_at"], "from": prior["send_user_email"]}
        stale = prior["created_at"] < datetime.now(timezone.utc) - timedelta(minutes=5)
        if prior["status"] == "unknown" or (prior["status"] == "sending" and stale):
            raise ReplyError(409, UNKNOWN)
        if prior["status"] == "sending":
            raise ReplyError(409, "This reply is being sent. Wait a moment.")
    digest = hashlib.sha256(body.encode()).hexdigest()
    twin = q1(conn, "SELECT status FROM lemlist_replies WHERE reply_to_activity_id=%s AND body_hash=%s AND status IN ('sending','sent','unknown') "
                    "AND created_at > %s AND idempotency_key <> %s ORDER BY created_at DESC LIMIT 1",
              (activity_id, digest, datetime.now(timezone.utc) - DUPLICATE_WINDOW, full_key))
    if twin:                                                       # a second tab or a new draft with the same words: never twice
        raise ReplyError(409, UNKNOWN if twin["status"] == "unknown" else "These exact words went to this thread a moment ago, so they were not sent again.")
    client = client if client is not None else make_client(settings)
    if client is None:
        raise ReplyError(503, "Lemlist is not connected on the server, so replies cannot be sent from here.")
    sender = _sender(conn, r)
    contact, lead = _contact(conn, r)
    if sender is None or (contact, lead) == (None, None):          # blocked_code has said so already; never send from a guessed mailbox
        raise ReplyError(409, BLOCKED["no_mailbox"])
    # the footer: this person's signature (or the same suggestion the box showed), once, unless the words already end with it
    footer = sig.to_add(body, sig.for_user(conn, user_id=user_id, email=actor, person_id=pid)["text"])
    row = conn.execute(
        "INSERT INTO lemlist_replies(idempotency_key, reply_to_activity_id, person_id, body_text, body_hash, signature_text, send_user_id, send_user_email, "
        "send_user_mailbox_id, lemlist_contact_id, lemlist_lead_id, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (idempotency_key) DO UPDATE SET status='sending', error=NULL, body_text=EXCLUDED.body_text, body_hash=EXCLUDED.body_hash, "
        "signature_text=EXCLUDED.signature_text, created_at=now() WHERE lemlist_replies.status='failed' RETURNING id",
        (full_key, activity_id, pid, body, digest, footer, sender["user_id"], sender["email"], sender["mailbox_id"], contact, lead, actor)).fetchone()
    if not row:
        raise ReplyError(409, "This reply is being sent. Wait a moment.")
    rid = str(row["id"])
    conn.commit()                                                  # the claim is visible to a second click before Lemlist is called
    status, error = "sent", None
    try:
        res = client.send_email(send_user_id=sender["user_id"], send_user_email=sender["email"], send_user_mailbox_id=sender["mailbox_id"],
                                message_html=to_html(body) + (sig.html(footer) if footer else ""), reply_to_activity_id=activity_id,
                                contact_id=contact, lead_id=None if contact else lead)
        if isinstance(res, dict) and res.get("ok") is False:
            status, error = "failed", "Lemlist did not accept the email, so nothing was sent."
    except LemlistError as exc:
        status, error = _lemlist_words(exc)
    except httpx.HTTPError:
        status, error = "unknown", UNKNOWN
    except Exception:                                              # noqa: BLE001 - recorded as unknown, never as sent
        log.exception("lemlist reply")
        status, error = "unknown", UNKNOWN
    conn.execute("UPDATE lemlist_replies SET status=%s, error=%s, sent_at=CASE WHEN %s='sent' THEN now() END WHERE id=%s", (status, error, status, rid))
    audit(conn, actor=actor, operation="lemlist_reply_sent" if status == "sent" else "lemlist_reply_failed", record_type="person", record_id=pid,
          detail={"activity_id": activity_id, "reply_id": rid, "chars": len(body), "signature_chars": len(footer or ""), "status": status,
                  "mailbox_id": sender["mailbox_id"]})
    conn.commit()
    if status != "sent":
        raise ReplyError(502 if status == "unknown" else 409, error or UNKNOWN)
    try:
        from .email_timeline import record_timeline
        record_timeline(conn, person_ids=[pid])                    # the answer onto the person's Conversations timeline at once
    except Exception:                                              # noqa: BLE001 - the email went out; the hourly sync writes the row
        conn.rollback()
        log.exception("lemlist answer timeline")
    sent_at = (q1(conn, "SELECT sent_at FROM lemlist_replies WHERE id=%s", (rid,)) or {}).get("sent_at")
    return {"status": "sent", "id": rid, "repeat": False, "sent_at": sent_at, "from": sender["email"]}
