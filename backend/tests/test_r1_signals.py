from datetime import datetime, timedelta, timezone

import pytest
import yaml
from pathlib import Path

from ri.signals.engine import EvalEvent, evaluate, load_rules
from ri.signals.persist import assert_fixture_coverage

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
FIXTURES = yaml.safe_load((Path(__file__).parent / "fixtures" / "signal_fixtures.yaml").read_text())
RULES = {r["id"]: r for r in load_rules()}


def _cases():
    for blk in FIXTURES:
        for c in blk["cases"]:
            yield pytest.param(blk, c, id=f"{blk['rule']}::{c['name']}")


def build_event(blk, c):
    base = {**blk["base"], "attrs": dict(blk["base"]["attrs"])}
    top = {**c.get("top", {})}
    attrs = {**base["attrs"], **c.get("set", {})}
    for k in c.get("drop", []):
        attrs.pop(k, None)
    etype = top.get("event_type", base["event_type"])
    days = top.get("days_ago", base["days_ago"])
    ts = NOW - timedelta(days=days)
    attrs["source_tier"] = top.get("source_tier", base["source_tier"])
    return EvalEvent(event_type=etype, attrs=attrs, summary="synthetic", pub_at=ts, event_at=ts,
                     verification_status="verified" if top.get("verified", True) else "unverified",
                     source_tier=attrs["source_tier"], other_roles=top.get("other_roles", base.get("other_roles", [])),
                     independent_groups=top.get("groups", 1)), top.get("cluster_count", base.get("cluster_count"))


@pytest.mark.parametrize("blk,c", list(_cases()))
def test_fixture(blk, c):
    ev, cc = build_event(blk, c)
    m, trace = evaluate(RULES[blk["rule"]], ev, NOW, cluster_count=cc)
    if c["expect"] == "match":
        assert m is not None, f"expected match, got none ({trace})"
        if "tier" in c:
            assert m.demand_tier == c["tier"]
        if m.kind == "negative":
            assert m.negative["kind"] in {"route_block", "demand_counterevidence", "ownership_gate", "hard_block", "soft_risk"}
    else:
        assert m is None, "expected no match"
        if "trace" in c:
            assert trace == c["trace"]


def test_every_active_rule_has_required_fixture_coverage():
    counts = assert_fixture_coverage([{"rule": b["rule"], **{"kind": c["kind"]}} for b in FIXTURES for c in b["cases"]], list(RULES.values()))
    assert set(counts) >= {r for r, d in RULES.items() if d["status"] == "active"}


def test_coverage_gate_blocks_underfixtured_rule():
    with pytest.raises(AssertionError, match="lack required fixtures"):
        assert_fixture_coverage([{"rule": "SRC-01", "kind": "positive"}], [RULES["SRC-01"]])


def test_tender_expiry_follows_official_close_not_days():
    ev, _ = build_event(FIXTURES[0], FIXTURES[0]["cases"][0])
    m, _ = evaluate(RULES["SRC-01"], ev, NOW)
    assert m.expires_at == datetime(2026, 12, 7, 12, 0, tzinfo=timezone.utc)


def test_unknown_event_date_is_flagged_not_replaced():
    ev, _ = build_event(FIXTURES[0], FIXTURES[0]["cases"][0])
    ev.event_at = None
    m, _ = evaluate(RULES["SRC-01"], ev, NOW)
    assert "event_date_unknown_publication_date_used_for_freshness_only" in m.notes
