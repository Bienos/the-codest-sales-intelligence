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
    ok = True                      # "proven": the facts are quoted or official and not stale, whether or not the company is one of ours yet
    if not claims:
        status = "failed"; ok = False; notes.append("no_claims")
    elif any(c["claim_kind"] == "quote" and not c["anchor_verified"] for c in claims):
        status = "failed"; ok = False; notes.append("quote_claim_without_verified_anchor")
    if status != "failed" and all(c["claim_kind"] == "inferred" for c in claims):
        status = "unverified"; ok = False; notes.append("only_inferred_claims")
    if ev["subject_org_id"] is None:
        status = "unverified" if status == "verified" else status
        notes.append("entity_unresolved")
    if not ev["event_at_known"]:
        notes.append("event_date_unknown")          # recorded; pub date is NOT substituted
    ref = ev["event_at"] or ev["pub_at"]
    if ref and now - ref > STALE_AFTER and status == "verified":
        status = "stale"; notes.append("older_than_400_days")
    if ref and now - ref > STALE_AFTER:
        ok = False

    if ev["event_type"] in {"procurement_published", "procurement_amended"} and ev["attrs"].get("notice_status") in {"open", "amended"}:
        dl = ev["attrs"].get("submission_deadline")
        if not dl:
            status = "unverified"; ok = False; notes.append("no_submission_deadline")
        elif datetime.fromisoformat(dl) <= now:
            status = "stale"; ok = False; notes.append("deadline_passed")
        if recheck_notice is not None and status == "verified":
            try:
                cur = recheck_notice(ev["attrs"]["notice_id"])
            except Exception as exc:  # noqa: BLE001
                cur = None; notes.append(f"status_recheck_failed:{type(exc).__name__}")
                status = "unverified"; ok = False
            if cur is not None:
                if cur.get("status") not in {"open", "amended"} or (cur.get("deadline_at") and cur["deadline_at"] <= now):
                    status = "stale"; ok = False; notes.append(f"official_status_now_{cur.get('status')}")
                else:
                    notes.append("official_status_rechecked_open")
            elif "status_recheck_failed" not in " ".join(notes):
                status = "unverified"; notes.append("notice_not_found_on_recheck")
        elif recheck_notice is None and status == "verified":
            notes.append("status_not_rechecked")   # alerting layer must recheck before an urgent alert

    conn.execute("UPDATE events SET verification_status=%s, verification_notes=%s, verified_at=CASE WHEN %s='verified' THEN now() ELSE verified_at END, "
                 "last_checked_at=now(), proven=%s WHERE id=%s", (status, jb(notes), status, ok and status in ("verified", "unverified"), event_id))
    tag_market(conn, event_id)
    if status == "stale" and ev["lifecycle_status"] == "active":
        conn.execute("UPDATE events SET lifecycle_status='expired' WHERE id=%s AND event_type LIKE 'procurement%%'", (event_id,))
    return status


def tag_market(conn: Conn, event_id: str) -> None:
    """Which market the event belongs to and whether the buyer is public sector. Country comes from the linked account, else the official notice, else a single-country source."""
    from ..market import alpha2, lane_for, tier_for
    ev = q1(conn, "SELECT e.subject_org_id, e.subject_name_raw, e.summary, o.display_name, o.country_code, o.entity_type FROM events e LEFT JOIN organizations o ON o.id=e.subject_org_id WHERE e.id=%s", (event_id,))
    if not ev:
        return
    country, name = ev["country_code"], ev["display_name"] or ev["subject_name_raw"]
    if not country or not name:
        pn = q1(conn, "SELECT pn.country, pn.buyer_name FROM event_documents ed JOIN procurement_notices pn ON pn.document_id=ed.document_id WHERE ed.event_id=%s LIMIT 1", (event_id,))
        if pn:
            country, name = country or pn["country"], name or pn["buyer_name"]
    if not country:
        src = q1(conn, "SELECT s.countries FROM event_documents ed JOIN source_documents d ON d.id=ed.document_id JOIN sources s ON s.source_id=d.source_id WHERE ed.event_id=%s LIMIT 1", (event_id,))
        cs = [c for c in ((src or {}).get("countries") or []) if alpha2(c) and len(c) <= 3]
        country = cs[0] if len(cs) == 1 else None
    conn.execute("UPDATE events SET market_tier=%s, lane=%s, market_country=%s, market_tagged_at=now() WHERE id=%s",
                 (tier_for(country), lane_for(name, ev["entity_type"]), alpha2(country), event_id))


def tag_untagged(conn: Conn, limit: int = 1500) -> int:
    """Back-fill: events recorded before proven/market tagging existed. Safe to run repeatedly; the scheduler does it in small batches."""
    n = 0
    for r in qa(conn, "SELECT id FROM events WHERE market_tagged_at IS NULL ORDER BY first_observed_at DESC LIMIT %s", (limit,)):
        verify_event(conn, str(r["id"]))        # recomputes "proven" with the current rules and tags the market in the same step
        n += 1
    return n
