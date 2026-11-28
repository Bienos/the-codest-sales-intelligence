"""Funnel outlook: how many deals should reach each stage in the next 90 days (MQL to SQL and on to won), the chance and time to get from
any stage to any later one, and what actually arrived in each stage month by month, so the prediction can be checked against reality.

The projection plays the open deals and the expected new deals through the same stage rates and durations the revenue forecast uses
(ri/forecast/engine.py), 2,000 times, and records when each simulated deal enters each stage. A deal already sitting in a stage does not
count as arriving there again. A deal that has sat in its stage much longer than usual is less likely to move on: past twice the stage's
median time its chance is halved, past four times it is quartered (STALE). Without this, months-old deals would all "arrive" next week. Measured rates come with an 80% interval (Wilson) so a rate from six deals reads as the guess it is."""
from __future__ import annotations

import calendar
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np

from ..db import Conn, qa
from .engine import MIN_LEFT, SIG, _poisson_ppf

RUNS = 2000
STALE = ((4.0, 0.25), (2.0, 0.5))          # (times the stage's median time already spent, factor on the chance of moving on)
WINDOWS = (30, 60, 90)
Z80 = 1.2816


def wilson(k: int, n: int, z: float = Z80) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def reach(conv: list[float], days: list[float]) -> list[list[dict[str, float] | None]]:
    """reach[i][j]: chance a deal entering stage i gets to stage j (j = len means won), and the median days that usually takes."""
    S = len(conv)
    out: list[list[dict[str, float] | None]] = []
    for i in range(S):
        row: list[dict[str, float] | None] = []
        p, t = 1.0, 0.0
        for j in range(S + 1):
            if j <= i:
                row.append(None)
                continue
            p *= conv[j - 1]
            t += days[j - 1]
            row.append({"p": p, "days": t})
        out.append(row)
    return out


def stale_factor(age: np.ndarray, median_days: np.ndarray) -> np.ndarray:
    ratio = age / np.maximum(1.0, median_days)
    f = np.ones_like(ratio, dtype=float)
    for times, factor in sorted(STALE):                 # ascending, so the larger threshold wins
        f = np.where(ratio > times, factor, f)
    return f


def _entries(start: np.ndarray, t0: np.ndarray, age: np.ndarray, u: np.ndarray, z: np.ndarray, conv: np.ndarray, days: np.ndarray) -> np.ndarray:
    """Time each simulated deal enters each stage (last column: won), NaN when it never does. Shapes broadcast as in engine._walk;
    `conv` is per stage, or per deal and stage (its last axis is the stage)."""
    S = len(days)
    shape = u.shape[:-1]
    t = np.broadcast_to(t0, shape).astype(float).copy()
    alive = np.ones(shape, dtype=bool)
    ent = np.full((*shape, S + 1), np.nan)
    for i in range(S):
        here = start <= i
        ent[..., i] = np.where(alive & here, t, np.nan)
        d = days[i] * np.exp(SIG * z[..., i])
        d = np.where(start == i, np.maximum(MIN_LEFT, d - age), d)
        t = t + np.where(here & alive, d, 0.0)
        alive &= ~(here & (u[..., i] >= conv[..., i]))
    ent[..., S] = np.where(alive, t, np.nan)
    return ent


def project(conv: list[float], days: list[float], open_stage: list[int], open_age: list[float], streams: list[tuple[float, int]],
            horizon: int = WINDOWS[-1], runs: int = RUNS, seed: int = 20261128) -> dict[str, Any]:
    """Arrivals per stage (and won) in each 30-day window of the next 90 days, as p10/p50/p90/mean over the runs."""
    rng = np.random.default_rng(seed)
    S = len(conv)
    cv, dy = np.asarray(conv, dtype=float), np.asarray(days, dtype=float)
    counts = np.zeros((runs, len(WINDOWS), S + 1))
    edges = np.array((0, *WINDOWS), dtype=float)

    def add(ent: np.ndarray, mask: np.ndarray | None = None) -> None:
        for w in range(len(WINDOWS)):
            hit = (ent >= edges[w]) & (ent < edges[w + 1])
            if mask is not None:
                hit &= mask[..., None]
            counts[:, w, :] += hit.reshape(runs, -1, S + 1).sum(axis=1)
    if open_stage:
        st = np.asarray(open_stage)[None, :]
        n = len(open_stage)
        age = np.asarray(open_age, dtype=float)
        cv_open = np.tile(cv, (n, 1))
        cv_open[np.arange(n), st[0]] *= stale_factor(age, dy[st[0]])
        ent = _entries(st, np.zeros(1), age[None, :], rng.random((runs, n, S)), rng.standard_normal((runs, n, S)), cv_open[None, :, :], dy)
        cols = np.arange(S + 1)[None, None, :]
        ent = np.where(cols == st[..., None], np.nan, ent)          # already in that stage: not an arrival
        add(ent)
    for lam, entry in streams:
        mean = lam * horizon / 30.4
        if mean <= 0:
            continue
        K = max(1, _poisson_ppf(0.9995, mean) + 1)
        cnt = rng.poisson(mean, runs)
        used = np.arange(K)[None, :] < cnt[:, None]
        t0 = rng.random((runs, K)) * horizon
        ent = _entries(np.int64(entry), t0, np.zeros(1), rng.random((runs, K, S)), rng.standard_normal((runs, K, S)), cv, dy)
        add(ent, used)

    def summ(a: np.ndarray) -> dict[str, float]:
        p10, p50, p90 = np.percentile(a, [10, 50, 90])
        return {"p10": float(p10), "p50": float(p50), "p90": float(p90), "mean": float(a.mean())}
    total = counts.sum(axis=1)
    return {"windows": list(WINDOWS),
            "stages": [{"by_window": [summ(counts[:, w, i]) for w in range(len(WINDOWS))], "total": summ(total[:, i])} for i in range(S + 1)]}


def _month_starts(now: datetime, n: int) -> list[date]:
    y, m = now.year, now.month
    out = []
    for _ in range(n):
        out.append(date(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def monthly_actuals(conn: Conn, stage_ids: list[int], won_ids: list[int], now: datetime, months: int = 12) -> dict[str, Any]:
    """Deals that entered each stage for the first time, per calendar month (the current month so far), from the stage history.
    'first_seen' rows are left out: they record when the mirror met a deal already in a stage, not when it arrived."""
    starts = _month_starts(now, months)
    since = datetime(starts[0].year, starts[0].month, 1, tzinfo=timezone.utc)
    rows = qa(conn, "SELECT to_stage_id, date_trunc('month', first_at)::date AS m, count(*) AS n FROM ("
                    "  SELECT crm_deal_id, to_stage_id, min(at) AS first_at FROM deal_stage_transitions"
                    "  WHERE source <> 'first_seen' AND to_stage_id = ANY(%s) GROUP BY 1,2) x WHERE first_at >= %s GROUP BY 1,2",
              (stage_ids, since))
    won = qa(conn, "SELECT date_trunc('month', won_time)::date AS m, count(*) AS n FROM crm_deals WHERE status='won' AND won_time >= %s "
                   "AND (stage_id = ANY(%s) OR stage_id = ANY(%s)) GROUP BY 1", (since, stage_ids, won_ids))
    idx = {s: i for i, s in enumerate(stage_ids)}
    grid = [[0] * months for _ in range(len(stage_ids) + 1)]
    pos = {d: i for i, d in enumerate(starts)}
    for r in rows:
        if r["m"] in pos and r["to_stage_id"] in idx:
            grid[idx[r["to_stage_id"]]][pos[r["m"]]] += int(r["n"])
    for r in won:
        if r["m"] in pos:
            grid[-1][pos[r["m"]]] += int(r["n"])
    day = now.day
    dim = calendar.monthrange(now.year, now.month)[1]
    return {"months": [d.isoformat() for d in starts], "counts": grid, "current_month_share": day / dim}


def default_pair(names: list[str]) -> tuple[int, int]:
    """MQL to SQL when the stages say so; otherwise the first stage to the third (or the last)."""
    low = [n.strip().lower() for n in names]
    src = next((i for i, n in enumerate(low) if "mql" in n), 0)
    dst = next((i for i, n in enumerate(low) if i > src and (n == "sql" or ("sql" in n and "mql" not in n))), None)
    if dst is None:
        dst = min(len(names), src + 2)
    return src, dst


def build(conn: Conn, inp: dict[str, Any], open_deals: list[dict[str, Any]], streams: list[tuple[float, int]], won_ids: list[int],
          recent: Any, now: datetime | None = None) -> dict[str, Any]:
    """`recent` is the History measured over the last 12 months only, for the trend next to each rate."""
    now = now or datetime.now(timezone.utc)
    st = inp["stages"]
    conv = [min(1.0, max(0.0, s["used"]["conv"] / 100)) for s in st]
    days = [max(1.0, s["used"]["days"]) for s in st]
    proj = project(conv, days, [o["stage_index"] for o in open_deals], [o["days_in_stage"] for o in open_deals], streams)
    stages = []
    for i, s in enumerate(st):
        m = s["measured"]
        ci = wilson(m["moved_on"], m["decided"])
        r = recent.stats[i] if recent is not None and i < len(recent.stats) else None
        rr = r.rate() if r is not None else None
        stages.append({"id": s["id"], "name": s["name"], "conv": conv[i], "days": days[i], "open": s["open"]["n"],
                       "measured": None if m["conv"] is None else m["conv"] / 100, "decided": m["decided"], "interval": ci,
                       "recent": rr, "recent_decided": r.resolved if r is not None else 0, "prior": s["prior"]["conv"] / 100,
                       "arrivals": proj["stages"][i]})
    src, dst = default_pair([s["name"] for s in st])
    return {"as_of": now.isoformat(), "runs": RUNS, "stale_rule": [{"over_times_median": t, "factor": f} for t, f in STALE], "windows": proj["windows"], "stages": stages, "won": {"arrivals": proj["stages"][-1]},
            "reach": reach(conv, days), "pair": {"from": src, "to": dst},
            "new_per_month": [{"entry": e, "per_month": lam} for lam, e in streams],
            "actuals": monthly_actuals(conn, [s["id"] for s in st], won_ids, now)}
