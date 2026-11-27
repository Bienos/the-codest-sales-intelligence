"""Bluedot meeting summaries, received by webhook and read tolerantly.

Bluedot documents that it can send webhooks but does not publish the format, so nothing here trusts one shape. A delivery is first kept RAW
(at most 20 KB, only the last 10 per person, so the parser can be matched to the real format), then read for the fields we care about:
  id          meeting_id, recording_id, call_id, id, uuid
  title       title, meeting_title, name, subject, topic
  summary     summary, ai_summary, notes, overview, tldr, recap, abstract, description  (text, a list of lines, or an object with text)
  start time  start_time, started_at, start, date, created_at, timestamp, ...           (ISO text, a bare date, or epoch seconds / milliseconds)
  attendees   attendees, participants, guests, invitees, members, emails, ...           (emails, "Name <email>", or objects with an email)
  link        url, link, share_url, recording_url, meeting_url, ...                     (http or https only)
Names are matched without caring about case, underscores or dashes (`meetingId`, `Meeting_ID` and `meeting-id` are the same), at the top level or
up to three levels down (`{"event": "...", "data": {"meeting": {...}}}`). One meeting interaction is stored for every attendee whose address
matches a person in our database (visibility 'team'); a delivery that matches nobody is still kept raw, marked as not handled, with a plain note."""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterator

from ..db import Conn, jb, qa
from . import store

MAX_RAW_BYTES = 20 * 1024          # a raw delivery is kept up to this size; a larger one is reduced to a marker
KEEP_EVENTS = 10                   # raw deliveries kept per person
GIST_CHARS = 600
MAX_DEPTH = 3                      # how far below the top of a delivery a field is looked for
MAX_ATTENDEES = 100
MAX_DELIVERIES = 20                # a delivery that carries a batch of meetings: at most this many are read
MAX_DICTS = 300                    # objects looked at inside one meeting, and keys looked at inside one object: a hostile or huge delivery cannot make reading slow
MAX_KEYS = 300

NOTE_NO_MATCH = "no attendee matched a contact in our database"
NOTE_NO_ATTENDEES = "no attendees were found in this delivery"
NOTE_NOT_JSON = "the delivery was not JSON, so it was only kept as text"
NOTE_NOT_AN_OBJECT = "the delivery was not a meeting object"
NOTE_UNREADABLE = "the delivery could not be read; it was only kept"

# Field names, compared without case, underscores, dashes or spaces (see `_norm`). The order is the order of preference.
ID_SPECIFIC = ("meetingid", "recordingid", "callid", "sessionid", "conversationid")
ID_GENERIC = ("id", "uuid")
TITLE = ("title", "meetingtitle", "meetingname", "subject", "topic", "name")
SUMMARY = ("summary", "aisummary", "meetingsummary", "notes", "overview", "tldr", "recap", "abstract", "description")
START = ("starttime", "startedat", "start", "startat", "startdate", "meetingdate", "date", "datetime", "createdat", "scheduledat", "occurredat", "timestamp")
ATTENDEES = ("attendees", "participants", "guests", "invitees", "members", "attendeeemails", "participantemails", "emails", "people")
URL = ("url", "link", "shareurl", "recordingurl", "meetingurl", "weburl", "publicurl", "permalink", "appurl")
CONTAINERS = ("data", "payload", "body", "result", "results", "items", "events", "meetings", "recordings", "records")
TEXT_KEYS = ("text", "content", "value", "summary", "body", "markdown", "message")
PERSON_EMAIL_KEYS = ("email", "emailaddress", "mail", "useremail", "contactemail", "address")
# Objects that describe somebody or something other than the meeting: never searched for the meeting's own title or id.
PERSONISH = {"organizer", "host", "owner", "user", "creator", "author", "sender", "speaker", "speakers", "attendee", "participant", "account", "workspace", "team",
             "company", "organization", "org", "tenant", "from", "to", "bot", "recorder"}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-']+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")


@lru_cache(maxsize=8192)
def _norm(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


# ------------------------------------------------------------------ cleaning and trimming the raw delivery
def clean(obj: Any, depth: int = 0) -> Any:
    """JSON that Postgres will accept: no NUL characters, no NaN or infinity, bounded nesting."""
    if depth > 25:
        return str(obj)[:200]
    if isinstance(obj, dict):
        return {str(k).replace("\x00", ""): clean(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v, depth + 1) for v in obj]
    if isinstance(obj, str):
        return obj.replace("\x00", "")
    if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        return None
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    return str(obj)[:500]


def keep_raw(payload: Any) -> Any:
    """What is stored as the raw delivery: the delivery itself up to 20 KB, else a marker that says how big it was and which keys it had."""
    payload = clean(payload)
    size = len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    if size <= MAX_RAW_BYTES:
        return payload
    keys = list(payload.keys())[:40] if isinstance(payload, dict) else None
    return {"_trimmed": True, "original_bytes": size, "limit_bytes": MAX_RAW_BYTES, "top_level_keys": keys,
            "note": "This delivery was larger than 20 KB, so only this marker was kept."}


# ------------------------------------------------------------------ reading a delivery
def deliveries(payload: Any) -> list[dict[str, Any]]:
    """The meeting objects in a delivery: the delivery itself, or each object of a batch (a top-level list, or a list under data/items/meetings...)."""
    if isinstance(payload, list):
        return [d for d in payload if isinstance(d, dict)][:MAX_DELIVERIES]
    if not isinstance(payload, dict):
        return []
    for key, value in list(payload.items())[:MAX_KEYS]:
        if _norm(key) in CONTAINERS and isinstance(value, list) and value and all(isinstance(v, dict) for v in value) and not _has_any(payload, ATTENDEES + SUMMARY):
            return list(value)[:MAX_DELIVERIES]
    return [payload]


def _has_any(d: dict[str, Any], names: tuple[str, ...]) -> bool:
    return any(_norm(k) in names and v not in (None, "", [], {}) for k, v in list(d.items())[:MAX_KEYS])


def _dicts(root: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    """The delivery and the objects inside it, shallowest first, never entering an object that describes a person or an account."""
    out, layer = [(0, root)], [root]
    for depth in range(1, MAX_DEPTH + 1):
        nxt = []
        for d in layer:
            for key, value in list(d.items())[:MAX_KEYS]:
                if isinstance(value, dict) and _norm(key) not in PERSONISH and len(out) < MAX_DICTS:
                    nxt.append(value)
                    out.append((depth, value))
        layer = nxt
    return out


def _nmap(d: dict[str, Any], cache: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    """An object's non-empty values by normalised key (the first of two keys that normalise alike wins)."""
    if cache is not None and id(d) in cache:
        return cache[id(d)]
    m: dict[str, Any] = {}
    for k, v in list(d.items())[:MAX_KEYS]:
        if v not in (None, "", [], {}):
            m.setdefault(_norm(k), v)
    if cache is not None:
        cache[id(d)] = m
    return m


def _lookup(d: dict[str, Any], name: str, cache: dict[int, dict[str, Any]] | None = None) -> Iterator[Any]:
    value = _nmap(d, cache).get(name)
    if value is not None:
        yield value


def _pick(names: tuple[str, ...], scope: dict[str, Any], all_dicts: list[tuple[int, dict[str, Any]]], accept, cache: dict[int, dict[str, Any]]) -> Any:
    """The first acceptable value: the meeting's own object first (names in order of preference), then the other objects, shallowest first."""
    for d in [scope] + [x for _, x in all_dicts if x is not scope]:
        for name in names:
            for value in _lookup(d, name, cache):
                got = accept(value)
                if got is not None:
                    return got
    return None


def _scope(all_dicts: list[tuple[int, dict[str, Any]]], cache: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """The object that most looks like the meeting itself (most of the known kinds of field), the shallowest on a tie."""
    best, best_score = all_dicts[0][1], -1
    for _, d in all_dicts:
        m = _nmap(d, cache)
        score = sum(1 for group in (TITLE, SUMMARY, START, ATTENDEES, URL) if any(name in m for name in group))
        if score > best_score:
            best, best_score = d, score
    return best


def text_of(value: Any, depth: int = 0) -> str | None:
    """Plain text from a string, a list of strings or an object with text (or with several text parts)."""
    if isinstance(value, str):
        t = re.sub(r"\s+", " ", value).strip()
        return t or None
    if depth > 3:
        return None
    if isinstance(value, (list, tuple)):
        parts = [t for t in (text_of(v, depth + 1) for v in value[:50]) if t]
        return "; ".join(parts) or None
    if isinstance(value, dict):
        for key in TEXT_KEYS:
            for v in _lookup(value, key):
                t = text_of(v, depth + 1)
                if t:
                    return t
        parts = [t for t in (text_of(v, depth + 1) for v in list(value.values())[:20]) if t]
        return "; ".join(parts) or None
    return None


def trim_text(text: str | None, limit: int = GIST_CHARS) -> str | None:
    """At most `limit` characters, cut at a word where possible, with an ellipsis when something was left out."""
    if not text:
        return None
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:.") + "…"


def when_of(value: Any, depth: int = 0) -> datetime | None:
    """ISO text, a bare date, an RFC 2822 date, or epoch seconds or milliseconds (as a number or text)."""
    if isinstance(value, bool) or value is None:
        return None
    dt: datetime | None = None
    if isinstance(value, dict) and depth < 2:
        for key in ("datetime", "dateTime", "iso", "utc", "date", "value", "start"):
            for v in _lookup(value, _norm(key)):
                got = when_of(v, depth + 1)
                if got:
                    return got
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\s*\d{9,14}(\.\d+)?\s*", value)):
        try:
            n = float(value)
            dt = datetime.fromtimestamp(n / 1000 if n > 1e11 else n, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" UTC", "+00:00"))
        except ValueError:
            try:
                dt = parsedate_to_datetime(s)
            except (TypeError, ValueError, IndexError):
                return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt if 2000 <= dt.year <= 2100 else None


def safe_url(value: Any) -> str | None:
    u = value.strip() if isinstance(value, str) else ""
    return u[:500] if re.match(r"^https?://[^\s]+$", u, re.I) else None


def _id_of(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) or (isinstance(value, float) and value == int(value)):
        return str(int(value))
    if isinstance(value, str) and value.strip() and len(value.strip()) <= 200:
        return value.strip().replace(":", "-")           # ':' separates the meeting from the person in our own key
    return None


def _title_of(value: Any) -> str | None:
    t = text_of(value) if isinstance(value, str) else None
    return t[:300] if t else None


def people_of(value: Any, depth: int = 0) -> list[str]:
    """The email addresses an attendee field names: a text of addresses or 'Name <email>' entries, objects with an email, or lists and groups of those."""
    out: list[str] = []
    if depth > MAX_DEPTH + 1:
        return out
    if isinstance(value, str):
        out.extend(m.group(0).lower() for m in EMAIL_RE.finditer(value[:5000]))
    elif isinstance(value, (list, tuple)):
        for v in value[:300]:
            out.extend(people_of(v, depth + 1))
    elif isinstance(value, dict):
        email = next((m.group(0).lower() for k in PERSON_EMAIL_KEYS for v in _lookup(value, k) if isinstance(v, str) for m in [EMAIL_RE.search(v)] if m), None)
        if email:
            out.append(email)
        else:
            out.extend(k.lower() for k in list(value)[:50] if isinstance(k, str) and EMAIL_RE.fullmatch(k.strip()))      # {"ada@x.com": {...}}
            for v in list(value.values())[:50]:
                if isinstance(v, (dict, list, tuple)):
                    out.extend(people_of(v, depth + 1))
    return list(dict.fromkeys(out))[:MAX_ATTENDEES]


def parse_meeting(d: dict[str, Any]) -> dict[str, Any]:
    """The meeting's fields; anything not found is None (attendees: an empty list)."""
    all_dicts, cache = _dicts(d), {}
    scope = _scope(all_dicts, cache)
    mid = _pick(ID_SPECIFIC, scope, all_dicts, _id_of, cache) or _pick(ID_GENERIC, scope, all_dicts, _id_of, cache)
    attendees = _pick(ATTENDEES, scope, all_dicts, lambda v: people_of(v) or None, cache) or []
    return {"id": mid, "title": _pick(TITLE, scope, all_dicts, _title_of, cache), "summary": trim_text(_pick(SUMMARY, scope, all_dicts, text_of, cache)),
            "start": _pick(START, scope, all_dicts, when_of, cache), "attendees": attendees, "url": _pick(URL, scope, all_dicts, safe_url, cache)}


# ------------------------------------------------------------------ storing
def _payload_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:20]


def _store_meeting(conn: Conn, user_id: str, d: dict[str, Any], now: datetime) -> dict[str, Any]:
    m = parse_meeting(d)
    out = {"attendees": len(m["attendees"]), "matched": 0, "created": 0, "updated": 0, "skipped": 0, "note": None}
    if not m["attendees"]:
        out["note"] = NOTE_NO_ATTENDEES
        return out
    emails = m["attendees"]
    people = qa(conn, "SELECT DISTINCT ON (lower(work_email)) id, lower(work_email) AS email, suppressed FROM people "
                      "WHERE lower(work_email) = ANY(%s) AND erased_at IS NULL ORDER BY lower(work_email), created_at", (emails,))
    usable = [p for p in people if not p["suppressed"]]
    out["skipped"] = len(people) - len(usable)
    if not usable:
        out["note"] = NOTE_NO_MATCH
        return out
    mid = m["id"] or _payload_hash(d)
    occurred = m["start"] or now
    orgs = {str(r["person_id"]): str(r["organization_id"]) for r in qa(conn,
            "SELECT DISTINCT ON (person_id) person_id, organization_id FROM employment WHERE person_id = ANY(%s::uuid[]) "
            "ORDER BY person_id, (valid_to IS NULL) DESC, observed_at DESC NULLS LAST", ([str(p["id"]) for p in usable],))}
    keys = [f"{mid}:{p['id']}" for p in usable]
    before = {r["external_id"]: r for r in qa(conn, "SELECT external_id, title, gist FROM contact_interactions WHERE user_id=%s AND channel='meeting' AND external_id = ANY(%s)", (user_id, keys))}
    for p, key in zip(usable, keys):
        old = before.get(key) or {}
        new = store.upsert_interaction(conn, user_id=user_id, channel="meeting", external_id=key, occurred_at=occurred, visibility="team", person_id=str(p["id"]),
                                       organization_id=orgs.get(str(p["id"])), direction="both", title=m["title"] or old.get("title"), gist=m["summary"] or old.get("gist"),
                                       meta={"url": m["url"]} if m["url"] else {})
        out["created" if new else "updated"] += 1
        out["matched"] += 1
    if not m["start"]:
        out["note"] = "no start time was found, so the time the delivery arrived was used"
    return out


def handle_delivery(conn: Conn, user_id: str, payload: Any) -> dict[str, Any]:
    """Keep the raw delivery, read it, and store one meeting interaction per attendee we know. Never raises on an odd payload.
    Returns {"handled": bool, "note": str|None, "matched": n, "created": n, "updated": n, "meetings": n}; `handled` is true when somebody matched."""
    now = datetime.now(timezone.utc)
    payload = clean(payload)                           # no NUL characters, NaN or absurd nesting reach the parser or the database
    result: dict[str, Any] = {"handled": False, "note": None, "matched": 0, "created": 0, "updated": 0, "meetings": 0}
    notes: list[str] = []
    try:
        with conn.transaction():                       # a failure rolls back only this reading, never the connection's other work
            items = deliveries(payload)
            if not items:
                notes.append(NOTE_NOT_AN_OBJECT)
            for d in items:
                r = _store_meeting(conn, user_id, d, now)
                result["meetings"] += 1
                for k in ("matched", "created", "updated"):
                    result[k] += r[k]
                if r["note"]:
                    notes.append(r["note"])
    except Exception as exc:  # noqa: BLE001 - an unreadable delivery is kept raw and reported, never an error to Bluedot
        result.update({"matched": 0, "created": 0, "updated": 0})
        notes = [f"{NOTE_UNREADABLE} ({type(exc).__name__})"]
    result["handled"] = result["matched"] > 0
    result["note"] = "; ".join(dict.fromkeys(notes))[:300] or None
    if result["handled"] and result["note"] and NOTE_NO_MATCH in result["note"]:
        result["note"] = result["note"].replace(NOTE_NO_MATCH, "").strip("; ") or None          # one meeting of a batch matched: the note is about the others
    store_raw(conn, user_id, payload, handled=result["handled"], note=result["note"])
    return result


def store_raw(conn: Conn, user_id: str, payload: Any, *, handled: bool = False, note: str | None = None) -> None:
    """Keep one raw delivery (trimmed to 20 KB) and only the last 10 per person."""
    conn.execute("INSERT INTO connection_events(user_id, provider, received_at, payload, handled, note) VALUES (%s,'bluedot',clock_timestamp(),%s,%s,%s)",
                 (user_id, jb(keep_raw(payload)), handled, (note or "")[:300] or None))
    conn.execute("DELETE FROM connection_events WHERE user_id=%s AND provider='bluedot' AND id NOT IN "
                 "(SELECT id FROM connection_events WHERE user_id=%s AND provider='bluedot' ORDER BY received_at DESC, id DESC LIMIT %s)", (user_id, user_id, KEEP_EVENTS))


def recent_events(conn: Conn, user_id: str, limit: int = KEEP_EVENTS) -> list[dict[str, Any]]:
    rows = qa(conn, "SELECT received_at, handled, note, payload FROM connection_events WHERE user_id=%s AND provider='bluedot' ORDER BY received_at DESC, id DESC LIMIT %s",
              (user_id, limit))
    return [{"received_at": r["received_at"].isoformat(), "handled": bool(r["handled"]), "note": r["note"], "payload": r["payload"]} for r in rows]
