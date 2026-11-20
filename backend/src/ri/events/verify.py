"""Event verification (spec 03 §6 step 2, 02 §6, 07 §13). Model confidence is never evidence confidence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..db import Conn, jb, q1, qa

STALE_AFTER = timedelta(days=400)


def verify_event(conn: Conn, event_id: str, *, recheck_notice: Callable[[str], dict[str, Any] | None] | None = None,
                 now: datetime | None = None) -> str:
    """Returns the resulting verification_status. Notes explain every failed/partial check."""
    now = now or datetime.now(timezone.utc)
    ev = q1(conn, "SELECT * FROM events WHERE id=%s", (event_id,))
    assert ev
    notes: list[str] = []
    claims = qa(conn, "SELECT c.* FROM claims c JOIN event_claims ec ON ec.claim_id=c.id WHERE ec.event_id=%s", (event_id,))
    status = "verified"
    if not claims:
        status = "failed"; notes.append("no_claims")
    elif any(c["claim_kind"] == "quote" and not c["anchor_verified"] for c in claims):
        status = "failed"; notes.append("quote_claim_without_verified_anchor")
    if status != "failed" and all(c["claim_kind"] == "inferred" for c in claims):
        status = "unverified"; notes.append("only_inferred_claims")
    if ev["subject_org_id"] is None:
        status = "unverified" if status == "verified" else status
        notes.append("entity_unresolved")
    if not ev["event_at_known"]:
        notes.append("event_date_unknown")          # recorded; pub date is NOT substituted
    ref = ev["event_at"] or ev["pub_at"]
    if ref and now - ref > STALE_AFTER and status == "verified":
        status = "stale"; notes.append("older_than_400_days")

    if ev["event_type"] in {"procurement_published", "procurement_amended"} and ev["attrs"].get("notice_status") in {"open", "amended"}:
        dl = ev["attrs"].get("submission_deadline")
        if not dl:
            status = "unverified"; notes.append("no_submission_deadline")
        elif datetime.fromisoformat(dl) <= now:
            status = "stale"; notes.append("deadline_passed")
        if recheck_notice is not None and status == "verified":
            try:
                cur = recheck_notice(ev["attrs"]["notice_id"])
            except Exception as exc:  # noqa: BLE001
                cur = None; notes.append(f"status_recheck_failed:{type(exc).__name__}")
                status = "unverified"
            if cur is not None:
                if cur.get("status") not in {"open", "amended"} or (cur.get("deadline_at") and cur["deadline_at"] <= now):
                    status = "stale"; notes.append(f"official_status_now_{cur.get('status')}")
                else:
                    notes.append("official_status_rechecked_open")
            elif "status_recheck_failed" not in " ".join(notes):
                status = "unverified"; notes.append("notice_not_found_on_recheck")
        elif recheck_notice is None and status == "verified":
            notes.append("status_not_rechecked")   # alerting layer must recheck before an urgent alert

    conn.execute("UPDATE events SET verification_status=%s, verification_notes=%s, verified_at=CASE WHEN %s='verified' THEN now() ELSE verified_at END, "
                 "last_checked_at=now() WHERE id=%s", (status, jb(notes), status, event_id))
    if status == "stale" and ev["lifecycle_status"] == "active":
        conn.execute("UPDATE events SET lifecycle_status='expired' WHERE id=%s AND event_type LIKE 'procurement%%'", (event_id,))
    return status
