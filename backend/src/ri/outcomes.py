"""Outcome tracking and learning (spec 07 §12, 14 §9, 01 §8). Attribution is TRANSPARENT multi-touch, not causal ROI.
No calibrated probabilities are produced; cohort statistics carry uncertainty and a minimum-sample flag."""
from __future__ import annotations

import math
from typing import Any

from .audit import audit
from .db import Conn, jb, q1, qa

FUNNEL = ["recommendation_accepted", "meaningful_conversation", "discovery_held", "buyer_confirmed_need", "supplier_route_validated", "procurement_validated",
          "qualified_crm_opportunity", "signed", "delivery_started", "revenue_observed"]
MIN_SAMPLE = 30


def record(conn: Conn, hyp_id: str, kind: str, *, actor: str, crm_ref: str | None = None, detail: dict[str, Any] | None = None) -> str:
    h = q1(conn, "SELECT * FROM opportunity_hypotheses WHERE id=%s", (hyp_id,))
    if not h:
        raise ValueError("hypothesis not found")
    contrib = [{"kind": "signal", "ref": r["rule_id"]} for r in qa(conn, "SELECT DISTINCT s.rule_id FROM signals s JOIN initiative_events ie ON ie.event_id=s.event_id WHERE ie.initiative_id=%s", (h["initiative_id"],))]
    contrib.append({"kind": "route", "ref": h["route_type"]})
    row = conn.execute("INSERT INTO outcome_events(hypothesis_id, kind, crm_ref, detail, contributions, recorded_by) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                       (hyp_id, kind, crm_ref, jb(detail or {}), jb(contrib), actor)).fetchone()
    audit(conn, actor=actor, operation="outcome_recorded", record_type="opportunity_hypothesis", record_id=hyp_id, detail={"kind": kind, "crm_ref": crm_ref})
    return str(row["id"])  # type: ignore[index]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(c - m, 0), 3), round(min(c + m, 1), 3))


def funnel(conn: Conn, *, group_by: str = "route_type") -> dict[str, Any]:
    """Counts distinct hypotheses reaching each stage, by cohort. Intervals shown; small samples are flagged, not extrapolated."""
    if group_by not in {"route_type", "priority", "state"}:
        raise ValueError("unsupported cohort dimension")
    rows = qa(conn, f"SELECT h.{group_by} AS cohort, o.kind, count(DISTINCT o.hypothesis_id) n FROM outcome_events o JOIN opportunity_hypotheses h ON h.id=o.hypothesis_id GROUP BY 1,2")  # noqa: S608
    presented = {r["cohort"]: r["n"] for r in qa(conn, f"SELECT h.{group_by} AS cohort, count(DISTINCT p.hypothesis_id) n FROM product_events p JOIN opportunity_hypotheses h ON h.id=p.hypothesis_id WHERE p.event='recommendation_presented' GROUP BY 1")}  # noqa: S608
    out: dict[str, Any] = {}
    for c in {r["cohort"] for r in rows} | set(presented):
        base = presented.get(c, 0)
        counts = {r["kind"]: r["n"] for r in rows if r["cohort"] == c}
        out[c] = {"presented": base, "stages": {k: {"n": counts.get(k, 0), "rate_vs_presented": round(counts.get(k, 0) / base, 3) if base else None,
                                                    "interval_95": wilson(counts.get(k, 0), base)} for k in FUNNEL},
                  "insufficient_sample": base < MIN_SAMPLE}
    return {"cohorts": out, "caveats": [f"Cohorts with fewer than {MIN_SAMPLE} presented recommendations are flagged: no conclusions.",
                                        "Attribution lists contributing signals/routes transparently; it does not establish causation.",
                                        "A reply or meeting is not proof the original inference was correct."]}


def cost_per_accepted(conn: Conn) -> dict[str, Any]:
    spent = float(q1(conn, "SELECT coalesce(sum(spent),0) s FROM provider_budgets")["s"])  # type: ignore[index]
    accepted = q1(conn, "SELECT count(DISTINCT hypothesis_id) n FROM outcome_events WHERE kind='recommendation_accepted'")["n"]  # type: ignore[index]
    return {"total_provider_spend": spent, "accepted_recommendations": accepted, "cost_per_accepted": round(spent / accepted, 4) if accepted else None,
            "note": "Provider rates are not assumed anywhere in this system; spend reflects configured, settled budget ledger entries only."}
