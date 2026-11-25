"""Buying signals: the companies on the list where something is happening now that suggests they need engineering help, and why.

A signal counts as high intent when it is live and checked (an active, verified signal on a verified event, backed by a supported,
anchored claim with a web source, inside the window) at a target-market company the person may see, and at least one of these holds:

* the rule says outside demand is evidenced or inferred (demand tier D1 or D2), for example an open tender or a call for contractors;
* the rule is one of the strongest (strength 3);
* the rule is a kind of event that usually starts a buying conversation (``HIGH_INTENT_RULES``): a vendor or integrator selected for a
  programme, hiring beyond capacity, a funded programme under way, a new technology leader in the first months, funding raised to build,
  an acquisition with integration work, or a known contact in a new role.

Open tenders for software work at target companies come from the same read model as the findings archive. Accounts with a stop signal
(a hiring freeze, an exclusive supplier) are held back and only counted. Context-only news (a market launch, a new product line, a
partnership) stays in the findings archive.

The answer is organised by company, not by kind of signal: one entry per company with every high-intent signal it has (one per kind,
the strongest and newest first, older news of the same kind folded into ``more``). Each company gets a strength from 1 to 3:

* 3 (strong) when they asked for outside help or it is likely (an open tender, demand tier D1 or D2);
* otherwise the strongest rule's strength (1 to 3), one step higher when two or more different kinds of signal are live at once.

``kinds`` counts companies per kind of signal for the current search, region and window but before the ``rule`` and ``family`` filters,
so the page can show every kind as a filter with its count. ``family`` narrows the list to one family of signal (the letters before the
dash: SRC buying route, CAP hiring, PRG funded project, MAN new leader, COM business event, REL relationship). ``sort`` orders the
companies: strongest first (the default), newest first, or by the lead list's own priority and score.

``pulse`` is the picture at the top of the page and does not follow the filters: across every target company the person may see, how
many companies have a signal of each family in the last 7, 30 and 90 days, how many pieces of news arrived in each of the last 13
weeks (rolling seven-day buckets ending now, so the last one is "this week"), and the strongest signal that is new this week (or, when
the week is quiet, in the last 30 days).

``organization.best_fit`` is the service of ours that fits the company best, from ``ri.solutions.best_fit_many`` when that module is
present; without it the field is ``None`` and the page leaves the column out.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, Query, Request

from ..access import Principal, org_scope_sql
from ..db import Conn, qa
from ..events.classify import non_engineering_title_sql_pattern
from ..target_market import target_region, target_sql
from .auth import require

log = logging.getLogger(__name__)

# Kinds of event that start a buying conversation even before outside demand is proven (on top of every D1/D2 or strength-3 rule).
HIGH_INTENT_RULES = (
    "SRC-01", "SRC-02", "SRC-03", "SRC-04", "SRC-05", "SRC-06",       # a buying route is open: tender, panel, re-tender, integrator or vendor chosen, contractor call
    "CAP-01", "CAP-02", "CAP-05", "CAP-07",                           # hiring beyond capacity, contract roles, production AI roles, a leader asking for a partner
    "PRG-01", "PRG-02", "PRG-03", "PRG-04", "PRG-05", "PRG-06", "PRG-07", "PRG-08",
    "PRG-09", "PRG-10", "PRG-11", "PRG-12", "PRG-13", "PRG-14", "PRG-15", "PRG-16",   # a funded programme is under way with named systems
    "MAN-01", "MAN-03", "MAN-04",                                     # a new technology leader in the first months, or naming a programme
    "COM-01", "COM-02",                                               # funding raised for a stated build, an acquisition with integration work
    "REL-01", "REL-03", "REL-05", "REL-07",                           # someone who knows us moves or asks
)
SORTS = ("strength", "newest", "priority")
FAMILIES = ("SRC", "CAP", "PRG", "MAN", "COM", "REL")                 # the fixed order of the families on the page
FRESH_DAYS = 14
PULSE_DAYS = 90
PULSE_WINDOWS = (7, 30, 90)
PULSE_WEEKS = 13
_GROUP_INTENT = {"SRC": "route", "CAP": "capacity", "PRG": "programme", "MAN": "leader", "COM": "business", "REL": "relationship", "REG": "deadline"}
_PRIORITY_RANK = {"P1": 0, "P2": 1, "P3": 2}


def _intent(rule_id: str, tier: str, strength: int) -> str:
    """Why this signal made the list, as a code the page turns into words."""
    if tier == "D1":
        return "outside_demand"
    if tier == "D2":
        return "need_likely"
    if strength >= 3:
        return "strong"
    return _GROUP_INTENT.get(rule_id[:3], "other")


def _rank(item: dict[str, Any]) -> int:
    if item["tender"]:
        return 0
    return {"outside_demand": 0, "need_likely": 1, "strong": 2}.get(item["intent"], 3)


def _ts(v: Any) -> float:
    return v.timestamp() if isinstance(v, datetime) else 0.0


def _strength(signals: list[dict[str, Any]]) -> tuple[int, str]:
    """How strongly one company's signals point to a need right now (1 to 3), and the main reason, as a code the page turns into words."""
    kinds = len({s["rule_id"] for s in signals})
    top = max((s["strength"] for s in signals), default=1)
    if any(s["tender"] or s["intent"] == "outside_demand" for s in signals):
        return 3, "outside_demand"
    if any(s["intent"] == "need_likely" for s in signals):
        return 3, "need_likely"
    level = max(1, min(3, top + (1 if kinds > 1 else 0)))
    if kinds > 1:
        return level, "several"
    return level, "strong_kind" if top >= 3 else "clear" if level == 2 else "early"


def _signals(conn: Conn, p: Principal, *, days: int, q: str) -> list[dict[str, Any]]:
    from .search import all_like, tokens

    where, args = org_scope_sql(p, "o.id")
    terms = tokens(q) or ([q.strip()] if q.strip() else [])
    match, search_args = all_like("concat_ws(' ', o.display_name, o.canonical_domain, e.summary, l.signal)", terms) if terms else ("TRUE", [])
    return qa(conn, f"""
        SELECT s.id::text AS signal_id, s.rule_id, s.strength, s.demand_tier,
               l.signal AS rule_name, l.grp AS rule_group, l.ordinal, l.who_to_contact,
               e.id::text AS event_id, e.event_type, e.summary, e.attrs->'fit' AS fit,
               e.attrs->>'summary_en' AS summary_en, e.attrs->>'summary_pl' AS summary_pl,
               coalesce(e.event_at, e.pub_at, s.effective_from) AS happened,
               o.id::text AS organization_id, o.country_code, coalesce(st.stopped, false) AS stopped,
               ev.excerpt, ev.translation, ev.url, ev.source_name, ev.published_at
        FROM signals s
        JOIN events e ON e.id=s.event_id AND e.subject_org_id=s.organization_id
        JOIN organizations o ON o.id=s.organization_id
        JOIN legacy_signals l ON l.id=s.rule_id
        LEFT JOIN account_scores st ON st.organization_id=o.id
        JOIN LATERAL (
            SELECT c.evidence_anchor AS excerpt, c.object_json->>'anchor_en' AS translation,
                   coalesce(sd.canonical_url, c.object_json->>'official_url', c.object_json->>'url') AS url,
                   src.display_name AS source_name, coalesce(sd.published_at, e.pub_at) AS published_at
            FROM event_claims ec JOIN claims c ON c.id=ec.claim_id
            LEFT JOIN source_documents sd ON sd.id=c.source_document_id
            LEFT JOIN sources src ON src.source_id=sd.source_id
            WHERE ec.event_id=e.id AND ec.claim_id=ANY(s.evidence_claim_ids)
              AND c.status='supported' AND c.anchor_verified AND c.claim_kind IN ('quote','structured')
              AND (c.valid_to IS NULL OR c.valid_to>now())
              AND coalesce(sd.canonical_url, c.object_json->>'official_url', c.object_json->>'url') ~* '^https?://'
            ORDER BY CASE src.source_tier WHEN 'S1' THEN 1 WHEN 'S2' THEN 2 WHEN 'S3' THEN 3 WHEN 'S4' THEN 4 ELSE 5 END, c.id
            LIMIT 1
        ) ev ON TRUE
        WHERE s.status='active' AND s.verification_status='verified' AND (s.expires_at IS NULL OR s.expires_at>now())
          AND e.verification_status='verified' AND e.lifecycle_status='active' AND NOT e.is_historical_replay
          AND e.lane IS DISTINCT FROM 'public' AND s.rule_id NOT LIKE 'NEG-%%'
          AND (s.demand_tier IN ('D1','D2') OR s.strength >= 3 OR s.rule_id = ANY(%s))
          AND coalesce(e.event_at, e.pub_at, s.effective_from) > now() - make_interval(days => %s)
          AND coalesce(e.event_at, e.pub_at, s.effective_from) <= now()
          AND (e.event_type NOT IN ('engineering_job_opened','contractor_job_opened')
               OR (coalesce(e.attrs->>'title','') !~* %s AND (NOT (e.attrs ? 'job_id') OR EXISTS (
                   SELECT 1 FROM job_postings j WHERE j.provider=e.attrs->>'provider' AND j.board=e.attrs->>'board'
                   AND j.job_id=e.attrs->>'job_id' AND j.open_status='open'))))
          AND ({where}) AND {target_sql()} AND ({match})
        ORDER BY s.demand_tier, s.strength DESC, coalesce(e.event_at, e.pub_at, s.effective_from) DESC, s.id
        LIMIT 600
        """, [list(HIGH_INTENT_RULES), days, non_engineering_title_sql_pattern(), *args, *search_args])      # noqa: S608


def _companies(conn: Conn, p: Principal, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Company facts and how many people we could write to there (an address on file, not opted out)."""
    if not ids:
        return {}
    where, args = org_scope_sql(p, "o.id")
    rows = qa(conn, f"""
        SELECT o.id::text AS id, o.display_name AS name, o.country_code AS country, o.segment, o.employees_estimate AS employees,
               o.employees_uncertain, o.canonical_domain AS domain, o.list_priority, o.list_score,
               (SELECT count(DISTINCT em.person_id) FROM employment em JOIN people pe ON pe.id=em.person_id AND pe.erased_at IS NULL
                WHERE em.organization_id=o.id) AS people,
               (SELECT count(DISTINCT em.person_id) FROM employment em JOIN people pe ON pe.id=em.person_id AND pe.erased_at IS NULL
                AND pe.work_email IS NOT NULL AND NOT pe.suppressed WHERE em.organization_id=o.id) AS people_with_email,
               EXISTS (SELECT 1 FROM watchlist w WHERE w.organization_id=o.id AND w.user_email=%s) AS watched
        FROM organizations o WHERE o.id = ANY(%s::uuid[]) AND ({where})""", [p.email, ids, *args])      # noqa: S608
    out = {r["id"]: {**r, "region": target_region(r["country"]), "best_fit": None,
                     "list_score": float(r["list_score"]) if r["list_score"] is not None else None} for r in rows}
    for oid, fit in _best_fit(conn, list(out)).items():
        if oid in out:
            out[oid]["best_fit"] = fit
    return out


def _best_fit(conn: Conn, ids: list[str]) -> dict[str, dict[str, Any]]:
    """The service of ours that fits each company best, when the fit engine (``ri.solutions``) is available. Never fails the page."""
    if not ids:
        return {}
    try:
        from .. import solutions      # type: ignore[attr-defined]
        fn = getattr(solutions, "best_fit_many", None)
    except ImportError:
        return {}
    if fn is None:
        return {}
    try:
        with conn.transaction():          # a savepoint: a failing fit query must not abort the page's transaction
            raw = fn(conn, ids) or {}
    except Exception:  # noqa: BLE001  (the column is optional; the list must still load)
        log.exception("best_fit_many failed; the buying signals list is shown without it")
        return {}
    out: dict[str, dict[str, Any]] = {}
    for oid, fit in dict(raw).items():
        shaped = _shape_fit(fit)
        if shaped:
            out[str(oid)] = shaped
    return out


def _shape_fit(fit: Any) -> dict[str, Any] | None:
    """One company's best fit as {key, name, reason, level, score}: the page needs a short service name and one line why.
    ``best_fit_many`` returns {service, label, score, level, why}; other shapes are read leniently so the page never breaks on a change."""
    if fit is None:
        return None
    if isinstance(fit, list):
        fit = fit[0] if fit else None
        if fit is None:
            return None
    if isinstance(fit, str):
        return {"key": None, "name": fit, "reason": None, "level": None, "score": None}
    if not isinstance(fit, dict):
        return None
    inner = fit.get("solution") if isinstance(fit.get("solution"), dict) else fit
    name = inner.get("label") or inner.get("short_name") or inner.get("name") or inner.get("title") or fit.get("name") or fit.get("service")
    if not name:
        return None
    reason = fit.get("why") or fit.get("reason") or fit.get("summary")
    if isinstance(reason, list):
        reason = "; ".join(str(x) for x in reason[:2]) or None
    level = fit.get("level")
    return {"key": inner.get("service") or inner.get("key") or inner.get("id") or fit.get("key"), "name": str(name),
            "reason": str(reason) if reason else None, "level": level if level in ("strong", "possible", "weak") else None,
            "score": fit.get("score") if isinstance(fit.get("score"), (int, float)) else None}


def _sort_key(sort: str):
    if sort == "newest":
        return lambda c: (-_ts(c["latest"]), -c["strength"], c["organization"]["name"].lower())
    if sort == "priority":
        return lambda c: (_PRIORITY_RANK.get(c["organization"]["list_priority"] or "", 9), -(c["organization"]["list_score"] or -1),
                          -c["strength"], -_ts(c["latest"]), c["organization"]["name"].lower())
    return lambda c: (-c["strength"], -c["kinds"], min(_rank(s) for s in c["signals"]), -_ts(c["latest"]),
                      _PRIORITY_RANK.get(c["organization"]["list_priority"] or "", 9), c["organization"]["name"].lower())


def _collect(conn: Conn, p: Principal, *, days: int, region: str, q: str) -> tuple[list[dict[str, Any]], set[str], dict[str, dict[str, Any]]]:
    """Every live high-intent signal in the window, one entry per piece of news (strongest and newest first), the companies held back
    by a stop signal, and the names of the rules seen."""
    from ..findings import findings

    raw: list[dict[str, Any]] = []
    tender_events: set[str] = set()
    # Open tenders for software work at target companies (the same checks as the findings archive and Today).
    for t in findings(conn, p, scope="archive", days=days, limit=50, kind="tender", region=region, search=q, qualification="target"):
        tender_events.update(str(x) for x in t["event_ids"])
        ev = t["evidence"][0] if t["evidence"] else None
        raw.append({"id": str(t["id"]), "rule_id": "SRC-01", "strength": 3, "demand_tier": "D1", "intent": "outside_demand",
                    "event_id": str(t["id"]), "event_type": t["type"], "summary": t["tender"].get("title") or t["summary"],
                    "summary_en": t.get("summary_en"), "summary_pl": t.get("summary_pl"), "happened": t["happened"], "fit": t.get("why"),
                    "tender": {k: t["tender"].get(k) for k in ("deadline", "url", "title", "scope", "notice_id", "checked_at")},
                    "evidence": {"quote": ev["claim"], "translation": ev.get("translation"), "url": ev["url"], "source": ev["source"],
                                 "published_at": ev["published_at"]} if ev else {"quote": None, "translation": None, "url": t["tender"]["url"],
                                                                                 "source": None, "published_at": None},
                    "organization_id": str(t["organization"]["id"])})

    held_back: set[str] = set()
    meta: dict[str, dict[str, Any]] = {}
    for r in _signals(conn, p, days=days, q=q):
        if region and target_region(r["country_code"]) != region:
            continue
        if r["stopped"]:
            held_back.add(r["organization_id"])
            continue
        meta.setdefault(r["rule_id"], {"name": r["rule_name"], "group": r["rule_group"], "ordinal": r["ordinal"], "who_to_contact": r["who_to_contact"]})
        if r["event_id"] in tender_events:
            continue        # already listed as an open tender
        raw.append({"id": r["signal_id"], "rule_id": r["rule_id"], "strength": int(r["strength"]), "demand_tier": r["demand_tier"],
                    "intent": _intent(r["rule_id"], r["demand_tier"], int(r["strength"])),
                    "event_id": r["event_id"], "event_type": r["event_type"], "summary": r["summary"],
                    "summary_en": r["summary_en"], "summary_pl": r["summary_pl"], "happened": r["happened"], "fit": r["fit"], "tender": None,
                    "evidence": {"quote": r["excerpt"], "translation": r["translation"], "url": r["url"], "source": r["source_name"], "published_at": r["published_at"]},
                    "organization_id": r["organization_id"]})

    if any(i["rule_id"] == "SRC-01" for i in raw) and "SRC-01" not in meta:
        rows = qa(conn, "SELECT id, signal, grp, ordinal, who_to_contact FROM legacy_signals WHERE id='SRC-01'")
        if rows:
            meta["SRC-01"] = {"name": rows[0]["signal"], "group": rows[0]["grp"], "ordinal": rows[0]["ordinal"], "who_to_contact": rows[0]["who_to_contact"]}
    return raw, held_back, meta


def _age(happened: Any, now: datetime) -> int | None:
    return max(0, (now - happened).days) if isinstance(happened, datetime) else None


def _group(raw: list[dict[str, Any]], orgs: dict[str, dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """One entry per company with one line per kind of signal; older news of the same kind at the same company is folded into ``more``."""
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    by_org: dict[str, dict[str, Any]] = {}
    for r in raw:
        org = orgs.get(r["organization_id"])
        if not org:
            continue
        key = (r["organization_id"], r["rule_id"])
        if key in seen:
            seen[key]["more"] += 1
            continue
        item = {k: v for k, v in r.items() if k != "organization_id"}
        item["more"] = 0
        item["age_days"] = _age(item["happened"], now)
        seen[key] = item
        by_org.setdefault(org["id"], {"organization": org, "signals": []})["signals"].append(item)
    companies: list[dict[str, Any]] = []
    for c in by_org.values():
        c["signals"].sort(key=lambda s: (_rank(s), -s["strength"], -_ts(s["happened"])))
        c["strength"], c["strength_reason"] = _strength(c["signals"])
        c["kinds"] = len({s["rule_id"] for s in c["signals"]})
        c["families"] = sorted({s["rule_id"][:3] for s in c["signals"]}, key=lambda f: FAMILIES.index(f) if f in FAMILIES else 99)
        c["latest"] = max((s["happened"] for s in c["signals"] if s["happened"]), default=None)
        c["age_days"] = min((s["age_days"] for s in c["signals"] if s["age_days"] is not None), default=None)
        companies.append(c)
    return companies


def _pulse(raw: list[dict[str, Any]], companies: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """The picture at the top: companies per family over 7, 30 and 90 days, news per week, and the strongest new signal."""
    families = []
    for fam in FAMILIES:
        row: dict[str, Any] = {"family": fam}
        for w in PULSE_WINDOWS:
            row[f"d{w}"] = len({r["organization_id"] for r in raw if r["rule_id"][:3] == fam and (_age(r["happened"], now) or 0) < w})
        families.append(row)
    weeks = []
    for i in range(PULSE_WEEKS):
        end = now - timedelta(days=7 * (PULSE_WEEKS - 1 - i))
        start = end - timedelta(days=7)
        hits = [r for r in raw if isinstance(r["happened"], datetime) and start < r["happened"] <= end]
        weeks.append({"start": start, "end": end, "news": len({r["event_id"] for r in hits}), "companies": len({r["organization_id"] for r in hits})})
    week = [r for r in raw if isinstance(r["happened"], datetime) and r["happened"] > now - timedelta(days=7)]

    def best(window: int) -> dict[str, Any] | None:
        pool = [(c, s) for c in companies for s in c["signals"] if s["age_days"] is not None and s["age_days"] < window]
        if not pool:
            return None
        c, s = min(pool, key=lambda cs: (-cs[0]["strength"], _rank(cs[1]), -cs[1]["strength"], -_ts(cs[1]["happened"]), cs[0]["organization"]["name"].lower()))
        o = c["organization"]
        return {"window": window, "organization": {"id": o["id"], "name": o["name"], "country": o["country"], "region": o["region"]},
                "company_strength": c["strength"], "strength_reason": c["strength_reason"],
                "signal": {k: s[k] for k in ("rule_id", "event_id", "summary", "summary_en", "summary_pl", "happened", "age_days", "strength", "tender")}}

    return {"days": PULSE_DAYS, "windows": list(PULSE_WINDOWS), "families": families, "weeks": weeks,
            "week": {"news": len({r["event_id"] for r in week}), "companies": len({r["organization_id"] for r in week})},
            "companies": len({r["organization_id"] for r in raw}),
            "top": best(7) or best(30)}


def buying_signals(conn: Conn, p: Principal, *, days: int = 90, region: str = "", q: str = "", rule: str = "", sort: str = "strength",
                   family: str = "", pulse: bool = False) -> dict[str, Any]:
    from ..outbound.signal_campaigns import ANGLES, DEFAULT_ANGLE

    now = datetime.now(timezone.utc)
    raw, held_back, meta = _collect(conn, p, days=days, region=region, q=q)
    # The picture at the top ignores the filters. With no search and no region it is a slice of the same rows; otherwise one more read.
    if pulse:
        if not region and not q.strip() and days >= PULSE_DAYS:
            pulse_raw = [r for r in raw if (_age(r["happened"], now) or 0) < PULSE_DAYS]
        else:
            pulse_raw, _, pulse_meta = _collect(conn, p, days=PULSE_DAYS, region="", q="")
            for k, v in pulse_meta.items():
                meta.setdefault(k, v)
    else:
        pulse_raw = []

    orgs = _companies(conn, p, sorted({i["organization_id"] for i in raw} | {i["organization_id"] for i in pulse_raw}))
    companies = _group(raw, orgs, now)
    by_org = {c["organization"]["id"] for c in companies}

    # Every kind of signal in this view, with how many companies have it: the page's filter, before the kind is picked.
    kinds: dict[str, dict[str, Any]] = {}
    for c in companies:
        for s in c["signals"]:
            info = meta.get(s["rule_id"], {})
            k = kinds.setdefault(s["rule_id"], {
                "rule_id": s["rule_id"], "name": info.get("name") or s["rule_id"], "group": info.get("group"), "family": s["rule_id"][:3],
                "strength": s["strength"], "meaning": ANGLES.get(s["rule_id"], DEFAULT_ANGLE), "who_to_contact": info.get("who_to_contact"),
                "ordinal": info.get("ordinal") or 999, "companies": 0, "fresh": 0, "latest": None})
            k["companies"] += 1
            k["fresh"] += 1 if s["age_days"] is not None and s["age_days"] <= FRESH_DAYS else 0
            k["latest"] = max(filter(None, (k["latest"], s["happened"])), default=None)
            k["strength"] = max(k["strength"], s["strength"])
    kind_list = sorted(kinds.values(), key=lambda k: (-k["companies"], -k["strength"], k["ordinal"]))
    for k in kind_list:
        k.pop("ordinal", None)
    everything = {"companies": len(companies), "signals": sum(len(c["signals"]) for c in companies)}

    if rule:
        companies = [c for c in companies if any(s["rule_id"] == rule for s in c["signals"])]
        for c in companies:      # the picked kind first, the rest of the company's signals after it
            c["signals"].sort(key=lambda s: s["rule_id"] != rule)
    if family:
        companies = [c for c in companies if any(s["rule_id"][:3] == family for s in c["signals"])]
        for c in companies:
            c["signals"].sort(key=lambda s: s["rule_id"][:3] != family)
    companies.sort(key=_sort_key(sort))
    signals = [s for c in companies for s in c["signals"]]
    out = {"days": days, "sort": sort, "rule": rule, "family": family, "companies": companies, "kinds": kind_list, "all": everything,
           "totals": {"companies": len(companies), "signals": len(signals),
                      "reachable": sum(1 for c in companies if c["organization"]["people_with_email"]),
                      "fresh": sum(1 for c in companies if c["age_days"] is not None and c["age_days"] <= FRESH_DAYS),
                      "strong": sum(1 for c in companies if c["strength"] >= 3),
                      "held_back": len(held_back - by_org)},
           "fresh_days": FRESH_DAYS, "high_intent_rules": list(HIGH_INTENT_RULES), "families": list(FAMILIES),
           "best_fit": any(c["organization"].get("best_fit") for c in companies)}
    if pulse:
        out["pulse"] = _pulse(pulse_raw, _group(pulse_raw, orgs, now), now)
    return out


def register_buying_signals(app: FastAPI) -> None:
    @app.get("/api/buying-signals")
    def list_buying_signals(request: Request, days: int = Query(90, ge=7, le=365),
                            region: str = Query("", pattern="^(|uk|dach|nordics|poland|europe|international)$"),
                            q: str = Query("", max_length=120), rule: str = Query("", pattern=r"^(|[A-Z]{3}-\d{2})$"),
                            family: str = Query("", pattern=f"^(|{'|'.join(FAMILIES)})$"),
                            sort: str = Query("strength", pattern=f"^({'|'.join(SORTS)})$"),
                            pulse: bool = Query(True),
                            p: Principal = Depends(require("accounts.read"))):
        with request.app.state.db.tx() as conn:
            return buying_signals(conn, p, days=days, region=region, q=q, rule=rule, sort=sort, family=family, pulse=pulse)
