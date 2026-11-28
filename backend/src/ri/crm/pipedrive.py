"""Pipedrive adapters (spec 09 §4). Shape verified against a live read of the account on 2026-11-20 (API v2: data[],
additional_data.next_cursor, owner_id, update_time, is_deleted).

READ client and WRITE executor are separate classes with separate credentials. AI workers get neither; only
`execute_approved` (after validating an approval) may call the writer. Pipedrive owns owner_id / deal / stage - this
module never derives them from research.
"""
from __future__ import annotations

import base64
import hmac
import os
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterator

import httpx

from ..approvals import ApprovalError, mark_done, mark_executing, validate_for_execution
from ..audit import audit
from ..config import Settings
from ..db import Conn, jb, q1, qa
from ..identity.resolver import resolve
from ..sheets.client import ConnectorBlocked
from ..sheets.parsers import normalize_domain

MARKER = "[ri-idem:"


class CrmError(Exception):
    pass


class PipedriveReader:
    def __init__(self, domain: str | None, token: str | None, *, http: httpx.Client | None = None, retries: int = 4):
        if not (domain and token):
            raise ConnectorBlocked("PIPEDRIVE_DOMAIN / PIPEDRIVE_API_TOKEN not set: CRM sync is blocked (public research continues; ownership checks degrade)")
        self.base = f"https://{domain}.pipedrive.com/api/v2"
        self.http, self.token, self.retries = http or httpx.Client(timeout=30), token, retries

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._get_url(f"{self.base}{path}", params)

    def _get_url(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self.retries + 1):
            r = self.http.get(url, params=params, headers={"x-api-token": self.token})
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == self.retries:
                    raise CrmError(f"Pipedrive {r.status_code} after retries")
                time.sleep(min(float(r.headers.get("Retry-After", 2 ** attempt)), 30))
                continue
            if r.status_code in (401, 403):
                raise ConnectorBlocked(f"Pipedrive denied access ({r.status_code})")
            r.raise_for_status()
            body = r.json()
            if not isinstance(body, dict) or "data" not in body:
                raise CrmError("unexpected Pipedrive response shape (schema drift)")
            return body
        raise CrmError("unreachable")  # pragma: no cover

    def paged(self, path: str, *, updated_since: str | None = None, limit: int = 200) -> Iterator[dict[str, Any]]:
        cursor = None
        while True:
            params: dict[str, Any] = {"limit": limit, "include_fields": "next_activity_id", **({"cursor": cursor} if cursor else {}), **({"updated_since": updated_since} if updated_since else {})}
            body = self._get(path, {k: v for k, v in params.items() if k != "include_fields"})
            yield from body["data"] or []
            cursor = (body.get("additional_data") or {}).get("next_cursor")
            if not cursor:
                return

    def deal_flow(self, deal_id: int, *, page: int = 100) -> list[dict[str, Any]]:
        """A deal's change history. Only API v1 has it, one deal per call (there is no bulk endpoint); offset paging."""
        url, start, out = f"{self.base.replace('/api/v2', '/api/v1')}/deals/{int(deal_id)}/flow", 0, []
        while True:
            body = self._get_url(url, {"start": start, "limit": page})
            out.extend(body["data"] or [])
            pg = (body.get("additional_data") or {}).get("pagination") or {}
            if not pg.get("more_items_in_collection"):
                return out
            nxt = pg.get("next_start")
            start = int(nxt) if nxt is not None and int(nxt) > start else start + page


class PipedriveWriter:
    """Separate privileged credential. Idempotency: a marker in the activity note lets us reconcile before any retry."""

    def __init__(self, domain: str | None, token: str | None, *, http: httpx.Client | None = None):
        if not (domain and token):
            raise ConnectorBlocked("PIPEDRIVE_WRITE_TOKEN not set: CRM writes are blocked")
        self.base = f"https://{domain}.pipedrive.com/api/v2"
        self.http, self.token = http or httpx.Client(timeout=30), token

    def find_by_marker(self, org_crm_id: int, key: str) -> dict[str, Any] | None:
        r = self.http.get(f"{self.base}/activities", params={"org_id": org_crm_id, "limit": 100}, headers={"x-api-token": self.token})
        r.raise_for_status()
        for a in r.json().get("data") or []:
            if f"{MARKER}{key}]" in (a.get("note") or ""):
                return a
        return None

    def create_activity(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        body = {**payload, "note": f"{payload.get('note', '')}\n{MARKER}{key}]".strip()}
        r = self.http.post(f"{self.base}/activities", json=body, headers={"x-api-token": self.token})
        r.raise_for_status()
        return r.json().get("data") or {}

    def find_deal(self, org_crm_id: int, title: str) -> dict[str, Any] | None:
        r = self.http.get(f"{self.base}/deals", params={"org_id": org_crm_id, "status": "open", "limit": 100}, headers={"x-api-token": self.token})
        r.raise_for_status()
        for d in r.json().get("data") or []:
            if (d.get("title") or "").strip().lower() == title.strip().lower():
                return d
        return None

    def create_deal(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = self.http.post(f"{self.base}/deals", json=payload, headers={"x-api-token": self.token})
        r.raise_for_status()
        return r.json().get("data") or {}

    def add_note(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        body = {**payload, "content": f"{payload.get('content', '')}\n{MARKER}{key}]".strip()}
        r = self.http.post("https://" + self.base.split("/")[2] + "/api/v1/notes", json=body, headers={"x-api-token": self.token})
        r.raise_for_status()
        return r.json().get("data") or {}


# ------------------------------------------------------------------ mirror upserts
def _dt(v):
    return v or None


def upsert_org(conn: Conn, o: dict[str, Any]) -> str:
    """Mirror + link. Linking is by existing CRM id, then conservative resolver; ambiguity -> 'review' (owner stays Pipedrive's)."""
    if o.get("is_deleted"):
        conn.execute("DELETE FROM crm_organizations WHERE crm_id=%s", (o["id"],))
        return "deleted"
    existing = q1(conn, "SELECT organization_id, link_status FROM crm_organizations WHERE crm_id=%s", (o["id"],))
    org_id, status = (str(existing["organization_id"]), existing["link_status"]) if existing and existing["organization_id"] else (None, "unlinked")
    if org_id is None:
        d = resolve(conn, name=o["name"], country=None, domain=normalize_domain(o.get("website")), allow_create=False)
        if d.action == "auto_link":
            org_id, status = d.org_id, "linked"
        elif d.action == "review":
            status = "review"
    addr = (o.get("address") or {}).get("value") if isinstance(o.get("address"), dict) else None
    conn.execute(
        "INSERT INTO crm_organizations(crm_id, organization_id, name, owner_id, open_deals_count, address, raw, link_status, synced_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now()) "
        "ON CONFLICT (crm_id) DO UPDATE SET name=EXCLUDED.name, owner_id=EXCLUDED.owner_id, open_deals_count=EXCLUDED.open_deals_count, address=EXCLUDED.address, "
        "raw=EXCLUDED.raw, organization_id=COALESCE(crm_organizations.organization_id, EXCLUDED.organization_id), "
        "link_status=CASE WHEN crm_organizations.organization_id IS NOT NULL THEN 'linked' ELSE EXCLUDED.link_status END, synced_at=now()",
        (o["id"], org_id, o["name"], o.get("owner_id"), o.get("open_deals_count"), addr, jb({k: v for k, v in o.items() if k != "custom_fields"}), status))
    from .fields import apply_org_fields
    apply_org_fields(conn, o)              # client status, contract end, people at the client (fields the app set up)
    return status


def upsert_person(conn: Conn, p: dict[str, Any]) -> None:
    if p.get("is_deleted"):
        conn.execute("DELETE FROM crm_persons WHERE crm_id=%s", (p["id"],))
        return
    org_raw = p.get("org_id")
    crm_org = org_raw.get("value") if isinstance(org_raw, dict) else org_raw          # v1 sends {"value": id, ...}, v2 sends the id
    optout = any(e.get("label") == "opt_out" for e in (p.get("emails") or []) if isinstance(e, dict)) or bool(p.get("marketing_status") in {"unsubscribed", "do_not_contact"})
    conn.execute(
        "INSERT INTO crm_persons(crm_id, crm_org_id, name, owner_id, email_optout, suppressed, raw, synced_at) VALUES (%s,%s,%s,%s,%s,%s,%s,now()) "
        "ON CONFLICT (crm_id) DO UPDATE SET crm_org_id=EXCLUDED.crm_org_id, name=EXCLUDED.name, owner_id=EXCLUDED.owner_id, email_optout=EXCLUDED.email_optout, "
        "suppressed=EXCLUDED.suppressed OR crm_persons.suppressed, synced_at=now()",
        (p["id"], crm_org, p.get("name") or "", p.get("owner_id"), optout, optout, jb({"id": p["id"], "marketing_status": p.get("marketing_status")})))
    from ..people import link_crm_person
    link_crm_person(conn, p)          # the same person in the people database: by email, else at the linked account
    if optout:   # suppression propagates to canonical people: honoured across ALL downstream tools
        conn.execute("UPDATE people SET suppressed=true, suppression_reason='crm opt-out' WHERE id IN (SELECT person_id FROM crm_persons WHERE crm_id=%s AND person_id IS NOT NULL)", (p["id"],))


DEAL_STATUSES = ("open", "won", "lost")


def _ts(v: Any) -> datetime | None:
    """Pipedrive times: v2 sends ISO 8601 with Z, v1 sends 'YYYY-MM-DD HH:MM:SS' in UTC. Anything unreadable is None."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).strip().replace("Z", "+00:00")
    if "T" not in s and " " in s:
        s = s.replace(" ", "T", 1)
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _day(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _int(v: Any) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _add_transitions(conn: Conn, deal_id: int, rows: list[tuple[Any, ...]], *, pipeline_id: Any, value: Any, currency: Any) -> int:
    """rows: (from_stage, to_stage, from_status, to_status, at, source). Duplicates of an already logged change are ignored."""
    n = 0
    for fs, ts, fst, tst, at, src in rows:
        if ts is None or tst not in DEAL_STATUSES or at is None:
            continue
        n += conn.execute("INSERT INTO deal_stage_transitions(crm_deal_id, pipeline_id, from_stage_id, to_stage_id, from_status, to_status, at, source, value, currency) "
                          "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                          (deal_id, pipeline_id, fs, ts, fst, tst, at, src, value, currency)).rowcount or 0
    return n


def record_deal_change(conn: Conn, d: dict[str, Any], prev: dict[str, Any] | None, source: str, now: datetime | None = None) -> int:
    """Log what changed between the mirror's last copy of a deal (`prev`) and the copy just received. Several moves between two hourly
    syncs show as one; webhooks see each move. A deal met for the first time is logged as 'first_seen' (or 'created' when it never moved)."""
    stage, status = _int(d.get("stage_id")), d.get("status")
    if stage is None or status not in DEAL_STATUSES:
        return 0
    now = now or datetime.now(timezone.utc)
    add, moved, upd = _ts(d.get("add_time")), _ts(d.get("stage_change_time")), _ts(d.get("update_time"))
    closed_at = _ts(d.get("won_time") if status == "won" else d.get("lost_time") if status == "lost" else None) or upd or now
    logged = q1(conn, "SELECT 1 FROM deal_stage_transitions WHERE crm_deal_id=%s LIMIT 1", (d["id"],))
    rows: list[tuple[Any, ...]] = []
    if prev is None or not logged:
        rows.append((None, stage, None, "open", add, "created") if moved is None and add else (None, stage, None, "open", moved or upd or now, "first_seen"))
        if status != "open":
            rows.append((stage, stage, "open", status, closed_at, "first_seen"))
    else:
        p_stage, p_status = _int(prev.get("stage_id")), prev.get("status") or "open"
        if p_stage != stage:
            rows.append((p_stage, stage, p_status, p_status, moved or upd or now, source))
        if p_status != status:
            rows.append((stage, stage, p_status, status, closed_at if status != "open" else (upd or now), source))
    return _add_transitions(conn, d["id"], rows, pipeline_id=_int(d.get("pipeline_id")), value=d.get("value"), currency=d.get("currency"))


ACTIVITY_KEEP = ("id", "type", "owner_id", "deal_id", "org_id", "person_id", "lead_id", "done", "due_date", "due_time", "duration", "busy",
                 "add_time", "update_time", "marked_as_done_time")      # no note, participants or attendees: they carry personal text


def _ref(v: Any) -> Any:
    return v.get("value") if isinstance(v, dict) else v            # v1 sends {"value": id, ...}, v2 sends the id


def upsert_activity(conn: Conn, a: dict[str, Any]) -> None:
    """One Pipedrive activity into crm_activities. Deleted ones leave the mirror. Due time is UTC in Pipedrive; an activity with a date
    and no time is due at the start of that day."""
    if a.get("is_deleted") or a.get("active_flag") is False:
        conn.execute("DELETE FROM crm_activities WHERE crm_id=%s", (a["id"],))
        return
    day = _day(a.get("due_date"))
    due = None
    if day:
        hhmm = str(a.get("due_time") or "").strip()
        due = _ts(f"{day.isoformat()}T{hhmm}:00Z") if len(hhmm) == 5 else datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    done = bool(a.get("done"))
    conn.execute(
        "INSERT INTO crm_activities(crm_id, crm_org_id, crm_deal_id, crm_person_id, type, subject, done, due_at, done_at, owner_id, update_time, raw, synced_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) ON CONFLICT (crm_id) DO UPDATE SET crm_org_id=EXCLUDED.crm_org_id, crm_deal_id=EXCLUDED.crm_deal_id, "
        "crm_person_id=EXCLUDED.crm_person_id, type=EXCLUDED.type, subject=EXCLUDED.subject, done=EXCLUDED.done, due_at=EXCLUDED.due_at, done_at=EXCLUDED.done_at, "
        "owner_id=EXCLUDED.owner_id, update_time=EXCLUDED.update_time, raw=EXCLUDED.raw, synced_at=now()",
        (a["id"], _ref(a.get("org_id")), _ref(a.get("deal_id")), _ref(a.get("person_id")), a.get("type"), (a.get("subject") or "")[:500], done, due,
         _ts(a.get("marked_as_done_time")) if done else None, _ref(a.get("owner_id") or a.get("user_id")), _ts(a.get("update_time")),
         jb({k: a.get(k) for k in ACTIVITY_KEEP if k in a})))


def upsert_deal(conn: Conn, d: dict[str, Any], stages: dict[int, str] | None = None, *, source: str = "sync") -> None:
    if d.get("is_deleted"):
        conn.execute("DELETE FROM crm_deals WHERE crm_id=%s", (d["id"],))
        return
    prev = q1(conn, "SELECT stage_id, status FROM crm_deals WHERE crm_id=%s", (d["id"],))
    stage_name = (stages or {}).get(d.get("stage_id"))
    if stage_name is None and d.get("stage_id") is not None:
        row = q1(conn, "SELECT name FROM crm_stages WHERE crm_id=%s", (_int(d.get("stage_id")),))
        stage_name = row["name"] if row else None
    origin, channel = d.get("origin"), d.get("channel")
    conn.execute(
        "INSERT INTO crm_deals(crm_id, crm_org_id, title, owner_id, stage_id, stage_name, pipeline_id, status, value, currency, update_time, raw, synced_at, "
        "add_time, stage_change_time, won_time, lost_time, lost_reason, expected_close_date, origin, channel) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (crm_id) DO UPDATE SET crm_org_id=EXCLUDED.crm_org_id, title=EXCLUDED.title, owner_id=EXCLUDED.owner_id, "
        "stage_id=EXCLUDED.stage_id, stage_name=COALESCE(EXCLUDED.stage_name, crm_deals.stage_name), pipeline_id=COALESCE(EXCLUDED.pipeline_id, crm_deals.pipeline_id), "
        "status=EXCLUDED.status, value=EXCLUDED.value, currency=COALESCE(EXCLUDED.currency, crm_deals.currency), update_time=EXCLUDED.update_time, synced_at=now(), "
        "add_time=COALESCE(EXCLUDED.add_time, crm_deals.add_time), stage_change_time=COALESCE(EXCLUDED.stage_change_time, crm_deals.stage_change_time), "
        "won_time=CASE WHEN EXCLUDED.status='won' THEN COALESCE(EXCLUDED.won_time, crm_deals.won_time) END, "
        "lost_time=CASE WHEN EXCLUDED.status='lost' THEN COALESCE(EXCLUDED.lost_time, crm_deals.lost_time) END, "
        "lost_reason=CASE WHEN EXCLUDED.status='lost' THEN COALESCE(EXCLUDED.lost_reason, crm_deals.lost_reason) END, "
        "expected_close_date=EXCLUDED.expected_close_date, origin=COALESCE(EXCLUDED.origin, crm_deals.origin), channel=COALESCE(EXCLUDED.channel, crm_deals.channel)",
        (d["id"], d.get("org_id"), d.get("title") or "", d.get("owner_id"), d.get("stage_id"), stage_name, d.get("pipeline_id"),
         d.get("status"), d.get("value"), d.get("currency"), d.get("update_time"), jb({"id": d["id"]}),
         _ts(d.get("add_time")), _ts(d.get("stage_change_time")), _ts(d.get("won_time")), _ts(d.get("lost_time")), (d.get("lost_reason") or None),
         _day(d.get("expected_close_date")), str(origin)[:80] if origin not in (None, "") else None, str(channel)[:80] if channel not in (None, "") else None))
    record_deal_change(conn, d, prev, source)
    from .fields import apply_deal_fields
    apply_deal_fields(conn, d)             # deal type, billing model, forecast category, start, duration, people, qualification, skills
    src_field = os.environ.get("RI_PIPEDRIVE_SOURCE_FIELD")         # the key of the deal field that says where the deal came from
    if src_field:
        raw = d.get(src_field, (d.get("custom_fields") or {}).get(src_field))
        if isinstance(raw, dict):
            raw = raw.get("id", raw.get("value"))
        if raw is not None or src_field in d or src_field in (d.get("custom_fields") or {}):
            conn.execute("UPDATE crm_deals SET lead_source_raw=%s WHERE crm_id=%s", (None if raw in (None, "") else str(raw)[:80], d["id"]))
    field = os.environ.get("RI_PIPEDRIVE_COMPETITOR_FIELD")         # the key of a text field where sellers name the competitors on the deal
    if field and (d.get(field) or (d.get("custom_fields") or {}).get(field)):
        from ..competitors import from_pipedrive_deal
        from_pipedrive_deal(conn, d, field)


def sync_field_options(conn: Conn, reader: PipedriveReader, keys: list[str]) -> int:
    """Labels of the option fields we read (e.g. Lead source: 92 -> Marketing). Deal fields are listed by API v1."""
    want, n = set(keys), 0
    body = reader._get_url(f"{reader.base.replace('/api/v2', '/api/v1')}/dealFields", {"start": 0, "limit": 500})
    for f in body.get("data") or []:
        if f.get("key") in want:
            for o in f.get("options") or []:
                conn.execute("INSERT INTO crm_field_options(field_key, option_id, label, synced_at) VALUES (%s,%s,%s,now()) "
                             "ON CONFLICT (field_key, option_id) DO UPDATE SET label=EXCLUDED.label, synced_at=now()", (f["key"], str(o.get("id")), str(o.get("label") or "")))
                n += 1
    return n


def sync_stages(conn: Conn, reader: PipedriveReader) -> int:
    n = 0
    for s in reader.paged("/stages"):
        conn.execute("INSERT INTO crm_stages(crm_id, pipeline_id, name, order_nr, deal_probability, is_deleted, synced_at) VALUES (%s,%s,%s,%s,%s,%s,now()) "
                     "ON CONFLICT (crm_id) DO UPDATE SET pipeline_id=EXCLUDED.pipeline_id, name=EXCLUDED.name, order_nr=EXCLUDED.order_nr, "
                     "deal_probability=EXCLUDED.deal_probability, is_deleted=EXCLUDED.is_deleted, synced_at=now()",
                     (s["id"], _int(s.get("pipeline_id")), s.get("name") or f"Stage {s['id']}", _int(s.get("order_nr")), _int(s.get("deal_probability")),
                      bool(s.get("is_deleted") or s.get("active_flag") is False)))
        n += 1
    return n


def history_from_flow(deal: dict[str, Any], flow: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """Turn Pipedrive's deal flow (v1 `dealChange` items) into transition rows: the stage the deal was created in, then every stage
    and status change in time order. Field values arrive as strings."""
    changes = []
    for it in flow:
        if not isinstance(it, dict) or it.get("object") != "dealChange":
            continue
        x = it.get("data") or {}
        if x.get("field_key") not in ("stage_id", "status"):
            continue
        at = _ts(x.get("log_time") or it.get("timestamp"))
        if at:
            changes.append((at, _int(x.get("id")) or 0, x["field_key"], x.get("old_value"), x.get("new_value")))
    changes.sort(key=lambda c: (c[0], c[1]))
    first_stage = next((_int(c[3]) for c in changes if c[2] == "stage_id" and _int(c[3]) is not None), None) or _int(deal.get("stage_id"))
    first_status = next((c[3] for c in changes if c[2] == "status" and c[3] in DEAL_STATUSES), None) or "open"
    rows: list[tuple[Any, ...]] = []
    created = _ts(deal.get("add_time"))
    if created and first_stage is not None:
        rows.append((None, first_stage, None, first_status, created, "backfill"))
    stage, status = first_stage, first_status
    for at, _, key, _old, new in changes:
        if key == "stage_id" and _int(new) is not None and _int(new) != stage:
            rows.append((stage, _int(new), status, status, at, "backfill"))
            stage = _int(new)
        elif key == "status" and new in DEAL_STATUSES and new != status:
            rows.append((stage, stage, status, new, at, "backfill"))
            status = new
    return rows


def backfill_deal_history(conn: Conn, reader: PipedriveReader, *, limit: int = 200) -> dict[str, int]:
    """Read Pipedrive's history for deals not read yet, one call per deal. Each deal commits on its own, so a run can stop at any time."""
    done = added = 0
    for d in qa(conn, "SELECT crm_id, stage_id, status, pipeline_id, value, currency, add_time FROM crm_deals WHERE history_backfilled_at IS NULL ORDER BY crm_id LIMIT %s", (limit,)):
        flow = reader.deal_flow(d["crm_id"])
        with conn.transaction():
            added += _add_transitions(conn, d["crm_id"], history_from_flow(d, flow), pipeline_id=d["pipeline_id"], value=d["value"], currency=d["currency"])
            conn.execute("UPDATE crm_deals SET history_backfilled_at=now() WHERE crm_id=%s", (d["crm_id"],))
        conn.commit()
        done += 1
    left = q1(conn, "SELECT count(*) AS n FROM crm_deals WHERE history_backfilled_at IS NULL")
    return {"deals": done, "transitions": added, "remaining": int(left["n"]) if left else 0}


WEBHOOK_PATH = "/webhooks/pipedrive"


def ensure_deal_webhook(reader: PipedriveReader, public_url: str, password: str) -> dict[str, Any]:
    """Make sure Pipedrive sends every deal change to this app. The only write the read side makes: it registers where Pipedrive
    delivers events and never touches a CRM record. Idempotent: an existing webhook for the same address is kept as it is."""
    url = public_url.rstrip("/") + WEBHOOK_PATH
    v1 = reader.base.replace("/api/v2", "/api/v1")
    hooks = reader._get_url(f"{v1}/webhooks", {}).get("data") or []
    for h in hooks:
        if h.get("subscription_url") == url and h.get("event_object") in ("deal", "*") and h.get("is_active") not in (False, 0):
            return {"webhook": "present", "id": h.get("id")}
    r = reader.http.post(f"{v1}/webhooks", headers={"x-api-token": reader.token},
                         json={"subscription_url": url, "event_action": "*", "event_object": "deal", "version": "2.0",
                               "http_auth_user": "pipedrive", "http_auth_password": password})
    if r.status_code in (401, 403):
        raise ConnectorBlocked(f"Pipedrive refused to create the webhook ({r.status_code}): the token's user may not manage webhooks")
    r.raise_for_status()
    return {"webhook": "created", "id": (r.json().get("data") or {}).get("id")}


def sync_activities(conn: Conn, reader: PipedriveReader) -> int:
    """Activities changed since the watermark (all of them on the first run), committed with the watermark so a failure re-reads them."""
    wm = q1(conn, "SELECT watermark FROM sync_state WHERE system='pipedrive' AND entity='activities'")
    newest = wm["watermark"] if wm else None
    n = 0
    with conn.transaction():
        for rec in reader.paged("/activities", updated_since=newest):
            upsert_activity(conn, rec)
            n += 1
            if rec.get("update_time") and (newest is None or rec["update_time"] > newest):
                newest = rec["update_time"]
        conn.execute("INSERT INTO sync_state(system, entity, watermark) VALUES ('pipedrive','activities',%s) "
                     "ON CONFLICT (system, entity) DO UPDATE SET watermark=EXCLUDED.watermark", (newest,))
    conn.commit()
    return n


def sync_incremental(conn: Conn, reader: PipedriveReader, *, stages: dict[int, str] | None = None) -> dict[str, Any]:
    """Watermark-based; the watermark advances only after a durable commit. Periodic full reconcile repairs missed webhooks."""
    out: dict[str, Any] = {"organizations": 0, "persons": 0, "deals": 0}
    try:          # the stage list gives stage names and their order; a failure here must not stop the mirror
        with conn.transaction():
            out["stages"] = sync_stages(conn, reader)
        conn.commit()
    except ConnectorBlocked:
        raise
    except Exception as exc:  # noqa: BLE001
        out["stages_error"] = f"{type(exc).__name__}: {exc}"[:200]
    if os.environ.get("RI_PIPEDRIVE_SOURCE_FIELD"):
        try:
            with conn.transaction():
                out["field_options"] = sync_field_options(conn, reader, [os.environ["RI_PIPEDRIVE_SOURCE_FIELD"]])
            conn.commit()
        except ConnectorBlocked:
            raise
        except Exception as exc:  # noqa: BLE001
            out["field_options_error"] = f"{type(exc).__name__}: {exc}"[:200]
    from . import fields as pdf
    for name, fn in (("fields", lambda: pdf.ensure_fields(conn, reader)), ("users", lambda: pdf.sync_users(conn, reader)),
                     ("pipelines", lambda: pdf.sync_pipelines(conn, reader))):
        try:      # the forecast's own Pipedrive fields (created once, then only looked up) and user names; never stops the mirror
            with conn.transaction():
                res = fn()
            conn.commit()
            if name != "fields" or res.get("created") or res.get("missing"):
                out[name] = res
        except Exception as exc:  # noqa: BLE001  (a token that may not edit fields or list users still syncs deals)
            out[f"{name}_error"] = f"{type(exc).__name__}: {exc}"[:200]
    if stages is None:
        stages = {r["crm_id"]: r["name"] for r in qa(conn, "SELECT crm_id, name FROM crm_stages")}
    open_seen: list[int] = []
    for entity, path, fn in (("organizations", "/organizations", upsert_org), ("persons", "/persons", upsert_person),
                             ("deals", "/deals", lambda c, r: (upsert_deal(c, r, stages), r.get("status") == "open" and not r.get("is_deleted") and open_seen.append(r["id"])))):
        wm = q1(conn, "SELECT watermark FROM sync_state WHERE system='pipedrive' AND entity=%s", (entity,))
        newest = wm["watermark"] if wm else None
        since = newest
        # A deal field we start reading (source, competitor) is filled in on deals that may never change again: read every deal once.
        mapped = [r["field_key"] for r in qa(conn, "SELECT field_key FROM crm_field_map WHERE entity='deal'")]
        fields_mark = "deals_fields:" + ",".join(sorted(k for k in (os.environ.get("RI_PIPEDRIVE_SOURCE_FIELD"), os.environ.get("RI_PIPEDRIVE_COMPETITOR_FIELD"), *mapped) if k))
        full_pass = entity == "deals" and fields_mark != "deals_fields:" and not q1(conn, "SELECT 1 FROM sync_state WHERE system='pipedrive' AND entity=%s", (fields_mark,))
        if full_pass:
            since = None
            out["deals_full_pass"] = True
        for rec in reader.paged(path, updated_since=since):
            fn(conn, rec)
            out[entity] += 1
            if rec.get("update_time") and (newest is None or rec["update_time"] > newest):
                newest = rec["update_time"]
        conn.execute("INSERT INTO sync_state(system, entity, watermark) VALUES ('pipedrive',%s,%s) ON CONFLICT (system, entity) DO UPDATE SET watermark=EXCLUDED.watermark", (entity, newest))
        if full_pass:
            conn.execute("INSERT INTO sync_state(system, entity, watermark) VALUES ('pipedrive',%s,'done') ON CONFLICT DO NOTHING", (fields_mark,))
        conn.commit()
    try:          # calls, meetings and tasks: next step and last contact per deal. Its own step, so a problem here never stops the deal mirror
        out["activities"] = sync_activities(conn, reader)
    except ConnectorBlocked:
        raise
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        out["activities_error"] = f"{type(exc).__name__}: {exc}"[:200]
    if open_seen:
        try:      # roles, people, monthly rate and billing start, when sellers price deals with products
            with conn.transaction():
                out["deal_products"] = pdf.sync_deal_products(conn, reader, sorted(set(open_seen)))
            conn.commit()
        except ConnectorBlocked:
            raise
        except Exception as exc:  # noqa: BLE001
            out["deal_products_error"] = f"{type(exc).__name__}: {exc}"[:200]
    return out


# ------------------------------------------------------------------ webhooks (durable inbox, replay-safe)
def verify_basic_auth(header: str | None, expected_user: str, expected_secret: str) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        user, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
    except Exception:  # noqa: BLE001
        return False
    # bytes, not str: compare_digest raises on non-ASCII text, which an attacker could use to turn a 401 into a 500
    return hmac.compare_digest(user.encode(), expected_user.encode()) & hmac.compare_digest(pwd.encode(), expected_secret.encode())


def receive_webhook(conn: Conn, body: dict[str, Any]) -> tuple[str, bool]:
    """Acknowledge fast: persist to the inbox keyed by a stable id; duplicates are no-ops. Processing is separate."""
    meta = body.get("meta") or {}
    # One key per EVENT, the same for Pipedrive's retries of it. (webhook_id names the webhook itself: every event shares it.)
    ent, eid = meta.get("entity") or meta.get("object"), meta.get("entity_id") or meta.get("id")
    parts = (meta.get("webhook_id") or "", ent, eid, meta.get("action"), meta.get("timestamp_micro") or meta.get("timestamp"), meta.get("correlation_id") or "")
    key = ":".join(str(x) for x in parts)[:300]
    row = conn.execute("INSERT INTO crm_webhook_inbox(dedup_key, event, payload) VALUES (%s,%s,%s) ON CONFLICT (dedup_key) DO NOTHING RETURNING id",
                       (key, f"{meta.get('entity', '?')}.{meta.get('action', '?')}", jb(body))).fetchone()
    return key, bool(row)


def process_inbox(conn: Conn, limit: int = 100) -> dict[str, int]:
    done = bad = 0
    for ev in qa(conn, "SELECT * FROM crm_webhook_inbox WHERE status='received' ORDER BY received_at LIMIT %s", (limit,)):
        meta, data = (ev["payload"].get("meta") or {}), ev["payload"].get("data") or ev["payload"].get("current")
        ent = meta.get("entity") or meta.get("object")
        if ent == "deal" and meta.get("action") in ("delete", "deleted") and not isinstance(data, dict):
            prev = ev["payload"].get("previous")              # a deleted deal arrives without data: what it was is in `previous`
            eid = _int(meta.get("entity_id") or meta.get("id")) or (_int(prev.get("id")) if isinstance(prev, dict) else None)
            data = {**(prev if isinstance(prev, dict) else {}), "id": eid, "is_deleted": True} if eid else None
        try:
            with conn.transaction():
                if not isinstance(data, dict) or "id" not in data:
                    raise CrmError("webhook payload missing data.id (schema drift)")
                if ent == "organization":
                    if meta.get("action") == "delete":
                        data = {**data, "is_deleted": True}
                    upsert_org(conn, data)
                elif ent == "person":
                    upsert_person(conn, data)
                elif ent == "deal":
                    upsert_deal(conn, data, source="webhook")
                else:
                    raise CrmError(f"unsupported entity {ent}")
                conn.execute("UPDATE crm_webhook_inbox SET status='processed', processed_at=now() WHERE id=%s", (ev["id"],))
            done += 1
        except Exception as exc:  # noqa: BLE001
            conn.execute("UPDATE crm_webhook_inbox SET status='quarantined', error=%s, processed_at=now() WHERE id=%s", (str(exc)[:300], ev["id"]))
            bad += 1        # quarantined and alertable, never silently discarded
    return {"processed": done, "quarantined": bad}


# ------------------------------------------------------------------ approved write executor
@dataclass
class ExecResult:
    status: str
    remote_id: int | None = None
    reconciled: bool = False


def execute_approved(conn: Conn, approval_id: str, writer: PipedriveWriter | None, settings: Settings) -> ExecResult:
    """The ONLY path to a CRM mutation. Kill switch -> approval/payload/target validation -> single-use claim ->
    fetch-before-retry reconciliation -> write -> audit. AI workers cannot reach this with a forged/modified payload."""
    if not settings.crm_writes_enabled:
        raise ApprovalError("CRM writes are disabled in this environment (RI_CRM_WRITES_ENABLED=false)")
    a = q1(conn, "SELECT * FROM approvals WHERE id=%s", (approval_id,))
    if not a:
        raise ApprovalError("approval not found")
    validate_for_execution(conn, approval_id, kind="crm_write", target_system="pipedrive", target_id=a["target_id"], operation=a["operation"], payload=a["payload"])
    if a["operation"] not in ("create_activity", "create_deal", "add_note"):
        raise ApprovalError(f"operation {a['operation']} is not supported by the executor")
    if writer is None:
        raise ConnectorBlocked("no CRM writer configured")
    org = q1(conn, "SELECT crm_id, owner_id FROM crm_organizations WHERE crm_id=%s", (int(a["target_id"]),))
    if not org:
        raise ApprovalError("target CRM organisation unknown to the mirror: sync first")
    key = a["idempotency_key"]
    mark_executing(conn, approval_id, str(uuid.uuid4()))
    conn.commit()
    try:
        op = a["operation"]
        if op == "create_deal":
            prior = writer.find_deal(org["crm_id"], a["payload"]["title"])
        elif op == "create_activity":
            prior = writer.find_by_marker(org["crm_id"], key)          # a timed-out earlier attempt may have succeeded
        else:
            prior = None
        if prior:
            mark_done(conn, approval_id, True, {"reconciled": True, "remote_id": prior.get("id")})
            return ExecResult("executed", prior.get("id"), True)
        base = {**a["payload"], "org_id": org["crm_id"]}
        if op == "create_deal":
            res = writer.create_deal(base)
        elif op == "add_note":
            res = writer.add_note(base, key)
        else:
            res = writer.create_activity(base, key)
        mark_done(conn, approval_id, True, {"remote_id": res.get("id")})
        audit(conn, actor="system:executor", operation={"create_deal": "crm_deal_created", "add_note": "crm_note_added"}.get(op, "crm_activity_created"), record_type="crm_organization",
              record_id=str(org["crm_id"]), approval_id=approval_id, detail={"remote_id": res.get("id")})
        return ExecResult("executed", res.get("id"))
    except (httpx.TransportError, httpx.TimeoutException, ConnectorBlocked) as exc:
        # AMBIGUOUS outcome (the write may have landed): hand the approval back so a retry runs fetch-before-write reconciliation
        conn.execute("UPDATE approvals SET status='approved', result=%s WHERE id=%s AND status='executing'", (jb({"ambiguous": type(exc).__name__}), approval_id))
        conn.commit()          # the API rolls the request back on error; the retryable state must already be saved
        raise
    except Exception as exc:  # noqa: BLE001
        mark_done(conn, approval_id, False, {"error": type(exc).__name__})
        conn.commit()
        raise
