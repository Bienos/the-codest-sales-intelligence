"""Lemlist client (REST, Basic auth with the API key as the password).

Written from Lemlist's documented API. It has not been called against a live account yet: the Integrations screen has a connection test,
and the first enrolment is the first real proof. Every call is bounded, retried only on 429/5xx, and never logs the key."""
from __future__ import annotations

import time
from urllib.parse import quote
from typing import Any

import httpx

BASE = "https://api.lemlist.com/api"


class LemlistError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class LemlistClient:
    def __init__(self, api_key: str | None, *, http: httpx.Client | None = None, retries: int = 3):
        if not api_key:
            from ..sheets.client import ConnectorBlocked
            raise ConnectorBlocked("LEMLIST_API_KEY not set: Lemlist is not connected")
        self._http = http or httpx.Client(timeout=30)
        self._auth = ("", api_key)
        self._retries = retries

    def _req(self, method: str, path: str, *, retry_server_errors: bool = True, **kw: Any) -> Any:
        """`retry_server_errors=False` for a call that must never run twice (sending an email): a 5xx may mean it was done, so it is
        raised at once. A 429 is always safe to retry: Lemlist refused the request before doing anything."""
        for attempt in range(self._retries + 1):
            r = self._http.request(method, f"{BASE}{path}", auth=self._auth, **kw)
            if r.status_code >= 500 and not retry_server_errors:
                raise LemlistError(f"Lemlist {r.status_code}", r.status_code)
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == self._retries:
                    raise LemlistError(f"Lemlist {r.status_code} after retries", r.status_code)
                time.sleep(min(float(r.headers.get("Retry-After", 2 ** attempt)), 20))
                continue
            if r.status_code in (401, 403):
                raise LemlistError("Lemlist refused the API key", r.status_code)
            if r.status_code >= 400:
                raise LemlistError(f"Lemlist {r.status_code}: {r.text[:200]}", r.status_code)
            return r.json() if r.content else {}
        raise LemlistError("unreachable")

    def team(self) -> dict[str, Any]:
        return self._req("GET", "/team")

    def create_campaign(self, name: str, timezone: str = "Europe/London") -> dict[str, Any]:
        return self._req("POST", "/campaigns", json={"name": name, "timezone": timezone})

    def sequences(self, campaign_id: str) -> Any:
        return self._req("GET", f"/campaigns/{campaign_id}/sequences")

    def add_step(self, campaign_id: str, sequence_id: str, *, subject: str | None, message: str, delay_days: int) -> dict[str, Any]:
        """A follow-up with no subject is sent as a reply in the first email's thread (Lemlist's behaviour); only the first step carries a subject."""
        body: dict[str, Any] = {"type": "email", "delay": delay_days, "message": message}
        if subject:
            body["subject"] = subject
        return self._req("POST", f"/campaigns/{campaign_id}/sequences/{sequence_id}/steps", json=body)

    def leads_by_email(self, email: str) -> list[dict[str, Any]]:
        try:
            result = self._req("GET", f"/leads/{quote(email, safe='@')}", params={"version": "v2"})
        except LemlistError as exc:
            if exc.status == 404:
                return []
            raise
        if not isinstance(result, list) or any(not isinstance(x, dict) for x in result):
            raise LemlistError("Unexpected Lemlist lead lookup response")
        return result

    def add_lead(self, campaign_id: str, email: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self._req("POST", f"/campaigns/{campaign_id}/leads/{quote(email, safe='@')}",
                         params={"deduplicate": "true", "updateStrategy": "fillEmptyOnly"}, json=fields)

    def activities(self, *, offset: int = 0, limit: int = 100, campaign_id: str | None = None) -> list[dict[str, Any]]:
        """Newest-first activity feed (sent, opened, replied, bounced, unsubscribed, interested). Read-only."""
        params: dict[str, Any] = {"offset": offset, "limit": limit, **({"campaignId": campaign_id} if campaign_id else {})}
        result = self._req("GET", "/activities", params=params)
        return result if isinstance(result, list) else []

    def reply_activities(self, *, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        """Newest-first `emailsReplied` activities (GET /activities, version v2, type filter). Each carries the contact, lead and sender ids,
        and (like the webhook payload, which mirrors the activity record) the reply's `text`/`html` when Lemlist includes them. Read-only."""
        result = self._req("GET", "/activities", params={"version": "v2", "type": "emailsReplied", "offset": offset, "limit": limit})
        return [a for a in result if isinstance(a, dict)] if isinstance(result, list) else []

    def contact_messages(self, contact_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Every email in the Lemlist inbox thread with one contact (GET /inbox/{contactId}): our campaign emails and their replies,
        each with `message` (HTML), `subject`, `type` and the sender ids. Read-only; does not mark the conversation as read."""
        result = self._req("GET", f"/inbox/{quote(contact_id, safe='')}", params={"limit": limit})
        rows = result.get("data") if isinstance(result, dict) else result
        return [m for m in rows if isinstance(m, dict)] if isinstance(rows, list) else []

    def send_email(self, *, send_user_id: str, send_user_email: str, send_user_mailbox_id: str, message_html: str,
                   reply_to_activity_id: str, contact_id: str | None = None, lead_id: str | None = None) -> dict[str, Any]:
        """Answer in an existing thread through the Lemlist inbox (POST /inbox/email). `replyToActivityId` threads it (In-Reply-To) and
        reuses the thread's subject and CC; the From address must belong to the sender mailbox. Never retried after a server error."""
        body: dict[str, Any] = {"sendUserId": send_user_id, "sendUserEmail": send_user_email, "sendUserMailboxId": send_user_mailbox_id,
                                "message": message_html, "replyToActivityId": reply_to_activity_id}
        if contact_id:
            body["contactId"] = contact_id
        elif lead_id:
            body["leadId"] = lead_id
        result = self._req("POST", "/inbox/email", json=body, retry_server_errors=False)
        return result if isinstance(result, dict) else {}

    def campaigns(self) -> list[dict[str, Any]]:
        result = self._req("GET", "/campaigns")
        return result if isinstance(result, list) else []

    def create_hook(self, target_url: str, *, event_type: str | None = None) -> dict[str, Any]:
        return self._req("POST", "/hooks", json={"targetUrl": target_url, **({"type": event_type} if event_type else {})})
