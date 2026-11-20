"""Enrichment provider adapters (spec 06 §5). No adapter may exceed budget or reach personal-contact data without a
valid approval receipt. Capabilities are DECLARED by the adapter and must be verified against the live account.
STATUS: Apollo adapter is pending_connector_validation (docs-based, unverified; egress blocked in the build sandbox).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from ..approvals import ApprovalError, mark_done, mark_executing, validate_for_execution
from ..budget import release, reserve, settle
from ..db import Conn
from ..sources.http import request_json


@dataclass
class CostEstimate:
    credits: float
    unit: str = "credits"
    known: bool = True       # False when the price list is unknown => treated as 'cannot approve spend'


@dataclass
class PersonCandidate:
    external_id: str
    name: str
    title: str | None
    company: str | None
    linkedin_url: str | None = None


@dataclass
class EnrichedPerson:
    external_id: str
    name: str
    title: str | None
    work_email: str | None
    email_status: str | None
    source: str
    raw: dict[str, Any] = field(default_factory=dict)


class EnrichmentProvider(Protocol):
    provider_id: str

    def capabilities(self) -> dict[str, bool]: ...
    def estimate_cost(self, operation: str) -> CostEstimate: ...
    async def search_people(self, *, company_domain: str, titles: list[str], limit: int = 5) -> list[PersonCandidate]: ...
    async def enrich_person(self, external_id: str) -> EnrichedPerson: ...


class ApolloProvider:
    """People SEARCH returns candidates without contact data; ENRICH reveals contact data and consumes credits."""
    provider_id = "apollo"

    def __init__(self, client: httpx.AsyncClient, api_key: str | None, *, credits_per_enrichment: float | None = None):
        if not api_key:
            from ..sheets.client import ConnectorBlocked
            raise ConnectorBlocked("APOLLO_API_KEY not set: contact enrichment is blocked")
        self.client, self.key, self._cpe = client, api_key, credits_per_enrichment

    def capabilities(self):
        return {"company": True, "people": True, "credits": True}

    def estimate_cost(self, operation: str) -> CostEstimate:
        if operation == "search_people":
            return CostEstimate(0.0)
        # price list must come from the live contract (spec 14 §10); unknown price => spend cannot be approved
        return CostEstimate(self._cpe or 0.0, known=self._cpe is not None)

    async def search_people(self, *, company_domain, titles, limit=5):
        data = await request_json(self.client, "POST", "https://api.apollo.io/api/v1/mixed_people/api_search", headers={"x-api-key": self.key},
                                  json={"q_organization_domains_list": [company_domain], "person_titles": titles, "per_page": limit})
        return [PersonCandidate(str(p["id"]), p.get("name") or f"{p.get('first_name', '')} {p.get('last_name_obfuscated', '')}".strip(), p.get("title"),
                                (p.get("organization") or {}).get("name"), p.get("linkedin_url")) for p in data.get("people", [])]

    async def enrich_person(self, external_id):
        data = await request_json(self.client, "POST", "https://api.apollo.io/api/v1/people/match", headers={"x-api-key": self.key},
                                  json={"id": external_id, "reveal_personal_emails": False})
        p = data.get("person") or {}
        return EnrichedPerson(external_id, p.get("name", ""), p.get("title"), p.get("email"), p.get("email_status"), "apollo", {})


class FakeProvider:
    """Deterministic test double. Records every call so tests can prove no credit was spent without approval."""
    provider_id = "fake"

    def __init__(self, credits: float = 1.0):
        self.calls: list[str] = []
        self._credits = credits

    def capabilities(self):
        return {"company": True, "people": True, "credits": True}

    def estimate_cost(self, operation):
        return CostEstimate(0.0 if operation == "search_people" else self._credits)

    async def search_people(self, *, company_domain, titles, limit=5):
        self.calls.append("search_people")
        return [PersonCandidate("p1", "Synthetic Person", titles[0] if titles else None, "Example Bank")]

    async def enrich_person(self, external_id):
        self.calls.append("enrich_person")
        return EnrichedPerson(external_id, "Synthetic Person", "CTO", "synthetic@example.invalid", "verified", "fake")


def enrich_person_with_approval(conn: Conn, provider: EnrichmentProvider, *, person_external_id: str, person_id: str, approval_id: str | None,
                                period: str, key: str) -> EnrichedPerson:
    """The ONLY path to personal-contact data. Single person, single use, budget reserved first, payload-bound approval."""
    import asyncio
    est = provider.estimate_cost("enrich_person")
    if not est.known:
        raise ApprovalError("provider price unknown: contact credits cannot be approved or spent")
    payload = {"provider": provider.provider_id, "person_external_id": person_external_id, "credits": est.credits}
    validate_for_execution(conn, approval_id, kind="contact_enrichment", target_system=provider.provider_id, target_id=person_id,
                           operation="enrich_person", payload=payload)
    mark_executing(conn, approval_id, key)                      # single-use: a second call with the same approval fails
    reserve(conn, provider.provider_id, "contact_enrichment", period, est.credits, key=f"enrich:{key}", ref_type="person", ref_id=person_id, purpose="approved contact enrichment")
    try:
        result = asyncio.run(provider.enrich_person(person_external_id))
    except Exception as exc:  # noqa: BLE001
        release(conn, provider.provider_id, "contact_enrichment", period, est.credits, key=f"enrich:{key}")
        mark_done(conn, approval_id, False, {"error": type(exc).__name__})
        raise
    settle(conn, provider.provider_id, "contact_enrichment", period, est.credits, est.credits, key=f"enrich:{key}")
    mark_done(conn, approval_id, True, {"external_id": person_external_id})
    return result
