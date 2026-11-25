"""Verify a claim by reading its ORIGINAL source: fetch -> document -> extract (budgeted, quote-anchored) -> events -> gates.
A sheet signal or a discovery hit never becomes an opportunity on its own: only events whose claims quote the fetched page do."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from ..db import Conn, jb, q1, qa
from ..events.extract import process_document
from ..ingest.process import finish_event
from ..jobs.queue import JobError
from ..sources.base import BudgetExceeded, ConnectorError, PolicyBlocked
from .fetch import Page

SOURCE_ID = "verify-original-page"          # default and lowest authority (S4); see classify_source
_OFFICIAL_SUFFIX = (".gov", ".gov.uk", ".gouv.fr", ".europa.eu", ".admin.ch", ".bund.de", ".gov.pl")


def _host(url: str) -> str:
    from urllib.parse import urlparse
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _under(host: str, domain: str | None) -> bool:
    d = (domain or "").lower().removeprefix("www.")
    return bool(d) and (host == d or host.endswith("." + d))


def classify_source(conn: Conn, url: str, org_id: str | None) -> str:
    """Authority of a fetched page, from its address: official sites S1, the company's own site S2, a known vendor's S3, otherwise S4.
    Never raised on a guess: an unknown site stays at the lowest tier so it cannot switch on a signal by itself."""
    host = _host(url)
    for r in qa(conn, "SELECT canonical_domain, source_tier FROM sources WHERE canonical_domain IS NOT NULL AND collection_method <> 'internal'"):
        if _under(host, r["canonical_domain"]):
            return {"S1": "verify-official-page", "S2": "verify-company-page", "S3": "verify-vendor-page"}.get(r["source_tier"], SOURCE_ID)
    if host.endswith(_OFFICIAL_SUFFIX):
        return "verify-official-page"
    if org_id:
        o = q1(conn, "SELECT canonical_domain FROM organizations WHERE id=%s", (org_id,))
        if o and _under(host, o["canonical_domain"]):
            return "verify-company-page"
    return SOURCE_ID
RESERVE_FOR_SELLERS = 0.30


def _set(conn: Conn, rid: str, status: str, **fields: Any) -> None:
    sets = ["status=%s", "updated_at=now()"]
    vals: list[Any] = [status]
    for k, v in fields.items():
        sets.append(f"{k}=%s")
        vals.append(jb(v) if k == "result" else v)
    conn.execute(f"UPDATE verification_requests SET {', '.join(sets)} WHERE id=%s", (*vals, rid))   # noqa: S608  (column names are code-controlled)


def org_aware_resolver(conn: Conn, resolver: Callable[[str], str | None] | None, org_id: str | None) -> Callable[[str], str | None]:
    """Resolve an event's subject without borrowing the requesting account's identity."""
    from ..identity.resolver import resolve as resolve_identity

    def resolve(name: str) -> str | None:
        if not name:
            return None
        # For an account check, the identity resolver must be able to distinguish
        # legal entities even when the request itself names one of the candidates.
        if org_id or resolver is None:
            decision = resolve_identity(conn, name=name, allow_create=False)
            return decision.org_id if decision.action == "auto_link" else None
        return resolver(name)
    return resolve


def run_verification(conn: Conn, request_id: str, *, fetch: Callable[[str], Page], extractor: Any,
                     resolver: Callable[[str], str | None] | None = None, now: datetime | None = None) -> dict[str, Any]:
    r = q1(conn, "SELECT * FROM verification_requests WHERE id=%s", (request_id,))
    if not r:
        raise JobError("verification request not found", "not_found")
    if r["status"] in {"verified", "no_event"}:
        return {"status": r["status"], "already_done": True}
    rid = str(r["id"])
    if r["origin"] == "exa_discovery":
        # discovery must never use up the model budget that sellers rely on: the last 30% is reserved for seller-requested checks
        b = q1(conn, "SELECT cap, reserved, spent FROM provider_budgets WHERE provider='anthropic' AND kind='monthly' AND period=to_char(now() AT TIME ZONE 'UTC','YYYY-MM')")
        if b and float(b["cap"]) > 0 and (float(b["cap"]) - float(b["reserved"]) - float(b["spent"])) / float(b["cap"]) < RESERVE_FOR_SELLERS:
            _set(conn, rid, "waiting_budget", result={"reason": "kept in reserve: discovery stops using model budget once 70% of the month is spent, so seller checks always have room"})
            return {"status": "waiting_budget", "reason": "seller_reserve"}
    _set(conn, rid, "fetching")
    conn.commit()
    try:
        page = fetch(r["url"])
    except PolicyBlocked as exc:
        _set(conn, rid, "blocked", result={"reason": str(exc)})
        return {"status": "blocked", "reason": str(exc)}
    except ConnectorError as exc:
        _set(conn, rid, "failed", result={"reason": str(exc), "error_class": exc.error_class})
        if exc.error_class in {"transient_network", "server_error", "rate_limited"}:
            raise JobError(str(exc), exc.error_class) from exc       # queue retries with backoff
        return {"status": "failed", "reason": str(exc)}
    if len(page.text) < 200:
        _set(conn, rid, "no_event", result={"reason": "page had almost no readable text (it may need JavaScript or a login)", "chars": len(page.text)})
        return {"status": "no_event", "reason": "no_text"}
    row = conn.execute(
        "INSERT INTO source_documents(source_id, external_id, version_key, canonical_url, title, published_at, content_hash, change_status, content_text, raw_meta) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,'new',%s,%s) ON CONFLICT (source_id, external_id, version_key) DO UPDATE SET content_text=EXCLUDED.content_text RETURNING id",
        (classify_source(conn, page.url, str(r["organization_id"]) if r["organization_id"] else None), page.url, page.content_hash[:32], page.url, page.title, page.published_at, page.content_hash, page.text,
         jb({"verification_request": rid, "origin": r["origin"]}))).fetchone()
    doc_id = str(row["id"])
    _set(conn, rid, "extracting", document_id=doc_id)
    conn.commit()
    if hasattr(extractor, "ctx"):
        extractor.ctx = {"document_id": doc_id, "request_id": rid, "organization_id": str(r["organization_id"]) if r["organization_id"] else None}
    resolve = org_aware_resolver(conn, resolver, str(r["organization_id"]) if r["organization_id"] else None)
    try:
        out = process_document(conn, doc_id, extractor, resolve_org=resolve)
    except BudgetExceeded:
        conn.rollback()
        _set(conn, rid, "waiting_budget", result={"reason": "monthly model budget reached; will retry when budget is available"})
        return {"status": "waiting_budget"}
    except ConnectorError as exc:
        # the model provider failed (out of credit, rate limit, outage): the page is fine, so the request waits and the queue retries it
        conn.rollback()
        _set(conn, rid, "queued", result={"reason": f"waiting for the AI provider: {exc}"[:300], "error_class": exc.error_class})
        conn.commit()
        raise JobError(str(exc)[:300], exc.error_class) from exc
    except JobError as exc:
        conn.rollback()
        _set(conn, rid, "failed", result={"reason": str(exc), "error_class": exc.error_class})
        return {"status": "failed", "reason": str(exc)}
    event_ids = [str(e["id"]) for e in conn.execute(
        "SELECT DISTINCT e.id FROM events e JOIN event_documents ed ON ed.event_id=e.id WHERE ed.document_id=%s", (doc_id,)).fetchall()]
    signals = negatives = matched = 0
    for eid in event_ids:
        res = finish_event(conn, eid, now=now)
        if r["organization_id"] is not None and not q1(
            conn,
            "SELECT 1 FROM events WHERE id=%s AND subject_org_id=%s AND verification_status='verified'",
            (eid, r["organization_id"]),
        ):
            continue
        matched += 1
        signals += len(res["signals"])
        negatives += len(res["negatives"])
    status = "verified" if matched else "no_event"
    if event_ids and not matched:
        out = {**out, "reason": "The source has no verified event linked to this account."}
    _set(conn, rid, status, events_created=matched, result={**out, "signals": signals, "negatives": negatives, "page_title": page.title,
                                                                   "page_published": page.published_at.isoformat() if page.published_at else None,
                                                                   "fetched_at": (now or datetime.now(timezone.utc)).isoformat()})
    return {"status": status, "events": matched, "signals": signals}


def sync_fetch(client_factory: Callable[[], Any]) -> Callable[[str], Page]:
    """Blocking wrapper for the worker thread."""
    from ..sources.safe import public_client
    from .fetch import fetch_page

    def go(url: str) -> Page:
        async def inner() -> Page:
            async with public_client(25) as c:      # connects only to the address that passed the public-address check
                return await fetch_page(c, url)
        return asyncio.run(inner())
    return go
