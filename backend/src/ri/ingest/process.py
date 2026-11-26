"""Glue: run result -> events -> verification -> signals. Idempotent end to end (replay creates nothing new)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from ..db import Conn, jb, q1
from ..events.extract import EventExtractor, process_document
from ..events.structured import emit_job_event, emit_procurement_events
from ..events.verify import verify_event
from ..signals.persist import evaluate_event, expire_on_job_closed
from ..jobs.queue import JobError
from ..sources.base import BudgetExceeded, ConnectorError
from .pipeline import RunResult


def finish_event(conn: Conn, event_id: str, *, recheck=None, now: datetime | None = None) -> dict[str, Any]:
    from ..initiatives.link import link_capacity_evidence, link_event
    from ..initiatives.routes import derive_routes
    status = verify_event(conn, event_id, recheck_notice=recheck, now=now)
    out = evaluate_event(conn, event_id, now=now)
    link = link_event(conn, event_id)
    cap = link_capacity_evidence(conn, event_id)
    out["initiative_id"] = link.initiative_id or cap.initiative_id
    if out["initiative_id"]:
        from ..opportunity.engine import refresh_hypotheses
        out["hypotheses"] = refresh_hypotheses(conn, out["initiative_id"], now=now)      # programme -> opportunity card, automatically
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
    if extractor is None:
        # no model available right now (no key, or blocked): queue the new text so a later run reads it instead of losing it
        for doc_id in [*res.new_doc_ids, *res.changed_doc_ids]:
            conn.execute("UPDATE source_documents SET raw_meta = coalesce(raw_meta,'{}'::jsonb) || '{\"pending_extraction\": true}'::jsonb "
                         "WHERE id=%s AND content_text IS NOT NULL AND coalesce(raw_meta->>'structured','') = ''", (doc_id,))
    if extractor is not None:
        pending = [str(r["id"]) for r in conn.execute("SELECT id FROM source_documents WHERE raw_meta->>'pending_extraction'='true' AND content_text IS NOT NULL ORDER BY fetched_at LIMIT 50").fetchall()]
        for doc_id in [*res.new_doc_ids, *res.changed_doc_ids, *[p for p in pending if p not in res.new_doc_ids]]:
            d = q1(conn, "SELECT raw_meta, title, coalesce(content_text, excerpt) AS body FROM source_documents WHERE id=%s", (doc_id,))
            if not d or (d["raw_meta"] or {}).get("structured"):
                continue
            from ..events.triage import looks_relevant
            twin = q1(conn, "SELECT o.id FROM source_documents me JOIN source_documents o ON o.canonical_url=me.canonical_url AND o.source_id<>me.source_id "
                            "WHERE me.id=%s AND me.canonical_url IS NOT NULL AND EXISTS (SELECT 1 FROM event_documents ed WHERE ed.document_id=o.id) LIMIT 1", (doc_id,))
            if twin:
                # the same article from a sister feed, i.e. ANOTHER source (Finextra publishes five); a changed page of the same source is a new version and is read again. Link it to the findings already made, do not pay to read it again
                conn.execute("INSERT INTO event_documents(event_id, document_id) SELECT event_id, %s FROM event_documents WHERE document_id=%s ON CONFLICT DO NOTHING",
                             (doc_id, twin["id"]))
                conn.execute("UPDATE source_documents SET raw_meta = (raw_meta - 'pending_extraction') || '{\"triage\": \"same_article_as_another_source\"}'::jsonb WHERE id=%s", (doc_id,))
                summary["same_article"] = summary.get("same_article", 0) + 1
                continue
            if not looks_relevant(d["title"], d["body"]):
                # saves model budget: recorded as skipped (never silently dropped), text purged for licence-restricted sources by the normal retention rule
                conn.execute("UPDATE source_documents SET raw_meta = (raw_meta - 'pending_extraction') || '{\"triage\": \"skipped_not_relevant\"}'::jsonb WHERE id=%s", (doc_id,))
                summary["skipped_not_relevant"] = summary.get("skipped_not_relevant", 0) + 1
                continue
            if hasattr(extractor, "ctx"):
                extractor.ctx = {"document_id": doc_id}
            try:
                with conn.transaction():
                    out = process_document(conn, doc_id, extractor, resolve_org=resolve_org, replay=replay)
                    conn.execute("UPDATE source_documents SET raw_meta = raw_meta - 'pending_extraction' WHERE id=%s", (doc_id,))
            except BudgetExceeded:
                # cap reached: the document stays queued (text retained) and is retried when budget is available - never silently dropped
                conn.execute("UPDATE source_documents SET raw_meta = raw_meta || '{\"pending_extraction\": true}'::jsonb WHERE id=%s", (doc_id,))
                summary["deferred_budget"] = summary.get("deferred_budget", 0) + 1
                continue
            except ConnectorError as exc:
                # the model provider failed (out of credit, rate limit, outage): keep this and every remaining document queued and stop calling it for this run
                left = [doc_id, *[x for x in [*res.new_doc_ids, *res.changed_doc_ids, *pending] if x != doc_id]]
                conn.execute("UPDATE source_documents SET raw_meta = coalesce(raw_meta,'{}'::jsonb) || '{\"pending_extraction\": true}'::jsonb "
                             "WHERE id = ANY(%s::uuid[]) AND content_text IS NOT NULL AND coalesce(raw_meta->>'triage','') = '' AND coalesce(raw_meta->>'structured','') = '' "
                             "AND NOT EXISTS (SELECT 1 FROM event_documents ed WHERE ed.document_id = source_documents.id)", (left,))
                summary["deferred_provider"] = exc.error_class
                break
            except JobError as exc:       # one bad document must not abort the run: dead-letter it and carry on
                conn.execute("INSERT INTO dead_letters(kind, payload, error_class, error) VALUES ('extract_document',%s,%s,%s)", (jb({"document_id": doc_id}), exc.error_class, str(exc)[:500]))
                conn.execute("UPDATE source_documents SET raw_meta = raw_meta - 'pending_extraction' WHERE id=%s", (doc_id,))
                summary["extraction_failed"] = summary.get("extraction_failed", 0) + 1
                continue
            for e in conn.execute("SELECT DISTINCT e.id FROM events e JOIN event_documents ed ON ed.event_id=e.id WHERE ed.document_id=%s", (doc_id,)).fetchall():
                r = finish_event(conn, str(e["id"]), recheck=recheck, now=now)
                summary["signals"] += len(r["signals"]); summary["negatives"] += len(r["negatives"])
            summary["events_created"] += out.get("events", 0)
    return summary
