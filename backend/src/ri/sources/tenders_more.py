"""More national tender portals with a keyless, documented, reusable open-data interface. They write the same notice tables as the first four tender
sources (procurement.py), so the Tenders page, the findings and the buying signals need no change.

Germany: the federal Bekanntmachungsservice (oeffentlichevergabe.de). Its OpenData API (documented at /documentation/api/opendata) exports every notice published on
a given day as a ZIP of eForms XML, with no key and no login; every notice says it is released under CC0. Notices published under an EU directive are also
published on TED (which we already read), so this reader keeps only the NATIONAL notices (below the EU threshold, or outside the directives): the part of the
German market TED does not show. Only contract (competition) notices with a buyer and a submission deadline are kept; award notices are left out.

Checked against the live service on 27 Nov 2026 (the day's export, its OpenAPI description, the notice page); the tests use invented notices.
"""
from __future__ import annotations

import asyncio
import io
import re
import zipfile
from datetime import date, datetime, timedelta
from typing import Any
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import httpx
from defusedxml import ElementTree as SafeET

from ..events.classify import _LICENCE_ONLY, _WORK
from ..market import alpha2
from .base import (FetchBatch, HealthCheck, PolicyBlocked, RateLimited, RawItem, SchemaDrift, SourceDescriptor, SourceDocumentDraft, TransientError, ServerError)
from .http import RATE_LIMIT_DEFAULT_WAIT, RATE_LIMIT_MAX_WAIT, UA, classify, request_json
from .procurement import derive_status
from .util import excerpt, sha, utcnow

OEV_HOST = "https://oeffentlichevergabe.de"          # the bare host: www. answers with a redirect
OEV_EXPORT = f"{OEV_HOST}/api/notice-exports"
OEV_API_DOC = f"{OEV_HOST}/documentation/api/opendata"
EFORMS_MIME = "application/vnd.bekanntmachungsservice.eforms.zip+zip"
BERLIN = ZoneInfo("Europe/Berlin")
MAX_ZIP_BYTES = 60_000_000           # a day is about 4 MB: anything far larger is not what the service documents
MAX_XML_BYTES = 6_000_000
MAX_FILES = 6000
DAY_PAUSE = 2.0                      # between two days' downloads in one run
NS = {"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2", "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
      "efac": "http://data.europa.eu/p27/eforms-ubl-extension-aggregate-components/1", "efbc": "http://data.europa.eu/p27/eforms-ubl-extension-basic-components/1",
      "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2", "efext": "http://data.europa.eu/p27/eforms-ubl-extensions/1"}
_CELEX = re.compile(r"^3\d{4}[A-Z]\d{4}$")            # an EU directive (2014/24/EU is 32014L0024): the notice is also on TED
_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})(Z|[+-]\d{2}:\d{2})?$")
_TIME = re.compile(r"^(\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?$")
# eForms notice type lists: what a notice does in the procurement
_KIND_BY_LIST = {"competition": "contract_notice", "change": "contract_notice", "planning": "planning", "result": "award"}


def _txt(el: ET.Element | None) -> str | None:
    return re.sub(r"\s+", " ", el.text).strip() if el is not None and el.text and el.text.strip() else None


def _first(root: ET.Element, path: str) -> str | None:
    return _txt(root.find(path, NS))


def when(day: str | None, time: str | None = None) -> datetime | None:
    """An eForms date ('2026-12-08+01:00') and optional time ('12:00:00+01:00') as one moment. A missing offset means German local time."""
    m = _DATE.match((day or "").strip())
    if not m:
        return None
    tm = _TIME.match((time or "").strip()) if time else None
    tz = (tm.group(2) if tm and tm.group(2) else m.group(2)) or None
    hhmmss = tm.group(1) if tm else "00:00:00"
    try:
        if tz:
            return datetime.fromisoformat(f"{m.group(1)}T{hhmmss}{'+00:00' if tz == 'Z' else tz}")
        return datetime.fromisoformat(f"{m.group(1)}T{hhmmss}").replace(tzinfo=BERLIN)
    except ValueError:
        return None


def parse_notice(data: bytes) -> dict[str, Any] | None:
    """One eForms notice (XML) as a plain record, or None when it is not a notice we can read. Entities and DTDs are refused."""
    try:
        root = SafeET.fromstring(data)
    except (ET.ParseError, ValueError):
        return None
    kind_root = root.tag.rsplit("}", 1)[-1]
    if kind_root not in {"ContractNotice", "ContractAwardNotice", "PriorInformationNotice"}:
        return None
    ntype = root.find("cbc:NoticeTypeCode", NS)
    notice_id = _first(root, "cbc:ID")
    folder = _first(root, "cbc:ContractFolderID") or notice_id          # the short national notices have no folder id: their own number is the key
    if ntype is None or not notice_id:
        return None
    orgs: dict[str, dict[str, str | None]] = {}
    for c in root.iterfind(".//efac:Organizations/efac:Organization/efac:Company", NS):
        oid = _first(c, "cac:PartyIdentification/cbc:ID")
        if oid:
            orgs[oid] = {"name": _first(c, "cac:PartyName/cbc:Name"), "country": _first(c, "cac:PostalAddress/cac:Country/cbc:IdentificationCode")}
    buyer_id = _first(root, "cac:ContractingParty/cac:Party/cac:PartyIdentification/cbc:ID")
    project = root.find("cac:ProcurementProject", NS)
    buyer = orgs.get(buyer_id or "") or {"name": _first(root, "cac:ContractingParty/cac:Party/cac:PartyName/cbc:Name"),
                                         "country": _first(project, "cac:RealizedLocation/cac:Address/cac:Country/cbc:IdentificationCode") if project is not None else None}
    cpv: list[str] = []
    deadlines: list[datetime] = []
    lot_titles: list[str] = []

    def add_cpv(el: ET.Element | None) -> None:
        if el is None:
            return
        for tag in ("cac:MainCommodityClassification", "cac:AdditionalCommodityClassification"):
            for c in el.iterfind(f"{tag}/cbc:ItemClassificationCode", NS):
                code = re.sub(r"-\d$", "", c.text.strip()) if c.text else ""          # the short national notices keep the CPV check digit ('72000000-5')
                if code and code not in cpv:
                    cpv.append(code)

    add_cpv(project)
    for lot in root.iterfind("cac:ProcurementProjectLot", NS):
        add_cpv(lot.find("cac:ProcurementProject", NS))
        t = _first(lot, "cac:ProcurementProject/cbc:Name")
        if t:
            lot_titles.append(t)
        d = when(_first(lot, "cac:TenderingProcess/cac:TenderSubmissionDeadlinePeriod/cbc:EndDate"), _first(lot, "cac:TenderingProcess/cac:TenderSubmissionDeadlinePeriod/cbc:EndTime"))
        if d:
            deadlines.append(d)
    top_deadline = when(_first(root, "cac:TenderingProcess/cac:TenderSubmissionDeadlinePeriod/cbc:EndDate"), _first(root, "cac:TenderingProcess/cac:TenderSubmissionDeadlinePeriod/cbc:EndTime"))
    if top_deadline:
        deadlines.append(top_deadline)
    bid = None
    for xp in (".//cac:CallForTendersDocumentReference/cac:Attachment/cac:ExternalReference/cbc:URI", ".//cac:TenderRecipientParty/cbc:EndpointID"):
        for el in root.iterfind(xp, NS):
            if el.text and re.match(r"^https?://", el.text.strip()):
                bid = el.text.strip()
                break
        if bid:
            break
    subtypes = [t.text.strip() for t in root.iterfind(".//efac:NoticeSubType/cbc:SubTypeCode", NS) if t.text and t.text.strip()]
    # a correction can withdraw the procedure ("Das Vergabeverfahren wurde eingestellt"): the reason code says so, in either notice form
    cancelled = any(c.tag.rsplit("}", 1)[-1] == "ReasonCode" and (c.text or "").strip().lower() == "cancel" for r in root.iterfind(".//efac:ChangeReason", NS) for c in r)
    issued = when(_first(root, "cbc:IssueDate"), _first(root, "cbc:IssueTime"))
    published = when(_first(root, "cbc:RequestedPublicationDate")) or issued
    title = _first(project, "cbc:Name") if project is not None else None
    return {"notice_id": notice_id, "folder_id": folder, "version": _first(root, "cbc:VersionID") or "01", "root": kind_root, "type": (ntype.text or "").strip(),
            "type_list": ntype.get("listName") or "", "regime": _first(root, "cbc:RegulatoryDomain"), "subtypes": subtypes,
            "corrects": _first(root, ".//efbc:ChangedNoticeIdentifier"), "cancelled": cancelled, "buyer_name": buyer.get("name"), "country": buyer.get("country"),
            "title": title or (lot_titles[0] if lot_titles else None), "description": _first(project, "cbc:Description") if project is not None else None,
            "cpv": cpv, "deadlines": deadlines, "procedure": _first(root, "cac:TenderingProcess/cbc:ProcedureCode"), "bid_url": bid, "issued": issued, "published": published}


def on_ted(n: dict[str, Any]) -> bool:
    """A notice under an EU directive (or one with an EU notice form number) is sent on to TED, which we read already."""
    return bool(n["subtypes"]) or bool(n["regime"] and _CELEX.match(n["regime"]))


def is_software_notice(title: str | None, scope: str | None, codes: list[str]) -> bool:
    """Intake filter, deliberately as broad as the Polish one: an IT services or software code (not servers), or wording about building or implementing
    software. Licence renewals are dropped. Whether a kept notice is work for us is decided later by services_relevant."""
    text = f"{title or ''} {scope or ''}"
    work = bool(_WORK.search(text))
    if _LICENCE_ONLY.search(text) and not work:
        return False
    return work or any(c.startswith(("72", "48")) and not c.startswith("4882") for c in codes)


def pick_deadline(deadlines: list[datetime], now: datetime) -> datetime | None:
    """The first lot deadline still ahead; when every lot has closed, the latest."""
    ahead = sorted(d for d in deadlines if d > now)
    return ahead[0] if ahead else (max(deadlines) if deadlines else None)


async def get_bytes(client: httpx.AsyncClient, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, retries: int = 2,
                    sleep=asyncio.sleep, max_bytes: int = MAX_ZIP_BYTES) -> bytes:
    """A binary download with the shared error classes: transient failures retried, one short 429 waited out, anything else raised for the job layer."""
    waited = False
    last: Exception | None = None
    attempt = 0
    while attempt <= retries:
        try:
            resp = await client.request("GET", url, params=params, headers={"User-Agent": UA, **(headers or {})})
            classify(resp)
            if len(resp.content) > max_bytes:
                raise SchemaDrift(f"download larger than {max_bytes} bytes")
            return resp.content
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = TransientError(f"{type(exc).__name__}: {exc}")
        except ServerError as exc:
            last = exc
        except RateLimited as exc:
            wait = RATE_LIMIT_DEFAULT_WAIT if exc.retry_after is None else exc.retry_after
            if waited or wait > RATE_LIMIT_MAX_WAIT:
                raise
            waited = True
            await sleep(wait)
            continue
        attempt += 1
        if attempt <= retries:
            await sleep(0.5 * (2 ** (attempt - 1)))
    assert last is not None
    raise last


class GermanNoticeConnector:
    """Germany's Bekanntmachungsservice open-data export, one publication day per call. The cursor is 'day=YYYY-MM-DD' (the next day to read). The service
    only has days up to yesterday (Berlin time), so a run that is already up to date reads nothing. The first run goes back `backfill_days`."""

    def __init__(self, client: httpx.AsyncClient, *, source_id: str = "procurement-de-bekanntmachung", now_fn=utcnow, backfill_days: int = 21, robots=None):
        from .webwatch import RobotsCache
        self.client, self.source_id, self.now, self.backfill = client, source_id, now_fn, backfill_days
        self.robots = robots or RobotsCache()
        self.day_pause = DAY_PAUSE
        self.sleep = asyncio.sleep          # replaced in tests
        self._reads = 0

    def describe(self) -> SourceDescriptor:
        return SourceDescriptor(self.source_id, "Bekanntmachungsservice (DE)", "official_api", "S1", "1.0")

    async def _allowed(self) -> None:
        if not await self.robots.allowed(self.client, OEV_EXPORT):
            raise PolicyBlocked(self.robots.refusal(OEV_EXPORT))

    async def health_check(self) -> HealthCheck:
        """The service's own API description (small) says the export exists: no day's ZIP is downloaded for a test."""
        try:
            await self._allowed()
            d = await request_json(self.client, "GET", OEV_API_DOC, retries=0)
            if not isinstance(d, dict) or "/api/notice-exports" not in (d.get("paths") or {}):
                raise SchemaDrift("the Bekanntmachungsservice API description has no /api/notice-exports")
            return HealthCheck(True)
        except Exception as exc:  # noqa: BLE001
            return HealthCheck(False, str(exc), getattr(exc, "error_class", "transient_network"))

    def last_day(self, now: datetime) -> date:
        return now.astimezone(BERLIN).date() - timedelta(days=1)

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        started = self.now()
        last = self.last_day(started)
        try:
            day = date.fromisoformat(cursor.removeprefix("day=")) if cursor and cursor.startswith("day=") else last - timedelta(days=self.backfill - 1)
        except ValueError:
            day = last - timedelta(days=self.backfill - 1)
        n = {"days": 0, "notices_seen": 0, "dropped_unreadable": 0, "dropped_not_competition": 0, "dropped_on_ted": 0, "dropped_not_software": 0,
             "dropped_no_deadline": 0, "dropped_closed": 0, "kept": 0}
        if day > last:
            return FetchBatch([], f"day={day.isoformat()}", False, started, "1.0", quota_usage={"requests": 0, **n})
        await self._allowed()
        if self._reads:
            await self.sleep(self.day_pause)
        self._reads += 1
        raw = await get_bytes(self.client, OEV_EXPORT, params={"pubDay": day.isoformat(), "format": "eforms.zip"}, headers={"Accept": EFORMS_MIME}, sleep=self.sleep)
        try:
            z = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise SchemaDrift("the day's export is not a ZIP file") from exc
        infos = [i for i in z.infolist() if i.filename.lower().endswith(".xml") and not i.is_dir()]
        if len(infos) > MAX_FILES or any(i.file_size > MAX_XML_BYTES for i in infos):
            raise SchemaDrift("the day's export is larger than the service documents")
        n["days"] = 1
        parsed: list[dict[str, Any]] = []
        for info in infos:
            n["notices_seen"] += 1
            rec = parse_notice(z.read(info))
            if rec is None:
                n["dropped_unreadable"] += 1
                continue
            if _KIND_BY_LIST.get(rec["type_list"]) != "contract_notice":
                n["dropped_not_competition"] += 1
                continue
            if on_ted(rec):
                n["dropped_on_ted"] += 1
                continue
            if not is_software_notice(rec["title"], rec["description"], rec["cpv"]):
                n["dropped_not_software"] += 1
                continue
            if not rec["cancelled"]:          # a withdrawal is kept even without a deadline: it closes the tender we may already hold
                if not rec["buyer_name"] or not rec["deadlines"]:
                    n["dropped_no_deadline"] += 1
                    continue
                if max(rec["deadlines"]) <= started:
                    n["dropped_closed"] += 1
                    continue
            elif not rec["buyer_name"]:
                n["dropped_no_deadline"] += 1
                continue
            parsed.append(rec)
        parsed.sort(key=lambda r: (r["issued"] or r["published"] or started, int(re.sub(r"\D", "", r["version"]) or 0)))          # a later version of a notice is applied last
        items = [RawItem(external_id=r["notice_id"], version=f"{r['version']}", payload={**r, "deadline": pick_deadline(r["deadlines"], started)}) for r in parsed]
        n["kept"] = len(items)
        nxt = day + timedelta(days=1)
        more = nxt <= last
        return FetchBatch(items, f"day={nxt.isoformat()}", more, started, "1.0", quota_usage={"requests": 1, **n})

    async def fetch_by_id(self, external_id: str) -> RawItem | None:
        return None

    def canonicalize(self, item: RawItem) -> SourceDocumentDraft:
        r = item.payload
        deadline = r["deadline"]
        correction = bool(r.get("corrects"))
        status = derive_status(self.now(), kind="contract_notice", deadline=deadline, amended=correction, cancelled=bool(r.get("cancelled")))
        url = f"{OEV_HOST}/api/notices/{r['notice_id']}?format=pdf"          # the notice itself, as the service publishes it
        scope = (r.get("description") or "")[:1200]
        if r.get("bid_url"):
            scope = f"{scope}\nTender documents: {r['bid_url']}".strip()
        tz = deadline.strftime("%z") if deadline else None
        rec = {"notice_id": r["folder_id"], "buyer_name": r["buyer_name"], "title": r["title"], "scope": scope or None, "procedure": r.get("procedure"), "service_codes": list(r["cpv"]),
               "country": alpha2(r.get("country")) or "DE", "published_at": r.get("published"), "amended_at": r.get("issued") if correction else None, "deadline_at": deadline,
               "deadline_tz": f"{tz[:3]}:{tz[3:]}" if tz else None, "status": status, "official_url": url, "notice_kind": "contract_notice", "value_if_reported": None}
        return SourceDocumentDraft(r["notice_id"], sha(r["notice_id"], r["version"], deadline.isoformat() if deadline else "", r["title"]), url, r["title"], r.get("published"), None,
                                   excerpt(r.get("description") or r["title"]), raw_meta={"notice_type": r["type"], "version": r["version"], "folder_id": r["folder_id"]},
                                   structured=rec, kind="procurement_notice")
