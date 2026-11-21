"""The company logo, taken from the company's own website the first time it is asked for and cached. Never redrawn.
Only https://thecodest.co is ever fetched, and only an image served from that site or its subdomains is accepted."""
from __future__ import annotations

import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

SITE = "https://thecodest.co/"
HOST = "thecodest.co"
TTL = 24 * 3600
MAX_BYTES = 400_000
_cache: dict[str, tuple[float, bytes | None, str]] = {}


class _Imgs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cands: list[tuple[int, str]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "img" and a.get("src"):
            blob = f"{a.get('src','')} {a.get('class','')} {a.get('alt','')} {a.get('id','')}".lower()
            if "logo" in blob:
                self.cands.append((0 if a["src"].lower().split("?")[0].endswith(".svg") else 1, a["src"]))
        if tag == "link" and "icon" in (a.get("rel") or "") and a.get("href") and a.get("href", "").lower().split("?")[0].endswith(".svg"):
            self.cands.append((2, a["href"]))


def _ours(url: str) -> bool:
    h = (urlparse(url).hostname or "").lower()
    return urlparse(url).scheme == "https" and (h == HOST or h.endswith("." + HOST))


def find_logo_url(html: str, base: str = SITE) -> str | None:
    p = _Imgs()
    p.feed(html[:1_500_000])
    for _, src in sorted(p.cands, key=lambda c: c[0]):
        u = urljoin(base, src)
        if _ours(u):
            return u
    return None


async def fetch_logo(client: httpx.AsyncClient | None = None) -> tuple[bytes | None, str]:
    now = time.time()
    hit = _cache.get("logo")
    if hit and now - hit[0] < TTL:
        return hit[1], hit[2]
    own = client is None
    client = client or httpx.AsyncClient(timeout=15, follow_redirects=True)
    try:
        page = await client.get(SITE)
        url = find_logo_url(page.text, str(page.url)) if page.status_code == 200 else None
        if not url:
            raise ValueError("no logo found on the home page")
        img = await client.get(url)
        ctype = img.headers.get("content-type", "").split(";")[0].lower()
        if img.status_code != 200 or len(img.content) > MAX_BYTES or ctype not in {"image/svg+xml", "image/png", "image/webp", "image/jpeg"}:
            raise ValueError("logo is not a small image")
        _cache["logo"] = (now, img.content, ctype)
        return img.content, ctype
    except Exception:   # noqa: BLE001 - a missing logo must never break a page: callers fall back to the text mark
        _cache["logo"] = (now - TTL + 600, None, "")      # retry in ten minutes
        return None, ""
    finally:
        if own:
            await client.aclose()
