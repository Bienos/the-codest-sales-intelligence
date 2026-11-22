"""Read models for Today / Account / Opportunity (spec 10). Summary first; evidence drill-down by claim."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .access import Principal, org_scope_sql
from .db import Conn, canonical_json, jb, q1, qa

SECTION_ORDER = ["critical", "existing_accounts", "new_discoveries", "relationships", "deal_blockers", "research_review"]


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
                    "source": r["display_name"]})
    return out


def card(conn: Conn, h: dict[str, Any], now: datetime, last_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    org = q1(conn, "SELECT o.id, o.display_name, o.in_master_list, o.country_code, c.owner_id, c.name AS crm_name FROM organizations o LEFT JOIN crm_organizations c ON c.organization_id=o.id AND c.link_status='linked' WHERE o.id=%s", (h["organization_id"],))
    ini = q1(conn, "SELECT * FROM initiatives WHERE id=%s", (h["initiative_id"],))
    ws = q1(conn, "SELECT kind, scope_statement, scope_confidence FROM workstreams WHERE id=%s", (h["workstream_id"],)) if h["workstream_id"] else None
    act = q1(conn, "SELECT * FROM recommended_actions WHERE hypothesis_id=%s AND status IN ('proposed','accepted')", (h["id"],))
    ev = q1(conn, "SELECT e.summary, e.event_at, e.pub_at, e.first_observed_at, e.last_checked_at, e.verified_at FROM events e JOIN initiative_events ie ON ie.event_id=e.id WHERE ie.initiative_id=%s AND ie.role='evidence' ORDER BY coalesce(e.event_at, e.pub_at) DESC NULLS LAST LIMIT 1", (h["initiative_id"],))
    tender = q1(conn, "SELECT notice_id, deadline_at, deadline_tz, last_status_check_at, status FROM procurement_notices n WHERE n.buyer_org_id=%s AND n.status IN ('open','amended') ORDER BY deadline_at LIMIT 1", (h["organization_id"],)) if h["route_type"] in {"procurement", "direct"} else None
    snap = snapshot(h, act["action_type"] if act else None)
    changes = {k: [last_snapshot.get(k), v] for k, v in snap.items() if last_snapshot and last_snapshot.get(k) != v} if last_snapshot else None
    why = (f"{ws['scope_statement']} ({ws['scope_confidence'].replace('_', ' ')})" if ws else "Workstream not yet established") + \
          f"; route: {h['route_type'].replace('_', ' ')} via {h['counterparty_name']} ({h['access_status']}); external demand: {h['external_demand_status']}."
    review = h["review_json"] or {}
    return {
        "hypothesis_id": str(h["id"]), "priority": h["priority"], "state": h["state"], "score": h["score_total"],
        "account": {"id": str(org["id"]), "name": org["display_name"], "in_master_list": org["in_master_list"], "country": org["country_code"],
                    "crm_owner_id": org["owner_id"], "ownership_verified": org["owner_id"] is not None},
        "initiative": {"id": str(ini["id"]), "title": ini["title"], "official_title": ini["title_is_official"], "stage": ini["stage"], "working_title_label": None if ini["title_is_official"] else "analyst working title"},
        "what_happened": {"summary": ev["summary"] if ev else None, "event_date": ev["event_at"] if ev else None, "published": ev["pub_at"] if ev else None,
                          "first_observed": ev["first_observed_at"] if ev else None, "last_checked": ev["last_checked_at"] if ev else None},
        "why_it_matters": why, "workstream": ws, "external_demand_status": h["external_demand_status"],
        "route": {"type": h["route_type"], "counterparty": h["counterparty_name"], "access_status": h["access_status"]},
        "buyer_status": h["buyer_status"], "evidence_confidence": h["evidence_confidence"], "research_completeness": float(h["research_completeness"]),
        "evidence": evidence_for(conn, h["supporting_claim_ids"]), "counterevidence": review.get("contradicts", [])[:3], "unknowns": [u["unknown"] for u in (h["critical_unknowns"] or [])][:5],
        "blockers": h["blockers"], "next_action": ({k: (str(v) if k.endswith("_id") and v is not None and k != "owner_id" else v) for k, v in act.items() if k in
                                                    {"id", "action_type", "objective", "reason", "owner_id", "owner_note", "target_role", "due_at", "trigger_condition", "approval_required", "expected_outcome", "stop_condition"}} if act else None),
        "tender": ({"notice_id": tender["notice_id"], "deadline_at": tender["deadline_at"], "deadline_tz": tender["deadline_tz"], "last_status_check_at": tender["last_status_check_at"]} if tender else None),
        "changed": changes, "last_checked": h["last_checked_at"], "fingerprint": fingerprint(conn, h),
    }


def section_of(c: dict[str, Any], h: dict[str, Any], conn: Conn) -> str:
    if c["priority"] == "urgent":
        return "critical"
    if h["state"] in {"engaged", "buyer_need_confirmed", "commercially_qualified", "proposal_procurement"} and c["blockers"]:
        return "deal_blockers"
    if q1(conn, "SELECT 1 FROM signals s JOIN initiative_events ie ON ie.event_id=s.event_id WHERE ie.initiative_id=%s AND s.rule_id='REL-03' AND s.status='active'", (h["initiative_id"],)):
        return "relationships"
    return "existing_accounts" if c["account"]["in_master_list"] else "new_discoveries"


def coverage_health(conn: Conn, p: Principal) -> dict[str, Any]:
    rows = qa(conn, "SELECT s.source_id, s.display_name, s.source_family, s.status, h.state, h.last_success_at, h.error_class FROM sources s LEFT JOIN source_health h USING (source_id) "
                    "WHERE s.source_tier <> 'internal' AND s.collection_method <> 'internal'")
    active = [r for r in rows if r["status"] in {"active", "degraded", "failed", "blocked"}]
    bad = [r for r in active if r["state"] in {"degraded", "failed", "blocked"}]
    pending = [r for r in rows if r["status"] == "pending_connector_validation"]
    return {"monitored_sources": len(active), "unhealthy": [{"source": r["display_name"], "family": r["source_family"], "state": r["state"], "error": r["error_class"]} for r in bad],
            "pending_validation_sources": len(pending), "all_monitored_healthy": not bad and len(active) > 0,
            "no_active_monitoring": len(active) == 0}


def today(conn: Conn, p: Principal, now: datetime | None = None, limit: int = 10) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    where, params = org_scope_sql(p, "h.organization_id")
    rows = qa(conn, f"SELECT h.* FROM opportunity_hypotheses h WHERE h.state NOT IN ('disqualified','superseded','expired') AND h.priority IN ('urgent','high','medium') AND ({where}) "  # noqa: S608
                    "ORDER BY CASE h.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END, h.score_total DESC", params)
    cards: list[dict[str, Any]] = []
    for h in rows:
        fp = fingerprint(conn, h)
        us = q1(conn, "SELECT decision, fingerprint, reason FROM hypothesis_user_state WHERE hypothesis_id=%s AND user_id=%s", (h["id"], p.user_id))
        if us and us["fingerprint"] == fp and us["decision"] in {"dismissed", "monitor"}:
            continue        # dismissed/monitored AND nothing material changed => never resurfaces
        # "seen" drives before/after change display and notification dedup ONLY; unanswered items stay in the work queue until the user decides
        last = q1(conn, "SELECT snapshot FROM hypothesis_seen WHERE hypothesis_id=%s AND fingerprint<>%s ORDER BY first_presented_at DESC LIMIT 1", (h["id"], fp))
        if us and us["decision"] == "accepted" and us["fingerprint"] == fp:
            continue        # accepted and unchanged: it is now active work (Opportunity workspace), not a new development
        c = card(conn, h, now, last["snapshot"] if last else None)
        if us and us["decision"] == "dismissed" and us["fingerprint"] != fp:
            c["reopened_after_dismissal"] = {"previous_reason": us["reason"], "note": "material new evidence since you dismissed this"}
        c["section"] = section_of(c, h, conn)
        cards.append(c)
        conn.execute("INSERT INTO hypothesis_seen(hypothesis_id, fingerprint, snapshot) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (h["id"], fp, jb(snapshot(h, c["next_action"]["action_type"] if c["next_action"] else None))))
    cards = sorted(cards, key=lambda c: (SECTION_ORDER.index(c["section"]), {"urgent": 0, "high": 1, "medium": 2}.get(c["priority"], 3)))[:limit]
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
                   f"ORDER BY s.score DESC LIMIT 8", [p.email, *sp])
    return {"generated_at": now, "count": len(cards), "sections": sections, "empty_state": empty, "coverage": health,
            "hot_accounts": [dict(r, id=str(r["id"]), score=float(r["score"]), groups=list(r["groups"] or [])) for r in hot]}
