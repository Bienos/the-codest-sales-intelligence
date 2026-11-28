"""More ways to watch competitors (docs/competitor-tracker.md, "How we track them"). Each check follows the rules of competitor_watch.py:
it remembers when it last ran per firm (competitor_watch), its first run only records a baseline, and anything it finds waits on the Review
tab as a candidate until a person confirms it. Counts (Clutch reviews, open roles, AWS tier) are facts and go straight onto the profile.

Per-firm identifiers live in the profile under ``track`` (seeded from research, editable): clutchUid, krs, ukCompany, ats {kind, slug},
nfj (NoFluffJobs company name), rss, sitemap, aws {name, website}, partnerPages [{vendor, url}].

Sources, all public:
* sitemap     the firm's sitemap; new service, industry, case-study and pricing pages           weekly (tier 3 monthly)
* feed        the firm's own blog/news RSS or Atom feed                                          weekly
* gdelt       GDELT news index, several firms per query (it allows one request per 5 seconds)    weekly, tiers 1-2
* clutch      Clutch review widget: rating, review count, latest review quotes                   monthly
* careers     the firm's job system (Workable, Lever, Greenhouse, Recruitee, SmartRecruiters, Ashby, Teamtailor, Personio)
* nfj         NoFluffJobs postings by company                                                    weekly
              -> together with justjoin.it: the mix of roles (AI and data, finance domain, sales in our markets, delivery leads)
* krs         Polish court register: board members and the latest entry                         monthly
* ch          UK Companies House: active officers (API when COMPANIES_HOUSE_API_KEY is set, else the public page)   monthly
* tenders     Polish (BZP), UK Contracts Finder and Find a Tender award notices won by a tracked firm -> awarded_contracts,
              which the existing awards step turns into candidate client wins                     daily
* aws         AWS Partner finder: partner tier, certifications, case studies                     monthly
* partners    vendor partner pages that list firms by name in their HTML                         monthly
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlparse

from . import competitors as ct
from .competitor_watch import Fetch, candidate, due, last_run, remember
from .db import Conn, jb, qa

Post = Callable[[str, dict[str, Any]], str]
UA_NOTE = "Mozilla/5.0 (compatible; CodestResearch/1.0)"
DAYS = {"sitemap": {1: 7, 2: 7, 3: 30}, "feed": {1: 7, 2: 7, 3: 30}, "gdelt": {1: 7, 2: 7}, "clutch": {1: 30, 2: 30, 3: 30},
        "careers": {1: 7, 2: 7, 3: 30}, "nfj": {1: 7, 2: 7, 3: 30}, "krs": {1: 30, 2: 30, 3: 30}, "ch": {1: 30, 2: 30, 3: 30},
        "aws": {1: 30, 2: 30, 3: 30}}
PER_RUN = {"sitemap": 10, "feed": 15, "clutch": 15, "careers": 15, "nfj": 15, "krs": 10, "ch": 10, "aws": 15}
GDELT_GAP_S = 5.5
GDELT_ON = lambda: os.environ.get("RI_GDELT_ENABLED", "").lower() in ("1", "true", "yes")      # noqa: E731  (off: GDELT refuses cloud servers; Exa covers news)
GDELT_PER_QUERY = 6
MAX_SITEMAP_URLS = 25_000
MAX_CHILD_SITEMAPS = 12
FS_WORDS = r"bank|banking|fintech|financ|payment|insur|lending|wealth|capital.?market|trading|dora|psd2|open.?banking|core.?banking|regtech|credit"


def _today(now: datetime) -> str:
    return now.date().isoformat()


def _tag(url: str, now: datetime) -> str:
    """A source address that is unique per check run (moves are de-duplicated by source)."""
    return f"{url.split('#')[0]}#seen-{_today(now)}"


def _h(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()[:12]


def _track(c: dict[str, Any]) -> dict[str, Any]:
    return c.get("track") or {}


def _due_firms(conn: Conn, comps: list[dict[str, Any]], check: str, now: datetime, need: Callable[[dict[str, Any]], Any]) -> list[dict[str, Any]]:
    out = []
    for c in sorted(comps, key=lambda c: (c["tier"], c["key"])):
        days = DAYS[check].get(c["tier"])
        if days and need(c) and due(conn, c["key"], check, days, now):
            out.append(c)
    return out[:PER_RUN.get(check, 10)]


def set_fact(conn: Conn, key: str, field: str, fact: dict[str, Any]) -> None:
    conn.execute("UPDATE competitors SET profile = jsonb_set(profile, %s, %s) WHERE key=%s", ("{" + field + "}", jb(fact), key))


# ------------------------------------------------------------------ sitemap: new pages on their site
PAGE_KINDS: list[tuple[str, str, str]] = [        # (path pattern, label, move type)
    (r"/(news|press|newsroom|media|press-releases?|aktualnosci)/[^/]+", "news or press page", "news"),
    (r"/(pricing|prices|cennik|rates|engagement-models?|how-we-work|packages?)(/|$)", "pricing page", "pricing"),
    (r"/(case-stud|case_stud|cases?|portfolio|our-work|work|projects|realizacje|success-stor|clients?/)", "case study", "client"),
    (r"/(industr|sectors?|branze|verticals?)", "industry page", "vertical"),
    (r"/(services?|solutions?|offer|uslugi|capabilit|expertise|what-we-do|consulting|products?)(/|-)", "service page", "offer"),
    (r"/(partners?|partnerships?|alliances?|technology-partners?)(/|$)", "partner page", "partner"),
    (r"/(about|o-nas|company|leadership|team|management)(/|$)", "company page", "leadership"),
    (r"/(locations?|offices?|contact)/[a-z]", "office page", "market"),
]
SKIP_PATH = r"/(blog|insights|articles?|resources|careers?|jobs?|kariera|praca|events?|webinars?|tag|category|author|page/\d)(/|$)|\.(jpg|png|pdf|svg|webp)$"


def sitemap_urls(fetch: Fetch, start: str) -> list[str]:
    """Every page address in a sitemap or sitemap index (a few levels deep, capped)."""
    seen: list[str] = []
    queue, children = [start], 0
    while queue and len(seen) < MAX_SITEMAP_URLS:
        xml = fetch(queue.pop(0))
        locs = [unescape(x) for x in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)]
        if "<sitemapindex" in xml[:2000]:
            for u in locs:
                if children < MAX_CHILD_SITEMAPS and not re.search(r"(image|video|blog|tag|author|attachment|category)", u, re.I):
                    queue.append(u)
                    children += 1
            continue
        seen += locs
    return seen[:MAX_SITEMAP_URLS]


def find_sitemap(fetch: Fetch, site: str) -> str | None:
    base = f"{urlparse(site).scheme}://{urlparse(site).netloc}"
    try:
        robots = fetch(base + "/robots.txt")
        m = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots)
        if m:
            return m[0]
    except Exception:  # noqa: BLE001
        pass
    for path in ("/sitemap.xml", "/sitemap_index.xml"):
        try:
            if "<loc>" in fetch(base + path)[:5000]:
                return base + path
        except Exception:  # noqa: BLE001
            continue
    return None


def classify_page(url: str) -> tuple[str, str] | None:
    path = urlparse(url).path.lower()
    if re.search(SKIP_PATH, path):
        return None
    for pat, label, kind in PAGE_KINDS:
        if re.search(pat, path):
            return label, kind
    return None


def sitemaps(conn: Conn, comps: list[dict[str, Any]], fetch: Fetch, now: datetime) -> dict[str, int]:
    done = made = failed = 0
    for c in _due_firms(conn, comps, "sitemap", now, lambda c: (c.get("url") or "").startswith("https://")):
        done += 1
        try:
            sm = _track(c).get("sitemap") or find_sitemap(fetch, c["url"])
            if not sm:
                raise LookupError("no sitemap")
            urls = sitemap_urls(fetch, sm)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            remember(conn, c["key"], "sitemap", (last_run(conn, c["key"], "sitemap") or {}).get("last_value"), now, error=type(exc).__name__)
            continue
        prev = (last_run(conn, c["key"], "sitemap") or {}).get("last_value") or {}
        known = set(prev.get("hashes") or [])
        hashes = {_h(u.split("?")[0].rstrip("/")): u for u in urls}
        if known:                                       # the first run is the baseline: nothing is "new" yet
            fresh = [u for h, u in hashes.items() if h not in known]
            groups: dict[str, tuple[str, list[str]]] = {}
            for u in fresh:
                k = classify_page(u)
                if k:
                    label, kind = k
                    if kind == "offer" and re.search(FS_WORDS, u, re.I):
                        label, kind = "finance service page", "vertical"
                    groups.setdefault(kind, (label, []))[1].append(u)
            from .competitor_watch import guess_type
            for kind, (label, pages) in list(groups.items()):
                if kind == "news":                      # press pages: one candidate each, typed from the words in the address
                    for p in pages[:8]:
                        words = urlparse(p).path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
                        made += candidate(conn, c["key"], day=_today(now), kind=guess_type(words), headline=f"News on their site: {words}"[:300], source=p,
                                          grade="official", impact=1, actor="collector:sitemap")
                    continue
                fs = any(re.search(FS_WORDS, p, re.I) for p in pages)
                names = [urlparse(p).path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ") for p in pages[:5]]
                head = f"New {label}{'s' if len(pages) > 1 else ''} on their site: " + "; ".join(n for n in names if n)[:220]
                impact = 2 if (fs or kind in ("pricing",)) and c["tier"] == 1 else 1
                made += candidate(conn, c["key"], day=_today(now), kind=kind, headline=head, source=pages[0], grade="official", impact=impact,
                                  actor="collector:sitemap", detail="\n".join(pages[:12]), payload={"pages": pages[:50]})
        remember(conn, c["key"], "sitemap", {"sitemap": sm, "pages": len(urls), "hashes": sorted(hashes)}, now)
    return {"sitemaps_checked": done, "sitemap_candidates": made, "sitemaps_failed": failed}


# ------------------------------------------------------------------ own feed: blog and news posts
def feed_items(xml: str) -> list[dict[str, str]]:
    items = []
    for block in re.findall(r"<(?:item|entry)\b.*?</(?:item|entry)>", xml, re.S | re.I)[:60]:
        def tag(name: str) -> str:
            m = re.search(rf"<{name}\b[^>]*>(.*?)</{name}>", block, re.S | re.I)
            v = m.group(1) if m else ""
            v = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", v, flags=re.S)
            return unescape(re.sub(r"<[^>]+>", " ", v)).strip()
        link = tag("link")
        if not link:
            m = re.search(r"<link\b[^>]*href=\"([^\"]+)\"", block, re.I)
            link = m.group(1) if m else ""
        title = re.sub(r"\s+", " ", tag("title"))
        if title and link.startswith("http"):
            items.append({"title": title, "link": link.strip(), "date": tag("pubDate") or tag("published") or tag("updated"), "summary": tag("description")[:400]})
    return items


FEED_PATHS = ("/feed/", "/blog/feed/", "/news/feed/", "/insights/feed/", "/en/feed/", "/rss.xml", "/blog/rss.xml", "/feed.xml", "/blog/rss/", "/rss/",
              "/blog/index.xml", "/index.xml", "/atom.xml")


def _feed_link(html: str, base: str) -> str | None:
    m = re.search(r"<link[^>]+type=[\"']application/(?:rss|atom)\+xml[\"'][^>]*href=[\"']([^\"']+)", html, re.I) or \
        re.search(r"<link[^>]+href=[\"']([^\"']+)[\"'][^>]*type=[\"']application/(?:rss|atom)\+xml", html, re.I)
    return urljoin(base, unescape(m.group(1))) if m else None


def find_feed(fetch: Fetch, site: str) -> str | None:
    """The firm's own feed: announced on the homepage or the blog page, else a common address that answers with RSS or Atom."""
    base = f"{urlparse(site).scheme}://{urlparse(site).netloc}"
    for page in (site, base + "/blog/", base + "/news/", base + "/insights/"):
        try:
            link = _feed_link(fetch(page), page)
            if link and "comments" not in link:
                return link
        except Exception:  # noqa: BLE001
            continue
    for path in FEED_PATHS:
        try:
            if re.search(r"<(rss|feed)\b", fetch(base + path)[:3000]):
                return base + path
        except Exception:  # noqa: BLE001
            continue
    return None


def _date(s: str, now: datetime) -> str:
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except Exception:  # noqa: BLE001
        m = re.match(r"(\d{4}-\d{2}-\d{2})", s or "")
        return m.group(1) if m else _today(now)


def feeds(conn: Conn, comps: list[dict[str, Any]], fetch: Fetch, now: datetime) -> dict[str, int]:
    from .competitor_watch import guess_type
    done = made = failed = 0
    def wanted(c: dict[str, Any]) -> bool:              # a site found to have no feed is looked at again monthly, not weekly
        r = last_run(conn, c["key"], "feed")
        none = r and isinstance(r.get("last_value"), dict) and r["last_value"].get("feed") is None and not _track(c).get("rss")
        return (c.get("url") or "").startswith("https://") and not (none and r["last_run_at"] and now - r["last_run_at"] < timedelta(days=30))
    for c in _due_firms(conn, comps, "feed", now, wanted):
        done += 1
        prev = (last_run(conn, c["key"], "feed") or {}).get("last_value") or {}
        try:
            url = _track(c).get("rss") or prev.get("feed") or find_feed(fetch, c["url"])
            if not url:                                 # no feed on their site: new press pages come through the sitemap check instead
                remember(conn, c["key"], "feed", {"feed": None, "seen": []}, now)
                continue
            items = feed_items(fetch(url))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            remember(conn, c["key"], "feed", prev or None, now, error=type(exc).__name__)
            continue
        known = set(prev.get("seen") or [])
        if known:
            for it in items:
                if _h(it["link"]) in known:
                    continue
                text = f"{it['title']} {it['summary']}"
                kind = guess_type(text)
                made += candidate(conn, c["key"], day=_date(it["date"], now), kind=kind, headline=it["title"], source=it["link"], grade="official",
                                  impact=1, actor="collector:feed", detail=it["summary"] or None)
        remember(conn, c["key"], "feed", {"feed": url, "seen": sorted(known | {_h(i["link"]) for i in items})[-400:]}, now)
    return {"feeds_checked": done, "feed_candidates": made, "feeds_failed": failed}


# ------------------------------------------------------------------ GDELT news index
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist&format=json&maxrecords=75&timespan={days}d&sort=datedesc"


def news_name(c: dict[str, Any]) -> str | None:
    """The name to search for. Short or common names (Sii, N-iX) need a longer alias, or they match unrelated news."""
    names = [c["name"], *(c.get("aliases") or [])]
    good = [n for n in names if len(re.sub(r"[^A-Za-z0-9]", "", n)) >= 6]
    return (_track(c).get("newsName") or (good[0] if good else None))


def gdelt(conn: Conn, comps: list[dict[str, Any]], fetch: Fetch, now: datetime, sleep: Callable[[float], None] = time.sleep) -> dict[str, int]:
    from .competitor_watch import guess_type
    from .research.vendor_intel import on_page
    firms = [c for c in sorted(comps, key=lambda c: (c["tier"], c["key"])) if DAYS["gdelt"].get(c["tier"]) and news_name(c)
             and due(conn, c["key"], "gdelt", DAYS["gdelt"][c["tier"]], now)][:GDELT_PER_QUERY * 4]
    done = made = failed = 0
    for i in range(0, len(firms), GDELT_PER_QUERY):
        group = firms[i:i + GDELT_PER_QUERY]
        q = "(" + " OR ".join(f'"{news_name(c)}"' for c in group) + ")"
        if done:
            sleep(GDELT_GAP_S)
        done += 1
        try:
            raw = fetch(GDELT.format(q=quote(q), days=10))
            arts = (json.loads(raw) if raw.strip().startswith("{") else {}).get("articles") or []
        except Exception as exc:  # noqa: BLE001  (refused or rate-limited: stop for today, the rest wait for tomorrow)
            failed += 1
            for c in group:
                remember(conn, c["key"], "gdelt", None, now, error=type(exc).__name__)
            break
        for c in group:
            variants = sorted(ct.name_keys(c)) + [c["name"], news_name(c) or ""]
            for a in arts:
                title, url = a.get("title") or "", a.get("url") or ""
                if not url.startswith("https://") or not on_page([v for v in variants if v], title):
                    continue
                d = re.match(r"(\d{4})(\d{2})(\d{2})", a.get("seendate") or "")
                made += candidate(conn, c["key"], day="-".join(d.groups()) if d else _today(now), kind=guess_type(title), headline=title, source=url,
                                  grade="reported", impact=1, actor="collector:gdelt", detail=f"{a.get('domain') or ''} · {a.get('language') or ''}".strip(" ·"))
            remember(conn, c["key"], "gdelt", {"articles": len(arts)}, now)
    return {"gdelt_queries": done, "gdelt_candidates": made, "gdelt_failed": failed}


# ------------------------------------------------------------------ Clutch review widget
CLUTCH_WIDGET = "https://widget.clutch.co/widgets/get/{n}?ref_domain=thecodest.co&uid={uid}&ref_path=/"


def parse_clutch(html: str) -> dict[str, Any]:
    t = re.sub(r"<style.*?</style>|<script.*?</script>", " ", html, flags=re.S | re.I)
    t = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", t)))
    m = re.search(r"(\d\.\d)\s+\d\.\d+\s+(\d[\d,]*)\s+reviews?", t) or re.search(r"(\d\.\d)\D{0,40}?(\d[\d,]*)\s+reviews?", t)
    quotes = re.findall(r"“([^”]{20,240})”|\"([^\"]{20,240})\"", t)
    if not m and "Review Widget" in t:                     # a profile without reviews yet: the widget shows only the name
        n = re.search(r"(\d[\d,]*)\s+reviews?", t)
        return {"rating": None, "reviews": int(n.group(1).replace(",", "")) if n else 0, "quotes": []}
    return {"rating": float(m.group(1)) if m else None, "reviews": int(m.group(2).replace(",", "")) if m else None,
            "quotes": [a or b for a, b in quotes][:3]}


def clutch(conn: Conn, comps: list[dict[str, Any]], fetch: Fetch, now: datetime) -> dict[str, int]:
    done = made = failed = 0
    for c in _due_firms(conn, comps, "clutch", now, lambda c: _track(c).get("clutchUid")):
        done += 1
        uid = _track(c)["clutchUid"]
        try:
            got = parse_clutch(fetch(CLUTCH_WIDGET.format(n=3, uid=uid)))
            if got["reviews"] is None:
                raise ValueError("widget had no rating")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            remember(conn, c["key"], "clutch", None, now, error=type(exc).__name__)
            continue
        old = c.get("clutch") or {}
        profile_url = old.get("source") if str(old.get("source") or "").startswith("https://clutch.co/") else f"https://clutch.co/profile/{c['key']}"
        set_fact(conn, c["key"], "clutch", {**old, "rating": got["rating"], "reviews": got["reviews"], "source": profile_url, "asOf": _today(now), "grade": "reported"})
        before = old.get("reviews") if isinstance(old.get("reviews"), int) else None
        if before is not None and got["reviews"] - before >= 3:
            made += candidate(conn, c["key"], day=_today(now), kind="client", headline=f"{got['reviews'] - before} new Clutch reviews ({before} to {got['reviews']}, rating {got['rating']})",
                              source=_tag(profile_url, now), grade="reported", impact=1, actor="collector:clutch",
                              detail=" | ".join(got["quotes"]) or None)
        if isinstance(old.get("rating"), (int, float)) and got["rating"] is not None and old["rating"] - got["rating"] >= 0.2:
            made += candidate(conn, c["key"], day=_today(now), kind="client", headline=f"Clutch rating falls from {old['rating']} to {got['rating']}",
                              source=_tag(profile_url + "?rating", now), grade="reported", impact=1, actor="collector:clutch")
        remember(conn, c["key"], "clutch", got, now)
    return {"clutch_checked": done, "clutch_candidates": made, "clutch_failed": failed}


# ------------------------------------------------------------------ jobs: their own job system and NoFluffJobs -> the mix of roles
ROLE_KINDS: list[tuple[str, str]] = [
    ("sales", r"\b(sales|business development|account (executive|manager|director)|bdm|new business|partnerships? manager|growth manager|presales|pre-sales|client partner)\b"),
    ("ai", r"\b(ai|ml|machine learning|llm|genai|gen ai|data scien|mlops|nlp|computer vision|prompt|agentic)\b"),
    ("fs", r"\b(bank|banking|fintech|payments?|insurance|insurtech|trading|capital markets|core banking|lending|wealth)\b"),
    ("data", r"\b(data engineer|data architect|analytics engineer|bi developer|databricks|snowflake|data platform)\b"),
    ("lead", r"\b(delivery (manager|lead|director)|project manager|program(me)? manager|engagement manager|scrum master|product owner)\b"),
]
NOT_SALES = r"\b(engineer|developer|analyst|designer|tester|qa|architect|administrator|recruiter)\b"      # 'Sales Operations Analyst' does not sell
MARKETS = {"UK": r"\b(uk|united kingdom|london|manchester|edinburgh|britain|england)\b", "DACH": r"\b(dach|germany|deutschland|berlin|munich|münchen|hamburg|frankfurt|austria|vienna|wien|switzerland|zurich|zürich)\b",
           "Nordics": r"\b(nordics?|sweden|stockholm|denmark|copenhagen|norway|oslo|finland|helsinki)\b", "US": r"\b(usa|us|united states|new york|boston|chicago|san francisco|austin)\b"}


def classify_roles(jobs: list[dict[str, str]]) -> dict[str, Any]:
    mix: dict[str, Any] = {k: 0 for k, _ in ROLE_KINDS}
    sales_markets: dict[str, int] = {}
    for j in jobs:
        t = f"{j.get('title') or ''} ".lower()
        loc = f"{j.get('location') or ''} {j.get('title') or ''}".lower()
        seller = bool(re.search(ROLE_KINDS[0][1], t)) and not re.search(NOT_SALES, t)
        for k, pat in ROLE_KINDS:
            if re.search(pat, t) and (k != "sales" or seller):
                mix[k] += 1
        if seller:
            for m, pat in MARKETS.items():
                if re.search(pat, loc):
                    sales_markets[m] = sales_markets.get(m, 0) + 1
    return {"total": len(jobs), **mix, "salesMarkets": sales_markets}


def ats_jobs(kind: str, slug: str, fetch: Fetch) -> tuple[list[dict[str, str]], str]:
    """(jobs [{title, location}], public address of the list) from the firm's job system."""
    s = quote(slug, safe="")
    if kind == "workable":
        url = f"https://apply.workable.com/api/v1/widget/accounts/{s}"
        d = json.loads(fetch(url))
        return [{"title": j.get("title", ""), "location": " ".join(str(j.get(x) or "") for x in ("city", "country"))} for j in d.get("jobs") or []], f"https://apply.workable.com/{s}/"
    if kind == "lever":
        url = f"https://api.lever.co/v0/postings/{s}?mode=json"
        return [{"title": j.get("text", ""), "location": (j.get("categories") or {}).get("location", "")} for j in json.loads(fetch(url))], f"https://jobs.lever.co/{s}"
    if kind == "greenhouse":
        url = f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs"
        return [{"title": j.get("title", ""), "location": (j.get("location") or {}).get("name", "")} for j in json.loads(fetch(url)).get("jobs") or []], f"https://boards.greenhouse.io/{s}"
    if kind == "recruitee":
        url = f"https://{s}.recruitee.com/api/offers/"
        return [{"title": j.get("title", ""), "location": j.get("location", "")} for j in json.loads(fetch(url)).get("offers") or []], f"https://{s}.recruitee.com/"
    if kind == "smartrecruiters":
        out, off = [], 0
        while off < 1000:
            d = json.loads(fetch(f"https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=100&offset={off}"))
            page = d.get("content") or []
            out += [{"title": j.get("name", ""), "location": " ".join(str((j.get("location") or {}).get(x) or "") for x in ("city", "country"))} for j in page]
            if len(page) < 100:
                break
            off += 100
        return out, f"https://jobs.smartrecruiters.com/{s}"
    if kind == "ashby":
        d = json.loads(fetch(f"https://api.ashbyhq.com/posting-api/job-board/{s}"))
        return [{"title": j.get("title", ""), "location": j.get("location", "")} for j in d.get("jobs") or []], f"https://jobs.ashbyhq.com/{s}"
    if kind == "bamboohr":
        d = json.loads(_get(fetch, f"https://{s}.bamboohr.com/careers/list", {"Accept": "application/json"}))
        return [{"title": j.get("jobOpeningName", ""), "location": " ".join(str((j.get("location") or {}).get(x) or "") for x in ("city", "state"))}
                for j in d.get("result") or []], f"https://{s}.bamboohr.com/careers"
    if kind == "breezy":
        return [{"title": j.get("name", ""), "location": (j.get("location") or {}).get("name", "")} for j in json.loads(fetch(f"https://{s}.breezy.hr/json"))], f"https://{s}.breezy.hr/"
    if kind == "traffit":
        d = json.loads(_get(fetch, f"https://{s}.traffit.com/public/job_posts/published", {"Accept": "application/json", "X-Request-Page-Size": "100"}))
        out = []
        for j in d:
            adv = j.get("advert") or {}
            geo = next((v.get("value") for v in adv.get("values") or [] if v.get("field_id") == "geolocation"), "") or ""
            out.append({"title": adv.get("name", ""), "location": geo if isinstance(geo, str) else json.dumps(geo)})
        return out, f"https://{s}.traffit.com/career/"
    if kind in ("teamtailor", "personio"):
        url = slug if slug.startswith("https://") else (f"https://{s}.teamtailor.com/jobs.rss" if kind == "teamtailor" else f"https://{s}.jobs.personio.de/xml")
        xml = fetch(url)
        titles = re.findall(r"<(?:title|name)>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</(?:title|name)>", xml, re.S)[1 if kind == "teamtailor" else 0:]
        return [{"title": unescape(t), "location": ""} for t in titles], url
    raise ValueError(f"unknown job system {kind}")


def _get(fetch: Fetch, url: str, headers: dict[str, str]) -> str:
    """A GET with extra headers when the fetcher takes them (the live one does; simple test fakes do not)."""
    try:
        return fetch(url, headers=headers)            # type: ignore[call-arg]
    except TypeError:
        return fetch(url)


NFJ = "https://nofluffjobs.com/api/search/posting?page=1&salaryCurrency=PLN&salaryPeriod=month&region=pl"


def nfj_jobs(name: str, post: Post) -> list[dict[str, str]]:
    d = json.loads(post(NFJ, {"criteriaSearch": {"company": [name]}, "page": 1}))
    want = ct.name_keys({"name": name})
    out, seen = [], set()
    for p in d.get("postings") or []:
        ref = p.get("reference") or p.get("id") or p.get("url") or p.get("title")
        if ref in seen:                                   # one posting per city: count each job once
            continue
        seen.add(ref)
        comp = str(p.get("name") or (p.get("company") or {}).get("name") or "")
        if comp and not (ct.name_keys({"name": comp}) & want) and name.lower() not in comp.lower():
            continue
        locs = " ".join(str(x.get("city") or "") for x in ((p.get("location") or {}).get("places") or []))
        out.append({"title": p.get("title", ""), "location": locs})
    return out


def careers(conn: Conn, comps: list[dict[str, Any]], fetch: Fetch, post: Post | None, now: datetime) -> dict[str, int]:
    done = made = failed = 0
    for c in _due_firms(conn, comps, "careers", now, lambda c: (_track(c).get("ats") or {}).get("kind") not in (None, "other") or _track(c).get("nfj")):
        done += 1
        t = _track(c)
        jobs: list[dict[str, str]] = []
        sources: list[str] = []
        errors = []
        ats = t.get("ats") or {}
        if ats.get("kind") not in (None, "other"):
            try:
                got, url = ats_jobs(ats["kind"], ats["slug"], fetch)
                jobs += got
                sources.append(url)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{ats['kind']}: {type(exc).__name__}")
        if t.get("nfj") and post is not None:
            try:
                got = nfj_jobs(t["nfj"], post)
                jobs += got
                sources.append(f"https://nofluffjobs.com/pl/?criteria=company%3D{quote(t['nfj'])}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"nofluffjobs: {type(exc).__name__}")
        if not sources:
            failed += 1
            remember(conn, c["key"], "careers", None, now, error="; ".join(errors)[:200] or "no source")
            continue
        mix = classify_roles(jobs)
        prev = (last_run(conn, c["key"], "careers") or {}).get("last_value") or {}
        set_fact(conn, c["key"], "roleMix", {**mix, "value": f"{mix['total']} open roles: {mix['ai']} AI or data science, {mix['sales']} sales, {mix['fs']} finance domain",
                                             "source": sources[0], "sources": sources, "asOf": _today(now), "grade": "official" if ats.get("kind") else "reported"})
        if prev:                                         # the first run is the baseline
            for m, n in mix["salesMarkets"].items():
                if n > (prev.get("salesMarkets") or {}).get(m, 0) and m in ("UK", "DACH", "Nordics"):
                    made += candidate(conn, c["key"], day=_today(now), kind="market", headline=f"Hiring sales in {m}: {n} open sales role{'s' if n > 1 else ''}",
                                      source=_tag(sources[0], now) + f"-sales-{m}", grade="official", impact=2 if c["tier"] == 1 else 1, actor="collector:careers",
                                      detail="; ".join(j["title"] for j in jobs if re.search(ROLE_KINDS[0][1], j["title"].lower()) and not re.search(NOT_SALES, j["title"].lower()))[:500])
            if mix["ai"] - (prev.get("ai") or 0) >= 3:
                made += candidate(conn, c["key"], day=_today(now), kind="hiring", headline=f"More AI and data science roles: {prev.get('ai') or 0} to {mix['ai']}",
                                  source=_tag(sources[0], now) + "-ai", grade="official", impact=1, actor="collector:careers")
            if mix["fs"] - (prev.get("fs") or 0) >= 3:
                made += candidate(conn, c["key"], day=_today(now), kind="vertical", headline=f"More finance-domain roles (banking, payments, insurance): {prev.get('fs') or 0} to {mix['fs']}",
                                  source=_tag(sources[0], now) + "-fs", grade="official", impact=2 if c["tier"] == 1 else 1, actor="collector:careers")
        remember(conn, c["key"], "careers", mix, now, error="; ".join(errors)[:200] or None)
    return {"careers_checked": done, "careers_candidates": made, "careers_failed": failed}


# ------------------------------------------------------------------ registers: KRS (Poland) and Companies House (UK)
KRS_API = "https://api-krs.ms.gov.pl/api/krs/OdpisAktualny/{n}?rejestr=P&format=json"


def _walk(x: Any, key: str) -> list[Any]:
    out = []
    if isinstance(x, dict):
        for k, v in x.items():
            if k == key:
                out.append(v)
            out += _walk(v, key)
    elif isinstance(x, list):
        for v in x:
            out += _walk(v, key)
    return out


def parse_krs(d: dict[str, Any]) -> dict[str, Any]:
    head = (d.get("odpis") or {}).get("naglowekA") or {}
    board = []
    for sklad in _walk(d, "sklad"):
        for p in sklad if isinstance(sklad, list) else []:
            sur = (p.get("nazwisko") or {}).get("nazwiskoICzlon") or ""
            first = (p.get("imiona") or {}).get("imie") or ""
            if sur:
                board.append(f"{first} {sur}".strip().title() + (f" ({p['funkcjaWOrganie'].lower()})" if p.get("funkcjaWOrganie") else ""))
    name = next(iter(_walk(d, "nazwa")), "")
    last = str(head.get("dataOstatniegoWpisu") or "")
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", last)
    return {"name": name if isinstance(name, str) else "", "lastEntry": f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else (last[:10] or None), "entryNo": head.get("numerOstatniegoWpisu"),
            "board": sorted(set(board))}


def registers(conn: Conn, comps: list[dict[str, Any]], fetch: Fetch, now: datetime) -> dict[str, int]:
    done = made = failed = 0
    for c in _due_firms(conn, comps, "krs", now, lambda c: _track(c).get("krs")):
        done += 1
        n = str(_track(c)["krs"][0]).zfill(10)
        try:
            raw = fetch(KRS_API.format(n=n))
            if "Przerwa techniczna" in raw[:2000]:
                raise ConnectionError("register under maintenance")
            got = parse_krs(json.loads(raw))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            remember(conn, c["key"], "krs", (last_run(conn, c["key"], "krs") or {}).get("last_value"), now, error=str(exc)[:80] if isinstance(exc, ConnectionError) else type(exc).__name__)
            if isinstance(exc, ConnectionError):
                break                                      # the whole register is down: try again tomorrow
            continue
        prev = (last_run(conn, c["key"], "krs") or {}).get("last_value") or {}
        src = f"https://wyszukiwarka-krs.ms.gov.pl/details?krs={n}"
        if prev.get("board") is not None and got["board"] != prev["board"]:
            gone, new = sorted(set(prev["board"]) - set(got["board"])), sorted(set(got["board"]) - set(prev["board"]))
            parts = ([f"joins: {', '.join(new)}"] if new else []) + ([f"leaves: {', '.join(gone)}"] if gone else [])
            made += candidate(conn, c["key"], day=got["lastEntry"] or _today(now), kind="leadership", headline=f"Board change in the Polish register: {'; '.join(parts)}"[:300],
                              source=_tag(src, now), grade="official", impact=2 if c["tier"] == 1 else 1, actor="collector:krs")
        remember(conn, c["key"], "krs", got, now)
    ch_done, ch_made, ch_failed = companies_house(conn, comps, fetch, now)
    return {"krs_checked": done, "krs_candidates": made, "krs_failed": failed, "ch_checked": ch_done, "ch_candidates": ch_made, "ch_failed": ch_failed}


CH_PAGE = "https://find-and-update.company-information.service.gov.uk/company/{n}/officers"
CH_API = "https://api.company-information.service.gov.uk/company/{n}/officers?items_per_page=100"


def parse_ch_officers(html: str) -> list[str]:
    """Active officers from the public officers page (resigned ones carry a 'Resigned on' line)."""
    out = []
    for block in re.split(r'<div class="appointment-\d+">', html)[1:]:
        name = re.search(r'id="officer-name-\d+"[^>]*>\s*(?:<a[^>]*>)?\s*([^<]+)', block)
        if name and "Resigned on" not in block:
            role = re.search(r'id="officer-role-\d+"[^>]*>\s*([^<]+)', block)
            out.append(f"{unescape(name.group(1)).strip().title()}" + (f" ({unescape(role.group(1)).strip().lower()})" if role else ""))
    return sorted(set(out))


def companies_house(conn: Conn, comps: list[dict[str, Any]], fetch: Fetch, now: datetime) -> tuple[int, int, int]:
    done = made = failed = 0
    key = os.environ.get("COMPANIES_HOUSE_API_KEY")
    for c in _due_firms(conn, comps, "ch", now, lambda c: _track(c).get("ukCompany")):
        done += 1
        n = str(_track(c)["ukCompany"]).upper()
        try:
            if key:
                import base64
                import httpx
                r = httpx.get(CH_API.format(n=n), headers={"Authorization": "Basic " + base64.b64encode(f"{key}:".encode()).decode()}, timeout=20)
                r.raise_for_status()
                officers = sorted({f"{o['name'].title()} ({o.get('officer_role', '').replace('-', ' ')})" for o in r.json().get("items") or [] if not o.get("resigned_on")})
            else:
                officers = parse_ch_officers(fetch(CH_PAGE.format(n=n)))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            remember(conn, c["key"], "ch", (last_run(conn, c["key"], "ch") or {}).get("last_value"), now, error=type(exc).__name__)
            continue
        prev = (last_run(conn, c["key"], "ch") or {}).get("last_value") or {}
        if prev.get("officers") is not None and officers != prev["officers"]:
            gone, new = sorted(set(prev["officers"]) - set(officers)), sorted(set(officers) - set(prev["officers"]))
            parts = ([f"appointed: {', '.join(new)}"] if new else []) + ([f"resigned: {', '.join(gone)}"] if gone else [])
            made += candidate(conn, c["key"], day=_today(now), kind="leadership", headline=f"UK company officers change: {'; '.join(parts)}"[:300],
                              source=_tag(CH_PAGE.format(n=n), now), grade="official", impact=2 if c["tier"] == 1 else 1, actor="collector:companies_house")
        remember(conn, c["key"], "ch", {"officers": officers}, now)
    return done, made, failed


# ------------------------------------------------------------------ public tenders won: Poland (BZP) and the UK
BZP = "https://ezamowienia.gov.pl/mo-board/api/v1/notice?NoticeType=TenderResultNotice&PublicationDateFrom={d0}&PublicationDateTo={d1}&PageSize=500&CpvCode={cpv}"
CF = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search?stages=award&publishedFrom={d0}&publishedTo={d1}&limit=100"
FTS = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages?stages=award&updatedFrom={d0}T00:00:00&updatedTo={d1}T00:00:00&limit=100"
IT_CPV = ("72", "48")


def _store_award(conn: Conn, notice_id: str, *, buyer: str, country: str, title: str, cpv: list[str], winners: list[str], day: str, url: str) -> bool:
    r = conn.execute("INSERT INTO awarded_contracts(notice_id, buyer_name, buyer_country, title, cpv, winners, awarded_on, end_basis, relevant, official_url) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s,'assumed',true,%s) ON CONFLICT (notice_id) DO NOTHING RETURNING notice_id",
                     (notice_id, buyer, country, title[:500], cpv, winners, day, url)).fetchone()
    return bool(r)


def tenders(conn: Conn, comps: list[dict[str, Any]], fetch: Fetch, now: datetime, sleep: Callable[[float], None] = time.sleep) -> dict[str, int]:
    """Award notices since the last run, kept only when a tracked firm won. The awards step then raises candidate client wins."""
    prev = last_run(conn, "_all", "tenders")
    start = (prev["last_run_at"].date() if prev and prev["last_run_at"] else (now - timedelta(days=3)).date())
    days = [(start + timedelta(days=i)) for i in range(max(1, min(14, (now.date() - start).days + 1)))]
    match = ct._matcher(comps)
    out = {"bzp_notices": 0, "uk_notices": 0, "awards_stored": 0}
    errors = []
    for d in days:
        d0, d1 = d.isoformat(), (d + timedelta(days=1)).isoformat()
        for cpv in IT_CPV:
            try:
                items = json.loads(fetch(BZP.format(d0=d0, d1=d1, cpv=cpv)))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"bzp {d0}: {type(exc).__name__}")
                continue
            for n in items if isinstance(items, list) else []:
                out["bzp_notices"] += 1
                names = [x.get("contractorName") or "" for x in n.get("contractors") or []]
                hits = {k for k in (_fuzzy(match, nm) for nm in names) if k}
                if hits:
                    url = f"https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/id/{n.get('objectId')}"
                    out["awards_stored"] += _store_award(conn, f"BZP:{n.get('bzpNumber')}", buyer=n.get("organizationName") or "", country="PL",
                                                         title=n.get("orderObject") or "", cpv=[str(n.get("cpvCode") or "")[:10]], winners=names,
                                                         day=str(n.get("publicationDate") or d0)[:10], url=url)
    for base, label in ((CF, "cf"), (FTS, "fts")):
        url: str | None = base.format(d0=days[0].isoformat(), d1=(days[-1] + timedelta(days=1)).isoformat())
        pages = 0
        while url and pages < 30:
            pages += 1
            try:
                d = json.loads(fetch(url))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{label}: {type(exc).__name__}")
                break
            for r in d.get("releases") or [x for p in d.get("releasePackages") or [] for x in p.get("releases") or []]:
                out["uk_notices"] += 1
                cpv = str(((r.get("tender") or {}).get("classification") or {}).get("id") or "")
                if not cpv.startswith(IT_CPV):
                    continue
                names = [s.get("name") or "" for a in r.get("awards") or [] for s in a.get("suppliers") or []]
                hits = {k for k in (_fuzzy(match, nm) for nm in names) if k}
                if hits:
                    link = f"https://www.contractsfinder.service.gov.uk/Notice/{r.get('id', '').rsplit('-', 1)[0]}" if label == "cf" else \
                        f"https://www.find-tender.service.gov.uk/Notice/{r.get('id', '')}"
                    out["awards_stored"] += _store_award(conn, f"{label.upper()}:{r.get('id')}", buyer=(r.get("buyer") or {}).get("name") or "", country="GB",
                                                         title=(r.get("tender") or {}).get("title") or "", cpv=[cpv], winners=names,
                                                         day=str(r.get("date") or "")[:10] or _today(now), url=link)
            url = (d.get("links") or {}).get("next")
            if url and label == "fts":
                sleep(2.0)                                   # Find a Tender rate-limits hard
    remember(conn, "_all", "tenders", out, now, error="; ".join(errors)[:300] or None)
    return out


def _fuzzy(match: dict[str, str], name: str) -> str | None:
    return ct.match_name(match, name)


# ------------------------------------------------------------------ partner programmes: AWS and vendor pages
AWS = "https://api.finder.partners.aws.a2z.com/search?locale=en&highlight=on&sourceFilter=searchPage&size=25&from=0&kw={q}"     # 'kw', not 'query'


def _host(u: str) -> str:
    return (urlparse(u if "//" in u else "https://" + u).hostname or "").lower().removeprefix("www.")


def aws(conn: Conn, comps: list[dict[str, Any]], fetch: Fetch, now: datetime, sleep: Callable[[float], None] = time.sleep) -> dict[str, int]:
    """AWS Partner finder, searched by name and matched to the firm by website domain: tier, certifications, competencies, case studies."""
    done = made = failed = 0
    for c in _due_firms(conn, comps, "aws", now, lambda c: _host(c.get("url") or "")):
        if done:
            sleep(0.5)
        done += 1
        hosts = {_host(c.get("url") or ""), _host((_track(c).get("aws") or {}).get("website") or "")} - {""}
        try:
            res = json.loads(fetch(AWS.format(q=quote(c["name"]))))["message"].get("results") or []
        except Exception as exc:  # noqa: BLE001
            failed += 1
            remember(conn, c["key"], "aws", None, now, error=type(exc).__name__)
            continue
        hit = next((r["_source"] for r in res if _host(r.get("_source", {}).get("website") or "") in hosts), None)
        if not hit:
            remember(conn, c["key"], "aws", {"listed": False}, now)
            continue
        got = {"listed": True, "tier": hit.get("current_program_status"), "certifications": hit.get("aws_certifications_count"),
               "competencies": hit.get("competencies_count"), "caseStudies": hit.get("references_casestudy_count")}
        src = f"https://partners.amazonaws.com/search/partners?keyword={quote(hit.get('name') or c['name'])}"
        set_fact(conn, c["key"], "aws", {**got, "value": f"AWS {got['tier']} partner, {got['certifications']} certifications, {got['competencies']} competencies",
                                         "source": src, "asOf": _today(now), "grade": "official"})
        prev = (last_run(conn, c["key"], "aws") or {}).get("last_value") or {}
        if prev.get("listed") and prev.get("tier") != got["tier"]:
            made += candidate(conn, c["key"], day=_today(now), kind="partner", headline=f"AWS partner tier changes: {prev.get('tier')} to {got['tier']}",
                              source=_tag(src, now), grade="official", impact=1, actor="collector:aws")
        if prev.get("listed") and (got["competencies"] or 0) > (prev.get("competencies") or 0):
            made += candidate(conn, c["key"], day=_today(now), kind="partner", headline=f"New AWS competency: {prev.get('competencies') or 0} to {got['competencies']}",
                              source=_tag(src, now) + "-comp", grade="official", impact=1, actor="collector:aws")
        remember(conn, c["key"], "aws", got, now)
    return {"aws_checked": done, "aws_candidates": made, "aws_failed": failed}


# Vendor pages that list their partners by name in the HTML (checked to work without a browser). More can be added per firm in track.partnerPages.
PARTNER_PAGES = [("Thought Machine", "https://www.thoughtmachine.net/partners"), ("10x Banking", "https://www.10xbanking.com/partners")]


def partner_pages(conn: Conn, comps: list[dict[str, Any]], fetch: Fetch, now: datetime) -> dict[str, int]:
    if not due(conn, "_all", "partners", 30, now):
        return {}
    from .research.vendor_intel import on_page
    pages = dict(PARTNER_PAGES)
    for c in comps:
        for p in _track(c).get("partnerPages") or []:
            if str(p.get("url") or "").startswith("https://"):
                pages.setdefault(p.get("vendor") or urlparse(p["url"]).hostname, p["url"])
    prev = (last_run(conn, "_all", "partners") or {}).get("last_value") or {}
    seen: dict[str, list[str]] = {}
    made = failed = 0
    for vendor, url in pages.items():
        try:
            text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fetch(url))))
        except Exception:  # noqa: BLE001
            failed += 1
            seen[vendor] = prev.get(vendor) or []
            continue
        seen[vendor] = sorted(c["key"] for c in comps if on_page(sorted(ct.name_keys(c)) + [c["name"]], text))
        if vendor in prev:
            for k in sorted(set(seen[vendor]) - set(prev[vendor])):
                made += candidate(conn, k, day=_today(now), kind="partner", headline=f"Listed as a {vendor} partner", source=_tag(url, now), grade="official",
                                  impact=2 if vendor in ("Thought Machine", "10x Banking", "Mambu") else 1, actor="collector:partners")
    remember(conn, "_all", "partners", seen, now, error=f"{failed} pages failed" if failed else None)
    return {"partner_pages": len(pages), "partner_candidates": made}


# ------------------------------------------------------------------ all of it
def run_all(conn: Conn, comps: list[dict[str, Any]], *, fetch: Fetch, post: Post | None, now: datetime, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for step in (lambda: tenders(conn, comps, fetch, now, sleep), lambda: sitemaps(conn, comps, fetch, now), lambda: feeds(conn, comps, fetch, now),
                 lambda: gdelt(conn, comps, fetch, now, sleep) if GDELT_ON() else {}, lambda: clutch(conn, comps, fetch, now), lambda: careers(conn, comps, fetch, post, now),
                 lambda: registers(conn, comps, fetch, now), lambda: aws(conn, comps, fetch, now, sleep), lambda: partner_pages(conn, comps, fetch, now)):
        try:
            out.update(step())
        except Exception as exc:  # noqa: BLE001  (one broken source never stops the others)
            conn.rollback()
            out.setdefault("errors", []).append(f"{type(exc).__name__}: {exc}"[:200])
        conn.commit()
    return out
