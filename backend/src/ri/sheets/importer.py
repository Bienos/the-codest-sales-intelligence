"""Google Sheets import (spec 09 §3 procedure): metadata -> tab discovery -> header contract check ->
bounded paged reads -> immutable staging with source-row refs -> canonical reconciliation -> logged run.

Read-only. Schema drift pauses the affected tab and keeps the previous snapshot; it never guesses columns.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from ..audit import audit
from ..db import Conn, canonical_json, jb, q1
from . import contracts as C
from .client import ConnectorBlocked, SheetsClient, SheetsError, TabInfo
from .reconcile import reconcile_run

PAGE = 500


@dataclass
class ImportResult:
    run_id: str
    status: str
    tabs: dict[str, dict[str, Any]] = field(default_factory=dict)
    reconcile: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    layout: str = "v1"


def normalise_headers(raw: list[str]) -> list[str]:
    """Strip; make duplicate headers unique deterministically ('Buyer Priority', 'Buyer Priority#2')."""
    seen: dict[str, int] = {}
    out = []
    for h in raw:
        h = (h or "").strip()
        seen[h] = seen.get(h, 0) + 1
        out.append(h if seen[h] == 1 or not h else f"{h}#{seen[h]}")
    return out


def _row_hash(values: list[str], links: dict[str, str]) -> str:
    return hashlib.sha256(canonical_json([values, links]).encode()).hexdigest()


def _identity(ct: C.SheetContract, headers: list[str], values: list[str], seen: dict[str, int]) -> str | None:
    idx = {h: i for i, h in enumerate(headers)}
    parts = [(values[idx[h]] if idx[h] < len(values) else "").strip().casefold() for h in ct.identity]
    if not parts[0]:
        return None  # blank company => not a record
    base = "|".join(parts)
    seen[base] = seen.get(base, 0) + 1
    return base if seen[base] == 1 else f"{base}#{seen[base]}"  # duplicated rows stay individually addressable


def _snapshot(conn: Conn, run_id: str, wb: str, tab: TabInfo | None, ct: C.SheetContract, headers: list[str],
              status: str, detail: dict[str, Any], rows: int) -> str:
    row = conn.execute(
        "INSERT INTO sheet_schema_snapshots(import_run_id, workbook_id, sheet_id, sheet_title, headers, header_hash, row_count, status, drift_detail) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (run_id, wb, tab.sheet_id if tab else "", ct.title, jb(headers), C.header_hash(headers), rows, status, jb(detail))).fetchone()
    return str(row["id"])  # type: ignore[index]


def import_workbook(conn: Conn, client: SheetsClient, workbook: str, *, actor: str = "system:sheet-import",
                    workbook_id: str | None = None, reconcile: bool = True) -> ImportResult:
    wb = workbook_id or C.WORKBOOK_IDS[workbook]
    run_id = str(conn.execute("INSERT INTO import_runs(workbook_key, workbook_id, status) VALUES (%s,%s,'running') RETURNING id",
                              (workbook, wb)).fetchone()["id"])  # type: ignore[index]
    conn.commit()
    res = ImportResult(run_id=run_id, status="running")
    try:
        tabs = {t.title: t for t in client.get_tabs(wb)}
    except ConnectorBlocked as exc:
        conn.execute("UPDATE import_runs SET status='blocked', error=%s, finished_at=now() WHERE id=%s", (str(exc), run_id))
        conn.commit()
        res.status, res.error = "blocked", str(exc)
        return res
    except SheetsError as exc:
        conn.execute("UPDATE import_runs SET status='failed', error=%s, finished_at=now() WHERE id=%s", (str(exc), run_id))
        conn.commit()
        res.status, res.error = "failed", str(exc)
        return res

    drift = False
    layout = C.detect_layout(workbook, list(tabs))
    res.layout = layout
    for ct in C.contracts_for(workbook, layout):
        tab = tabs.get(ct.title)
        if tab is None:
            _snapshot(conn, run_id, wb, None, ct, [], "missing", {"reason": "tab not found"}, 0)
            res.tabs[ct.title] = {"status": "missing"}
            drift = True
            continue
        try:
            stats = _import_tab(conn, client, run_id, wb, tab, ct)
        except ConnectorBlocked:
            raise
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            res.tabs[ct.title] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            continue
        conn.commit()
        res.tabs[ct.title] = stats
        drift = drift or stats["status"] == "drift"
    known = {ct.title for ct in C.contracts_for(workbook, layout)}
    for t in tabs:
        if t not in known:
            res.tabs[t] = {"status": "unknown_tab"}   # surfaced to admins; never auto-imported

    if reconcile:
        rc = reconcile_run(conn, run_id, workbook, layout)
        res.reconcile = rc.as_dict()
    failed = any(v.get("status") == "failed" for v in res.tabs.values())
    res.status = "failed" if failed else ("paused_schema_drift" if drift else "completed")
    conn.execute("UPDATE import_runs SET status=%s, finished_at=now(), stats=%s WHERE id=%s",
                 (res.status, jb({"tabs": res.tabs, "reconcile": res.reconcile, "layout": layout}), run_id))
    audit(conn, actor=actor, operation="sheet_import", record_type="import_run", record_id=run_id, after={"status": res.status})
    conn.commit()
    return res


def _import_tab(conn: Conn, client: SheetsClient, run_id: str, wb: str, tab: TabInfo, ct: C.SheetContract) -> dict[str, Any]:
    width = max(tab.column_count, len(ct.headers))
    header_row = client.get_values(wb, tab.title, 1, 1, width)
    raw_headers = header_row[0] if header_row else []
    headers_all = normalise_headers(raw_headers)
    # trailing blank-header columns are tolerated ONLY if they carry no data
    n = len(headers_all)
    while n and not headers_all[n - 1]:
        n -= 1
    headers, blank_cols = headers_all[:n], len(headers_all) - n
    expected = list(ct.headers)
    if headers != expected:
        detail = {"missing": [h for h in expected if h not in headers], "unexpected": [h for h in headers if h not in expected],
                  "order_changed": sorted(headers) == sorted(expected)}
        _snapshot(conn, run_id, wb, tab, ct, headers, "drift", detail, max(tab.row_count - 1, 0))
        audit(conn, actor="system:sheet-import", operation="schema_drift", record_type="sheet", record_id=f"{wb}/{tab.sheet_id}", detail=detail)
        return {"status": "drift", **detail}

    rows: list[list[str]] = []
    first = 2
    while first <= max(tab.row_count, 1):
        last = min(first + PAGE - 1, tab.row_count)
        page = client.get_values(wb, tab.title, first, last, width)
        rows.extend(page + [[] for _ in range((last - first + 1) - len(page))])  # keep row numbers aligned
        first = last + 1
    if blank_cols and any(any((c or "").strip() for c in r[len(headers):]) for r in rows):
        detail = {"reason": "data present under blank headers", "blank_columns": blank_cols}
        _snapshot(conn, run_id, wb, tab, ct, headers, "drift", detail, len(rows))
        return {"status": "drift", **detail}
    while rows and not any((c or "").strip() for c in rows[-1]):
        rows.pop()

    link_cols: dict[str, dict[int, str]] = {}
    for h in ct.hyperlink_cols:
        link_cols[h] = client.get_hyperlinks(wb, tab.title, headers.index(h) + 1, 2, len(rows) + 1)

    _snapshot(conn, run_id, wb, tab, ct, headers, "ok", {"blank_trailing_columns": blank_cols}, len(rows))
    seen: dict[str, int] = {}
    new = changed = unchanged = blank = 0
    live_identities: set[str] = set()
    for i, values in enumerate(rows):
        rownum = i + 2
        values = [(v or "") for v in values[: len(headers)]] + [""] * max(0, len(headers) - len(values))
        ident = _identity(ct, headers, values, seen)
        if ident is None:
            blank += 1
            continue
        live_identities.add(ident)
        links = {h: u for h, m in link_cols.items() if (u := m.get(rownum))}
        rh = _row_hash(values, links)
        prev = q1(conn, "SELECT row_hash FROM staging_sheet_rows WHERE workbook_id=%s AND sheet_id=%s AND row_identity=%s ORDER BY id DESC LIMIT 1",
                  (wb, tab.sheet_id, ident))
        if prev and prev["row_hash"] == rh:
            unchanged += 1
            conn.execute("UPDATE account_source_links SET row_number=%s, observed_at=now() WHERE source_file_id=%s AND sheet_id=%s AND row_identity=%s",
                         (rownum, wb, tab.sheet_id, ident))   # move detection: identity is the key, position is metadata
            continue
        new, changed = (new + 1, changed) if not prev else (new, changed + 1)
        conn.execute(
            "INSERT INTO staging_sheet_rows(import_run_id, workbook_id, sheet_id, sheet_title, row_number, row_identity, row_hash, raw_values, links) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (run_id, wb, tab.sheet_id, tab.title, rownum, ident, rh, jb(values), jb(links)))
    removed = [r["row_identity"] for r in conn.execute(
        "SELECT row_identity FROM account_source_links WHERE source_file_id=%s AND sheet_id=%s", (wb, tab.sheet_id))
        if r["row_identity"] not in live_identities]
    return {"status": "ok", "rows": len(rows), "new": new, "changed": changed, "unchanged": unchanged,
            "blank": blank, "removed_from_source": len(removed)}
