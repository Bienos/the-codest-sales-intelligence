"""Adaptive research planner (spec 06 §11, 14 §6). Each step must answer: 'Which unknown could change the commercial decision?'
Skips: already-known facts, hard-blocked/disqualified hypotheses, questions only a buyer can answer (-> qualification action),
steps over budget, and anything past the diminishing-returns depth. Every skip is logged with a reason."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable

from ..budget import release, reserve, settle
from ..db import Conn, jb, q1, qa
from ..sources.base import BudgetExceeded

MAX_DEPTH_WITHOUT_CHANGE = 3
QUESTIONS = [  # spec 06 §8 ordering
    ("G4", "Is the engineering requirement real and sourced externally?", "buyer_conversation", "action", 0.0),
    ("G5", "Is there a direct vendor/framework/MSP route, or can an existing SI subcontract specialist work?", "targeted_retrieval", "route", 1.0),
    ("G6", "Who owns delivery of this workstream (role-first), and who controls supplier selection?", "official_api", "action", 1.0),
    ("G7", "Is the scope deliverable by Codest in the stated window (internal check)?", "existing_data", "action", 0.0),
]


@dataclass
class Step:
    gate: str
    question: str
    method: str
    decision_effect: str
    max_cost: float
    stop_condition: str
    provider: str | None = None


def _qkey(hyp_id: str, question: str) -> str:
    return f"research:{hyp_id}:{hashlib.sha256(question.encode()).hexdigest()[:16]}"


def plan_next(conn: Conn, hyp_id: str, *, remaining_budget: float, record: bool = True) -> tuple[Step | None, str]:
    """Returns (step, reason). step=None means STOP and `reason` says why (logged to research_tasks)."""
    h = q1(conn, "SELECT * FROM opportunity_hypotheses WHERE id=%s", (hyp_id,))
    if not h:
        return None, "hypothesis_not_found"
    if h["state"] in {"disqualified", "superseded", "blocked", "expired"} or any(b["scope"] == "hard" for b in (h["blockers"] or [])):
        return _skip(conn, hyp_id, "hard_blocker", "Hard-blocked hypothesis: no research can change the decision", record)
    done = qa(conn, "SELECT status, stop_reason FROM research_tasks WHERE target_type='hypothesis' AND target_id=%s AND method<>'existing_data'", (hyp_id,))
    unproductive = len([d for d in done if d["status"] == "completed" and d["stop_reason"] == "no_decision_change"])
    if unproductive >= MAX_DEPTH_WITHOUT_CHANGE:
        return _skip(conn, hyp_id, "diminishing_value", f"{unproductive} consecutive steps changed no decision", record)
    gates = h["gates"] or {}
    for gate, question, method, effect, cost in QUESTIONS:
        if (gates.get(gate) or {}).get("status") != "unknown":
            continue
        key = _qkey(hyp_id, question)
        prior = q1(conn, "SELECT status FROM research_tasks WHERE idempotency_key=%s", (key,))
        if prior and prior["status"] in {"completed", "skipped", "planned", "running"}:
            continue          # already answered / queued: never repeat (cache)
        if method == "buyer_conversation":
            return _skip(conn, hyp_id, "resolvable_only_by_buyer", f"{question} -> create a qualification action, not more web research", record, question=question, gate=gate)
        if cost > remaining_budget:
            if record:
                conn.execute("INSERT INTO research_tasks(target_type,target_id,research_question,evidence_gap,decision_effect,method,estimated_cost,max_cost,status,stop_reason,idempotency_key) "
                             "VALUES ('hypothesis',%s,%s,%s,%s,%s,%s,%s,'budget_exceeded','budget',%s) ON CONFLICT DO NOTHING",
                             (hyp_id, question, gates[gate]["reason"], effect, method, cost, cost, key))
            return None, "budget_exceeded"
        step = Step(gate, question, method, effect, cost, "gate resolved, hard blocker found, or budget/depth limit reached", "exa" if method in {"targeted_retrieval", "semantic_search"} else None)
        if record:
            conn.execute("INSERT INTO research_tasks(target_type,target_id,research_question,evidence_gap,decision_effect,method,provider,estimated_cost,max_cost,status,idempotency_key) "
                         "VALUES ('hypothesis',%s,%s,%s,%s,%s,%s,%s,%s,'planned',%s) ON CONFLICT DO NOTHING",
                         (hyp_id, question, gates[gate]["reason"], effect, method, step.provider, cost, cost, key))
        return step, "planned"
    return None, "no_unknown_gate_can_change_the_decision"


def _skip(conn: Conn, hyp_id: str, code: str, why: str, record: bool, *, question: str | None = None, gate: str | None = None) -> tuple[None, str]:
    if record:
        q = question or "(none)"
        key = _qkey(hyp_id, question) if question else _qkey(hyp_id, q + code)      # question-specific skips are cached so they are never repeated
        conn.execute("INSERT INTO research_tasks(target_type,target_id,research_question,evidence_gap,decision_effect,method,status,stop_reason,idempotency_key) "
                     "VALUES ('hypothesis',%s,%s,%s,'none','existing_data','skipped',%s,%s) ON CONFLICT DO NOTHING",
                     (hyp_id, q, why, code, key))
    return None, code


def execute(conn: Conn, hyp_id: str, step: Step, runner: Callable[[Step], dict[str, Any]], *, period: str, provider: str = "exa") -> dict[str, Any]:
    """Run a planned step under a hard per-hypothesis cap. Reserve BEFORE the call; release on failure; settle with actuals."""
    key = _qkey(hyp_id, step.question)
    task = q1(conn, "SELECT id, status FROM research_tasks WHERE idempotency_key=%s", (key,))
    if not task or task["status"] != "planned":
        return {"skipped": True, "reason": f"task status {task['status'] if task else 'missing'}"}
    try:
        reserve(conn, provider, "hypothesis", hyp_id, step.max_cost, key=f"rk:{key}", ref_type="research_task", ref_id=str(task["id"]), purpose=step.question)
    except BudgetExceeded:
        conn.execute("UPDATE research_tasks SET status='budget_exceeded', stop_reason='budget', finished_at=now() WHERE id=%s", (task["id"],))
        return {"skipped": True, "reason": "budget_exceeded"}
    conn.execute("UPDATE research_tasks SET status='running' WHERE id=%s", (task["id"],))
    try:
        out = runner(step)
    except Exception as exc:  # noqa: BLE001
        release(conn, provider, "hypothesis", hyp_id, step.max_cost, key=f"rk:{key}")
        conn.execute("UPDATE research_tasks SET status='blocked', stop_reason=%s, finished_at=now() WHERE id=%s", (f"provider_error:{type(exc).__name__}", task["id"]))
        raise
    actual = float(out.get("cost", step.max_cost))
    settle(conn, provider, "hypothesis", hyp_id, step.max_cost, actual, key=f"rk:{key}")
    conn.execute("UPDATE research_tasks SET status='completed', result=%s, stop_reason=%s, finished_at=now() WHERE id=%s",
                 (jb(out), "gate_resolved" if out.get("changed_decision") else "no_decision_change", task["id"]))
    return out
