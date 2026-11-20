"""Google Sheets access. READ-ONLY scope only (spec 09 §3, 11 §4). No write method exists on this interface.

If credentials are missing the client raises ConnectorBlocked - we never fabricate a successful sync.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import jwt

SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
API = "https://sheets.googleapis.com/v4/spreadsheets"


class ConnectorBlocked(RuntimeError):
    """Integration cannot run (missing credentials / egress / entitlement). Reported as 'blocked', not failure."""


class SheetsError(RuntimeError):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


@dataclass(frozen=True)
class TabInfo:
    sheet_id: str
    title: str
    row_count: int
    column_count: int
    frozen_rows: int = 0


class SheetsClient(Protocol):
    def get_tabs(self, workbook_id: str) -> list[TabInfo]: ...
    def get_values(self, workbook_id: str, title: str, first_row: int, last_row: int, last_col: int) -> list[list[str]]: ...
    def get_hyperlinks(self, workbook_id: str, title: str, col_index: int, first_row: int, last_row: int) -> dict[int, str]: ...


def col_letter(n: int) -> str:  # 1-based
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


_HYPERLINK = re.compile(r'=HYPERLINK\(\s*"([^"]+)"', re.I)


class GoogleSheetsClient:
    """Service-account (JWT bearer) client. Rate-limit aware: honours 429 Retry-After with bounded retries."""

    def __init__(self, credentials_file: str | None, *, http: httpx.Client | None = None, max_retries: int = 4):
        if not credentials_file:
            raise ConnectorBlocked("GOOGLE_APPLICATION_CREDENTIALS not set: Google Sheets connector is blocked")
        try:
            with open(credentials_file, encoding="utf-8") as fh:
                self._sa = json.load(fh)
        except OSError as exc:
            raise ConnectorBlocked(f"cannot read service-account file: {exc}") from exc
        self._http = http or httpx.Client(timeout=30)
        self._token: tuple[str, float] | None = None
        self._max_retries = max_retries

    def _bearer(self) -> str:
        now = time.time()
        if self._token and self._token[1] - 60 > now:
            return self._token[0]
        claims = {
            "iss": self._sa["client_email"], "scope": SCOPE, "aud": self._sa["token_uri"],
            "iat": int(now), "exp": int(now) + 3300,
        }
        assertion = jwt.encode(claims, self._sa["private_key"], algorithm="RS256")
        r = self._http.post(self._sa["token_uri"], data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
        if r.status_code in (400, 401, 403):
            raise ConnectorBlocked(f"Google token endpoint refused credentials ({r.status_code})")
        r.raise_for_status()
        body = r.json()
        self._token = (body["access_token"], now + int(body.get("expires_in", 3300)))
        return self._token[0]

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            r = self._http.get(url, params=params, headers={"Authorization": f"Bearer {self._bearer()}"})
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == self._max_retries:
                    raise SheetsError(f"Sheets API {r.status_code} after retries", r.status_code)
                time.sleep(min(float(r.headers.get("Retry-After", 2 ** attempt)), 30))
                continue
            if r.status_code in (401, 403):
                raise ConnectorBlocked(f"Sheets API denied access ({r.status_code}): share the workbook with the service account")
            if r.status_code == 404:
                raise SheetsError("workbook not found", 404)
            r.raise_for_status()
            return r.json()
        raise SheetsError("unreachable")  # pragma: no cover

    def get_tabs(self, workbook_id: str) -> list[TabInfo]:
        data = self._get(f"{API}/{workbook_id}", {"fields": "sheets.properties(sheetId,title,gridProperties)"})
        out = []
        for s in data.get("sheets", []):
            p = s["properties"]
            g = p.get("gridProperties", {})
            out.append(TabInfo(str(p["sheetId"]), p["title"], g.get("rowCount", 0), g.get("columnCount", 0), g.get("frozenRowCount", 0)))
        return out

    def get_values(self, workbook_id, title, first_row, last_row, last_col):
        rng = f"'{title}'!A{first_row}:{col_letter(last_col)}{last_row}"
        data = self._get(f"{API}/{workbook_id}/values/{rng}",
                         {"valueRenderOption": "FORMATTED_VALUE", "majorDimension": "ROWS"})
        return [[str(c) for c in row] for row in data.get("values", [])]

    def get_hyperlinks(self, workbook_id, title, col_index, first_row, last_row):
        letter = col_letter(col_index)
        rng = f"'{title}'!{letter}{first_row}:{letter}{last_row}"
        data = self._get(f"{API}/{workbook_id}/values/{rng}", {"valueRenderOption": "FORMULA"})
        out: dict[int, str] = {}
        for i, row in enumerate(data.get("values", [])):
            if row and (m := _HYPERLINK.match(str(row[0]))):
                out[first_row + i] = m.group(1)
        return out


class FixtureSheetsClient:
    """Deterministic in-memory client for tests/offline dev. Data MUST be labelled synthetic by the caller."""

    def __init__(self, workbooks: dict[str, dict[str, list[list[str]]]], links: dict | None = None):
        self.workbooks = workbooks
        self.links = links or {}
        self.calls: list[str] = []

    def get_tabs(self, workbook_id):
        self.calls.append("tabs")
        tabs = self.workbooks.get(workbook_id)
        if tabs is None:
            raise ConnectorBlocked("fixture workbook not found")
        return [TabInfo(str(100 + i), t, len(rows), max((len(r) for r in rows), default=0), 1)
                for i, (t, rows) in enumerate(tabs.items())]

    def get_values(self, workbook_id, title, first_row, last_row, last_col):
        self.calls.append(f"values:{title}:{first_row}-{last_row}")
        rows = self.workbooks[workbook_id][title][first_row - 1:last_row]
        return [list(r[:last_col]) for r in rows]

    def get_hyperlinks(self, workbook_id, title, col_index, first_row, last_row):
        return {r: u for (w, t, c, r), u in self.links.items() if (w, t, c) == (workbook_id, title, col_index)}
