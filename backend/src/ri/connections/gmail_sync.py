"""Gmail sync: what each seller's own mailbox tells us about people who are ALREADY in our database.

For one person (job payload `{"user_id": ...}`) it looks up conversations with the contacts we hold an address for, and stores one
`contact_interactions` row per message: subject, Gmail's own short snippet, direction (in / out / both) and date. Nothing else:
  * read-only and metadata only (no bodies, no attachments), nothing is ever sent to an AI;
  * unrelated mail is never even opened: Gmail is searched for our contacts' addresses and only those hits are read;
  * colleagues (the seller's own domain), erased people and suppressed or unsubscribed addresses are never looked up or stored;
  * the subject and snippet are visible to the owner only unless they switched on "share the gist of my emails" (`store.email_visibility`).

The sync position lives in `user_connections.cursor`. A "pass" checks every eligible contact once. The first pass looks back 90 days; later
passes start a day before the previous pass began. Once a week a pass looks back 90 days again, so a contact added later gets their history.
Each run does at most MAX_CONTACTS_PER_RUN contacts (in groups, one Gmail search per group) and carries on from the cursor next run.
Messages already stored are never read again, so a repeat run costs only the searches."""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .. import vault
from ..db import Conn, q1, qa
from ..jobs.queue import enqueue
from ..people import email_domain, org_for_email
from . import gmail_api, store
from .gmail_api import GmailAuthError, GmailConfigError, GmailError, GmailNotFound, GmailRateLimited, GmailUnavailable, MessageMeta

log = logging.getLogger("ri.connections.gmail")

PROVIDER = "gmail"
LOOKBACK_DAYS = 90
SAFETY = timedelta(days=1)                 # later passes start this long before the previous pass began
DEEP_EVERY = timedelta(days=7)             # a full look-back again this often (picks up contacts added after the first pass)
GROUP_SIZE = 10                            # addresses per Gmail search
MAX_CONTACTS_PER_RUN = 200                 # addresses checked per run (= 20 searches); the rest waits for the next run
MAX_MESSAGES_PER_RUN = 150                 # messages read per run
MAX_LIST_PAGES = 10                        # search result pages per group (100 messages each)
TITLE_MAX, GIST_MAX = 300, 200
NO_SUBJECT = "(no subject)"
IN_FLIGHT = ("scheduled", "retryable_failed", "claimed", "fetching", "parsing", "verifying", "committing")
SAFE_ADDRESS = re.compile(r"^[a-z0-9][a-z0-9._-]*@[a-z0-9-]+(\.[a-z0-9-]+)+$")     # nothing that could change a Gmail search (spaces, quotes, brackets, colons)

AUTH_MESSAGE = "Google no longer accepts this connection (access was removed, expired or the password changed). Connect Gmail again."


# ---------------------------------------------------------------- addresses
def _split(addr: str | None) -> tuple[str, str] | None:
    a = (addr or "").strip().lower()
    if a.count("@") != 1:
        return None
    local, domain = a.split("@")
    local = local.split("+", 1)[0]                      # plus-addressing: jane+news@x is jane@x
    if domain == "googlemail.com":
        domain = "gmail.com"
    return (local, domain) if local and domain else None


def match_key(addr: str | None) -> str | None:
    """The form two addresses are compared in: lower case, no +tag, googlemail = gmail, dots ignored on gmail.com (as Gmail does)."""
    s = _split(addr)
    if not s:
        return None
    local, domain = s
    return f"{local.replace('.', '') if domain == 'gmail.com' else local}@{domain}"


def query_form(addr: str | None) -> str | None:
    """The address as it goes into a Gmail search; None unless it is plainly safe to put there."""
    s = _split(addr)
    form = f"{s[0]}@{s[1]}" if s else ""
    return form if SAFE_ADDRESS.match(form) else None


def build_query(addresses: list[str], after: date) -> str:
    group = " OR ".join(addresses)
    return f"(from:({group}) OR to:({group}) OR cc:({group})) after:{after:%Y/%m/%d}"


# ---------------------------------------------------------------- who we look for
@dataclass(frozen=True)
class Contact:
    person_id: str
    organization_id: str | None
    query: str
    key: str


def load_contacts(conn: Conn, own_domains: set[str], own_keys: set[str]) -> list[Contact]:
    """People with an address who may be written about: not erased, not suppressed (people, Pipedrive mirror) and not unsubscribed or bounced
    in a campaign; colleagues and the seller's own addresses left out. One entry per address (the oldest person record wins)."""
    rows = qa(conn,
              "SELECT p.id AS person_id, p.work_email, "
              "  (SELECT e.organization_id FROM employment e WHERE e.person_id=p.id ORDER BY (e.valid_to IS NULL) DESC, e.observed_at DESC LIMIT 1) AS organization_id "
              "FROM people p WHERE p.erased_at IS NULL AND NOT p.suppressed AND position('@' in p.work_email) > 1 "
              "AND NOT EXISTS (SELECT 1 FROM crm_persons cp WHERE cp.person_id=p.id AND cp.suppressed) "
              "AND NOT EXISTS (SELECT 1 FROM outbound_enrolments oe WHERE oe.status IN ('unsubscribed','bounced') AND lower(trim(oe.email)) = lower(trim(p.work_email))) "
              "ORDER BY p.created_at, p.id")
    out: dict[str, Contact] = {}
    for r in rows:
        key, query = match_key(r["work_email"]), query_form(r["work_email"])
        if not key or not query or key in out or key in own_keys:
            continue
        s = _split(r["work_email"])
        if s and s[1] in own_domains:
            continue
        out[key] = Contact(str(r["person_id"]), str(r["organization_id"]) if r["organization_id"] else None, query, key)
    return list(out.values())


def classify(m: MessageMeta, index: dict[str, Contact], own_keys: set[str]) -> tuple[Contact, str] | None:
    """Which contact a message is about and which way it went. 'out': the seller wrote it to the contact. 'in': the contact wrote it.
    'both': someone else wrote it and the seller and the contact are both on it. None: no known contact on it (or a draft)."""
    if "DRAFT" in m.labels:
        return None
    senders = [k for k in (match_key(a) for a in m.sender) if k]
    others = [k for k in (match_key(a) for a in (*m.to, *m.cc)) if k]
    if "SENT" in m.labels or any(k in own_keys for k in senders):
        return next(((index[k], "out") for k in others if k in index), None)
    hit = next((k for k in senders if k in index), None)
    if hit:
        return index[hit], "in"
    return next(((index[k], "both") for k in others if k in index), None)


# ---------------------------------------------------------------- helpers
def _ts(value: Any) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(value))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _clean(text: str, limit: int) -> str:
    return text.replace("\x00", "")[:limit]


def queue_sync(conn: Conn, user_id: str, *, now: datetime | None = None) -> bool:
    """Queue a sync for this person right now (after connecting). Nothing is added while an earlier sync still waits or runs; the same minute is the same job."""
    now = now or datetime.now(timezone.utc)
    if q1(conn, "SELECT 1 FROM jobs WHERE kind='gmail_sync' AND payload->>'user_id'=%s AND status = ANY(%s) LIMIT 1", (str(user_id), list(IN_FLIGHT))):
        return False
    _, created = enqueue(conn, "gmail_sync", {"user_id": str(user_id)}, idempotency_key=f"usersync:gmail:{user_id}:first:{now:%Y%m%d%H%M}", max_attempts=2,
                         correlation_id="first-connection")
    return created


def revoke_for(conn: Conn, vault_key: str | None, user_id: str) -> bool:
    """For a disconnect: ask Google to forget this person's refresh token. Best effort, never raises; call it BEFORE `store.disconnect` (which forgets the token)."""
    try:
        token = store.secret(conn, vault_key, user_id, PROVIDER)
    except vault.VaultError:
        return False
    return gmail_api.revoke_token(token) if token else False


def _known(conn: Conn, user_id: str, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    return {r["external_id"] for r in qa(conn, "SELECT external_id FROM contact_interactions WHERE user_id=%s AND channel='email' AND external_id = ANY(%s)", (user_id, ids))}


def _fail(conn: Conn, user_id: str, now: datetime, message: str, out: dict[str, Any]) -> dict[str, Any]:
    """A problem a person must look at: the connection shows 'needs attention' with this plain message."""
    store.record_sync(conn, user_id, PROVIDER, now, error=message)
    conn.commit()
    log.warning("gmail sync needs attention user=%s", user_id)
    return {**out, "needs_attention": True, "error": message}


# ---------------------------------------------------------------- the job
def run(conn: Conn, job: dict[str, Any], *, settings: Any, now: datetime | None = None) -> dict[str, Any]:
    """Background job `gmail_sync` for `job["payload"]["user_id"]`. Commits as it goes. Returns counts: contacts checked, messages seen, new interactions,
    and `more` = True when it stopped early (limit, Google asked us to slow down, more contacts wait for the next run)."""
    now = now or datetime.now(timezone.utc)
    out: dict[str, Any] = {"contacts_checked": 0, "messages_seen": 0, "new_interactions": 0, "more": False}
    uid = str(((job or {}).get("payload") or {}).get("user_id") or "")
    try:
        uuid.UUID(uid)
    except ValueError:
        return {**out, "skipped": "no user in the job"}
    user = q1(conn, "SELECT email, active FROM users WHERE id=%s", (uid,))
    row = store.get(conn, uid, PROVIDER)
    if not user or not user["active"] or not row or row["status"] == "disconnected" or not row.get("secret_enc"):
        return {**out, "skipped": "not connected"}

    if not vault.available(settings.vault_key):
        return _fail(conn, uid, now, "Gmail is paused: the server has no encryption key for personal connections. An administrator needs to set it.", out)
    if not (settings.google_client_id and settings.google_client_secret):
        return _fail(conn, uid, now, "Gmail is paused: the Google client id or secret is not set on the server. An administrator needs to add it.", out)
    try:
        token = store.secret(conn, settings.vault_key, uid, PROVIDER)
    except vault.VaultError:
        return _fail(conn, uid, now, "We could not open the saved Gmail permission. Connect Gmail again.", out)
    if not token:
        return {**out, "skipped": "not connected"}

    label = (row.get("account_label") or "").strip().lower()
    cur: dict[str, Any] = dict(row.get("cursor") or {})
    if cur.get("account") != label:                       # first run, or another Gmail account was connected: look back afresh
        cur = {"account": label}
    own_keys = {k for k in (match_key(label), match_key(user["email"])) if k}
    own_domains = {d for d in (email_domain(label), email_domain(user["email"]), (settings.allowed_email_domain or "").strip().lower() or None) if d}

    # where this pass stands
    if cur.get("pass_start"):                              # a pass is in progress: same window, carry on after the last group done
        pass_start, after = _ts(cur["pass_start"]) or now, _ts(cur.get("pass_after")) or now - timedelta(days=LOOKBACK_DAYS)
        deep, last = bool(cur.get("pass_deep")), str(cur.get("last") or "")
    else:
        done, deep_at = _ts(cur.get("synced_through")), _ts(cur.get("deep_at"))
        deep = done is None or deep_at is None or now - deep_at >= DEEP_EVERY
        pass_start = now
        after = now - timedelta(days=LOOKBACK_DAYS) if deep else (done - SAFETY)             # type: ignore[operator]
        last = ""
    after_day = after.date()

    contacts = load_contacts(conn, own_domains, own_keys)
    index = {c.key: c for c in contacts}
    queries = sorted({c.query for c in contacts})
    todo = [a for a in queries if a > last]
    batch = todo[:MAX_CONTACTS_PER_RUN]
    groups = [batch[i:i + GROUP_SIZE] for i in range(0, len(batch), GROUP_SIZE)]
    conn.commit()                                          # nothing open while we wait for Google

    def state(finished: bool) -> dict[str, Any]:
        base = {"account": label, "synced_through": cur.get("synced_through"), "deep_at": cur.get("deep_at")}
        if finished:
            return {**base, "synced_through": pass_start.isoformat(), "deep_at": pass_start.isoformat() if deep else cur.get("deep_at")}
        return {**base, "pass_start": pass_start.isoformat(), "pass_after": after.isoformat(), "pass_deep": deep, "last": last}

    stopped: str | None = None
    fetched = bad = known_skipped = unmatched = 0
    org_by_address: dict[str, str | None] = {}
    try:
        with gmail_api.GmailClient(token, settings.google_client_id, settings.google_client_secret) as gm:
            for group in groups:
                if fetched >= MAX_MESSAGES_PER_RUN:
                    stopped = "message limit"
                    break
                try:
                    ids, _more = gm.list_message_ids(build_query(group, after_day), max_pages=MAX_LIST_PAGES)
                except (GmailRateLimited, GmailUnavailable) as exc:
                    stopped = "Google asked us to slow down" if isinstance(exc, GmailRateLimited) else "Google could not be reached"
                    break
                except (GmailAuthError, GmailConfigError):
                    raise
                except GmailError:
                    ids, bad = [], bad + 1                                   # a search Google will not take: skip this group rather than stall on it for ever
                out["messages_seen"] += len(ids)
                known = _known(conn, uid, ids)
                known_skipped += sum(1 for i in ids if i in known)
                complete, metas = True, []
                for mid in (i for i in ids if i not in known):
                    if fetched >= MAX_MESSAGES_PER_RUN:
                        complete, stopped = False, "message limit"
                        break
                    fetched += 1
                    try:
                        metas.append(gm.get_metadata(mid))
                    except GmailNotFound:
                        bad += 1                                         # deleted since the search
                    except (GmailRateLimited, GmailUnavailable) as exc:
                        complete = False
                        stopped = "Google asked us to slow down" if isinstance(exc, GmailRateLimited) else "Google could not be reached"
                        break
                    except (GmailAuthError, GmailConfigError):
                        raise
                    except (GmailError, ValueError):
                        bad += 1                                         # one odd message never stops the rest
                live = store.get(conn, uid, PROVIDER)                    # disconnected while we were reading? then store nothing more
                if not live or live["status"] == "disconnected":
                    conn.rollback()
                    return {**out, "stopped": "disconnected", "more": False}
                vis = store.email_visibility(live.get("settings"))
                for m in metas:
                    hit = classify(m, index, own_keys)
                    if not hit:
                        unmatched += 1
                        continue
                    contact, direction = hit
                    org = contact.organization_id
                    if org is None:
                        if contact.query not in org_by_address:
                            org_by_address[contact.query] = org_for_email(conn, contact.query)
                        org = org_by_address[contact.query]
                    try:
                        with conn.transaction():                          # a savepoint: one row that the database refuses never spoils the others
                            if store.upsert_interaction(conn, user_id=uid, channel="email", external_id=m.id, occurred_at=m.occurred_at, visibility=vis,
                                                        person_id=contact.person_id, organization_id=org, direction=direction,
                                                        title=_clean(m.subject, TITLE_MAX) or NO_SUBJECT, gist=_clean(m.snippet, GIST_MAX) or None,
                                                        meta={"thread": m.thread_id} if m.thread_id else {}):
                                out["new_interactions"] += 1
                    except Exception:  # noqa: BLE001
                        bad += 1
                        log.warning("gmail sync: one message could not be stored user=%s (%s)", uid, "database")
                if complete:
                    out["contacts_checked"] += len(group)
                    last = group[-1]
                    store.record_sync(conn, uid, PROVIDER, now, cursor=state(False))
                conn.commit()
                if not complete:
                    break
    except GmailAuthError:
        conn.rollback()
        return _fail(conn, uid, now, AUTH_MESSAGE, out)
    except GmailConfigError as exc:
        conn.rollback()
        return _fail(conn, uid, now, str(exc), out)

    finished = stopped is None and len(todo) <= len(batch)
    store.record_sync(conn, uid, PROVIDER, now, cursor=state(finished))
    conn.commit()
    out["more"] = bool(stopped) or not finished
    out.update({"pass_complete": finished, "deep": deep, "already_stored": known_skipped, "no_contact_on_message": unmatched, "skipped_messages": bad,
                "contacts_known": len(queries)})
    if stopped:
        out["stopped"] = stopped
    log.info("gmail sync user=%s contacts=%d seen=%d new=%d more=%s", uid, out["contacts_checked"], out["messages_seen"], out["new_interactions"], out["more"])
    return out
