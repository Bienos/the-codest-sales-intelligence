"""One-off, read-only probe of the public TED search API: which award fields exist, and how many IT award notices exist for past years.
Run as a job from the server (this sandbox cannot reach TED), so no field name is guessed in the live connector. Results are stored on the job."""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .procurement import TED_URL
from .http import request_json

IT_CPV = "72000000 72200000 72260000 72300000 72500000 72600000 48000000"
CORE_ISO3 = "GBR DEU AUT CHE SWE DNK NOR FIN POL"
AWARDS = "notice-type = can-standard"
RECENT = f"{AWARDS} AND classification-cpv IN ({IT_CPV}) AND publication-date >= today(-90)"
CANDIDATES = [
    "winner-name", "winner-country", "winner-size", "winner-selection-status", "organisation-name-tenderer", "tenderer-name",
    "result-value-lot", "result-value-notice", "total-value", "tender-value", "framework-value-notice", "estimated-value-lot",
    "contract-conclusion-date-lot", "winner-decision-date", "dispatch-date", "contract-end-date",
    "duration-period-value-lot", "duration-period-unit-lot", "duration-start-date-lot", "duration-end-date-lot", "duration-other-lot",
    "renewal-maximum-lot", "renewal-description-lot", "number-of-lots", "buyer-name", "notice-title",
]


def _short(v: Any, n: int = 160) -> str:
    return str(v)[:n]


async def _search(client: httpx.AsyncClient, query: str, fields: list[str]) -> dict[str, Any]:
    return await request_json(client, "POST", TED_URL, json={"query": query, "fields": fields, "page": 1, "limit": 3, "scope": "ALL",
                                                              "paginationMode": "PAGE_NUMBER"}, retries=1)


async def probe(client: httpx.AsyncClient, pause: float = 1.5) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for f in CANDIDATES:
        try:
            d = await _search(client, RECENT, ["publication-number", f])
            vals = [n.get(f) for n in d.get("notices", []) if n.get(f) not in (None, "", [], {})]
            fields[f] = {"ok": True, "returned": len(d.get("notices", [])), "non_empty": len(vals), "sample": _short(vals[0]) if vals else None}
        except Exception as exc:  # noqa: BLE001  (an unknown field is expected to fail: that is the answer)
            fields[f] = {"ok": False, "error": _short(exc, 140)}
        await asyncio.sleep(pause)
    sizes: dict[str, Any] = {}
    for year in (2021, 2022, 2023, 2024):
        for label, extra in (("all_eu", ""), ("core_markets", f" AND buyer-country IN ({CORE_ISO3})")):
            q = f"{AWARDS} AND classification-cpv IN ({IT_CPV}) AND publication-date >= {year}0101 AND publication-date <= {year}1231{extra}"
            try:
                d = await _search(client, q, ["publication-number"])
                sizes[f"{year}_{label}"] = d.get("totalNoticeCount")
            except Exception as exc:  # noqa: BLE001
                sizes[f"{year}_{label}"] = f"error: {_short(exc, 120)}"
            await asyncio.sleep(pause)
    return {"fields": fields, "it_award_notices": sizes, "working_fields": [k for k, v in fields.items() if v.get("ok") and v.get("non_empty")]}


def run_probe() -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as c:
            return await probe(c)
    return asyncio.run(go())
