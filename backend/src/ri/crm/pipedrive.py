"""Pipedrive adapters (spec 09 §4). Shape verified against a live read of the account on 2026-11-20 (API v2: data[],
additional_data.next_cursor, owner_id, update_time, is_deleted).

READ client and WRITE executor are separate classes with separate credentials. AI workers get neither; only
`execute_approved` (after validating an approval) may call the writer. Pipedrive owns owner_id / deal / stage - this
module never derives them from research.
"""
from __future__ import annotations

import base64
import hmac
import time
import uuid
from dataclasses import dataclass
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
        for attempt in range(self.retries + 1):
            r = self.http.get(f"{self.base}{path}", params=params, headers={"x-api-token": self.token})
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
    return status


def upsert_person(conn: Conn, p: dict[str, Any]) -> None:
    if p.get("is_deleted"):
        conn.execute("DELETE FROM crm_persons WHERE crm_id=%s", (p["id"],))
        return
    optout = any(e.get("label") == "opt_out" for e in (p.get("emails") or []) if isinstance(e, dict)) or bool(p.get("marketing_status") in {"unsubscribed", "do_not_contact"})
    conn.execute(
        "INSERT INTO crm_persons(crm_id, crm_org_id, name, owner_id, email_optout, suppressed, raw, synced_at) VALUES (%s,%s,%s,%s,%s,%s,%s,now()) "
        "ON CONFLICT (crm_id) DO UPDATE SET crm_org_id=EXCLUDED.crm_org_id, name=EXCLUDED.name, owner_id=EXCLUDED.owner_id, email_optout=EXCLUDED.email_optout, "
        "suppressed=EXCLUDED.suppressed OR crm_persons.suppressed, synced_at=now()",
        (p["id"], p.get("org_id"), p.get("name") or "", p.get("owner_id"), optout, optout, jb({"id": p["id"], "marketing_status": p.get("marketing_status")})))
    if optout:   # suppression propagates to canonical people: honoured across ALL downstream tools
        conn.execute("UPDATE people SET suppressed=true, suppression_reason='crm opt-out' WHERE id IN (SELECT person_id FROM crm_persons WHERE crm_id=%s AND person_id IS NOT NULL)", (p["id"],))


def upsert_deal(conn: Conn, d: dict[str, Any], stages: dict[int, str] | None = None) -> None:
    if d.get("is_deleted"):
        conn.execute("DELETE FROM crm_deals WHERE crm_id=%s", (d["id"],))
        return
    conn.execute(
        "INSERT INTO crm_deals(crm_id, crm_org_id, title, owner_id, stage_id, stage_name, pipeline_id, status, value, currency, update_time, raw, synced_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) ON CONFLICT (crm_id) DO UPDATE SET crm_org_id=EXCLUDED.crm_org_id, title=EXCLUDED.title, owner_id=EXCLUDED.owner_id, "
        "stage_id=EXCLUDED.stage_id, stage_name=EXCLUDED.stage_name, status=EXCLUDED.status, value=EXCLUDED.value, update_time=EXCLUDED.update_time, synced_at=now()",
        (d["id"], d.get("org_id"), d.get("title") or "", d.get("owner_id"), d.get("stage_id"), (stages or {}).get(d.get("stage_id")), d.get("pipeline_id"),
         d.get("status"), d.get("value"), d.get("currency"), d.get("update_time"), jb({"id": d["id"]})))


def sync_incremental(conn: Conn, reader: PipedriveReader, *, stages: dict[int, str] | None = None) -> dict[str, int]:
    """Watermark-based; the watermark advances only after a durable commit. Periodic full reconcile repairs missed webhooks."""
    out = {"organizations": 0, "persons": 0, "deals": 0}
    for entity, path, fn in (("organizations", "/organizations", upsert_org), ("persons", "/persons", upsert_person),
                             ("deals", "/deals", lambda c, r: upsert_deal(c, r, stages))):
        wm = q1(conn, "SELECT watermark FROM sync_state WHERE system='pipedrive' AND entity=%s", (entity,))
        newest = wm["watermark"] if wm else None
        for rec in reader.paged(path, updated_since=newest):
            fn(conn, rec)
            out[entity] += 1
            if rec.get("update_time") and (newest is None or rec["update_time"] > newest):
                newest = rec["update_time"]
        conn.execute("INSERT INTO sync_state(system, entity, watermark) VALUES ('pipedrive',%s,%s) ON CONFLICT (system, entity) DO UPDATE SET watermark=EXCLUDED.watermark", (entity, newest))
        conn.commit()
    return out


# ------------------------------------------------------------------ webhooks (durable inbox, replay-safe)
def verify_basic_auth(header: str | None, expected_user: str, expected_secret: str) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        user, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
    except Exception:  # noqa: BLE001
        return False
    return hmac.compare_digest(user, expected_user) and hmac.compare_digest(pwd, expected_secret)


def receive_webhook(conn: Conn, body: dict[str, Any]) -> tuple[str, bool]:
    """Acknowledge fast: persist to the inbox keyed by a stable id; duplicates are no-ops. Processing is separate."""
    meta = body.get("meta") or {}
    key = str(meta.get("webhook_id") or meta.get("id") or f"{meta.get('entity')}:{meta.get('entity_id')}:{meta.get('action')}:{meta.get('timestamp')}")
    row = conn.execute("INSERT INTO crm_webhook_inbox(dedup_key, event, payload) VALUES (%s,%s,%s) ON CONFLICT (dedup_key) DO NOTHING RETURNING id",
                       (key, f"{meta.get('entity', '?')}.{meta.get('action', '?')}", jb(body))).fetchone()
    return key, bool(row)


def process_inbox(conn: Conn, limit: int = 100) -> dict[str, int]:
    done = bad = 0
    for ev in qa(conn, "SELECT * FROM crm_webhook_inbox WHERE status='received' ORDER BY received_at LIMIT %s", (limit,)):
        meta, data = (ev["payload"].get("meta") or {}), ev["payload"].get("data") or ev["payload"].get("current")
        try:
            with conn.transaction():
                ent = meta.get("entity")
                if not isinstance(data, dict) or "id" not in data:
                    raise CrmError("webhook payload missing data.id (schema drift)")
                if ent == "organization":
                    if meta.get("action") == "delete":
                        data = {**data, "is_deleted": True}
                    upsert_org(conn, data)
                elif ent == "person":
                    upsert_person(conn, data)
                elif ent == "deal":
                    upsert_deal(conn, data)
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
    if a["operation"] != "create_activity":
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
        prior = writer.find_by_marker(org["crm_id"], key)          # a timed-out earlier attempt may have succeeded
        if prior:
            mark_done(conn, approval_id, True, {"reconciled": True, "remote_id": prior.get("id")})
            return ExecResult("executed", prior.get("id"), True)
        res = writer.create_activity({**a["payload"], "org_id": org["crm_id"]}, key)
        mark_done(conn, approval_id, True, {"remote_id": res.get("id")})
        audit(conn, actor="system:executor", operation="crm_activity_created", record_type="crm_organization", record_id=str(org["crm_id"]), approval_id=approval_id,
              detail={"remote_id": res.get("id")})
        return ExecResult("executed", res.get("id"))
    except (httpx.TransportError, httpx.TimeoutException, ConnectorBlocked) as exc:
        # AMBIGUOUS outcome (the write may have landed): hand the approval back so a retry runs fetch-before-write reconciliation
        conn.execute("UPDATE approvals SET status='approved', result=%s WHERE id=%s AND status='executing'", (jb({"ambiguous": type(exc).__name__}), approval_id))
        raise
    except Exception as exc:  # noqa: BLE001
        mark_done(conn, approval_id, False, {"error": type(exc).__name__})
        raise
