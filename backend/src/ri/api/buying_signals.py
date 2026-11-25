"""Buying signals: only the high-intent news, the things happening now that suggest a company on the list needs engineering help.

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
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Query, Request

from ..access import Principal, org_scope_sql
from ..db import Conn, qa
from ..events.classify import non_engineering_title_sql_pattern
from ..target_market import target_region, target_sql
from .auth import require

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
_TIER_RANK = {"D1": 0, "D2": 1, "D3": 2, "D4": 3}
_GROUP_INTENT = {"SRC": "route", "CAP": "capacity", "PRG": "programme", "MAN": "leader", "COM": "business", "REL": "relationship", "REG": "deadline"}


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


def _signals(conn: Conn, p: Principal, *, days: int, q: str, rule: str) -> list[dict[str, Any]]:
    from .search import all_like, tokens

    where, args = org_scope_sql(p, "o.id")
    terms = tokens(q) or ([q.strip()] if q.strip() else [])
    match, search_args = all_like("concat_ws(' ', o.display_name, o.canonical_domain, e.summary, l.signal)", terms) if terms else ("TRUE", [])
    return qa(conn, f"""
        SELECT s.id::text AS signal_id, s.rule_id, s.strength, s.demand_tier, s.commercial_questions,
               l.signal AS rule_name, l.grp AS rule_group, l.ordinal, l.who_to_contact,
               e.id::text AS event_id, e.event_type, e.summary, e.attrs->'fit' AS fit,
               e.attrs->>'summary_en' AS summary_en, e.attrs->>'summary_pl' AS summary_pl,
               coalesce(e.event_at, e.pub_at, s.effective_from) AS happened, e.first_observed_at AS first_seen,
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
          AND (%s = '' OR s.rule_id = %s)
          AND coalesce(e.event_at, e.pub_at, s.effective_from) > now() - make_interval(days => %s)
          AND coalesce(e.event_at, e.pub_at, s.effective_from) <= now()
          AND (e.event_type NOT IN ('engineering_job_opened','contractor_job_opened')
               OR (coalesce(e.attrs->>'title','') !~* %s AND (NOT (e.attrs ? 'job_id') OR EXISTS (
                   SELECT 1 FROM job_postings j WHERE j.provider=e.attrs->>'provider' AND j.board=e.attrs->>'board'
                   AND j.job_id=e.attrs->>'job_id' AND j.open_status='open'))))
          AND ({where}) AND {target_sql()} AND ({match})
        ORDER BY s.demand_tier, s.strength DESC, coalesce(e.event_at, e.pub_at, s.effective_from) DESC, s.id
        LIMIT 600
        """, [list(HIGH_INTENT_RULES), rule, rule, days, non_engineering_title_sql_pattern(), *args, *search_args])      # noqa: S608


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
    return {r["id"]: {**r, "region": target_region(r["country"]), "list_score": float(r["list_score"]) if r["list_score"] is not None else None} for r in rows}


def buying_signals(conn: Conn, p: Principal, *, days: int = 90, region: str = "", q: str = "", rule: str = "") -> dict[str, Any]:
    from ..findings import findings
    from ..outbound.signal_campaigns import ANGLES, DEFAULT_ANGLE

    now = datetime.now(timezone.utc)
    items: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    tender_events: set[str] = set()

    # Open tenders for software work at target companies (the same checks as the findings archive and Today).
    if rule in ("", "SRC-01"):
        for t in findings(conn, p, scope="archive", days=days, limit=50, kind="tender", region=region, search=q, qualification="target"):
            tender_events.update(str(x) for x in t["event_ids"])
            key = (str(t["organization"]["id"]), "SRC-01")
            ev = t["evidence"][0] if t["evidence"] else None
            item = {"id": str(t["id"]), "rule_id": "SRC-01", "strength": 3, "demand_tier": "D1", "intent": "outside_demand",
                    "event_id": str(t["id"]), "event_type": t["type"], "summary": t["tender"].get("title") or t["summary"],
                    "summary_en": t.get("summary_en"), "summary_pl": t.get("summary_pl"),
                    "happened": t["happened"], "first_seen": t.get("first_seen"), "fit": t.get("why"),
                    "tender": {k: t["tender"].get(k) for k in ("deadline", "url", "title", "scope", "notice_id", "checked_at")},
                    "evidence": {"quote": ev["claim"], "translation": ev.get("translation"), "url": ev["url"], "source": ev["source"],
                                 "published_at": ev["published_at"]} if ev else {"quote": None, "translation": None, "url": t["tender"]["url"],
                                                                                 "source": None, "published_at": None},
                    "organization_id": str(t["organization"]["id"]), "more": 0, "questions": []}
            if key in seen:
                seen[key]["more"] += 1
                continue
            seen[key] = item
            items.append(item)

    held_back: set[str] = set()
    meta: dict[str, dict[str, Any]] = {}
    for r in _signals(conn, p, days=days, q=q, rule=rule):
        if region and target_region(r["country_code"]) != region:
            continue
        if r["stopped"]:
            held_back.add(r["organization_id"])
            continue
        meta.setdefault(r["rule_id"], {"name": r["rule_name"], "group": r["rule_group"], "ordinal": r["ordinal"], "who_to_contact": r["who_to_contact"]})
        if r["event_id"] in tender_events:
            continue        # already listed as an open tender
        key = (r["organization_id"], r["rule_id"])
        if key in seen:
            seen[key]["more"] += 1
            continue
        item = {"id": r["signal_id"], "rule_id": r["rule_id"], "strength": int(r["strength"]), "demand_tier": r["demand_tier"],
                "intent": _intent(r["rule_id"], r["demand_tier"], int(r["strength"])),
                "event_id": r["event_id"], "event_type": r["event_type"], "summary": r["summary"],
                "summary_en": r["summary_en"], "summary_pl": r["summary_pl"],
                "happened": r["happened"], "first_seen": r["first_seen"], "fit": r["fit"], "tender": None,
                "evidence": {"quote": r["excerpt"], "translation": r["translation"], "url": r["url"], "source": r["source_name"], "published_at": r["published_at"]},
                "organization_id": r["organization_id"], "more": 0, "questions": list(r["commercial_questions"] or [])[:2]}
        seen[key] = item
        items.append(item)

    if any(i["rule_id"] == "SRC-01" for i in items) and "SRC-01" not in meta:
        rows = qa(conn, "SELECT id, signal, grp, ordinal, who_to_contact FROM legacy_signals WHERE id='SRC-01'")
        if rows:
            meta["SRC-01"] = {"name": rows[0]["signal"], "group": rows[0]["grp"], "ordinal": rows[0]["ordinal"], "who_to_contact": rows[0]["who_to_contact"]}

    companies = _companies(conn, p, sorted({i["organization_id"] for i in items}))
    groups: dict[str, dict[str, Any]] = {}
    for item in items:
        org = companies.get(item.pop("organization_id"))
        if not org:
            continue
        item["organization"] = org
        happened = item["happened"]
        item["age_days"] = max(0, (now - happened).days) if isinstance(happened, datetime) else None
        info = meta.get(item["rule_id"], {})
        g = groups.setdefault(item["rule_id"], {
            "rule_id": item["rule_id"], "name": info.get("name") or item["rule_id"], "group": info.get("group"), "strength": item["strength"],
            "meaning": ANGLES.get(item["rule_id"], DEFAULT_ANGLE), "who_to_contact": info.get("who_to_contact"),
            "ordinal": info.get("ordinal") or 999, "items": []})
        g["items"].append(item)

    out = []
    for g in groups.values():
        g["items"].sort(key=lambda i: (_rank(i), -(i["happened"].timestamp() if isinstance(i["happened"], datetime) else 0)))
        g["rank"] = min(_rank(i) for i in g["items"])
        g["companies"] = len({i["organization"]["id"] for i in g["items"]})
        g["reachable"] = len({i["organization"]["id"] for i in g["items"] if i["organization"]["people_with_email"]})
        g["latest"] = max((i["happened"] for i in g["items"] if i["happened"]), default=None)
        out.append(g)
    out.sort(key=lambda g: (g["rank"], -g["companies"], g["ordinal"]))
    for g in out:
        g.pop("ordinal", None)
    all_items = [i for g in out for i in g["items"]]
    orgs = {i["organization"]["id"] for i in all_items}
    return {"days": days, "groups": out,
            "totals": {"signals": len(all_items), "companies": len(orgs),
                       "reachable": len({i["organization"]["id"] for i in all_items if i["organization"]["people_with_email"]}),
                       "fresh": sum(1 for i in all_items if i["age_days"] is not None and i["age_days"] <= 14),
                       "held_back": len(held_back - orgs)},
            "high_intent_rules": list(HIGH_INTENT_RULES)}


def register_buying_signals(app: FastAPI) -> None:
    @app.get("/api/buying-signals")
    def list_buying_signals(request: Request, days: int = Query(90, ge=7, le=365),
                            region: str = Query("", pattern="^(|uk|dach|nordics|poland|europe|international)$"),
                            q: str = Query("", max_length=120), rule: str = Query("", pattern=r"^(|[A-Z]{3}-\d{2})$"),
                            p: Principal = Depends(require("accounts.read"))):
        with request.app.state.db.tx() as conn:
            return buying_signals(conn, p, days=days, region=region, q=q, rule=rule)
