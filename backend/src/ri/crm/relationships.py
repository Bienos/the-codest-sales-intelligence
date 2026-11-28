"""Fill "relationship with us" on an account from its Pipedrive deals.

Only for accounts linked to a Pipedrive organisation (``crm_organizations.link_status='linked'``), and never over a person's choice: an account
whose relationship a person set or cleared (``relationship_set_by`` is anything but ``SOURCE``) is left alone. What we set is kept in step
with Pipedrive: if a deal is won or lost later, the next run changes it.

  * a won deal and an open deal, or a deal won in the last ``CLIENT_DAYS`` days  -> client
  * a deal won longer ago                                                       -> past_client
  * an open deal                                                                -> in_talks
  * only lost deals                                                             -> prospect, with the latest lost date and reason in the note
The note says what it was read from ("From Pipedrive: 1 won, 2 open, 0 lost deals"). The hourly lane ``crm_relationships`` runs it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import Conn, qa

SOURCE = "pipedrive"
CLIENT_DAYS = 365


def decide(won: int, open_: int, lost: int, last_won: datetime | None, last_lost: datetime | None, lost_reason: str | None,
           now: datetime) -> tuple[str, str] | None:
    """(status, note) for an account's deals, or None when its deals say nothing."""
    counts = f"From Pipedrive: {won} won, {open_} open, {lost} lost deal{'s' if lost != 1 else ''}."
    if won and (open_ or (last_won and now - last_won <= timedelta(days=CLIENT_DAYS))):
        return "client", counts
    if won:
        return "past_client", counts + (f" Last won {last_won:%d %b %Y}." if last_won else "")
    if open_:
        return "in_talks", counts
    if lost:
        why = f" Last lost {last_lost:%d %b %Y}" + (f": {lost_reason.strip()[:120]}" if lost_reason and lost_reason.strip() else "") + "." if last_lost else ""
        return "prospect", counts + why
    return None


def fill_from_pipedrive(conn: Conn, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    rows = qa(conn, """
        SELECT o.id, o.relationship, o.relationship_note, o.relationship_set_by,
               count(d.crm_id) FILTER (WHERE d.status='won') AS won, count(d.crm_id) FILTER (WHERE d.status='open') AS open,
               count(d.crm_id) FILTER (WHERE d.status='lost') AS lost,
               max(coalesce(d.won_time, d.update_time)) FILTER (WHERE d.status='won') AS last_won,
               max(coalesce(d.lost_time, d.update_time)) FILTER (WHERE d.status='lost') AS last_lost,
               (array_agg(d.lost_reason ORDER BY coalesce(d.lost_time, d.update_time) DESC NULLS LAST) FILTER (WHERE d.status='lost'))[1] AS lost_reason
        FROM organizations o
        JOIN crm_organizations c ON c.organization_id=o.id AND c.link_status='linked'
        JOIN crm_deals d ON d.crm_org_id=c.crm_id
        WHERE o.relationship_set_by IS NULL OR o.relationship_set_by = %s
        GROUP BY o.id""", (SOURCE,))
    out = {"relationships_set": 0, "relationships_changed": 0, "accounts_with_deals": len(rows)}
    for r in rows:
        d = decide(int(r["won"]), int(r["open"]), int(r["lost"]), r["last_won"], r["last_lost"], r["lost_reason"], now)
        if d is None or (r["relationship"], r["relationship_note"]) == d:
            continue
        conn.execute("UPDATE organizations SET relationship=%s, relationship_note=%s, relationship_set_by=%s, relationship_set_at=%s WHERE id=%s "
                     "AND (relationship_set_by IS NULL OR relationship_set_by=%s)", (d[0], d[1], SOURCE, now, r["id"], SOURCE))
        out["relationships_changed" if r["relationship"] else "relationships_set"] += 1
    return out
