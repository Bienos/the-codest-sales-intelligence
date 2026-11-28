"""Weekly collection for the competitor tracker (docs/competitor-tracker.md, section 11). The lane ``competitor_watch`` queues one job a day; the job
works through a batch of firms so every firm gets its checks on time without one long run.

What it does, cheapest first:
* NEW PUBLIC AWARDS (no network): contract awards stored since the last run that a tracked firm won become candidate "client wins".
* OPEN ROLES (one public file): justjoin.it's sitemap of active offers, counted per firm by the firm's slug. Updated on the profile directly:
  a count is a fact, not a judgement. A big jump also becomes a candidate "hiring" move.
* HOMEPAGE HEADLINE (one page per firm): the first heading of the firm's homepage. When it differs from the headline on record, a candidate
  "positioning" move waits for review; confirming it updates the profile's headline.
* NEWS (Exa, inside the monthly Exa budget): pages from the last weeks that name the firm. Each becomes a candidate move with a type guessed
  from its words; a person confirms, re-types or dismisses it in Review.

Nothing found here is shown on the page until a person confirms it, except the open-role count. Every fetch goes through the public-address
client (sources/safe.py). Each check remembers when it last ran per firm in ``competitor_watch``.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urlparse

from . import competitors as ct
from .db import Conn, jb, qa
from .research.vendor_intel import on_page

JOBS_SITEMAP = "https://justjoin.it/sitemaps/active-jobs/part{}.xml"
MAX_SITEMAP_PARTS = 6
EVERY_DAYS = {1: 7, 2: 7, 3: 30}             # homepage check per tier
NEWS_DAYS = {1: 7, 2: 30}                    # news search per tier; tier 3 has none
HOMEPAGES_PER_RUN = 12
SEARCHES_PER_RUN = 10
NEWS_PER_FIRM = 3                            # at most this many news candidates per firm per search
OUR_COUNTRIES = {"GB", "DE", "AT", "CH", "SE", "DK", "NO", "FI", "PL"}
Fetch = Callable[[str], str]


# ------------------------------------------------------------------ bookkeeping
def last_run(conn: Conn, key: str, check: str) -> dict[str, Any] | None:
    return conn.execute("SELECT last_run_at, last_value FROM competitor_watch WHERE competitor_key=%s AND check_name=%s", (key, check)).fetchone()


def remember(conn: Conn, key: str, check: str, value: Any, now: datetime, error: str | None = None) -> None:
    conn.execute("INSERT INTO competitor_watch(competitor_key, check_name, last_run_at, last_value, last_error) VALUES (%s,%s,%s,%s,%s) "
                 "ON CONFLICT (competitor_key, check_name) DO UPDATE SET last_run_at=EXCLUDED.last_run_at, last_value=EXCLUDED.last_value, last_error=EXCLUDED.last_error",
                 (key, check, now, jb(value), error))


def due(conn: Conn, key: str, check: str, days: int, now: datetime) -> bool:
    r = last_run(conn, key, check)
    return r is None or r["last_run_at"] is None or now - r["last_run_at"] >= timedelta(days=days) - timedelta(hours=2)


def candidate(conn: Conn, key: str, *, day: str, kind: str, headline: str, source: str, grade: str, impact: int, actor: str,
              detail: str | None = None, payload: dict[str, Any] | None = None) -> bool:
    """A move waiting for review. Returns False when the same move (or the same source page for this firm) is already recorded."""
    headline = re.sub(r"\s+", " ", headline).strip()[:300]
    if len(headline) < 5 or not source.startswith("https://"):
        return False
    if conn.execute("SELECT 1 FROM competitor_moves WHERE competitor_key=%s AND source_url=%s", (key, source)).fetchone():
        return False
    r = conn.execute("INSERT INTO competitor_moves(competitor_key, happened_on, move_type, headline, detail, source_url, grade, impact, status, added_by, payload) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'candidate',%s,%s) ON CONFLICT (competitor_key, happened_on, headline) DO NOTHING RETURNING id",
                     (key, day, kind, headline, detail, source, grade, impact, actor, jb(payload or {}))).fetchone()
    return bool(r)


def tracked(conn: Conn) -> list[dict[str, Any]]:
    """Live firms with their tier, worked out the same way as the page."""
    rows = qa(conn, "SELECT key, name, is_us, reference, origin, profile FROM competitors WHERE retired_at IS NULL")
    us = next((r["profile"] for r in rows if r["is_us"]), {"pos": {"y": 5}})
    comps = [{**r["profile"], "key": r["key"], "name": r["name"], "reference": r["reference"], "origin": r["origin"]} for r in rows if not r["is_us"]]
    tiers = ct.assign_tiers(comps, us)
    for c in comps:
        c["tier"] = tiers[c["key"]]
    return comps


# ------------------------------------------------------------------ new public awards
def awards_since(conn: Conn, comps: list[dict[str, Any]], now: datetime) -> int:
    since = last_run(conn, "_all", "awards")
    cutoff = since["last_run_at"] if since else now - timedelta(days=35)
    match = ct._matcher(comps)
    tiers = {c["key"]: c["tier"] for c in comps}
    made = 0
    for r in qa(conn, "SELECT notice_id, buyer_name, buyer_country, winners, awarded_on, official_url, title FROM awarded_contracts "
                      "WHERE relevant AND first_seen_at > %s AND official_url LIKE 'https://%%'", (cutoff,)):
        for k in {ct.match_name(match, w) for w in r["winners"]} - {None}:
            ours = r["buyer_country"] in OUR_COUNTRIES
            made += candidate(conn, k, day=(r["awarded_on"] or now.date()).isoformat(), kind="client",
                              headline=f"Wins a public IT contract from {r['buyer_name'] or 'a public buyer'} ({r['buyer_country'] or '?'})",
                              detail=(r["title"] or "")[:500] or None, source=r["official_url"], grade="official",
                              impact=2 if ours and tiers.get(k) == 1 else 1, actor="collector:awards")
    remember(conn, "_all", "awards", {"new": made}, now)
    return made


# ------------------------------------------------------------------ open roles (justjoin.it)
def job_slug(c: dict[str, Any]) -> str:
    return str(c.get("jobSlug") or c["key"]).lower()


def count_offers(urls: list[str], slugs: dict[str, str]) -> dict[str, int]:
    """Offers per firm, by the slug at the start of the offer address (https://justjoin.it/job-offer/<slug>-<title>-...). Longest slug wins, so
    'sii' never takes 'sii-something-else' from a firm whose own slug is longer."""
    order = sorted(slugs.items(), key=lambda kv: -len(kv[1]))
    out = {k: 0 for k in slugs}
    for u in urls:
        m = re.match(r"https://justjoin\.it/job-offer/([a-z0-9-]+)", u)
        if not m:
            continue
        path = m.group(1) + "-"
        for k, slug in order:
            if path.startswith(slug + "-"):
                out[k] += 1
                break
    return out


def open_roles(conn: Conn, comps: list[dict[str, Any]], fetch: Fetch, now: datetime) -> dict[str, int]:
    if not due(conn, "_all", "jobs", 7, now):
        return {}
    urls: list[str] = []
    for i in range(MAX_SITEMAP_PARTS):
        try:
            xml = fetch(JOBS_SITEMAP.format(i))
        except Exception:  # noqa: BLE001  (a missing part ends the list; the first part failing is reported below)
            if i == 0:
                remember(conn, "_all", "jobs", {}, now, error="justjoin.it sitemap did not load")
                return {}
            break
        found = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
        if not found:
            break
        urls += found
    counts = count_offers(urls, {c["key"]: job_slug(c) for c in comps})
    source = JOBS_SITEMAP.format(0)
    for c in comps:
        n = counts[c["key"]]
        before = (c.get("jobs") or {}).get("n")
        if n == 0 and before is None:
            continue            # never seen on justjoin.it: leave the profile as it is
        fact = {"value": f"{n} open offers", "n": n, "source": source, "asOf": now.date().isoformat(), "grade": "reported"}
        conn.execute("UPDATE competitors SET profile = jsonb_set(profile, '{jobs}', %s) WHERE key=%s", (jb(fact), c["key"]))
        if isinstance(before, int) and c["tier"] in (1, 2) and abs(n - before) >= 10 and abs(n - before) >= max(before, 1) * 0.5:
            verb = "Opens" if n > before else "Cuts"
            candidate(conn, c["key"], day=now.date().isoformat(), kind="hiring", headline=f"{verb} job ads on justjoin.it: {before} to {n} open offers in a week",
                      source=f"https://justjoin.it/job-offers/all-locations?keyword={job_slug(c)}#{now.date().isoformat()}", grade="reported", impact=1,
                      actor="collector:jobs")
    remember(conn, "_all", "jobs", {"offers": len(urls)}, now)
    return counts


# ------------------------------------------------------------------ homepage headline
class _Head(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.h1: list[str] = []
        self.title = ""
        self.og = ""
        self._in: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "meta" and (a.get("property") or a.get("name")) in ("og:title",) and a.get("content"):
            self.og = self.og or str(a["content"])
        if tag in ("h1", "title") and self._in is None:
            self._in, self._buf = tag, []
        if tag in ("script", "style") and self._in:
            self._in, self._buf = None, []

    def handle_endtag(self, tag: str) -> None:
        if tag == self._in:
            text = re.sub(r"\s+", " ", unescape(" ".join(self._buf))).strip()
            if tag == "h1" and text:
                self.h1.append(text)
            elif tag == "title" and not self.title:
                self.title = text
            self._in = None

    def handle_data(self, data: str) -> None:
        if self._in:
            self._buf.append(data)


def headline_of(html: str) -> str:
    """The homepage's first heading; the social-card title or page title when the page has no heading in its HTML."""
    p = _Head()
    p.feed(html[:800_000])
    return (p.h1[0] if p.h1 else p.og or p.title)[:300]


def _same(a: str, b: str) -> bool:
    w = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()      # noqa: E731
    return bool(w(a)) and (w(a) == w(b) or w(a) in w(b) or w(b) in w(a))


def homepages(conn: Conn, comps: list[dict[str, Any]], fetch: Fetch, now: datetime) -> dict[str, int]:
    done = changed = failed = 0
    for c in sorted(comps, key=lambda c: c["tier"]):
        if done >= HOMEPAGES_PER_RUN:
            break
        url = c.get("url") or ""
        if not url.startswith("https://") or not due(conn, c["key"], "homepage", EVERY_DAYS[c["tier"]], now):
            continue
        done += 1
        try:
            head = headline_of(fetch(url))
        except Exception as exc:  # noqa: BLE001  (one unreachable site never stops the others)
            failed += 1
            remember(conn, c["key"], "homepage", None, now, error=type(exc).__name__)
            continue
        prev = (last_run(conn, c["key"], "homepage") or {}).get("last_value")
        on_record = (c.get("hero") or {}).get("value") or ""
        if head and not _same(head, on_record) and not (isinstance(prev, str) and _same(head, prev)):
            changed += candidate(conn, c["key"], day=now.date().isoformat(), kind="positioning", headline=f"New homepage headline: “{head}”",
                                 detail=f"On record: “{on_record}”" if on_record else None, source=f"{url.split('#')[0]}#seen-{now.date().isoformat()}",
                                 grade="official", impact=2 if c["tier"] == 1 else 1, actor="collector:homepage", payload={"hero": head, "url": url})
        remember(conn, c["key"], "homepage", head or prev, now)
    return {"homepages_checked": done, "headlines_changed": changed, "homepages_failed": failed}


# ------------------------------------------------------------------ news (Exa)
TYPE_WORDS: list[tuple[str, str]] = [
    (r"\b(acquires|acquired|acquisition|buys|bought|merger|merges)\b", "ma_buy"),
    (r"\b(appoints|appointed|new ceo|chief executive|managing director|joins as)\b", "leadership"),
    (r"\b(opens|opened|new office|expands to|enters)\b", "market"),
    (r"\b(partner|partnership|certified|certification|iso 27001|soc 2)\b", "partner"),
    (r"\b(revenue|results|profit|quarter|q[1-4] 20\d\d)\b", "results"),
    (r"\b(layoffs?|redundanc|hiring|hires|recruit)\b", "hiring"),
    (r"\b(launches|launched|introduces|new service|offering)\b", "offer"),
    (r"\b(award|ranked|ranking|winner)\b", "award"),
    (r"\b(selects|selected|chosen|contract|wins)\b", "client"),
]


def guess_type(text: str) -> str:
    t = (text or "").lower()
    for pat, kind in TYPE_WORDS:
        if re.search(pat, t):
            return kind
    return "content"


def news(conn: Conn, comps: list[dict[str, Any]], search: Callable[[str, dict[str, Any]], list[dict[str, Any]]] | None, now: datetime) -> dict[str, int]:
    if search is None:
        return {"news_searches": 0}
    from .research.exa_search import since, text_contents
    done = made = 0
    for c in sorted(comps, key=lambda c: c["tier"]):
        days = NEWS_DAYS.get(c["tier"])
        if not days or done >= SEARCHES_PER_RUN or not due(conn, c["key"], "news", days, now):
            continue
        done += 1
        body = {"query": f"{c['name']} software company news", "type": "auto", "numResults": 8, "startPublishedDate": since(days + 3, now),
                "contents": text_contents(1500)}
        try:
            pages = search(f"competitor_news:{c['key']}", body)
        except Exception as exc:  # noqa: BLE001  (budget used up or Exa refused: stop searching, keep what we have)
            remember(conn, c["key"], "news", None, now, error=type(exc).__name__)
            break
        variants = sorted(ct.name_keys(c)) + [c["name"]]
        own = urlparse(c.get("url") or "").hostname or ""
        own = own.removeprefix("www.")
        kept = 0
        for p in pages:
            title, text = p.get("title") or "", f"{p.get('title') or ''} {p.get('text') or ''}"
            if kept >= NEWS_PER_FIRM or not p.get("url", "").startswith("https://") or not on_page(variants, title):
                continue                      # the headline must name the firm: a passing mention in the text is not news about it
            host = (urlparse(p["url"]).hostname or "").removeprefix("www.")
            ours = bool(own and host.endswith(own))
            kind = guess_type(text[:600])
            if kind == "content" and not ours:
                continue                      # blog posts and opinion pieces elsewhere say nothing about what the firm did
            got = candidate(conn, c["key"], day=(p.get("published") or now.date().isoformat())[:10], kind=kind,
                            headline=title, source=p["url"], grade="official" if ours else "reported",
                            impact=1, actor="collector:news", detail=(p.get("text") or "")[:400] or None)
            made += got
            kept += got
        remember(conn, c["key"], "news", {"pages": len(pages)}, now)
    return {"news_searches": done, "news_candidates": made}


# ------------------------------------------------------------------ the job
def run(conn: Conn, *, fetch: Fetch, search: Callable[[str, dict[str, Any]], list[dict[str, Any]]] | None = None, now: datetime | None = None,
        post: Callable[[str, dict[str, Any]], str] | None = None, sleep: Callable[[float], None] | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    comps = tracked(conn)
    if not comps:
        return {"firms": 0}
    out: dict[str, Any] = {"firms": len(comps)}
    from .competitor_sources import run_all                 # tenders first: the awards step below turns new wins into candidates
    out.update(run_all(conn, comps, fetch=fetch, post=post, now=now, **({"sleep": sleep} if sleep else {})))
    out["award_candidates"] = awards_since(conn, comps, now)
    conn.commit()
    roles = open_roles(conn, comps, fetch, now)
    out["firms_with_roles"] = sum(1 for v in roles.values() if v)
    conn.commit()
    out.update(homepages(conn, comps, fetch, now))
    conn.commit()
    out.update(news(conn, comps, search, now))
    conn.commit()
    return out


def http_fetch(url: str, headers: dict[str, str] | None = None) -> str:
    """One public page, through the client that only reaches public addresses and re-checks every redirect."""
    from .sources.safe import SafeClient

    async def go() -> str:
        r = await SafeClient(timeout=20).get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; CodestResearch/1.0)", "Accept": "text/html,application/xml", **(headers or {})},
                                             follow_redirects=True)
        r.raise_for_status()
        return r.text[:3_000_000]
    return asyncio.run(go())


def http_post(url: str, body: dict[str, Any]) -> str:
    """A JSON POST to a public address (job boards that search by POST), through the same public-address client."""
    from .sources.safe import SafeClient

    async def go() -> str:
        r = await SafeClient(timeout=20).post(url, json=body, headers={"User-Agent": "Mozilla/5.0 (compatible; CodestResearch/1.0)", "Accept": "application/json"})
        r.raise_for_status()
        return r.text[:3_000_000]
    return asyncio.run(go())


def schedule(conn: Conn, now: datetime) -> int:
    """The lane: one job a day (the job itself decides which firms are due)."""
    from .jobs.queue import enqueue
    if not conn.execute("SELECT 1 FROM competitors LIMIT 1").fetchone():
        return 0
    _, created = enqueue(conn, "competitor_watch", {"day": now.date().isoformat()}, idempotency_key=f"competitor_watch:{now.date().isoformat()}", max_attempts=2)
    return int(created)


def watch_status(conn: Conn) -> dict[str, Any]:
    rows = qa(conn, "SELECT check_name, max(last_run_at) AS at, count(*) FILTER (WHERE last_error IS NOT NULL) AS errors FROM competitor_watch GROUP BY check_name")
    return {r["check_name"]: {"last_run": r["at"].isoformat() if r["at"] else None, "errors": r["errors"]} for r in rows}

