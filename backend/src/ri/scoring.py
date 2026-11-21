"""Account score. Several different kinds of signal on one account are worth more than the same kind repeated, and a stop rule ends the story.

Provisional weights, not win probabilities. Every number is explained by the signals listed beside it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .db import Conn, jb, qa

GROUPS = {"SRC": "buying route", "CAP": "capacity", "MAN": "new leader", "PRG": "funded programme", "COM": "business event", "REG": "deadline", "REL": "relationship"}
DEMAND = {"D1": 1.5, "D2": 1.3, "D3": 1.1, "D4": 1.0}
HOT, WARM = 9.0, 4.0


def band_for(score: float, stopped: bool) -> str:
    if stopped or score <= 0:
        return "none"
    return "hot" if score >= HOT else "warm" if score >= WARM else "watch"


def freshness(eff: datetime, exp: datetime | None, now: datetime) -> float:
    """1.0 when new, falling to 0.25 as the signal's own window runs out."""
    if exp is None or exp <= eff:
        return 0.6
    left = (exp - now).total_seconds() / (exp - eff).total_seconds()
    return max(0.25, min(1.0, left))


def score_account(signals: list[dict[str, Any]], *, stopped: bool, downgraded: bool, now: datetime) -> dict[str, Any]:
    best: dict[str, float] = {}           # one contribution per rule: a rule firing three times is still one rule
    detail: dict[str, dict[str, Any]] = {}
    for s in signals:
        w = float(s["strength"]) * DEMAND.get(s["demand_tier"], 1.0) * freshness(s["effective_from"], s["expires_at"], now)
        if w > best.get(s["rule_id"], 0):
            best[s["rule_id"]] = w
            detail[s["rule_id"]] = {"rule_id": s["rule_id"], "strength": s["strength"], "weight": round(w, 2), "expires_at": s["expires_at"].isoformat() if s["expires_at"] else None}
    groups = sorted({r[:3] for r in best})
    bonus = 1 + 0.25 * min(max(len(groups) - 1, 0), 3)
    raw = sum(best.values()) * bonus * (0.5 if downgraded else 1.0)
    score = 0.0 if stopped else round(raw, 1)
    top = sorted(detail.values(), key=lambda d: -d["weight"])[:5]
    return {"score": score, "band": band_for(score, stopped), "groups": groups, "signal_count": len(best), "top": top, "bonus": bonus}


def recompute_scores(conn: Conn, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    rows = qa(conn, "SELECT s.organization_id, s.rule_id, s.strength, s.demand_tier, s.effective_from, s.expires_at FROM signals s "
                    "WHERE s.organization_id IS NOT NULL AND s.status='active' AND (s.expires_at IS NULL OR s.expires_at > %s)", (now,))
    by_org: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_org.setdefault(str(r["organization_id"]), []).append(r)
    neg = qa(conn, "SELECT scope_id, effect FROM negative_findings WHERE scope_type='account' AND status='active' AND (expires_at IS NULL OR expires_at > %s)", (now,))
    stops = {n["scope_id"] for n in neg if n["effect"] == "stop"}
    downs = {n["scope_id"] for n in neg if n["effect"] == "downgrade"}
    n = 0
    for oid, sigs in by_org.items():
        r = score_account(sigs, stopped=oid in stops, downgraded=oid in downs, now=now)
        last = max(s["effective_from"] for s in sigs)
        conn.execute("INSERT INTO account_scores(organization_id, score, band, groups, signal_count, top, stopped, downgraded, last_signal_at, computed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                     "ON CONFLICT (organization_id) DO UPDATE SET score=EXCLUDED.score, band=EXCLUDED.band, groups=EXCLUDED.groups, signal_count=EXCLUDED.signal_count, top=EXCLUDED.top, "
                     "stopped=EXCLUDED.stopped, downgraded=EXCLUDED.downgraded, last_signal_at=EXCLUDED.last_signal_at, computed_at=EXCLUDED.computed_at",
                     (oid, r["score"], r["band"], r["groups"], r["signal_count"], jb(r["top"]), oid in stops, oid in downs, last, now))
        n += 1
    gone = conn.execute("DELETE FROM account_scores WHERE organization_id <> ALL(%s::uuid[]) RETURNING 1", (list(by_org) or ["00000000-0000-0000-0000-000000000000"],)).fetchall()
    return {"scored": n, "cleared": len(gone)}
