"""Was this useful? One vote per seller per item on Today and Buying signals, and the per-rule results the Library shows.

The page sends only the item's key (the one Today and Buying signals already use) and the answer. Everything else is looked up here from
the key, never taken from the page: what kind of item it is, the company (which must be in the seller's scope), the signal and its rule
and rule version, the finding behind it and the model that read the page. A vote changes nothing about the item; it is evidence for
tuning the rules, read together with replies and meetings, which count for more.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .access import Principal, org_scope_sql
from .db import Conn, jb, q1, qa

REASONS: dict[str, str] = {
    "wrong_company": "Wrong company",
    "out_of_date": "Out of date",
    "not_our_work": "Not our kind of work",
    "ghost_job": "Job ad not real or already filled",
    "already_talking": "Already talking to them",
    "no_real_need": "No real need behind it",
    "other": "Other",
}
SHOWN_ON = ("today", "signals", "account", "feed")
KINDS = {"lead_list": "Lead list research", "deadline": "Regulatory deadlines", "tender": "Open tenders"}
_UUID = r"[0-9a-fA-F-]{36}"
_KEY = re.compile(rf"^(?:signal:(?P<s_org>{_UUID}):(?P<s_id>{_UUID})|tender:(?P<t_id>{_UUID})|list:(?P<l_org>{_UUID}):(?P<l_id>[\w.:-]{{1,80}})"
                  rf"|deadline:(?P<d_id>[a-z0-9-]{{1,60}}):(?P<d_org>{_UUID}))$")


class FeedbackError(Exception):
    pass


def resolve(conn: Conn, key: str) -> dict[str, Any]:
    """What a key points at. Raises FeedbackError for a key that is not a known kind of item, or points at nothing."""
    m = _KEY.match(key or "")
    if not m:
        raise FeedbackError("unknown item")
    g = m.groupdict()
    if g["s_id"]:
        s = q1(conn, "SELECT s.id, s.rule_id, s.rule_version, s.event_id, coalesce(s.organization_id, e.subject_org_id) AS org, e.extraction_model_version AS mv "
                     "FROM signals s JOIN events e ON e.id=s.event_id WHERE s.id=%s", (g["s_id"],))
        if s:
            return {"kind": "tender" if s["rule_id"] == "SRC-01" else "signal", "organization_id": str(s["org"]) if s["org"] else g["s_org"],
                    "signal_id": str(s["id"]), "event_id": str(s["event_id"]), "rule_id": s["rule_id"], "rule_version": s["rule_version"], "model_version": s["mv"]}
        e = q1(conn, "SELECT e.id, e.subject_org_id, e.extraction_model_version AS mv, e.event_type, "
                     "(SELECT s.rule_id FROM signals s WHERE s.event_id=e.id AND s.rule_id NOT LIKE 'NEG-%%' ORDER BY s.strength DESC LIMIT 1) AS rule_id "
                     "FROM events e WHERE e.id=%s", (g["s_id"],))
        if e:                       # a tender finding on Buying signals carries its event id
            tender = e["event_type"].startswith("procurement")
            return {"kind": "tender" if tender else "signal", "organization_id": str(e["subject_org_id"]) if e["subject_org_id"] else g["s_org"],
                    "signal_id": None, "event_id": str(e["id"]), "rule_id": e["rule_id"] or ("SRC-01" if tender else None), "rule_version": None, "model_version": e["mv"]}
        raise FeedbackError("unknown item")
    if g["t_id"]:
        n = q1(conn, "SELECT id, buyer_org_id FROM procurement_notices WHERE id=%s", (g["t_id"],))
        if not n:
            raise FeedbackError("unknown item")
        return {"kind": "tender", "organization_id": str(n["buyer_org_id"]) if n["buyer_org_id"] else None, "signal_id": None, "event_id": None,
                "rule_id": "SRC-01", "rule_version": None, "model_version": None}
    org = g["l_org"] or g["d_org"]
    if not q1(conn, "SELECT 1 FROM organizations WHERE id=%s", (org,)):
        raise FeedbackError("unknown item")
    if g["d_id"]:
        ev = q1(conn, "SELECT id FROM events WHERE event_key=%s", (key,))
        return {"kind": "deadline", "organization_id": org, "signal_id": None, "event_id": str(ev["id"]) if ev else None, "rule_id": None,
                "rule_version": None, "model_version": None}
    return {"kind": "lead_list", "organization_id": org, "signal_id": None, "event_id": None, "rule_id": None, "rule_version": None, "model_version": None}


def _in_scope(conn: Conn, p: Principal, org: str | None) -> bool:
    if org is None:
        return True                 # a tender from a buyer that is not one of our accounts: nothing about an account is revealed
    where, params = org_scope_sql(p, "o.id")
    return bool(q1(conn, f"SELECT 1 FROM organizations o WHERE o.id=%s AND ({where})", [org, *params]))     # noqa: S608


def vote(conn: Conn, p: Principal, key: str, *, useful: bool, reason: str | None = None, note: str | None = None, shown_on: str = "today") -> dict[str, Any]:
    if shown_on not in SHOWN_ON:
        raise FeedbackError("unknown page")
    if useful:
        reason = None
    elif reason not in REASONS:
        raise FeedbackError("say why it was not useful")
    note = (note or "").strip()[:300] or None
    t = resolve(conn, key)
    if not _in_scope(conn, p, t["organization_id"]):
        raise FeedbackError("unknown item")
    conn.execute("""
        INSERT INTO signal_feedback(user_id, item_key, item_kind, organization_id, signal_id, event_id, rule_id, rule_version, model_version, useful, reason, note, shown_on)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (user_id, item_key) DO UPDATE SET useful=EXCLUDED.useful, reason=EXCLUDED.reason, note=EXCLUDED.note, shown_on=EXCLUDED.shown_on,
            rule_id=EXCLUDED.rule_id, rule_version=EXCLUDED.rule_version, model_version=EXCLUDED.model_version, updated_at=now()""",
                 (p.user_id, key, t["kind"], t["organization_id"], t["signal_id"], t["event_id"], t["rule_id"], t["rule_version"], t["model_version"],
                  useful, reason, note, shown_on))
    conn.execute("INSERT INTO product_events(user_id, event, organization_id, rule_version, detail) VALUES (%s,'signal_feedback',%s,%s,%s)",
                 (p.user_id, t["organization_id"], t["rule_version"], jb({"key": key, "useful": useful, "reason": reason, "rule_id": t["rule_id"], "shown_on": shown_on})))
    return {"key": key, "useful": useful, "reason": reason}


def undo(conn: Conn, p: Principal, key: str) -> None:
    conn.execute("DELETE FROM signal_feedback WHERE user_id=%s AND item_key=%s", (p.user_id, key))


def mine(conn: Conn, p: Principal, keys: list[str]) -> dict[str, dict[str, Any]]:
    """The caller's own votes on these items, so a page shows what was already answered."""
    if not keys:
        return {}
    rows = qa(conn, "SELECT item_key, useful, reason FROM signal_feedback WHERE user_id=%s AND item_key = ANY(%s)", (p.user_id, keys[:200]))
    return {r["item_key"]: {"useful": r["useful"], "reason": r["reason"]} for r in rows}


def by_rule(conn: Conn, days: int = 90, now: datetime | None = None) -> list[dict[str, Any]]:
    """Per rule (or per kind of item without a rule): votes in the window, the share found useful, and the reasons given against it.
    Everyone's votes count, as the rules are shared; nobody's name is shown."""
    since = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    rows = qa(conn, """
        SELECT coalesce(rule_id, item_kind) AS k, max(rule_id) AS rule_id, count(*) AS votes, count(*) FILTER (WHERE useful) AS useful,
               count(DISTINCT user_id) AS people, max(updated_at) AS last_vote_at
        FROM signal_feedback WHERE updated_at >= %s GROUP BY 1 ORDER BY count(*) DESC, 1""", (since,))
    reasons: dict[str, dict[str, int]] = {}
    for r in qa(conn, "SELECT coalesce(rule_id, item_kind) AS k, reason, count(*) AS n FROM signal_feedback WHERE updated_at >= %s AND reason IS NOT NULL "
                      "GROUP BY 1, 2", (since,)):
        reasons.setdefault(r["k"], {})[r["reason"]] = int(r["n"])
    names = {r["id"]: r["signal"] for r in qa(conn, "SELECT id, signal FROM legacy_signals")}
    out = []
    for r in rows:
        votes, useful = int(r["votes"]), int(r["useful"])
        out.append({"rule_id": r["rule_id"], "key": r["k"], "name": names.get(r["rule_id"]) if r["rule_id"] else KINDS.get(r["k"], r["k"]),
                    "votes": votes, "useful": useful, "not_useful": votes - useful, "people": int(r["people"]),
                    "useful_share": round(useful / votes, 2) if votes else None, "reasons": reasons.get(r["k"], {}), "last_vote_at": r["last_vote_at"]})
    return out
