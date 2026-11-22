"""Daily-style self-test. Cheap checks that the platform is doing its job, not only that processes are alive.
A failure is written to the audit log and, when a Slack webhook is set, sent once per change (not every hour)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .audit import audit
from .db import Conn, jb, q1, qa


def run_checks(conn: Conn) -> list[dict[str, str]]:
    bad: list[dict[str, str]] = []

    def check(code: str, ok: bool, detail: str) -> None:
        if not ok:
            bad.append({"code": code, "detail": detail})

    hb = q1(conn, "SELECT extract(epoch FROM now()-at)/60 AS mins FROM system_heartbeats WHERE component='worker'")
    check("worker_silent", bool(hb) and float(hb["mins"]) < 20, "The background worker has not reported for over 20 minutes.")
    sched = q1(conn, "SELECT extract(epoch FROM now()-at)/60 AS mins, detail FROM system_heartbeats WHERE component='scheduler'")
    check("scheduler_silent", bool(sched) and float(sched["mins"]) < 20, "The scheduler has not reported for over 20 minutes.")
    errs = [k for k in ((sched or {}).get("detail") or {}) if str(k).endswith("_error")]
    check("scheduler_step_failing", not errs, f"Scheduler steps failing: {', '.join(errs)}")
    active = q1(conn, "SELECT count(*) AS n FROM sources WHERE status='active'")["n"]  # type: ignore[index]
    docs = q1(conn, "SELECT count(*) AS n FROM source_documents WHERE fetched_at > now() - interval '24 hours'")["n"]  # type: ignore[index]
    check("no_documents_24h", not active or docs > 0, f"{active} sources are active but nothing was read in the last 24 hours.")
    ev = q1(conn, "SELECT count(*) AS n FROM events WHERE first_observed_at > now() - interval '72 hours'")["n"]  # type: ignore[index]
    check("no_events_72h", not active or ev > 0, "No new events in 72 hours.")
    silent = [r["source_id"] for r in qa(conn, "SELECT s.source_id FROM sources s WHERE s.status='active' "
                                             "AND (SELECT count(*) FROM monitoring_runs m WHERE m.source_id=s.source_id AND m.status='success') >= 3 "
                                             "AND NOT EXISTS (SELECT 1 FROM source_documents d WHERE d.source_id=s.source_id)")]
    check("silent_sources", not silent, f"Active sources that have run at least three times and never returned a single item: {', '.join(silent)}. Check their connector.")
    rules = q1(conn, "SELECT count(*) AS n FROM signal_rule_versions WHERE status='active'")["n"]  # type: ignore[index]
    check("no_active_rules", rules > 0, "No signal rule is active.")
    dl = q1(conn, "SELECT count(*) AS n FROM dead_letters WHERE replayed_at IS NULL AND created_at > now() - interval '24 hours'")["n"]  # type: ignore[index]
    check("dead_letters", dl <= 20, f"{dl} jobs failed for good in the last 24 hours.")
    billing = q1(conn, "SELECT count(*) AS n FROM jobs WHERE last_error_class='provider_billing' AND status IN ('retryable_failed','permanent_failed') AND updated_at > now() - interval '12 hours'")["n"]  # type: ignore[index]
    legacy = q1(conn, "SELECT count(*) AS n FROM dead_letters WHERE replayed_at IS NULL AND error ILIKE '%%credit balance%%' AND created_at > now() - interval '7 days'")["n"]  # type: ignore[index]
    check("provider_billing", not (billing or legacy), "A provider account is out of credit (Anthropic: 'credit balance is too low'). Reading and extraction are stalled until it is topped up; failed work retries by itself or can be replayed.")
    for b in qa(conn, "SELECT provider, kind, period, cap, reserved, spent FROM provider_budgets WHERE kind='monthly' AND cap > 0 AND period = to_char(now(), 'YYYY-MM')"):
        check(f"budget_{b['provider']}", float(b["spent"]) + float(b["reserved"]) < 0.9 * float(b["cap"]), f"{b['provider']} has used over 90% of this month's cap.")
    return bad


def run_canary(conn: Conn, settings: Any, now: datetime | None = None, sink: Any = None) -> dict[str, Any]:
    """Hourly. Alerts only when the set of failures changes, so a long outage is one message, not twenty-four."""
    now = now or datetime.now(timezone.utc)
    if now.minute >= 5:
        return {}
    bad = run_checks(conn)
    sig = hashlib.sha256(",".join(sorted(b["code"] for b in bad)).encode()).hexdigest()[:16] if bad else "ok"
    prev = (q1(conn, "SELECT detail->>'sig' AS sig FROM system_heartbeats WHERE component='canary'") or {}).get("sig")
    conn.execute("INSERT INTO system_heartbeats(component, at, detail) VALUES ('canary', now(), %s) ON CONFLICT (component) DO UPDATE SET at=now(), detail=EXCLUDED.detail",
                 (jb({"sig": sig, "failing": [b["code"] for b in bad]}),))
    out: dict[str, Any] = {"canary_failing": len(bad)}
    if sig != prev:
        if bad:
            audit(conn, actor="system:canary", operation="canary_failed", record_type="platform", record_id="canary", detail={"checks": bad})
        if sink is not None and (bad or prev not in (None, "ok")):
            text = ("Revenue Intelligence self-test: " + "; ".join(b["detail"] for b in bad)) if bad else "Revenue Intelligence self-test: all checks pass again."
            try:
                sink.send(None, text)
                out["canary_alerted"] = True
            except Exception as exc:  # noqa: BLE001
                out["canary_alert_error"] = type(exc).__name__
    return out
