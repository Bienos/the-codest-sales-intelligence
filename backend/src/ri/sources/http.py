"""Shared async HTTP helper: timeout, bounded retry budget, Retry-After, error classification, secret-safe logging."""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .base import (AccessDenied, AuthError, EgressBlocked, NotFound, RateLimited, SchemaDrift, ServerError,
                   TransientError)

UA = "CodestRevenueIntelligence/0.1 (+contact: sales-intelligence@thecodest.co)"


def classify(resp: httpx.Response) -> None:
    s = resp.status_code
    if s < 400 or s == 304:
        return
    if s == 401:
        raise AuthError("401 unauthorized", status=s)
    if s == 403:
        # the sandbox/egress proxy answers 403 with an [agent-proxy] marker; real provider denial otherwise
        if "agent proxy" in resp.text[:300].lower():
            raise EgressBlocked("egress proxy refused host", status=s)
        raise AccessDenied("403 forbidden", status=s)
    if s == 404:
        raise NotFound("404 not found", status=s)
    if s == 429:
        ra = resp.headers.get("Retry-After")
        raise RateLimited("429 rate limited", retry_after=float(ra) if ra and ra.isdigit() else None, status=s)
    if s >= 500:
        raise ServerError(f"{s} server error", status=s)
    body = ""
    try:
        body = resp.text[:240].replace("\n", " ")        # providers explain 4xx errors in the body; keep it so failures are diagnosable
    except Exception:  # noqa: BLE001  (a streaming response that was not read)
        pass
    raise SchemaDrift(f"unexpected status {s}" + (f": {body}" if body else ""), status=s)


async def request_json(client: httpx.AsyncClient, method: str, url: str, *, retries: int = 2, backoff: float = 0.5,
                       **kw: Any) -> Any:
    """Retries only transient classes inside the call; rate-limit/auth/etc. propagate for the job layer to schedule."""
    last: Exception | None = None
    extra_headers = kw.pop("headers", {})
    for attempt in range(retries + 1):
        try:
            resp = await client.request(method, url, headers={"User-Agent": UA, **extra_headers}, **kw)
            classify(resp)
            try:
                return resp.json()
            except ValueError as exc:
                raise SchemaDrift("response was not JSON") from exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = TransientError(f"{type(exc).__name__}: {exc}")
        except ServerError as exc:
            last = exc
        if attempt < retries:
            await asyncio.sleep(backoff * (2 ** attempt))
    assert last is not None
    raise last
