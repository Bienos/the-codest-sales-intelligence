"""Shared async HTTP helper: timeout, bounded retry budget, Retry-After, error classification, secret-safe logging."""
from __future__ import annotations

import asyncio
from typing import Any

import re

import httpx

from .base import (AccessDenied, AuthError, BillingError, EgressBlocked, NotFound, RateLimited, SchemaDrift, ServerError,
                   TransientError)

UA = "CodestRevenueIntelligence/0.1 (+contact: sales-intelligence@thecodest.co)"
RATE_LIMIT_MAX_WAIT = 30.0          # a page that answers 429 is retried once when the provider asks us to wait at most this long
RATE_LIMIT_DEFAULT_WAIT = 10.0      # used when a 429 carries no Retry-After header


def retry_after_seconds(value: str | None) -> float | None:
    """Retry-After is either a number of seconds or an HTTP date."""
    if not value:
        return None
    v = value.strip()
    if v.isdigit():
        return float(v)
    try:
        from datetime import datetime, timezone
        from email.utils import parsedate_to_datetime
        return max(0.0, (parsedate_to_datetime(v) - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, IndexError):
        return None


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
        raise RateLimited("429 rate limited", retry_after=retry_after_seconds(resp.headers.get("Retry-After")), status=s)
    if s >= 500:
        raise ServerError(f"{s} server error", status=s)
    body = ""
    try:
        body = resp.text[:240].replace("\n", " ")        # providers explain 4xx errors in the body; keep it so failures are diagnosable
    except Exception:  # noqa: BLE001  (a streaming response that was not read)
        pass
    if s in (400, 402) and re.search(r"credit balance|billing|insufficient (funds|credit)|payment required|quota exceeded", body, re.I):
        raise BillingError(f"provider billing: {body}", status=s)
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


async def request_json_patient(client: httpx.AsyncClient, method: str, url: str, *, sleep=asyncio.sleep,
                               max_wait: float = RATE_LIMIT_MAX_WAIT, **kw: Any) -> Any:
    """request_json, but a 429 that asks for a short wait (Retry-After up to `max_wait`, or no header at all) is waited out and the
    same request is tried ONCE more, so a paged run does not stop halfway. A longer wait, or a second 429, is raised for the job layer."""
    try:
        return await request_json(client, method, url, **kw)
    except RateLimited as exc:
        wait = RATE_LIMIT_DEFAULT_WAIT if exc.retry_after is None else exc.retry_after
        if wait > max_wait:
            raise
        await sleep(wait)
    return await request_json(client, method, url, **kw)
