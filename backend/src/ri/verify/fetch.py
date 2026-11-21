"""Fetch an ORIGINAL page for verification. Hardened: only public addresses (no private, loopback, link-local or cloud-metadata ranges),
every redirect hop re-validated, robots.txt honoured, size cap, HTML/text only, no social-network scraping, no login or paywall bypass."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx

from ..sources.base import NotFound, PolicyBlocked, TransientError
from ..sources.http import UA, classify
from ..sources.webwatch import RobotsCache, extract_section

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 3
SOCIAL = ("linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "t.co", "tiktok.com", "youtube.com", "youtu.be")


@dataclass(frozen=True)
class Page:
    url: str
    title: str | None
    text: str
    published_at: datetime | None
    content_hash: str


async def assert_public(url: str) -> None:
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme not in {"http", "https"} or not host:
        raise PolicyBlocked("only http(s) URLs can be verified")
    if u.username or u.password:
        raise PolicyBlocked("URLs with credentials are refused")
    if any(host == d or host.endswith("." + d) for d in SOCIAL):
        raise PolicyBlocked("social-network pages are not fetched (terms of service); use the company's own page or a news article")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, u.port or (443 if u.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise NotFound(f"host does not resolve: {host}") from exc
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise PolicyBlocked("host resolves to a non-public address")


_PUB = [
    re.compile(r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|og:published_time|date|pubdate|publishdate|dc\.date)["\'][^>]+content=["\']([^"\']+)', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:article:published_time|og:published_time)["\']', re.I),
    re.compile(r'"datePublished"\s*:\s*"([^"]+)"'),
    re.compile(r'<time[^>]+datetime=["\']([^"\']+)', re.I),
]


def parse_published(html: str) -> datetime | None:
    for rx in _PUB:
        m = rx.search(html)
        if m:
            try:
                d = datetime.fromisoformat(m.group(1).strip().replace("Z", "+00:00"))
                return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


async def fetch_page(client: httpx.AsyncClient, url: str, *, robots: RobotsCache | None = None) -> Page:
    robots = robots or RobotsCache()
    cur = url
    for _ in range(MAX_REDIRECTS + 1):
        await assert_public(cur)
        if not await robots.allowed(client, cur):
            raise PolicyBlocked("robots.txt disallows fetching this page")
        try:
            async with client.stream("GET", cur, headers={"User-Agent": UA, "Accept": "text/html,text/plain;q=0.9"}, follow_redirects=False) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    cur = urljoin(cur, r.headers["location"])
                    continue
                classify(r)
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype and ctype not in {"text/html", "application/xhtml+xml", "text/plain"}:
                    raise PolicyBlocked(f"unsupported content type {ctype} (PDF and other files are not read yet)")
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_BYTES:
                        break
                enc = r.encoding or "utf-8"
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TransientError(f"{type(exc).__name__}: {exc}") from exc
        html = bytes(buf[:MAX_BYTES]).decode(enc, errors="replace")
        text, _ = extract_section(html, None)
        tm = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = re.sub(r"\s+", " ", tm.group(1)).strip()[:300] if tm else None
        return Page(cur, title, text[:60000], parse_published(html), hashlib.sha256(text.encode()).hexdigest())
    raise PolicyBlocked("too many redirects")


def page_to_json(p: Page) -> str:      # for logs/tests only
    return json.dumps({"url": p.url, "title": p.title, "chars": len(p.text)})
