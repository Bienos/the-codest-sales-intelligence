"""Rule version sync + signal/negative persistence (spec 03 §6.7-§6.9, §9, §13)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..db import Conn, jb, q1, qa
from ..events.origin import independent_group_count
from .engine import EvalEvent, evaluate, load_rules

LEGACY_FIELDS = ("grp", "signal", "strength", "fresh_for_days", "window_note", "how_to_spot", "who_to_contact",
                 "check_it_is_real", "pairs_best_with", "stop_effect")


def sync_rule_versions(conn: Conn, rules: list[dict[str, Any]] | None = None) -> dict[str, int]:
    """Every legacy ID gets a version row; only rules with validated detectors are active. Never activates silently:
    activation requires fixture coverage (checked by tests/CI and by `assert_fixture_coverage`)."""
    rules = rules if rules is not None else load_rules()
    by_id = {r["id"]: r for r in rules}
    n_active = n_inactive = 0
    for leg in qa(conn, "SELECT * FROM legacy_signals ORDER BY ordinal"):
        r = by_id.get(leg["id"])
        if r:
            conn.execute("INSERT INTO signal_rule_versions(rule_id, version, status, definition, changelog) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                         (leg["id"], r["version"], r["status"], jb(r), "validated detector"))
            n_active += r["status"] == "active"
        else:
            conn.execute("INSERT INTO signal_rule_versions(rule_id, version, status, definition, changelog) VALUES (%s,'0.0.0','defined_not_activated',%s,%s) ON CONFLICT DO NOTHING",
                         (leg["id"], jb({"legacy": {k: leg[k] for k in LEGACY_FIELDS}}), "imported from original tracker; no detector"))
            n_inactive += 1
    return {"active": n_active, "defined_not_activated": n_inactive}


def active_rules(conn: Conn) -> list[dict[str, Any]]:
    return [r["definition"] for r in qa(conn, "SELECT definition FROM signal_rule_versions WHERE status='active' ORDER BY rule_id")]


def to_eval(conn: Conn, ev: dict[str, Any]) -> EvalEvent:
    others = [str(o.get("role", "")).lower() for o in (ev["other_entity_roles"] or [])]
    attrs = dict(ev["attrs"] or {})
    return EvalEvent(event_type=ev["event_type"], attrs=attrs, summary=ev["summary"], pub_at=ev["pub_at"], event_at=ev["event_at"],
                     verification_status=ev["verification_status"], subject_org_id=str(ev["subject_org_id"]) if ev["subject_org_id"] else None,
                     source_tier=attrs.get("source_tier"), other_roles=others, independent_groups=max(independent_group_count(conn, str(ev["id"])), 1),
                     id=str(ev["id"]))


def _cluster_count(conn: Conn, rule: dict[str, Any], ev: dict[str, Any], now: datetime) -> int:
    """How many events of the rule's types the account has in the cluster window. `group_by` without `team` counts across teams;
    `count_matching: true` counts only the events that pass the rule's own match and exclusions (CAP-04: ads in our stacks, not every ad)."""
    c = rule["cluster"]
    by_team = "team" in (c.get("group_by") or ["subject_org_id", "team"])
    where = ("e.event_type = ANY(%s) AND e.verification_status='verified' AND e.subject_org_id IS NOT DISTINCT FROM %s "
             + ("AND coalesce(e.attrs->>'team','') = coalesce(%s,'') " if by_team else "")
             + "AND coalesce(e.event_at, e.pub_at) >= %s - make_interval(days => %s) AND coalesce(e.event_at, e.pub_at) <= coalesce(%s, now()) AND e.lifecycle_status='active'")
    args = [rule["required_event_types"], ev["subject_org_id"], *([(ev["attrs"] or {}).get("team") or ""] if by_team else []), now, c["window_days"],
            ev["event_at"] or ev["pub_at"]]
    if not c.get("count_matching"):
        return q1(conn, f"SELECT count(*) n FROM events e WHERE {where}", args)["n"]  # type: ignore[index]   # noqa: S608
    from .engine import eval_node
    n = 0
    for r in qa(conn, f"SELECT e.event_type, e.attrs, e.summary FROM events e WHERE {where}", args):        # noqa: S608
        cand = EvalEvent(event_type=r["event_type"], attrs=dict(r["attrs"] or {}), summary=r["summary"] or "")
        if eval_node(cand, rule["match"], now) and not any(eval_node(cand, x, now) for x in rule.get("exclusions", [])):
            n += 1
    return n


def evaluate_event(conn: Conn, event_id: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    ev = q1(conn, "SELECT * FROM events WHERE id=%s", (event_id,))
    assert ev
    eev = to_eval(conn, ev)
    claim_ids = [r["claim_id"] for r in qa(conn, "SELECT claim_id FROM event_claims WHERE event_id=%s", (event_id,))]
    groups = [g for g in {ev["independent_origin_group"]} if g]
    out: dict[str, Any] = {"signals": [], "negatives": [], "trace": {}}
    for rule in active_rules(conn):
        cc = _cluster_count(conn, rule, ev, now) if rule["kind"] == "cluster" and rule["required_event_types"] and ev["event_type"] in rule["required_event_types"] else None
        m, trace = evaluate(rule, eev, now, cluster_count=cc)
        out["trace"][rule["id"]] = trace
        if m is None:
            continue
        if m.kind == "signal":
            neg_ids = [n["rule_id"] for n in qa(conn, "SELECT rule_id FROM negative_findings WHERE scope_type='account' AND scope_id=%s AND status='active'",
                                                  (str(ev["subject_org_id"]),))] if ev["subject_org_id"] else []
            row = conn.execute(
                "INSERT INTO signals(event_id, rule_id, rule_version, organization_id, strength, demand_tier, verification_status, effective_from, expires_at, "
                "negative_rule_ids, evidence_claim_ids, independent_origin_groups, reason_code, summary_is_inference, commercial_questions) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (event_id, rule_id, rule_version) DO NOTHING RETURNING id",
                (event_id, m.rule_id, m.version, ev["subject_org_id"], m.strength, m.demand_tier, ev["verification_status"], m.effective_from, m.expires_at,
                 neg_ids, claim_ids, groups, m.reason_code, m.summary_is_inference, jb(m.commercial_questions))).fetchone()
            if row:
                out["signals"].append(str(row["id"]))
        else:
            neg = m.negative or {}
            scope_type = neg["scope_type"]
            org = str(ev["subject_org_id"]) if ev["subject_org_id"] else None
            if org is None:
                out["trace"][rule["id"]] = "negative_entity_unresolved"      # never attach a block to an unknown account
                continue
            scope_id = org if scope_type == "account" else f"{org}:{neg.get('scope_suffix', 'direct')}"
            row = conn.execute(
                "INSERT INTO negative_findings(rule_id, rule_version, scope_type, scope_id, kind, effect, event_id, starts_at, expires_at, detail) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (rule_id, scope_type, scope_id, event_id) DO NOTHING RETURNING id",
                (m.rule_id, m.version, scope_type, scope_id, neg["kind"], neg["effect"], event_id, m.effective_from, m.expires_at,
                 jb({"reason_code": m.reason_code}))).fetchone()
            if row:
                out["negatives"].append(str(row["id"]))
                for prefix in neg.get("expire_related", []):     # NEG-06: expire related PRG/CAP signals now; positives are KEPT, only their status changes
                    dom = (ev["attrs"] or {}).get("programme_domain")
                    conn.execute(
                        "UPDATE signals s SET status='expired' WHERE s.organization_id=%s AND s.status='active' AND s.rule_id LIKE %s AND "
                        "(%s::text IS NULL OR EXISTS (SELECT 1 FROM events e2 WHERE e2.id=s.event_id AND coalesce(e2.attrs->>'programme_domain', %s) = %s))",
                        (org, f"{prefix}-%", dom, dom, dom))
    return out


def sweep_expiry(conn: Conn, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    s = conn.execute("UPDATE signals SET status='expired' WHERE status='active' AND expires_at IS NOT NULL AND expires_at <= %s", (now,)).rowcount
    n = conn.execute("UPDATE negative_findings SET status='expired' WHERE status='active' AND expires_at IS NOT NULL AND expires_at <= %s", (now,)).rowcount
    return {"signals_expired": s, "negatives_expired": n}


def expire_on_job_closed(conn: Conn, job_event_id: str) -> int:
    """Job signals end on vacancy closure (spec 03 §8)."""
    ev = q1(conn, "SELECT attrs FROM events WHERE id=%s AND event_type='job_closed'", (job_event_id,))
    if not ev:
        return 0
    a = ev["attrs"]
    return conn.execute(
        "UPDATE signals s SET status='expired' FROM events e WHERE e.id=s.event_id AND s.status='active' AND s.rule_id IN ('CAP-02','CAP-03','CAP-05','CAP-06') "
        "AND e.attrs->>'provider'=%s AND e.attrs->>'board'=%s AND e.attrs->>'job_id'=%s", (a.get("provider"), a.get("board"), a.get("job_id"))).rowcount


def assert_fixture_coverage(fixtures: list[dict[str, Any]], rules: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Release gate (spec 03 §12): an active rule needs >=3 positive, >=3 negative, >=2 ambiguous, >=1 expiry fixtures."""
    from .engine import MIN_FIXTURES
    counts: dict[str, dict[str, int]] = {}
    for f in fixtures:
        counts.setdefault(f["rule"], {}).setdefault(f["kind"], 0)
        counts[f["rule"]][f["kind"]] += 1
    gaps = {}
    for r in rules:
        if r["status"] != "active":
            continue
        have = counts.get(r["id"], {})
        short = {k: need - have.get(k, 0) for k, need in MIN_FIXTURES.items() if have.get(k, 0) < need}
        if short:
            gaps[r["id"]] = short
    if gaps:
        raise AssertionError(f"active rules lack required fixtures: {gaps}")
    return counts
