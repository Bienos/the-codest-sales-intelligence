"""The account scan: given an account's own website, find where it publishes (news feeds, press page, careers page, job-board slug) and read it.

Only the account's own pages are fetched, through the SSRF-safe client, with robots.txt honoured. Nothing here uses a login or gets round a block.
Every hit still goes through the normal pipeline (quote-anchored extraction, verification), so a page is never treated as evidence by itself.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from . import ats_more
from .http import UA
from .webwatch import RobotsCache

NEWS_PATH = re.compile(r"/(news|newsroom|press|press-releases?|media|media-centre|media-center|presse|pressemitteilungen|medien|medienmitteilungen|biuro-prasowe|aktualnosci|pressrum|pressemeddelelser|insights|stories|blog)(/|$|\?)", re.I)
CAREERS_PATH = re.compile(r"/(careers?|jobs|join-us|work-with-us|vacancies|karriere|offene-stellen|kariera|oferty-pracy|ledige-stillinger|lediga-jobb)(/|$|\?)", re.I)
PROCUREMENT_PATH = re.compile(r"/(procurement|suppliers?|supplier-portal|become-a-supplier|supplier-registration|supplying-goods-and-services|tenders?|ausschreibungen|lieferanten|zamowienia|przetargi|dostawcy|upphandling|anskaffelser|udbud)(/|$)", re.I)
ATS = [("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)", re.I)),
       ("greenhouse_eu", re.compile(r"(?:boards|job-boards)\.eu\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)", re.I)),
       ("lever", re.compile(r"jobs\.lever\.co/([a-z0-9_-]+)", re.I)),
       ("lever_eu", re.compile(r"jobs\.eu\.lever\.co/([a-z0-9_-]+)", re.I)),
       ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_.-]+)", re.I)),
       ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I))]
BOARD_HOST = {"greenhouse": "boards.greenhouse.io", "lever": "jobs.lever.co", "lever_eu": "jobs.eu.lever.co", "ashby": "jobs.ashbyhq.com", "smartrecruiters": "jobs.smartrecruiters.com"}
# Workday, Personio, Teamtailor, Recruitee and Workable are recognised from the host name by ats_more.detect_board (a Workday board needs the tenant, the
# data-centre number and the site, so one pattern with one capture group does not fit); JOIN is recognised and reported but has no public listing interface.
UNREAD_NOTE = {"join": "job board on JOIN ({board}); JOIN has no public listing interface without an API key, so it is not read"}


def board_url(provider: str, board: str) -> str:
    """The public page of a job board: what `account_endpoints.url` stores for it (unique per account)."""
    if provider in BOARD_HOST:
        return f"https://{BOARD_HOST[provider]}/{board}"
    return ats_more.board_url(provider, board)


def ats_source(provider: str) -> str:
    """One registry source per board family: the EU Lever instance shares the Lever source."""
    return "ats-" + provider.split("_")[0]
_LEGAL = re.compile(r"\b(ag|gmbh|kg|se|sa|spa|s\.p\.a|ltd|limited|plc|llp|inc|llc|corp|corporation|bv|nv|ab|as|oy|oyj|asa|aps|a/s|sp|zoo|z o\.o|group|holding|holdings|the)\b", re.I)


def _name_tokens(name: str) -> set[str]:
    return {t for t in re.sub(r"[^a-z0-9 ]+", " ", _LEGAL.sub(" ", name.lower())).split() if len(t) > 1}


def name_matches(org_name: str, found_name: str | None) -> bool:
    """A board found by guessing the company's short name is used only when the board's own company name is the same company (all words of the shorter name appear in the other)."""
    a, b = _name_tokens(org_name), _name_tokens(found_name or "")
    if not a or not b:
        return False
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    return small <= big


async def probe_boards(client: httpx.AsyncClient, domain: str, org_name: str) -> list[tuple[str, str]]:
    """No job board linked from the website: try the company's short name on boards whose API states the company name, and keep a hit only if that name matches.
    Lever and Ashby do not state a company name, so they are never guessed."""
    label = domain.removeprefix("www.").split(".")[0]
    slugs = list(dict.fromkeys([label, re.sub(r"[^a-z0-9]", "", org_name.lower().split(" ")[0])]))
    hits: list[tuple[str, str]] = []
    for slug in [x for x in slugs if len(x) >= 3][:2]:
        try:
            r = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}", headers={"User-Agent": UA})
            if r.status_code == 200 and name_matches(org_name, (r.json() or {}).get("name")):
                hits.append(("greenhouse", slug))
        except (httpx.HTTPError, ValueError):
            pass
        try:
            r = await client.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params={"limit": 1}, headers={"User-Agent": UA})
            if r.status_code == 200:
                c = (r.json() or {}).get("content") or []
                if c and name_matches(org_name, (c[0].get("company") or {}).get("name")):
                    hits.append(("smartrecruiters", slug))
        except (httpx.HTTPError, ValueError):
            pass
    return hits
MAX_CAREERS_PAGES = 4                     # careers pages read when looking for a job board (home page links first, then what those pages link to)
EMPTY_LISTING = "Listing was empty"      # marker kept in `account_endpoints.last_detail` after a first empty read of a job board
FEED_GUESSES = ("/feed", "/rss.xml", "/news/feed", "/blog/feed")
MAX_BYTES = 2_000_000

# --- the account's engineering/tech blog and its press/insights feed -----------------------------------------------------------------------------------
# Found from what the home page already shows (links and <link rel=alternate>), so the only extra page read is ONE: the engineering page, to see its own feed.
# With the (at most) two extra feeds the scan reads, a scan costs at most three requests more than before. Stored as `account_endpoints` of kind `feed`;
# the class goes in `provider` (the account page shows "Feed (Engineering blog)").
FEED_LABEL = {"engineering": "Engineering blog", "press": "Press and insights", "blog": "Blog"}
ENG_WORDS = {"engineering", "engineers", "engineer", "tech", "techblog", "devblog"}      # not "developers": that is usually the API documentation, not a blog
SECTION_EXTRA = {"blog", "blogs", "news", "articles", "posts", "hub", "team"}               # words that may sit next to an engineering word in a section name (tech-blog)
PRESS_WORDS = {"press", "presse", "pressemitteilungen", "pressemeddelelser", "pressrum", "news", "newsroom", "media", "medien", "medienmitteilungen", "insights",
               "insight", "stories", "releases", "aktualnosci", "nyheter", "uutiset"}
BLOG_WORDS = {"blog", "blogs"}
ENG_HOST = {"engineering", "tech", "techblog", "devblog", "eng"}          # engineering.bank.example, tech.bank.example
MAX_EXTRA_FEEDS = 2                                                       # an engineering feed and a press/insights feed, on top of the first three feeds
MEDIUM_PUBLICATION = re.compile(r"^https?://(?:(?P<sub>[a-z0-9][a-z0-9-]*)\.medium\.com|(?:www\.)?medium\.com/(?P<path>[a-z0-9][a-z0-9._-]*))/?$", re.I)
NOT_PUBLICATION = {"www", "feed", "m", "me", "tag", "topics", "jobs", "plans", "about", "policy", "creators", "business", "membership", "search"}


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if w}


def _own_host_part(host: str, domain: str) -> str:
    """The part of `host` in front of the account's own domain: "engineering" for engineering.bank.example, "" for bank.example or www.bank.example,
    and the whole host for another site (medium.com)."""
    h, b = host.lower().removeprefix("www."), domain.lower().removeprefix("www.")
    return h.removesuffix(b).strip(".") if (h == b or h.endswith("." + b)) else h


def classify_feed(url: str, domain: str, page_hint: str | None = None) -> str | None:
    """Plain class of a feed or page address: "engineering", "press", "blog" or None. Decided by the address only (host part in front of the account's domain, and path),
    because feed titles carry the company's name ("Tech Bank » Feed") and say nothing about the section. `page_hint` is the class of the page the feed was found on."""
    u = urlparse(url)
    words = _words(f"{_own_host_part(u.netloc, domain)} {u.path}")
    if words & ENG_WORDS:
        return "engineering"
    if words & PRESS_WORDS:
        return "press"
    if words & BLOG_WORDS:
        return "blog"
    return page_hint


def is_tech_section(url: str, domain: str) -> bool:
    """A link to the account's engineering or tech blog index: engineering.bank.example, /engineering, /tech, /engineering/blog, /blog/engineering.
    Index pages only (an article under it is not the section), and only on the account's own site."""
    u = urlparse(url)
    if not _same_site(u.netloc, domain):
        return False
    segs = [x for x in u.path.split("/") if x and not re.fullmatch(r"[a-z]{2}(-[a-z]{2})?", x, re.I)]
    sub = _own_host_part(u.netloc, domain)
    if not segs:
        return bool(sub) and sub.split(".")[0].lower() in ENG_HOST
    last = _words(segs[-1].removesuffix(".html"))
    if len(segs) > 3:
        return False
    if last & ENG_WORDS and not (last - ENG_WORDS - SECTION_EXTRA):          # /engineering, /tech-blog, /engineering-news
        return True
    return bool(last) and not (last - SECTION_EXTRA) and len(segs) >= 2 and bool(_words(segs[-2]) & ENG_WORDS)      # /engineering/blog


def medium_feed(url: str, domain: str) -> str | None:
    """Many fintechs publish their engineering blog as a Medium publication. A link to one is taken as the account's own only when the publication's name
    contains the account's own name as a whole word (wise-engineering for wise.com, qonto-way for qonto.com); the feed is Medium's public publication feed."""
    m = MEDIUM_PUBLICATION.match(url.split("#")[0].split("?")[0])
    if not m:
        return None
    slug = (m.group("sub") or m.group("path") or "").lower()
    label = domain.lower().removeprefix("www.").split(".")[0]
    if not slug or slug in NOT_PUBLICATION or slug.startswith("@") or len(label) < 3:
        return None
    label_words, slug_words = {w for w in re.split(r"[-_.]", label) if w}, {w for w in re.split(r"[-_.]", slug) if w}
    return f"https://medium.com/feed/{slug}" if label_words <= slug_words else None


# Names that do not tell two companies apart when an account's OWN page names itself ("Nordea" on the site of Nordea Bank Abp).
_CORE_DROP = {"bank", "abp", "publ", "company", "co"}


def same_company(account_name: str, subject: str | None) -> bool:
    """True when `subject` is the account itself, written the short way: the same words once legal endings and "bank" are left out. A parent, a subsidiary or a
    sister company never matches ("Santander" is not "Santander Consumer Bank Polska"): those still go through the normal identity check."""
    a, b = _name_tokens(account_name) - _CORE_DROP, _name_tokens(subject or "") - _CORE_DROP
    return bool(a) and a == b


def first_party_resolver(org_id: str, account_name: str, base):
    """An event found on an account's own pages that names the account itself is about that account; every other name goes the normal way."""
    def resolve(name: str) -> str | None:
        return org_id if name and same_company(account_name, name) else base(name)
    return resolve


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.feeds: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "link" and "alternate" in (a.get("rel") or "").lower() and any(t in (a.get("type") or "").lower() for t in ("rss", "atom")) and a.get("href"):
            self.feeds.append(a["href"])
        elif tag in ("a", "iframe", "script") and (a.get("href") or a.get("src")):
            self.links.append(a.get("href") or a.get("src") or "")


def _same_site(host: str, base: str) -> bool:
    h, b = host.lower().removeprefix("www."), base.lower().removeprefix("www.")
    return h == b or h.endswith("." + b)


def parse_page(html: str, page_url: str, domain: str) -> dict[str, list]:
    p = _Links()
    p.feed(html[:MAX_BYTES])
    feeds = [urljoin(page_url, f) for f in p.feeds
             if not re.search(r"/comments/feed/?$", urlparse(f).path, re.I)]
    news, careers, procurement, ats, unread = [], [], [], [], []
    for raw in p.links:
        u = urljoin(page_url, raw.strip())
        if not u.startswith("http"):
            continue
        for prov, rx in ATS:
            m = rx.search(u)
            if m and m.group(1).lower() not in {"embed", "jobs", "www"}:
                ats.append((prov, m.group(1)))
        more = ats_more.detect_board(u)               # Workday, Personio, Teamtailor, Recruitee, Workable (decided by the host name)
        if more:
            ats.append(more)
        skipped = ats_more.detect_unread_board(u)     # recognised, not readable
        if skipped:
            unread.append(skipped)
        host = urlparse(u).netloc
        path = urlparse(u).path
        depth = len([x for x in path.split("/") if x and not re.fullmatch(r"[a-z]{2}(-[a-z]{2})?", x, re.I)])   # ignore a language prefix such as /en/
        last = "/" + ([x for x in path.split("/") if x] or [""])[-1].removesuffix(".html") + "/"
        if _same_site(host, domain) and depth <= 3:          # section index pages only: the last path segment must itself be the section, not an article
            if NEWS_PATH.search(last):
                news.append(u.split("#")[0])
            if CAREERS_PATH.search(last) or (last == "//" and CAREERS_PATH.search("/" + host.split(".")[0] + "/")):
                careers.append(u.split("#")[0])
            if PROCUREMENT_PATH.search(last):
                procurement.append(u.split("#")[0])
    tech, medium = [], []
    for raw in p.links:
        u = urljoin(page_url, raw.strip())
        if not u.startswith("http"):
            continue
        if is_tech_section(u, domain):
            tech.append(u.split("#")[0])
        feed = medium_feed(u, domain)
        if feed:
            medium.append(feed)
    dedupe = lambda xs: list(dict.fromkeys(xs))   # noqa: E731
    return {"feeds": dedupe(feeds), "news": dedupe(news), "careers": dedupe(careers), "procurement": dedupe(procurement), "ats": dedupe(ats), "unread": dedupe(unread),
            "tech": dedupe(tech), "medium": dedupe(medium)}


def boards_in_links(links: list[str], base_url: str, readable: set[str]) -> list[tuple[str, str]]:
    """Job boards that a page links to, for the providers we can read: the careers page we already fetch each scan shows boards that were not there
    (or not recognised) when the account's pages were first found. No extra request."""
    out: list[tuple[str, str]] = []
    for raw in links:
        u = urljoin(base_url, (raw or "").strip())
        if not u.startswith("http"):
            continue
        for prov, rx in ATS:
            m = rx.search(u)
            if m and m.group(1).lower() not in {"embed", "jobs", "www"}:
                out.append((prov, m.group(1)))
        more = ats_more.detect_board(u)
        if more:
            out.append(more)
    return [pb for pb in dict.fromkeys(out) if pb[0] in readable]


async def _get(client: httpx.AsyncClient, robots: RobotsCache, url: str) -> str | None:
    if not await robots.allowed(client, url):
        return None
    try:
        r = await client.get(url, headers={"User-Agent": UA}, follow_redirects=True)
    except httpx.HTTPError:
        return None
    return r.text if r.status_code == 200 else None


async def discover(client: httpx.AsyncClient, domain: str, robots: RobotsCache | None = None, org_name: str | None = None) -> dict[str, Any]:
    """Returns {endpoints: [{kind,url,provider,board}], notes: [...]}. Never raises for an unreachable site: that is a finding, not a failure."""
    robots = robots or RobotsCache()
    domain = domain.strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
    home_url = f"https://{domain}/"
    notes: list[str] = []
    html = await _get(client, robots, home_url)
    if html is None:
        html = await _get(client, robots, f"https://www.{domain}/") if not domain.startswith("www.") else None
        home_url = f"https://www.{domain}/" if html else home_url
    if html is None:
        return {"endpoints": [], "notes": [f"{domain}: home page could not be read (blocked by robots.txt, an error, or no response)"]}
    found = parse_page(html, home_url, domain)
    feeds, news, careers, ats, unread = list(found["feeds"]), list(found["news"]), list(found["careers"]), list(found["ats"]), list(found["unread"])
    medium = [f for f in found["medium"] if f not in feeds]          # the account's own Medium publication, if its site links to one (kept apart: it must not stop the plain feed guesses)
    feed_class: dict[str, str | None] = {f: classify_feed(f, domain) for f in feeds}
    feed_class.update({f: classify_feed(f, domain) or "blog" for f in medium})        # a publication the account links to is its own blog
    to_read, read_pages = list(careers[:2]), set[str]()      # job-board links usually sit on the careers page (a company often has a second, local one)
    while to_read and len(read_pages) < MAX_CAREERS_PAGES:
        careers_url = to_read.pop(0)
        if careers_url in read_pages:
            continue
        read_pages.add(careers_url)
        page = await _get(client, robots, careers_url)
        if page:
            more = parse_page(page, careers_url, domain)
            ats += more["ats"]
            unread += more["unread"]
            if not ats:                          # nothing yet: the careers page may point to the company's real jobs site (careers.bank.example)
                to_read += [u for u in more["careers"] if u not in read_pages and u not in to_read][:2]
    eu = [b for p, b in ats if p == "greenhouse_eu"]
    if eu:
        ats = [(p, b) for p, b in ats if p != "greenhouse_eu"]
        notes.append(f"{domain}: job board on Greenhouse's EU instance ({', '.join(eu)}); that instance has no public listing API, so it is not read")
    for prov, board in dict.fromkeys(unread):    # a board we recognise but cannot read: say so instead of staying silent
        if prov in UNREAD_NOTE:
            notes.append(f"{domain}: " + UNREAD_NOTE[prov].format(board=board))
    if not ats and org_name:                     # nothing linked from the site: try the company's short name, accepting only a board that names this company
        ats += await probe_boards(client, domain, org_name)
        if ats:
            notes.append(f"{domain}: job board found by name match, not linked from the website: {', '.join(f'{p}/{b}' for p, b in ats)}")
    if news:                                     # a newsroom often advertises its own feed
        page = await _get(client, robots, news[0])
        if page:
            hint = "engineering" if news[0] in found["tech"] else classify_feed(news[0], domain)
            for f in parse_page(page, news[0], domain)["feeds"]:
                if f not in feed_class:                # a feed new to us on a press page is that section's own; one already known keeps its class
                    feed_class[f] = classify_feed(f, domain, hint)
                feeds.append(f)
    tech_url = next((u for u in found["tech"] if not news or u != news[0]), None)       # (the newsroom page is read already)
    if tech_url and "engineering" not in {feed_class.get(f) for f in [*feeds, *medium]}:      # THE one extra page read: the engineering blog's own page, to see which feed it offers
        page = await _get(client, robots, tech_url)
        if page:
            under = urlparse(tech_url).path.rstrip("/") + "/"
            # a feed counts as the blog's own when its address says engineering, or it sits under the blog's own path (a site-wide feed listed on the page does not)
            own = [f for f in parse_page(page, tech_url, domain)["feeds"] if f not in feed_class
                   and (classify_feed(f, domain) == "engineering" or (under != "/" and urlparse(f).path.startswith(under)))]
            if own:
                feed_class[own[0]] = "engineering"
                feeds.append(own[0])
    if not feeds:
        for g in FEED_GUESSES[:3]:
            body = await _get(client, robots, urljoin(home_url, g))
            if body and re.match(r"\s*(<\?xml|<rss|<feed)", body):
                feeds.append(urljoin(home_url, g))
                feed_class[feeds[-1]] = classify_feed(feeds[-1], domain)
                break
    eps: list[dict[str, Any]] = []
    feeds = list(dict.fromkeys([*feeds, *medium]))
    chosen = feeds[:3]
    for kind in ("engineering", "press"):        # an engineering and a press/insights feed are kept even when the site lists more than three feeds
        extra = next((f for f in feeds if feed_class.get(f) == kind), None)
        if extra and extra not in chosen and len(chosen) < 3 + MAX_EXTRA_FEEDS:
            chosen.append(extra)
    for u in chosen:
        eps.append({"kind": "feed", "url": u, "provider": FEED_LABEL.get(feed_class.get(u) or ""), "board": None})
    for u in news[:2]:
        eps.append({"kind": "newsroom", "url": u, "provider": None, "board": None})
    for u in found["procurement"][:2]:
        eps.append({"kind": "procurement", "url": u, "provider": None, "board": None})
    for u in careers[:1]:
        eps.append({"kind": "careers", "url": u, "provider": None, "board": None})
    for prov, board in list(dict.fromkeys(ats))[:3]:
        eps.append({"kind": "job_board", "url": board_url(prov, board), "provider": prov, "board": board})
    if not eps:
        notes.append(f"{domain}: no news feed, press page, careers page or job board found on the home page")
    return {"endpoints": eps, "notes": notes}


def run_scan(conn, org_id: str, *, client: httpx.AsyncClient, extractor=None, resolver=None, now=None, force_discover: bool = False) -> dict[str, Any]:
    """Discover (or re-use) the account's endpoints, read each one, and push what was found through the normal pipeline."""
    from ..db import q1, qa
    from ..ingest.pipeline import RunResult, _run, close_unseen_jobs, upsert_document, upsert_job
    from ..ingest.process import process_run
    from .factory import ats_connectors
    from .util import utcnow
    from .webwatch import FeedConnector, PageWatchConnector
    now = now or utcnow()
    org = q1(conn, "SELECT id, display_name, canonical_domain FROM organizations WHERE id=%s", (org_id,))
    if not org:
        return {"status": "failed", "error": "account not found"}
    if not org["canonical_domain"]:
        return {"status": "no_website", "error": f"{org['display_name']} has no website recorded, so there is nothing to scan"}
    existing = qa(conn, "SELECT * FROM account_endpoints WHERE organization_id=%s", (org_id,))
    notes: list[str] = []
    if force_discover or not existing or not q1(
        conn, "SELECT 1 FROM account_source_coverage WHERE organization_id=%s AND source_family='procurement'", (org_id,)
    ):
        d = _run(discover(client, org["canonical_domain"], org_name=org["display_name"]))
        notes += d["notes"]
        for e in d["endpoints"]:
            conn.execute("INSERT INTO account_endpoints(organization_id, kind, url, provider, board) VALUES (%s,%s,%s,%s,%s) "
                         "ON CONFLICT (organization_id, url) DO UPDATE SET provider = EXCLUDED.provider WHERE account_endpoints.kind = 'feed' AND account_endpoints.provider IS NULL AND EXCLUDED.provider IS NOT NULL",
                         (org_id, e["kind"], e["url"], e["provider"], e["board"]))
        conn.commit()
        existing = qa(conn, "SELECT * FROM account_endpoints WHERE organization_id=%s", (org_id,))
    res = RunResult("account-scan", "completed")
    robots = RobotsCache()
    retention = {r["source_id"]: r["retention_policy"] for r in qa(conn, "SELECT source_id, retention_policy FROM sources WHERE source_id = 'corporate-ir-newsrooms' OR source_id LIKE 'ats-%'")}
    ats_cls = ats_connectors()
    scanned = 0
    queue = list(existing)          # a job board found on a careers page during this scan is appended and read in the same run
    for ep in queue:
        status, detail, seen = "ok", None, 0
        found_boards: list[dict[str, Any]] = []
        try:
            with conn.transaction():
                if ep["kind"] in ("feed", "newsroom", "careers", "procurement"):
                    sid = "corporate-ir-newsrooms"
                    con = FeedConnector(client, ep["url"], source_id=sid, robots=robots, publisher=org["display_name"]) if ep["kind"] == "feed" else PageWatchConnector(client, ep["url"], source_id=sid, robots=robots)
                    batch = _run(con.fetch(None, 30))
                    for item in batch.items:
                        d = con.canonicalize(item)
                        doc_id, st = upsert_document(conn, sid, d, retention.get(sid) or "excerpt")
                        seen += 1
                        if ep["kind"] == "procurement":
                            # Recertify an unchanged notice from the exact same bytes.
                            # New content must pass extraction again before it can stay open.
                            conn.execute("""UPDATE procurement_notices
                                SET last_status_check_at=CASE WHEN document_id=%s THEN %s ELSE last_status_check_at END,
                                    status=CASE WHEN document_id=%s THEN status ELSE 'unknown' END
                                WHERE buyer_org_id=%s AND official_url=%s AND source_id=%s AND status IN ('open','amended')""",
                                (doc_id, now, doc_id, org_id, ep["url"], sid))
                        if st == "new":
                            res.new_doc_ids.append(doc_id)
                        elif st == "changed":
                            res.changed_doc_ids.append(doc_id)
                    if ep["kind"] == "careers" and batch.items:
                        # boards linked from the careers page we just read (no extra request): accounts scanned before a career system was
                        # recognised pick it up here. At most three boards an account, as in discovery.
                        have = {(e["provider"], e["board"]) for e in queue if e["kind"] == "job_board"}
                        for prov, board in boards_in_links(batch.items[0].payload.get("links") or [], ep["url"], set(ats_cls)):
                            if len(have) >= 3:
                                break
                            if (prov, board) in have:
                                continue
                            row = conn.execute("INSERT INTO account_endpoints(organization_id, kind, url, provider, board) VALUES (%s,'job_board',%s,%s,%s) "
                                               "ON CONFLICT (organization_id, url) DO NOTHING RETURNING *", (org_id, board_url(prov, board), prov, board)).fetchone()
                            if row:
                                have.add((prov, board))
                                found_boards.append(dict(row))
                                notes.append(f"{org['canonical_domain']}: job board {prov}/{board} found on the careers page")
                elif ep["kind"] == "job_board" and ep["provider"] in ats_cls:
                    cls = ats_cls[ep["provider"]]
                    extra_kw: dict[str, Any] = {"robots": robots} if getattr(cls, "USES_ROBOTS", False) else {}
                    if getattr(cls, "USES_KNOWN", False):       # big boards: stored adverts are not asked for again (see WorkdayConnector)
                        extra_kw["known"] = {r["job_id"]: r for r in qa(conn, "SELECT job_id, url, location, employment_type, tech_keywords, posted_at FROM job_postings "
                                                                            "WHERE provider=%s AND board=%s AND open_status='open'", (ep["provider"], ep["board"]))}
                    con = cls(client, ep["board"], employer_name=org["display_name"], source_id=ats_source(ep["provider"]), **extra_kw)
                    batch = _run(con.fetch(None, 500))
                    for w in batch.warnings:
                        notes.append(w)
                        detail = detail or w[:300]
                    ids: set[str] = set()
                    for item in batch.items:
                        d = con.canonicalize(item)
                        upsert_document(conn, ats_source(ep["provider"]), d, retention.get(ats_source(ep["provider"])) or "excerpt")
                        jid, ch = upsert_job(conn, d.structured, org_id, now)
                        ids.add(d.structured["job_id"])
                        seen += 1
                        if ch != "unchanged":
                            res.structured_changes.append(("job", jid, ch))
                    if batch.complete_listing:
                        # The newer readers (XML, RSS and widget feeds) can answer 200 with an empty list when a feed is switched off or moved. An empty list
                        # that would close every open advert of the board is believed only on the SECOND check in a row (the first leaves a marker).
                        open_now = (q1(conn, "SELECT count(*) AS n FROM job_postings WHERE provider=%s AND board=%s AND open_status='open'", (ep["provider"], ep["board"])) or {"n": 0})["n"]
                        board_empty = (batch.quota_usage or {}).get("postings_seen", len(batch.items)) == 0      # Workday keeps only engineering roles: a busy board with none is not empty
                        if ep["provider"] in ats_more.MORE_CONNECTORS and board_empty and open_now and not (ep.get("last_detail") or "").startswith(EMPTY_LISTING):
                            detail = f"{EMPTY_LISTING}: its {open_now} open adverts are kept until the next check says the same"
                            notes.append(f"{ep['provider']}/{ep['board']}: " + detail)
                        else:
                            for closed in close_unseen_jobs(conn, ep["provider"], ep["board"], ids, now):   # only after an affirmative full listing
                                res.structured_changes.append(("job", closed, "closed"))
                else:
                    raise ValueError("Account endpoint has no supported connector")
            queue.extend(found_boards)
            scanned += 1
        except Exception as exc:   # noqa: BLE001 - one bad endpoint must not stop the others
            status, detail = "failed", f"{type(exc).__name__}: {exc}"[:300]
            if ep["kind"] == "procurement":
                conn.execute("""UPDATE procurement_notices SET last_status_check_at=NULL
                    WHERE buyer_org_id=%s AND official_url=%s AND source_id='corporate-ir-newsrooms'""",
                    (org_id, ep["url"]))
        conn.execute("UPDATE account_endpoints SET last_scanned_at=%s, last_success_at=CASE WHEN %s='ok' THEN %s ELSE last_success_at END, "
                     "last_status=%s, last_detail=%s, docs_seen=%s WHERE id=%s",
                     (now, status, now, status, detail, seen, ep["id"]))
        conn.commit()
    for fam, kinds in (("newsroom", ("feed", "newsroom")), ("jobs", ("job_board", "careers")), ("procurement", ("procurement",))):
        rows = qa(conn, "SELECT url, last_status FROM account_endpoints WHERE organization_id=%s AND kind = ANY(%s)", (org_id, list(kinds)))
        state = "source_not_found" if not rows else ("monitored_healthy" if any(r["last_status"] == "ok" for r in rows) else "monitored_degraded")
        conn.execute("INSERT INTO account_source_coverage(organization_id, source_family, coverage_state, source_ids, last_success_at, note, updated_at) VALUES (%s,%s,%s,%s,%s,%s,now()) "
                     "ON CONFLICT (organization_id, source_family) DO UPDATE SET coverage_state=EXCLUDED.coverage_state, source_ids=EXCLUDED.source_ids, last_success_at=coalesce(EXCLUDED.last_success_at,account_source_coverage.last_success_at), note=EXCLUDED.note, updated_at=now()",
                     (org_id, fam, state, [r["url"] for r in rows][:5], now if state == "monitored_healthy" else None, "; ".join(notes)[:300] or None))
    conn.commit()
    existing = queue
    if not existing:
        outcome = "no_sources"
    elif not scanned:
        outcome = "all_sources_failed"
    elif scanned < len(existing):
        outcome = "partially_checked"
    else:
        outcome = "ok"
    out: dict[str, Any] = {"status": outcome, "endpoints": len(existing), "scanned": scanned, "documents": len(res.new_doc_ids) + len(res.changed_doc_ids), "job_changes": len(res.structured_changes), "notes": notes}
    if res.new_doc_ids or res.changed_doc_ids or res.structured_changes:
        from ..verify.run import org_aware_resolver
        res_resolver = first_party_resolver(str(org_id), org["display_name"], org_aware_resolver(conn, resolver, org_id))
        out["processed"] = process_run(conn, res, extractor=extractor, resolve_org=res_resolver)
    conn.commit()
    return out
