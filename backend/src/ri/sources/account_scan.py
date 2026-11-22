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

from .http import UA
from .webwatch import RobotsCache

NEWS_PATH = re.compile(r"/(news|newsroom|press|press-releases?|media|media-centre|media-center|insights|stories|blog)(/|$|\?)", re.I)
CAREERS_PATH = re.compile(r"/(careers?|jobs|join-us|work-with-us|vacancies)(/|$|\?)", re.I)
ATS = [("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)", re.I)),
       ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([a-z0-9_-]+)", re.I)),
       ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_.-]+)", re.I)),
       ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I))]
BOARD_HOST = {"greenhouse": "boards.greenhouse.io", "lever": "jobs.lever.co", "ashby": "jobs.ashbyhq.com", "smartrecruiters": "jobs.smartrecruiters.com"}
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
FEED_GUESSES = ("/feed", "/rss.xml", "/news/feed", "/blog/feed")
MAX_BYTES = 2_000_000


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
    feeds = [urljoin(page_url, f) for f in p.feeds]
    news, careers, ats = [], [], []
    for raw in p.links:
        u = urljoin(page_url, raw.strip())
        if not u.startswith("http"):
            continue
        for prov, rx in ATS:
            m = rx.search(u)
            if m and m.group(1).lower() not in {"embed", "jobs", "www"}:
                ats.append((prov, m.group(1)))
        host = urlparse(u).netloc
        path = urlparse(u).path
        depth = len([x for x in path.split("/") if x and not re.fullmatch(r"[a-z]{2}(-[a-z]{2})?", x, re.I)])   # ignore a language prefix such as /en/
        last = "/" + ([x for x in path.split("/") if x] or [""])[-1] + "/"
        if _same_site(host, domain) and depth <= 2:          # section index pages only: the last path segment must itself be the section, not an article
            if NEWS_PATH.search(last):
                news.append(u.split("#")[0])
            if CAREERS_PATH.search(last):
                careers.append(u.split("#")[0])
    dedupe = lambda xs: list(dict.fromkeys(xs))   # noqa: E731
    return {"feeds": dedupe(feeds), "news": dedupe(news), "careers": dedupe(careers), "ats": dedupe(ats)}


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
    feeds, news, careers, ats = list(found["feeds"]), list(found["news"]), list(found["careers"]), list(found["ats"])
    if careers:                                  # job-board slugs usually sit on the careers page
        page = await _get(client, robots, careers[0])
        if page:
            ats += parse_page(page, careers[0], domain)["ats"]
    if not ats and org_name:                     # nothing linked from the site: try the company's short name, accepting only a board that names this company
        ats += await probe_boards(client, domain, org_name)
        if ats:
            notes.append(f"{domain}: job board found by name match, not linked from the website: {', '.join(f'{p}/{b}' for p, b in ats)}")
    if news:                                     # a newsroom often advertises its own feed
        page = await _get(client, robots, news[0])
        if page:
            feeds += parse_page(page, news[0], domain)["feeds"]
    if not feeds:
        for g in FEED_GUESSES[:3]:
            body = await _get(client, robots, urljoin(home_url, g))
            if body and re.match(r"\s*(<\?xml|<rss|<feed)", body):
                feeds.append(urljoin(home_url, g))
                break
    eps: list[dict[str, Any]] = []
    for u in list(dict.fromkeys(feeds))[:3]:
        eps.append({"kind": "feed", "url": u, "provider": None, "board": None})
    for u in news[:2]:
        eps.append({"kind": "newsroom", "url": u, "provider": None, "board": None})
    for u in careers[:1]:
        eps.append({"kind": "careers", "url": u, "provider": None, "board": None})
    for prov, board in list(dict.fromkeys(ats))[:3]:
        eps.append({"kind": "job_board", "url": f"https://{BOARD_HOST[prov]}/{board}", "provider": prov, "board": board})
    if not eps:
        notes.append(f"{domain}: no news feed, press page, careers page or job board found on the home page")
    return {"endpoints": eps, "notes": notes}


def run_scan(conn, org_id: str, *, client: httpx.AsyncClient, extractor=None, resolver=None, now=None, force_discover: bool = False) -> dict[str, Any]:
    """Discover (or re-use) the account's endpoints, read each one, and push what was found through the normal pipeline."""
    from ..db import q1, qa
    from ..ingest.pipeline import RunResult, _run, close_unseen_jobs, upsert_document, upsert_job
    from ..ingest.process import process_run
    from .ats import AshbyConnector, GreenhouseConnector, LeverConnector, SmartRecruitersConnector
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
    if force_discover or not existing:
        d = _run(discover(client, org["canonical_domain"], org_name=org["display_name"]))
        notes += d["notes"]
        for e in d["endpoints"]:
            conn.execute("INSERT INTO account_endpoints(organization_id, kind, url, provider, board) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (organization_id, url) DO NOTHING",
                         (org_id, e["kind"], e["url"], e["provider"], e["board"]))
        conn.commit()
        existing = qa(conn, "SELECT * FROM account_endpoints WHERE organization_id=%s", (org_id,))
    res = RunResult("account-scan", "completed")
    robots = RobotsCache()
    retention = {r["source_id"]: r["retention_policy"] for r in qa(conn, "SELECT source_id, retention_policy FROM sources WHERE source_id IN ('corporate-ir-newsrooms','ats-greenhouse','ats-lever','ats-ashby','ats-smartrecruiters')")}
    ats_cls = {"greenhouse": GreenhouseConnector, "lever": LeverConnector, "ashby": AshbyConnector, "smartrecruiters": SmartRecruitersConnector}
    scanned = 0
    for ep in existing:
        status, detail, seen = "ok", None, 0
        try:
            with conn.transaction():
                if ep["kind"] in ("feed", "newsroom", "careers"):
                    sid = "corporate-ir-newsrooms"
                    con = FeedConnector(client, ep["url"], source_id=sid, robots=robots) if ep["kind"] == "feed" else PageWatchConnector(client, ep["url"], source_id=sid, robots=robots)
                    batch = _run(con.fetch(None, 30))
                    for item in batch.items:
                        d = con.canonicalize(item)
                        doc_id, st = upsert_document(conn, sid, d, retention.get(sid) or "excerpt")
                        seen += 1
                        if st == "new":
                            res.new_doc_ids.append(doc_id)
                        elif st == "changed":
                            res.changed_doc_ids.append(doc_id)
                elif ep["kind"] == "job_board" and ep["provider"] in ats_cls:
                    con = ats_cls[ep["provider"]](client, ep["board"], employer_name=org["display_name"], source_id=f"ats-{ep['provider']}")
                    batch = _run(con.fetch(None, 500))
                    ids: set[str] = set()
                    for item in batch.items:
                        d = con.canonicalize(item)
                        upsert_document(conn, f"ats-{ep['provider']}", d, retention.get(f"ats-{ep['provider']}") or "excerpt")
                        jid, ch = upsert_job(conn, d.structured, org_id, now)
                        ids.add(d.structured["job_id"])
                        seen += 1
                        if ch != "unchanged":
                            res.structured_changes.append(("job", jid, ch))
                    if batch.complete_listing:
                        for closed in close_unseen_jobs(conn, ep["provider"], ep["board"], ids, now):   # only after an affirmative full listing
                            res.structured_changes.append(("job", closed, "closed"))
            scanned += 1
        except Exception as exc:   # noqa: BLE001 - one bad endpoint must not stop the others
            status, detail = "failed", f"{type(exc).__name__}: {exc}"[:300]
        conn.execute("UPDATE account_endpoints SET last_scanned_at=%s, last_status=%s, last_detail=%s, docs_seen=%s WHERE id=%s", (now, status, detail, seen, ep["id"]))
        conn.commit()
    for fam, kinds in (("newsroom", ("feed", "newsroom")), ("jobs", ("job_board", "careers"))):
        rows = qa(conn, "SELECT url, last_status FROM account_endpoints WHERE organization_id=%s AND kind = ANY(%s)", (org_id, list(kinds)))
        state = "source_not_found" if not rows else ("monitored_healthy" if any(r["last_status"] == "ok" for r in rows) else "monitored_degraded")
        conn.execute("INSERT INTO account_source_coverage(organization_id, source_family, coverage_state, source_ids, last_success_at, note, updated_at) VALUES (%s,%s,%s,%s,%s,%s,now()) "
                     "ON CONFLICT (organization_id, source_family) DO UPDATE SET coverage_state=EXCLUDED.coverage_state, source_ids=EXCLUDED.source_ids, last_success_at=EXCLUDED.last_success_at, note=EXCLUDED.note, updated_at=now()",
                     (org_id, fam, state, [r["url"] for r in rows][:5], now if state == "monitored_healthy" else None, "; ".join(notes)[:300] or None))
    conn.commit()
    out: dict[str, Any] = {"status": "ok", "endpoints": len(existing), "scanned": scanned, "documents": len(res.new_doc_ids) + len(res.changed_doc_ids), "job_changes": len(res.structured_changes), "notes": notes}
    if res.new_doc_ids or res.changed_doc_ids or res.structured_changes:
        res_resolver = (lambda name: (resolver(name) if resolver else None) or org_id)
        out["processed"] = process_run(conn, res, extractor=extractor, resolve_org=res_resolver)
    conn.commit()
    return out
