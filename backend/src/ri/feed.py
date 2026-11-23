"""Market feed helpers: what a verified event means for what The Codest sells. Rule -> sales play -> offer, from the original tracker and rules/service_fit.yaml."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RULES = Path(__file__).resolve().parents[2] / "rules"


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    rules = {r["id"]: r for r in yaml.safe_load((RULES / "signal_rules.yaml").read_text(encoding="utf-8"))["rules"]}
    plays = {p["id"]: p for p in yaml.safe_load((RULES / "sales_plays.yaml").read_text(encoding="utf-8"))["plays"]}
    fit = yaml.safe_load((RULES / "service_fit.yaml").read_text(encoding="utf-8"))
    return {"rules": rules, "plays": plays, "fit": fit}


def fit_for(rule_ids: list[str]) -> dict[str, Any]:
    d = _load()
    seen: dict[str, dict[str, Any]] = {}
    for rid in rule_ids:
        r = d["rules"].get(rid)
        if not r or r.get("kind") == "negative" or not r.get("related_play"):
            continue
        pid = r["related_play"]
        play = d["plays"].get(pid)
        f = d["fit"]["plays"].get(pid)
        if play and pid not in seen:
            seen[pid] = {"play_id": pid, "play": play["name"], "primary_buyer": play.get("primary_buyer"), "why": f["why"] if f else None,
                         "offers": [d["fit"]["offers"][o] for o in (f["offers"] if f else [])]}
    offers = sorted({o for s in seen.values() for o in s["offers"]})
    return {"plays": list(seen.values()), "offers": offers, "label": "Suggested fit (working mapping from the sales plays; a judgement, not evidence)"}


def rule_questions(rule_ids: list[str]) -> list[str]:
    d = _load()
    out: list[str] = []
    for rid in rule_ids:
        for q in (d["rules"].get(rid, {}).get("commercial_questions") or [])[:2]:
            if q not in out:
                out.append(q)
    return out[:4]


def play_ids() -> list[tuple[str, str]]:
    return [(p["id"], p["name"]) for p in _load()["plays"].values()]


def rules_for_play(play_id: str) -> list[str]:
    return [rid for rid, r in _load()["rules"].items() if r.get("related_play") == play_id]


def contact_hint(event_type: str, rule_ids: list[str], *, buyer_unclear: bool = False) -> dict[str, str]:
    """Give a seller a role to research without asserting who owns the buying decision."""
    rules = _load()["rules"]
    plays = fit_for(rule_ids)["plays"]
    if event_type.startswith("procurement_"):
        role = "Procurement lead and technical sponsor"
    else:
        role = next((p["primary_buyer"] for p in plays if p["primary_buyer"]), "Programme owner or engineering delivery lead")
    question = next((q for rid in rule_ids for q in rules.get(rid, {}).get("commercial_questions") or []),
                    "Who owns this workstream, and is any part of the delivery open to outside engineering support?")
    if buyer_unclear:
        question = "Which company and team own the workstream? If a vendor or integrator leads delivery, who manages specialist partners?"
    return {"role": role, "question": question}


# Keep the grouping scoped to one buyer and event type. A date distinguishes repeated milestones.
# An explicit event key can corroborate a story across independent origins. Otherwise require
# the same tracked origin and matching wording before folding records together.
STORY_KEY_SQL = """jsonb_build_array(
    coalesce(e.subject_org_id::text, nullif(lower(trim(e.subject_name_raw)), ''), e.id::text),
    e.event_type,
    (e.event_at AT TIME ZONE 'UTC')::date,
    CASE WHEN nullif(trim(e.attrs->>'dedup_key'), '') IS NOT NULL
         THEN jsonb_build_array('key', lower(trim(e.attrs->>'dedup_key')),
                                CASE WHEN e.event_at IS NULL THEN coalesce(e.independent_origin_group,e.id::text) END)
         ELSE jsonb_build_array('origin', coalesce(e.independent_origin_group,e.id::text),
                                trim(regexp_replace(lower(e.summary), '[[:space:][:punct:]]+', ' ', 'g')))
    END
)::text"""
