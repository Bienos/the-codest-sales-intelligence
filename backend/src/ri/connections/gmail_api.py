"""A small, careful Gmail client over httpx: read-only, metadata only.

What it can do (and nothing else):
  * exchange an OAuth code for tokens, refresh an access token from the stored refresh token, revoke a token;
  * `users.messages.list` with a search query (ids only);
  * `users.messages.get?format=metadata` for From, To, Cc, Subject and Date, which also returns Gmail's own short `snippet`.
It never asks for a message body or an attachment, never sends, modifies or deletes anything, and never logs a token, an address or any
message content (the httpx request log is silenced for Google hosts, because its lines carry the search query).

Pacing is polite (a short pause before every call). 429 and 5xx (and Gmail's "rate limit" 403s) are retried with backoff, then reported as
`GmailRateLimited` / `GmailUnavailable` so a sync can stop and carry on next run. A refresh token Google no longer accepts (`invalid_grant`)
is `GmailAuthError`: the connection needs a person to connect Gmail again. Every error message is plain English and contains no secret."""
from __future__ import annotations

import html
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urlencode

import httpx
import jwt

log = logging.getLogger("ri.connections.gmail")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SCOPES = ("openid", "email", GMAIL_SCOPE)          # exactly this: nothing that sends, modifies or deletes mail
METADATA_HEADERS = ("From", "To", "Cc", "Subject", "Date")

TRANSPORT: httpx.BaseTransport | None = None        # tests put a fake Google here (httpx.MockTransport serves sync and async clients)
_sleep = time.sleep                                  # tests replace it to avoid waiting
PACE_S = 0.1                                         # pause before every call to Google
RETRIES = 3                                          # retries after the first try on 429 / 5xx / network trouble (waits 1, 2, 4 seconds unless Google says otherwise)
MAX_WAIT_S = 30.0
RATE_REASONS = {"ratelimitexceeded", "userratelimitexceeded", "dailylimitexceeded", "quotaexceeded", "backenderror"}


class _QuietGoogle(logging.Filter):
    """httpx logs every request line at INFO, URL included; a Gmail search URL carries our contacts' addresses. Keep Google calls out of the log."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "googleapis.com" not in msg and "accounts.google.com" not in msg


logging.getLogger("httpx").addFilter(_QuietGoogle())


class GmailError(Exception):
    """Base class. The message is plain English, safe to show; `reason` is Google's short code (for example invalid_grant) when it gave one."""

    def __init__(self, message: str, reason: str = ""):
        super().__init__(message)
        self.reason = reason


class GmailAuthError(GmailError):
    """Google no longer accepts this permission (revoked, expired, password reset...): a person must connect Gmail again."""


class GmailConfigError(GmailError):
    """The server side is not set up right (client id or secret rejected, Gmail API switched off, scope not granted): a person or administrator must look."""


class GmailRateLimited(GmailError):
    """Still limited after the retries: stop now, carry on next run."""


class GmailUnavailable(GmailError):
    """Google could not be reached or kept failing: try again next run."""


class GmailNotFound(GmailError):
    """The message was deleted between listing it and reading it."""


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0), transport=TRANSPORT, follow_redirects=False)


def _aclient() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0), transport=TRANSPORT, follow_redirects=False)


def _reason(r: httpx.Response) -> str:
    """Google's short error code from either error shape (OAuth: {"error": "invalid_grant"}; API: {"error": {"errors": [{"reason": ...}]}})."""
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        return ""
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, str):
        return err[:60]
    if isinstance(err, dict):
        errs = err.get("errors")
        if isinstance(errs, list) and errs and isinstance(errs[0], dict) and errs[0].get("reason"):
            return str(errs[0]["reason"])[:60]
        return str(err.get("status") or "")[:60]
    return ""


def _wait(attempt: int, retry_after: str | None) -> float:
    try:
        if retry_after:
            return max(0.0, min(float(retry_after), MAX_WAIT_S))
    except ValueError:
        pass
    return min(2.0 ** attempt, MAX_WAIT_S)


def _pace() -> None:
    if PACE_S > 0:
        _sleep(PACE_S)


def _retryable(r: httpx.Response) -> bool:
    if r.status_code == 429 or r.status_code >= 500:
        return True
    return r.status_code == 403 and _reason(r).lower() in RATE_REASONS


# ---------------------------------------------------------------- OAuth: code exchange, account address, revoke
def consent_url(client_id: str, redirect_uri: str, state: str, login_hint: str | None = None) -> str:
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
              "state": state}
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str, *, client_id: str, client_secret: str, redirect_uri: str) -> dict[str, Any]:
    """Swap the one-time code for tokens. Tried once only: a code can be used once, so a blind retry could not help."""
    _pace()
    try:
        with _client() as http:
            r = http.post(TOKEN_URL, data={"code": code, "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    except httpx.HTTPError:
        raise GmailUnavailable("Google could not be reached.") from None
    if r.status_code >= 500 or r.status_code == 429:
        raise GmailUnavailable("Google could not be reached.", str(r.status_code))
    if r.status_code != 200:
        reason = _reason(r)
        raise GmailAuthError("Google refused the code.", reason)
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        raise GmailAuthError("Google sent an answer we could not read.") from None
    if not isinstance(body, dict):
        raise GmailAuthError("Google sent an answer we could not read.")
    return body


def account_address(tokens: dict[str, Any], client_id: str) -> str | None:
    """The Google account's address: from the ID token (received straight from Google's token endpoint over TLS, so reading it without a
    signature check is allowed by the OpenID rules), else from the userinfo endpoint. None when Google gives neither."""
    idt = tokens.get("id_token")
    if isinstance(idt, str) and idt:
        try:
            claims = jwt.decode(idt, options={"verify_signature": False})
            if claims.get("aud") == client_id and claims.get("email_verified") is not False and "@" in str(claims.get("email") or ""):
                return str(claims["email"]).strip().lower()
        except Exception:  # noqa: BLE001
            pass
    access = tokens.get("access_token")
    if isinstance(access, str) and access:
        _pace()
        try:
            with _client() as http:
                r = http.get(USERINFO_URL, headers={"Authorization": f"Bearer {access}"})
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and "@" in str(data.get("email") or "") and data.get("email_verified") is not False:
                    return str(data["email"]).strip().lower()
        except Exception:  # noqa: BLE001
            return None
    return None


def revoke_token(token: str) -> bool:
    """Best effort: tell Google to forget this refresh token. True when Google confirmed; never raises (a failure must not stop a disconnect)."""
    if not token:
        return False
    try:
        _pace()
        with _client() as http:
            r = http.post(REVOKE_URL, data={"token": token}, headers={"Content-Type": "application/x-www-form-urlencoded"})
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


async def revoke_token_async(token: str) -> bool:
    """Async twin of `revoke_token` for async routes. Same promise: best effort, never raises."""
    if not token:
        return False
    try:
        async with _aclient() as http:
            r = await http.post(REVOKE_URL, data={"token": token}, headers={"Content-Type": "application/x-www-form-urlencoded"})
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- messages
@dataclass(frozen=True)
class MessageMeta:
    """What a metadata read gives us. No body, no attachments."""
    id: str
    thread_id: str | None
    occurred_at: datetime
    snippet: str
    subject: str
    sender: tuple[str, ...]
    to: tuple[str, ...]
    cc: tuple[str, ...]
    labels: tuple[str, ...]


def _addresses(values: list[str]) -> tuple[str, ...]:
    out: list[str] = []
    for _, addr in getaddresses(values):
        a = addr.strip().lower()
        if a.count("@") == 1 and " " not in a and a not in out:
            out.append(a)
    return tuple(out)


def _text(value: str) -> str:
    try:
        value = str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        pass
    return " ".join(value.split())


def parse_metadata(body: dict[str, Any]) -> MessageMeta:
    """Raises ValueError for a payload with no id. Everything else is read tolerantly."""
    mid = body.get("id")
    if not isinstance(mid, str) or not mid:
        raise ValueError("message without an id")
    headers: dict[str, list[str]] = {}
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
    for h in payload.get("headers") or []:
        if isinstance(h, dict) and isinstance(h.get("name"), str) and isinstance(h.get("value"), str):
            headers.setdefault(h["name"].lower(), []).append(h["value"])
    when: datetime | None = None
    try:
        when = datetime.fromtimestamp(int(body["internalDate"]) / 1000, tz=timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        pass
    if when is None:
        try:
            when = parsedate_to_datetime((headers.get("date") or [""])[0])
            when = (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        except (TypeError, ValueError, IndexError):
            when = datetime.now(timezone.utc)
    labels = tuple(str(x) for x in (body.get("labelIds") or []) if isinstance(x, str))
    return MessageMeta(id=mid, thread_id=body.get("threadId") if isinstance(body.get("threadId"), str) else None, occurred_at=when,
                       snippet=" ".join(html.unescape(str(body.get("snippet") or "")).split()),
                       subject=_text((headers.get("subject") or [""])[0]), sender=_addresses(headers.get("from", [])), to=_addresses(headers.get("to", [])),
                       cc=_addresses(headers.get("cc", [])), labels=labels)


class GmailClient:
    """One person's mailbox, read-only. Use as a context manager. The refresh token lives only in this object and is never printed."""

    def __init__(self, refresh_token: str, client_id: str, client_secret: str):
        self._refresh, self._cid, self._secret = refresh_token, client_id, client_secret
        self._http = _client()
        self._access: str | None = None
        self._expires = 0.0
        self.calls = 0

    def __repr__(self) -> str:
        return "GmailClient(<hidden>)"

    def __enter__(self) -> GmailClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()
        self._access = None

    # ---- tokens
    def _token(self) -> str:
        if self._access and time.monotonic() < self._expires - 60:
            return self._access
        r = self._send("POST", TOKEN_URL, authed=False, data={"client_id": self._cid, "client_secret": self._secret, "refresh_token": self._refresh, "grant_type": "refresh_token"})
        if r.status_code == 200:
            try:
                body = r.json()
                self._access = str(body["access_token"])
                self._expires = time.monotonic() + float(body.get("expires_in") or 3000)
                return self._access
            except (KeyError, ValueError, TypeError):
                raise GmailUnavailable("Google sent an answer we could not read.") from None
        reason = _reason(r)
        if reason == "invalid_grant" or r.status_code == 400:
            raise GmailAuthError("Google no longer accepts this connection (access was removed, expired or the password changed).", reason or "invalid_grant")
        if reason in ("invalid_client", "unauthorized_client", "access_denied") or r.status_code in (401, 403):
            raise GmailConfigError("Google did not accept this app's sign-in settings (client id or secret). An administrator needs to check them.", reason)
        raise GmailUnavailable("Google refused to renew the access for now.", reason)

    # ---- one call, with pacing, the one allowed token refresh on 401, and backoff on 429 / 5xx / network trouble
    def _send(self, method: str, url: str, *, authed: bool = True, **kw: Any) -> httpx.Response:
        attempt, renewed = 0, False
        while True:
            headers = {"Accept": "application/json"}
            if authed:
                headers["Authorization"] = f"Bearer {self._token()}"
            _pace()
            self.calls += 1
            try:
                r = self._http.request(method, url, headers=headers, **kw)
            except httpx.TransportError:
                if attempt >= RETRIES:
                    raise GmailUnavailable("Google could not be reached.") from None
                _sleep(_wait(attempt, None))
                attempt += 1
                continue
            if authed and r.status_code == 401:
                if renewed:
                    raise GmailAuthError("Google no longer accepts this connection.", "unauthorized")
                self._access, renewed = None, True
                continue
            if _retryable(r):
                if attempt >= RETRIES:
                    if r.status_code >= 500:
                        raise GmailUnavailable("Google is having trouble right now.", str(r.status_code))
                    raise GmailRateLimited("Google asked us to slow down.", _reason(r) or "429")
                _sleep(_wait(attempt, r.headers.get("Retry-After")))
                attempt += 1
                continue
            return r

    def _json(self, r: httpx.Response) -> dict[str, Any]:
        if r.status_code == 200:
            try:
                body = r.json()
            except ValueError:
                raise GmailUnavailable("Google sent an answer we could not read.") from None
            if isinstance(body, dict):
                return body
            raise GmailUnavailable("Google sent an answer we could not read.")
        reason = _reason(r)
        if r.status_code == 404:
            raise GmailNotFound("That message is gone.", reason)
        if r.status_code == 403 and reason in ("accessNotConfigured", "SERVICE_DISABLED"):
            raise GmailConfigError("The Gmail API is not switched on for the company's Google project. An administrator needs to switch it on.", reason)
        if r.status_code in (401, 403):
            raise GmailConfigError("Google did not allow reading this mailbox. Connect Gmail again and allow read access.", reason)
        raise GmailError(f"Google did not accept the request ({r.status_code}).", reason)

    # ---- the two reads
    def list_message_ids(self, q: str, *, page_size: int = 100, max_pages: int = 10) -> tuple[list[str], bool]:
        """Message ids matching a Gmail search, newest first (spam and trash are left out by Gmail). Second value: True when more pages exist beyond `max_pages`."""
        ids: list[str] = []
        token: str | None = None
        for _ in range(max_pages):
            params: dict[str, Any] = {"q": q, "maxResults": page_size, "fields": "messages/id,nextPageToken"}
            if token:
                params["pageToken"] = token
            body = self._json(self._send("GET", f"{API}/messages", params=params))
            ids += [m["id"] for m in body.get("messages") or [] if isinstance(m, dict) and isinstance(m.get("id"), str)]
            token = body.get("nextPageToken") if isinstance(body.get("nextPageToken"), str) else None
            if not token:
                return ids, False
        return ids, True

    def get_metadata(self, message_id: str) -> MessageMeta:
        """Headers and Gmail's snippet only (format=metadata): the body and attachments are never requested."""
        params = [("format", "metadata"), *[("metadataHeaders", h) for h in METADATA_HEADERS], ("fields", "id,threadId,labelIds,snippet,internalDate,payload/headers")]
        return parse_metadata(self._json(self._send("GET", f"{API}/messages/{quote(message_id, safe='')}", params=params)))
