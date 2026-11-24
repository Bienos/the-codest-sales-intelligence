"""Promote invitations on a buyer's own site using only date and scope text we can verify."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

from ..db import q1
from ..ingest.pipeline import upsert_notice
from ..sources.util import parse_dt, sha, tz_of
from .classify import services_relevant
from .extract import anchor_in_text

_DEADLINE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})(?!\d)")
_CLOSED = re.compile(r"\b(cancelled|canceled|awarded|closed|uniewa[zż]nion\w*|zako[nń]czon\w*|aufgehoben|abgeschlossen|avbruten)\b", re.I)
_CALL = re.compile(r"\b(bids?|proposals?|tenders?|submit|submission|deadline|ang[eé]bot\w*|frist|ofert\w*|termin|anbud\w*|tilbud\w*)\b", re.I)


def record_buyer_notice(conn, document: dict, event, buyer_id: str, text: str) -> dict | None:
    quote = event.attrs.get("deadline_quote")
    scope = event.attrs.get("scope_quote")
    if not isinstance(quote, str) or not isinstance(scope, str) or len(quote) > 600 or len(scope) > 600:
        return None
    if not _CALL.search(quote) or _CLOSED.search(quote) or not anchor_in_text(quote, text) or not anchor_in_text(scope, text):
        return None
    matches = set(_DEADLINE.findall(quote))
    if len(matches) != 1:
        return None
    raw, = matches
    deadline = parse_dt(raw)
    if not deadline or deadline <= datetime.now(UTC) or not services_relevant(scope, None, []):
        return None
    buyer = q1(conn, "SELECT display_name, canonical_domain, country_code FROM organizations WHERE id=%s", (buyer_id,))
    url = urlsplit(document["canonical_url"] or "")
    site = (url.hostname or "").lower()
    domain = ((buyer or {}).get("canonical_domain") or "").lower().removeprefix("www.")
    if url.scheme != "https" or not domain or not (site == domain or site.endswith("." + domain)):
        return None
    if not anchor_in_text(event.subject_name, text):
        return None
    reference = event.attrs.get("notice_reference")
    if not isinstance(reference, str) or not 3 <= len(reference) <= 120 or not anchor_in_text(reference, text):
        return None
    notice_id = sha(buyer_id, reference.casefold().strip())[:24]
    n = {"notice_id": notice_id, "buyer_name": buyer["display_name"], "title": document["title"] or scope,
         "scope": scope, "country": buyer["country_code"], "published_at": document["published_at"],
         "deadline_at": deadline, "deadline_tz": tz_of(raw), "status": "open", "official_url": url.geturl()}
    upsert_notice(conn, document["source_id"], n, str(document["id"]), buyer_id)
    return {"notice_id": notice_id, "notice_status": "open", "submission_deadline": deadline.isoformat(),
            "deadline_tz": n["deadline_tz"], "authoritative_url": n["official_url"],
            "relevant_services": True, "buyer_entity": buyer["display_name"]}
