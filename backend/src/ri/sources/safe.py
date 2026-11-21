"""One HTTP client for every connector: a fresh connection per request (an httpx AsyncClient is bound to the event loop that first used it,
and the pipeline runs one loop per page), public addresses only, and every redirect hop re-checked."""
from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

MAX_HOPS = 4


class SafeClient:
    def __init__(self, timeout: float = 30):
        self.timeout = timeout

    async def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        from ..verify.fetch import assert_public       # imported late: avoids a cycle with the connectors
        follow = kw.pop("follow_redirects", False)
        cur = url
        for _ in range(MAX_HOPS + 1):
            await assert_public(cur)
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as c:
                r = await c.request(method, cur, **kw)
            if follow and r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                cur = urljoin(cur, r.headers["location"])
                if r.status_code == 303:
                    method, kw = "GET", {k: v for k, v in kw.items() if k not in {"json", "data", "content"}}
                continue
            return r
        raise httpx.TooManyRedirects("too many redirects")

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("POST", url, **kw)
