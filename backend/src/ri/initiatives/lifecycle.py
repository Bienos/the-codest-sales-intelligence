"""Initiative refresh (spec 05 §9) and timeline reasoning (§8)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..db import Conn, jb, qa

REVIEW_CADENCE_DAYS = {"implementation": 14, "sourcing": 7, "planning": 30, "announced": 45, "transition_or_launch": 14}


def refresh(conn: Conn, now: datetime | None = None) -> dict[str, int]:
    """Retire passed 'upcoming' milestones (no new official progress => not upcoming) and flag stale initiatives."""
    now = now or datetime.now(timezone.utc)
    retired = conn.execute("UPDATE initiatives SET next_milestone_at=NULL, revision=revision+1 WHERE status='active' AND next_milestone_at IS NOT NULL AND next_milestone_at < %s", (now,)).rowcount
    stale = 0
    for i in qa(conn, "SELECT id, stage, last_material_event_at, research_gaps FROM initiatives WHERE status='active' AND stage NOT IN ('completed','cancelled','candidate')"):
        cad = timedelta(days=REVIEW_CADENCE_DAYS.get(i["stage"], 60))
        if i["last_material_event_at"] and now - i["last_material_event_at"] > cad * 4:
            gaps = [g for g in i["research_gaps"] if not str(g).startswith("STALE:")] + [f"STALE: no material update for >{cad.days * 4} days - verify programme status"]
            conn.execute("UPDATE initiatives SET research_gaps=%s WHERE id=%s", (jb(gaps), i["id"]))
            stale += 1
    return {"milestones_retired": retired, "stale_flagged": stale}
