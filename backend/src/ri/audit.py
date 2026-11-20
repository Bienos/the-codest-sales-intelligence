"""Append-only audit trail (spec 04 `audit_events`, 11 §5/§11)."""
from __future__ import annotations

import hashlib
from typing import Any

from .db import Conn, canonical_json, jb


def digest(obj: Any) -> str | None:
    return None if obj is None else hashlib.sha256(canonical_json(obj).encode()).hexdigest()


def audit(
    conn: Conn,
    *,
    actor: str,
    operation: str,
    record_type: str,
    record_id: str | None,
    before: Any = None,
    after: Any = None,
    approval_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_events(actor, operation, record_type, record_id, before_hash, after_hash, approval_id, detail)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (actor, operation, record_type, record_id, digest(before), digest(after), approval_id, jb(detail or {})),
    )
