"""Exa discovery adapter (spec 02 §7 market search, 09 §5). Search results are DISCOVERY evidence only: factual
claims need the original source fetched (spec 09 §5). Every call reserves budget first; no key => ConnectorBlocked.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .base import RawItem, SourceDocumentDraft
from .http import request_json
from .util import excerpt, parse_dt, sha, utcnow

EXA_SEARCH = "https://api.exa.ai/search"


@dataclass(frozen=True)
class SearchPlan:
    """Stable plan identity: signal rule + sector + geography + language + source class + recency window."""
    signal_rule_id: str
    sector: str
    geography: str
    language: str
    source_class: str
    recency_days: int
    query: str
    include_domains: tuple[str, ...] = ()

    @property
    def plan_id(self) -> str:
        return sha(self.signal_rule_id, self.sector, self.geography, self.language, self.source_class, self.recency_days, self.query)[:16]


class ExaClient:
    def __init__(self, client: httpx.AsyncClient | None, api_key: str | None, *, est_cost_per_search: float = 0.0, now_fn=utcnow):
        if not api_key:
            from ..sheets.client import ConnectorBlocked
            raise ConnectorBlocked("EXA_API_KEY not set: Exa discovery is blocked (structured sources continue)")
        self.client, self.key, self.est_cost, self.now = client, api_key, est_cost_per_search, now_fn

    async def search(self, plan: SearchPlan, *, num_results: int = 10) -> tuple[list[RawItem], dict[str, Any]]:
        from datetime import timedelta
        body: dict[str, Any] = {"query": plan.query, "numResults": num_results, "type": "auto",
                                "startPublishedDate": (self.now() - timedelta(days=plan.recency_days)).strftime("%Y-%m-%dT00:00:00.000Z")}
        if plan.include_domains:
            body["includeDomains"] = list(plan.include_domains)
        if self.client is None:       # one client per call: an AsyncClient is bound to the event loop that first used it
            async with httpx.AsyncClient(timeout=60) as c:
                data = await request_json(c, "POST", EXA_SEARCH, json=body, headers={"x-api-key": self.key})
        else:
            data = await request_json(self.client, "POST", EXA_SEARCH, json=body, headers={"x-api-key": self.key})
        if not isinstance(data, dict) or "results" not in data:
            from .base import SchemaDrift
            raise SchemaDrift("Exa response has no results")
        items = []
        for rank, r in enumerate(data["results"], start=1):
            if not r.get("url"):
                continue
            items.append(RawItem(external_id=r["url"], version=r.get("publishedDate"),
                                 payload={"title": r.get("title"), "url": r["url"], "published": r.get("publishedDate"),
                                          "snippet": r.get("text") or (r.get("highlights") or [None])[0], "rank": rank,
                                          "plan_id": plan.plan_id, "query": plan.query}, url=r["url"]))
        cost = ((data.get("costDollars") or {}).get("total")) if isinstance(data.get("costDollars"), dict) else None
        return items, {"requests": 1, "reported_cost": cost, "plan_id": plan.plan_id}

    @staticmethod
    def canonicalize(item: RawItem) -> SourceDocumentDraft:
        p = item.payload
        # snippet only: marked as discovery evidence so extraction never treats it as an original source
        return SourceDocumentDraft(p["url"], sha(p["url"], p.get("published")), p["url"], p.get("title"), parse_dt(p.get("published")),
                                   None, excerpt(p.get("snippet")), raw_meta={"discovery_only": True, "plan_id": p["plan_id"],
                                                                              "rank": p["rank"], "query": p["query"]})
