"""Unstructured-document event extraction (spec 03 §6, 11 §8, 12 §4).

Controls: JSON-schema validation (invalid output writes NOTHING); every claim's anchor must appear VERBATIM in the
document or the claim is dropped; an event with no verified claim is dropped; discovery snippets are never treated as
original sources; prompt-injection patterns are flagged and extraction results never trigger actions.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from ..db import Conn, jb, q1
from ..jobs.queue import JobError
from ..sources.http import request_json
from ..sources.util import parse_dt
from .origin import assign_origin
from .store import add_claim, norm_key, store_event

PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "extract_events_v1.txt"
EVENT_TYPES = ["procurement_published", "procurement_amended", "procurement_awarded", "vendor_selected", "implementation_launched",
               "programme_announced", "programme_completed", "integration_release", "product_launch", "engineering_job_opened",
               "contractor_job_opened", "job_closed", "technology_leader_appointed", "acquisition_announced", "funding_announced",
               "supplier_panel_opened", "supplier_panel_closed", "regulatory_change", "deadline_approaching", "crm_buyer_response",
               "relationship_changed", "negative_cancellation", "programme_delayed", "supplier_exit", "market_launch", "licence_granted", "cost_programme"]
INJECTION = re.compile(r"(ignore (all |any |the )?(previous|prior|above) (instructions|prompts)|disregard (the )?(system|previous)|"
                       r"you are now|system prompt|api[ _-]?key|exfiltrat|send (this|the|your) (token|key|secret)|reveal (your|the) (prompt|instructions))", re.I)


class XClaim(BaseModel):
    predicate: str = Field(min_length=1, max_length=120)
    object: dict[str, Any] = Field(default_factory=dict)
    anchor: str = Field(min_length=3, max_length=600)
    anchor_en: str | None = Field(default=None, max_length=900)


class XEntity(BaseModel):
    name: str
    role: str


class XEvent(BaseModel):
    event_type: Literal[tuple(EVENT_TYPES)]  # type: ignore[valid-type]
    subject_name: str = Field(min_length=1)
    event_date: str | None = None
    summary: str = Field(min_length=3, max_length=500)
    attrs: dict[str, Any] = Field(default_factory=dict)
    other_entities: list[XEntity] = Field(default_factory=list)
    claims: list[XClaim] = Field(min_length=1)
    language: str | None = Field(default=None, max_length=8)
    fit: dict[str, Any] | None = None

    @field_validator("event_date")
    @classmethod
    def _iso(cls, v):
        if v is not None and not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            raise ValueError("event_date must be ISO yyyy-mm-dd or null")
        return v


class XResult(BaseModel):
    events: list[XEvent]


class EventExtractor(Protocol):
    version: str

    async def extract(self, *, title: str | None, text: str, published_at: str | None) -> dict[str, Any]: ...


FIT_LEVELS = {"strong", "possible", "none"}
FIT_SERVICES = {"staff_augmentation", "team_extension", "ai_services", "none"}


def clean_fit(fit: dict[str, Any] | None, language: str | None) -> dict[str, Any]:
    """The model's plain-English view of why a finding could mean work for The Codest. Advisory only: it never creates a signal by itself."""
    out: dict[str, Any] = {}
    if language and re.match(r"^[a-z]{2}$", language.strip().lower()):
        out["language"] = language.strip().lower()
    if isinstance(fit, dict):
        level = str(fit.get("level") or "").lower()
        service = str(fit.get("service") or "").lower()
        reason = re.sub(r"\s+", " ", str(fit.get("reason") or "")).strip()[:400]
        if level in FIT_LEVELS:
            out["fit"] = {"level": level, "service": service if service in FIT_SERVICES else "none", "reason": reason}
    return out


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def anchor_in_text(anchor: str, text: str) -> bool:
    return _squash(anchor) in _squash(text)


def parse_events_json(out: str) -> dict:
    """Parse the model's JSON. When the reply was cut off (token limit) or has one broken event, keep every COMPLETE event before the break.
    Nothing is repaired or invented: an event is kept only if it parses whole, and its quote is still checked against the page afterwards."""
    m = re.search(r"\{.*\}", out, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            pass
    i = out.find('"events"')
    j = out.find("[", i) if i >= 0 else -1
    if j < 0:
        raise ValueError("no JSON object in model output")
    dec, pos, events = json.JSONDecoder(), j + 1, []
    while pos < len(out):
        while pos < len(out) and out[pos] in " \n\r\t,":
            pos += 1
        if pos >= len(out) or out[pos] != "{":
            break
        try:
            obj, pos = dec.raw_decode(out, pos)
        except ValueError:
            break
        events.append(obj)
    return {"events": events, "_salvaged": True}


class AnthropicExtractor:
    """Production extractor. Requires ANTHROPIC_API_KEY; without it the integration is blocked (never faked)."""

    def __init__(self, client: httpx.AsyncClient | None, api_key: str | None, model: str = "claude-haiku-5-5", max_tokens: int = 8000):
        if not api_key:
            from ..sheets.client import ConnectorBlocked
            raise ConnectorBlocked("ANTHROPIC_API_KEY not set: LLM extraction is blocked")
        self.client, self.key, self.model, self.max_tokens = client, api_key, model, max_tokens
        self._system = PROMPT_PATH.read_text(encoding="utf-8").format(event_types=", ".join(EVENT_TYPES))
        self.version = f"anthropic:{model}:prompt-{hashlib.sha256(self._system.encode()).hexdigest()[:10]}"
        self.max_tokens_budget = max_tokens
        self.last_usage_tokens = 0
        self.last_tokens_in = self.last_tokens_out = 0

    async def extract(self, *, title, text, published_at):
        user = f"<document title={json.dumps(title or '')} published={json.dumps(published_at)}>\n{text[:30000]}\n</document>"
        payload = {"model": self.model, "max_tokens": self.max_tokens, "system": self._system, "messages": [{"role": "user", "content": user}]}
        hdrs = {"x-api-key": self.key, "anthropic-version": "2023-06-01"}
        if self.client is None:       # one client per call: an AsyncClient is bound to the event loop that first used it
            async with httpx.AsyncClient(timeout=90) as c:
                data = await request_json(c, "POST", "https://api.anthropic.com/v1/messages", headers=hdrs, json=payload)
        else:
            data = await request_json(self.client, "POST", "https://api.anthropic.com/v1/messages", headers=hdrs, json=payload)
        u = data.get("usage") or {}
        self.last_tokens_in, self.last_tokens_out = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
        self.last_usage_tokens = self.last_tokens_in + self.last_tokens_out
        out = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return parse_events_json(out)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def process_document(conn: Conn, doc_id: str, extractor: EventExtractor, *, resolve_org=None, replay: bool = False) -> dict[str, Any]:
    d = q1(conn, "SELECT d.*, s.source_tier, s.retention_policy FROM source_documents d JOIN sources s USING (source_id) WHERE d.id=%s", (doc_id,))
    if not d:
        raise JobError("document not found", "not_found")
    meta = dict(d["raw_meta"] or {})
    if meta.get("discovery_only"):
        # search snippets are discovery evidence only: the original source must be fetched before any factual claim
        conn.execute("UPDATE source_documents SET raw_meta=%s WHERE id=%s", (jb({**meta, "needs_original_fetch": True}), doc_id))
        return {"events": 0, "reason": "discovery_snippet_not_an_original_source"}
    text = d["content_text"] or ""
    if not text.strip():
        return {"events": 0, "reason": "no_text"}
    flags = []
    if INJECTION.search(text):
        flags.append("prompt_injection_suspected")
        conn.execute("INSERT INTO audit_events(actor, operation, record_type, record_id, detail) VALUES ('system:extract','prompt_injection_flag','source_document',%s,%s)",
                     (doc_id, jb({"source_id": d["source_id"]})))
    try:
        raw = _run(extractor.extract(title=d["title"], text=text, published_at=d["published_at"].isoformat() if d["published_at"] else None))
        parsed = XResult.model_validate(raw)
    except (ValidationError, ValueError, KeyError, TypeError) as exc:
        # schema failure: nothing is written, the job is quarantined for review (spec 03 §12)
        raise JobError(f"extraction output failed schema validation: {exc}"[:500], "invalid_payload") from exc
    group = assign_origin(conn, doc_id, text)
    stored, dropped_claims, dropped_events = [], 0, 0
    for ev in parsed.events:
        good = [c for c in ev.claims if anchor_in_text(c.anchor, text)]
        dropped_claims += len(ev.claims) - len(good)
        if not good:
            dropped_events += 1
            continue
        claim_ids = [add_claim(conn, subject=ev.subject_name, predicate=c.predicate, obj={**c.object, **({"anchor_en": c.anchor_en[:900]} if c.anchor_en and c.anchor_en.strip() != c.anchor.strip() else {})}, doc_id=doc_id, anchor=c.anchor[:600],
                               kind="quote", anchor_verified=True, model_version=extractor.version, valid_from=d["published_at"]) for c in good]
        org_id = resolve_org(ev.subject_name) if resolve_org else None
        others = [{"name": o.name, "role": o.role, "org_id": (resolve_org(o.name) if resolve_org else None)} for o in ev.other_entities]
        attrs = {**ev.attrs, "source_tier": d["source_tier"], "flags": flags, **clean_fit(ev.fit, ev.language)}
        dk = ev.attrs.get("dedup_key") or norm_key(ev.summary)
        # same fact in mirrored articles shares the origin group => one event; independent first-party issuers do not
        eid, created = store_event(conn, event_type=ev.event_type, subject_name=ev.subject_name, subject_org_id=org_id, summary=ev.summary, attrs=attrs,
                                   key_parts=(norm_key(str(dk)), group), event_at=parse_dt(ev.event_date), pub_at=d["published_at"], claim_ids=claim_ids,
                                   document_ids=[doc_id], origin_document_id=d["origin_document_id"] or doc_id, origin_group=group,
                                   other_entities=others, model_version=extractor.version, replay=replay)
        stored.append((eid, created))
    if d["retention_policy"] != "full_text":      # licence-restricted sources: purge the transient text after extraction
        conn.execute("UPDATE source_documents SET content_text=NULL, content_purged_at=now() WHERE id=%s", (doc_id,))
    return {"events": len([1 for _, c in stored if c]), "linked_existing": len([1 for _, c in stored if not c]),
            "dropped_unverified_claims": dropped_claims, "dropped_events": dropped_events, "flags": flags, "origin_group": group}
