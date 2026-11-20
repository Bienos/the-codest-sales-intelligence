"""Job handlers (spec 13 §3 worker types). Handlers receive (conn, job) and raise JobError subclasses to control retries."""
from __future__ import annotations

from typing import Any, Callable

from ..db import Conn
from ..ingest.pipeline import run_source
from ..ingest.process import finish_event, process_run
from ..sources.base import ConnectorError
from .queue import JobError, RequiresApproval


def build_handlers(*, connector_factory: Callable[[str], Any], extractor_factory: Callable[[], Any] | None = None,
                   resolve_org_factory: Callable[[Conn], Callable[[str], str | None]] | None = None) -> dict[str, Callable[[Conn, dict], dict]]:
    def source_run(conn: Conn, job: dict[str, Any]) -> dict[str, Any]:
        sid = job["payload"]["source_id"]
        connector = connector_factory(sid)
        resolver = resolve_org_factory(conn) if resolve_org_factory else None
        res = run_source(conn, connector, resolve_org=resolver, job_id=str(job["id"]))
        if res.status in {"failed", "partial"} and res.error_class:
            raise JobError(res.error or "source run failed", res.error_class)
        if res.status == "skipped":
            return {"skipped": res.error}
        ex = extractor_factory() if extractor_factory else None
        out = process_run(conn, res, extractor=ex, resolve_org=resolver)
        return {"run_id": res.run_id, "new": res.new, "changed": res.changed, "unchanged": res.unchanged, **out}

    def evaluate(conn: Conn, job: dict[str, Any]) -> dict[str, Any]:
        return finish_event(conn, job["payload"]["event_id"])

    def approval_gate(conn: Conn, job: dict[str, Any]) -> dict[str, Any]:
        raise RequiresApproval("this job class requires an approval record")

    return {"source_run": source_run, "evaluate_event": evaluate, "approval_gate": approval_gate}


__all__ = ["build_handlers", "ConnectorError"]
