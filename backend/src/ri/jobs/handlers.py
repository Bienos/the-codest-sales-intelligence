"""Job handlers (spec 13 §3 worker types). Handlers receive (conn, job) and raise JobError subclasses to control retries."""
from __future__ import annotations

from typing import Any, Callable

from ..db import Conn, jb
from ..ingest.pipeline import run_source
from ..ingest.process import finish_event, process_run
from ..sources.base import ConnectorError
from .queue import JobError, RequiresApproval


def build_handlers(*, connector_factory: Callable[[str], Any], extractor_factory: Callable[[Conn], Any] | None = None,
                   resolve_org_factory: Callable[[Conn], Callable[[str], str | None]] | None = None,
                   fetch_factory: Callable[[], Callable[[str], Any]] | None = None, discovery_factory: Callable[[Conn], dict[str, Any] | None] | None = None) -> dict[str, Callable[[Conn, dict], dict]]:
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
        if ex is not None and hasattr(ex, "reserve_fraction"):
            ex.reserve_fraction = 0.3          # monitoring never uses the last 30% of the model budget: sellers' checks come first
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
        from ..events.relink import relink_unmatched      # accounts now exist: link everything that arrived before them
        try:
            rl = relink_unmatched(conn, 1500)
            conn.commit()
        except Exception:  # noqa: BLE001  (the import itself succeeded; the scheduler will keep relinking)
            conn.rollback()
            rl = {"checked": 0, "linked": 0}
        return {"import_run_id": res.run_id, "status": res.status, "tabs": {k: v.get("status") for k, v in res.tabs.items()}, "reconcile": res.reconcile}

    def sheet_sync(conn: Conn, job: dict[str, Any]) -> dict[str, Any]:
        """Reads a workbook straight from Google Sheets (service account) and imports it. Needs the workbook shared with the service account."""
        from ..config import Settings
        from ..sheets import contracts as C
        from ..sheets.client import ConnectorBlocked, GoogleSheetsClient
        from ..sheets.importer import import_workbook
        key = job["payload"]["workbook"]
        st = Settings.from_env(require_auth=False)
        conn.execute("UPDATE jobs SET lease_expires_at = now() + interval '2 hours' WHERE id=%s", (job["id"],))
        conn.commit()
        try:
            client = GoogleSheetsClient(st.google_credentials_file, credentials_json=st.google_service_account_json)
            res = import_workbook(conn, client, key, actor=f"sync:{job['payload'].get('requested_by', 'system')}")
        except ConnectorBlocked as exc:
            conn.rollback()
            raise JobError(str(exc)[:300], "access_denied") from exc
        conn.commit()
        from ..events.relink import relink_unmatched
        try:
            relink_unmatched(conn, 1500)
            conn.commit()
        except Exception:  # noqa: BLE001
            conn.rollback()
        return {"import_run_id": res.run_id, "status": res.status, "tabs": {k: v.get("status") for k, v in res.tabs.items()}, "reconcile": res.reconcile}

    def account_brief(conn: Conn, job: dict[str, Any]) -> dict[str, Any]:
        """Writes a cited research brief for one company. Budget-gated like every other model call; a failure is recorded on the brief, never silent."""
        import httpx

        from .. import budget, usage
        from ..config import LIST_PRICES, Settings
        from ..db import q1 as _q1
        from ..ingest.pipeline import _run
        from ..research.brief import generate
        from ..sources.base import BudgetExceeded
        from ..sources.http import request_json
        st = Settings.from_env(require_auth=False)
        bid = job["payload"]["brief_id"]
        b = _q1(conn, "SELECT * FROM account_briefs WHERE id=%s", (bid,))
        if not b:
            raise JobError("brief not found", "not_found")
        if not st.anthropic_api_key:
            conn.execute("UPDATE account_briefs SET status='failed', error='AI is not configured (no API key).', finished_at=now() WHERE id=%s", (bid,))
            return {"status": "failed"}
        conn.execute("UPDATE account_briefs SET status='running' WHERE id=%s", (bid,))
        conn.commit()
        period = usage.period_now()

        async def call(system: str, user: str):
            est = float(len(system + user) // 3 + 5000)
            key = f"brief:{bid}"
            budget.reserve(conn, "anthropic", "monthly", period, est, key=key, ref_type="brief", purpose="account brief")
            conn.commit()
            hdrs = {"x-api-key": st.anthropic_api_key, "anthropic-version": "2023-06-01"}
            last_exc: Exception | None = None
            for model in dict.fromkeys([st.anthropic_research_model, st.anthropic_model]):
                try:
                    async with httpx.AsyncClient(timeout=180) as c:
                        data = await request_json(c, "POST", "https://api.anthropic.com/v1/messages", headers=hdrs,
                                                  json={"model": model, "max_tokens": 6000, "system": system, "messages": [{"role": "user", "content": user}]})
                    text = "".join(x.get("text", "") for x in data.get("content", []) if x.get("type") == "text")
                    if not text.strip():
                        raise ValueError("empty model output")
                    u = data.get("usage") or {}
                    tin, tout = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
                    pin, pout = LIST_PRICES.get(model, (st.anthropic_price_in_per_mtok, st.anthropic_price_out_per_mtok))
                    cost, basis = usage.model_cost(tin, tout, pin, pout)
                    budget.settle(conn, "anthropic", "monthly", period, est, float(tin + tout), key=key)
                    usage.record(conn, provider="anthropic", operation="account_brief", units=float(tin + tout), unit="tokens", tokens_in=tin, tokens_out=tout, cost_usd=cost, cost_basis=basis,
                                 organization_id=str(b["organization_id"]), detail={"model": model})
                    conn.commit()
                    return text, tin, tout, model
                except Exception as exc:  # noqa: BLE001 - try the reading model once before giving up
                    last_exc = exc
            budget.release(conn, "anthropic", "monthly", period, est, key=key)
            conn.commit()
            raise last_exc  # type: ignore[misc]
        try:
            r = generate(conn, str(b["organization_id"]), call, _run)
        except BudgetExceeded as exc:
            conn.rollback()
            conn.execute("UPDATE account_briefs SET status='failed', error=%s, finished_at=now() WHERE id=%s", (f"Not enough AI budget left this month: {exc}"[:300], bid))
            return {"status": "failed", "reason": "budget"}
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            conn.execute("UPDATE account_briefs SET status='failed', error=%s, finished_at=now() WHERE id=%s", (f"{type(exc).__name__}: {exc}"[:300], bid))
            return {"status": "failed", "reason": type(exc).__name__}
        conn.execute("UPDATE account_briefs SET status='done', content=%s, dropped=%s, model=%s, tokens_in=%s, tokens_out=%s, finished_at=now() WHERE id=%s",
                     (jb(r["content"]), r["dropped"], r["model"], r["tokens_in"], r["tokens_out"], bid))
        return {"status": "done", "dropped": r["dropped"]}

    account_brief.own_transactions = True  # type: ignore[attr-defined]

    def campaign_draft(conn: Conn, job: dict[str, Any]) -> dict[str, Any]:
        """Writes and brand-checks a draft email sequence for one company. Drafts only."""
        from ..config import Settings
        from ..db import q1 as _q1
        from ..ingest.pipeline import _run
        from ..outbound.writer import write
        from ..sources.base import BudgetExceeded
        from .modelcall import make_call
        st = Settings.from_env(require_auth=False)
        did = job["payload"]["draft_id"]
        d = _q1(conn, "SELECT * FROM campaign_drafts WHERE id=%s", (did,))
        if not d:
            raise JobError("draft not found", "not_found")
        if not st.anthropic_api_key:
            conn.execute("UPDATE campaign_drafts SET status='failed', error='AI is not configured (no API key).', finished_at=now() WHERE id=%s", (did,))
            return {"status": "failed"}
        conn.execute("UPDATE campaign_drafts SET status='running' WHERE id=%s", (did,))
        conn.commit()
        call = make_call(conn, st, org_id=str(d["organization_id"]), operation="campaign_draft", key_prefix=f"draft:{did}", models=[st.anthropic_research_model, st.anthropic_model])
        try:
            r = write(conn, str(d["organization_id"]), d["role"], call, _run)
        except BudgetExceeded as exc:
            conn.rollback()
            conn.execute("UPDATE campaign_drafts SET status='failed', error=%s, finished_at=now() WHERE id=%s", (f"Not enough AI budget left this month: {exc}"[:300], did))
            return {"status": "failed", "reason": "budget"}
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            conn.execute("UPDATE campaign_drafts SET status='failed', error=%s, finished_at=now() WHERE id=%s", (f"{type(exc).__name__}: {exc}"[:300], did))
            return {"status": "failed", "reason": type(exc).__name__}
        conn.execute("UPDATE campaign_drafts SET status=%s, steps=%s, review=%s, rounds=%s, facts=%s, model=%s, tokens_in=%s, tokens_out=%s, finished_at=now() WHERE id=%s",
                     (r["status"], jb(r["steps"]), jb(r["review"]), r["rounds"], jb(r["facts"]), r["model"], r["tokens_in"], r["tokens_out"], did))
        return {"status": r["status"], "rounds": r["rounds"]}

    campaign_draft.own_transactions = True  # type: ignore[attr-defined]

    def verify_url(conn: Conn, job: dict[str, Any]) -> dict[str, Any]:
        from ..verify.run import run_verification
        if extractor_factory is None or fetch_factory is None:
            raise JobError("verification is not configured on this worker", "access_denied")
        ex = extractor_factory(conn)
        if ex is None:
            conn.execute("UPDATE verification_requests SET status='waiting_budget', updated_at=now(), result=%s WHERE id=%s",
                         ('{"reason": "no Anthropic API key configured on the worker"}', job["payload"]["request_id"]))
            return {"status": "waiting_budget", "reason": "no model key"}
        resolver = resolve_org_factory(conn) if resolve_org_factory else None
        rid = job["payload"]["request_id"]
        try:
            return run_verification(conn, rid, fetch=fetch_factory(), extractor=ex, resolver=resolver)
        except JobError as exc:
            conn.rollback()
            retry = exc.error_class in {"rate_limited", "transient_network", "server_error", "timeout"} and int(job.get("attempts") or 1) < int(job.get("max_attempts") or 3)
            conn.execute("UPDATE verification_requests SET status=%s, updated_at=now(), result=%s WHERE id=%s", ("queued" if retry else "failed", jb({"reason": str(exc)[:300], "error_class": exc.error_class}), rid))
            conn.commit()
            raise
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            conn.execute("UPDATE verification_requests SET status='failed', updated_at=now(), result=%s WHERE id=%s", (jb({"reason": f"{type(exc).__name__}: {exc}"[:300]}), rid))
            conn.commit()
            raise JobError(f"{type(exc).__name__}: {exc}"[:300], "invalid_payload") from exc

    def discovery_run(conn: Conn, job: dict[str, Any]) -> dict[str, Any]:
        from ..discovery.run import run_discovery
        cfg = discovery_factory(conn) if discovery_factory else None
        if cfg is None:
            return {"skipped": "Exa is not configured (no key or no budget cap)"}
        return run_discovery(conn, cfg.pop("exa"), plan_ids=job["payload"].get("plan_ids"), requested_by=job["payload"].get("requested_by", "system:discovery"), **cfg)

    def sources_validate_all(conn: Conn, job: dict[str, Any]) -> dict[str, Any]:
        from ..sources import registry
        only = job["payload"].get("only")
        rows = conn.execute("SELECT source_id FROM sources WHERE collection_method <> 'internal' AND status <> 'active' AND (%s::text[] IS NULL OR source_id = ANY(%s)) ORDER BY source_tier, source_id", (only, only)).fetchall()
        out: dict[str, Any] = {}
        for r in rows:
            sid = r["source_id"]
            try:
                out[sid] = registry.validate_and_maybe_activate(conn, sid, activate=bool(job["payload"].get("activate")), by=job["payload"].get("by", "system:bulk-validate"))
                conn.commit()
            except Exception as exc:  # noqa: BLE001  (one bad source must not stop the rest)
                conn.rollback()
                out[sid] = {"result": "error", "reason": f"{type(exc).__name__}: {exc}"[:200]}
        summary: dict[str, int] = {}
        for v in out.values():
            summary[v["result"]] = summary.get(v["result"], 0) + 1
        return {"summary": summary, "sources": {k: (v.get("reason") or v.get("detail") or v["result"]) for k, v in out.items()}}

    source_run.own_transactions = True  # type: ignore[attr-defined]     # run_source commits per page of work; it must not sit inside a wrapping transaction
    sources_validate_all.own_transactions = True  # type: ignore[attr-defined]
    verify_url.own_transactions = True  # type: ignore[attr-defined]
    def account_scan(conn: Conn, job: dict[str, Any]) -> dict[str, Any]:
        from ..sources.account_scan import run_scan
        from ..sources.safe import SafeClient
        resolver = resolve_org_factory(conn) if resolve_org_factory else None
        ex = extractor_factory(conn) if extractor_factory else None
        if ex is not None and hasattr(ex, "reserve_fraction"):
            ex.reserve_fraction = 0.3
        return run_scan(conn, job["payload"]["organization_id"], client=SafeClient(timeout=30), extractor=ex, resolver=resolver, force_discover=bool(job["payload"].get("rediscover")))

    account_scan.own_transactions = True  # type: ignore[attr-defined]
    discovery_run.own_transactions = True  # type: ignore[attr-defined]
    sheet_sync.own_transactions = True  # type: ignore[attr-defined]
    sheet_import_xlsx.own_transactions = True  # type: ignore[attr-defined]
    return {"source_run": source_run, "evaluate_event": evaluate, "approval_gate": approval_gate, "sheet_import_xlsx": sheet_import_xlsx, "verify_url": verify_url, "discovery_run": discovery_run, "sources_validate_all": sources_validate_all, "account_scan": account_scan, "sheet_sync": sheet_sync, "account_brief": account_brief, "campaign_draft": campaign_draft}


__all__ = ["build_handlers", "ConnectorError"]
