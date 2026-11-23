"""Read models for Today / Account / Opportunity (spec 10). Summary first; evidence drill-down by claim."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .access import Principal, org_scope_sql
from .db import Conn, canonical_json, jb, q1, qa
from .market import sector_sql
from .opportunity.model import initiative_tender
from .signals.seller import leading_signals

SECTION_ORDER = ["critical", "existing_accounts", "new_discoveries", "relationships", "deal_blockers", "research_review", "public_tenders"]
PUBLIC_TENDER_LIMIT = 5


def fingerprint(conn: Conn, h: dict[str, Any]) -> str:
    sigs = [str(r["id"]) for r in qa(conn, "SELECT s.id FROM signals s JOIN initiative_events ie ON ie.event_id=s.event_id WHERE ie.initiative_id=%s AND s.status='active' ORDER BY s.id", (h["initiative_id"],))]
    act = q1(conn, "SELECT action_type FROM recommended_actions WHERE hypothesis_id=%s AND status IN ('proposed','accepted')", (h["id"],))
    material = {"state": h["state"], "priority": h["priority"], "gates": {k: v["status"] for k, v in (h["gates"] or {}).items()}, "action": act["action_type"] if act else None,
                "rev": h["initiative_revision"], "signals": sigs, "blockers": sorted(b["type"] for b in (h["blockers"] or []))}
    return hashlib.sha256(canonical_json(material).encode()).hexdigest()[:24]


def snapshot(h: dict[str, Any], action: str | None) -> dict[str, Any]:
    return {"priority": h["priority"], "state": h["state"], "action": action, "demand": h["external_demand_status"], "access": h["access_status"],
            "gates": {k: v["status"] for k, v in (h["gates"] or {}).items()}}


def evidence_for(conn: Conn, claim_ids: list[Any], limit: int = 6) -> list[dict[str, Any]]:
    """Evidence pills: exact source URL, published/retrieved dates, attributed claim, authority tier."""
    if not claim_ids:
        return []
    rows = qa(conn, "SELECT c.id, c.predicate, c.evidence_anchor, c.claim_kind, c.anchor_verified, c.object_json, d.canonical_url, d.published_at, d.fetched_at, d.title, s.source_tier, s.display_name "
                    "FROM claims c LEFT JOIN source_documents d ON d.id=c.source_document_id LEFT JOIN sources s ON s.source_id=d.source_id WHERE c.id = ANY(%s) LIMIT %s",
              ([str(c) for c in claim_ids], limit))
    out = []
    for r in rows:
        url = r["canonical_url"] or (r["object_json"] or {}).get("official_url") or (r["object_json"] or {}).get("url")
        o = r["object_json"] or {}
        shown = r["evidence_anchor"] or (f"{r['predicate'].replace('_', ' ')}: " + ", ".join(f"{k}={v}" for k, v in list(o.items())[:4] if v not in (None, '', [])) if o else r["predicate"].replace("_", " "))
        out.append({"claim_id": str(r["id"]), "claim": shown, "kind": r["claim_kind"], "anchor_verified": r["anchor_verified"], "url": url,
                    "published_at": r["published_at"], "retrieved_at": r["fetched_at"], "authority": r["source_tier"] or ("S1" if (r["object_json"] or {}).get("official_url") else None),
                    "source": r["display_name"], "translation": o.get("anchor_en")})
    return out



def story_sources(conn: Conn, event_ids: list[Any]) -> list[dict[str, Any]]:
    """List distinct source URLs and their tracked origins for a grouped finding."""
    return qa(conn, """
        SELECT url, source, published_at, origin_group FROM (
            SELECT DISTINCT ON (d.canonical_url) d.canonical_url AS url, s.display_name AS source,
                   d.published_at, coalesce(d.independent_origin_group, e.independent_origin_group) AS origin_group,
                   s.source_tier, d.origin_document_id IS NULL AS primary_source
            FROM event_documents ed
            JOIN events e ON e.id=ed.event_id
            JOIN source_documents d ON d.id=ed.document_id
            JOIN sources s ON s.source_id=d.source_id
            WHERE ed.event_id=ANY(%s::uuid[]) AND d.canonical_url ~* '^https?://'
            ORDER BY d.canonical_url, s.source_tier, d.published_at NULLS LAST, d.id
        ) links
        ORDER BY source_tier, primary_source DESC, published_at NULLS LAST, url
        LIMIT 12
        """, (event_ids,))

def card(conn: Conn, h: dict[str, Any], now: datetime, last_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    org = q1(conn, "SELECT o.id, o.display_name, o.in_master_list, o.country_code, c.owner_id, c.name AS crm_name FROM organizations o LEFT JOIN crm_organizations c ON c.organization_id=o.id AND c.link_status='linked' WHERE o.id=%s", (h["organization_id"],))
    ini = q1(conn, "SELECT * FROM initiatives WHERE id=%s", (h["initiative_id"],))
    ws = q1(conn, "SELECT kind, scope_statement, scope_confidence FROM workstreams WHERE id=%s", (h["workstream_id"],)) if h["workstream_id"] else None
    act = q1(conn, "SELECT * FROM recommended_actions WHERE hypothesis_id=%s AND status IN ('proposed','accepted')", (h["id"],))
    ev = q1(conn, "SELECT e.summary, e.event_at, e.pub_at, e.first_observed_at, e.last_checked_at, e.verified_at, e.attrs->'fit' AS fit FROM events e JOIN initiative_events ie ON ie.event_id=e.id WHERE ie.initiative_id=%s AND ie.role='evidence' ORDER BY coalesce(e.event_at, e.pub_at) DESC NULLS LAST LIMIT 1", (h["initiative_id"],))
    tender = initiative_tender(conn, h["initiative_id"], h["organization_id"]) if h["route_type"] in {"procurement", "direct"} else None
    oid = str(org["id"])
    snap = snapshot(h, act["action_type"] if act else None)
    changes = {k: [last_snapshot.get(k), v] for k, v in snap.items() if last_snapshot and last_snapshot.get(k) != v} if last_snapshot else None
    why = (f"{ws['scope_statement']} ({ws['scope_confidence'].replace('_', ' ')})" if ws else "Workstream not yet established") + \
          f"; route: {h['route_type'].replace('_', ' ')} via {h['counterparty_name']} ({h['access_status']}); external demand: {h['external_demand_status']}."
    owner_id = act.get("owner_id") if act else None
    owner = q1(conn, "SELECT min(display_name) AS name FROM users WHERE active AND pipedrive_user_id=%s HAVING count(*)=1", (owner_id,)) if owner_id else None
    review = h["review_json"] or {}
    return {
        "hypothesis_id": str(h["id"]), "priority": h["priority"], "state": h["state"], "score": h["score_total"],
        "account": {"id": oid, "name": org["display_name"], "in_master_list": org["in_master_list"], "country": org["country_code"],
                    "crm_owner_id": org["owner_id"], "ownership_verified": org["owner_id"] is not None},
        "initiative": {"id": str(ini["id"]), "title": ini["title"], "official_title": ini["title_is_official"], "stage": ini["stage"], "working_title_label": None if ini["title_is_official"] else "analyst working title"},
        "what_happened": {"summary": ev["summary"] if ev else None, "event_date": ev["event_at"] if ev else None, "published": ev["pub_at"] if ev else None,
                          "first_observed": ev["first_observed_at"] if ev else None, "last_checked": ev["last_checked_at"] if ev else None},
        "why_it_matters": why, "plain_reason": (ev["fit"] or {}).get("reason") if ev else None, "workstream": ws, "external_demand_status": h["external_demand_status"],
        "route": {"type": h["route_type"], "counterparty": h["counterparty_name"], "access_status": h["access_status"]},
        "buyer_status": h["buyer_status"], "evidence_confidence": h["evidence_confidence"], "research_completeness": float(h["research_completeness"]),
        "evidence": evidence_for(conn, h["supporting_claim_ids"]), "buying_signal": leading_signals(conn, [oid], initiative_id=h["initiative_id"]).get(oid),
        "counterevidence": review.get("contradicts", [])[:3], "unknowns": [u["unknown"] for u in (h["critical_unknowns"] or [])][:5],
        "blockers": h["blockers"], "next_action": ({k: (str(v) if k.endswith("_id") and v is not None and k != "owner_id" else v) for k, v in act.items() if k in
                                                    {"id", "action_type", "objective", "reason", "owner_id", "owner_note", "target_role", "due_at", "trigger_condition", "approval_required", "expected_outcome", "stop_condition"}} | {"owner_name": owner["name"] if owner else None} if act else None),
        "tender": ({"notice_id": tender["notice_id"], "deadline_at": tender["deadline_at"], "deadline_tz": tender["deadline_tz"], "last_status_check_at": tender["last_status_check_at"]} if tender else None),
        "changed": changes, "last_checked": h["last_checked_at"], "fingerprint": fingerprint(conn, h),
    }


def coverage_health(conn: Conn, p: Principal) -> dict[str, Any]:
    rows = qa(conn, "SELECT s.source_id, s.display_name, s.source_family, s.status, h.state, h.last_success_at, h.error_class FROM sources s LEFT JOIN source_health h USING (source_id) "
                    "WHERE s.source_tier <> 'internal' AND s.collection_method <> 'internal'")
    active = [r for r in rows if r["status"] in {"active", "degraded", "failed", "blocked"}]
    bad = [r for r in active if r["state"] in {"degraded", "failed", "blocked"} or r["status"] in {"failed", "blocked"} or r["last_success_at"] is None]
    pending = [r for r in rows if r["status"] == "pending_connector_validation"]
    return {"monitored_sources": len(active), "unhealthy": [{"source": r["display_name"], "family": r["source_family"], "state": r["state"] if r["last_success_at"] else "never_succeeded", "error": r["error_class"]} for r in bad],
            "pending_validation_sources": len(pending), "all_monitored_healthy": not bad and len(active) > 0,
            "no_active_monitoring": len(active) == 0}


def _pre_section(h: dict[str, Any], rel: set[str]) -> str:
    """Determine the group before building a card, so only visible cards are loaded."""
    if h.get("entity_type") == "public_body" or ("entity_type" not in h and h.get("route_type") == "procurement"):
        return "public_tenders"
    if h["priority"] == "urgent":
        return "critical"
    if h["state"] in {"engaged", "buyer_need_confirmed", "commercially_qualified", "proposal_procurement"} and h.get("blockers"):
        return "deal_blockers"
    if str(h["initiative_id"]) in rel:
        return "relationships"
    return "existing_accounts" if h["in_master_list"] else "new_discoveries"


def today(conn: Conn, p: Principal, now: datetime | None = None, limit: int = 10, focus: str = "") -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    where, params = org_scope_sql(p, "h.organization_id")
    sector = sector_sql("o.segment")
    rows = qa(conn, f"SELECT h.*, o.in_master_list, o.entity_type, {sector} AS sector, "
                    "EXISTS (SELECT 1 FROM initiative_events ie JOIN events e ON e.id=ie.event_id "
                    "WHERE ie.initiative_id=h.initiative_id AND ie.role='evidence' AND e.attrs ? 'notice_id') AS public_notice "
                    "FROM opportunity_hypotheses h JOIN organizations o ON o.id=h.organization_id "  # noqa: S608
                    f"WHERE h.state NOT IN ('disqualified','superseded','expired') AND h.priority IN ('urgent','high','medium') AND ({where}) "
                    "ORDER BY CASE h.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END, h.score_total DESC", params)
    rel = {str(r["initiative_id"]) for r in qa(conn, "SELECT DISTINCT ie.initiative_id FROM signals s JOIN initiative_events ie ON ie.event_id=s.event_id WHERE s.rule_id='REL-03' AND s.status='active'")}
    rank = {"urgent": 0, "high": 1, "medium": 2}
    rows.sort(key=lambda h: (SECTION_ORDER.index(_pre_section(h, rel)), rank.get(h["priority"], 3), -(h["score_total"] or 0)))
    states = {str(r["hypothesis_id"]): r for r in qa(conn, "SELECT hypothesis_id, decision, fingerprint, reason FROM hypothesis_user_state WHERE user_id=%s", (p.user_id,))}
    cards: list[dict[str, Any]] = []
    main = tenders = 0
    for h in rows:
        pre = _pre_section(h, rel)
        public = pre == "public_tenders" or h["entity_type"] == "public_body"
        if focus == "finance" and (public or h["sector"] != "finance"):
            continue
        if focus == "other" and (public or h["sector"] == "finance"):
            continue
        if focus == "public":
            if not public:
                continue
            pre = "public_tenders"
        if (pre == "public_tenders" and tenders >= PUBLIC_TENDER_LIMIT) or (pre != "public_tenders" and main >= limit):
            continue
        fp = fingerprint(conn, h)
        us = states.get(str(h["id"]))
        if us and us["fingerprint"] == fp and us["decision"] in {"dismissed", "monitor"}:
            continue        # dismissed/monitored AND nothing material changed => never resurfaces
        if us and us["decision"] == "accepted" and us["fingerprint"] == fp:
            continue        # accepted and unchanged: it is now active work (Opportunity workspace), not a new development
        # "seen" drives before/after change display and notification dedup ONLY; unanswered items stay in the work queue until the user decides
        last = q1(conn, "SELECT snapshot FROM hypothesis_seen WHERE hypothesis_id=%s AND fingerprint<>%s ORDER BY first_presented_at DESC LIMIT 1", (h["id"], fp))
        c = card(conn, h, now, last["snapshot"] if last else None)
        if us and us["decision"] == "dismissed" and us["fingerprint"] != fp:
            c["reopened_after_dismissal"] = {"previous_reason": us["reason"], "note": "material new evidence since you dismissed this"}
        c["section"] = pre
        cards.append(c)
        if c["section"] == "public_tenders":
            tenders += 1
        else:
            main += 1
        conn.execute("INSERT INTO hypothesis_seen(hypothesis_id, fingerprint, snapshot) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (h["id"], fp, jb(snapshot(h, c["next_action"]["action_type"] if c["next_action"] else None))))
    cards = sorted(cards, key=lambda c: (SECTION_ORDER.index(c["section"]), rank.get(c["priority"], 3)))
    health = coverage_health(conn, p)
    empty = None
    if not cards:
        empty = ("No new high-priority changes among successfully monitored sources." if health["all_monitored_healthy"] else
                 "No findings, but monitoring coverage is incomplete - this is NOT confirmation that nothing happened." if health["unhealthy"] or health["no_active_monitoring"] else
                 "No new high-priority changes.")
    sections: dict[str, list] = {k: [] for k in SECTION_ORDER}
    for c in cards:
        sections[c["section"]].append(c)
    sw, sp = org_scope_sql(p, "o.id")
    hot = qa(conn, f"SELECT o.id, o.display_name, o.country_code, o.segment, s.score, s.band, s.groups, s.signal_count, s.last_signal_at, "      # noqa: S608
                   f"EXISTS (SELECT 1 FROM watchlist w WHERE w.organization_id=o.id AND w.user_email=%s) AS watched "
                   f"FROM account_scores s JOIN organizations o ON o.id=s.organization_id WHERE s.band IN ('hot','warm') AND NOT s.stopped AND o.entity_type <> 'public_body' AND ({sw}) "
                   f"AND (%s='' OR (%s='finance' AND ({sector})='finance') OR (%s='other' AND ({sector})<>'finance')) "
                   f"ORDER BY s.score DESC LIMIT 8", [p.email, *sp, focus, focus, focus])
    signals = leading_signals(conn, [str(r["id"]) for r in hot])
    return {"generated_at": now, "count": len(cards), "sections": sections, "empty_state": empty, "coverage": health,
            "hot_accounts": [dict(r, id=str(r["id"]), score=float(r["score"]), groups=list(r["groups"] or []),
                                  buying_signal=signals.get(str(r["id"]))) for r in hot]}
