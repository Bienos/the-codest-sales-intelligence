"""Job handlers (spec 13 §3 worker types). Handlers receive (conn, job) and raise JobError subclasses to control retries."""
from __future__ import annotations

from typing import Any, Callable

from ..db import Conn
from ..ingest.pipeline import run_source
from ..ingest.process import finish_event, process_run
from ..sources.base import ConnectorError
from .queue import JobError, RequiresApproval


def build_handlers(*, connector_factory: Callable[[str], Any], extractor_factory: Callable[[Conn], Any] | None = None,
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
        ex = extractor_factory(conn) if extractor_factory else None
        out = process_run(conn, res, extractor=ex, resolve_org=resolver)
        return {"run_id": res.run_id, "new": res.new, "changed": res.changed, "unchanged": res.unchanged, **out}

    def evaluate(conn: Conn, job: dict[str, Any]) -> dict[str, Any]:
        return finish_event(conn, job["payload"]["event_id"])

    def approval_gate(conn: Conn, job: dict[str, Any]) -> dict[str, Any]:
        raise RequiresApproval("this job class requires an approval record")

    def sheet_import_xlsx(conn: Conn, job: dict[str, Any]) -> dict[str, Any]:
        import io

        from ..db import q1
        from ..sheets import contracts as C
        from ..sheets.client import XlsxSheetsClient
        from ..sheets.importer import import_workbook
        up = q1(conn, "SELECT * FROM import_uploads WHERE id=%s", (job["payload"]["upload_id"],))
        if not up or up["content"] is None:
            raise JobError("upload not found or already consumed", "invalid_payload")
        conn.execute("UPDATE import_uploads SET status='importing' WHERE id=%s", (up["id"],))
        # a full import over a remote database can outlast the default 5-minute lease; the importer is idempotent so a crash is safe to reclaim later
        conn.execute("UPDATE jobs SET lease_expires_at = now() + interval '2 hours' WHERE id=%s", (job["id"],))
        conn.commit()
        try:
            client = XlsxSheetsClient(C.WORKBOOK_IDS[up["workbook_key"]], io.BytesIO(bytes(up["content"])))
            res = import_workbook(conn, client, up["workbook_key"], actor=f"upload:{up['uploaded_by']}")
        except Exception as exc:   # noqa: BLE001
            conn.rollback()
            conn.execute("UPDATE import_uploads SET status='failed', error=%s, content=NULL, processed_at=now() WHERE id=%s", (f"{type(exc).__name__}: {exc}"[:500], up["id"]))
            conn.commit()
            raise JobError(str(exc)[:300], "invalid_payload") from exc
        conn.execute("UPDATE import_uploads SET status=%s, import_run_id=%s, content=NULL, processed_at=now() WHERE id=%s",
                     ("completed" if res.status == "completed" else "failed", res.run_id, up["id"]))
        conn.commit()
        return {"import_run_id": res.run_id, "status": res.status, "tabs": {k: v.get("status") for k, v in res.tabs.items()}, "reconcile": res.reconcile}

    sheet_import_xlsx.own_transactions = True  # type: ignore[attr-defined]
    return {"source_run": source_run, "evaluate_event": evaluate, "approval_gate": approval_gate, "sheet_import_xlsx": sheet_import_xlsx}


__all__ = ["build_handlers", "ConnectorError"]
