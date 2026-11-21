"""Event -> initiative association (spec 05 §4). Never combines unrelated programmes just because they share a bank."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..audit import audit
from ..db import Conn, jb, q1, qa
from ..events.store import norm_key

LINKABLE = {"programme_announced", "vendor_selected", "implementation_launched", "programme_completed", "programme_delayed",
            "negative_cancellation", "procurement_published", "procurement_amended", "procurement_awarded", "regulatory_change",
            "integration_release"}
STAGE_RANK = {"candidate": 0, "scope_unknown": 0, "announced": 1, "planning": 2, "sourcing": 3, "implementation": 4,
              "transition_or_launch": 5, "completed": 6}
TERMINAL = {"completed", "cancelled"}
WORKSTREAM_FOR_DOMAIN = {
    "core_banking": [("core_banking_customisation", "Core platform customisation and configuration"), ("data_migration", "Legacy data migration and reconciliation"),
                     ("integration_api", "Backend/API integration to surrounding systems")],
    "payments_rail": [("payment_rails", "Payment rail / scheme integration and certification")],
    "cloud_data": [("cloud_migration", "Cloud landing zone / workload migration"), ("data_platform", "Data platform and streaming")],
    "digital_channels": [("digital_channel_build", "Digital channel / onboarding build")],
    "lending": [("lending_platform", "Lending origination/decisioning/servicing build")],
    "fraud_aml": [("fraud_aml_platform", "Fraud / AML platform replacement")],
    "ai_use_case": [("ai_implementation", "AI use-case implementation")],
    "api_platform": [("integration_api", "API / partner platform rebuild")],
}
CAPABILITY_FOR_KIND = {"core_banking_customisation": ["java", "banking_domain"], "data_migration": ["python", "data"], "integration_api": ["api", "java", "typescript"],
                       "payment_rails": ["payments", "iso20022"], "cloud_migration": ["aws", "devops"], "data_platform": ["python", "data"],
                       "digital_channel_build": ["react", "mobile"], "lending_platform": ["java", "banking_domain"], "fraud_aml_platform": ["python", "data"],
                       "ai_implementation": ["python", "llm"]}


@dataclass
class LinkResult:
    initiative_id: str | None
    strength: str | None
    created: bool
    review_opened: bool = False
    reason: str = ""


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _systems(attrs: dict[str, Any]) -> set[str]:
    return {norm_key(str(s)) for s in (attrs.get("named_systems") or []) if s}


def _stage_for(event_type: str, attrs: dict[str, Any]) -> str | None:
    status = attrs.get("programme_status")
    if event_type == "negative_cancellation" or status == "cancelled":
        return "cancelled"
    if status == "paused":
        return "paused"
    if event_type == "programme_completed" or status == "completed":
        return "completed"
    if event_type in {"procurement_published", "procurement_amended"} and attrs.get("notice_status") in {"open", "amended"}:
        return "sourcing"
    if event_type in {"procurement_awarded", "vendor_selected"}:
        return "planning" if status in (None, "announced", "planning") else "implementation"
    if event_type == "implementation_launched" or status == "implementation":
        return "implementation"
    if event_type == "programme_announced":
        return {"planning": "planning"}.get(status, "announced")
    return None


def _candidates(conn: Conn, org_id: str) -> list[dict[str, Any]]:
    return qa(conn, "SELECT * FROM initiatives WHERE owner_org_id=%s AND status='active'", (org_id,))


def _initiative_systems(conn: Conn, init_id: str) -> tuple[set[str], set[str]]:
    r = q1(conn, "SELECT technology_tags FROM initiatives WHERE id=%s", (init_id,))
    vend = {norm_key(x["supplier_name"]) for x in qa(conn, "SELECT supplier_name FROM supplier_edges WHERE initiative_id=%s", (init_id,))}
    return {norm_key(t) for t in (r["technology_tags"] if r else [])}, vend


def _create(conn: Conn, ev: dict[str, Any], actor: str) -> str:
    a = ev["attrs"] or {}
    name = a.get("programme_name")
    domain = a.get("programme_domain") or ("procurement" if ev["event_type"].startswith("procurement") else None)
    if name:
        title, official = name, True
    else:
        label = {"core_banking": "Core banking platform change", "payments_rail": "Payment rail integration", "cloud_data": "Cloud / data platform migration",
                 "ai_use_case": "AI use case", "digital_channels": "Digital channel build", "lending": "Lending platform build",
                 "fraud_aml": "Fraud / AML platform", "api_platform": "API / partner platform", "procurement": "Procurement-led engineering services"}.get(domain or "", "Technology programme")
        title, official = f"{label} (analyst working title)", False       # invented titles are LABELLED as such (spec 05 §5)
    row = conn.execute(
        "INSERT INTO initiatives(owner_org_id, title, title_is_official, programme_domain, technology_tags, aliases, last_material_event_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id", (ev["subject_org_id"], title, official, domain, sorted(_systems(a)), [norm_key(name)] if name else [],
                                                         ev["event_at"] or ev["pub_at"] or datetime.now(timezone.utc))).fetchone()
    iid = str(row["id"])  # type: ignore[index]
    audit(conn, actor=actor, operation="create_initiative", record_type="initiative", record_id=iid, after={"title": title, "event": str(ev["id"])})
    return iid


def link_event(conn: Conn, event_id: str, *, actor: str = "system:initiative-linker") -> LinkResult:
    ev = q1(conn, "SELECT * FROM events WHERE id=%s", (event_id,))
    assert ev
    if q1(conn, "SELECT 1 FROM initiative_events WHERE event_id=%s AND link_strength<>'rejected_link' AND role='evidence'", (event_id,)):
        return LinkResult(None, None, False, reason="already_linked")
    if ev["event_type"] not in LINKABLE or ev["subject_org_id"] is None:
        return LinkResult(None, None, False, reason="not_linkable_or_entity_unresolved")
    if ev["verification_status"] not in {"verified"}:
        return LinkResult(None, None, False, reason="event_not_verified")
    a = ev["attrs"] or {}
    domain = a.get("programme_domain") or ("procurement" if ev["event_type"].startswith("procurement") else None)
    name_n = norm_key(a.get("programme_name")) if a.get("programme_name") else None
    if not domain and not name_n:
        return LinkResult(None, None, False, reason="no_programme_identity")
    systems, vendors = _systems(a), {norm_key(o.get("name", "")) for o in (ev["other_entity_roles"] or []) if o.get("role") in {"vendor", "si", "prime", "technology_provider"}}
    best: tuple[str, str, str, dict[str, Any]] | None = None     # strength, reason, init_id
    near: list[dict[str, Any]] = []
    window = timedelta(days=540)
    ref = ev["event_at"] or ev["pub_at"] or datetime.now(timezone.utc)
    for c in _candidates(conn, str(ev["subject_org_id"])):
        if c["last_material_event_at"] and abs(ref - c["last_material_event_at"]) > window:
            continue
        csys, cvend = _initiative_systems(conn, str(c["id"]))
        if name_n and (name_n == norm_key(c["title"]) or name_n in (c["aliases"] or [])):
            best = ("confirmed_link", "explicit_programme_name", str(c["id"]), c); break
        if c["programme_domain"] and domain and c["programme_domain"] == domain:
            overlap = _jaccard(systems, csys) > 0 or bool(vendors & cvend)
            if overlap and (domain != "ai_use_case" or systems & csys):       # unrelated AI initiatives at one bank stay separate
                if best is None or best[0] != "confirmed_link":
                    best = ("probable_link", "same_domain_with_system_or_vendor_overlap", str(c["id"]), c)
            else:
                near.append(c)
    if best:
        _attach(conn, ev, best[2], best[0], best[1], actor)
        return LinkResult(best[2], best[0], False, reason=best[1])
    iid = _create(conn, ev, actor)
    _attach(conn, ev, iid, "confirmed_link" if name_n else "probable_link", "initiative_created_from_event", actor)
    opened = False
    for c in near:   # same domain, insufficient evidence -> human review, never an automatic merge
        a_id, b_id = sorted([iid, str(c["id"])])
        cur = conn.execute("INSERT INTO initiative_reviews(kind, initiative_a, initiative_b, reason) VALUES ('possible_duplicate_initiative',%s,%s,%s) "
                           "ON CONFLICT DO NOTHING RETURNING id", (a_id, b_id, f"same owner and domain '{domain}' but no named-system/vendor overlap")).fetchone()
        opened = opened or bool(cur)
    return LinkResult(iid, "probable_link", True, review_opened=opened, reason="new_initiative")


def parse_milestone(v) -> str | None:
    """The model may give a full date, a month ("2027-03") or a year. A month means 'by the end of that month'. Anything else is ignored, never guessed."""
    import calendar
    import re
    t = str(v or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?", t)
    if m:
        try:
            from datetime import date
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{4})-(\d{2})", t)
    if m and 1 <= int(m[2]) <= 12:
        return f"{m[1]}-{m[2]}-{calendar.monthrange(int(m[1]), int(m[2]))[1]:02d}"
    m = re.fullmatch(r"(\d{4})", t)
    return f"{m[1]}-12-31" if m else None


def _attach(conn: Conn, ev: dict[str, Any], iid: str, strength: str, reason: str, actor: str) -> None:
    conn.execute("INSERT INTO initiative_events(initiative_id, event_id, link_strength, reason) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                 (iid, ev["id"], strength, reason))
    a = ev["attrs"] or {}
    cur = q1(conn, "SELECT * FROM initiatives WHERE id=%s", (iid,))
    assert cur
    material = False
    new_stage = _stage_for(ev["event_type"], a)
    stage = cur["stage"]
    if new_stage and new_stage != stage:
        order_ok = STAGE_RANK.get(new_stage, 99) > STAGE_RANK.get(stage, 0) or new_stage in {"cancelled", "paused", "completed"}
        reopen = stage in TERMINAL and new_stage not in TERMINAL and new_stage in {"implementation", "sourcing", "planning"}      # needs new official progress
        if (order_ok and stage not in TERMINAL) or reopen:
            conn.execute("INSERT INTO initiative_stage_history(initiative_id, from_stage, to_stage, event_id, actor) VALUES (%s,%s,%s,%s,%s)", (iid, stage, new_stage, ev["id"], actor))
            claim = [r["claim_id"] for r in qa(conn, "SELECT claim_id FROM event_claims WHERE event_id=%s", (ev["id"],))]
            conn.execute("UPDATE initiatives SET stage=%s, stage_evidence_claim_ids=%s, stage_verified_at=now() WHERE id=%s", (new_stage, claim, iid))
            stage, material = new_stage, True
    tags = sorted(set(cur["technology_tags"]) | _systems(a))
    if tags != sorted(cur["technology_tags"]):
        conn.execute("UPDATE initiatives SET technology_tags=%s WHERE id=%s", (tags, iid))
    ms = parse_milestone(a.get("milestone_date"))
    if ms:
        conn.execute("UPDATE initiatives SET next_milestone_at = CASE WHEN next_milestone_at IS NULL OR next_milestone_at < now() THEN %s::timestamptz ELSE LEAST(next_milestone_at, %s::timestamptz) END WHERE id=%s",
                     (ms, ms, iid))
        material = True
    for o in (ev["other_entity_roles"] or []):
        role = o.get("role")
        conn.execute("INSERT INTO initiative_organizations(initiative_id, organization_id, name, role) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (iid, o.get("org_id"), o["name"], role))
        if role in {"vendor", "si", "prime", "technology_provider"}:
            material |= _supplier_edge(conn, ev, iid, o)
    domain = cur["programme_domain"] or a.get("programme_domain")
    if domain in WORKSTREAM_FOR_DOMAIN:
        for kind, text in WORKSTREAM_FOR_DOMAIN[domain]:
            # confirmed scope only when the source states scope; otherwise it is an INFERRED possibility, never a fact
            conf = "confirmed_scope" if a.get("award_scope_includes_dev") and a.get("named_systems") and kind == WORKSTREAM_FOR_DOMAIN[domain][0][0] else \
                   ("implied_engineering_activity" if a.get("named_systems") else "inferred_possible_external")
            conn.execute("INSERT INTO workstreams(initiative_id, kind, scope_statement, scope_confidence, needed_capabilities, owner_org_id) VALUES (%s,%s,%s,%s,%s,%s) "
                         "ON CONFLICT (initiative_id, kind) DO UPDATE SET scope_confidence = CASE WHEN workstreams.scope_confidence='confirmed_scope' THEN 'confirmed_scope' ELSE EXCLUDED.scope_confidence END",
                         (iid, kind, text, conf, CAPABILITY_FOR_KIND.get(kind, []), cur["owner_org_id"]))
    gaps = ["Is integration/specialist work allocated to existing partners?", "Who controls supplier selection for separable workstreams?"]
    if not cur["budget_verified"]:
        gaps.append("Budget is not verified by any cited source (budget_verified=false).")
    conn.execute("UPDATE initiatives SET research_gaps=%s, updated_at=now() WHERE id=%s", (jb(gaps), iid))
    if material:
        conn.execute("UPDATE initiatives SET revision=revision+1, last_material_event_at=COALESCE(%s, now()) WHERE id=%s", (ev["event_at"] or ev["pub_at"], iid))
        from .routes import derive_routes
        derive_routes(conn, iid)


def _supplier_edge(conn: Conn, ev: dict[str, Any], iid: str, o: dict[str, Any]) -> bool:
    a = ev["attrs"] or {}
    claim = q1(conn, "SELECT claim_id FROM event_claims WHERE event_id=%s LIMIT 1", (ev["id"],))
    relation = "selected" if ev["event_type"] in {"vendor_selected", "procurement_awarded"} or a.get("counterparty_selected") else "mentioned"
    scope = "development / integration scope stated" if a.get("award_scope_includes_dev") else "scope not stated"
    cur = conn.execute(
        "INSERT INTO supplier_edges(supplier_org_id, supplier_name, buyer_org_id, initiative_id, supplier_role, scope_text, relation, claim_id, event_id, valid_from) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (buyer_org_id, initiative_id, supplier_name, supplier_role) DO NOTHING RETURNING id",
        (o.get("org_id"), o["name"], ev["subject_org_id"], iid, o["role"], scope, relation, claim["claim_id"] if claim else None, ev["id"], ev["event_at"] or ev["pub_at"])).fetchone()
    return bool(cur)


def link_capacity_evidence(conn: Conn, event_id: str) -> LinkResult:
    """Hiring cluster -> initiative as CAPACITY evidence with a caveat. Never changes demand status (spec 05 §9, scenario F)."""
    ev = q1(conn, "SELECT * FROM events WHERE id=%s", (event_id,))
    assert ev
    if ev["event_type"] not in {"engineering_job_opened", "contractor_job_opened"} or ev["subject_org_id"] is None:
        return LinkResult(None, None, False, reason="not_applicable")
    a = ev["attrs"] or {}
    kws = {norm_key(k) for k in (a.get("tech_keywords") or [])}
    for c in _candidates(conn, str(ev["subject_org_id"])):
        tags = {norm_key(t) for t in c["technology_tags"]}
        title_hit = bool(c["programme_domain"]) and any(w in norm_key(f"{a.get('title', '')} {a.get('team', '')}") for w in {
            "core banking": ["core", "ledger"], "payments rail": ["payment"], "cloud data": ["cloud", "data", "platform"]}.get((c["programme_domain"] or "").replace("_", " "), []))
        if kws & tags or (title_hit and kws):
            conn.execute("INSERT INTO initiative_events(initiative_id, event_id, link_strength, role, reason, caveat) VALUES (%s,%s,'probable_link','capacity_evidence',%s,%s) ON CONFLICT DO NOTHING",
                         (c["id"], event_id, "hiring overlaps initiative technology/team", "Hiring is capacity evidence only: insourcing vs external demand is NOT established"))
            ws = q1(conn, "SELECT id, resource_evidence FROM workstreams WHERE initiative_id=%s ORDER BY created_at LIMIT 1", (c["id"],))
            if ws:
                ev_list = list(ws["resource_evidence"] or []) + [{"event_id": event_id, "title": a.get("title"), "employment_type": a.get("employment_type"),
                                                                  "caveat": "staffing demand unconfirmed; may be insourcing"}]
                conn.execute("UPDATE workstreams SET resource_evidence=%s WHERE id=%s", (jb(ev_list), ws["id"]))
            return LinkResult(str(c["id"]), "probable_link", False, reason="capacity_evidence")
    return LinkResult(None, None, False, reason="no_overlapping_initiative")


def resolve_duplicate(conn: Conn, review_id: str, decision: str, reviewer: str, note: str) -> None:
    """merge: keep both event sets, aliases and audit; kept_separate: dismiss. Reasons are mandatory."""
    if not note.strip():
        raise ValueError("a reviewer reason is required")
    rv = q1(conn, "SELECT * FROM initiative_reviews WHERE id=%s AND status='open'", (review_id,))
    if not rv:
        raise ValueError("review not found or already resolved")
    if decision == "merge":
        keep, drop = rv["initiative_a"], rv["initiative_b"]
        conn.execute("UPDATE initiative_events SET initiative_id=%s WHERE initiative_id=%s AND event_id NOT IN (SELECT event_id FROM initiative_events WHERE initiative_id=%s)", (keep, drop, keep))
        conn.execute("UPDATE initiatives SET status='merged', merged_into=%s, updated_at=now() WHERE id=%s", (keep, drop))
        dt = q1(conn, "SELECT title, technology_tags FROM initiatives WHERE id=%s", (drop,))
        conn.execute("UPDATE initiatives SET aliases = aliases || %s, technology_tags=(SELECT array_agg(DISTINCT x) FROM unnest(technology_tags || %s) x), revision=revision+1 WHERE id=%s",
                     ([norm_key(dt["title"])], dt["technology_tags"], keep))
        new_status = "merged"
    elif decision == "kept_separate":
        new_status = "kept_separate"
    else:
        raise ValueError("decision must be merge|kept_separate")
    conn.execute("UPDATE initiative_reviews SET status=%s, reviewer=%s, note=%s, resolved_at=now() WHERE id=%s", (new_status, reviewer, note, review_id))
    audit(conn, actor=reviewer, operation=f"initiative_{new_status}", record_type="initiative_review", record_id=review_id, detail={"note": note})
