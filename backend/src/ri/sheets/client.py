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

    def __init__(self, credentials_file: str | None, *, http: httpx.Client | None = None, max_retries: int = 4, credentials_json: str | None = None):
        if credentials_json:
            try:
                self._sa = json.loads(credentials_json)
                self._sa["client_email"], self._sa["private_key"], self._sa["token_uri"]      # noqa: B018  (fail early if the key is incomplete)
            except (ValueError, KeyError) as exc:
                raise ConnectorBlocked("GOOGLE_SERVICE_ACCOUNT_JSON is not a complete service-account key") from exc
        elif not credentials_file:
            raise ConnectorBlocked("Google Sheets is not connected: set GOOGLE_SERVICE_ACCOUNT_JSON (or GOOGLE_APPLICATION_CREDENTIALS)")
        else:
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


class XlsxSheetsClient:
    """Reads a Google Sheets .xlsx export (one workbook). Same contract and drift checks as the live API client.

    Used for uploaded exports and the Drive connector. Values are read as displayed text; hyperlinks are taken from cell links.
    """

    def __init__(self, workbook_id: str, source: Any):
        import datetime as _dt

        import openpyxl

        self._wb_id = workbook_id
        self._dt = _dt
        self._xl = openpyxl.load_workbook(source, read_only=False, data_only=True)

    def _ws(self, workbook_id: str, title: str):
        if workbook_id != self._wb_id:
            raise ConnectorBlocked("xlsx snapshot does not contain that workbook")
        return self._xl[title]

    def _text(self, v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, self._dt.datetime):
            return v.date().isoformat() if v.time() == self._dt.time(0) else v.isoformat(sep=" ")
        if isinstance(v, self._dt.date):
            return v.isoformat()
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v).strip()

    def get_tabs(self, workbook_id: str) -> list[TabInfo]:
        if workbook_id != self._wb_id:
            raise ConnectorBlocked("xlsx snapshot does not contain that workbook")
        out = []
        for ws in self._xl.worksheets:
            last = 0
            for r in range(ws.max_row, 0, -1):          # true last non-empty row; exports often carry empty tails
                if any(c.value not in (None, "") for c in ws[r]):
                    last = r
                    break
            out.append(TabInfo(f"xlsx:{ws.title}", ws.title, last, ws.max_column, 1))   # title-keyed: stable if tabs are reordered
        return out

    def get_values(self, workbook_id, title, first_row, last_row, last_col):
        ws = self._ws(workbook_id, title)
        return [[self._text(c.value) for c in row] for row in
                ws.iter_rows(min_row=first_row, max_row=last_row, min_col=1, max_col=last_col)]

    def get_hyperlinks(self, workbook_id, title, col_index, first_row, last_row):
        ws = self._ws(workbook_id, title)
        out: dict[int, str] = {}
        for r in range(first_row, last_row + 1):
            c = ws.cell(row=r, column=col_index)
            if c.hyperlink and c.hyperlink.target:
                out[r] = c.hyperlink.target
            elif isinstance(c.value, str) and (m := _HYPERLINK.match(c.value)):
                out[r] = m.group(1)
        return out


class XlsxValuesClient(XlsxSheetsClient):
    """Streaming, values-only reader for layouts whose tabs need no cell hyperlinks. Only the tabs in `keep` are held in memory
    (as plain values); the others are listed by title only, which is all the importer needs to report them as not imported.
    Full loading of a 30-tab export took ~450 MB, too close to a 512 MB worker; this keeps it to the tabs that are imported."""

    def __init__(self, workbook_id: str, source: Any, keep: set[str]):
        import datetime as _dt

        import openpyxl

        self._wb_id = workbook_id
        self._dt = _dt
        self._rows: dict[str, list[list[str]]] = {}
        self._titles: list[str] = []
        wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                self._titles.append(ws.title)
                if ws.title in keep:
                    rows = [[self._text(v) for v in r] for r in ws.iter_rows(values_only=True)]
                    while rows and not any(rows[-1]):
                        rows.pop()           # exports often carry empty tails
                    self._rows[ws.title] = rows
        finally:
            wb.close()

    def get_tabs(self, workbook_id: str) -> list[TabInfo]:
        if workbook_id != self._wb_id:
            raise ConnectorBlocked("xlsx snapshot does not contain that workbook")
        out = []
        for title in self._titles:
            rows = self._rows.get(title, [])
            out.append(TabInfo(f"xlsx:{title}", title, len(rows), max((len(r) for r in rows), default=0), 1))
        return out

    def get_values(self, workbook_id, title, first_row, last_row, last_col):
        if workbook_id != self._wb_id:
            raise ConnectorBlocked("xlsx snapshot does not contain that workbook")
        rows = self._rows.get(title, [])[first_row - 1:last_row]
        return [(r + [""] * last_col)[:last_col] for r in rows]

    def get_hyperlinks(self, workbook_id, title, col_index, first_row, last_row):
        return {}


def xlsx_client(workbook_key: str, workbook_id: str, data: bytes) -> XlsxSheetsClient:
    """Pick the reader for an uploaded export: the streaming one when the detected layout reads no cell hyperlinks."""
    import io

    import openpyxl

    from . import contracts as C

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
    names = list(wb.sheetnames)
    wb.close()
    cts = C.contracts_for(workbook_key, C.detect_layout(workbook_key, names))
    if any(ct.hyperlink_cols for ct in cts):
        return XlsxSheetsClient(workbook_id, io.BytesIO(data))
    return XlsxValuesClient(workbook_id, io.BytesIO(data), {ct.title for ct in cts})


MAX_XLSX_BYTES = 25 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED = 120 * 1024 * 1024        # full (not read-only) loading is needed for cell hyperlinks, and it costs several times this in memory


def detect_workbook(data: bytes) -> str:
    """Validate an uploaded .xlsx and say which contract workbook it is (A or B). Raises ValueError with a user-safe message."""
    import io
    import zipfile

    if len(data) > MAX_XLSX_BYTES:
        raise ValueError("file is larger than 25 MB")
    if data[:4] != b"PK\x03\x04":
        raise ValueError("not an .xlsx file (export the sheet as Microsoft Excel .xlsx)")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_XLSX_UNCOMPRESSED:
                raise ValueError("workbook expands to an unreasonable size")
            if "xl/workbook.xml" not in z.namelist():
                raise ValueError("not a spreadsheet workbook")
    except zipfile.BadZipFile as exc:
        raise ValueError("corrupt .xlsx file") from exc
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
    names = set(wb.sheetnames)
    wb.close()
    if {"MASTER ACCOUNTS", "BUYING SIGNAL LOG"} <= names or {"MASTER ACCOUNTS", "MASTER CONTACTS", "MASTER BUYING SIGNALS"} <= names:
        return "A"
    if {"Accounts", "Priority Outreach"} <= names:
        return "B"
    raise ValueError("unrecognised workbook: expected the Financial Sector Master List (A) or Codest Outreach Base (B)")
