"""One account tier: how good a fit the account is (the lead list's priority) crossed with how timely it is (its buying signals and where we
stand with it). The same function serves every page, so Accounts, Buying signals, Today and the account page always show the same tier.

Fit is the team's own ranking on the lead list: P1, P2, P3, or not ranked. Timing, from what the platform holds:
  * now   - a hot signal score (``scoring.HOT``), or we are in talks with them (relationship ``in_talks``, from a person or from Pipedrive);
  * soon  - a warm signal score;
  * watch - a weak signal (score band "watch");
  * quiet - nothing current.
An account with a reason to stop or wait (``account_scores.stopped``) is tier 4 whatever else is true.

                 now  soon  watch  quiet
    P1            1    1     2      2
    P2            1    2     3      3
    P3            2    3     4      4
    not ranked    3    3     4      4

Tier 1 "Act now", 2 "Work this month", 3 "Keep warm", 4 "Monitor". It orders work; it is not a probability of winning.
"""
from __future__ import annotations

from typing import Any, Iterable

from .db import Conn, qa

FITS = ("P1", "P2", "P3")
MATRIX: dict[str, dict[str, int]] = {
    "P1": {"now": 1, "soon": 1, "watch": 2, "quiet": 2},
    "P2": {"now": 1, "soon": 2, "watch": 3, "quiet": 3},
    "P3": {"now": 2, "soon": 3, "watch": 4, "quiet": 4},
    "none": {"now": 3, "soon": 3, "watch": 4, "quiet": 4},
}
LABEL = {1: "Act now", 2: "Work this month", 3: "Keep warm", 4: "Monitor"}
FIT_TEXT = {"P1": "P1 on your lead list", "P2": "P2 on your lead list", "P3": "P3 on your lead list", "none": "Not ranked on your lead list"}
TIMING_TEXT = {"now": "hot buying signals", "soon": "warm buying signals", "watch": "an early signal", "quiet": "no current signal"}


def timing(band: str | None, relationship: str | None = None) -> str:
    if relationship == "in_talks" or band == "hot":
        return "now"
    return {"warm": "soon", "watch": "watch"}.get(band or "", "quiet")


def tier(priority: str | None, band: str | None, relationship: str | None = None, *, stopped: bool = False) -> dict[str, Any]:
    fit = priority if priority in FITS else "none"
    t = timing(band, relationship)
    if stopped:
        return {"n": 4, "label": LABEL[4], "fit": fit, "timing": "stopped", "why": f"{FIT_TEXT[fit]} · a reason to stop or wait was found"}
    n = MATRIX[fit][t]
    why = FIT_TEXT[fit] + " · " + ("in talks with us" if relationship == "in_talks" and band != "hot" else TIMING_TEXT[t])
    return {"n": n, "label": LABEL[n], "fit": fit, "timing": t, "why": why}


def tiers_for(conn: Conn, org_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """The tier of each account, from one query (lists call this with every row's id)."""
    ids = [str(i) for i in org_ids if i]
    if not ids:
        return {}
    rows = qa(conn, "SELECT o.id::text AS id, o.list_priority, o.relationship, s.band, coalesce(s.stopped, false) AS stopped "
                    "FROM organizations o LEFT JOIN account_scores s ON s.organization_id=o.id WHERE o.id = ANY(%s::uuid[])", (ids,))
    return {r["id"]: tier(r["list_priority"], r["band"], r["relationship"], stopped=r["stopped"]) for r in rows}
