"""Permitted page/feed change monitoring (spec 02 §7 website change monitoring).

Rules: robots.txt is honoured (disallow => PolicyBlocked, collector stops); conditional GET (ETag/Last-Modified);
only the configured page SECTION is hashed (chrome/tracking stripped) so cosmetic churn emits nothing; dynamic
pages that repeatedly fail are marked degraded - we do NOT bypass blocks, paywalls or authentication.
"""
from __future__ import annotations

import html as html_lib
import re
import urllib.robotparser
import xml.etree.ElementTree as ET
from functools import lru_cache
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from .base import (FetchBatch, HealthCheck, PolicyBlocked, RawItem, SchemaDrift, SourceDescriptor, SourceDocumentDraft)
from .http import UA, classify
from .util import excerpt, parse_dt, sha, utcnow

_DROP = {"script", "style", "noscript", "nav", "footer", "header", "form", "iframe", "svg"}
CONTENT_ENCODED = "{http://purl.org/rss/1.0/modules/content/}encoded"
CONTENT_EXCERPT = 1500          # characters kept of an item's body when the feed gives no description (a short excerpt, as for every licensed feed)


def plain_excerpt(markup: str | None, limit: int = CONTENT_EXCERPT) -> str:
    """Visible text of a snippet of HTML, shortened. Used for feeds that carry the article only as `content:encoded` (Medium, some blogs)."""
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", markup or ""))).strip()[:limit]


@lru_cache(maxsize=1)
def first_party_publishers() -> dict[str, str]:
    """Registry sources that are ONE company's own feed (family `company_ir`, e.g. an engineering blog), by source id -> the company's name.
    The reader tells the model who published an item ("Published by Wise."), so a post that only says "we" is still about the right company.
    Read from the shipped registry file; `corporate-ir-newsrooms` (every account) gets its publisher from the account scan instead."""
    import yaml
    from .registry import SEED
    try:
        rows = yaml.safe_load(SEED.read_text(encoding="utf-8"))["sources"]
    except (OSError, KeyError, yaml.YAMLError):
        return {}
    return {r["source_id"]: r["provider"] for r in rows if r.get("source_family") == "company_ir" and r.get("provider") and r["source_id"] != "corporate-ir-newsrooms"
            and (r.get("collection_method") == "feed")}


class _Extract(HTMLParser):
    """Collect visible text, optionally only inside an element matched by id/class/tag (`selector`)."""

    def __init__(self, selector: str | None):
        super().__init__()
        self.sel, self.depth_drop, self.depth_sel, self.chunks, self.links = selector, 0, 0, [], []
        self._stack: list[tuple[str, bool]] = []

    def _match(self, tag, attrs) -> bool:
        if not self.sel:
            return True
        a = dict(attrs)
        if self.sel.startswith("#"):
            return a.get("id") == self.sel[1:]
        if self.sel.startswith("."):
            return self.sel[1:] in (a.get("class") or "").split()
        return tag == self.sel

    def handle_starttag(self, tag, attrs):
        if tag in _DROP:
            self.depth_drop += 1
        entering = self.depth_sel == 0 and self._match(tag, attrs)
        if self.depth_sel > 0 or entering:
            self.depth_sel += 1
        self._stack.append((tag, self.depth_sel > 0))
        if self.depth_sel > 0 and tag == "a" and dict(attrs).get("href"):
            self.links.append(dict(attrs)["href"])

    def handle_endtag(self, tag):
        if tag in _DROP and self.depth_drop:
            self.depth_drop -= 1
        if self._stack:
            self._stack.pop()
            if self.depth_sel > 0:
                self.depth_sel -= 1

    def handle_data(self, data):
        if self.depth_sel > 0 and not self.depth_drop and data.strip():
            self.chunks.append(data.strip())


def extract_section(html: str, selector: str | None) -> tuple[str, list[str]]:
    p = _Extract(selector)
    p.feed(html)
    text = " ".join(p.chunks)
    text = re.sub(r"\b\d{1,2}:\d{2}(:\d{2})?\b", "", text)       # drop clocks/timestamps that churn every load
    text = re.sub(r"\s+", " ", text).strip()
    return text, p.links


_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}


def xml_safe(text: str) -> str:
    """Real-world RSS often contains HTML entities (&nbsp;, &eacute;) that strict XML rejects, and stray control characters. Make it parseable without changing the meaning."""
    import html.entities as he

    def fix(m: re.Match) -> str:
        n = m.group(1)
        if n in _XML_ENTITIES:
            return m.group(0)
        cp = he.name2codepoint.get(n)
        return f"&#{cp};" if cp else "&amp;" + n + ";"
    text = re.sub(r"&([A-Za-z][A-Za-z0-9]{1,31});", fix, text)
    text = re.sub(r"&(?!(?:[A-Za-z][A-Za-z0-9]{1,31}|#\d+|#x[0-9A-Fa-f]+);)", "&amp;", text)       # bare ampersands
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def content_signals(robots_txt: str, user_agent: str = UA) -> dict[str, str]:
    """Cloudflare-style `Content-Signal: search=yes, ai-train=no` lines that apply to `user_agent`.

    A line before any User-agent line applies to everyone; a line inside a group applies to that group's agents. Groups are read the
    way urllib.robotparser reads them (a blank line ends a group), and a group naming our agent wins over the `*` group, as for Allow/Disallow.
    Group values override the global ones key by key. Keys and values are lower-cased; tokens that are not key=value are ignored."""
    me = user_agent.split("/")[0].lower()
    glob: dict[str, str] = {}
    groups: list[tuple[list[str], dict[str, str]]] = []
    cur: tuple[list[str], dict[str, str]] | None = None
    has_rules = False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            cur = None
            continue
        key, sep, val = line.partition(":")
        if not sep:
            continue
        key, val = key.strip().lower(), val.strip()
        if key == "user-agent":
            if cur is None or has_rules:
                cur, has_rules = ([], {}), False
                groups.append(cur)
            cur[0].append(val.lower())
            continue
        if cur is not None:
            has_rules = True
        if key == "content-signal":
            target = cur[1] if cur is not None else glob
            for part in val.split(","):
                k, eq, v = part.partition("=")
                if eq and k.strip():
                    target[k.strip().lower()] = v.strip().lower()
    mine = [g for agents, g in groups if any(a != "*" and a in me for a in agents)]
    chosen = mine or [g for agents, g in groups if "*" in agents]
    out = dict(glob)
    for g in chosen:
        out.update(g)
    return out


class RobotsCache:
    """robots.txt per origin. Besides Allow/Disallow it honours Cloudflare Content Signals: everything we fetch can be sent to a language
    model for extraction, so `ai-input=no` means we may not read the page at all. `ai-train=no` alone does not stop us (we never train)."""

    def __init__(self):
        self._c: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._signals: dict[str, dict[str, str]] = {}

    @staticmethod
    def _origin(url: str) -> str:
        return f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    async def _load(self, client: httpx.AsyncClient, origin: str) -> None:
        rp = urllib.robotparser.RobotFileParser()
        self._signals[origin] = {}
        try:
            r = await client.get(f"{origin}/robots.txt", headers={"User-Agent": UA})
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
                self._c[origin] = rp
                self._signals[origin] = content_signals(r.text)
            elif r.status_code in (401, 403):
                self._c[origin] = None          # explicit denial => treat as disallowed
            else:
                self._c[origin] = rp            # no robots file => allowed (parser default)
                rp.parse([])
        except httpx.HTTPError:
            self._c[origin] = rp
            rp.parse([])

    def ai_input_refused(self, url: str) -> bool:
        """True when the site's robots.txt says its content may not be given to AI models (Content-Signal ai-input=no)."""
        return self._signals.get(self._origin(url), {}).get("ai-input") == "no"

    def refusal(self, url: str) -> str:
        """Plain reason for a refused URL, for error messages."""
        if self.ai_input_refused(url):
            return f"robots.txt Content-Signal says ai-input=no: the site does not allow its pages to be given to AI models ({url})"
        return f"robots.txt disallows {url}"

    async def allowed(self, client: httpx.AsyncClient, url: str) -> bool:
        origin = self._origin(url)
        if origin not in self._c:
            await self._load(client, origin)
        rp = self._c[origin]
        return bool(rp and rp.can_fetch(UA, url)) and not self.ai_input_refused(url)


class PageWatchConnector:
    def __init__(self, client: httpx.AsyncClient, url: str, *, source_id: str, selector: str | None = None, now_fn=utcnow,
                 robots: RobotsCache | None = None):
        self.client, self.url, self.source_id, self.selector, self.now = client, url, source_id, selector, now_fn
        self.robots = robots or RobotsCache()
        self._validators: dict[str, str] = {}

    def describe(self) -> SourceDescriptor:
        return SourceDescriptor(self.source_id, "page-monitor", "change_monitor", "S2", "1")

    async def health_check(self) -> HealthCheck:
        try:
            batch = await self.fetch(None, 1)
            if not batch.items or len(batch.items[0].payload.get("text", "").strip()) < 50:
                raise SchemaDrift("page contains too little readable text to monitor")
            return HealthCheck(True)
        except Exception as exc:  # noqa: BLE001
            return HealthCheck(False, str(exc), getattr(exc, "error_class", "transient_network"))

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        if not await self.robots.allowed(self.client, self.url):
            raise PolicyBlocked(self.robots.refusal(self.url))
        hdrs = {"User-Agent": UA}
        if cursor:  # cursor = "etag|last-modified" from the previous successful run
            etag, _, lm = cursor.partition("|")
            if etag:
                hdrs["If-None-Match"] = etag
            if lm:
                hdrs["If-Modified-Since"] = lm
        resp = await self.client.get(self.url, headers=hdrs, follow_redirects=True)
        if resp.status_code == 304:
            return FetchBatch([], cursor, False, self.now(), "1", quota_usage={"requests": 1}, complete_listing=False)
        classify(resp)
        text, links = extract_section(resp.text, self.selector)
        if self.selector and not text:
            raise SchemaDrift(f"selector {self.selector!r} matched nothing on {self.url} (page layout changed?)")
        nxt = f"{resp.headers.get('ETag', '')}|{resp.headers.get('Last-Modified', '')}"
        return FetchBatch([RawItem(self.url, sha(text), {"text": text, "links": links, "html_bytes": len(resp.content)}, self.url)],
                          nxt, False, self.now(), "1", quota_usage={"requests": 1})

    async def fetch_by_id(self, external_id: str) -> RawItem | None:
        b = await self.fetch(None, 1)
        return b.items[0] if b.items else None

    def canonicalize(self, item: RawItem) -> SourceDocumentDraft:
        t = item.payload["text"]
        return SourceDocumentDraft(item.external_id, item.version or sha(t), item.external_id, None, None, t, excerpt(t),
                                   raw_meta={"links": item.payload["links"][:50]})


class FeedConnector:
    """RSS 2.0 / Atom. `external_id` = guid/id, falling back to link. publication date preserved separately."""

    def __init__(self, client: httpx.AsyncClient, url: str, *, source_id: str, now_fn=utcnow, robots: RobotsCache | None = None, publisher: str | None = None):
        self.client, self.url, self.source_id, self.now = client, url, source_id, now_fn
        self.robots = robots or RobotsCache()
        self.publisher = publisher or first_party_publishers().get(source_id)       # set only for a company's own feed: it names who published each item

    def describe(self) -> SourceDescriptor:
        return SourceDescriptor(self.source_id, "feed", "feed", "S2", "1")

    async def health_check(self) -> HealthCheck:
        try:
            await self.fetch(None, 1)
            return HealthCheck(True)
        except Exception as exc:  # noqa: BLE001
            return HealthCheck(False, str(exc), getattr(exc, "error_class", "transient_network"))

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        if not await self.robots.allowed(self.client, self.url):
            raise PolicyBlocked(self.robots.refusal(self.url))
        resp = await self.client.get(self.url, headers={"User-Agent": UA}, follow_redirects=True)
        classify(resp)
        try:
            root = ET.fromstring(xml_safe(resp.text).lstrip("\ufeff \t\r\n"))      # many WordPress feeds start with a blank line before <?xml
        except ET.ParseError as exc:
            raise SchemaDrift(f"feed is not valid XML: {exc}") from exc
        items = []
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entries = root.findall(".//item") or root.findall(".//a:entry", ns)
        if root.tag.endswith("}RDF"):            # RSS 1.0 (RDF), used by BIS and others
            def local(el, name):
                for c in el:
                    if c.tag.split("}")[-1] == name and c.text:
                        return c.text.strip()
                return None
            for e in [el for el in root if el.tag.split("}")[-1] == "item"][:limit]:
                link = local(e, "link")
                guid = e.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about") or link
                if guid:
                    when = local(e, "date") or local(e, "pubDate")
                    items.append(RawItem(guid, when, {"title": local(e, "title"), "link": link, "summary": local(e, "description"), "published": when}, link))
            return FetchBatch(items, None, False, self.now(), "1", quota_usage={"requests": 1})
        if not entries and root.tag not in {"rss", "{http://www.w3.org/2005/Atom}feed"}:
            raise SchemaDrift("unrecognised feed format")
        for e in entries[:limit]:
            def g(tag, atag=None, e=e):
                el = e.find(tag)
                if el is None and atag:
                    el = e.find(f"a:{atag}", ns)
                return (el.text or "").strip() if el is not None and el.text else None
            link = g("link")
            if not link:
                l = e.find("a:link", ns)
                link = l.get("href") if l is not None else None
            guid = g("guid", "id") or link
            if not guid:
                continue
            summary, content = g("description", "summary"), None
            if not summary:                 # no description: take a short excerpt of the full text (content:encoded, or Atom <content>) instead of the title alone
                body = e.find(CONTENT_ENCODED)
                if body is None:
                    body = e.find("a:content", ns)
                content = plain_excerpt(body.text) if body is not None and body.text else None
            items.append(RawItem(guid, g("pubDate", "updated"), {"title": g("title", "title"), "link": link, "summary": summary, "content": content,
                                                             "published": g("pubDate", "published") or g("pubDate", "updated")}, link))
        return FetchBatch(items, None, False, self.now(), "1", quota_usage={"requests": 1})

    async def fetch_by_id(self, external_id: str) -> RawItem | None:
        b = await self.fetch(None, 200)
        return next((i for i in b.items if i.external_id == external_id), None)

    def canonicalize(self, item: RawItem) -> SourceDocumentDraft:
        p = item.payload
        text = re.sub(r"<[^>]+>", " ", p.get("summary") or "") or (p.get("content") or "")      # the description; failing that the excerpt of the full text
        text = re.sub(r"\s+", " ", f"{p.get('title') or ''}. {text}").strip()
        if self.publisher:
            text = f"Published by {self.publisher}. {text}"
        pub = None
        if p.get("published"):
            when = re.sub(r"<[^>]+>", "", p["published"]).strip()         # some feeds wrap the date in a <time> tag
            try:
                from email.utils import parsedate_to_datetime
                pub = parsedate_to_datetime(when)
            except (TypeError, ValueError):
                pub = parse_dt(when)
        return SourceDocumentDraft(item.external_id, sha(item.external_id, p.get("title"), p.get("summary")), urljoin(self.url, p["link"]) if p.get("link") else None,
                                   p.get("title"), pub, text, excerpt(text))
