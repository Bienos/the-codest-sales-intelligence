"""Deterministic signal rule evaluation (spec 03 §6). Pure functions: no DB, no AI, replayable.

Order: event type -> source tier -> verification -> required fields -> positive predicate -> exclusions -> freshness/expiry
-> demand tier. Model confidence is never an input. Output is a Match or None (+ a trace explaining why).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

RULES_PATH = Path(__file__).resolve().parents[3] / "rules" / "signal_rules.yaml"
MIN_FIXTURES = {"positive": 3, "negative": 3, "ambiguous": 2, "expiry": 1}
VER_ORDER = {"failed": 0, "stale": 0, "unverified": 1, "verified": 2}
TIER_ORDER = {"D1": 1, "D2": 2, "D3": 3, "D4": 4}


@dataclass
class EvalEvent:
    event_type: str
    attrs: dict[str, Any]
    summary: str = ""
    pub_at: datetime | None = None
    event_at: datetime | None = None
    verification_status: str = "verified"
    subject_org_id: str | None = None
    source_tier: str | None = None
    other_roles: list[str] = field(default_factory=list)
    independent_groups: int = 1
    id: str | None = None


@dataclass
class Match:
    rule_id: str
    version: str
    kind: str                         # signal | negative
    reason_code: str
    strength: int = 0
    demand_tier: str | None = None
    effective_from: datetime | None = None
    expires_at: datetime | None = None
    commercial_questions: list[str] = field(default_factory=list)
    negative: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)
    summary_is_inference: bool = False


def load_rules(path: Path = RULES_PATH) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["rules"]


def _val(ev: EvalEvent, name: str) -> Any:
    if name == "summary":
        return ev.summary
    if name == "event_type":
        return ev.event_type
    if name == "other_roles":
        return ev.other_roles
    return ev.attrs.get(name)


def _days_since(v: Any, now: datetime) -> int | None:
    if not v:
        return None
    try:
        d = date.fromisoformat(str(v)[:10])
    except ValueError:
        return None
    return (now.date() - d).days


def _leaf(ev: EvalEvent, node: dict[str, Any], now: datetime) -> bool:
    v = _val(ev, node["attr"])
    for op, arg in node.items():
        if op == "attr":
            continue
        if op == "eq":
            return v == arg
        if op == "in":
            return v in arg
        if op == "not_in":
            return v not in arg
        if op == "gte":
            return v is not None and v >= arg
        if op == "lte":
            return v is not None and v <= arg
        if op == "exists":
            present = v not in (None, "", [], {})
            return present if arg else not present
        if op == "is_true":
            return (v is True) == bool(arg)
        if op == "is_false":
            return v is False
        if op == "contains_any":
            hay = [str(x).lower() for x in v] if isinstance(v, list) else [str(v or "").lower()]
            return any(re.search(rf"(?<![a-z0-9]){re.escape(str(a).lower())}(?![a-z0-9])", h) for h in hay for a in arg)
        if op == "days_since_in":
            d = _days_since(v, now)
            return d is not None and arg[0] <= d <= arg[1]
        raise ValueError(f"unknown predicate op {op}")
    return False


def eval_node(ev: EvalEvent, node: dict[str, Any], now: datetime) -> bool:
    if "all" in node:
        return all(eval_node(ev, n, now) for n in node["all"])
    if "any" in node:
        return any(eval_node(ev, n, now) for n in node["any"])
    if "not" in node:
        return not eval_node(ev, node["not"], now)
    return _leaf(ev, node, now)


def _dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def compute_expiry(rule: dict[str, Any], ev: EvalEvent, now: datetime) -> tuple[datetime | None, list[str]]:
    ex = rule.get("expiry") or {"mode": "none"}
    ref = ev.event_at or ev.pub_at or now
    notes = [] if ev.event_at else ["event_date_unknown_publication_date_used_for_freshness_only"]
    m = ex["mode"]
    if m == "official_close":
        return _dt(ev.attrs.get(ex["field"])), notes            # tender signals expire on official close, not arbitrary days
    if m in {"days", "vacancy_closure"}:
        return ref + timedelta(days=ex["days"]), notes
    if m == "date_field_window":
        start = ev.attrs.get(ex["field"])
        base = _dt(start) or (datetime.fromisoformat(start).replace(tzinfo=timezone.utc) if start else ref)
        return base + timedelta(days=ex["days"]), notes
    return None, notes


def evaluate(rule: dict[str, Any], ev: EvalEvent, now: datetime, *, cluster_count: int | None = None) -> tuple[Match | None, str]:
    """Returns (match, trace). `trace` is the first failing step (for audit / 'why not' views)."""
    if rule["status"] != "active":
        return None, "rule_not_active"
    if ev.event_type not in rule["required_event_types"]:
        return None, "event_type_not_applicable"
    if ev.source_tier and rule.get("source_tiers") and ev.source_tier not in rule["source_tiers"]:
        return None, "source_tier_not_allowed"
    if VER_ORDER.get(ev.verification_status, 0) < VER_ORDER[rule.get("min_verification", "verified")]:
        return None, f"event_{ev.verification_status}"
    for f in rule.get("required_fields", []):
        if ev.attrs.get(f) in (None, "", []):
            return None, f"missing_required_field:{f}"
    if not eval_node(ev, rule["match"], now):
        return None, "positive_pattern_not_matched"
    for ex in rule.get("exclusions", []):
        if eval_node(ev, ex, now):
            return None, "excluded"
    if rule["kind"] == "cluster":
        need = rule["cluster"]["min_count"]
        if cluster_count is None or cluster_count != need:      # fire exactly once, on the event that completes the cluster
            return None, f"cluster_not_completed:{cluster_count}/{need}"
    expires, notes = compute_expiry(rule, ev, now)
    if expires is not None and expires <= now:
        return None, "expired"
    ref = ev.event_at or ev.pub_at or now
    if rule["kind"] == "negative":
        neg = dict(rule["negative"])
        months = neg.get("window_months")
        exp = (ref + timedelta(days=int(months * 30.4))) if months else None
        if exp is not None and exp <= now:
            return None, "expired"
        return Match(rule["id"], rule["version"], "negative", rule["reason_code"], effective_from=ref, expires_at=exp, negative=neg, notes=notes), "matched"
    tier = rule["demand_tier"]
    up = rule.get("upgrade_with_independent_groups")
    if up and ev.independent_groups >= up["min"] and TIER_ORDER[up["to"]] < TIER_ORDER[tier]:
        tier = up["to"]
        notes.append(f"upgraded_by_{ev.independent_groups}_independent_origin_groups")
    return Match(rule["id"], rule["version"], "signal", rule["reason_code"], strength=int(rule["strength"]), demand_tier=tier,
                 effective_from=ref, expires_at=expires, commercial_questions=list(rule.get("commercial_questions", [])), notes=notes,
                 summary_is_inference=tier in {"D2", "D3", "D4"}), "matched"
