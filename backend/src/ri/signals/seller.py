"""Evidence-backed signal summaries for the account worklist."""
from __future__ import annotations

from typing import Any

from ..db import Conn, qa


def leading_signals(conn: Conn, organization_ids: list[str], *, initiative_id: str | None = None, commercial_only: bool = False) -> dict[str, dict[str, Any]]:
    """One current signal per account, prioritising evidence of outside demand.

    A signal, its event and its supporting claim must all belong to the same
    chain. An old score or a document that merely mentions the buyer is not proof.
    """
    if not organization_ids:
        return {}
    rows = qa(conn, """
        SELECT DISTINCT ON (s.organization_id) s.organization_id::text AS organization_id,
               s.rule_id, l.signal AS label, s.demand_tier, s.commercial_questions,
               e.id::text AS event_id, e.summary, e.event_at AS happened_at,
               coalesce(sd.published_at, e.pub_at) AS published_at,
               c.evidence_anchor AS excerpt, evidence.url AS source_url,
               src.display_name AS source_name
        FROM signals s
        JOIN events e ON e.id=s.event_id AND e.subject_org_id=s.organization_id
        JOIN legacy_signals l ON l.id=s.rule_id
        JOIN event_claims ec ON ec.event_id=e.id AND ec.claim_id=ANY(s.evidence_claim_ids)
        JOIN claims c ON c.id=ec.claim_id
        LEFT JOIN source_documents sd ON sd.id=c.source_document_id
        LEFT JOIN sources src ON src.source_id=sd.source_id
        CROSS JOIN LATERAL (
            SELECT coalesce(sd.canonical_url,c.object_json->>'official_url',c.object_json->>'url') AS url
        ) evidence
        WHERE s.organization_id=ANY(%s::uuid[]) AND s.status='active'
          AND (s.expires_at IS NULL OR s.expires_at>now())
          AND s.verification_status='verified' AND e.verification_status='verified'
          AND e.lifecycle_status='active' AND NOT e.is_historical_replay
          AND (NOT %s OR e.lane <> 'public')
          AND c.status='supported' AND c.anchor_verified AND c.claim_kind IN ('quote','structured')
          AND (c.valid_to IS NULL OR c.valid_to>now())
          AND evidence.url ~* '^https?://'
          AND (%s::uuid IS NULL OR EXISTS (
              SELECT 1 FROM initiative_events ie WHERE ie.event_id=e.id AND ie.initiative_id=%s AND ie.role='evidence'
          ))
        ORDER BY s.organization_id, s.demand_tier, s.effective_from DESC, s.strength DESC,
                 CASE src.source_tier WHEN 'S1' THEN 1 WHEN 'S2' THEN 2 WHEN 'S3' THEN 3
                                      WHEN 'S4' THEN 4 ELSE 5 END, s.id, c.id
        """, (organization_ids, commercial_only, initiative_id, initiative_id))
    from ..outbound.signal_campaigns import ANGLES, DEFAULT_ANGLE
    for row in rows:
        row["meaning"] = ANGLES.get(row["rule_id"], DEFAULT_ANGLE)        # why this kind of news tends to mean engineering work
    return {row.pop("organization_id"): row for row in rows}
