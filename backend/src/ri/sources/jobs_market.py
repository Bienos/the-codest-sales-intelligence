"""Market-level job search from open data. These sources find who is hiring engineers in financial services even before they are an account.
Sweden: Arbetsförmedlingen JobTech open job-search API (public, keyless; fields checked against the live service).
Germany, Austria, Switzerland: the free public Arbeitnow job-board API (keyless; its terms allow API use with a link back to Arbeitnow, which every
posting we keep carries). Germany's job-agency API refuses ordinary requests (403) and is deliberately not used.
Round 8: two keyless remote-job boards that publish a documented API with a link-back rule, read the same way as Arbeitnow (finance-employer filter, agency
filter, our markets): Jobicy (Europe and worldwide remote roles) and Remote OK. Neither lists a job once it closes, so a job missing from a run is never closed."""
from __future__ import annotations

import asyncio
import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..events.classify import engineering_relevant
from .ats import classify_employment, tech_keywords
from .base import FetchBatch, HealthCheck, PolicyBlocked, RawItem, SchemaDrift, SourceDescriptor, SourceDocumentDraft
from .http import request_json, request_json_patient
from .util import excerpt, parse_dt, sha, utcnow

JOBTECH_URL = "https://jobsearch.api.jobtechdev.se/search"
IT_FIELD = "apaJ_2ja_LuF"          # occupation field "Data/IT"
FINANCE = re.compile(r"\b(bank\w*|betal\w*|fintech|finans\w*|försäkring\w*|kredit\w*|värdepapper\w*|pension\w*|börs\w*|swish|clearing|payments?|insurance|lending)\b", re.I)
# Staffing and consulting firms advertise roles for their CLIENTS: the advert may mention a bank, but the employer is not the buyer. They are left out of the hiring signals.
AGENCY = re.compile(r"\b(academic work|agile resources|experis|randstad|adecco|manpower|tng|kelly|hays|michael page|robert half|bemanning|rekrytering|recruit\w*|staffing|konsult\w*|consult\w*|"
                    r"cognizant|capgemini|accenture|tietoevry|infosys|wipro|tcs|tata consultancy|hcl|ework|nikita|friday|cgi|sogeti|nordic investin|talent|headhunt\w*|search ab|interim)\b", re.I)
DEFAULT_QUERIES = ["bank", "betalningar", "fintech", "försäkring", "finans", "kredit", "payments"]
# adverts for any employer mention "pension" and "insurance" in the benefits text, so the advert body is judged on stricter words
BODY_FINANCE = re.compile(r"\b(bank\w*|betal\w*|fintech|finansiell\w*|finanssektor\w*|kredit\w*|värdepapper\w*|clearing|payments?|lending|swish)\b", re.I)
PAGE = 100
MAX_PER_QUERY = 300


def employment_from_label(label: str | None, title: str) -> str:
    l = (label or "").lower()
    if "frilans" in l or "behov" in l:
        return "freelance"
    if "tidsbegr" in l or "vikariat" in l:
        return "contract"
    if "vanlig" in l or "tillsvidare" in l:
        return "permanent"
    if "sommar" in l or "feriejobb" in l:
        return "internship"
    return classify_employment(label, title)


def from_epoch_ms(ts: Any) -> datetime | None:
    """JobTech's `timestamp` is epoch milliseconds; the database needs a real time, not the number as text."""
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return parse_dt(ts) if isinstance(ts, str) else None
    return datetime.fromtimestamp(ts / 1000, timezone.utc)


class JobTechConnector:
    def __init__(self, client: httpx.AsyncClient, *, queries: list[str] | None = None, source_id: str = "jobs-jobtech-se", now_fn=utcnow, backfill_days: int = 14):
        self.client, self.queries, self.source_id, self.now, self.backfill = client, queries or DEFAULT_QUERIES, source_id, now_fn, backfill_days

    def describe(self) -> SourceDescriptor:
        return SourceDescriptor(self.source_id, "Arbetsförmedlingen JobTech", "official_api", "S2", "v1")

    async def health_check(self) -> HealthCheck:
        try:
            d = await request_json(self.client, "GET", JOBTECH_URL, params={"q": "bank", "limit": 1}, retries=0)
            if not isinstance(d, dict) or "hits" not in d:
                raise SchemaDrift("JobTech response has no 'hits'")
            return HealthCheck(True)
        except Exception as exc:  # noqa: BLE001
            return HealthCheck(False, str(exc), getattr(exc, "error_class", "transient_network"))

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        started = self.now()
        since = cursor.removeprefix("since:") if cursor else (started - timedelta(days=self.backfill)).strftime("%Y-%m-%dT%H:%M:%S")
        seen: dict[str, dict[str, Any]] = {}
        seen_total = dropped_agency = 0
        for q in self.queries:
            for off in range(0, MAX_PER_QUERY, PAGE):
                d = await request_json(self.client, "GET", JOBTECH_URL, params={"q": q, "occupation-field": IT_FIELD, "published-after": since, "limit": PAGE, "offset": off})
                if not isinstance(d, dict) or "hits" not in d:
                    raise SchemaDrift("JobTech response has no 'hits'")
                for h in d["hits"]:
                    seen_total += 1
                    if h.get("removed"):
                        continue
                    employer = (h.get("employer") or {}).get("name") or ""
                    if AGENCY.search(employer):
                        dropped_agency += 1
                        continue
                    near = f"{employer} {h.get('headline') or ''}"
                    body = (h.get("description") or {}).get("text") or ""
                    # the employer or the job title is financial, or the advert itself is about finance (at least two different finance words)
                    if FINANCE.search(near) or len({m.group(0).lower() for m in BODY_FINANCE.finditer(body)}) >= 2:
                        seen[str(h["id"])] = h
                if len(d["hits"]) < PAGE:
                    break
        items = [RawItem(external_id=i, version=h.get("timestamp") and str(h["timestamp"]), payload=h) for i, h in seen.items()]
        return FetchBatch(items, f"since:{started.strftime('%Y-%m-%dT%H:%M:%S')}", False, started, "v1",
                          quota_usage={"requests": len(self.queries), "hits_seen": seen_total, "kept_financial": len(items), "dropped_agency": dropped_agency}, complete_listing=False)

    async def fetch_by_id(self, external_id: str) -> RawItem | None:
        return None

    def canonicalize(self, item: RawItem) -> SourceDocumentDraft:
        h = item.payload
        title = h.get("headline") or ""
        text = (h.get("description") or {}).get("text") or ""
        emp = employment_from_label((h.get("employment_type") or {}).get("label"), title)
        addr = h.get("workplace_address") or {}
        loc = ", ".join(x for x in (addr.get("municipality"), addr.get("region")) if x) or None
        rec = {"provider": "jobtech", "board": "se", "job_id": item.external_id, "employer_name": (h.get("employer") or {}).get("name"), "title": title,
               "team": (h.get("occupation_group") or {}).get("label"), "location": loc, "employment_type": emp, "posted_at": parse_dt(h.get("publication_date")),
               "updated_at_source": from_epoch_ms(h.get("timestamp")), "url": h.get("webpage_url"), "tech_keywords": tech_keywords(f"{title} {text}"), "open_status": "open",
               "country": "SE", "application_deadline": h.get("application_deadline")}
        return SourceDocumentDraft(item.external_id, sha(item.external_id, h.get("timestamp"), title), h.get("webpage_url"), title, parse_dt(h.get("publication_date")), None,
                                   excerpt(text), structured=rec, kind="job_posting")


# ------------------------------------------------------------------------------------------------ Arbeitnow (DACH job board)
ARBEITNOW_URL = "https://www.arbeitnow.com/api/job-board-api"
ARBEITNOW_PAGE_PAUSE = 1.5          # the API allows 50 requests a minute and asks not to be abused
# German staffing and consulting wording, on top of the shared AGENCY list
DACH_AGENCY = re.compile(r"\b(\w*beratung\w*|personalvermittlung|personaldienstleist\w*|zeitarbeit|arbeitnehmerüberlassung|capco|wavestone|adesso|gulp|solcom|etengo)\b", re.I)
# an advert written for someone else's vacancy ("Unser Kunde ist eine Privatbank", "Our client is a bank"): the bank is unnamed and the advertiser is not the buyer
CLIENT_ADVERT = re.compile(r"\b(?:unser kunde\b|für unseren kunden\b|unserem kunden\b|unseres kunden\b|unser(?:em|en)? mandant|our client (?:is|has|seeks|in|based)\b|"
                           r"our client,|on behalf of (?:a|our) client\b|(?:join|for) our client(?![\w'’]|\s+(?:base|offering|network|portfolio|products?|relationships?)\b))", re.I)
# a financial employer read from its NAME: German compounds such as Commerzbank, Sparkasse or Versicherungskammer
NAME_FINANCE_DE = re.compile(r"(\w+bank\b|\bsparkasse\w*|\bversicher\w*|\bfinanz\w*)", re.I)
# Employers introduce themselves at the top of the advert; two different finance words there mean the employer is in financial services.
# (The whole advert is not used: benefits text mentions pensions, insurance and payments for every employer.)
FINANCE_INTRO = re.compile(
    r"\b(bank(s|ing|en)?|neo-?bank\w*|\w*sparkasse\w*|fintech\w*|insurtech\w*|regtech\w*|wealthtech\w*|payments|"
    r"payment (platform|provider|service provider|infrastructure|solutions?)|zahlungsverkehr\w*|zahlungsdienst\w*|lending|kredit\w*|"
    r"credit cards?|card issuing|acquiring|clearing|brokerage|trading platform|asset management|wealth management|insurer|insurance compan(y|ies)|"
    r"versicherung\w*|versicherer|finanzdienstleist\w*|financial services|financial institutions?|open banking|core banking|capital markets|"
    r"stock exchange|börse\w*|wertpapier\w*)\b", re.I)
INTRO_CHARS = 1200
MARKETS = {"DE", "AT", "CH", "GB", "SE", "NO", "DK", "FI", "PL"}
_COUNTRY = [
    ("CH", r"switzerland|schweiz|suisse|svizzera|zürich|zurich|genève|geneva|basel|bern|lausanne|zug|lugano|winterthur|luzern|lucerne|st\.? gallen|aarau|schlieren"),
    ("AT", r"austria|österreich|wien|vienna|graz|linz|salzburg|innsbruck"),
    ("DE", r"germany|deutschland|berlin|münchen|munich|hamburg|frankfurt|köln|koeln|cologne|düsseldorf|dusseldorf|stuttgart|leipzig|dresden|hannover|hanover|"
           r"nürnberg|nuremberg|bremen|essen|dortmund|bonn|mannheim|karlsruhe|kassel|augsburg|freiburg|mainz|wiesbaden|aachen|potsdam|bielefeld|bochum|"
           r"münster|ratingen|heidelberg|darmstadt|regensburg|ulm|eschborn|homeoffice"),
    ("GB", r"united kingdom|uk|england|scotland|wales|northern ireland|london|manchester|edinburgh|glasgow|cardiff|belfast|bristol|birmingham|leeds|"
           r"cambridge|oxford|milton keynes|bath|woking|reading"),
    ("PL", r"poland|polska|warsaw|warszawa|kraków|krakow|wrocław|wroclaw|gdańsk|gdansk|poznań|poznan|łódź|lodz"),
    ("SE", r"sweden|sverige|stockholm|göteborg|gothenburg|malmö|malmo"),
    ("DK", r"denmark|danmark|copenhagen|københavn|aarhus"),
    ("NO", r"norway|norge|oslo"),
    ("FI", r"finland|suomi|helsinki|espoo|tampere"),
]
_COUNTRY_RE = [(c, re.compile(rf"\b({p})\b", re.I)) for c, p in _COUNTRY]
_ELSEWHERE = re.compile(r"\b(france|paris|lyon|toulouse|bordeaux|nantes|lille|marseille|strasbourg|rennes|île-de-france|netherlands|nederland|amsterdam|"
                        r"rotterdam|utrecht|eindhoven|den haag|the hague|groningen|delft|spain|españa|madrid|barcelona|portugal|lisbon|lisboa|ireland|dublin|"
                        r"belgium|brussels|bruxelles|italy|italia|milan|milano|united states|usa|new york|san francisco|canada|toronto|india|bangalore|"
                        r"singapore|dubai|czech|prague|praha|romania|bucharest|hungary|budapest|greece|athens|estonia|tallinn|lithuania|vilnius|latvia|riga|"
                        r"australia|sydney|melbourne|japan|tokyo|hong kong|china|shanghai|brazil|são paulo|mexico|israel|tel aviv|south africa)\b", re.I)


def country_of(location: str | None) -> str | None:
    """The country of a free-text Arbeitnow location: an ISO code for our markets, 'other' for a place clearly elsewhere, None for remote or unknown."""
    loc = location or ""
    for code, rx in _COUNTRY_RE:
        if rx.search(loc):
            return code
    return "other" if _ELSEWHERE.search(loc) else None


def employment_from_types(job_types: list[str] | None, title: str) -> str:
    """Arbeitnow job types are free text in English, German and French ('Unbefristeter Arbeitsvertrag', 'fulltime fixed term', 'Werkstudent')."""
    l = " ".join(job_types or []).lower()
    if re.search(r"freelance|freiberuf|selbstständig|selbststandig|independent contractor", l):
        return "freelance"
    if re.search(r"praktik|\bintern\b|internship|werkstudent|working student|student|ausbildung|apprentice|\bstage\b|alternance|dual studies|minijob", l):
        return "internship"
    if re.search(r"\bbefristet|fixed[- ]term|temporary|\bcontract\b|interim|\bcdd\b", l) and "indefinite contract" not in l:
        return "contract"
    if re.search(r"unbefristet|permanent|full[- ]?time|part[- ]?time|vollzeit|teilzeit|\bcdi\b|employee", l):
        return "permanent"
    return classify_employment(" ".join(job_types or []) or None, title)


def plain_text(desc: str | None) -> str:
    """Arbeitnow descriptions are HTML that has itself been HTML-escaped."""
    t = html.unescape(desc or "")
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.I | re.S)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def financial_employer(employer: str, title: str, text: str) -> str | None:
    """'name' when the employer's name or the job title is financial, 'advert' when the advert's opening describes a financial employer, else None."""
    from ..events.structured import employer_role
    if FINANCE.search(f"{employer} {title}") or employer_role(employer) or NAME_FINANCE_DE.search(employer):
        return "name"
    return "advert" if len({m.group(0).lower() for m in FINANCE_INTRO.finditer(text[:INTRO_CHARS])}) >= 2 else None


class ArbeitnowConnector:
    """Arbeitnow's free job-board API: about 5,000 recent postings, up to 300 a page, mostly Germany, Austria and Switzerland (some UK, French and
    Dutch postings too). There is no search or category filter, so every page is read with a pause, and only engineering and IT roles at banks,
    payment, fintech and insurance employers in our markets are kept (the same title rule as job events, and JobTech's finance and agency words)."""

    def __init__(self, client: httpx.AsyncClient, *, source_id: str = "jobs-arbeitnow-de", now_fn=utcnow, backfill_days: int = 14, robots=None):
        from .webwatch import RobotsCache
        self.client, self.source_id, self.now, self.backfill = client, source_id, now_fn, backfill_days
        self.robots = robots or RobotsCache()
        self.page_pause = ARBEITNOW_PAGE_PAUSE
        self.sleep = asyncio.sleep          # replaced in tests

    def describe(self) -> SourceDescriptor:
        return SourceDescriptor(self.source_id, "Arbeitnow", "official_api", "S2", "v1")

    async def _page(self, page: int) -> dict[str, Any]:
        if not await self.robots.allowed(self.client, ARBEITNOW_URL):
            raise PolicyBlocked(self.robots.refusal(ARBEITNOW_URL))
        d = await request_json_patient(self.client, "GET", ARBEITNOW_URL, sleep=self.sleep, params={"page": page})
        if not isinstance(d, dict) or not isinstance(d.get("data"), list):
            raise SchemaDrift("Arbeitnow response has no 'data' list")
        if d["data"] and "slug" not in d["data"][0]:
            raise SchemaDrift("Arbeitnow job has no 'slug'")
        return d

    async def health_check(self) -> HealthCheck:
        try:
            await self._page(1)
            return HealthCheck(True)
        except Exception as exc:  # noqa: BLE001
            return HealthCheck(False, str(exc), getattr(exc, "error_class", "transient_network"))

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        """One API page per call. The cursor is 'page=<n>'; after the last page it is cleared, so the next run reads the listing from page 1 again."""
        started = self.now()
        page = int(cursor[5:]) if cursor and cursor.startswith("page=") and cursor[5:].isdigit() else 1
        if page > 1:
            await self.sleep(self.page_pause)
        d = await self._page(page)
        cutoff = started - timedelta(days=self.backfill)
        items: list[RawItem] = []
        n = {"hits_seen": 0, "dropped_agency": 0, "dropped_not_engineering": 0, "dropped_other_country": 0, "dropped_old": 0, "dropped_not_financial": 0}
        for j in d["data"]:
            n["hits_seen"] += 1
            employer, title = (j.get("company_name") or "").strip(), (j.get("title") or "").strip()
            if not j.get("slug") or not title:
                continue
            if not engineering_relevant(title):
                n["dropped_not_engineering"] += 1
                continue
            text = plain_text(j.get("description"))
            if AGENCY.search(employer) or DACH_AGENCY.search(employer) or CLIENT_ADVERT.search(text[:INTRO_CHARS]):
                n["dropped_agency"] += 1
                continue
            if country_of(j.get("location")) == "other":
                n["dropped_other_country"] += 1
                continue
            posted = j.get("created_at")
            if isinstance(posted, (int, float)) and datetime.fromtimestamp(posted, timezone.utc) < cutoff:
                n["dropped_old"] += 1
                continue
            if not financial_employer(employer, title, text):
                n["dropped_not_financial"] += 1
                continue
            items.append(RawItem(external_id=str(j["slug"]), version=str(posted) if posted is not None else None, payload=j, url=j.get("url")))
        more = bool((d.get("links") or {}).get("next")) and bool(d["data"])
        return FetchBatch(items, f"page={page + 1}" if more else None, more, started, "v1",
                          quota_usage={"requests": 1, **n, "kept_financial": len(items)}, complete_listing=False)

    async def fetch_by_id(self, external_id: str) -> RawItem | None:
        return None

    def canonicalize(self, item: RawItem) -> SourceDocumentDraft:
        j = item.payload
        title = (j.get("title") or "").strip()
        text = plain_text(j.get("description"))
        tags = [str(t) for t in (j.get("tags") or [])]
        posted = datetime.fromtimestamp(j["created_at"], timezone.utc) if isinstance(j.get("created_at"), (int, float)) else None
        country = country_of(j.get("location"))
        rec = {"provider": "arbeitnow", "board": "de", "job_id": item.external_id, "employer_name": (j.get("company_name") or "").strip() or None, "title": title,
               "team": tags[0] if tags else None, "location": (j.get("location") or "").strip() or None,
               "employment_type": employment_from_types(j.get("job_types"), title), "posted_at": posted, "updated_at_source": posted, "url": j.get("url"),
               "tech_keywords": tech_keywords(f"{title} {text}"), "open_status": "open", "country": country if country in MARKETS else None, "application_deadline": None}
        return SourceDocumentDraft(item.external_id, sha(item.external_id, item.version, title), j.get("url"), title, posted, None, excerpt(text),
                                   raw_meta={"remote": bool(j.get("remote")), "tags": tags[:10], "job_types": list(j.get("job_types") or [])[:10]},
                                   structured=rec, kind="job_posting")


# ------------------------------------------------------------------------------------------------ remote-job boards (Jobicy, Remote OK)
# Both boards list roles that can be done from home, with a free-text "who may apply" location. Our rule: keep the role when it is open to a market where we
# sell (or to Europe or the whole world), drop it when it is clearly for somewhere else. The employer must be a bank, payment, fintech or insurance company
# (the same finance test as Arbeitnow), staffing firms and "our client" adverts are left out, and only engineering titles are kept.
_OPEN_SCOPE = re.compile(r"\b(worldwide|anywhere|global|europe\w*|emea|eu|eea|dach|nordics?|scandinavia\w*)\b", re.I)
_ELSEWHERE_REMOTE = re.compile(r"\b(us|u\.s\.|usa|united states|north america|americas?|latam|latin america|apac|asia|canada|australia|india|brazil|mexico|africa|"
                               r"middle east|new zealand|philippines|singapore|japan)\b", re.I)
REMOTE_PAUSE = 1.5


def markets_in(location: str | None) -> list[str]:
    """Every market of ours the free-text location names, in a fixed order (CH, AT, DE, GB, PL, SE, DK, NO, FI)."""
    loc = location or ""
    return [code for code, rx in _COUNTRY_RE if rx.search(loc)]


def remote_scope(location: str | None) -> str:
    """'market' when the location names one of our markets, 'open' when it is worldwide, Europe-wide or not stated, 'other' when it is clearly for somewhere else."""
    loc = re.sub(r"\s+", " ", location or "").strip()
    if markets_in(loc):
        return "market"
    if not loc or _OPEN_SCOPE.search(loc):
        return "open"
    return "other" if (country_of(loc) == "other" or _ELSEWHERE_REMOTE.search(loc)) else "open"


def screen_remote_job(employer: str, title: str, text: str, location: str | None, n: dict[str, int]) -> bool:
    """True when a remote advert is an engineering role at a financial employer, open to our markets. Counts every drop by reason in `n`."""
    if not engineering_relevant(title):
        n["dropped_not_engineering"] += 1
        return False
    if AGENCY.search(employer) or DACH_AGENCY.search(employer) or CLIENT_ADVERT.search(text[:INTRO_CHARS]):
        n["dropped_agency"] += 1
        return False
    if remote_scope(location) == "other":
        n["dropped_other_country"] += 1
        return False
    if not financial_employer(employer, title, text):
        n["dropped_not_financial"] += 1
        return False
    return True


def _remote_counts() -> dict[str, int]:
    return {"hits_seen": 0, "dropped_agency": 0, "dropped_not_engineering": 0, "dropped_other_country": 0, "dropped_old": 0, "dropped_not_financial": 0}


def clean(s: Any) -> str:
    """Board text can carry HTML entities ('Zone &#038; Co'): one plain line."""
    return re.sub(r"\s+", " ", html.unescape(str(s or ""))).strip()


def _cursor_parts(cursor: str | None) -> dict[str, str]:
    return dict(p.split("=", 1) for p in (cursor or "").split(";") if "=" in p)


JOBICY_URL = "https://jobicy.com/api/jobs"
JOBICY_GEO = "anywhere,europe,emea,uk,germany,austria,switzerland,poland,sweden,norway,denmark,finland"
JOBICY_PAGE = 100                  # the API's own maximum
JOBICY_PAGE_PAUSE = 2.0
JOBICY_WINDOW_DAYS = 7             # Jobicy only serves the last seven days


class JobicyConnector:
    """Jobicy's free job API (documented at jobicy.com, keyless): remote engineering roles open to Europe, the world or one of our markets, newest first, only the
    last seven days. Its terms allow storing and analysing the listings with credit to Jobicy; every posting we keep links to its Jobicy page. A job that is no longer
    in the list is not necessarily closed (the list is a seven-day window), so nothing is ever closed from it. One API page per call: the cursor is
    'since=<time>' between runs (stop at what the last run already saw) and 'since=<time>;next=<token>' inside a run. A token expires after a day: an expired one
    makes the run start the list again instead of failing."""

    def __init__(self, client: httpx.AsyncClient, *, source_id: str = "jobs-jobicy-remote", now_fn=utcnow, backfill_days: int = JOBICY_WINDOW_DAYS, robots=None):
        from .webwatch import RobotsCache
        self.client, self.source_id, self.now, self.backfill = client, source_id, now_fn, backfill_days
        self.robots = robots or RobotsCache()
        self.page_pause = JOBICY_PAGE_PAUSE
        self.sleep = asyncio.sleep          # replaced in tests

    def describe(self) -> SourceDescriptor:
        return SourceDescriptor(self.source_id, "Jobicy", "official_api", "S2", "v1")

    async def _page(self, token: str | None) -> dict[str, Any]:
        if not await self.robots.allowed(self.client, JOBICY_URL):
            raise PolicyBlocked(self.robots.refusal(JOBICY_URL))
        params: dict[str, Any] = {"count": JOBICY_PAGE, "geo": JOBICY_GEO, "industry": "engineering"}
        if token:
            params["cursor"] = token
        d = await request_json_patient(self.client, "GET", JOBICY_URL, sleep=self.sleep, params=params)
        if not isinstance(d, dict) or not isinstance(d.get("jobs"), list):
            raise SchemaDrift("Jobicy response has no 'jobs' list")
        if d["jobs"] and "id" not in d["jobs"][0]:
            raise SchemaDrift("Jobicy job has no 'id'")
        return d

    async def health_check(self) -> HealthCheck:
        try:
            await self._page(None)
            return HealthCheck(True)
        except Exception as exc:  # noqa: BLE001
            return HealthCheck(False, str(exc), getattr(exc, "error_class", "transient_network"))

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        started = self.now()
        st = _cursor_parts(cursor)
        since = parse_dt(st.get("since")) or (started - timedelta(days=self.backfill))
        token = st.get("next") or None
        if token:
            await self.sleep(self.page_pause)
        try:
            d = await self._page(token)
        except SchemaDrift as exc:
            if not (token and getattr(exc, "status", None) == 400):
                raise
            token, d = None, await self._page(None)          # the token expired: read the list again from the newest
        cutoff = since - timedelta(days=1)                   # a day of overlap: the board lists a job a few hours after it is posted
        n = _remote_counts()
        items: list[RawItem] = []
        reached_old = False
        for j in d["jobs"]:
            n["hits_seen"] += 1
            posted = parse_dt(j.get("pubDate"))
            if posted is not None and posted < cutoff:
                n["dropped_old"] += 1
                reached_old = True                           # newest first: everything after this is older too
                continue
            employer, title = clean(j.get("companyName")), clean(j.get("jobTitle"))
            if not j.get("id") or not title:
                continue
            text = plain_text(j.get("jobDescription")) or clean(j.get("jobExcerpt"))
            if screen_remote_job(employer, title, text, clean(j.get("jobGeo")), n):
                items.append(RawItem(external_id=str(j["id"]), version=str(j.get("pubDate") or ""), payload=j, url=j.get("url")))
        nxt = d.get("nextCursor") if isinstance(d.get("nextCursor"), str) else None
        more = bool(d.get("hasMore")) and bool(nxt) and not reached_old and bool(d["jobs"])
        next_cursor = f"since={since.isoformat(timespec='seconds')};next={nxt}" if more else f"since={started.isoformat(timespec='seconds')}"
        return FetchBatch(items, next_cursor, more, started, "v1", quota_usage={"requests": 1, **n, "kept_financial": len(items)}, complete_listing=False)

    async def fetch_by_id(self, external_id: str) -> RawItem | None:
        return None

    def canonicalize(self, item: RawItem) -> SourceDocumentDraft:
        j = item.payload
        title = clean(j.get("jobTitle"))
        text = plain_text(j.get("jobDescription")) or clean(j.get("jobExcerpt"))
        geo = clean(j.get("jobGeo"))
        markets = markets_in(geo)
        industries = [clean(x) for x in (j.get("jobIndustry") or [])] if isinstance(j.get("jobIndustry"), list) else []
        types = [clean(x) for x in (j.get("jobType") or [])] if isinstance(j.get("jobType"), list) else []
        posted = parse_dt(j.get("pubDate"))
        rec = {"provider": "jobicy", "board": "remote", "job_id": item.external_id, "employer_name": clean(j.get("companyName")) or None, "title": title,
               "team": industries[0] if industries else None, "location": geo or None, "employment_type": employment_from_types(types, title), "posted_at": posted,
               "updated_at_source": posted, "url": j.get("url"), "tech_keywords": tech_keywords(f"{title} {text}"), "open_status": "open",
               "country": markets[0] if len(markets) == 1 else None, "application_deadline": None}
        return SourceDocumentDraft(item.external_id, sha(item.external_id, item.version, title), j.get("url"), title, posted, None, excerpt(text),
                                   raw_meta={"remote": True, "level": j.get("jobLevel"), "job_types": types[:10]}, structured=rec, kind="job_posting")


REMOTEOK_URL = "https://remoteok.com/api"


class RemoteOkConnector:
    """Remote OK's public job API (keyless, one request, the newest ~100 remote roles). Its terms ask for a link back to the Remote OK page and a mention of Remote OK
    as the source: every posting we keep links to its Remote OK page and we never use its logo or its apply link. robots.txt and its content signals are read first.
    The list is a window of recent roles, so a job missing from it is never closed."""

    def __init__(self, client: httpx.AsyncClient, *, source_id: str = "jobs-remoteok", now_fn=utcnow, backfill_days: int = 14, robots=None):
        from .webwatch import RobotsCache
        self.client, self.source_id, self.now, self.backfill = client, source_id, now_fn, backfill_days
        self.robots = robots or RobotsCache()
        self.sleep = asyncio.sleep          # replaced in tests

    def describe(self) -> SourceDescriptor:
        return SourceDescriptor(self.source_id, "Remote OK", "official_api", "S2", "v1")

    async def _list(self) -> list[dict[str, Any]]:
        if not await self.robots.allowed(self.client, REMOTEOK_URL):
            raise PolicyBlocked(self.robots.refusal(REMOTEOK_URL))
        d = await request_json_patient(self.client, "GET", REMOTEOK_URL, sleep=self.sleep)
        if not isinstance(d, list):
            raise SchemaDrift("Remote OK response is not a list")
        jobs = [x for x in d if isinstance(x, dict) and "legal" not in x]          # the first element is the terms notice
        if jobs and not (jobs[0].get("id") and jobs[0].get("position")):
            raise SchemaDrift("Remote OK job has no 'id' or 'position'")
        return jobs

    async def health_check(self) -> HealthCheck:
        try:
            await self._list()
            return HealthCheck(True)
        except Exception as exc:  # noqa: BLE001
            return HealthCheck(False, str(exc), getattr(exc, "error_class", "transient_network"))

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        started = self.now()
        jobs = await self._list()
        cutoff = started - timedelta(days=self.backfill)
        n = _remote_counts()
        items: list[RawItem] = []
        for j in jobs:
            n["hits_seen"] += 1
            employer, title = clean(j.get("company")), clean(j.get("position"))
            if not j.get("id") or not title:
                continue
            epoch = j.get("epoch")
            if isinstance(epoch, (int, float, str)) and str(epoch).isdigit() and datetime.fromtimestamp(int(epoch), timezone.utc) < cutoff:
                n["dropped_old"] += 1
                continue
            if screen_remote_job(employer, title, plain_text(j.get("description")), clean(j.get("location")), n):
                items.append(RawItem(external_id=str(j["id"]), version=str(j.get("epoch") or ""), payload=j, url=j.get("url")))
        return FetchBatch(items, None, False, started, "v1", quota_usage={"requests": 1, **n, "kept_financial": len(items)}, complete_listing=False)

    async def fetch_by_id(self, external_id: str) -> RawItem | None:
        return None

    def canonicalize(self, item: RawItem) -> SourceDocumentDraft:
        j = item.payload
        title = clean(j.get("position"))
        text = plain_text(j.get("description"))
        loc = clean(j.get("location"))
        tags = [clean(t) for t in (j.get("tags") or [])] if isinstance(j.get("tags"), list) else []
        epoch = j.get("epoch")
        posted = datetime.fromtimestamp(int(epoch), timezone.utc) if str(epoch).isdigit() else parse_dt(j.get("date"))
        markets = markets_in(loc)
        url = j.get("url")          # the Remote OK page, never the apply link
        rec = {"provider": "remoteok", "board": "remote", "job_id": item.external_id, "employer_name": clean(j.get("company")) or None, "title": title,
               "team": tags[0] if tags else None, "location": loc or None, "employment_type": employment_from_types(tags, title), "posted_at": posted,
               "updated_at_source": posted, "url": url, "tech_keywords": tech_keywords(f"{title} {' '.join(tags)} {text}"), "open_status": "open",
               "country": markets[0] if len(markets) == 1 else None, "application_deadline": None}
        return SourceDocumentDraft(item.external_id, sha(item.external_id, item.version, title), url, title, posted, None, excerpt(text),
                                   raw_meta={"remote": True, "tags": tags[:10]}, structured=rec, kind="job_posting")
