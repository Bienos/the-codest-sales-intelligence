"""Weekly precision review: which signal rules earn their place, judged by what sellers actually do with them and what outreach produced.
It reports; it never changes a rule. A person decides. Small samples are flagged, not judged."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .db import Conn, jb, q1, qa
from .outcomes import MIN_SAMPLE, wilson

MIN_DECISIONS = 10
FLAG_DISMISSAL_RATE = 0.6


def rule_precision(conn: Conn, days: int = 90) -> list[dict[str, Any]]:
    rows = qa(conn, """
        SELECT s.rule_id, count(DISTINCT s.id) AS fired,
               count(DISTINCT h.id) FILTER (WHERE u.decision='accepted') AS accepted,
               count(DISTINCT h.id) FILTER (WHERE u.decision='dismissed') AS dismissed,
               count(DISTINCT h.id) FILTER (WHERE u.decision='monitor') AS monitored
        FROM signals s
        LEFT JOIN initiative_events ie ON ie.event_id = s.event_id
        LEFT JOIN opportunity_hypotheses h ON h.initiative_id = ie.initiative_id
        LEFT JOIN hypothesis_user_state u ON u.hypothesis_id = h.id AND u.decided_at > now() - make_interval(days => %s)
        WHERE s.effective_from > now() - make_interval(days => %s) AND s.rule_id NOT LIKE 'NEG-%%'
        GROUP BY s.rule_id ORDER BY s.rule_id""", (days, days))
    reasons = qa(conn, """
        SELECT s.rule_id, u.reason, count(DISTINCT h.id) AS n FROM signals s
        JOIN initiative_events ie ON ie.event_id = s.event_id JOIN opportunity_hypotheses h ON h.initiative_id = ie.initiative_id
        JOIN hypothesis_user_state u ON u.hypothesis_id = h.id AND u.decision='dismissed' AND u.decided_at > now() - make_interval(days => %s)
        GROUP BY s.rule_id, u.reason""", (days,))
    top: dict[str, tuple[str, int]] = {}
    for r in reasons:
        if r["reason"] and r["n"] > top.get(r["rule_id"], ("", 0))[1]:
            top[r["rule_id"]] = (r["reason"], r["n"])
    out = []
    for r in rows:
        decided = r["accepted"] + r["dismissed"]
        rate = round(r["dismissed"] / decided, 3) if decided else None
        lo, hi = wilson(r["dismissed"], decided)
        out.append({"rule_id": r["rule_id"], "fired": r["fired"], "accepted": r["accepted"], "dismissed": r["dismissed"], "monitored": r["monitored"], "decided": decided,
                    "dismissal_rate": rate, "interval_95": [lo, hi], "top_dismiss_reason": top.get(r["rule_id"], (None, 0))[0],
                    "enough_data": decided >= MIN_DECISIONS, "flag": decided >= MIN_DECISIONS and lo >= FLAG_DISMISSAL_RATE - 0.15 and (rate or 0) >= FLAG_DISMISSAL_RATE})
    return out


def outreach_results(conn: Conn, days: int = 90) -> dict[str, int]:
    rows = qa(conn, "SELECT status, count(*) AS n FROM outbound_enrolments WHERE created_at > now() - make_interval(days => %s) GROUP BY status", (days,))
    return {r["status"]: r["n"] for r in rows}


def review(conn: Conn, days: int = 90) -> dict[str, Any]:
    rules = rule_precision(conn, days)
    flagged = [r for r in rules if r["flag"]]
    return {"window_days": days, "rules": rules, "flagged": [r["rule_id"] for r in flagged], "outreach": outreach_results(conn, days),
            "caveats": [f"A rule needs at least {MIN_DECISIONS} seller decisions before it is judged; below that it is shown but never flagged.",
                        "A dismissal is a seller's opinion, not proof the signal was wrong. Read the top reason before changing a rule.",
                        f"Outreach outcomes under {MIN_SAMPLE} contacts say nothing about a rule yet."]}


def weekly_review_step(conn: Conn, settings: Any, now: datetime | None = None, sink: Any = None) -> dict[str, Any]:
    """Monday 08:00 to 08:04 UTC, once per week. Records the result and sends one short message when a rule is flagged."""
    now = now or datetime.now(timezone.utc)
    if now.weekday() != 0 or now.hour != 8 or now.minute >= 5:
        return {}
    week = now.strftime("%G-W%V")
    if (q1(conn, "SELECT detail->>'week' AS w FROM system_heartbeats WHERE component='precision_review'") or {}).get("w") == week:
        return {}
    rv = review(conn)
    conn.execute("INSERT INTO system_heartbeats(component, at, detail) VALUES ('precision_review', now(), %s) ON CONFLICT (component) DO UPDATE SET at=now(), detail=EXCLUDED.detail",
                 (jb({"week": week, "flagged": rv["flagged"]}),))
    out: dict[str, Any] = {"precision_flagged": len(rv["flagged"])}
    if rv["flagged"] and sink is not None:
        try:
            sink.send(None, "Revenue Intelligence weekly review: sellers are dismissing these signal rules more than 60% of the time: " + ", ".join(rv["flagged"]) + ". Open Administration, Rules, Precision to look at the reasons.")
            out["precision_alerted"] = True
        except Exception as exc:  # noqa: BLE001
            out["precision_alert_error"] = type(exc).__name__
    return out
