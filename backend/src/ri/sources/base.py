"""Connector contract (spec 02 §5) and error taxonomy (spec 02 §6.6, 13 §6)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class ConnectorError(Exception):
    error_class = "transient_network"

    def __init__(self, msg: str, *, retry_after: float | None = None, status: int | None = None):
        super().__init__(msg)
        self.retry_after = retry_after
        self.status = status


class AuthError(ConnectorError): error_class = "auth"
class AccessDenied(ConnectorError): error_class = "access_denied"
class RateLimited(ConnectorError): error_class = "rate_limited"
class TransientError(ConnectorError): error_class = "transient_network"
class ServerError(ConnectorError): error_class = "server_error"
class SchemaDrift(ConnectorError): error_class = "schema_drift"
class NotFound(ConnectorError): error_class = "not_found"
class PolicyBlocked(ConnectorError): error_class = "policy_block"      # robots/licence/ToS: collector stops
class BudgetExceeded(ConnectorError): error_class = "budget_exceeded"
class BillingError(ConnectorError): error_class = "provider_billing"   # the provider account is out of credit: nothing wrong with the work, retry after a top-up
class EgressBlocked(ConnectorError): error_class = "access_denied"     # sandbox/proxy refused the host


@dataclass(frozen=True)
class SourceDescriptor:
    source_id: str
    provider: str
    method: str
    source_tier: str
    version: str


@dataclass
class RawItem:
    external_id: str
    version: str | None                # provider version/etag/updated timestamp when available
    payload: dict[str, Any]
    url: str | None = None


@dataclass
class SourceDocumentDraft:
    external_id: str
    version_key: str
    canonical_url: str | None
    title: str | None
    published_at: datetime | None
    content_text: str | None
    excerpt: str | None
    language: str | None = None
    raw_meta: dict[str, Any] = field(default_factory=dict)
    structured: dict[str, Any] | None = None      # normalised record for procurement/jobs
    kind: str = "document"                        # document | procurement_notice | job_posting
    origin_hint: str | None = None                # URL/ID of the originating document when syndicated


@dataclass
class FetchBatch:
    items: list[RawItem]
    next_cursor: str | None
    has_more: bool
    requested_at: datetime
    source_version: str = ""
    quota_usage: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    partial_result: bool = False
    complete_listing: bool = False       # True only when the batch is an AFFIRMATIVE full listing (job closure semantics)


@dataclass
class HealthCheck:
    ok: bool
    detail: str = ""
    error_class: str | None = None


class SourceConnector(Protocol):
    def describe(self) -> SourceDescriptor: ...
    async def health_check(self) -> HealthCheck: ...
    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch: ...
    async def fetch_by_id(self, external_id: str) -> RawItem | None: ...
    def canonicalize(self, item: RawItem) -> SourceDocumentDraft: ...
