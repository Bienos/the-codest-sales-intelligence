from datetime import datetime, timedelta, timezone

from ri.jobs import queue
from ri.scheduler import due_sources, run_maintenance, schedule_due
from ri.signals.baseline import import_baseline
from ri.signals.persist import sync_rule_versions
from ri.sources import registry
from test_r1_pipeline import CERT

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def test_only_activated_sources_scheduled_and_scheduling_is_idempotent(conn):
    import_baseline(conn); sync_rule_versions(conn); registry.seed_registry(conn)
    assert schedule_due(conn, NOW) == 0                                       # everything is pending_connector_validation
    registry.activate(conn, "procurement-ted", CERT, "t")
    assert schedule_due(conn, NOW) == 1 and schedule_due(conn, NOW) == 0     # same bucket: no duplicate job
    conn.execute("update source_health set last_attempt_at=%s where source_id='procurement-ted'", (NOW,))
    assert due_sources(conn, NOW + timedelta(hours=1)) == []                  # not due again until its interval elapses
    assert [s["source_id"] for s in due_sources(conn, NOW + timedelta(days=1, minutes=1))] == ["procurement-ted"]
    assert queue.run_one(conn, "w", {"source_run": lambda c, j: {"ok": True}}) == "completed"


def test_maintenance_runs_cleanly_on_empty_database(conn):
    import_baseline(conn); sync_rule_versions(conn); registry.seed_registry(conn)
    out = run_maintenance(conn, NOW)
    assert out["signals_expired"] == 0 and out["degraded_sources"] == []
