"""Official procurement connectors: EU TED and UK Find a Tender (OCDS).

STATUS: pending_connector_validation. Field names/endpoints below follow the public documentation as known at
build time; the sandbox egress proxy blocked live calls, so they have NOT been verified against live responses
(see docs/integrations/INVENTORY.md). Tests use hand-authored fixtures, not recorded traffic.
"""
from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import quote

from datetime import datetime
from typing import Any

import httpx

from .base import (FetchBatch, HealthCheck, RawItem, SchemaDrift, SourceDescriptor, SourceDocumentDraft)
from .http import request_json
from .util import excerpt, first_text, parse_dt, sha, tz_of, utcnow

TED_URL = "https://api.ted.europa.eu/v3/notices/search"
FTS_URL = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
FTS_TAGS = {"tender", "tenderUpdate", "tenderAmendment", "tenderCancellation", "award", "awardUpdate"}
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
            # Checked against the live API: a comma-separated "stages" value silently returns no releases, and no updatedFrom pages through all history.
            # So: always a window (14 days on the first run), no stages filter, and relevance decided here.
            since = cursor.removeprefix("since:") if cursor else (requested - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S")
            params: dict[str, Any] = {"limit": min(limit, 100), "updatedFrom": since}
            if self.stages and "," not in self.stages:
                params["stages"] = self.stages
            data = await request_json(self.client, "GET", FTS_URL, params=params)
        if not isinstance(data, dict) or "releases" not in data:
            raise SchemaDrift("OCDS package has no 'releases'")
        items = [RawItem(external_id=r["ocid"], version=r.get("date"), payload=r) for r in data["releases"]
                 if r.get("ocid") and FTS_TAGS.intersection(r.get("tag") or []) and is_software_notice(r)]
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


CF_URL = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
_SOFTWARE_CPV = ("72", "48", "73")      # IT services, software packages, R&D/consulting
_SOFTWARE_WORDS = re.compile(r"\b(software|digital|cloud|platform|application|api|data|ai|machine learning|cyber|payments?|banking|engineering|"
                             r"development|devops|integration|modernis|migration|IT services?)\b", re.I)


def is_software_notice(release: dict) -> bool:
    """The Contracts Finder search has no CPV filter, so relevance is decided here: an IT/software CPV code, or software wording in the title."""
    tender = release.get("tender") or {}
    cpvs = [str((it.get("classification") or {}).get("id") or "") for it in tender.get("items", [])]
    cpvs += [str((a or {}).get("id") or "") for a in (tender.get("additionalClassifications") or [])]
    if any(c.startswith(_SOFTWARE_CPV) for c in cpvs):
        return True
    return bool(_SOFTWARE_WORDS.search(f"{tender.get('title') or ''}"))


class ContractsFinderConnector(FindTenderConnector):
    """UK Contracts Finder (OCDS search: publishedFrom, publishedTo, stages, limit, cursor). Same release shape as Find a Tender; filtered to software work."""

    def __init__(self, client: httpx.AsyncClient, *, source_id: str = "procurement-uk-contracts-finder", stages: str = "tender,award", now_fn=utcnow):
        super().__init__(client, source_id=source_id, stages=stages, now_fn=now_fn)

    def describe(self) -> SourceDescriptor:
        return SourceDescriptor(self.source_id, "UK Contracts Finder", "official_api", "S1", "1.0")

    async def health_check(self) -> HealthCheck:
        try:
            await request_json(self.client, "GET", CF_URL, params={"limit": 1, "stages": "tender"}, retries=0)
            return HealthCheck(True)
        except Exception as exc:  # noqa: BLE001
            return HealthCheck(False, str(exc), getattr(exc, "error_class", "transient_network"))

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        requested = self.now()
        if cursor and cursor.startswith("http"):
            data = await request_json(self.client, "GET", cursor)
        else:
            since = cursor.removeprefix("since:") if cursor else (requested - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S")
            data = await request_json(self.client, "GET", CF_URL, params={"limit": min(limit, 100), "stages": self.stages, "publishedFrom": since,
                                                                          "publishedTo": requested.strftime("%Y-%m-%dT%H:%M:%S")})
        if not isinstance(data, dict) or "releases" not in data:
            raise SchemaDrift("OCDS package has no 'releases'")
        items = [RawItem(external_id=r["ocid"], version=r.get("date"), payload=r) for r in data["releases"] if r.get("ocid") and is_software_notice(r)]
        nxt = (data.get("links") or {}).get("next")
        return FetchBatch(items, nxt or f"since:{requested.strftime('%Y-%m-%dT%H:%M:%S')}", bool(nxt), requested, "ocds-1.1", quota_usage={"requests": 1})

    def canonicalize(self, item: RawItem) -> SourceDocumentDraft:
        d = super().canonicalize(item)
        nid = str(item.payload.get("ocid", "")).split("-")[-1]
        url = f"https://www.contractsfinder.service.gov.uk/Notice/{nid}" if nid else None
        if d.structured:
            d.structured["official_url"] = url
        d.canonical_url = url
        return d


BZP_URL = "https://ezamowienia.gov.pl/mo-board/api/v1/Board/Search"
BZP_PAGE = 10                    # the service returns at most 10 notices per page whatever PageSize says (checked against the live service)
BZP_PAGES_PER_RUN = 40
_BZP_CODE = re.compile(r"\b(\d{8})-\d\b")
_BZP_WORDS = re.compile(r"(oprogramowani|system\w* informatyczn|informatyk|aplikacj|licencj|wdro[zż]eni\w* system|rozw[oó]j system|chmur|cyberbezpiecz|"
                        r"integracj\w* system|hosting|us[lł]ug\w* it\b|portal|platform\w* (cyfrow|internet|e-))", re.I)


def bzp_codes(raw: str | None) -> list[str]:
    return _BZP_CODE.findall(raw or "")


def is_bzp_software(n: dict) -> bool:
    """The BZP search filters poorly by CPV, so relevance is decided here: any IT CPV code (72, 48, 73) or Polish software wording in the subject."""
    if any(c.startswith(_SOFTWARE_CPV) for c in bzp_codes(n.get("cpvCode"))):
        return True
    return bool(_BZP_WORDS.search(n.get("orderObject") or ""))


class PolishBzpConnector:
    """Polish Biuletyn Zamówień Publicznych via the open e-Zamówienia board API. Public, no key. Contract notices and tender results.
    Verified against the live service: GET /Board/Search with NoticeType, PublicationDateFrom/To, PageNumber (max 10 per page); an empty page ends the window."""

    def __init__(self, client: httpx.AsyncClient, *, source_id: str = "procurement-ezamowienia", now_fn=utcnow, backfill_days: int = 3):
        self.client, self.source_id, self.now, self.backfill = client, source_id, now_fn, backfill_days

    def describe(self) -> SourceDescriptor:
        return SourceDescriptor(self.source_id, "e-Zamówienia (BZP)", "official_api", "S1", "v1")

    async def health_check(self) -> HealthCheck:
        try:
            data = await request_json(self.client, "GET", BZP_URL, params={"NoticeType": "ContractNotice", "PageSize": 1}, retries=0)
            if not isinstance(data, list):
                raise SchemaDrift("BZP search did not return a list")
            return HealthCheck(True)
        except Exception as exc:  # noqa: BLE001
            return HealthCheck(False, str(exc), getattr(exc, "error_class", "transient_network"))

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        """Cursor is 'from=<iso>;kind=<ContractNotice|TenderResultNotice>;page=<n>'. A finished window restarts from the time it began."""
        started = self.now()
        st = dict(p.split("=", 1) for p in (cursor or "").split(";") if "=" in p)
        since = st.get("from") or (started - timedelta(days=self.backfill)).strftime("%Y-%m-%dT%H:%M:%SZ")
        kind, page = st.get("kind", "ContractNotice"), int(st.get("page", 1))
        items: list[RawItem] = []
        done_window = False
        for _ in range(BZP_PAGES_PER_RUN):
            data = await request_json(self.client, "GET", BZP_URL, params={"NoticeType": kind, "PageSize": BZP_PAGE, "PageNumber": page, "PublicationDateFrom": since})
            if not isinstance(data, list):
                raise SchemaDrift("BZP search did not return a list")
            if data and "noticeNumber" not in data[0]:
                raise SchemaDrift("BZP notice has no noticeNumber")
            items += [RawItem(external_id=str(n["noticeNumber"]), version=str(n.get("publicationDate")), payload=n) for n in data if n.get("noticeNumber") and is_bzp_software(n)]
            if len(data) < BZP_PAGE:
                if kind == "ContractNotice":
                    kind, page = "TenderResultNotice", 1
                else:
                    done_window = True
                    break
            else:
                page += 1
        if done_window:
            nxt = f"from={started.strftime('%Y-%m-%dT%H:%M:%SZ')};kind=ContractNotice;page=1"
        else:
            nxt = f"from={since};kind={kind};page={page}"
        return FetchBatch(items, nxt, not done_window, started, "v1", quota_usage={"requests": 1})

    def canonicalize(self, item: RawItem) -> SourceDocumentDraft:
        n = item.payload
        deadline_raw = n.get("submittingOffersDate")
        deadline = parse_dt(deadline_raw)
        result = (n.get("procedureResult") or "").lower()
        is_result = n.get("noticeType") == "TenderResultNotice"
        kind = "award" if is_result and result == "zawarcieumowy" else "contract_notice"
        cancelled = is_result and result != "zawarcieumowy"          # annulled or no contract concluded: never shown as open or awarded
        status = derive_status(self.now(), kind=kind, deadline=deadline, cancelled=cancelled)
        pub = parse_dt(n.get("publicationDate"))
        num = str(n["noticeNumber"])
        url = "https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/id/" + quote(num, safe="")
        title = (n.get("orderObject") or "").strip("„”\" ") or None
        rec = {"notice_id": num, "buyer_name": n.get("organizationName"), "title": title, "procedure": n.get("tenderType"), "service_codes": bzp_codes(n.get("cpvCode")),
               "country": n.get("organizationCountry") or "PL", "published_at": pub, "deadline_at": deadline, "deadline_tz": tz_of(deadline_raw), "status": status,
               "official_url": url, "notice_kind": kind, "below_eu_threshold": n.get("isTenderAmountBelowEU"), "order_type": n.get("orderType"),
               "awarded_to": [c.get("contractorName") for c in (n.get("contractors") or []) if c.get("contractorName")] if kind == "award" else []}
        vkey = sha(num, str(n.get("publicationDate")), deadline_raw, status)
        return SourceDocumentDraft(num, vkey, url, title, pub, None, excerpt(title), raw_meta={"notice_type": n.get("noticeType")}, structured=rec, kind="procurement_notice")
