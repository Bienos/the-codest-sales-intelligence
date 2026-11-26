"""More career systems, all read from their PUBLIC, login-free job interfaces: Workday, Personio, Teamtailor, Recruitee and Workable.

Same design as the Greenhouse / Ashby / SmartRecruiters readers in ats.py: one connector class per system with the same interface
(describe, health_check, fetch, fetch_by_id, canonicalize), the same normalised job record (provider, board, job_id, title, team,
location, employment type, dates, url, tech keywords), employment wording through `classify_employment`, technology words through
`tech_keywords`, and the same closing rule: a board is an AFFIRMATIVE listing (`complete_listing=True`) only when the whole listing was
read, so a half-read board, a 404, a timeout or a block never closes an advert.

What each system gives us (checked against the live service on 26 Nov 2026 unless said otherwise)
  Workday    POST https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs  (20 postings a page, the API refuses more) and
             GET  .../wday/cxs/{tenant}/{site}/job/{path} for one posting. Checked live. The page answers `total` only on the first page, and an
             offset past the end wraps round to the first page again, so pages are counted from the first page's total and de-duplicated.
  Personio   GET https://{company}.jobs.personio.de/xml  (the same feed answers on .jobs.personio.com). Checked live.
  Teamtailor GET https://{company}.teamtailor.com/jobs.rss. Checked live.
  Recruitee  GET https://{company}.recruitee.com/api/offers/. Checked live.
  Workable   GET https://apply.workable.com/api/v1/widget/accounts/{company}?details=true. DOCUMENTATION ONLY: the build sandbox's address is
             refused by Workable's network (429, Retry-After 12 hours), so the response shape comes from Workable's widget documentation and
             the reader is strict about what it accepts.

Rules every reader here follows (the account scan passes in its shared robots cache and safe client)
  * robots.txt and Content-Signal (ai-input=no) are honoured for every address we call (PolicyBlocked = the site said no; we never work round it).
  * A short 429 is waited out once (shared helper in http.py); a long one is raised for the job layer. Pages are read with a pause.
  * A board that cannot be read is a clean ConnectorError (NotFound, SchemaDrift, RateLimited, PolicyBlocked...), never a crash.
  * Only the advert's public text is kept (an excerpt and technology words). No candidate data, no login.
JOIN (join.com) has no public listing interface without an API token, so it is recognised on a careers page and reported, not read.
"""
from __future__ import annotations

import asyncio
import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx
from defusedxml import ElementTree as SafeET

from ..events.classify import engineering_relevant
from .ats import _AtsBase, classify_employment, tech_keywords
from .base import ConnectorError, FetchBatch, HealthCheck, NotFound, PolicyBlocked, RateLimited, RawItem, SchemaDrift, ServerError, TransientError
from .http import RATE_LIMIT_DEFAULT_WAIT, RATE_LIMIT_MAX_WAIT, UA, classify, request_json_patient
from .util import parse_dt, utcnow
from .webwatch import RobotsCache, xml_safe

MAX_BYTES = 12_000_000                 # a feed larger than this is not an advert list
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

# ------------------------------------------------------------------------------------------------------------------ detection
# Host labels that belong to the vendor, not to a customer's board.
_VENDOR_LABELS = frozenset({"www", "app", "api", "support", "help", "docs", "blog", "status", "partner", "partners", "developer", "developers",
                            "community", "marketplace", "mail", "cdn", "assets", "static", "login", "auth", "email", "go", "info", "learn",
                            "events", "academy", "shop", "store", "demo", "trust", "security", "www2", "staging", "sandbox", "preview"})
_WORKDAY_HOST = re.compile(r"^([a-z0-9][a-z0-9-]*)\.(wd\d{1,3})\.myworkdayjobs\.com$")
_PERSONIO_HOST = re.compile(r"^([a-z0-9][a-z0-9-]*)\.jobs\.personio\.(?:de|com)$")
_TEAMTAILOR_HOST = re.compile(r"^([a-z0-9][a-z0-9-]*)\.teamtailor\.com$")
_RECRUITEE_HOST = re.compile(r"^([a-z0-9][a-z0-9-]*)\.recruitee\.com$")
_JOIN_HOST = re.compile(r"^(?:www\.)?join\.com$")
_JOIN_SUBDOMAIN = re.compile(r"^([a-z0-9][a-z0-9-]*)\.join\.com$")
_LOCALE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2,4})?$")                    # en-US, de-DE, pt-BR, zh-CN, fr
# first path segments on a Workday host that are not a career site
_WORKDAY_NOT_SITE = frozenset({"wday", "login", "job", "jobs", "userhome", "sitemap.xml", "robots.txt", "favicon.ico", "assets", "static", "talentcommunity",
                               "refreshfacet", "details", "apply", "passwordreset", "resetpassword", "createaccount", "redirectbackafterauth"})
_WORKABLE_NOT_COMPANY = frozenset({"api", "j", "ap", "assets", "static", "login", "signup", "jobs", "careers", "widget", "embed", "oauth", "legal", "privacy",
                                   "terms", "app", "help", "support"})

MORE_PROVIDERS = ("workday", "personio", "teamtailor", "recruitee", "workable")


def _host_path(url: str) -> tuple[str, list[str]] | None:
    try:
        p = urlparse(url.strip())
        host = (p.hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not host:
        return None
    return host, [s for s in p.path.split("/") if s]


def _label(m: re.Match[str] | None) -> str | None:
    if not m:
        return None
    s = m.group(1)
    return s if s not in _VENDOR_LABELS and _SLUG.match(s) else None


def workday_board(tenant: str, wd: str, site: str) -> str:
    return f"{tenant}.{wd}/{site}"


def split_workday_board(board: str) -> tuple[str, str, str, str]:
    """'db.wd3/DBWebsite' -> (host 'db.wd3.myworkdayjobs.com', tenant 'db', wd 'wd3', site 'DBWebsite'). ValueError for anything else."""
    head, _, site = board.partition("/")
    tenant, _, wd = head.partition(".")
    if not (_SLUG.match(tenant) and re.fullmatch(r"wd\d{1,3}", wd) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", site)):
        raise ValueError(f"not a Workday board: {board!r}")
    return f"{tenant}.{wd}.myworkdayjobs.com", tenant, wd, site


def _workday(host: str, segs: list[str]) -> tuple[str, str] | None:
    m = _WORKDAY_HOST.match(host)
    if not m or m.group(1) in _VENDOR_LABELS:
        return None
    tenant, wd = m.group(1), m.group(2)
    if len(segs) >= 4 and segs[0].lower() == "wday" and segs[1].lower() == "cxs":       # the API address: /wday/cxs/{tenant}/{site}/jobs
        segs = segs[3:]
    elif segs and _LOCALE.match(segs[0]):                                                  # a language prefix: /en-US/{site}; a language alone is no site
        segs = segs[1:]
    if not segs or segs[0].lower() in _WORKDAY_NOT_SITE or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", segs[0]):
        return None
    return "workday", workday_board(tenant, wd, segs[0])


def detect_board(url: str) -> tuple[str, str] | None:
    """(provider, board) for an address on one of the career systems read here, else None. The HOST decides, never a look-alike in a query string or path."""
    hp = _host_path(url)
    if not hp:
        return None
    host, segs = hp
    if host.endswith(".myworkdayjobs.com"):
        return _workday(host, segs)
    s = _label(_PERSONIO_HOST.match(host))
    if s:
        return "personio", s
    s = _label(_TEAMTAILOR_HOST.match(host))
    if s:
        return "teamtailor", s
    s = _label(_RECRUITEE_HOST.match(host))
    if s:
        return "recruitee", s
    if host == "apply.workable.com" and segs:
        c = segs[0].lower()
        if c not in _WORKABLE_NOT_COMPANY and _SLUG.match(c):
            return "workable", c
    return None


def detect_unread_board(url: str) -> tuple[str, str] | None:
    """A career system we recognise but cannot read (no public listing interface): JOIN, as join.com/companies/{company} or {company}.join.com."""
    hp = _host_path(url)
    if not hp:
        return None
    host, segs = hp
    if _JOIN_HOST.match(host) and len(segs) >= 2 and segs[0] == "companies" and _SLUG.match(segs[1].lower()):
        return "join", segs[1].lower()
    s = _label(_JOIN_SUBDOMAIN.match(host))
    return ("join", s) if s else None


def board_url(provider: str, board: str) -> str:
    """The public page of a board (what an account_endpoints row stores as its address)."""
    if provider == "workday":
        host, _, _, site = split_workday_board(board)
        return f"https://{host}/{site}"
    if provider == "personio":
        return f"https://{board}.jobs.personio.de"
    if provider == "teamtailor":
        return f"https://{board}.teamtailor.com/jobs"
    if provider == "recruitee":
        return f"https://{board}.recruitee.com"
    if provider == "workable":
        return f"https://apply.workable.com/{board}/"
    raise KeyError(provider)


# ------------------------------------------------------------------------------------------------------------------ shared helpers
def plain_text(markup: str | None) -> str:
    """Advert text from HTML (possibly escaped twice): tags and scripts removed, entities decoded, white space collapsed."""
    t = html.unescape(markup or "")
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.I | re.S)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def _dt(value: Any) -> datetime | None:
    """ISO times and the 'YYYY-MM-DD HH:MM:SS UTC' form Recruitee uses; RFC 2822 for feeds."""
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    v = re.sub(r"\s+UTC$", "+00:00", v)
    v = re.sub(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:)", r"\1T\2", v)
    d = parse_dt(v)
    if d is None:
        try:
            d = parsedate_to_datetime(value.strip())
        except (TypeError, ValueError, IndexError):
            return None
        if d is not None and d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
    return d


async def request_text_patient(client: Any, url: str, *, check=None, sleep=asyncio.sleep, retries: int = 2, backoff: float = 0.5, max_redirects: int = 3,
                               max_wait: float = RATE_LIMIT_MAX_WAIT, accept: str = "application/xml, text/xml, application/rss+xml, text/plain, */*") -> str:
    """GET text through the shared error classes of http.py: transient errors and 5xx are retried twice, a short 429 is waited out once, redirects are followed
    by hand (at most three) and `check(url)` (the robots test) runs on EVERY address before it is called."""
    cur, redirects, attempt, waited = url, 0, 0, False
    last: ConnectorError | None = None
    while True:
        if check is not None:
            await check(cur)
        try:
            resp = await client.request("GET", cur, headers={"User-Agent": UA, "Accept": accept})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = TransientError(f"{type(exc).__name__}: {exc}")
        else:
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                redirects += 1
                if redirects > max_redirects:
                    raise SchemaDrift("too many redirects")
                cur = urljoin(cur, resp.headers["location"])
                continue
            try:
                classify(resp)
            except ServerError as exc:
                last = exc
            except RateLimited as exc:
                wait = RATE_LIMIT_DEFAULT_WAIT if exc.retry_after is None else exc.retry_after
                if waited or wait > max_wait:
                    raise
                waited = True
                await sleep(wait)
                continue
            else:
                if len(resp.content) > MAX_BYTES:
                    raise SchemaDrift("response is too large to be a list of adverts")
                return resp.text
        attempt += 1
        if attempt > retries:
            assert last is not None
            raise last
        await sleep(backoff * (2 ** (attempt - 1)))


def parse_xml(text: str, what: str) -> ET.Element:
    """Strict, entity-safe XML (defusedxml) with the same tolerance for stray HTML entities as the news feeds."""
    if not text.strip():
        raise SchemaDrift(f"{what} is empty")
    try:
        return SafeET.fromstring(xml_safe(text))
    except (ET.ParseError, ValueError) as exc:        # DefusedXmlException is a ValueError: entity declarations are refused
        raise SchemaDrift(f"{what} is not valid XML: {exc}") from exc


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(el: ET.Element, name: str) -> str | None:
    for c in el:
        if _local(c.tag) == name:
            t = "".join(c.itertext()).strip()
            return t or None
    return None


class _MoreBase(_AtsBase):
    """Shared plumbing: the robots test, the 'whole listing read?' flag, warnings, and a board name that can only be a host label."""
    USES_ROBOTS = True
    BOARD_RE = _SLUG

    def __init__(self, client: httpx.AsyncClient, board: str, *, employer_name: str | None = None, source_id: str | None = None, now_fn=utcnow,
                 robots: RobotsCache | None = None, sleep=None):
        if not self.BOARD_RE.match(board or ""):
            raise ValueError(f"not a {self.provider} board name: {board!r}")
        super().__init__(client, board, employer_name=employer_name, source_id=source_id, now_fn=now_fn)
        self.robots = robots or RobotsCache()
        self._sleep = sleep or asyncio.sleep
        self.complete = True
        self.warnings: list[str] = []
        self.company_name: str | None = None
        self.stats: dict[str, Any] = {}

    async def _allowed(self, url: str) -> None:
        if not await self.robots.allowed(self.client, url):
            raise PolicyBlocked(self.robots.refusal(url))

    async def _json(self, url: str, **kw: Any) -> Any:
        await self._allowed(url)
        return await request_json_patient(self.client, "GET", url, sleep=self._sleep, **kw)

    async def _text(self, url: str) -> str:
        return await request_text_patient(self.client, url, check=self._allowed, sleep=self._sleep)

    async def fetch(self, cursor: str | None, limit: int):
        self.complete, self.warnings, self.stats = True, [], {}
        jobs = await self._list()
        items = [RawItem(external_id=str(j["_id"]), version=j.get("_updated"), payload=j) for j in jobs]
        usage = {"requests": self.stats.pop("requests", 1), "postings": len(items), **self.stats}
        return FetchBatch(items, None, False, self.now(), "v1", quota_usage=usage, warnings=list(self.warnings), partial_result=not self.complete,
                          complete_listing=bool(self.complete))


# ------------------------------------------------------------------------------------------------------------------ Workday
_POSTED_ON = re.compile(r"posted\s+(today|yesterday|(\d+)\+?\s+days?\s+ago)", re.I)


def workday_posted_at(text: str | None, now: datetime) -> datetime | None:
    """The list says 'Posted Today', 'Posted Yesterday', 'Posted 3 Days Ago', 'Posted 30+ Days Ago': a date at midnight UTC, only as exact as that wording."""
    m = _POSTED_ON.search(text or "")
    if not m:
        return None
    word = m.group(1).lower()
    days = 0 if word == "today" else 1 if word == "yesterday" else int(m.group(2))
    base = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return base - timedelta(days=days)


def workday_job_id(external_path: str) -> str:
    """The requisition id at the end of the posting's path ('.../Senior-Developer_R0457649-1' -> 'R0457649-1'): it survives a title being edited."""
    last = external_path.rstrip("/").rsplit("/", 1)[-1]
    tail = last.rsplit("_", 1)[-1]
    return tail if "_" in last and 0 < len(tail) <= 40 else last


class WorkdayConnector(_MoreBase):
    """Workday career sites through the public `cxs` job interface that the career page itself uses.

    * `board` is '{tenant}.wd{N}/{site}', for example 'acme.wd3/External'.
    * Pages of 20 (the maximum the service accepts), at most MAX_PAGES a run and a pause between requests; a wall-clock budget keeps a run inside the
      job lease. The listing is complete only when every posting the first page announced (`total`) was read in the same run.
    * ONLY ENGINEERING postings (the same title test the job events use) are kept and get a detail call (at most DETAIL_CAP a run). Large banks list a
      thousand roles of every kind; storing them all would add rows and "role closed" events nobody can use. Closing still works: an advert missing
      from a complete listing (or retitled to a non-engineering role) is closed.
    """
    provider = "workday"
    BOARD_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.wd\d{1,3}/[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
    PAGE = 20
    MAX_PAGES = 60                 # 1,200 postings
    DETAIL_CAP = 40
    PAGE_PAUSE = 0.3
    TIME_BUDGET_S = 170.0

    def __init__(self, *a: Any, clock=time.monotonic, **kw: Any):
        super().__init__(*a, **kw)
        self._clock = clock
        self.page_pause = self.PAGE_PAUSE
        self.host, self.tenant, self.wd, self.site = split_workday_board(self.board)
        self.api = f"https://{self.host}/wday/cxs/{self.tenant}/{quote(self.site)}"

    async def _post_page(self, offset: int, limit: int) -> dict[str, Any]:
        url = f"{self.api}/jobs"
        await self._allowed(url)
        await self._allowed(f"https://{self.host}/{quote(self.site)}/")             # robots rules name the career site's own path, not /wday/
        try:
            data = await request_json_patient(self.client, "POST", url, sleep=self._sleep, json={"limit": limit, "offset": offset, "searchText": "", "appliedFacets": {}},
                                              headers={"Accept": "application/json"})
        except SchemaDrift as exc:
            if exc.status == 422:       # an unknown tenant answers 422 with no body
                raise NotFound("Workday does not know this tenant", status=422) from exc
            raise
        if not isinstance(data, dict) or not isinstance(data.get("jobPostings"), list):
            raise SchemaDrift("Workday response has no 'jobPostings' list")
        if offset == 0 and (isinstance(data.get("total"), bool) or not isinstance(data.get("total"), int) or data["total"] < 0):
            raise SchemaDrift("Workday response has no usable 'total'")         # only the first page carries it
        return data

    async def health_check(self) -> HealthCheck:
        try:
            await self._post_page(0, 1)
            return HealthCheck(True)
        except Exception as exc:  # noqa: BLE001
            return HealthCheck(False, str(exc), getattr(exc, "error_class", "transient_network"))

    async def _list(self) -> list[dict[str, Any]]:
        started = self._clock()
        now = self.now()
        seen: dict[str, dict[str, Any]] = {}
        unnamed = pages = 0
        total: int | None = None
        capped = False
        offset = 0
        while True:
            if pages >= self.MAX_PAGES or self._clock() - started > self.TIME_BUDGET_S:
                capped = True
                break
            if pages:
                await self._sleep(self.page_pause)
            data = await self._post_page(offset, self.PAGE)
            pages += 1
            if total is None:
                total = data["total"]
            postings = data["jobPostings"]
            fresh = 0
            for p in postings:
                path = p.get("externalPath") if isinstance(p, dict) else None
                if not isinstance(path, str) or not path.startswith("/"):
                    unnamed += 1
                    continue
                if path not in seen:
                    seen[path] = p
                    fresh += 1
            offset += self.PAGE
            # stop at the announced total; an offset beyond the end would wrap round to the first page, so never ask for one
            if offset >= total or len(postings) < self.PAGE or fresh == 0:
                break
        read = len(seen) + unnamed
        self.complete = (not capped) and total is not None and read >= total
        if capped:
            self.warnings.append(f"Workday board {self.board}: read the newest {read} of {total} postings in {pages} pages, so no advert is closed on this read")
        elif not self.complete:
            self.warnings.append(f"Workday board {self.board}: read {read} of {total} postings (the list changed while it was read), so no advert is closed on this read")
        self.stats.update({"requests": pages, "pages": pages, "postings_seen": read, "board_total": total or 0})
        rows: list[dict[str, Any]] = []
        used_ids: set[str] = set()
        details = 0
        detail_stop = False
        for path, p in seen.items():
            title = (p.get("title") or "").strip()
            if not title or not engineering_relevant(title):
                continue
            jid = workday_job_id(path)
            if jid in used_ids:
                jid = path.rstrip("/").rsplit("/", 1)[-1]
            used_ids.add(jid)
            desc, time_type, start, url, loc = "", None, None, None, (p.get("locationsText") or "").strip() or None
            if details < self.DETAIL_CAP and not detail_stop and self._clock() - started <= self.TIME_BUDGET_S:
                if details:
                    await self._sleep(self.page_pause)
                details += 1
                try:
                    d = await self._json(f"{self.api}{quote(path, safe='/-_.~%')}", headers={"Accept": "application/json"})
                    info = (d or {}).get("jobPostingInfo") if isinstance(d, dict) else None
                    if isinstance(info, dict):
                        desc = plain_text(info.get("jobDescription"))
                        time_type, start, url = info.get("timeType"), _dt(info.get("startDate")), info.get("externalUrl")
                        loc = (info.get("location") or loc or "").strip() or None
                except (RateLimited, PolicyBlocked):
                    detail_stop = True        # the service asked us to slow down (or the site said no): keep what we have, stop asking
                except Exception:  # noqa: BLE001 - a missing detail only loses keywords, never the posting
                    pass
            rows.append({"_id": jid, "_title": title, "_team": None, "_location": loc, "_employment": classify_employment(time_type, title, desc),
                         "_posted": start or workday_posted_at(p.get("postedOn"), now), "_updated": None,
                         "_url": url if isinstance(url, str) and url.startswith("http") else f"https://{self.host}/{self.site}{path}",
                         "_desc": desc, "_tech": tech_keywords(f"{title} {desc}")})
        self.stats.update({"requests": pages + details, "details": details, "postings_kept": len(rows)})
        return rows


# ------------------------------------------------------------------------------------------------------------------ Personio
def personio_employment(code: str | None, title: str) -> str:
    c = (code or "").lower().replace("-", "_")
    if c in ("intern", "internship", "trainee", "working_student", "student", "apprentice", "apprenticeship"):
        return "internship"
    if c in ("freelance", "freelancer"):
        return "freelance"
    if c in ("fixed_term", "temporary", "temp", "contract"):
        return "contract"
    if c == "permanent":
        return "permanent"
    return classify_employment(c.replace("_", " ") or None, title)


class PersonioConnector(_MoreBase):
    """Personio's public job feed (the XML 'workzag-jobs' document every Personio career page is built from). The feed carries the full advert text."""
    provider = "personio"
    TLDS = ("de", "com")

    async def _feed(self) -> str:
        last: NotFound | None = None
        for tld in self.TLDS:
            try:
                return await self._text(f"https://{self.board}.jobs.personio.{tld}/xml")
            except NotFound as exc:
                last = exc
        assert last is not None
        raise last

    async def _list(self) -> list[dict[str, Any]]:
        root = parse_xml(await self._feed(), "Personio feed")
        if _local(root.tag) != "workzag-jobs":
            raise SchemaDrift("Personio feed has no 'workzag-jobs' root")
        out = []
        for p in [c for c in root if _local(c.tag) == "position"]:
            pid, title = _child_text(p, "id"), _child_text(p, "name")
            if not pid or not title:
                continue
            desc = ""
            for jd in p.iter():
                if _local(jd.tag) == "jobDescription":
                    desc += " " + plain_text(_child_text(jd, "value"))
            desc = desc.strip()
            if self.company_name is None:
                self.company_name = _child_text(p, "subcompany")
            office = _child_text(p, "office")
            out.append({"_id": pid, "_title": title, "_team": _child_text(p, "department"), "_location": office,
                        "_employment": personio_employment(_child_text(p, "employmentType"), title),
                        "_posted": _dt(_child_text(p, "createdAt")), "_updated": None, "_url": f"https://{self.board}.jobs.personio.de/job/{pid}",
                        "_desc": desc, "_tech": tech_keywords(f"{title} {desc}")})
        return out


# ------------------------------------------------------------------------------------------------------------------ Teamtailor
class TeamtailorConnector(_MoreBase):
    """Teamtailor's public job feed (RSS 2.0 with Teamtailor's location and department elements)."""
    provider = "teamtailor"

    async def _list(self) -> list[dict[str, Any]]:
        root = parse_xml(await self._text(f"https://{self.board}.teamtailor.com/jobs.rss"), "Teamtailor feed")
        if _local(root.tag) != "rss":
            raise SchemaDrift("Teamtailor feed is not an RSS document")
        channel = next((c for c in root if _local(c.tag) == "channel"), None)
        if channel is None:
            raise SchemaDrift("Teamtailor feed has no channel")
        self.company_name = _child_text(channel, "title")
        out = []
        for it in [c for c in channel if _local(c.tag) == "item"]:
            guid, title, link = _child_text(it, "guid"), _child_text(it, "title"), _child_text(it, "link")
            jid = guid or link
            if not jid or not title:
                continue
            locs = []
            for c in it:
                if _local(c.tag) == "locations":
                    for loc in c:
                        parts = [_child_text(loc, k) for k in ("city", "country")] or []
                        name = ", ".join(x for x in parts if x) or _child_text(loc, "name")
                        if name:
                            locs.append(name)
            desc = plain_text(_child_text(it, "description"))
            out.append({"_id": jid, "_title": title, "_team": _child_text(it, "department") or _child_text(it, "division"), "_location": "; ".join(dict.fromkeys(locs)) or None,
                        "_employment": classify_employment(None, title, desc), "_posted": _dt(_child_text(it, "pubDate")), "_updated": None, "_url": link,
                        "_desc": desc, "_tech": tech_keywords(f"{title} {desc}")})
        return out


# ------------------------------------------------------------------------------------------------------------------ Recruitee
def recruitee_employment(code: str | None, title: str) -> str:
    c = (code or "").lower()
    if any(w in c for w in ("intern", "trainee", "apprentice", "student")):
        return "internship"
    if "freelance" in c:
        return "freelance"
    if any(w in c for w in ("fixed_term", "temporary", "contract")):
        return "contract"
    if "permanent" in c:
        return "permanent"
    return classify_employment(c.replace("_", " ") or None, title)


class RecruiteeConnector(_MoreBase):
    """Recruitee's public offers list: every published offer with its full text, in one answer."""
    provider = "recruitee"

    async def _list(self) -> list[dict[str, Any]]:
        data = await self._json(f"https://{self.board}.recruitee.com/api/offers/")
        if not isinstance(data, dict) or not isinstance(data.get("offers"), list):
            raise SchemaDrift("Recruitee response has no 'offers' list")
        out = []
        for o in data["offers"]:
            if not isinstance(o, dict) or o.get("id") is None or not o.get("title"):
                continue
            if o.get("status") not in (None, "published"):
                continue
            if self.company_name is None:
                self.company_name = o.get("company_name")
            title = str(o["title"]).strip()
            desc = plain_text(f"{o.get('description') or ''} {o.get('requirements') or ''}")
            loc = o.get("location") or ", ".join(x for x in (o.get("city"), o.get("country")) if x) or None
            url = o.get("careers_url") or (f"https://{self.board}.recruitee.com/o/{o['slug']}" if o.get("slug") else None)
            out.append({"_id": str(o["id"]), "_title": title, "_team": o.get("department"), "_location": loc,
                        "_employment": recruitee_employment(o.get("employment_type_code"), title), "_posted": _dt(o.get("published_at")) or _dt(o.get("created_at")),
                        "_updated": o.get("updated_at"), "_url": url, "_desc": desc, "_tech": tech_keywords(f"{title} {desc}")})
        return out


# ------------------------------------------------------------------------------------------------------------------ Workable
class WorkableConnector(_MoreBase):
    """Workable's public account widget (what Workable's own embed uses): the published adverts of one account, with text when `details=true`.
    Built from Workable's documentation: the build sandbox could not reach it (see the module note)."""
    provider = "workable"

    async def _list(self) -> list[dict[str, Any]]:
        data = await self._json(f"https://apply.workable.com/api/v1/widget/accounts/{self.board}", params={"details": "true"})
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SchemaDrift("Workable response has no 'jobs' list")
        self.company_name = data.get("name") if isinstance(data.get("name"), str) else None
        out = []
        for j in data["jobs"]:
            if not isinstance(j, dict):
                continue
            jid = j.get("shortcode") or j.get("code")
            title = (j.get("title") or "").strip()
            if not jid or not title:
                continue
            desc = plain_text(j.get("description"))
            locs = j.get("locations") if isinstance(j.get("locations"), list) else []
            loc = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x) or \
                  next((", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x) for l in locs if isinstance(l, dict) and not l.get("hidden")), None)
            out.append({"_id": str(jid), "_title": title, "_team": j.get("department") or j.get("function"), "_location": loc or None,
                        "_employment": classify_employment(j.get("employment_type"), title, desc),
                        "_posted": _dt(j.get("published_on")) or _dt(j.get("created_at")), "_updated": None,
                        "_url": j.get("url") or j.get("shortlink"), "_desc": desc, "_tech": tech_keywords(f"{title} {desc}")})
        return out


MORE_CONNECTORS: dict[str, type[_MoreBase]] = {"workday": WorkdayConnector, "personio": PersonioConnector, "teamtailor": TeamtailorConnector,
                                               "recruitee": RecruiteeConnector, "workable": WorkableConnector}
