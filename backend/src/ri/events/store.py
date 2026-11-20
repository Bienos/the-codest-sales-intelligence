"""Idempotent event/claim persistence. Documents, claims, events and signals are distinct objects (spec 03 §1)."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..db import Conn, jb, q1
from ..sources.util import sha

MODEL_STRUCTURED = "structured-extractor/1.0"


def norm_key(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def add_claim(conn: Conn, *, subject: str, predicate: str, obj: dict[str, Any], doc_id: str | None, anchor: str | None,
              kind: str = "structured", anchor_verified: bool = False, model_version: str = MODEL_STRUCTURED,
              status: str = "supported", valid_from: datetime | None = None) -> str:
    row = conn.execute(
        "INSERT INTO claims(subject, predicate, object_json, evidence_anchor, anchor_verified, claim_kind, source_document_id, status, "
        "extraction_model_version, valid_from) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (subject, predicate, jb(obj), anchor, anchor_verified, kind, doc_id, status, model_version, valid_from)).fetchone()
    return str(row["id"])  # type: ignore[index]


def store_event(conn: Conn, *, event_type: str, subject_name: str | None, subject_org_id: str | None, summary: str,
                attrs: dict[str, Any], key_parts: tuple[Any, ...], event_at: datetime | None, pub_at: datetime | None,
                claim_ids: list[str], document_ids: list[str], origin_document_id: str | None = None,
                origin_group: str | None = None, other_entities: list[dict[str, Any]] | None = None,
                model_version: str = MODEL_STRUCTURED, replay: bool = False,
                lifecycle: str = "active", supersedes: str | None = None) -> tuple[str, bool]:
    """Returns (event_id, created). Same key => the existing event gains the new documents/claims, no duplicate."""
    key = sha("evt", event_type, subject_org_id or norm_key(subject_name), *key_parts)
    row = conn.execute(
        "INSERT INTO events(event_key, event_type, subject_org_id, subject_name_raw, other_entity_roles, event_at, event_at_known, pub_at, summary, attrs, "
        "extraction_model_version, origin_document_id, independent_origin_group, is_historical_replay, lifecycle_status, supersedes_event_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (event_key) DO NOTHING RETURNING id",
        (key, event_type, subject_org_id, subject_name, jb(other_entities or []), event_at, event_at is not None, pub_at, summary, jb(attrs),
         model_version, origin_document_id, origin_group, replay, lifecycle, supersedes)).fetchone()
    created = row is not None
    event_id = str(row["id"]) if row else str(q1(conn, "SELECT id FROM events WHERE event_key=%s", (key,))["id"])  # type: ignore[index]
    for cid in claim_ids:
        conn.execute("INSERT INTO event_claims(event_id, claim_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (event_id, cid))
    for did in document_ids:
        conn.execute("INSERT INTO event_documents(event_id, document_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (event_id, did))
    if not created:
        conn.execute("UPDATE events SET last_checked_at=now() WHERE id=%s", (event_id,))
    return event_id, created
