"""One HTTP client for every connector: a fresh connection per request (an httpx AsyncClient is bound to the event loop that first used it,
and the pipeline runs one loop per page), public addresses only, and every redirect hop re-checked.

DNS rebinding: checking a host name and then letting the HTTP library resolve it again leaves a window in which the name can start pointing
at an internal address. `public_transport()` closes it: the connection is opened to the exact address that was checked, and TLS still
verifies the certificate for the original host name. When the process reaches the internet through an HTTP proxy (only in sandboxes, never
on Render) the proxy resolves names itself, so pinning is not possible there and the pre-flight check is the control.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from typing import Any
from urllib.parse import urljoin

import httpcore
import httpx

MAX_HOPS = 4
ALLOWED_PORTS = frozenset({80, 443})
_PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")


def _blocked(msg: str) -> Exception:
    from .base import PolicyBlocked        # imported late: base has no dependencies, but keeps this module importable on its own
    return PolicyBlocked(msg)


def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped                      # ::ffff:10.0.0.1 is 10.0.0.1
    return addr.is_global and not addr.is_multicast


async def resolve_public(host: str, port: int) -> str:
    """Resolve `host` and return one address, refusing if ANY address it resolves to is not public."""
    if port not in ALLOWED_PORTS:
        raise _blocked(f"port {port} is not allowed (only 80 and 443)")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        from .base import NotFound
        raise NotFound(f"host does not resolve: {host}") from exc
    ips = [str(i[4][0]) for i in infos]
    if not ips or not all(is_public_ip(ip) for ip in ips):
        raise _blocked("host resolves to a non-public address")
    return ips[0]


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    """Resolves and checks the host at connect time and connects to the checked address (no second lookup)."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None):
        from httpcore._backends.auto import AutoBackend
        self._inner = inner or AutoBackend()

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address: str | None = None,
                          socket_options: Any = None) -> httpcore.AsyncNetworkStream:
        ip = await resolve_public(host, port)
        return await self._inner.connect_tcp(ip, port, timeout=timeout, local_address=local_address, socket_options=socket_options)

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options: Any = None) -> httpcore.AsyncNetworkStream:
        raise _blocked("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def proxied() -> bool:
    return any(os.environ.get(v) for v in _PROXY_VARS)


def public_transport() -> httpx.AsyncHTTPTransport | None:
    """A transport that can only reach public addresses. None when an egress proxy is configured (see the module note)."""
    if proxied():
        return None
    t = httpx.AsyncHTTPTransport()
    t._pool._network_backend = _PinnedBackend()       # httpcore reads this attribute for every new connection
    return t


def public_client(timeout: float = 30, **kw: Any) -> httpx.AsyncClient:
    """An AsyncClient for fetching addresses that came from users, sheets, search results or scraped pages."""
    kw.setdefault("follow_redirects", False)
    transport = public_transport()
    return httpx.AsyncClient(timeout=timeout, transport=transport, **kw) if transport else httpx.AsyncClient(timeout=timeout, **kw)


class SafeClient:
    def __init__(self, timeout: float = 30):
        self.timeout = timeout

    async def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        from ..verify.fetch import assert_public       # imported late: avoids a cycle with the connectors
        follow = kw.pop("follow_redirects", False)
        cur = url
        for _ in range(MAX_HOPS + 1):
            await assert_public(cur)
            async with public_client(self.timeout) as c:
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

    def stream(self, method: str, url: str, **kw: Any):
        """Streaming through the safe path: one hop, checked; callers that follow redirects re-check each hop themselves."""
        return _SafeStream(self.timeout, method, url, kw)


class _SafeStream:
    def __init__(self, timeout: float, method: str, url: str, kw: dict[str, Any]):
        self.timeout, self.method, self.url, self.kw = timeout, method, url, kw
        self._client: httpx.AsyncClient | None = None
        self._cm: Any = None

    async def __aenter__(self) -> httpx.Response:
        from ..verify.fetch import assert_public
        await assert_public(self.url)
        self._client = public_client(self.timeout)
        self._cm = self._client.stream(self.method, self.url, **{**self.kw, "follow_redirects": False})
        return await self._cm.__aenter__()

    async def __aexit__(self, *exc: Any) -> None:
        try:
            if self._cm is not None:
                await self._cm.__aexit__(*exc)
        finally:
            if self._client is not None:
                await self._client.aclose()
