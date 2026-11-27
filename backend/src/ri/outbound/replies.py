"""Replies to our Lemlist campaigns: what people wrote back, the thread with them, and answering in that same thread.

Where the words come from (Lemlist's documented API, developer.lemlist.com):
  * the activity record itself: an `emailsReplied` activity carries `text` (plain), `html` and `messagePreview` in the webhook payload, which
    "mirrors the underlying activity record", and the same record is read from GET /activities (version v2, type=emailsReplied);
  * the inbox thread: GET /inbox/{contactId} returns every email with the contact (our campaign emails and their replies) with `message` (HTML,
    the quoted history included), `subject` and the sender ids.
The hourly Lemlist sync stores what the feed carries and then asks for the replies still without words (`fill_reply_bodies`); opening a person's
thread asks again for that person (`refresh_person`). Only plain text is kept: HTML is reduced to text, the quoted history is cut off, and the
result is capped at MAX_BODY characters.

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
from html.parser import HTMLParser
from typing import Any

import httpx

from ..audit import audit
from ..db import Conn, q1, qa
from .lemlist import LemlistError

log = logging.getLogger(__name__)

MAX_BODY = 8000                 # characters kept of one email (a long email is cut, with an ellipsis)
MAX_REPLY = 5000                # characters a reply written here may have
FEED_PAGES = 3                  # pages of 100 replies read from the feed per hourly run
THREADS_PER_RUN = 25            # inbox threads read per hourly run
LEAD_LOOKUPS_PER_RUN = 10       # replies whose Lemlist contact is looked up by address per run
MAX_ATTEMPTS = 6                # after this many hourly tries without words, the reply says plainly that the text is not available
RECHECK = timedelta(hours=6)    # how long the hourly sync waits before asking Lemlist again about the same reply
REFRESH_EVERY = timedelta(minutes=5)   # opening the same thread again within this time does not ask Lemlist again
DUPLICATE_WINDOW = timedelta(minutes=10)
THREAD_LIMIT = 60
REPLIED, SENT = "emailsReplied", "emailsSent"

_ID = re.compile(r"^[A-Za-z0-9_-]{3,80}$")
LEMLIST_HOME = "https://app.lemlist.com/"

# --------------------------------------------------------------------------------------------------- text
_QUOTE_CLASSES = ("gmail_quote", "gmail_attr", "yahoo_quoted", "moz-cite-prefix", "protonmail_quote", "x_gmail_quote")
_QUOTE_IDS = ("divrplyfwdmsg", "appendonsend", "mail-editor-reference-message-container", "x_divrplyfwdmsg")
_BLOCK = {"p", "div", "li", "tr", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header", "footer"}
_SKIP = {"script", "style", "head", "title", "noscript"}


class _Text(HTMLParser):
    """HTML to plain text: block elements become line breaks, links keep their words, and everything from the first quoted-history
    marker on (a blockquote, Gmail's or Outlook's quote container) is dropped: a reply's quoted history is our own earlier email."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done:
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        cls, ident = a.get("class", "").lower(), a.get("id", "").lower()
        if tag == "blockquote" or any(c in cls for c in _QUOTE_CLASSES) or ident in _QUOTE_IDS:
            self.done = True
            return
        if tag in _SKIP:
            self.skip += 1
        elif tag == "br":
            self.parts.append("\n")
        elif tag in _BLOCK:
            self._edge()

    def _edge(self) -> None:
        """A block starts or ends: begin a new line, but never add an empty one (Gmail writes one <div> per line)."""
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self.done and tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.done:
            return
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK:
            self._edge()

    def handle_data(self, data: str) -> None:
        if not self.done and not self.skip and data:
            self.parts.append(data if data.strip() or not self.parts or not self.parts[-1].endswith("\n") else data.strip(" \t\n"))


def html_to_text(value: str) -> str:
    p = _Text()
    try:
        p.feed(value)
        p.close()
    except Exception:  # noqa: BLE001 - malformed HTML: fall back to removing the tags
        return htmllib.unescape(re.sub(r"<[^>]+>", " ", value))
    return "".join(p.parts)


# The line that introduces the quoted history, in the languages our buyers write in (English, Polish, German, French, Nordic, Dutch),
# possibly wrapped over two or three lines ("On Mon, 17 Nov 2025 at 16:16 Ann <ann@x>\nwrote:").
_WRAP = r"[^\n]{0,250}(?:\n[^\n]{0,250}){0,2}?"
_HISTORY = [
    re.compile(r"^[ \t>]*On\s" + _WRAP + r"\bwrote:[ \t]*$", re.I | re.M),
    re.compile(r"^[ \t>]*W\s+dniu\s" + _WRAP + r"(?:napisał|napisała|napisał\(a\)|pisze)[^\n]{0,20}:[ \t]*$", re.I | re.M),
    re.compile(r"^[ \t>]*(?:Dnia|Wiadomość napisana przez)\s" + _WRAP + r"(?:napisał|napisała|napisał\(a\)|w dniu)[^\n]{0,60}:?[ \t]*$", re.I | re.M),
    re.compile(r"^[ \t>]*Am\s" + _WRAP + r"\bschrieb[^\n]{0,120}:[ \t]*$", re.I | re.M),
    re.compile(r"^[ \t>]*Le\s" + _WRAP + r"a\s+écrit\s*:[ \t]*$", re.I | re.M),
    re.compile(r"^[ \t>]*(?:Den|Op|Den)\s" + _WRAP + r"\b(?:skrev|schreef)[^\n]{0,120}:[ \t]*$", re.I | re.M),
    re.compile(r"^[ \t]*-{2,}\s*(?:Original Message|Ursprüngliche Nachricht|Wiadomość oryginalna|Oryginalna wiadomość|Message d'origine|Opprinnelig melding|Ursprungligt meddelande)\s*-{2,}", re.I | re.M),
    re.compile(r"^[ \t]*\*?(?:From|Od|Von|De|Fra|Från|Van)\s*:\*?[^\n]*\n[ \t]*\*?(?:Sent|Date|Wysłano|Wysłane|Data|Gesendet|Datum|Envoyé|Sendt|Skickat|Verzonden)\s*:", re.I | re.M),
    re.compile(r"^[ \t]*_{20,}[ \t]*$", re.M),
]
_IMAGE = re.compile(r"\[(?:image|cid|obraz)[^\]]{0,200}\]", re.I)


def strip_history(text: str) -> str:
    """Cut a reply at its quoted history and drop any left-over quoted lines. If nothing would be left (the whole email was a quote,
    or an inline answer), keep the text as it came rather than show nothing."""
    cut = min((m.start() for rx in _HISTORY if (m := rx.search(text))), default=len(text))
    head = "\n".join(line for line in text[:cut].split("\n") if not line.lstrip().startswith(">"))
    return head if head.strip() else text


def tidy(text: str) -> str:
    t = _IMAGE.sub("", text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ").replace("​", ""))
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in t.split("\n")]
    t = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return t if len(t) <= MAX_BODY else t[:MAX_BODY].rstrip() + "…"


def looks_html(value: str) -> bool:
    return bool(re.search(r"<(?:html|body|div|p|br|span|blockquote|a|table|img)\b", value, re.I))


def extract_body(a: dict[str, Any]) -> tuple[str | None, bool]:
    """(plain text, is_preview) from an activity or inbox message. The full words come from `text`, `html`, `message` or `body` (quoted
    history cut off); when only Lemlist's one-line `messagePreview` is there, it is returned as a preview."""
    for key in ("text", "html", "message", "body"):
        v = a.get(key)
        if not isinstance(v, str) or not v.strip():
            continue
        raw = html_to_text(v) if (key == "html" or (key != "text" and looks_html(v))) else v
        t = tidy(strip_history(raw.replace("\r\n", "\n")))
        if t:
            return t, False
    v = a.get("messagePreview")
    if isinstance(v, str) and v.strip():
        return tidy(v), True
    return None, False


def _id(v: Any) -> str | None:
    s = str(v).strip() if isinstance(v, (str, int)) else ""
    return s if _ID.match(s) else None


def _email(v: Any) -> str | None:
    s = str(v).strip() if isinstance(v, str) else ""
    return s[:320] if "@" in s and " " not in s else None


def _short(v: Any, n: int = 300) -> str | None:
    s = re.sub(r"\s+", " ", str(v)).strip() if isinstance(v, str) else ""
    return s[:n] or None


def remember(conn: Conn, activity_id: str, a: dict[str, Any], source: str) -> bool:
    """Keep the words and the thread ids of one stored activity. Additive: a known value is only replaced by a new one, never by nothing,
    and full words are never replaced by a preview. True when the activity exists."""
    text, preview = extract_body(a)
    src = "preview" if preview else source
    row = conn.execute(
        """UPDATE lemlist_activities SET
             body_text = CASE WHEN %(text)s::text IS NOT NULL AND (body_text IS NULL OR body_source = 'preview' OR %(src)s <> 'preview') THEN %(text)s ELSE body_text END,
             body_source = CASE WHEN %(text)s::text IS NOT NULL AND (body_text IS NULL OR body_source = 'preview' OR %(src)s <> 'preview') THEN %(src)s ELSE body_source END,
             subject = coalesce(%(subject)s, subject), message_id = coalesce(%(mid)s, message_id), lemlist_team_id = coalesce(%(team)s, lemlist_team_id),
             lemlist_contact_id = coalesce(%(contact)s, lemlist_contact_id), lemlist_lead_id = coalesce(%(lead)s, lemlist_lead_id),
             send_user_id = coalesce(%(uid)s, send_user_id), send_user_email = coalesce(%(uemail)s, send_user_email),
             send_user_mailbox_id = coalesce(%(mbox)s, send_user_mailbox_id), send_user_name = coalesce(%(uname)s, send_user_name)
           WHERE id = %(id)s RETURNING id""",
        {"id": activity_id, "text": text, "src": src, "subject": _short(a.get("subject")), "mid": _short(a.get("messageId"), 500),
         "team": _id(a.get("teamId")), "contact": _id(a.get("contactId")), "lead": _id(a.get("leadId")), "uid": _id(a.get("sendUserId")),
         "uemail": _email(a.get("sendUserEmail")), "mbox": _id(a.get("sendUserMailboxId")), "uname": _short(a.get("sendUserName"), 120)}).fetchone()
    return bool(row)


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


_ACT_COLS = ("la.id, la.type, la.person_id, la.campaign_id, la.campaign_name, la.happened_at, la.subject, la.body_text, la.body_source, la.body_attempts, "
             "la.lemlist_team_id, la.lemlist_contact_id, la.lemlist_lead_id, la.send_user_id, la.send_user_email, la.send_user_mailbox_id, la.send_user_name")


def _reply_view(conn: Conn, r: dict[str, Any], person: dict[str, Any]) -> dict[str, Any]:
    sender = _sender(conn, r)
    contact, _ = _contact(conn, r)
    answered = q1(conn, "SELECT status, created_at, sent_at, created_by FROM lemlist_replies WHERE reply_to_activity_id=%s AND status IN ('sent','sending','unknown') "
                        "ORDER BY created_at DESC LIMIT 1", (r["id"],))
    # the server works with one Lemlist key, so one team: any activity that names it gives the link (the link only, never a sender)
    team = r.get("lemlist_team_id") or (q1(conn, "SELECT lemlist_team_id FROM lemlist_activities WHERE lemlist_team_id IS NOT NULL LIMIT 1") or {}).get("lemlist_team_id")
    return {"activity_id": r["id"], "at": r["happened_at"], "campaign": r.get("campaign_name"), "subject": r.get("subject"),
            "text": r.get("body_text"), "text_state": _text_state(r), "lemlist_url": lemlist_url(team, contact),
            "sender": {"name": sender.get("name"), "email": sender["email"]} if sender else None,
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


def thread(conn: Conn, person: dict[str, Any]) -> dict[str, Any]:
    """The emails between a person and us that Lemlist told us about (our campaign emails, their replies) and the replies sent from this app,
    oldest first, plus the reply the box answers (their newest)."""
    pid = str(person["id"])
    acts = qa(conn, f"SELECT {_ACT_COLS} FROM lemlist_activities la WHERE la.person_id=%s AND la.type IN ('emailsSent','emailsReplied') "
                    "ORDER BY la.happened_at DESC NULLS LAST LIMIT %s", (pid, THREAD_LIMIT))           # noqa: S608
    ours = qa(conn, "SELECT id, reply_to_activity_id, body_text, status, error, created_by, created_at, sent_at, send_user_email FROM lemlist_replies "
                    "WHERE person_id=%s ORDER BY created_at DESC LIMIT 30", (pid,))
    items: list[dict[str, Any]] = []
    for o in ours:
        items.append({"id": str(o["id"]), "kind": "answer", "at": o["sent_at"] or o["created_at"], "text": o["body_text"], "text_state": "full",
                      "status": o["status"], "error": o["error"] if o["status"] in ("failed", "unknown") else None, "by": o["created_by"],
                      "from": {"name": None, "email": o["send_user_email"]}, "reply_to": o["reply_to_activity_id"]})
    for a in acts:
        if a["type"] == SENT and not a.get("campaign_id") and any(_same(a.get("body_text"), o["body_text"]) for o in ours):
            continue                                               # our own reply, read back from the Lemlist inbox: shown once, from our record
        mine = a["type"] == SENT
        items.append({"id": a["id"], "kind": "sent" if mine else "reply", "at": a["happened_at"], "campaign": a.get("campaign_name"), "subject": a.get("subject"),
                      "text": a.get("body_text"), "text_state": _text_state(a) if not mine else ("full" if a.get("body_text") else "missing"),
                      "from": {"name": a.get("send_user_name"), "email": a.get("send_user_email")} if mine else {"name": person.get("full_name"), "email": None}})
    items.sort(key=lambda x: x["at"] or datetime.min.replace(tzinfo=timezone.utc))
    latest = next((a for a in acts if a["type"] == REPLIED), None)
    return {"items": items, "reply": _reply_view(conn, latest, person) if latest else None}


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
            return {"status": "sent", "id": str(prior["id"]), "repeat": True, "sent_at": prior["sent_at"]}
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
    row = conn.execute(
        "INSERT INTO lemlist_replies(idempotency_key, reply_to_activity_id, person_id, body_text, body_hash, send_user_id, send_user_email, send_user_mailbox_id, "
        "lemlist_contact_id, lemlist_lead_id, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (idempotency_key) DO UPDATE SET status='sending', error=NULL, body_text=EXCLUDED.body_text, body_hash=EXCLUDED.body_hash, created_at=now() "
        "WHERE lemlist_replies.status='failed' RETURNING id",
        (full_key, activity_id, pid, body, digest, sender["user_id"], sender["email"], sender["mailbox_id"], contact, lead, actor)).fetchone()
    if not row:
        raise ReplyError(409, "This reply is being sent. Wait a moment.")
    rid = str(row["id"])
    conn.commit()                                                  # the claim is visible to a second click before Lemlist is called
    status, error = "sent", None
    try:
        res = client.send_email(send_user_id=sender["user_id"], send_user_email=sender["email"], send_user_mailbox_id=sender["mailbox_id"],
                                message_html=to_html(body), reply_to_activity_id=activity_id, contact_id=contact, lead_id=None if contact else lead)
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
          detail={"activity_id": activity_id, "reply_id": rid, "chars": len(body), "status": status, "mailbox_id": sender["mailbox_id"]})
    conn.commit()
    if status != "sent":
        raise ReplyError(502 if status == "unknown" else 409, error or UNKNOWN)
    return {"status": "sent", "id": rid, "repeat": False}
