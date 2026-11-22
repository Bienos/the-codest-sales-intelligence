"""Source ingestion (spec 02 §6): cursor-after-commit, change detection, health classification, replay-safe.

Idempotency key for ingestion: (source_id, external_id, version_key). A cursor advances only after the page's
documents/records are durably committed. A failed or partial window is recorded as coverage loss - never as
'no events' (spec 02 §9, 12 §1).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from ..audit import audit
from ..db import Conn, jb, q1
from ..sources.base import ConnectorError, SourceConnector, SourceDocumentDraft
from ..sources.util import sha, utcnow

DEGRADE_AFTER = 3          # consecutive failures before 'degraded' for transient classes
MAX_PAGES = 50
ACTIVE_STATES = ("active", "degraded")


@dataclass
class RunResult:
    run_id: str
    status: str
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    unavailable: int = 0
    new_doc_ids: list[str] = field(default_factory=list)
    changed_doc_ids: list[str] = field(default_factory=list)
    structured_changes: list[tuple[str, str, str]] = field(default_factory=list)   # (kind, record_id, change)
    error_class: str | None = None
    error: str | None = None


def upsert_document(conn: Conn, source_id: str, d: SourceDocumentDraft, retention: str) -> tuple[str, str]:
    """Returns (document_id, change_status in new|changed|unchanged)."""
    same = q1(conn, "SELECT id FROM source_documents WHERE source_id=%s AND external_id=%s AND version_key=%s",
              (source_id, d.external_id, d.version_key))
    if same:
        return str(same["id"]), "unchanged"
    prev = q1(conn, "SELECT id FROM source_documents WHERE source_id=%s AND external_id=%s ORDER BY fetched_at DESC LIMIT 1",
              (source_id, d.external_id))
    status = "changed" if prev else "new"
    keep_text = d.content_text if d.content_text is not None else None
    row = conn.execute(
        "INSERT INTO source_documents(source_id, external_id, version_key, canonical_url, title, language, published_at, content_hash, "
        "change_status, excerpt, content_text, raw_meta) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (source_id, d.external_id, d.version_key, d.canonical_url, d.title, d.language, d.published_at,
         sha(keep_text) if keep_text else None, status, d.excerpt if retention != "metadata_only" else None, keep_text,
         jb({**d.raw_meta, **({"structured": True} if d.kind != "document" else {})}))).fetchone()
    return str(row["id"]), status  # type: ignore[index]


def upsert_notice(conn: Conn, source_id: str, rec: dict[str, Any], doc_id: str, org_id: str | None) -> tuple[str, str]:
    """Normalised tender record. A change in deadline/status/title updates the SAME notice (never a duplicate lead)."""
    cur = q1(conn, "SELECT * FROM procurement_notices WHERE source_id=%s AND notice_id=%s", (source_id, rec["notice_id"]))
    vals = (rec.get("buyer_name"), org_id, rec.get("title"), rec.get("scope"), rec.get("procedure"), rec.get("service_codes") or [], rec.get("country"),
            rec.get("published_at"), rec.get("amended_at"), rec.get("deadline_at"), rec.get("deadline_tz"), rec["status"], rec.get("official_url"),
            rec.get("value_if_reported"), doc_id)
    if not cur:
        row = conn.execute(
            "INSERT INTO procurement_notices(source_id, notice_id, buyer_name, buyer_org_id, title, scope, procedure, service_codes, country, published_at, "
            "amended_at, deadline_at, deadline_tz, status, official_url, value_if_reported, document_id, last_status_check_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) RETURNING id", (source_id, rec["notice_id"], *vals)).fetchone()
        return str(row["id"]), "new"  # type: ignore[index]
    changes = []
    if rec.get("deadline_at") != cur["deadline_at"]:
        changes.append("deadline")
    if rec["status"] != cur["status"]:
        changes.append("status")
    if rec.get("title") != cur["title"]:
        changes.append("title")
    amended_at = rec.get("amended_at") or (rec.get("published_at") if changes else cur["amended_at"])
    conn.execute(
        "UPDATE procurement_notices SET buyer_name=%s, buyer_org_id=COALESCE(%s,buyer_org_id), title=%s, scope=%s, procedure=%s, service_codes=%s, "
        "country=%s, published_at=COALESCE(published_at,%s), amended_at=%s, deadline_at=%s, deadline_tz=%s, status=%s, official_url=%s, "
        "value_if_reported=%s, document_id=%s, last_status_check_at=now(), updated_at=now() WHERE id=%s",
        (vals[0], vals[1], vals[2], vals[3], vals[4], vals[5], vals[6], vals[7], amended_at, vals[9], vals[10], vals[11], vals[12], vals[13], doc_id, cur["id"]))
    return str(cur["id"]), ("amended:" + ",".join(changes)) if changes else "unchanged"


def upsert_job(conn: Conn, rec: dict[str, Any], org_id: str | None, now) -> tuple[str, str]:
    cur = q1(conn, "SELECT * FROM job_postings WHERE provider=%s AND board=%s AND job_id=%s", (rec["provider"], rec["board"], rec["job_id"]))
    if not cur:
        row = conn.execute(
            "INSERT INTO job_postings(provider, board, job_id, organization_id, employer_name, title, team, location, employment_type, posted_at, "
            "updated_at_source, open_status, url, tech_keywords, last_seen_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open',%s,%s,%s) RETURNING id",
            (rec["provider"], rec["board"], rec["job_id"], org_id, rec.get("employer_name"), rec["title"], rec.get("team"), rec.get("location"),
             rec["employment_type"], rec.get("posted_at"), rec.get("updated_at_source"), rec.get("url"), rec.get("tech_keywords") or [], now)).fetchone()
        return str(row["id"]), "new"  # type: ignore[index]
    change = "unchanged"
    if cur["open_status"] != "open":
        change = "reopened"
    elif (rec["title"], rec["employment_type"]) != (cur["title"], cur["employment_type"]):
        change = "changed"
    conn.execute("UPDATE job_postings SET title=%s, team=%s, location=%s, employment_type=%s, updated_at_source=%s, open_status='open', closed_at=NULL, "
                 "url=%s, tech_keywords=%s, last_seen_at=%s WHERE id=%s",
                 (rec["title"], rec.get("team"), rec.get("location"), rec["employment_type"], rec.get("updated_at_source"), rec.get("url"),
                  rec.get("tech_keywords") or [], now, cur["id"]))
    return str(cur["id"]), change


def close_unseen_jobs(conn: Conn, provider: str, board: str, seen_ids: set[str], now) -> list[str]:
    """ONLY called after an affirmative complete listing. Returns closed job row ids."""
    closed = []
    for r in conn.execute("SELECT id, job_id FROM job_postings WHERE provider=%s AND board=%s AND open_status='open'", (provider, board)).fetchall():
        if r["job_id"] not in seen_ids:
            conn.execute("UPDATE job_postings SET open_status='closed', closed_at=%s WHERE id=%s", (now, r["id"]))
            closed.append(str(r["id"]))
    return closed


def _record_failure(conn: Conn, source_id: str, exc: ConnectorError | Exception, now) -> None:
    ec = getattr(exc, "error_class", "transient_network")
    h = q1(conn, "SELECT consecutive_failures FROM source_health WHERE source_id=%s", (source_id,)) or {"consecutive_failures": 0}
    n = h["consecutive_failures"] + 1
    # human-action classes fail immediately; transient classes degrade after a threshold
    if type(exc).__name__ == "EgressBlocked":
        state = "blocked"            # environment/network policy, not a credential or provider fault
    elif ec in {"auth", "access_denied", "policy_block", "schema_drift"}:
        state = "failed"
    else:
        state = "degraded" if n >= DEGRADE_AFTER else "healthy"
    conn.execute("INSERT INTO source_health(source_id, state, last_attempt_at, consecutive_failures, error_class, detail, updated_at) "
                 "VALUES (%s,%s,%s,%s,%s,%s,now()) ON CONFLICT (source_id) DO UPDATE SET state=%s, last_attempt_at=%s, consecutive_failures=%s, "
                 "error_class=%s, detail=%s, updated_at=now()",
                 (source_id, state, now, n, ec, str(exc)[:500], state, now, n, ec, str(exc)[:500]))
    if state in {"failed", "blocked"}:
        conn.execute("UPDATE sources SET status=%s WHERE source_id=%s AND status IN ('active','degraded')",
                     ("blocked" if state == "blocked" else "failed", source_id))
    elif state == "degraded":
        conn.execute("UPDATE sources SET status='degraded' WHERE source_id=%s AND status='active'", (source_id,))


def _record_success(conn: Conn, source_id: str, now) -> None:
    conn.execute("INSERT INTO source_health(source_id, state, last_success_at, last_attempt_at, consecutive_failures, missed_due_runs, updated_at) "
                 "VALUES (%s,'healthy',%s,%s,0,0,now()) ON CONFLICT (source_id) DO UPDATE SET state='healthy', last_success_at=%s, "
                 "last_attempt_at=%s, consecutive_failures=0, missed_due_runs=0, error_class=NULL, detail=NULL, updated_at=now()",
                 (source_id, now, now, now, now))
    conn.execute("UPDATE sources SET status='active' WHERE source_id=%s AND status='degraded'", (source_id,))


def run_source(conn: Conn, connector: SourceConnector, *, resolve_org=None, limit: int = 100, max_pages: int = MAX_PAGES,
               job_id: str | None = None, allow_pending: bool = False, now_fn=utcnow) -> RunResult:
    """Run one monitoring cycle. `resolve_org(name) -> org_id|None` links buyers/employers to canonical organisations."""
    desc = connector.describe()
    src = q1(conn, "SELECT * FROM sources WHERE source_id=%s", (desc.source_id,))
    if not src:
        raise KeyError(f"unregistered source {desc.source_id}")
    now = now_fn()
    if src["status"] not in ACTIVE_STATES and not (allow_pending and src["status"] == "pending_connector_validation"):
        rid = conn.execute("INSERT INTO monitoring_runs(source_id, job_id, status, finished_at, error_class, error) VALUES (%s,%s,'skipped',now(),'policy_block',%s) RETURNING id",
                           (desc.source_id, job_id, f"source status is {src['status']}: connector not activated")).fetchone()["id"]  # type: ignore[index]
        return RunResult(str(rid), "skipped", error_class="policy_block", error=f"source status {src['status']}")
    cur_row = q1(conn, "SELECT cursor_value FROM source_cursors WHERE source_id=%s AND cursor_key='default'", (desc.source_id,))
    cursor = cur_row["cursor_value"] if cur_row else None
    run_id = str(conn.execute("INSERT INTO monitoring_runs(source_id, job_id, cursor_before) VALUES (%s,%s,%s) RETURNING id",
                              (desc.source_id, job_id, cursor)).fetchone()["id"])  # type: ignore[index]
    conn.commit()
    res = RunResult(run_id, "running")
    seen_jobs: dict[tuple[str, str], set[str]] = {}
    complete_listing = False
    usage: dict[str, Any] = {}
    try:
        for _ in range(max_pages):
            batch = _run(connector.fetch(cursor, limit))
            complete_listing = batch.complete_listing
            usage["pages"] = usage.get("pages", 0) + 1
            usage["items"] = usage.get("items", 0) + len(batch.items)
            for k, v in (batch.quota_usage or {}).items():          # what the connector saw versus kept: a run that fetches nothing is visible, not a silent "success"
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    usage[k] = usage.get(k, 0) + v
            with conn.transaction():
                for item in batch.items:
                    d = connector.canonicalize(item)
                    doc_id, st = upsert_document(conn, desc.source_id, d, src["retention_policy"])
                    if st == "unchanged":
                        res.unchanged += 1
                    elif st == "new":
                        res.new += 1; res.new_doc_ids.append(doc_id)
                    else:
                        res.changed += 1; res.changed_doc_ids.append(doc_id)
                    if d.kind == "procurement_notice" and d.structured:
                        org = resolve_org(d.structured.get("buyer_name")) if resolve_org else None
                        nid, ch = upsert_notice(conn, desc.source_id, d.structured, doc_id, org)
                        if ch != "unchanged":
                            res.structured_changes.append(("procurement", nid, ch))
                    elif d.kind == "job_posting" and d.structured:
                        org = (src["config"] or {}).get("organization_id")
                        key = (d.structured["provider"], d.structured["board"])
                        seen_jobs.setdefault(key, set()).add(d.structured["job_id"])
                        jid, ch = upsert_job(conn, d.structured, org, now)
                        if ch != "unchanged":
                            res.structured_changes.append(("job", jid, ch))
                if complete_listing and not batch.has_more:
                    for (prov, board), ids in seen_jobs.items():
                        for closed_id in close_unseen_jobs(conn, prov, board, ids, now):
                            res.structured_changes.append(("job", closed_id, "closed"))
                    if not seen_jobs and (src["config"] or {}).get("board"):   # empty AFFIRMATIVE listing closes everything on that board
                        for closed_id in close_unseen_jobs(conn, (src["config"] or {}).get("provider", desc.provider), src["config"]["board"], set(), now):
                            res.structured_changes.append(("job", closed_id, "closed"))
                # cursor is persisted in the SAME transaction as the data it covers
                if batch.next_cursor is not None or not batch.has_more:
                    conn.execute("INSERT INTO source_cursors(source_id, cursor_key, cursor_value, updated_at) VALUES (%s,'default',%s,now()) "
                                 "ON CONFLICT (source_id, cursor_key) DO UPDATE SET cursor_value=EXCLUDED.cursor_value, updated_at=now()",
                                 (desc.source_id, batch.next_cursor))
                cursor = batch.next_cursor
            conn.commit()
            if not batch.has_more:
                break
        conn.execute("UPDATE monitoring_runs SET status='success', finished_at=now(), items_new=%s, items_changed=%s, items_unchanged=%s, cursor_after=%s, usage=%s WHERE id=%s",
                     (res.new, res.changed, res.unchanged, cursor, jb(usage), run_id))
        _record_success(conn, desc.source_id, now)
        res.status = "success"
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        ec = getattr(exc, "error_class", "transient_network")
        conn.execute("UPDATE monitoring_runs SET status=%s, finished_at=now(), error_class=%s, error=%s, items_new=%s, items_changed=%s WHERE id=%s",
                     ("partial" if res.new or res.changed else "failed", ec, str(exc)[:500], res.new, res.changed, run_id))
        _record_failure(conn, desc.source_id, exc, now)
        audit(conn, actor="system:ingest", operation="source_run_failed", record_type="source", record_id=desc.source_id,
              detail={"error_class": ec, "run_id": run_id})
        res.status, res.error_class, res.error = ("partial" if res.new or res.changed else "failed"), ec, str(exc)[:500]
        conn.commit()
        return res
    conn.commit()
    return res


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("run_source is synchronous; use run_source_async from a running event loop") from None   # pragma: no cover


def evaluate_staleness(conn: Conn, now_fn=utcnow) -> list[str]:
    """Missed due runs => degraded coverage (never an absent-event signal). Returns degraded source ids."""
    out = []
    now = now_fn()
    for s in conn.execute("SELECT s.source_id, s.poll_interval_s, s.status, h.last_success_at FROM sources s JOIN source_health h USING (source_id) "
                          "WHERE s.status IN ('active','degraded')").fetchall():
        last = s["last_success_at"]
        if last is None:
            continue
        missed = int((now - last) / timedelta(seconds=s["poll_interval_s"])) - 1
        if missed >= 1:
            conn.execute("UPDATE source_health SET missed_due_runs=%s, state=CASE WHEN state='healthy' THEN 'degraded' ELSE state END, updated_at=now() WHERE source_id=%s",
                         (missed, s["source_id"]))
            conn.execute("UPDATE sources SET status='degraded' WHERE source_id=%s AND status='active'", (s["source_id"],))
            out.append(s["source_id"])
    return out
