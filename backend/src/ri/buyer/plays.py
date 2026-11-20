"""Sales plays (spec 08 §6) + discovery question engine (§8). Questions are generated from MISSING gates and never ask what CRM already confirms."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..db import Conn, q1, qa

PATH = Path(__file__).resolve().parents[3] / "rules" / "sales_plays.yaml"


@lru_cache
def catalogue() -> list[dict[str, Any]]:
    return yaml.safe_load(PATH.read_text(encoding="utf-8"))["plays"]


def select_play(conn: Conn, h: dict[str, Any]) -> dict[str, Any]:
    active = {r["rule_id"] for r in qa(conn, "SELECT DISTINCT s.rule_id FROM signals s JOIN initiative_events ie ON ie.event_id=s.event_id WHERE ie.initiative_id=%s AND s.status='active'", (h["initiative_id"],))}
    dom = (q1(conn, "SELECT programme_domain d FROM initiatives WHERE id=%s", (h["initiative_id"],)) or {}).get("d")
    best, why = None, "no trigger matched"
    for p in catalogue():
        t = p["triggers"]
        if set(t.get("rules", [])) & active:
            best, why = p, f"active signal(s) {sorted(set(t['rules']) & active)}"
            break
        if dom in t.get("domains", []) and best is None:
            best, why = p, f"programme domain {dom}"
    if best is None:
        best = next(p for p in catalogue() if p["id"] == "SP-05") if dom == "core_banking" else None
    if best is None:
        return {"id": None, "reason": why}
    # route-aware refinement: a prime route is SP-08 regardless of other triggers; a procurement route is SP-09
    if h["route_type"] == "prime_subcontract":
        best = next(p for p in catalogue() if p["id"] == "SP-08")
    elif h["route_type"] == "procurement":
        best = next(p for p in catalogue() if p["id"] == "SP-09")
    return {**best, "reason": why}


def discovery_questions(conn: Conn, h: dict[str, Any], play: dict[str, Any]) -> list[dict[str, str]]:
    """Targeted by missing gates; skips questions whose answers are already verified (gate pass) or in CRM (open deal at discovery+)."""
    g = h["gates"] or {}
    qs: list[dict[str, str]] = []
    crm_deal = q1(conn, "SELECT stage_name FROM crm_deals d JOIN crm_organizations o ON o.crm_id=d.crm_org_id WHERE o.organization_id=%s AND d.status='open' LIMIT 1", (h["organization_id"],))
    def need(gate: str) -> bool:
        return (g.get(gate) or {}).get("status") != "pass"
    if need("G4"):
        qs.append({"gate": "G4", "question": "Is the separable engineering workstream sourced externally, and through which supplier model?"})
    if need("G5"):
        qs.append({"gate": "G5", "question": "Does your framework or panel support an EU engineering supplier or a subcontracted delivery model?"})
    if need("G6"):
        qs.append({"gate": "G6", "question": "Which team owns the integration and the target migration milestone, and who signs off technical acceptance and supplier eligibility?"})
    if need("G7"):
        qs.append({"gate": "G7", "question": "What start window, team shape and duration would make this feasible on both sides?"})
    for extra in play.get("questions", []):
        qs.append({"gate": "play", "question": extra})
    if crm_deal and crm_deal["stage_name"] not in {"Webform MQL", "Marketing Nurturing "}:
        qs = [x for x in qs if x["gate"] != "G6"] or qs          # an open qualified deal usually already names the owner: do not re-ask what CRM holds
    return qs
