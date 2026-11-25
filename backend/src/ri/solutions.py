"""What fits them best: which of The Codest's four services fits each account (and each person there), with the evidence behind it.

Deterministic and explainable. Nothing here calls a paid service or a model: it reads what we already hold and scores it.

  * Evidence comes from stored records only: verified buying signals (and the events behind them), open and past tenders where the company is
    the buyer, contracts that are about to end, open job posts, the Exa company profile (how we could help, news, tech stack, vendors), the
    lead list (its service-fit column and its buying-signal log), and our own relationship history.
  * Each piece of evidence points at one or more services with a weight (how strongly that kind of evidence means that need) times a
    freshness factor. A service's score combines its pieces like independent hints (1 - product of (1 - w)), so many weak hints never
    outrank one strong, dated one by sheer count, and the score stays between 0 and 100.
  * Reasons to stop or wait (layoffs, a big outsourcing deal, an own nearshore hub, "out of scope" on the lead list) lower the score of the
    services they affect and are listed as watch-outs.
  * Every line we show carries its source (a link, or "your lead list" / "our records") and its date when there is one. Suggestions (questions,
    openers) are worded as suggestions and are built only from those lines. No numbers or client names are invented; email addresses never
    leave this module (any address-like text is removed from the output).

best_fit_many() is the fast path for list pages (a handful of set-based queries for any number of accounts); solution_fit() and
person_brief() add the detail the company and person pages show. All three use the same evidence and the same scoring, so a list and a page
never disagree about the best fit.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Iterable

from .db import Conn, q1, qa

SERVICES = ("delivery_team", "team_extension", "ai_data", "qa")
LABEL = {"delivery_team": "Delivery team", "team_extension": "Team extension", "ai_data": "AI and data", "qa": "QA and testing"}
PITCH = {
    "delivery_team": "A complete team from The Codest that builds or modernises a product or platform for them, with its own delivery lead.",
    "team_extension": "Engineers from The Codest who join their own teams, work in their stack and ways of working, and scale up or down.",
    "ai_data": "AI and data engineering: data platforms and pipelines, and AI features taken from pilot to production.",
    "qa": "QA and testing: test automation, regression and release testing, and quality for migrations.",
}
STRONG, POSSIBLE = 60, 35

# ------------------------------------------------------------------ what each kind of verified signal says about need
# rule id -> {service: how strongly this rule points at it (0..1)}; read from the rule list's wording (legacy_signals) and the field guide's plays
RULE_SERVICE: dict[str, dict[str, float]] = {
    "REL-01": {"team_extension": .5, "delivery_team": .5}, "REL-02": {"team_extension": .4, "delivery_team": .4},
    "REL-03": {"team_extension": .6, "delivery_team": .6}, "REL-04": {"team_extension": .4, "delivery_team": .4},
    "REL-05": {"delivery_team": .9, "team_extension": .7}, "REL-06": {"team_extension": .5, "delivery_team": .5},
    "REL-07": {"delivery_team": .9, "team_extension": .9},
    "SRC-01": {"delivery_team": .9, "team_extension": .5}, "SRC-02": {"team_extension": .6, "delivery_team": .6},
    "SRC-03": {"delivery_team": 1.0, "team_extension": .6}, "SRC-04": {"team_extension": .8, "delivery_team": .6},
    "SRC-05": {"delivery_team": .9}, "SRC-06": {"team_extension": 1.0},
    "CAP-01": {"team_extension": 1.0, "delivery_team": .5}, "CAP-02": {"team_extension": 1.0}, "CAP-03": {"team_extension": .9},
    "CAP-04": {"team_extension": 1.0}, "CAP-05": {"ai_data": 1.0, "team_extension": .5}, "CAP-06": {"qa": 1.0, "team_extension": .4},
    "CAP-07": {"team_extension": 1.0, "delivery_team": .6},
    "PRG-01": {"delivery_team": 1.0, "qa": .6}, "PRG-02": {"ai_data": .9, "delivery_team": .6}, "PRG-03": {"delivery_team": 1.0, "qa": .4},
    "PRG-04": {"delivery_team": 1.0, "qa": .4}, "PRG-05": {"delivery_team": 1.0, "qa": .4}, "PRG-06": {"delivery_team": 1.0, "qa": .5},
    "PRG-07": {"delivery_team": 1.0, "qa": .5}, "PRG-08": {"delivery_team": .9, "ai_data": .6}, "PRG-09": {"ai_data": .7, "delivery_team": .7},
    "PRG-10": {"ai_data": 1.0}, "PRG-11": {"delivery_team": 1.0}, "PRG-12": {"delivery_team": 1.0, "team_extension": .5},
    "PRG-13": {"delivery_team": 1.0}, "PRG-14": {"delivery_team": 1.0}, "PRG-15": {"delivery_team": .9},
    "PRG-16": {"delivery_team": .8, "team_extension": .8, "qa": .5},
    "COM-01": {"team_extension": .8, "delivery_team": .6}, "COM-02": {"delivery_team": 1.0, "ai_data": .4}, "COM-03": {"delivery_team": .8, "team_extension": .6},
    "COM-04": {"delivery_team": .9}, "COM-05": {"delivery_team": .8}, "COM-06": {"delivery_team": .9, "ai_data": .5}, "COM-07": {"delivery_team": .8},
    "COM-08": {"team_extension": .9, "delivery_team": .6, "ai_data": .4},
    "MAN-01": {"team_extension": .6, "delivery_team": .6}, "MAN-02": {"team_extension": .6}, "MAN-03": {"ai_data": 1.0},
    "MAN-04": {"delivery_team": .5, "team_extension": .5}, "MAN-05": {"delivery_team": .7, "team_extension": .5},
    "REG-01": {"delivery_team": .9, "qa": .6}, "REG-02": {"delivery_team": .7, "qa": .5}, "REG-03": {"qa": 1.0, "delivery_team": .5},
}
# reasons to stop or wait: (points taken off per service or "*" for all, what the seller should know)
NEGATIVE: dict[str, tuple[dict[str, int], str]] = {
    "NEG-01": ({"*": 30}, "They said no to outside engineering spend. Wait 6 to 12 months before trying again."),
    "NEG-02": ({"team_extension": 20, "delivery_team": 10}, "They cut engineering jobs or froze hiring in the last 90 days. Do not pitch extra headcount; AI work may still be open."),
    "NEG-03": ({"delivery_team": 20, "team_extension": 10}, "A large integrator signed an outsourcing deal for this scope in the last year. Go through that integrator instead."),
    "NEG-04": ({"team_extension": 20}, "They opened their own nearshore or engineering hub. Offer niche skills the hub cannot hire."),
    "NEG-05": ({"*": 30}, "They cut their supplier list. Approach only through a supplier already on it."),
    "NEG-06": ({"*": 15}, "A programme was cancelled, paused or lost its budget."),
    "NEG-07": ({"*": 10}, "A merger or acquisition is pending. Wait until it closes, then look for integration work."),
    "NEG-08": ({"*": 15}, "Signs of financial strain. Check their finances before any proposal."),
    "NEG-09": ({"*": 30}, "A compliance or reputation red flag. Stop until Compliance clears it."),
    "NEG-10": ({"*": 25}, "Too small, or no in-house engineering. Outside the companies we sell to."),
    "NEG-11": ({}, "Contact restriction in this market: use a channel they have agreed to."),
    "NEG-12": ({"*": 30}, "Another seller or a partner owns this account. Route it to the owner."),
}
# what a tender's work type (tender_summary.WORK_TYPES) asks for
WORK_SERVICE: dict[str, dict[str, float]] = {
    "software_development": {"delivery_team": 1.0, "team_extension": .5}, "maintenance_support": {"team_extension": .9, "delivery_team": .7},
    "consulting": {"team_extension": .6, "delivery_team": .6}, "data_ai": {"ai_data": 1.0}, "testing_qa": {"qa": 1.0},
}
# what the service words in free text point at (lead list, news, job titles); the first service named in a text leads
KEYWORDS: dict[str, re.Pattern[str]] = {
    "qa": re.compile(r"\bqa\b|\btest(s|ing|er|ers)?\b|test automation|quality assurance|\bsdet\b|release quality|regression", re.I),
    "ai_data": re.compile(r"\bai\b|\bgen ?ai\b|artificial intelligence|machine learning|\bml\b|\bllms?\b|\brag\b|mlops|agentic|\bai agents?\b|chatbot|virtual assistant|"
                          r"conversational|\bdata\b|analytics|databricks|snowflake|microsoft fabric|synapse|\betl\b|kafka|streaming|process mining", re.I),
    "team_extension": re.compile(r"augment|team extension|staffing|contractors?\b|contract roles?|interim|freelanc|nearshor|outstaff|"
                                 r"engineering hiring|technology hiring|hiring|recruit|vacanc|open roles", re.I),
    "delivery_team": re.compile(r"develop|\bbuild|re-?build|platform|moderni[sz]|migrat|integrat|replatform|implementation|portal|\bapps?\b|mobile|core banking|"
                                r"\bapis?\b|cloud|devops|re-?architect|transformation|prototype|software|launch|roll-?out|separation|carve-out|onboarding|backend|full-stack", re.I),
}
LIST_FIT = {"development": {"delivery_team": 1.0}, "ai": {"ai_data": 1.0}, "augmentation": {"team_extension": 1.0}, "prototype": {"delivery_team": .9},
            "qa": {"qa": 1.0}, "testing": {"qa": 1.0}}
CONFIDENCE = {"high": 1.0, "medium": .8, "low": .5}
ENG_TITLE = re.compile(r"engineer|developer|programmer|architect|devops|\bsre\b|data|machine learning|\bml\b|\bai\b|\bqa\b|test|software|platform|"
                       r"cloud|backend|frontend|full[- ]?stack|mobile|ios|android|java|python|\.net|scrum|tech lead|cto|vp eng", re.I)
CONTRACT_TYPES = {"contract", "interim", "freelance"}
BIG_SI = re.compile(r"\b(accenture|capgemini|infosys|tcs|tata consultancy|wipro|cognizant|hcl|ibm|deloitte|kpmg|pwc|ey\b|ernst|atos|sopra|"
                    r"dxc|ntt data|luxoft|epam|globallogic|endava|thoughtworks)\b", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
FIN = re.compile(r"bank|insur|payment|fintech|lend|wealth|financ|credit|asset|invest|pension|brok|card|capital|leasing|clearing|exchange", re.I)


# ------------------------------------------------------------------ small helpers
def _day(v: Any) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    m = re.match(r"\d{4}-\d{2}(-\d{2})?", str(v).strip())
    return m.group(0) if m else None


def _age(d: str | None, today: date) -> int | None:
    if not d:
        return None
    p = [int(x) for x in d.split("-")] + [1, 1]
    try:
        return (today - date(p[0], p[1], p[2])).days
    except ValueError:
        return None


def _fresh(d: str | None, today: date) -> float:
    """How much a dated piece of evidence still counts: full for three months, then less; undated counts a bit less than fresh."""
    a = _age(d, today)
    if a is None:
        return .7
    return 1.0 if a <= 90 else .8 if a <= 180 else .6 if a <= 365 else .3


def _url(v: Any) -> str | None:
    s = str(v or "").strip()
    return s if re.match(r"^https?://", s, re.I) else None


def _url_key(u: str | None) -> str:
    return re.sub(r"^https?://(www\.)?", "", (u or "").strip().casefold()).split("#")[0].rstrip("/")


def _clip(s: Any, n: int) -> str:
    t = re.sub(r"\s+", " ", str(s or "")).strip()
    return t if len(t) <= n else t[:n - 1].rsplit(" ", 1)[0].rstrip(",;:-") + "…"


def _scrub(v: Any) -> Any:
    """No email address ever leaves this module, whatever a stored text contains."""
    if isinstance(v, str):
        return EMAIL.sub("[address removed]", v)
    if isinstance(v, list):
        return [_scrub(x) for x in v]
    if isinstance(v, dict):
        return {k: _scrub(x) for k, x in v.items()}
    return v


def services_in(text: str | None) -> dict[str, float]:
    """The services a piece of text talks about. The first one named leads (1.0); the others count a little less (0.75)."""
    t = str(text or "")
    if not t.strip():
        return {}
    low = re.sub(r"\s+", " ", t.strip().casefold())
    if low in LIST_FIT:
        return dict(LIST_FIT[low])
    found = sorted(((m.start(), s) for s, rx in KEYWORDS.items() if (m := rx.search(t))), key=lambda x: x[0])
    return {s: (1.0 if i == 0 else .75) for i, (_, s) in enumerate(found)}


def _fmt_day(d: str | None) -> str:
    if not d:
        return ""
    p = d.split("-")
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    try:
        return f"{int(p[2])} {months[int(p[1]) - 1]} {p[0]}" if len(p) == 3 else f"{months[int(p[1]) - 1]} {p[0]}"
    except (ValueError, IndexError):
        return d


class Ev(dict):
    """One piece of evidence: text, source_url, date, kind, weights {service: 0..100}, and a key so the same story counts once."""


def _ev(*, text: str, kind: str, weights: dict[str, float], url: str | None = None, day: str | None = None, key: str | None = None,
        short: str | None = None, rank: int = 0) -> Ev:
    return Ev(text=_clip(text, 300), short=_clip(short or text, 120), source_url=_url(url), date=day, kind=kind,
              weights={s: round(w, 1) for s, w in weights.items() if w > 0}, key=key or _url_key(url) or f"{kind}:{_clip(text, 80).casefold()}", rank=rank)


def _scale(base: float, factors: dict[str, float], fresh: float = 1.0) -> dict[str, float]:
    return {s: base * f * fresh for s, f in factors.items() if s in SERVICES}


# ------------------------------------------------------------------ gathering what we hold
def _orgs(conn: Conn, ids: list[str]) -> dict[str, dict[str, Any]]:
    return {str(r["id"]): r for r in qa(conn, "SELECT id, display_name, segment, country_code, employees_estimate, employees_uncertain, company_ownership, entity_type, "
                                              "list_eligibility, list_priority, list_icp_segment, list_region, relationship, in_master_list "
                                              "FROM organizations WHERE id = ANY(%s::uuid[])", (ids,))}


def _signals(conn: Conn, ids: list[str]) -> list[dict[str, Any]]:
    return qa(conn, """
        SELECT s.organization_id::text AS org, s.rule_id, l.signal AS rule_name, s.strength, s.demand_tier, e.id::text AS event_id, e.summary,
               coalesce(e.event_at, e.pub_at) AS at,
               (SELECT coalesce(sd.canonical_url, c.object_json->>'official_url', c.object_json->>'url') FROM claims c
                  LEFT JOIN source_documents sd ON sd.id=c.source_document_id
                 WHERE c.id = ANY(s.evidence_claim_ids) AND coalesce(sd.canonical_url, c.object_json->>'official_url', c.object_json->>'url') ~* '^https?://'
                 ORDER BY c.id LIMIT 1) AS url
        FROM signals s JOIN events e ON e.id=s.event_id AND e.subject_org_id=s.organization_id
        LEFT JOIN legacy_signals l ON l.id=s.rule_id
        WHERE s.organization_id = ANY(%s::uuid[]) AND s.status='active' AND (s.expires_at IS NULL OR s.expires_at > now())
          AND s.verification_status='verified' AND e.verification_status='verified' AND e.lifecycle_status='active' AND NOT e.is_historical_replay
        ORDER BY s.effective_from DESC""", (ids,))


def _negatives(conn: Conn, ids: list[str]) -> list[dict[str, Any]]:
    return qa(conn, "SELECT split_part(n.scope_id, ':', 1) AS org, n.rule_id, n.starts_at, e.summary FROM negative_findings n LEFT JOIN events e ON e.id=n.event_id "
                    "WHERE n.status='active' AND (n.expires_at IS NULL OR n.expires_at > now()) AND split_part(n.scope_id, ':', 1) = ANY(%s)", ([str(i) for i in ids],))


def _tenders(conn: Conn, ids: list[str]) -> list[dict[str, Any]]:
    return qa(conn, "SELECT buyer_org_id::text AS org, title, status, deadline_at, published_at, official_url, summary->>'title_en' AS title_en, "
                    "summary->>'work_type' AS work_type, summary->'fit'->>'level' AS fit_level, summary->'technologies' AS tech "
                    "FROM procurement_notices WHERE buyer_org_id = ANY(%s::uuid[]) AND published_at > now() - interval '2 years' "
                    "ORDER BY coalesce(deadline_at, published_at) DESC NULLS LAST", (ids,))


def _awards(conn: Conn, ids: list[str]) -> list[dict[str, Any]]:
    return qa(conn, "SELECT buyer_org_id::text AS org, title, winners, awarded_on, expected_end, official_url FROM awarded_contracts "
                    "WHERE buyer_org_id = ANY(%s::uuid[]) AND expected_end BETWEEN now() - interval '3 months' AND now() + interval '18 months' "
                    "AND coalesce(relevant, true) ORDER BY expected_end", (ids,))


def _jobs(conn: Conn, ids: list[str]) -> list[dict[str, Any]]:
    return qa(conn, "SELECT organization_id::text AS org, title, team, employment_type, posted_at, first_seen_at, url, tech_keywords FROM job_postings "
                    "WHERE organization_id = ANY(%s::uuid[]) AND open_status='open' ORDER BY coalesce(posted_at, first_seen_at) DESC", (ids,))


def _profiles(conn: Conn, ids: list[str]) -> dict[str, dict[str, Any]]:
    return {r["org"]: r for r in qa(conn, "SELECT DISTINCT ON (organization_id) organization_id::text AS org, content, finished_at FROM account_profiles "
                                          "WHERE organization_id = ANY(%s::uuid[]) AND status='done' ORDER BY organization_id, created_at DESC", (ids,))}


def _list_rows(conn: Conn, ids: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    master: dict[str, dict[str, Any]] = {}
    log: dict[str, list[dict[str, Any]]] = {}
    for r in qa(conn, "SELECT organization_id::text AS org, kind, original, observed_at FROM legacy_account_research WHERE organization_id = ANY(%s::uuid[]) "
                      "AND kind IN ('master_account','buying_signal_log') ORDER BY observed_at DESC", (ids,)):
        if r["kind"] == "master_account":
            master.setdefault(r["org"], r["original"] or {})
        else:
            log.setdefault(r["org"], []).append(r["original"] or {})
    return master, log


def _events(conn: Conn, ids: list[str]) -> list[dict[str, Any]]:
    """Checked news about the company that did not become a buying signal (a signal already says it more strongly)."""
    return qa(conn, """
        SELECT * FROM (
          SELECT e.subject_org_id::text AS org, e.id::text AS event_id, e.event_type, e.summary, coalesce(e.event_at, e.pub_at) AS at,
                 (SELECT coalesce(sd.canonical_url, c.object_json->>'official_url', c.object_json->>'url') FROM event_claims ec JOIN claims c ON c.id=ec.claim_id
                    LEFT JOIN source_documents sd ON sd.id=c.source_document_id
                   WHERE ec.event_id=e.id AND coalesce(sd.canonical_url, c.object_json->>'official_url', c.object_json->>'url') ~* '^https?://' ORDER BY c.id LIMIT 1) AS url,
                 row_number() OVER (PARTITION BY e.subject_org_id ORDER BY coalesce(e.event_at, e.pub_at) DESC NULLS LAST) AS n
          FROM events e
          WHERE e.subject_org_id = ANY(%s::uuid[]) AND e.verification_status='verified' AND e.lifecycle_status='active' AND NOT e.is_historical_replay
            AND coalesce(e.event_at, e.pub_at) > now() - interval '1 year'
            AND NOT EXISTS (SELECT 1 FROM signals s WHERE s.event_id=e.id AND s.status='active')) x
        WHERE n <= 6""", (ids,))


def _gather(conn: Conn, ids: list[str]) -> dict[str, dict[str, Any]]:
    ids = list(dict.fromkeys(str(i) for i in ids if i))
    if not ids:
        return {}
    orgs = _orgs(conn, ids)
    out: dict[str, dict[str, Any]] = {i: {"org": orgs[i], "signals": [], "negatives": [], "tenders": [], "awards": [], "jobs": [], "events": [],
                                          "profile": None, "master": {}, "log": []} for i in ids if i in orgs}
    for name, rows in (("signals", _signals(conn, ids)), ("negatives", _negatives(conn, ids)), ("tenders", _tenders(conn, ids)),
                       ("awards", _awards(conn, ids)), ("jobs", _jobs(conn, ids)), ("events", _events(conn, ids))):
        for r in rows:
            if r["org"] in out:
                out[r["org"]][name].append(r)
    for oid, p in _profiles(conn, ids).items():
        if oid in out:
            out[oid]["profile"] = p
    master, log = _list_rows(conn, ids)
    for oid in out:
        out[oid]["master"], out[oid]["log"] = master.get(oid, {}), log.get(oid, [])
    return out


# ------------------------------------------------------------------ turning records into evidence
def evidence(g: dict[str, Any], today: date) -> tuple[list[Ev], dict[str, int], list[str]]:
    """(evidence, points off per service, watch-outs) for one account's gathered records."""
    items: list[Ev] = []
    for s in g["signals"]:
        d = _day(s["at"])
        factors = dict(RULE_SERVICE.get(s["rule_id"] or "", {}))
        for svc, f in services_in(s["summary"]).items():           # the story's own words can name a service the rule does not (an AI feature launch)
            factors[svc] = max(factors.get(svc, 0), .6 * f)
        base = 30 + 6 * int(s["strength"] or 1) + {"D1": 6, "D2": 3}.get(s["demand_tier"] or "", 0)
        items.append(_ev(text=s["summary"] or s["rule_name"] or "Buying signal", kind="signal", weights=_scale(base, factors, _fresh(d, today)),
                         url=s["url"], day=d, key=f"event:{s['event_id']}", rank=0))
    seen_events = {s["event_id"] for s in g["signals"]}
    for e in g["events"]:
        if e["event_id"] in seen_events:
            continue
        d = _day(e["at"])
        f = services_in(f"{e['event_type'] or ''} {e['summary'] or ''}")
        if f and e["summary"]:
            items.append(_ev(text=e["summary"], kind="news", weights=_scale(18, f, _fresh(d, today)), url=e["url"], day=d, key=f"event:{e['event_id']}", rank=3))
    for t in g["tenders"]:
        title = t["title_en"] or t["title"] or "Tender"
        deadline, pub = _day(t["deadline_at"]), _day(t["published_at"])
        is_open = t["status"] in ("open", "amended") and not (deadline and (_age(deadline, today) or 0) > 0)
        factors = WORK_SERVICE.get(t["work_type"] or "") or (services_in(title) if t["work_type"] in (None, "", "other") else {})
        if not factors:
            continue                                            # licences, hardware: not work for us
        if is_open:
            base = 50 + {"strong": 5, "weak": -20, "none": -40}.get(t["fit_level"] or "", 0)
            when = f", closes {_fmt_day(deadline)}" if deadline else ""
            items.append(_ev(text=f"Open tender: {title}{when}", short=f"Open tender: {title}", kind="tender", weights=_scale(base, factors),
                             url=t["official_url"], day=deadline or pub, rank=0))
        else:
            items.append(_ev(text=f"Bought through a tender before: {title}", kind="tender", weights=_scale(14, factors, _fresh(pub, today)),
                             url=t["official_url"], day=pub, rank=4))
    for a in g["awards"]:
        end = _day(a["expected_end"])
        who = ", ".join([w for w in (a["winners"] or []) if w][:2])
        items.append(_ev(text=f"A contract for {a['title'] or 'IT services'}{f' with {who}' if who else ''} is due to end around {_fmt_day(end)}; it may be tendered again.",
                         short=f"Contract ends {_fmt_day(end)}: {a['title'] or 'IT services'}", kind="tender",
                         weights=_scale(30, {"delivery_team": 1.0, "team_extension": .5}), url=a["official_url"], day=end, rank=1))
    jobs = [j for j in g["jobs"] if ENG_TITLE.search(f"{j['title']} {j['team'] or ''}")]
    if jobs:
        per: dict[str, list[dict[str, Any]]] = {}
        for j in jobs:
            f = services_in(f"{j['title']} {' '.join(j['tech_keywords'] or [])}")
            svc = "qa" if "qa" in f and f["qa"] == 1.0 else "ai_data" if f.get("ai_data") == 1.0 else "team_extension"
            per.setdefault(svc, []).append(j)
        newest = _day(max((j["posted_at"] or j["first_seen_at"] for j in jobs), default=None))
        contract = [j for j in jobs if j["employment_type"] in CONTRACT_TYPES]
        for svc, js in per.items():
            eg = "; ".join(_clip(j["title"], 50) for j in js[:2])
            w = min(15 + 6 * len(js), 45)
            weights = {svc: w} if svc == "team_extension" else {svc: w, "team_extension": w * .6}
            items.append(_ev(text=f"{len(js)} open {'role' if len(js) == 1 else 'roles'} on their careers page, e.g. {eg}", kind="job", weights=weights,
                             url=js[0]["url"], day=_day(js[0]["posted_at"] or js[0]["first_seen_at"]) or newest, key=f"jobs:{svc}", rank=1))
        if contract:
            items.append(_ev(text=f"{len(contract)} contract or interim engineering {'role' if len(contract) == 1 else 'roles'}, e.g. {_clip(contract[0]['title'], 60)}",
                             kind="job", weights={"team_extension": 40}, url=contract[0]["url"], day=_day(contract[0]["posted_at"] or contract[0]["first_seen_at"]),
                             key="jobs:contract", rank=0))
    c = (g["profile"] or {}).get("content") or {}
    done = _day((g["profile"] or {}).get("finished_at"))
    for h in c.get("how_we_help") or []:
        svc = h.get("service") if h.get("service") in SERVICES else None
        f = {svc: 1.0} if svc else services_in(h.get("angle"))
        cite = next((s for s in h.get("sources") or [] if _url(s.get("source_url"))), {})
        if f and h.get("angle"):
            items.append(_ev(text=f"{h['angle']}{': ' + h['why'] if h.get('why') else ''}", short=h["angle"], kind="profile", weights=_scale(26, f),
                             url=cite.get("source_url"), day=_day(cite.get("published")) or done, key=f"help:{_clip(h['angle'], 60).casefold()}", rank=2))
    for r in c.get("recent") or []:
        d = _day(r.get("date"))
        f = services_in(f"{r.get('headline') or ''} {r.get('detail') or ''}")
        if f and r.get("headline") and (_age(d, today) or 0) <= 400:
            items.append(_ev(text=r["headline"], kind="news", weights=_scale(22, f, _fresh(d, today)), url=r.get("source_url"), day=d, rank=2))
    for p in c.get("icp_points") or []:
        label, val = str(p.get("label") or ""), str(p.get("value") or "")
        if re.search(r"change|programme|program", label, re.I) and p.get("fit") == "good":
            f = services_in(val)
            if f:
                items.append(_ev(text=f"{label}: {val}", short=val, kind="profile", weights=_scale(15, f), url=p.get("source_url"), day=_day(p.get("published")) or done, rank=3))
        elif re.search(r"engineering team|tech stack", label, re.I) and p.get("fit") == "good":
            items.append(_ev(text=f"{label}: {val}", short=val, kind="profile", weights={"team_extension": 12}, url=p.get("source_url"), day=_day(p.get("published")) or done, rank=3))
    m = g["master"] or {}
    fit_text = str(m.get("The Codest Service Fit") or "").strip()
    if fit_text:
        items.append(_ev(text=f"Your lead list sees a fit for: {fit_text}", short=f"Lead list: {fit_text}", kind="lead_list", weights=_scale(28, services_in(fit_text)),
                         day=_day(m.get("Last Researched")), key="list:fit", rank=2))
    sig_day = _day(m.get("Signal Date"))
    for col in ("Strongest Buying Signal", "Potential Opportunity", "Potential Engineering Need"):
        v = str(m.get(col) or "").strip()
        if v and services_in(v):
            items.append(_ev(text=v, kind="lead_list", weights=_scale(14, services_in(v), _fresh(sig_day, today)), url=m.get("Primary Source URL"), day=sig_day,
                             key=f"list:{_clip(v, 80).casefold()}", rank=3))
    seen_story: set[tuple[str, str]] = set()
    for x in g["log"]:
        d = _day(x.get("Event date") or x.get("Event Date"))
        story = (_url_key(x.get("Source URL")) or _clip(x.get("Verified event"), 80).casefold(), d or "")
        if story in seen_story or (_age(d, today) or 0) > 365:
            continue                                            # the list logs one story several times; older than a year is no longer news
        seen_story.add(story)
        what = str(x.get("Verified event") or x.get("Verified Event") or "").strip()
        fit = str(x.get("Service Fit") or "").strip()
        f = services_in(fit) or services_in(f"{x.get('Signal type') or ''} {what}")
        if not what or not f:
            continue
        conf = CONFIDENCE.get(str(x.get("Evidence confidence") or x.get("Evidence Confidence") or "").strip().casefold(), .6)
        items.append(_ev(text=what, short=f"{x.get('Signal type') or 'Lead list signal'}: {what}", kind="lead_list", weights=_scale(24 * conf, f, _fresh(d, today)),
                         url=x.get("Source URL"), day=d, rank=1))

    off: dict[str, int] = {}
    watch: list[str] = []
    for n in g["negatives"]:
        pts, why = NEGATIVE.get(n["rule_id"], ({}, ""))
        for s, v in pts.items():
            for svc in (SERVICES if s == "*" else (s,)):
                off[svc] = max(off.get(svc, 0), v)
        if why and why not in watch:
            watch.append(why)
    elig = str(g["org"].get("list_eligibility") or m.get("Eligibility") or "").strip()
    if re.search(r"out of scope|not eligible|exclude", elig, re.I):
        for svc in SERVICES:
            off[svc] = max(off.get(svc, 0), 15)
        watch.append(f"Your lead list marks them “{elig}”{': ' + str(m.get('Coverage Gap')) if m.get('Coverage Gap') and m.get('Coverage Gap') != elig else ''}.")
    return items, off, watch


def score(items: Iterable[Ev], off: dict[str, int]) -> list[dict[str, Any]]:
    """Services ranked by score; a service needs at least one piece of evidence to be listed."""
    per: dict[str, dict[str, Ev]] = {s: {} for s in SERVICES}
    for e in items:
        for s, w in e["weights"].items():
            cur = per[s].get(e["key"])
            if cur is None or cur["weights"][s] < w:
                per[s][e["key"]] = e                            # the same story counts once per service, at its strongest
    out = []
    for s in SERVICES:
        evs = sorted(per[s].values(), key=lambda e: (-e["weights"][s], e["rank"]))
        if not evs:
            continue
        rest = 1.0
        for e in evs:
            rest *= 1 - min(e["weights"][s], 95) / 100
        sc = max(0, min(100, round(100 * (1 - rest)) - off.get(s, 0)))
        out.append({"service": s, "label": LABEL[s], "score": sc, "level": "strong" if sc >= STRONG else "possible" if sc >= POSSIBLE else "weak", "evidence": evs})
    out.sort(key=lambda x: (-x["score"], -len(x["evidence"]), SERVICES.index(x["service"])))
    return out


def _why_line(e: Ev) -> str:
    d = f" ({_fmt_day(e['date'])})" if e.get("date") and e["kind"] != "tender" else ""
    return _clip(f"{e['short']}{d}", 140)


# ------------------------------------------------------------------ list pages
def best_fit_many(conn: Conn, org_ids: list[str]) -> dict[str, dict[str, Any]]:
    """The best-fitting service per account for list pages: {org_id: {service, label, score, level, why}}. No entry when nothing we hold
    supports a fit. Seven set-based queries whatever the number of accounts."""
    today = datetime.now(timezone.utc).date()
    out: dict[str, dict[str, Any]] = {}
    for oid, g in _gather(conn, list(org_ids)).items():
        items, off, _ = evidence(g, today)
        ranked = score(items, off)
        if ranked and ranked[0]["score"] > 0:
            top = ranked[0]
            out[oid] = _scrub({"service": top["service"], "label": top["label"], "score": top["score"], "level": top["level"], "why": _why_line(top["evidence"][0])})
    return out


# ------------------------------------------------------------------ the company page
def _proof(conn: Conn, services: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Approved case studies and the field guide's lines about each service. A client is named only when the case study is approved for
    external use; anything else is marked internal."""
    out: dict[str, list[dict[str, Any]]] = {s: [] for s in services}
    tags = {"delivery_team": ("delivery_team", "delivery", "dedicated_team", "development"), "team_extension": ("team_extension", "augmentation", "staff_augmentation"),
            "ai_data": ("ai_data", "ai", "data", "data_engineering"), "qa": ("qa", "testing", "test_automation")}
    try:
        rows = qa(conn, "SELECT title, service_tags, approved_audience, client_name, effective_date, restrictions FROM proof_assets WHERE status='active' "
                        "AND approved_audience IN ('external_approved','internal_only','limited_audience') ORDER BY approved_audience='external_approved' DESC, effective_date DESC NULLS LAST")
    except Exception:          # noqa: BLE001 - an old database without the table: no proof rather than no page
        rows = []
    for r in rows:
        mine = {str(t).casefold() for t in r["service_tags"] or []}
        for s in services:
            if mine & set(tags[s]) and len(out[s]) < 3:
                ext = r["approved_audience"] == "external_approved"
                detail = (f"Approved for use with clients{': ' + r['client_name'] if r['client_name'] else ''}." if ext
                          else "Internal only: you may describe the work, not name the client.")
                if r["restrictions"]:
                    detail += f" {r['restrictions']}"
                out[s].append({"title": r["title"], "detail": detail, "href": None})
    try:
        from . import kb
        words = {"delivery_team": ["delivery team"], "team_extension": ["team extension"], "ai_data": ["ai"], "qa": ["qa"]}
        for s in services:
            if len(out[s]) >= 3:
                continue
            for hit in kb.search(words[s], limit=2, conn=conn):
                if hit["doc"] == "field-guide" and len(out[s]) < 3:
                    out[s].append({"title": hit["doc_title"], "detail": _clip(hit["text"], 240), "href": "/knowledge/field-guide"})
    except Exception:          # noqa: BLE001 - the knowledge base is optional
        pass
    return out


def _unknowns(conn: Conn, oid: str, g: dict[str, Any], ranked: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    c = (g["profile"] or {}).get("content") or {}
    if not g["profile"]:
        out.append("We have not researched them with Exa yet, so their tech stack, vendors and recent news are not known here.")
    else:
        labels = " ".join(str(p.get("label") or "") for p in c.get("icp_points") or [])
        if not re.search(r"tech stack", labels, re.I):
            out.append("Their tech stack: the research did not find it.")
        if not c.get("vendors"):
            out.append("Who supplies their engineering today: no vendors were named in the pages found.")
    if not g["jobs"]:
        out.append("Whether they are hiring engineers: we do not watch their careers page yet, or it shows no open roles.")
    route = str((g["master"] or {}).get("Procurement Route") or (g["master"] or {}).get("Procurement / Vendor Route") or "").strip()
    if not route or route.casefold() == "unknown":
        out.append("How they buy engineering services: directly, through a supplier panel, or by tender.")
    n = q1(conn, "SELECT count(DISTINCT e.person_id) AS n FROM employment e JOIN people pe ON pe.id=e.person_id WHERE e.organization_id=%s AND pe.erased_at IS NULL "
                 "AND NOT pe.suppressed", (oid,)) or {"n": 0}
    if not n["n"]:
        out.append("Who decides: we have no contacts recorded at this company.")
    if ranked and not any(e["kind"] in ("signal", "tender", "job") for e in ranked[0]["evidence"]):
        out.append("Whether they have budget for outside help: nothing we checked shows external demand yet.")
    gap = str((g["master"] or {}).get("Coverage Gap") or "").strip()
    if gap and not re.search(r"outside icp|none|^-$", gap, re.I):
        out.append(f"Your lead list notes a gap: {gap}")
    for qn in (c.get("open_questions") or [])[:2]:
        out.append(str(qn))
    return out[:6]


def _watch_more(g: dict[str, Any], rel: dict[str, Any] | None) -> list[str]:
    out: list[str] = []
    o = g["org"]
    if o.get("entity_type") == "public_body" or o.get("company_ownership") == "state_owned":
        out.append("State-owned or a public body: they usually buy through formal tenders, which take months.")
    c = (g["profile"] or {}).get("content") or {}
    for p in c.get("icp_points") or []:
        if p.get("fit") == "bad" and re.search(r"outsourc|vendor|partner|supplier", f"{p.get('label')} {p.get('value')}", re.I):
            out.append(f"{p.get('label')}: {_clip(p.get('value'), 160)}")
    sis = [v["name"] for v in c.get("vendors") or [] if BIG_SI.search(str(v.get("name") or ""))]
    if sis:
        out.append(f"They already work with {', '.join(sis[:3])}. Ask what {'that partner does' if len(sis) == 1 else 'these partners do'} not cover, or offer to work alongside.")
    if o.get("segment") and not FIN.search(str(o["segment"])) and not FIN.search(str((g["master"] or {}).get("Industry") or "")):
        out.append(f"Sector “{o['segment']}” is outside the financial services we know best: lead with technology, not industry, experience.")
    out_ = (rel or {}).get("outreach") or {}
    if out_.get("unsubscribes"):
        out.append("Someone here unsubscribed from our emails. Check before writing to anyone else.")
    if (rel or {}).get("outreach", {}).get("engagement") == "not_interested":
        out.append("Someone here told us they are not interested. Give it time and bring a new reason.")
    if out_.get("bounces"):
        out.append("Some of our emails to this company bounced.")
    return out


def _why_now(ranked: list[dict[str, Any]], today: date) -> str | None:
    """The freshest strong, dated reason among the best services: an open tender first, then a verified signal, a job post, then the rest."""
    cands = [e for r in ranked[:2] for e in r["evidence"] if e.get("date") and e["kind"] in ("tender", "signal", "job", "lead_list", "news")]
    fresh = [e for e in cands if (e["kind"] == "tender" and (_age(e["date"], today) or 0) <= 0) or (_age(e["date"], today) is not None and _age(e["date"], today) <= 120)]  # type: ignore[operator]
    if not fresh:
        return None
    order = {"tender": 0, "signal": 1, "job": 2, "lead_list": 3, "news": 4}
    e = sorted(fresh, key=lambda x: (order[x["kind"]], _age(x["date"], today) or 0))[0]
    if e["kind"] == "tender":
        return _clip(e["text"], 220)
    return _clip(f"{e['text']} ({_fmt_day(e['date'])})", 220)


def solution_fit(conn: Conn, org_id: str) -> dict[str, Any]:
    """The company page's "What fits them best": every service with any evidence, ranked, with the evidence lines (date and source), proof
    we may use, why now, watch-outs and what we still do not know."""
    today = datetime.now(timezone.utc).date()
    g = _gather(conn, [org_id]).get(str(org_id))
    if not g:
        return {"services": [], "best": None, "why_now": None, "watch_outs": [], "unknowns": []}
    items, off, watch = evidence(g, today)
    ranked = score(items, off)
    try:
        from .api.profiles import relationship_facts
        rel = relationship_facts(conn, str(org_id))
    except Exception:          # noqa: BLE001 - the relationship summary is a nice-to-have here
        rel = None
    if rel and rel.get("recorded", {}) and (rel["recorded"] or {}).get("status") in ("client", "past_client"):
        for r in ranked:
            r["evidence"].append(Ev(text="They have worked with us before (recorded on the company page).", short="Worked with us before", source_url=None,
                                    date=_day((rel["recorded"] or {}).get("set_at")), kind="records", weights={r["service"]: 0}, key="records:client", rank=5))
    proof = _proof(conn, [r["service"] for r in ranked])
    services = [{"service": r["service"], "label": r["label"], "score": r["score"], "level": r["level"], "pitch": PITCH[r["service"]],
                 "why": [{"text": e["text"], "source_url": e["source_url"], "date": e["date"], "kind": e["kind"]} for e in r["evidence"][:6]],
                 "more": max(0, len(r["evidence"]) - 6), "proof": proof.get(r["service"], [])} for r in ranked]
    best = ranked[0]["service"] if ranked and ranked[0]["score"] >= 15 else None
    return _scrub({"services": services, "best": best, "why_now": _why_now(ranked, today),
                   "watch_outs": list(dict.fromkeys([*watch, *_watch_more(g, rel)]))[:6], "unknowns": _unknowns(conn, str(org_id), g, ranked)})


# ------------------------------------------------------------------ the person page
COMMITTEE_LABEL = {"decision": "Decision maker", "technical": "Technical buyer", "procurement": "Procurement", "influencer": "Influencer"}
# what each part of the buying committee usually weighs (from the ICP and buying-persona map's committee roles and common B2B buying research)
CARES = {
    "decision": ["Delivering this year's programmes on time without growing fixed headcount",
                 "Risk: a supplier that is reliable, secure and acceptable to regulators (third-party and outsourcing rules)",
                 "Cost and flexibility compared with hiring or their current vendors"],
    "technical": ["Engineers who fit their stack and ways of working, and get productive quickly",
                  "Code quality, ownership and knowledge staying in the team",
                  "Removing a delivery bottleneck without adding management work"],
    "procurement": ["Whether a supplier can be onboarded: compliance, security, third-party risk checks",
                    "Fit with their supplier panels, frameworks and rate cards",
                    "Clear commercial terms and a low-risk start"],
    "influencer": ["Getting their product or project delivered", "A partner who understands their domain and constraints",
                   "Less time spent on hiring and coordination"],
}
CARES_SERVICE = {
    "ai_data": "Moving AI and data work from pilot to production, with data quality and governance under control",
    "qa": "Release quality and fewer incidents: test automation and regression coverage",
    "delivery_team": "Getting a programme delivered end to end with one accountable team",
    "team_extension": "Adding capacity quickly in skills that are hard to hire",
}
QUESTIONS = {
    "decision": ["Which of this year's programmes worry you most for lack of people or time?",
                 "When you need more engineering capacity, how do you choose between hiring, your current vendors and a new partner?",
                 "What would a new partner have to prove in the first three months?"],
    "technical": ["Where is the team most stretched right now, and in which skills?",
                  "How do outside engineers usually join your teams: onboarding, code review, ownership?",
                  "What would make you trust an external team with production code?"],
    "procurement": ["Is there a supplier panel or framework for software engineering, and when does it next open?",
                    "What does onboarding a new IT supplier involve, and how long does it take?",
                    "Who in technology sponsors a new supplier?"],
    "influencer": ["What are you trying to get delivered this year, and what is slowing it down?",
                   "Who decides when the team brings in outside help?",
                   "How have outside teams worked for you before?"],
}
QUESTION_SERVICE = {
    "ai_data": "Which AI or data use case is closest to production, and what is stopping it?",
    "qa": "How much of your regression testing is automated today, and where do releases slip?",
    "delivery_team": "Is there a piece of work you would rather hand to one team end to end?",
    "team_extension": "Which roles have been open longest, and what does that delay?",
}
AVOID = {
    "decision": "Long capability lists. One programme, one outcome, one next step.",
    "technical": "Generic claims about great engineers. Talk about their stack, their bottleneck and how we work in their team.",
    "procurement": "Technical detail first. Start with how we can qualify as a supplier and what we already meet.",
    "influencer": "Pushing for a meeting with the decision maker straight away. Learn what they need first.",
}
ROLE_AFFINITY = {
    "ai_data": re.compile(r"\bai\b|data|analytics|machine learning|\bml\b|insight|intelligence", re.I),
    "qa": re.compile(r"\bqa\b|quality|test", re.I),
    "delivery_team": re.compile(r"delivery|programme|program|transformation|digital|product|platform|architect|cto|cio", re.I),
    "team_extension": re.compile(r"engineering|development|software|head of (it|technology)|vp eng|tech lead|engineering manager", re.I),
}
TEAM_HINT = re.compile(r"\b(team|organisation|organization|department|unit) of (\d[\d,]*)\b|\b(\d[\d,]*)\+? (engineers|developers|people|staff|employees|technologists)\b", re.I)
KIND_WORD = {"talk": "talk", "post": "post", "article": "article", "interview": "interview", "podcast": "podcast", "news": "news", "appointment": "appointment",
             "award": "award", "other": "update"}
ASK_FOR = {"delivery_team": "who is building it, and is there room for an outside team?", "team_extension": "is the team big enough to deliver it this year?",
           "ai_data": "what does it take to move that into production?", "qa": "how are you keeping release quality up while you ship it?"}


def _lens_group(title: str | None, lead: dict[str, str], seniority: str | None) -> str:
    from .api.profiles import committee_group
    return committee_group(title, seniority, lead.get("persona"), lead.get("committee_role"))


def person_brief(conn: Conn, person_id: str) -> dict[str, Any]:
    """A sales brief for one person: how they are likely to look at us, the best fit for them, openers from dated evidence, questions for
    their role, what we know (with sources), what we still need to find out, and what to avoid. The caller checks visibility."""
    today = datetime.now(timezone.utc).date()
    pe = q1(conn, "SELECT id, full_name, linkedin_url, work_email IS NOT NULL AS has_email, suppressed, suppression_reason FROM people WHERE id=%s AND erased_at IS NULL", (person_id,))
    if not pe:
        raise LookupError("person not found")
    job = q1(conn, "SELECT e.title, e.organization_id::text AS org, e.valid_from, o.display_name, o.employees_estimate, o.employees_uncertain FROM employment e "
                   "JOIN organizations o ON o.id=e.organization_id WHERE e.person_id=%s ORDER BY (e.valid_to IS NULL) DESC, e.observed_at DESC NULLS LAST LIMIT 1", (person_id,))
    lead: dict[str, str] = {}
    for s in qa(conn, "SELECT detail FROM person_sources WHERE person_id=%s AND system='sheets' ORDER BY first_seen_at", (person_id,)):
        lead.update({k: str(v).strip() for k, v in (s["detail"] or {}).items() if str(v or "").strip() and str(v).strip() != "Unknown"})
    prof = q1(conn, "SELECT content, finished_at FROM person_profiles WHERE person_id=%s AND status='done' ORDER BY created_at DESC LIMIT 1", (person_id,))
    pc = (prof or {}).get("content") or {}
    confident = (pc.get("identity") or {}).get("confident") is not False
    cur = pc.get("current_role") or {}
    title = (job or {}).get("title") or cur.get("title") or lead.get("title")
    from .api.profiles import title_seniority
    seniority = lead.get("seniority") or title_seniority(title)
    group = _lens_group(title, lead, seniority)
    company = solution_fit(conn, job["org"]) if job else {"services": [], "best": None, "why_now": None, "watch_outs": [], "unknowns": []}

    # best fit for this person: the company's evidence, nudged towards what their role owns
    remit = " ".join([str(title or ""), str(lead.get("persona") or ""), str(cur.get("remit") or ""), *(str(f.get("label") or "") for f in pc.get("focus_areas") or [])])
    aff = {s: bool(rx.search(remit)) for s, rx in ROLE_AFFINITY.items()}
    best_fit = None
    if company["services"]:
        pick = max(company["services"], key=lambda s: (s["score"] + (20 if aff.get(s["service"]) else 0), -SERVICES.index(s["service"])))
        top = pick["why"][0] if pick["why"] else None
        why = f"{_clip(top['text'], 140)}{' (' + _fmt_day(top['date']) + ')' if top and top.get('date') and top['kind'] != 'tender' else ''}" if top else ""
        if aff.get(pick["service"]) and pick["service"] != company["best"]:
            why = f"Their role covers this area, and the company shows a need: {why}"
        best_fit = {"service": pick["service"], "label": pick["label"], "why": why or "Fits what the company is doing.", "level": pick["level"], "score": pick["score"]}
    elif any(aff.values()):
        s = next(x for x in ("ai_data", "qa", "delivery_team", "team_extension") if aff.get(x))
        best_fit = {"service": s, "label": LABEL[s], "why": "A suggestion from their role only: we hold no evidence of a need at the company yet.", "level": "weak", "score": 0}

    svc = (best_fit or {}).get("service")
    cares = [*CARES[group]]
    if svc and CARES_SERVICE.get(svc) not in cares:
        cares.insert(1 if group == "decision" else 0, CARES_SERVICE[svc])
    if confident:
        cares.extend(f"Named in their own profile: {f['label']}" for f in (pc.get("focus_areas") or [])[:2] if f.get("label"))

    # openers: their own dated activity first, then the company's dated evidence, then the lead list's note about them
    openers: list[dict[str, Any]] = []
    ask = ASK_FOR.get(svc or "delivery_team")
    if confident:
        for a in sorted(pc.get("recent_activity") or [], key=lambda x: str(x.get("date") or ""), reverse=True)[:2]:
            d = _day(a.get("date"))
            if not a.get("title") or (_age(d, today) or 0) > 540:
                continue
            openers.append({"text": f"Your {KIND_WORD.get(a.get('kind') or 'other', 'update')} “{_clip(a['title'], 90)}”{' (' + _fmt_day(d) + ')' if d else ''} caught my eye: {ask}",
                            "because": "Their own public activity: specific, recent and about their work.", "source_url": _url(a.get("source_url")), "date": d})
    for s in company["services"]:
        for e in s["why"]:
            if len(openers) >= 3:
                break
            if e["kind"] in ("signal", "tender", "news", "lead_list", "job") and e.get("date") and (_age(e["date"], today) or 0) <= 365 \
                    and not any(o["source_url"] and o["source_url"] == e["source_url"] for o in openers):
                line = _clip(e["text"], 150)
                lead_in = "I saw" if e["kind"] in ("news", "signal", "lead_list") else "I noticed"
                openers.append({"text": f"{lead_in}: {line}{'' if e['kind'] == 'tender' else ' (' + _fmt_day(e['date']) + ')'}. {ask[0].upper() + ask[1:]}",
                                "because": f"Company evidence behind the best fit ({LABEL[s['service']]}).", "source_url": e["source_url"], "date": e["date"]})
        if len(openers) >= 2:
            break
    if len(openers) < 3 and lead.get("signal"):
        openers.append({"text": f"Open with what your lead list noted about them: {_clip(lead['signal'], 180)}", "because": "Your lead list's note about this person (not checked by us).",
                        "source_url": None, "date": None})
    if len(openers) < 3 and lead.get("angle"):
        openers.append({"text": f"Angle from your lead list: {_clip(lead['angle'], 180)}", "because": "Your lead list's suggested angle.", "source_url": None, "date": None})
    if len(openers) < 2 and confident:
        for t in (pc.get("talking_points") or [])[:2]:
            cite = next((c for c in t.get("sources") or [] if _url(c.get("source_url"))), {})
            if t.get("point") and len(openers) < 3:
                openers.append({"text": _clip(t["point"], 200), "because": _clip(t.get("why") or "From their Exa profile.", 200),
                                "source_url": cite.get("source_url"), "date": _day(cite.get("published"))})

    questions = [*QUESTIONS[group]]
    if svc:
        questions.insert(1, QUESTION_SERVICE[svc])
    if any("How they buy" in u for u in company["unknowns"]) and group != "procurement":
        questions.append("How do you usually bring in an outside engineering partner: a framework, a supplier panel or directly?")
    questions = list(dict.fromkeys(questions))[:5]

    facts: list[dict[str, Any]] = []
    if title:
        facts.append({"label": "Role", "value": title, "source": "Our records"})
    if confident and cur.get("remit"):
        facts.append({"label": "Remit", "value": _clip(cur["remit"], 300), "source": "Exa research", "source_url": _url(cur.get("source_url"))})
    since = str(cur.get("since") or "").strip() if confident else ""
    if re.fullmatch(r"(19|20)\d{2}", since):
        facts.append({"label": "In this role since", "value": f"{since} (about {max(0, today.year - int(since))} years)" if today.year > int(since) else since,
                      "source": "Exa research", "source_url": _url(cur.get("source_url"))})
    elif job and job.get("valid_from"):
        facts.append({"label": "In this role since", "value": _fmt_day(_day(job["valid_from"])), "source": "Our records"})
    if confident and pc.get("focus_areas"):
        facts.append({"label": "Focus areas", "value": ", ".join(str(f.get("label")) for f in pc["focus_areas"][:5] if f.get("label")), "source": "Exa research"})
    if confident and pc.get("recent_activity"):
        a = sorted(pc["recent_activity"], key=lambda x: str(x.get("date") or ""), reverse=True)[0]
        facts.append({"label": "Latest public activity", "value": f"{_clip(a.get('title'), 120)}{' (' + _fmt_day(_day(a.get('date'))) + ')' if _day(a.get('date')) else ''}",
                      "source": "Exa research", "source_url": _url(a.get("source_url"))})
    if confident and pc.get("previous_roles"):
        r0 = pc["previous_roles"][0]
        facts.append({"label": "Before this", "value": _clip(f"{r0.get('title') or ''}, {r0.get('company') or ''}".strip(", "), 120), "source": "Exa research"})
    for k, label in (("seniority", "Seniority"), ("persona", "Persona"), ("committee_role", "Part in the buying decision"), ("priority", "Priority")):
        if lead.get(k):
            facts.append({"label": label, "value": lead[k], "source": "Lead list"})
    hint = TEAM_HINT.search(str(cur.get("remit") or "")) if confident else None
    if hint:
        facts.append({"label": "Team size hint", "value": _clip(hint.group(0), 60), "source": "Exa research", "source_url": _url(cur.get("source_url"))})
    if job and job.get("employees_estimate"):
        facts.append({"label": "Company size", "value": f"{'about ' if job.get('employees_uncertain') else ''}{int(job['employees_estimate']):,} staff", "source": "Our records"})
    acts = qa(conn, "SELECT type, happened_at FROM lemlist_activities WHERE person_id=%s ORDER BY happened_at DESC NULLS LAST LIMIT 20", (person_id,))
    enrol = qa(conn, "SELECT status, coalesce(last_event_at, created_at) AS at FROM outbound_enrolments WHERE person_id=%s ORDER BY created_at DESC", (person_id,))
    from .people import ACTIVITY_STATUS, engagement
    eng = engagement([*(e["status"] for e in enrol), *(ACTIVITY_STATUS.get(a["type"]) for a in acts)])
    last = max([x for x in [*(a["happened_at"] for a in acts), *(e["at"] for e in enrol)] if x], default=None)
    from .api.profiles import ENGAGEMENT_LABEL
    facts.append({"label": "Our contact so far", "value": f"{ENGAGEMENT_LABEL.get(eng, eng)}{', last on ' + _fmt_day(_day(last)) if last else ''}", "source": "Outreach history"})
    facts.append({"label": "Email", "value": "Email ✓" if pe["has_email"] else "No email", "source": "Our records"})

    gaps: list[str] = []
    if not prof:
        gaps.append("We have not researched this person with Exa: their remit, focus areas and recent activity are not known here.")
    elif not confident:
        gaps.append("Exa was not sure the pages it found are about this person, so we left them out. Check the profile before using it.")
    if not since and not (job or {}).get("valid_from"):
        gaps.append("How long they have been in this role.")
    if not hint:
        gaps.append("How big their team is, and how much of it is external.")
    if not lead.get("committee_role"):
        gaps.append("Their part in the buying decision: do they own budget, choose suppliers or advise?")
    if not pe["has_email"]:
        gaps.append("An email address: find one, or start on LinkedIn.")
    gaps.extend(u for u in company["unknowns"] if u.startswith(("How they buy", "Whether they have budget")))
    gaps = list(dict.fromkeys(gaps))[:6]

    avoid: list[str] = []
    if pe["suppressed"]:
        avoid.append(f"Do not contact them: {pe['suppression_reason'] or 'they are on the do-not-contact list'}.")
    if eng == "unsubscribed":
        avoid.append("They unsubscribed from our emails: do not email them again.")
    elif eng == "not_interested":
        avoid.append(f"They told us they are not interested{' on ' + _fmt_day(_day(last)) if last else ''}. Wait, and come back only with a new reason.")
    elif eng == "bounced":
        avoid.append("Our last email to them bounced: check the address before writing again.")
    elif eng in ("sent", "opened", "clicked", "pushed"):
        avoid.append("They are already in one of our email sequences: do not start a second one in parallel.")
    avoid.append(AVOID[group])
    avoid.append("Client names, unless the case study is approved for use with clients, and any number we cannot point to.")
    for w in company["watch_outs"][:2]:
        avoid.append(w)

    return _scrub({"lens": {"role": title or "Role not recorded", "cares_about": cares[:5], "committee": COMMITTEE_LABEL[group], "committee_key": group},
                   "best_fit": best_fit, "openers": openers[:3], "questions": questions, "facts": facts, "gaps": gaps, "avoid": list(dict.fromkeys(avoid))[:5],
                   "company": {"id": job["org"], "name": job["display_name"], "best": company["best"], "why_now": company["why_now"]} if job else None})
