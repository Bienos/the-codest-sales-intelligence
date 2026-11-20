"""Syndication / duplicate-origin detection (spec 03 §5, 05 §13-E, FR-005).

Different outlets quoting one release are ONE origin. Shingled Jaccard similarity + title equality detect mirrors; the
origin is the first-party (S1-S3) document if present, otherwise the earliest published.
"""
from __future__ import annotations

import re
from datetime import timedelta

from ..db import Conn, qa, q1
from ..sources.util import sha

TIER_RANK = {"S1": 0, "S2": 1, "S3": 2, "S4": 3, "S5": 4, "internal": 5}
THRESHOLD = 0.55


def shingles(text: str, k: int = 5) -> set[str]:
    w = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(w[i:i + k]) for i in range(max(len(w) - k + 1, 1))} if w else set()


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _title_norm(t: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def assign_origin(conn: Conn, doc_id: str, text: str | None = None, window_days: int = 14) -> str:
    """Sets origin_document_id / independent_origin_group on the document; returns the group id."""
    d = q1(conn, "SELECT d.*, s.source_tier FROM source_documents d JOIN sources s USING (source_id) WHERE d.id=%s", (doc_id,))
    assert d
    if d["independent_origin_group"]:
        return d["independent_origin_group"]
    body = text if text is not None else (d["content_text"] or d["excerpt"] or d["title"] or "")
    mine = shingles(body)
    anchor = d["published_at"] or d["fetched_at"]
    cands = qa(conn,
               "SELECT d.id, d.title, d.content_text, d.excerpt, d.published_at, d.fetched_at, d.independent_origin_group, d.origin_document_id, s.source_tier "
               "FROM source_documents d JOIN sources s USING (source_id) WHERE d.id<>%s AND d.independent_origin_group IS NOT NULL "
               "AND coalesce(d.published_at, d.fetched_at) BETWEEN %s AND %s",
               (doc_id, anchor - timedelta(days=window_days), anchor + timedelta(days=window_days)))
    best = None
    for c in cands:
        other = c["content_text"] or c["excerpt"] or c["title"] or ""
        sim = jaccard(mine, shingles(other))
        same_title = bool(d["title"]) and _title_norm(d["title"]) == _title_norm(c["title"])
        if sim >= THRESHOLD or same_title:
            if best is None or sim > best[0]:
                best = (sim, c)
    if best:
        c = best[1]
        group = c["independent_origin_group"]
        origin_id = c["origin_document_id"] or c["id"]
        # a first-party document arriving later still becomes the group's origin
        if TIER_RANK[d["source_tier"]] < TIER_RANK[c["source_tier"]] and TIER_RANK[d["source_tier"]] <= 2:
            conn.execute("UPDATE source_documents SET origin_document_id=%s WHERE independent_origin_group=%s AND id<>%s", (doc_id, group, doc_id))
            origin_id = None
            conn.execute("UPDATE source_documents SET origin_document_id=NULL WHERE id=%s", (doc_id,))
        conn.execute("UPDATE source_documents SET independent_origin_group=%s, origin_document_id=%s WHERE id=%s", (group, origin_id, doc_id))
        return group
    group = f"grp:{sha(doc_id)[:12]}"
    conn.execute("UPDATE source_documents SET independent_origin_group=%s WHERE id=%s", (group, doc_id))
    return group


def independent_group_count(conn: Conn, event_id: str) -> int:
    """Independent origin groups behind an event. Secondary-media-only groups (S4/S5) never exceed their first-party origin."""
    rows = qa(conn,
              "SELECT DISTINCT d.independent_origin_group g FROM event_documents ed JOIN source_documents d ON d.id=ed.document_id "
              "WHERE ed.event_id=%s AND d.independent_origin_group IS NOT NULL", (event_id,))
    groups = {r["g"] for r in rows}
    ev = q1(conn, "SELECT independent_origin_group FROM events WHERE id=%s", (event_id,))
    if ev and ev["independent_origin_group"]:
        groups.add(ev["independent_origin_group"])
    return len(groups)
