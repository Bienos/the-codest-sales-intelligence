"""Monte Carlo of closed-won revenue by quarter.

Each simulated year plays out every open deal and every deal expected to be created from next month on. At each funnel stage a deal moves
on with that stage's rate or drops out; the time it spends there is lognormal around the stage's median. A deal that gets through the last
stage closes and earns its value (lognormal spread around the average) in the bucket its close date falls in.

All random draws are made once per `Draws` and reused for every what-if, so a what-if differs from the base case only by the input changed
(common random numbers). That keeps the sensitivity bars free of simulation noise.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np

SIG = 0.5         # spread of time in stage (lognormal sigma): about two thirds of deals take between 0.6x and 1.65x the median
VSIG = 0.4        # spread of deal value around the average
MIN_LEFT = 3.0    # an open deal already past its stage's typical time still needs a few days to move


@dataclass
class Inputs:
    conv: list[float]                       # per stage: chance of moving on (the last stage: of winning)
    days: list[float]                       # per stage: median days spent there
    open_stage: list[int]                   # open deals: current stage index
    open_value: list[float]
    open_age: list[float]                   # days already spent in the current stage
    streams: list[tuple[float, int]]        # new deals: (deals per month, stage index they enter at)
    deal_value: float                       # average value of a new deal


@dataclass
class Calendar:
    today: date
    year: int

    def __post_init__(self) -> None:
        y = self.year
        starts = [date(y, 1, 1), date(y, 4, 1), date(y, 7, 1), date(y, 10, 1), date(y + 1, 1, 1)]
        self.edges = np.array([(s - self.today).days for s in starts], dtype=float)     # bucket 0: before the year, 1-4: quarters, 5: after
        months = []
        m = date(self.today.year + (self.today.month // 12), self.today.month % 12 + 1, 1)
        while m.year < y + 1:
            nxt = date(m.year + (m.month // 12), m.month % 12 + 1, 1)
            months.append(((m - self.today).days, (nxt - m).days))
            m = nxt
        self.months = np.array(months, dtype=float).reshape(-1, 2)

    def bucket(self, t: np.ndarray) -> np.ndarray:
        return np.searchsorted(self.edges, t, side="right")          # 0 before the year, 1..4 quarters, 5 after


class Draws:
    def __init__(self, runs: int, n_open: int, stages: int, n_streams: int, n_months: int, max_lam: float, seed: int = 20270101):
        rng = np.random.default_rng(seed)
        self.runs, self.S = runs, stages
        self.K = max(1, _poisson_ppf(0.9995, max_lam) + 2)          # most deals any month can produce; sized for the largest what-if
        f = np.float32
        self.o_pass = rng.random((runs, n_open, stages), dtype=f)
        self.o_dur = rng.standard_normal((runs, n_open, stages), dtype=f)
        self.o_val = rng.standard_normal((runs, n_open), dtype=f)
        shp = (runs, n_streams, n_months)
        self.n_cnt = rng.random(shp, dtype=f)
        self.n_day = rng.random((*shp, self.K), dtype=f)
        self.n_pass = rng.random((*shp, self.K, stages), dtype=f)
        self.n_dur = rng.standard_normal((*shp, self.K, stages), dtype=f)
        self.n_val = rng.standard_normal((*shp, self.K), dtype=f)


def _poisson_ppf(q: float, lam: float) -> int:
    if lam <= 0:
        return 0
    p = math.exp(-lam)
    c, k = p, 0
    while c < q and k < 10_000:
        k += 1
        p *= lam / k
        c += p
    return k


def _poisson_cdf(lam: float, K: int) -> np.ndarray:
    out = np.empty(K + 1)
    p = math.exp(-lam) if lam > 0 else 1.0
    c = 0.0
    for k in range(K + 1):
        if k > 0:
            p = p * lam / k if lam > 0 else 0.0
        c += p
        out[k] = c
    return out


def _walk(start: np.ndarray, t0: np.ndarray, age: np.ndarray, u_pass: np.ndarray, z_dur: np.ndarray, conv: np.ndarray, days: np.ndarray):
    """Vectorised path through the funnel. start/t0/age broadcast to the leading shape; u_pass/z_dur carry a trailing stage axis.
    Returns (won mask, close time in days from today)."""
    S = len(conv)
    alive = np.ones(u_pass.shape[:-1], dtype=bool)
    t = np.broadcast_to(t0, alive.shape).astype(float)
    for i in range(S):
        here = start <= i
        d = days[i] * np.exp(SIG * z_dur[..., i])
        d = np.where(start == i, np.maximum(MIN_LEFT, d - age), d)
        t = t + np.where(here & alive, d, 0.0)
        alive &= ~(here & (u_pass[..., i] >= conv[i]))
    return alive, t


def simulate(inp: Inputs, cal: Calendar, dr: Draws, *, detail: bool = True) -> dict:
    conv, days = np.asarray(inp.conv, dtype=float), np.asarray(inp.days, dtype=float)
    R, NB = dr.runs, 6
    rev_open = np.zeros((R, NB))
    rev_new = np.zeros((R, NB))
    wins = np.zeros((R, NB))
    if len(inp.open_stage):
        st = np.asarray(inp.open_stage)[None, :]
        won, t = _walk(st, np.zeros(1), np.asarray(inp.open_age, dtype=float)[None, :], dr.o_pass, dr.o_dur, conv, days)
        val = np.asarray(inp.open_value, dtype=float)[None, :] * np.exp(VSIG * dr.o_val - VSIG * VSIG / 2)
        b = cal.bucket(t)
        idx = (np.arange(R)[:, None] * NB + b)[won]
        rev_open += np.bincount(idx, weights=val[won], minlength=R * NB).reshape(R, NB)
        wins += np.bincount(idx, minlength=R * NB).reshape(R, NB)
    M = len(cal.months)
    if M:
        off, length = cal.months[:, 0], cal.months[:, 1]
        for s, (lam, entry) in enumerate(inp.streams):
            if lam <= 0:
                continue
            cnt = np.searchsorted(_poisson_cdf(lam, dr.K), dr.n_cnt[:, s, :], side="right").clip(0, dr.K)        # (R, M)
            used = np.arange(dr.K)[None, None, :] < cnt[:, :, None]                                                # (R, M, K)
            t0 = off[None, :, None] + dr.n_day[:, s] * length[None, :, None]
            won, t = _walk(np.int64(entry), t0, np.zeros(1), dr.n_pass[:, s], dr.n_dur[:, s], conv, days)
            ok = won & used
            val = inp.deal_value * np.exp(VSIG * dr.n_val[:, s] - VSIG * VSIG / 2)
            b = cal.bucket(t)
            idx = (np.arange(R)[:, None, None] * NB + b)[ok]
            rev_new += np.bincount(idx, weights=val[ok], minlength=R * NB).reshape(R, NB)
            wins += np.bincount(idx, minlength=R * NB).reshape(R, NB)
    total = rev_open + rev_new
    year = total[:, 1:5].sum(axis=1)
    out = {"mean": float(year.mean()), "wins": float(wins[:, 1:5].sum(axis=1).mean())}
    if detail:
        def pct(a: np.ndarray) -> dict:
            p10, p50, p90 = np.percentile(a, [10, 50, 90])
            return {"p10": float(p10), "p50": float(p50), "p90": float(p90), "mean": float(a.mean())}
        out["year"] = pct(year)
        out["buckets"] = [{**pct(total[:, b]), "open": float(rev_open[:, b].mean()), "new": float(rev_new[:, b].mean()),
                           "wins": float(wins[:, b].mean())} for b in range(5)]
        out["_year_samples"] = year
    return out


def chance_at_least(res: dict, target: float) -> float | None:
    if target <= 0 or "_year_samples" not in res:
        return None
    return float((res["_year_samples"] >= target).mean())
