"""Official procurement connectors: EU TED and UK Find a Tender (OCDS).

STATUS: pending_connector_validation. Field names/endpoints below follow the public documentation as known at
build time; the sandbox egress proxy blocked live calls, so they have NOT been verified against live responses
(see docs/integrations/INVENTORY.md). Tests use hand-authored fixtures, not recorded traffic.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from .base import (FetchBatch, HealthCheck, RawItem, SchemaDrift, SourceDescriptor, SourceDocumentDraft)
from .http import request_json
from .util import excerpt, first_text, parse_dt, sha, tz_of, utcnow

TED_URL = "https://api.ted.europa.eu/v3/notices/search"
FTS_URL = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
TED_FIELDS = ["publication-number", "notice-title", "buyer-name", "buyer-country", "notice-type", "publication-date",
              "deadline-receipt-tender-date-lot", "classification-cpv", "procedure-type", "links"]


def derive_status(now: datetime, *, kind: str, deadline: datetime | None, amended: bool = False, cancelled: bool = False) -> str:
    """Spec 07 §13 / UJ-03: an award is never 'open'; a past deadline is 'expired'; amended notices stay open."""
    if cancelled:
        return "cancelled"
    if kind == "award":
        return "awarded"
    if kind == "planning":
        return "planning"
    if deadline is not None and deadline <= now:
        return "expired"
    if kind == "contract_notice":
        return "amended" if amended else "open"
    return "unknown"


def _ted_kind(notice_type: str | None) -> str:
    t = (notice_type or "").lower()
    if t.startswith("can") or "award" in t:
        return "award"
    if t.startswith(("pin", "pmc")) or "planning" in t:
        return "planning"
    if t.startswith(("cn", "competition")) or "contract-notice" in t:
        return "contract_notice"
    return "other"


class TedConnector:
    def __init__(self, client: httpx.AsyncClient, *, query: str, source_id: str = "procurement-ted", page_size: int = 100,
                 now_fn=utcnow):
        self.client, self.query, self.source_id, self.page_size, self.now = client, query, source_id, page_size, now_fn

    def describe(self) -> SourceDescriptor:
        return SourceDescriptor(self.source_id, "TED", "official_api", "S1", "v3")

    async def health_check(self) -> HealthCheck:
        try:
            await request_json(self.client, "POST", TED_URL, json={"query": self.query, "fields": ["publication-number"], "page": 1, "limit": 1},
                               retries=0)
            return HealthCheck(True)
        except Exception as exc:  # noqa: BLE001
            return HealthCheck(False, str(exc), getattr(exc, "error_class", "transient_network"))

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        page = int(cursor) if cursor else 1
        body = {"query": self.query, "fields": TED_FIELDS, "page": page, "limit": min(limit, self.page_size), "scope": "ACTIVE",
                "paginationMode": "PAGE_NUMBER"}
        data = await request_json(self.client, "POST", TED_URL, json=body)
        if not isinstance(data, dict) or "notices" not in data:
            raise SchemaDrift("TED response has no 'notices'")
        items = [RawItem(external_id=str(n["publication-number"]), version=None, payload=n) for n in data["notices"]
                 if n.get("publication-number")]
        total = int(data.get("totalNoticeCount", 0))
        has_more = page * body["limit"] < total
        return FetchBatch(items, str(page + 1) if has_more else None, has_more, self.now(), "v3",
                          quota_usage={"requests": 1}, partial_result=False)

    async def fetch_by_id(self, external_id: str) -> RawItem | None:
        body = {"query": f'publication-number = "{external_id}"', "fields": TED_FIELDS, "page": 1, "limit": 1, "scope": "ALL"}
        data = await request_json(self.client, "POST", TED_URL, json=body)
        ns = data.get("notices", [])
        return RawItem(external_id, None, ns[0]) if ns else None

    def canonicalize(self, item: RawItem) -> SourceDocumentDraft:
        n = item.payload
        raw_deadline = first_text(n.get("deadline-receipt-tender-date-lot"))
        deadline = parse_dt(raw_deadline)
        kind = _ted_kind(first_text(n.get("notice-type")))
        pub = parse_dt(first_text(n.get("publication-date")))
        links = n.get("links") or {}
        url = first_text(links.get("html") if isinstance(links, dict) else None) or f"https://ted.europa.eu/en/notice/-/detail/{item.external_id}"
        status = derive_status(self.now(), kind=kind, deadline=deadline)
        title = first_text(n.get("notice-title"))
        rec = {"notice_id": item.external_id, "buyer_name": first_text(n.get("buyer-name")), "title": title,
               "procedure": first_text(n.get("procedure-type")), "service_codes": [str(c) for c in (n.get("classification-cpv") or [])],
               "country": first_text(n.get("buyer-country")), "published_at": pub, "deadline_at": deadline,
               "deadline_tz": tz_of(raw_deadline), "status": status, "official_url": url, "notice_kind": kind}
        vkey = sha(item.external_id, raw_deadline, first_text(n.get("notice-type")), title)
        return SourceDocumentDraft(item.external_id, vkey, url, title, pub, None, excerpt(title), raw_meta={"notice_type": first_text(n.get("notice-type"))},
                                   structured=rec, kind="procurement_notice")


class FindTenderConnector:
    """UK Find a Tender via OCDS release packages. Uses standard OCDS semantics (tag, tender.status, tenderPeriod)."""

    def __init__(self, client: httpx.AsyncClient, *, source_id: str = "procurement-uk-fts", stages: str = "tender,award",
                 now_fn=utcnow):
        self.client, self.source_id, self.stages, self.now = client, source_id, stages, now_fn

    def describe(self) -> SourceDescriptor:
        return SourceDescriptor(self.source_id, "UK Find a Tender", "official_api", "S1", "1.0")

    async def health_check(self) -> HealthCheck:
        try:
            await request_json(self.client, "GET", FTS_URL, params={"limit": 1}, retries=0)
            return HealthCheck(True)
        except Exception as exc:  # noqa: BLE001
            return HealthCheck(False, str(exc), getattr(exc, "error_class", "transient_network"))

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        requested = self.now()
        if cursor and cursor.startswith("http"):
            data = await request_json(self.client, "GET", cursor)
        else:
            since = cursor.removeprefix("since:") if cursor else None
            params: dict[str, Any] = {"limit": min(limit, 100), "stages": self.stages}
            if since:
                params["updatedFrom"] = since
            data = await request_json(self.client, "GET", FTS_URL, params=params)
        if not isinstance(data, dict) or "releases" not in data:
            raise SchemaDrift("OCDS package has no 'releases'")
        items = [RawItem(external_id=r["ocid"], version=r.get("date"), payload=r) for r in data["releases"] if r.get("ocid")]
        nxt = (data.get("links") or {}).get("next")
        # window cursor only advances once the whole window has been read
        return FetchBatch(items, nxt or f"since:{requested.strftime('%Y-%m-%dT%H:%M:%S')}", bool(nxt), requested, "ocds-1.1",
                          quota_usage={"requests": 1})

    async def fetch_by_id(self, external_id: str) -> RawItem | None:
        data = await request_json(self.client, "GET", f"{FTS_URL}/{external_id}")
        rels = data.get("releases", [])
        return RawItem(external_id, rels[-1].get("date"), rels[-1]) if rels else None

    def canonicalize(self, item: RawItem) -> SourceDocumentDraft:
        r = item.payload
        tender = r.get("tender") or {}
        tags = set(r.get("tag") or [])
        deadline_raw = (tender.get("tenderPeriod") or {}).get("endDate")
        deadline = parse_dt(deadline_raw)
        tstatus = tender.get("status")
        if "award" in tags or "contract" in tags:
            kind = "award"
        elif tstatus == "planned" or "planning" in tags:
            kind = "planning"
        else:
            kind = "contract_notice"
        status = derive_status(self.now(), kind=kind, deadline=deadline, amended="tenderUpdate" in tags,
                               cancelled=tstatus in {"cancelled", "withdrawn", "unsuccessful"})
        cpv = [it["classification"]["id"] for it in tender.get("items", []) if it.get("classification", {}).get("id")]
        url = f"https://www.find-tender.service.gov.uk/Notice/{r['ocid'].split('-')[-1]}" if r.get("ocid") else None
        rec = {"notice_id": r["ocid"], "buyer_name": (r.get("buyer") or {}).get("name"), "title": tender.get("title"),
               "scope": tender.get("description"), "procedure": tender.get("procurementMethod"), "service_codes": cpv,
               "country": "GB", "published_at": parse_dt(r.get("date")), "amended_at": parse_dt(r.get("date")) if "tenderUpdate" in tags else None,
               "deadline_at": deadline, "deadline_tz": tz_of(deadline_raw), "status": status, "official_url": url, "notice_kind": kind,
               "value_if_reported": ((tender.get("value") or {}).get("amount"))}
        vkey = sha(r["ocid"], r.get("date"), r.get("id"), deadline_raw, status)
        return SourceDocumentDraft(r["ocid"], vkey, url, tender.get("title"), parse_dt(r.get("date")), None, excerpt(tender.get("description")),
                                   raw_meta={"tags": sorted(tags)}, structured=rec, kind="procurement_notice")
