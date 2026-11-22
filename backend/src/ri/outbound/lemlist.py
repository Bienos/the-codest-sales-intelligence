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

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        for attempt in range(self._retries + 1):
            r = self._http.request(method, f"{BASE}{path}", auth=self._auth, **kw)
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

    def add_lead(self, campaign_id: str, email: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self._req("POST", f"/campaigns/{campaign_id}/leads/{quote(email, safe='@')}", json=fields)

    def create_hook(self, target_url: str, *, event_type: str | None = None) -> dict[str, Any]:
        return self._req("POST", "/hooks", json={"targetUrl": target_url, **({"type": event_type} if event_type else {})})
