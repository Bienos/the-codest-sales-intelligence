"""Glue: run result -> events -> verification -> signals. Idempotent end to end (replay creates nothing new)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from ..db import Conn, q1
from ..events.extract import EventExtractor, process_document
from ..events.structured import emit_job_event, emit_procurement_events
from ..events.verify import verify_event
from ..signals.persist import evaluate_event, expire_on_job_closed
from .pipeline import RunResult


def finish_event(conn: Conn, event_id: str, *, recheck=None, now: datetime | None = None) -> dict[str, Any]:
    from ..initiatives.link import link_capacity_evidence, link_event
    from ..initiatives.routes import derive_routes
    status = verify_event(conn, event_id, recheck_notice=recheck, now=now)
    out = evaluate_event(conn, event_id, now=now)
    link = link_event(conn, event_id)
    cap = link_capacity_evidence(conn, event_id)
    out["initiative_id"] = link.initiative_id or cap.initiative_id
    if out["negatives"]:     # scoped negatives (route blocks) change route access for the account's initiatives
        for i in conn.execute("SELECT i.id FROM initiatives i JOIN events e ON e.subject_org_id=i.owner_org_id WHERE e.id=%s AND i.status='active'", (event_id,)).fetchall():
            derive_routes(conn, str(i["id"]))
    ev = q1(conn, "SELECT event_type FROM events WHERE id=%s", (event_id,))
    if ev and ev["event_type"] == "job_closed":
        out["jobs_signals_expired"] = expire_on_job_closed(conn, event_id)
    return {"verification": status, **out}


def process_run(conn: Conn, res: RunResult, *, extractor: EventExtractor | None = None, resolve_org: Callable[[str], str | None] | None = None,
                recheck: Callable[[str], dict[str, Any] | None] | None = None, now: datetime | None = None, replay: bool = False) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    summary: dict[str, Any] = {"events_created": 0, "signals": 0, "negatives": 0}
    for kind, rid, change in res.structured_changes:
        eid = emit_procurement_events(conn, rid, change, replay=replay) if kind == "procurement" else emit_job_event(conn, rid, change, now=now, replay=replay)
        if eid:
            summary["events_created"] += 1
            r = finish_event(conn, eid, recheck=recheck, now=now)
            summary["signals"] += len(r["signals"]); summary["negatives"] += len(r["negatives"])
    if extractor is not None:
        for doc_id in [*res.new_doc_ids, *res.changed_doc_ids]:
            d = q1(conn, "SELECT raw_meta FROM source_documents WHERE id=%s", (doc_id,))
            if not d or (d["raw_meta"] or {}).get("structured"):
                continue
            out = process_document(conn, doc_id, extractor, resolve_org=resolve_org, replay=replay)
            for e in conn.execute("SELECT DISTINCT e.id FROM events e JOIN event_documents ed ON ed.event_id=e.id WHERE ed.document_id=%s", (doc_id,)).fetchall():
                r = finish_event(conn, str(e["id"]), recheck=recheck, now=now)
                summary["signals"] += len(r["signals"]); summary["negatives"] += len(r["negatives"])
            summary["events_created"] += out.get("events", 0)
    return summary
