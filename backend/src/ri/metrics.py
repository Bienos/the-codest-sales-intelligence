"""Operational/quality metrics (spec 12 §7, 13 §8). Computed from the database; no synthetic baselines."""
from __future__ import annotations

from typing import Any

from .db import Conn, q1, qa


def quality_snapshot(conn: Conn) -> dict[str, Any]:
    total_alerts = q1(conn, "SELECT count(*) n FROM notifications_sent WHERE status='sent'")["n"]  # type: ignore[index]
    dup = q1(conn, "SELECT count(*) n FROM (SELECT recipient, hypothesis_id, payload_summary FROM notifications_sent WHERE status='sent' AND kind='daily_digest' GROUP BY 1,2,3 HAVING count(*)>1) x")["n"]  # type: ignore[index]
    uncited = q1(conn, "SELECT count(*) n FROM opportunity_hypotheses WHERE priority IN ('urgent','high') AND cardinality(supporting_claim_ids)=0")["n"]  # type: ignore[index]
    return {
        "source_health": qa(conn, "SELECT state, count(*) n FROM source_health GROUP BY state ORDER BY state"),
        "stale_sources": q1(conn, "SELECT count(*) n FROM source_health WHERE missed_due_runs>0")["n"],  # type: ignore[index]
        "dead_letters_unreplayed": q1(conn, "SELECT count(*) n FROM dead_letters WHERE replayed_at IS NULL AND dismissed_at IS NULL")["n"],  # type: ignore[index]
        "alerts_sent": total_alerts, "duplicate_alert_groups": dup,
        "high_priority_without_citation": uncited,                      # must be 0 (spec 12: 100% traceability for actionable assertions)
        "open_identity_reviews": q1(conn, "SELECT count(*) n FROM identity_reviews WHERE status='open'")["n"],  # type: ignore[index]
        "unverified_events": q1(conn, "SELECT count(*) n FROM events WHERE verification_status='unverified' AND NOT is_historical_replay")["n"],  # type: ignore[index]
        "unauthorised_writes_detected": q1(conn, "SELECT count(*) n FROM approvals WHERE status='executed' AND (approver IS NULL OR approved_at IS NULL)")["n"],  # type: ignore[index]
        "pending_approvals": q1(conn, "SELECT count(*) n FROM approvals WHERE status='pending'")["n"],  # type: ignore[index]
    }


def prometheus_text(conn: Conn) -> str:
    q = quality_snapshot(conn)
    lines = []
    for k, v in q.items():
        if isinstance(v, (int, float)):
            lines.append(f"ri_{k} {v}")
    for r in q["source_health"]:
        lines.append(f'ri_source_health{{state="{r["state"]}"}} {r["n"]}')
    return "\n".join(lines) + "\n"
