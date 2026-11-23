"""Approval objects (spec 09 §10, 11 §5). An approval is bound to requester, approver, exact target, operation and an
immutable payload hash, with expiry. The executor rejects stale, modified, mismatched or reused approvals.
AI workers never hold write credentials; only an executor that validates an approval may call a mutation adapter.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from .audit import audit
from .db import Conn, canonical_json, jb, q1

TWO_PERSON = {"contact_enrichment", "crm_write", "outbound_message", "sequence_enrolment", "supplier_application", "quote_or_commitment", "proof_release"}
APPROVER_ROLES = {"sales_manager", "admin", "bdm"}


class ApprovalError(Exception):
    pass


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def request(conn: Conn, *, kind: str, target_system: str, target_id: str, operation: str, payload: dict[str, Any], requested_by: str,
            justification: str, idempotency_key: str, ttl_hours: int = 72, scope: dict[str, Any] | None = None) -> dict[str, Any]:
    if not justification.strip():
        raise ApprovalError("justification required")
    existing = q1(conn, "SELECT * FROM approvals WHERE idempotency_key=%s", (idempotency_key,))
    if existing:
        if existing["payload_hash"] != payload_hash(payload):
            raise ApprovalError("idempotency key reused with a different payload")
        return existing
    row = conn.execute(
        "INSERT INTO approvals(kind, target_system, target_id, operation, payload, payload_hash, requested_by, justification, scope, idempotency_key, expires_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (kind, target_system, target_id, operation, jb(payload), payload_hash(payload), requested_by, justification, jb(scope or {}), idempotency_key,
         datetime.now(timezone.utc) + timedelta(hours=ttl_hours))).fetchone()
    audit(conn, actor=requested_by, operation="approval_requested", record_type="approval", record_id=str(row["id"]), after={"op": operation, "target": target_id},
          approval_id=str(row["id"]))
    return row  # type: ignore[return-value]


def decide(conn: Conn, approval_id: str, *, approver: str, approver_role: str, approve: bool, now: datetime | None = None) -> dict[str, Any]:
    a = q1(conn, "SELECT * FROM approvals WHERE id=%s FOR UPDATE", (approval_id,))
    if not a:
        raise ApprovalError("approval not found")
    if a["status"] != "pending":
        raise ApprovalError(f"approval is {a['status']}, not pending")
    now = now or datetime.now(timezone.utc)
    if a["expires_at"] <= now:
        conn.execute("UPDATE approvals SET status='expired' WHERE id=%s", (approval_id,))
        raise ApprovalError("approval expired")
    if approver_role not in APPROVER_ROLES:
        raise ApprovalError(f"role {approver_role} cannot approve")
    if a["kind"] in TWO_PERSON and approver == a["requested_by"]:
        raise ApprovalError("requester cannot approve their own request")
    status = "approved" if approve else "rejected"
    conn.execute("UPDATE approvals SET status=%s, approver=%s, approved_at=%s WHERE id=%s", (status, approver, now, approval_id))
    audit(conn, actor=approver, operation=f"approval_{status}", record_type="approval", record_id=approval_id, approval_id=approval_id)
    return q1(conn, "SELECT * FROM approvals WHERE id=%s", (approval_id,))  # type: ignore[return-value]


def validate_for_execution(conn: Conn, approval_id: str | None, *, kind: str, target_system: str, target_id: str, operation: str,
                           payload: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Raises unless the approval exactly matches what is about to be executed. Called by executors only."""
    if not approval_id:
        raise ApprovalError("no approval supplied")
    a = q1(conn, "SELECT * FROM approvals WHERE id=%s FOR UPDATE", (approval_id,))
    if not a:
        raise ApprovalError("approval not found")
    now = now or datetime.now(timezone.utc)
    if a["status"] != "approved":
        raise ApprovalError(f"approval is {a['status']}")
    if a["expires_at"] <= now:
        conn.execute("UPDATE approvals SET status='expired' WHERE id=%s", (approval_id,))
        raise ApprovalError("approval expired")
    for field, want in (("kind", kind), ("target_system", target_system), ("target_id", target_id), ("operation", operation)):
        if a[field] != want:
            raise ApprovalError(f"approval {field} mismatch ({a[field]!r} != {want!r})")
    if a["payload_hash"] != payload_hash(payload):
        raise ApprovalError("payload differs from the approved payload")
    return a


def mark_executing(conn: Conn, approval_id: str, execution_id: str) -> None:
    n = conn.execute("UPDATE approvals SET status='executing', execution_id=%s WHERE id=%s AND status='approved'", (execution_id, approval_id)).rowcount
    if not n:
        raise ApprovalError("approval already used or not approved (single-use)")


def mark_done(conn: Conn, approval_id: str, ok: bool, result: dict[str, Any]) -> None:
    conn.execute("UPDATE approvals SET status=%s, executed_at=now(), result=%s WHERE id=%s", ("executed" if ok else "failed", jb(result), approval_id))
    audit(conn, actor="system:executor", operation="approval_executed" if ok else "approval_failed", record_type="approval", record_id=approval_id,
          approval_id=approval_id, detail={"ok": ok})
