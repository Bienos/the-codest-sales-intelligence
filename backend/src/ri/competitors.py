"""Competitor tracker: who we compete with, what they changed, and where we meet them (docs/competitor-tracker.md).

Rows hold evidence; everything judged from them is worked out here on every read, so a number on the page always matches the rows behind it:

* TIERS. Firms marked as reference are tier 3. Of the rest, the seven with the highest overlap score are tier 1 and the others tier 2.
* OVERLAP (0-100) says who is likely to meet us in a deal, not who is better: same buyers (financial-services score, 30), same way of selling
  (closeness to us on the people-to-outcomes axis, 30), same markets (5 for each of the UK, DACH, the Nordics and Poland), similar size (20).
  Each part is rounded and the total is their sum, so the parts shown always add up.
* PATTERNS count firms that meet a rule (homepage headline mentions AI, sells fixed steps, shows ISO 27001 ...). The briefing text uses
  placeholders such as {fs2} that become "22 of 36" from the same counts.
* WHERE WE MEET THEM: tracked firms named as a supplier at an account by vendor intelligence or in supplier edges (scoped to the accounts the
  person may see), public contract awards they won (TED), and Pipedrive deals that name them (head-to-head, counts only).

The seed (rules/competitors_seed.json) is research from public sources. Loading it never overwrites a row a person has edited.
"""
from __future__ import annotations

import json
import math
import re
import statistics
from pathlib import Path
from typing import Any

from .db import Conn, jb, qa
from .research.vendor_intel import canon, name_key


def watch_status(conn: Conn) -> dict[str, Any]:
    from .competitor_watch import watch_status as ws
    return ws(conn)

SEED_PATH = Path(__file__).resolve().parents[2] / "rules" / "competitors_seed.json"
PL_PATH = Path(__file__).resolve().parents[2] / "rules" / "competitors_pl.json"     # English text of the seed -> Polish, for the Polish UI
# Values that are names, quotes, links or codes: never translated (a homepage headline is quoted word for word).
NO_TRANSLATE = {"name", "names", "aliases", "certs", "clients", "hero", "url", "source", "key", "id", "origin", "grade", "type", "status", "verdict",
                "date", "asOf", "seedAsOf", "trend", "strength", "markets", "edited_by", "added_by", "defaultCompare", "watch", "overlap", "can", "updated_at"}
_PL: dict[str, str] | None = None


def _pl() -> dict[str, str]:
    global _PL
    if _PL is None:
        try:
            _PL = json.loads(PL_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            _PL = {}
    return _PL


def translate(x: Any, lang: str, _key: str | None = None) -> Any:
    """Swap every known English string for its Polish version; anything not in the dictionary (a fact a person typed) stays as it is."""
    if lang != "pl":
        return x
    if isinstance(x, str):
        return _pl().get(x, x)
    if isinstance(x, dict):
        return {k: (v if k in NO_TRANSLATE else translate(v, lang, k)) for k, v in x.items()}
    if isinstance(x, list):
        return [translate(v, lang, _key) for v in x]
    return x
MOVE_TYPES = ("positioning", "offer", "pricing", "vertical", "market", "ma_buy", "ma_sold", "capital", "leadership", "hiring", "partner", "client", "content",
              "results", "award")
GRADES = ("official", "reported", "estimate")
OUR_MARKETS = ("UK", "DACH", "Nordics", "PL")
TIER1_SIZE = 7
MIN_DECIDED = 5                 # win rate is shown only after this many decided deals against a firm
ROLES = ("incumbent", "shortlisted", "won_against", "lost_to", "unknown")
# Profile fields a person may change from the page or the API; anything else stays as the research left it.
EDITABLE = {"name", "url", "hq", "founded", "archetype", "ownership", "headcount", "revenue", "hero", "oneLiner", "relevance", "fitNote", "services", "models",
            "entryOffer", "offers", "ai", "fs", "markets", "offices", "clutch", "clients", "certs", "pos", "strengths", "weaknesses", "howWeWin", "questions",
            "quickDismiss", "jobs", "aliases", "plDelivery"}
FACT_FIELDS = {"ownership", "headcount", "revenue", "hero", "jobs"}
COLLECTED = ("jobs", "hero")            # facts the weekly collection keeps current (ri/competitor_watch.py)       # each is {value, source, asOf, grade}


# ------------------------------------------------------------------ loading the seed
def _move_row(key: str, m: dict[str, Any]) -> tuple:
    return (key, str(m["date"]), m["type"], m["headline"], m.get("detail") or None, (m.get("sowhat") or "").strip() or None, m["source"], m["grade"],
            int(m["impact"]))


def load_seed(conn: Conn, path: Path = SEED_PATH) -> dict[str, int]:
    """Idempotent. Inserts new firms and moves; refreshes rows still owned by the seed; leaves rows a person edited alone."""
    seed = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {"competitors": 0, "moves": 0, "market": 0}
    for c in [seed["us"], *seed["competitors"]]:
        is_us = c is seed["us"]
        profile = {k: v for k, v in c.items() if k != "moves"}
        had = conn.execute("SELECT profile FROM competitors WHERE key=%s", (c["key"],)).fetchone()
        for f in COLLECTED:              # what the weekly collection measured since the seed was written stays
            mine = ((had or {}).get("profile") or {}).get(f) or {}
            if str(mine.get("asOf") or "") > str((profile.get(f) or {}).get("asOf") or ""):
                profile[f] = mine
        r = conn.execute(
            "INSERT INTO competitors(key, name, is_us, reference, origin, profile, updated_by) VALUES (%s,%s,%s,%s,%s,%s,'seed') "
            "ON CONFLICT (key) DO UPDATE SET name=EXCLUDED.name, reference=EXCLUDED.reference, origin=EXCLUDED.origin, profile=EXCLUDED.profile, updated_at=now() "
            "WHERE competitors.updated_by='seed' RETURNING key",
            (c["key"], c["name"], is_us, bool(c.get("reference")), c["origin"], jb(profile))).fetchone()
        out["competitors"] += 1 if r else 0
        for m in c.get("moves") or []:
            r = conn.execute(
                "INSERT INTO competitor_moves(competitor_key, happened_on, move_type, headline, detail, so_what, source_url, grade, impact, status, added_by, reviewed_by, reviewed_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'confirmed','seed','seed',now()) ON CONFLICT (competitor_key, happened_on, headline) DO NOTHING RETURNING id",
                _move_row(c["key"], m)).fetchone()
            out["moves"] += 1 if r else 0
    docs = {"market": seed["market"], "copy": seed["copy"], "method": seed["method"],
            "meta": {"asOf": seed["asOf"], "moveTypes": seed["moveTypes"], "defaultCompare": seed["defaultCompare"]}}
    for name, content in docs.items():
        r = conn.execute("INSERT INTO competitor_market(name, content, updated_by) VALUES (%s,%s,'seed') ON CONFLICT (name) DO UPDATE SET content=EXCLUDED.content, "
                         "updated_at=now() WHERE competitor_market.updated_by='seed' RETURNING name", (name, jb(content))).fetchone()
        out["market"] += 1 if r else 0
    conn.commit()
    return out


# ------------------------------------------------------------------ scores worked out from the rows
def rnd(v: float) -> int:
    """Round half up, the same rule as the page's Math.round."""
    return int(math.floor(v + 0.5 + 1e-9))


def _n(c: dict[str, Any]) -> int | None:
    n = (c.get("headcount") or {}).get("n")
    return n if isinstance(n, (int, float)) else None


def overlap(c: dict[str, Any], us: dict[str, Any]) -> dict[str, int]:
    fs = ((c.get("fs") or {}).get("score") or 0) / 3 * 30
    y, uy = (c.get("pos") or {}).get("y", 5), (us.get("pos") or {}).get("y", 5)
    md = max(0.0, 1 - abs(float(y) - float(uy)) / 6) * 30
    mk = len([m for m in OUR_MARKETS if m in (c.get("markets") or [])]) / 4 * 20
    n = _n(c)
    sz = (0.5 if n is None else 1 if n < 500 else 0.6 if n < 2000 else 0.25 if n < 6000 else 0) * 20
    parts = {"buyers": rnd(fs), "selling": rnd(md), "markets": rnd(mk), "size": rnd(sz)}
    return {**parts, "total": sum(parts.values())}


def assign_tiers(comps: list[dict[str, Any]], us: dict[str, Any]) -> dict[str, int]:
    """Reference firms are tier 3; the seven highest overlap scores among the rest are tier 1. Ties: higher finance score, then closer size."""
    un = _n(us) or 150
    live = [c for c in comps if not c.get("reference")]
    ranked = sorted(live, key=lambda c: (-overlap(c, us)["total"], -((c.get("fs") or {}).get("score") or 0), abs((_n(c) or 1000) - un), c["name"]))
    tiers = {c["key"]: 3 for c in comps if c.get("reference")}
    for i, c in enumerate(ranked):
        tiers[c["key"]] = 1 if i < TIER1_SIZE else 2
    return tiers


def _certs(c: dict[str, Any]) -> str:
    return " ".join(c.get("certs") or [])


RULES: dict[str, tuple[str, Any]] = {
    "hero_ai": ("Homepage headline mentions AI or agents", lambda c: bool(re.search(r"\bAI\b|agentic|artificial intelligence", (c.get("hero") or {}).get("value") or "", re.I))),
    "beyond_hours": ("Publicly offer something other than hours: fixed scope, sprints, pods, outcomes, managed service",
                     lambda c: bool(re.search(r"fixed|outcome|subscription|sprint|pod|packaged|managed", " ".join(c.get("models") or []), re.I))),
    "fs2": ("Financial services is a lead industry (score 2 or 3)", lambda c: ((c.get("fs") or {}).get("score") or 0) >= 2),
    "security": ("Show ISO 27001, SOC 2 or DORA readiness where we looked", lambda c: bool(re.search(r"ISO 27001|ISO/IEC 27001|SOC 2|DORA", _certs(c)))),
    "ai_badge": ("Show an AI model vendor partner badge (Anthropic, OpenAI)", lambda c: bool(re.search(r"Claude|Anthropic|OpenAI", _certs(c)))),
    "nordics": ("Target the Nordics", lambda c: "Nordics" in (c.get("markets") or [])),
    "entry": ("Sell a named, fixed-length first step (sprint, workshop, assessment)", lambda c: bool(c.get("entryOffer"))),
    "ma": ("Bought, sold or merged in the last two years", None),        # needs the moves; worked out in indicators()
}


def indicators(comps: list[dict[str, Any]], moves_by_key: dict[str, list[dict[str, Any]]], as_of: str) -> dict[str, dict[str, Any]]:
    n = len(comps)
    out: dict[str, dict[str, Any]] = {}
    for key, (label, fn) in RULES.items():
        if fn is None:
            year = int(as_of[:4]) - 2
            names = [c["name"] for c in comps if any(m["move_type"] in ("ma_buy", "ma_sold") and m["happened_on"][:7] >= f"{year}{as_of[4:7]}"
                                                     for m in moves_by_key.get(c["key"], []))]
            out[key] = {"label": label, "count": len(names), "of": n, "names": names}
            continue
        names = [c["name"] for c in comps if fn(c)]
        out[key] = {"label": label, "count": len(names), "of": n, "names": names}
    known = [c for c in comps if (c.get("revenue") or {}).get("trend") in ("up", "down", "flat")]
    down = [c["name"] for c in known if c["revenue"]["trend"] == "down"]
    out["down"] = {"label": "Revenue fell in the latest reported year (of those with data)", "count": len(down), "of": len(known), "names": down}
    foreign = [c for c in comps if c.get("origin") != "PL"]
    pl = [c["name"] for c in foreign if c.get("plDelivery")]
    out["pl_delivery"] = {"label": "Non-Polish firms with their own delivery in Poland", "count": len(pl), "of": len(foreign), "names": pl}
    return out


MEASURES: list[tuple[str, str, float | None]] = [          # (key, label, fixed maximum or None for "largest seen")
    ("headcount", "Headcount", None), ("reviews", "Clutch reviews", None), ("rating", "Clutch rating", 5), ("fs", "Financial-services focus", 3),
    ("ai", "AI in the offer", 3), ("markets", "Our four markets covered", 4), ("proof", "Proof points shown", 4), ("roles", "Open roles on justjoin.it", None),
    ("moves", "Moves in the last 12 months", None)]
PROOF = (("security", "ISO 27001, SOC 2 or DORA"), ("entry", "Named fixed-length first step"), ("beyond_hours", "Sells more than hours"),
         ("ai_badge", "AI vendor partner badge"))


def scorecard(c: dict[str, Any], moves: list[dict[str, Any]], as_of: str) -> dict[str, Any]:
    """The numbers every comparison on the page is drawn from. None means not found, never zero."""
    num = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else None    # noqa: E731
    cl = c.get("clutch") or {}
    flags = {k: bool(RULES[k][1](c)) for k, _ in PROOF}
    cut = f"{int(as_of[:4]) - 1}{as_of[4:10]}" if len(as_of) >= 7 else ""
    return {"headcount": _n(c), "reviews": num(cl.get("reviews")), "rating": num(cl.get("rating")), "fs": num((c.get("fs") or {}).get("score")),
            "ai": num((c.get("ai") or {}).get("score")), "markets": len([m for m in OUR_MARKETS if m in (c.get("markets") or [])]),
            "proof": sum(flags.values()), "flags": flags, "roles": num((c.get("jobs") or {}).get("n")),
            "moves": len([m for m in moves if str(m.get("happened_on") or m.get("date") or "") >= cut]) if cut else None}


def measure_stats(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for key, label, fixed in MEASURES:
        vals = sorted(v for v in (x.get(key) for x in cards) if isinstance(v, (int, float)))
        out.append({"key": key, "label": label, "max": fixed, "t1": {"n": len(vals), "min": vals[0] if vals else None, "max": vals[-1] if vals else None,
                                                                   "median": _median(vals)}})
    return out


def _median(vals: list[Any]) -> float | None:
    v = [x for x in vals if isinstance(x, (int, float))]
    return statistics.median(v) if v else None


def standing(us: dict[str, Any], t1: list[dict[str, Any]], ind_rules: dict[str, Any] = RULES) -> list[dict[str, Any]]:
    """The Codest against the tier 1 median, on things public data can measure."""
    def verdict(a: Any, b: Any) -> str:
        if a is None or b is None:
            return "level"
        return "ahead" if a > b else "behind" if a < b else "level"
    mk = lambda c: len([m for m in OUR_MARKETS if m in (c.get("markets") or [])])     # noqa: E731
    fmt = lambda v: "–" if v is None else f"{v:g}"                                     # noqa: E731
    rows = []
    ur = (us.get("clutch") or {}).get("reviews")
    m = _median([(c.get("clutch") or {}).get("reviews") for c in t1])
    last = (us.get("clutch") or {}).get("latest")
    rows.append({"what": "Clutch reviews", "basis": "Count on Clutch." + (f" Ours: last review {last}." if last else ""), "us": fmt(ur), "them": fmt(m), "verdict": verdict(ur, m)})
    for label, field, basis in (("Financial-services focus", "fs", "Rubric 0–3 (Method)."), ("AI in the offer", "ai", "Rubric 0–3 (Method).")):
        u = (us.get(field) or {}).get("score")
        m = _median([(c.get(field) or {}).get("score") for c in t1])
        rows.append({"what": label, "basis": basis, "us": fmt(u), "them": fmt(m), "verdict": verdict(u, m)})
    m = _median([mk(c) for c in t1])
    rows.append({"what": "Our four markets covered", "basis": "UK, DACH, Nordics, Poland.", "us": fmt(mk(us)), "them": fmt(m), "verdict": verdict(mk(us), m)})
    for label, key, basis in (("Named, fixed-length first step", "entry", "A priced or time-boxed sprint, workshop or assessment."),
                              ("Security or DORA proof on the site", "security", "ISO 27001, SOC 2, DORA.")):
        fn = ind_rules[key][1]
        n = len([c for c in t1 if fn(c)])
        ours = fn(us)
        rows.append({"what": label, "basis": basis, "us": "Yes" if ours else "No", "them": f"{n} of {len(t1)}",
                     "verdict": "ahead" if ours and n < len(t1) else "behind" if not ours and n > len(t1) / 2 else "level"})
    m = _median([_n(c) for c in t1])
    un = _n(us)
    rows.append({"what": "Headcount", "basis": "Latest public figure. Size is not a score: smaller means faster starts, larger means deeper benches.",
                 "us": f"~{un}" if un else "–", "them": f"~{m:g}" if m else "–", "verdict": "level",
                 "label": "Smaller" if un and m and m > un * 2 else "Larger" if un and m and un > m * 2 else "Similar"})
    return rows


def fill(text: str, subs: dict[str, str]) -> str:
    """Replaces {key} placeholders; an unknown placeholder is left visible rather than guessed."""
    return re.sub(r"\{([A-Za-z0-9_]+)\}", lambda mt: subs.get(mt.group(1), mt.group(0)), text or "")


# ------------------------------------------------------------------ where we meet them
def name_keys(c: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for n in [c.get("name"), *(c.get("aliases") or [])]:
        if not n:
            continue
        out.add(name_key(n))
        out.update(name_key(v) for v in canon(n)[2])
    return {k for k in out if len(k) >= 3}


def _matcher(comps: list[dict[str, Any]]) -> dict[str, str]:
    m: dict[str, str] = {}
    for c in comps:
        for k in name_keys(c):
            m.setdefault(k, c["key"])
    return m


def presence(conn: Conn, comps: list[dict[str, Any]], scope_sql: str, scope_args: list[Any], *, key: str | None = None) -> dict[str, dict[str, Any]]:
    """Per firm: target accounts (that this person may see) where it is named as a supplier, and public contract awards it won."""
    match = _matcher(comps)
    out: dict[str, dict[str, Any]] = {c["key"]: {"accounts": [], "awards": []} for c in comps}
    seen: set[tuple[str, str]] = set()
    rows = qa(conn, f"SELECT DISTINCT ON (v.organization_id) v.organization_id, o.display_name, v.content, v.finished_at FROM account_vendor_intel v "
                    f"JOIN organizations o ON o.id=v.organization_id WHERE v.status='done' AND v.content IS NOT NULL AND ({scope_sql}) "
                    f"ORDER BY v.organization_id, v.created_at DESC", scope_args)
    for r in rows:
        for v in (r["content"] or {}).get("vendors") or []:
            k = match.get(name_key(v.get("name")))
            if not k or (key and k != key) or (k, str(r["organization_id"])) in seen:
                continue
            seen.add((k, str(r["organization_id"])))
            ev = (v.get("evidence") or [{}])[0]
            out[k]["accounts"].append({"organization_id": str(r["organization_id"]), "name": r["display_name"], "relationship": v.get("relationship"),
                                       "role": v.get("role"), "source": ev.get("source_url"), "quote": ev.get("quote"), "date": ev.get("date"), "via": "vendor_intel"})
    for r in qa(conn, f"SELECT se.supplier_name, se.buyer_org_id, o.display_name, se.relation, se.scope_text FROM supplier_edges se JOIN organizations o ON o.id=se.buyer_org_id "
                      f"WHERE ({scope_sql})", scope_args):
        k = match.get(name_key(r["supplier_name"]))
        if not k or (key and k != key) or (k, str(r["buyer_org_id"])) in seen:
            continue
        seen.add((k, str(r["buyer_org_id"])))
        out[k]["accounts"].append({"organization_id": str(r["buyer_org_id"]), "name": r["display_name"], "relationship": r["relation"], "role": r["scope_text"],
                                   "source": None, "quote": None, "date": None, "via": "finding"})
    winners = {r["w"]: match.get(name_key(r["w"])) for r in qa(conn, "SELECT DISTINCT unnest(winners) AS w FROM awarded_contracts WHERE relevant")}
    hits = {w: k for w, k in winners.items() if k and (not key or k == key)}
    if hits:
        for r in qa(conn, "SELECT notice_id, buyer_name, buyer_country, winners, awarded_on, expected_end, end_basis, official_url FROM awarded_contracts "
                          "WHERE relevant AND winners && %s::text[] ORDER BY awarded_on DESC NULLS LAST", (list(hits),)):
            for k in {hits[w] for w in r["winners"] if w in hits}:
                out[k]["awards"].append({"notice_id": r["notice_id"], "buyer": r["buyer_name"], "country": r["buyer_country"],
                                         "awarded_on": r["awarded_on"].isoformat() if r["awarded_on"] else None,
                                         "expected_end": r["expected_end"].isoformat() if r["expected_end"] else None, "end_basis": r["end_basis"], "url": r["official_url"]})
    return out


def head_to_head(conn: Conn) -> dict[str, Any]:
    """Counts only (no deal names), from deals that name a tracked firm. Won and lost follow the deal's own status in Pipedrive; a role the seller
    recorded fills in when the deal is not synced."""
    rows = qa(conn, "SELECT dc.crm_deal_id, dc.competitor_key, dc.role, dc.reason, d.status FROM deal_competitors dc LEFT JOIN crm_deals d ON d.crm_id=dc.crm_deal_id")
    total = len({r["crm_deal_id"] for r in rows})
    per: dict[str, dict[str, Any]] = {}
    for r in rows:
        s = per.setdefault(r["competitor_key"], {"met": 0, "won": 0, "lost": 0, "reasons": {}})
        s["met"] += 1
        won = r["status"] == "won" or (r["status"] is None and r["role"] == "won_against")
        lost = r["status"] == "lost" or (r["status"] is None and r["role"] == "lost_to")
        s["won"] += won
        s["lost"] += lost
        if lost and r["reason"]:
            s["reasons"][r["reason"]] = s["reasons"].get(r["reason"], 0) + 1
    out = {}
    for k, s in per.items():
        decided = s["won"] + s["lost"]
        share = s["met"] / total if total else 0
        rate = s["won"] / decided if decided >= MIN_DECIDED else None
        out[k] = {"met": s["met"], "won": s["won"], "lost": s["lost"], "share": round(share, 3), "win_rate": None if rate is None else round(rate, 3),
                  "threat": None if rate is None else round(share / max(rate, 0.05), 2),
                  "top_lost_reason": max(s["reasons"], key=s["reasons"].get) if s["reasons"] else None}
    return {"deals": total, "by_competitor": out, "min_decided": MIN_DECIDED}


# ------------------------------------------------------------------ the whole tracker, as the page reads it
def _move_out(m: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(m["id"]), "date": m["happened_on"], "type": m["move_type"], "headline": m["headline"], "detail": m["detail"], "sowhat": m["so_what"],
            "source": m["source_url"], "grade": m["grade"], "impact": m["impact"], "status": m["status"], "added_by": m["added_by"]}


def tracker(conn: Conn, scope_sql: str = "TRUE", scope_args: list[Any] | None = None, lang: str = "en") -> dict[str, Any]:
    docs = {r["name"]: r["content"] for r in qa(conn, "SELECT name, content FROM competitor_market")}
    rows = qa(conn, "SELECT key, name, is_us, reference, origin, profile, updated_by, updated_at FROM competitors WHERE retired_at IS NULL ORDER BY name")
    if not rows:
        return {"empty": True}
    moves = qa(conn, "SELECT * FROM competitor_moves WHERE status='confirmed' ORDER BY happened_on DESC, created_at DESC")
    by_key: dict[str, list[dict[str, Any]]] = {}
    for m in moves:
        by_key.setdefault(m["competitor_key"], []).append(m)

    def prof(r: dict[str, Any]) -> dict[str, Any]:
        return {**r["profile"], "key": r["key"], "id": r["key"], "name": r["name"], "origin": r["origin"], "reference": r["reference"],
                "edited_by": None if r["updated_by"] == "seed" else r["updated_by"], "updated_at": r["updated_at"].isoformat()}
    us_row = next((r for r in rows if r["is_us"]), None)
    us = prof(us_row) if us_row else {"key": "us", "id": "us", "name": "The Codest", "origin": "PL", "pos": {"x": 5, "y": 5}}
    comps = [prof(r) for r in rows if not r["is_us"]]
    tiers = assign_tiers(comps, us)
    meta = docs.get("meta") or {}
    as_of = max([meta.get("asOf") or "", *(m["happened_on"] for m in moves)]) or meta.get("asOf") or ""
    pres = presence(conn, comps, scope_sql, scope_args or [])
    h2h = head_to_head(conn)
    for c in comps:
        c["tier"] = tiers[c["key"]]
        c["overlap"] = overlap(c, us)
        c["moves"] = [_move_out(m) for m in by_key.get(c["key"], [])]
        c["presence"] = {"accounts": len(pres[c["key"]]["accounts"]), "awards": len(pres[c["key"]]["awards"])}
        c["h2h"] = h2h["by_competitor"].get(c["key"])
        c["score"] = scorecard(c, by_key.get(c["key"], []), as_of)
        if c["tier"] == 1:
            o = c["overlap"]
            fit = translate((c.get("fitNote") or "").strip(), lang)
            if lang == "pl":
                why = f"Zbieżność {o['total']} na 100 (klienci {o['buyers']}/30, sposób sprzedaży {o['selling']}/30, rynki {o['markets']}/20, wielkość {o['size']}/20)."
            else:
                why = f"Overlap {o['total']} of 100 (buyers {o['buyers']}/30, way of selling {o['selling']}/30, markets {o['markets']}/20, size {o['size']}/20)."
            c["tierWhy"] = f"{fit} {why}".strip()
    us["tier"] = 0
    us["moves"] = [_move_out(m) for m in by_key.get(us["key"], [])]
    us["score"] = {**scorecard(us, by_key.get(us["key"], []), as_of), "moves": None}        # our own moves are not tracked here
    ind = indicators(comps, by_key, meta.get("asOf") or as_of)
    t1 = [c for c in comps if c["tier"] == 1]
    subs = {k: f"{v['count']} {'z' if lang == 'pl' else 'of'} {v['of']}" for k, v in ind.items()}
    subs["N"] = str(len(comps))
    m = _median([(c.get("clutch") or {}).get("reviews") for c in t1])
    subs["t1_reviews"] = f"{m:g}" if m is not None else ("brak danych" if lang == "pl" else "unknown")
    copy = translate(docs.get("copy") or {}, lang)          # templates are translated before their numbers go in
    patterns = []
    for p in copy.get("patterns") or []:
        p = dict(p)
        k = p.pop("indicatorKey", None)
        if k:
            p["indicator"] = ind.get(k) or {"label": k, "count": 0, "of": 0}
        patterns.append(p)
    market = docs.get("market") or {}
    return translate({"asOf": as_of, "seedAsOf": meta.get("asOf"), "moveTypes": meta.get("moveTypes") or {t: {"label": t} for t in MOVE_TYPES},
            "defaultCompare": [k for k in meta.get("defaultCompare") or [] if k in tiers][:4], "us": us, "competitors": comps,
            "lede": fill(copy.get("lede") or "", subs), "briefing": [{k: fill(v, subs) for k, v in b.items()} for b in copy.get("briefing") or []],
            "landscapeNotes": copy.get("landscapeNotes") or [], "patterns": patterns, "indicators": ind, "standing": standing(us, t1),
            "bellwethers": market.get("bellwethers") or [], "alternatives": market.get("alternatives") or [], "buyers": market.get("buyers") or [],
            "deals": market.get("deals") or [], "rates": market.get("rates") or [], "method": docs.get("method") or {}, "headToHead": h2h,
            "measures": measure_stats([c["score"] for c in comps if c["tier"] == 1]), "proofLabels": dict(PROOF),
            "watch": watch_status(conn)}, lang)


# ------------------------------------------------------------------ changes people make
class InvalidChange(ValueError):
    """A change that would break the evidence rules; the API answers 422 with this text."""


def _check_fact(name: str, f: Any) -> None:
    if not isinstance(f, dict) or "value" not in f:
        raise InvalidChange(f"{name} needs a value")
    if f.get("value") not in (None, "", "Not found"):
        if not str(f.get("source") or "").startswith("https://"):
            raise InvalidChange(f"{name} needs an https source link")
        if f.get("grade") not in GRADES:
            raise InvalidChange(f"{name} needs a grade: official, reported or estimate")
        if not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", str(f.get("asOf") or "")):
            raise InvalidChange(f"{name} needs the date of its source (YYYY, YYYY-MM or YYYY-MM-DD)")


def update_profile(conn: Conn, key: str, changes: dict[str, Any], actor: str) -> dict[str, Any]:
    unknown = set(changes) - EDITABLE - {"reference", "retired"}
    if unknown:
        raise InvalidChange("these fields cannot be changed here: " + ", ".join(sorted(unknown)))
    for f in FACT_FIELDS & set(changes):
        _check_fact(f, changes[f])
    if "pos" in changes:
        p = changes["pos"]
        if not (isinstance(p, dict) and all(isinstance(p.get(a), (int, float)) and 0 <= p[a] <= 10 for a in ("x", "y")) and str(p.get("why") or "").strip()):
            raise InvalidChange("a map placement needs x and y from 0 to 10 and a reason")
    row = conn.execute("SELECT profile, name FROM competitors WHERE key=%s FOR UPDATE", (key,)).fetchone()
    if not row:
        raise KeyError(key)
    before = row["profile"]
    profile = {**before, **{k: v for k, v in changes.items() if k in EDITABLE}}
    conn.execute("UPDATE competitors SET profile=%s, name=%s, reference=coalesce(%s::boolean, reference), retired_at=CASE WHEN %s::boolean IS NULL THEN retired_at WHEN %s::boolean THEN now() ELSE NULL END, "
                 "updated_by=%s, updated_at=now() WHERE key=%s",
                 (jb(profile), changes.get("name") or row["name"], changes.get("reference"), changes.get("retired"), changes.get("retired"), actor, key))
    return {"before": {k: before.get(k) for k in changes}, "after": changes}


def add_competitor(conn: Conn, *, key: str, name: str, origin: str, url: str, actor: str) -> None:
    profile = {"key": key, "name": name, "origin": origin, "url": url, "markets": [], "offers": [], "pos": {"x": 5, "y": 5, "why": "Not placed yet: research pending."},
               "fs": {"score": 0, "note": "Not researched yet."}, "ai": {"score": 0, "value": ""}, "oneLiner": "Added by hand; research pending."}
    conn.execute("INSERT INTO competitors(key, name, origin, profile, updated_by) VALUES (%s,%s,%s,%s,%s)", (key, name, origin, jb(profile), actor))


def add_move(conn: Conn, key: str, m: dict[str, Any], actor: str, *, confirm: bool) -> str:
    if m["type"] not in MOVE_TYPES:
        raise InvalidChange("unknown type of move")
    if confirm and int(m["impact"]) >= 2 and not (m.get("sowhat") or "").strip():
        raise InvalidChange("a medium or high impact move needs a 'So what' before it is confirmed")
    r = conn.execute(
        "INSERT INTO competitor_moves(competitor_key, happened_on, move_type, headline, detail, so_what, source_url, grade, impact, status, added_by, reviewed_by, reviewed_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (competitor_key, happened_on, headline) DO NOTHING RETURNING id",
        (*_move_row(key, m), "confirmed" if confirm else "candidate", actor, actor if confirm else None, "now" if confirm else None)).fetchone()
    if not r:
        raise InvalidChange("this move is already recorded")
    return str(r["id"])


def review_move(conn: Conn, move_id: str, *, status: str, actor: str, impact: int | None = None, so_what: str | None = None) -> dict[str, Any]:
    m = conn.execute("SELECT * FROM competitor_moves WHERE id=%s FOR UPDATE", (move_id,)).fetchone()
    if not m:
        raise KeyError(move_id)
    imp = impact or m["impact"]
    sw = (so_what if so_what is not None else m["so_what"]) or None
    if status == "confirmed" and imp >= 2 and not (sw or "").strip():
        raise InvalidChange("a medium or high impact move needs a 'So what' before it is confirmed")
    conn.execute("UPDATE competitor_moves SET status=%s, impact=%s, so_what=%s, reviewed_by=%s, reviewed_at=now() WHERE id=%s", (status, imp, sw, actor, move_id))
    hero = (m.get("payload") or {}).get("hero")
    if status == "confirmed" and hero:          # a confirmed new homepage headline becomes the headline on record
        fact = {"value": hero, "source": (m["payload"].get("url") or m["source_url"]), "asOf": m["happened_on"], "grade": "official"}
        conn.execute("UPDATE competitors SET profile = jsonb_set(profile, '{hero}', %s) WHERE key=%s", (jb(fact), m["competitor_key"]))
    return {"before": {"status": m["status"], "impact": m["impact"]}, "after": {"status": status, "impact": imp}}


def moves_queue(conn: Conn) -> list[dict[str, Any]]:
    return [{**_move_out(m), "competitor": m["competitor_key"], "name": m["name"]} for m in qa(
        conn, "SELECT m.*, c.name FROM competitor_moves m JOIN competitors c ON c.key=m.competitor_key WHERE m.status='candidate' ORDER BY m.created_at DESC LIMIT 200")]


def record_deal_competitor(conn: Conn, crm_deal_id: int, key: str, role: str, reason: str | None, actor: str) -> None:
    if role not in ROLES:
        raise InvalidChange("unknown role")
    conn.execute("INSERT INTO deal_competitors(crm_deal_id, competitor_key, role, reason, noted_by) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (crm_deal_id, competitor_key) "
                 "DO UPDATE SET role=EXCLUDED.role, reason=EXCLUDED.reason, noted_by=EXCLUDED.noted_by, noted_at=now()", (crm_deal_id, key, role, reason or None, actor))


def from_pipedrive_deal(conn: Conn, deal: dict[str, Any], field_key: str) -> int:
    """Reads the competitor names a seller typed into a Pipedrive deal field (comma or semicolon separated) and records them against the deal.
    A lost deal with one named competitor is recorded as lost to it, with Pipedrive's lost reason. Names that match no tracked firm are ignored."""
    raw = deal.get(field_key)
    if raw is None and isinstance(deal.get("custom_fields"), dict):       # Pipedrive API v2 nests custom fields
        raw = deal["custom_fields"].get(field_key)
    if isinstance(raw, dict):
        raw = raw.get("value")
    if not raw or not isinstance(raw, str):
        return 0
    comps = [{"key": r["key"], "name": r["name"], **r["profile"]} for r in qa(conn, "SELECT key, name, profile FROM competitors WHERE NOT is_us AND retired_at IS NULL")]
    match = _matcher(comps)
    keys = list(dict.fromkeys(k for k in (match.get(name_key(p)) for p in re.split(r"[,;\n]", raw)) if k))
    status = deal.get("status")
    for k in keys:
        role = "won_against" if status == "won" else "lost_to" if status == "lost" and len(keys) == 1 else "unknown"
        record_deal_competitor(conn, int(deal["id"]), k, role, deal.get("lost_reason") if status == "lost" else None, "pipedrive")
    return len(keys)
