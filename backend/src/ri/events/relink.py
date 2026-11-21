"""Re-match events whose subject company was unknown when they arrived (for example tenders collected before the account lists were imported).
Safe to repeat: an event is only re-checked when a newer organisation has appeared since it was last checked."""
from __future__ import annotations

from datetime import datetime, timezone

from ..db import Conn, jb, q1, qa
from ..identity.resolver import resolve


def relink_unmatched(conn: Conn, limit: int = 300, now: datetime | None = None) -> dict[str, int]:
    from ..ingest.process import finish_event
    now = now or datetime.now(timezone.utc)
    newest = q1(conn, "SELECT max(created_at) AS t FROM organizations")
    if not newest or not newest["t"]:
        return {"checked": 0, "linked": 0}
    rows = qa(conn, "SELECT id, subject_name_raw, attrs FROM events WHERE subject_org_id IS NULL AND subject_name_raw IS NOT NULL AND length(subject_name_raw) > 2 "
                    "AND (attrs->>'relink_checked_at' IS NULL OR (attrs->>'relink_checked_at')::timestamptz < %s) ORDER BY first_observed_at DESC NULLS LAST LIMIT %s", (newest["t"], limit))
    linked = 0
    for r in rows:
        d = resolve(conn, name=r["subject_name_raw"], allow_create=False)
        if d.action == "auto_link" and d.org_id:
            conn.execute("UPDATE events SET subject_org_id=%s WHERE id=%s", (d.org_id, r["id"]))
            nid = (r["attrs"] or {}).get("notice_id")
            if nid:
                conn.execute("UPDATE procurement_notices SET buyer_org_id=%s WHERE notice_id=%s AND buyer_org_id IS NULL", (d.org_id, nid))
            try:
                with conn.transaction():                          # one bad event must never stop the others (or the scheduler)
                    finish_event(conn, str(r["id"]), now=now)    # signals, programme and opportunity, exactly as if it had matched on arrival
                linked += 1
            except Exception as exc:  # noqa: BLE001
                conn.execute("UPDATE events SET attrs = attrs || %s::jsonb WHERE id=%s", (jb({"relink_error": f"{type(exc).__name__}: {exc}"[:200]}), r["id"]))
        else:
            conn.execute("UPDATE events SET attrs = attrs || %s::jsonb WHERE id=%s", (jb({"relink_checked_at": now.isoformat()}), r["id"]))
    return {"checked": len(rows), "linked": linked}
