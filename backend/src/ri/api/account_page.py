"""The company page, said once: the reasons to get in touch now (strongest first, at most three) and opportunities without repeats.

Why this exists (owner feedback, 25 Nov 2026: "opportunities are repeating ... two of the same kind"):
  * the opportunity engine keeps ONE hypothesis per route (direct, through the platform vendor, through the integrator), so one programme
    showed up as two or three cards with the same title;
  * a second event about the same kind of programme can open a second initiative with the same working title;
  * the lead list's buying-signal log often records one tender or announcement several times (same page, same date).
Everything here is pure (no database), so the rules are easy to test.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Iterable

WORKING_TITLE = re.compile(r"\s*\((?:analyst )?working title\)\s*$", re.I)
TIER_SCORE = {"D1": 100, "D2": 85, "D3": 65, "D4": 45}
LIST_CONFIDENCE = {"high": 70, "medium": 55}
MAX_REASONS = 3
MAX_AGE_DAYS = 365


def plain_title(title: str | None) -> str:
    """An initiative title without the internal 'analyst working title' marker."""
    return WORKING_TITLE.sub("", title or "").strip()


def kind_key(title: str | None) -> str:
    return re.sub(r"[^\w]+", " ", plain_title(title).casefold()).strip()


def url_key(url: str | None) -> str:
    u = (url or "").strip().casefold()
    u = re.sub(r"^https?://(www\.)?", "", u).split("#")[0]
    return u.rstrip("/")


def group_opportunities(hyps: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """One group per programme: every route of one initiative, and initiatives of the same kind (same plain title), together.
    `hyps` come best first; each group keeps that order, so its first item leads the card."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for h in hyps:
        key = kind_key(h.get("initiative_title")) or f"initiative:{h['initiative_id']}"
        groups.setdefault(key, []).append(h)
    return list(groups.values())


def opportunity_cards(conn: Any, hyps: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """One card per programme (built from its best-scored hypothesis), listing every way in and how many records it brings together.
    Cards are built only for the group leaders, so a programme with three routes costs one card's queries, not three."""
    from ..readmodel import card
    out = []
    for g in group_opportunities(hyps):
        c = card(conn, g[0], now)
        c["initiative"]["plain_title"] = plain_title(c["initiative"]["title"])
        routes: dict[tuple[str, str], dict[str, Any]] = {}
        for h in g:
            routes.setdefault((h["route_type"], h["counterparty_name"]), {"hypothesis_id": str(h["id"]), "type": h["route_type"], "counterparty": h["counterparty_name"],
                                                                        "access_status": h["access_status"], "priority": h["priority"]})
        c["routes"] = list(routes.values())
        c["sources"] = len(g)
        c["programmes"] = len({str(h["initiative_id"]) for h in g})
        out.append(c)
    return out


def dedupe_list_signals(rows: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    """The lead list's buying signals, one per story: the same page on the same date is one signal, however often it was logged.
    Rows come newest first; the first row of a story is kept and counts how often the list recorded it."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        text = re.sub(r"\s+", " ", str(r.get("event") or r.get("category") or "")).strip().casefold()
        key = (url_key(r.get("source_url")) or text[:120], str(r.get("date") or "")[:10])
        if not key[0]:
            continue
        if key in out:
            out[key]["repeats"] += 1
        else:
            out[key] = {**r, "repeats": 1}
    return list(out.values())[:limit]


def _day(v: Any) -> str | None:
    """A date as YYYY-MM-DD, YYYY-MM or YYYY (research dates can be partial)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    m = re.match(r"\d{4}(-\d{2}(-\d{2})?)?", str(v).strip())
    return m.group(0) if m else None


def _age_days(d: str | None, now: datetime) -> int | None:
    if not d:
        return None
    parts = [int(x) for x in d.split("-")] + [1, 1]
    try:
        return (now.date() - date(parts[0], parts[1], parts[2])).days
    except ValueError:
        return None


def _fresh(score: int, age: int | None) -> int:
    if age is None:
        return score - 10
    return score + (8 if age <= 30 else 4 if age <= 90 else 0 if age <= 180 else -15)


def _strength(score: int) -> int:
    return 3 if score >= 80 else 2 if score >= 55 else 1


def why_now(*, lead_signal: dict[str, Any] | None, signals: Iterable[dict[str, Any]], list_signals: Iterable[dict[str, Any]],
            recent: Iterable[dict[str, Any]], tenders: Iterable[dict[str, Any]], list_profile: dict[str, Any],
            opportunities: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    """At most three reasons to get in touch now, strongest first, each with a date and a link, and one suggested next step.

    Strength: an open tender or a signal that shows they want outside help comes first, then programmes and hiring we checked,
    then the lead list's own signals, then news the research found. Newer beats older; anything older than a year is not a reason
    to call *now*. The same page never gives two reasons."""
    now = now or datetime.now(timezone.utc)
    cands: list[dict[str, Any]] = []

    for t in tenders:
        deadline = _day(t.get("deadline_at"))
        if deadline and (_age_days(deadline, now) or 0) > 0:
            continue                                            # closed: not a reason to call
        cands.append({"kind": "tender", "text": t.get("title") or "Open tender", "detail": None, "date": _day(t.get("published_at")), "deadline": deadline,
                      "url": t.get("official_url"), "rule_id": "SRC-01", "score": 95})

    seen_story: set[str] = set()
    sigs = [s for s in signals if s.get("status") == "active"]
    if lead_signal:
        seen_story.add(str(lead_signal.get("event_id")))
        d = _day(lead_signal.get("happened_at") or lead_signal.get("published_at"))
        cands.append({"kind": "signal", "text": lead_signal.get("summary") or lead_signal.get("label"), "detail": lead_signal.get("meaning"), "date": d,
                      "url": lead_signal.get("source_url"), "rule_id": lead_signal.get("rule_id"), "demand_tier": lead_signal.get("demand_tier"),
                      "score": _fresh(TIER_SCORE.get(lead_signal.get("demand_tier") or "", 45), _age_days(d, now))})
        for s in sigs:
            if str(s.get("event_id")) == str(lead_signal.get("event_id")) and s.get("story_key"):
                seen_story.add(str(s["story_key"]))
    for s in sorted(sigs, key=lambda x: (x.get("demand_tier") or "D9")):
        story = str(s.get("story_key") or s.get("event_id"))
        if story in seen_story or str(s.get("event_id")) in seen_story:
            continue
        url = next((e.get("url") for e in (s.get("evidence") or []) if str(e.get("url") or "").startswith(("http://", "https://"))), None)
        if not url:
            continue                                            # a reason always carries its source
        seen_story.add(story)
        d = _day(s.get("event_at") or s.get("pub_at"))
        cands.append({"kind": "signal", "text": s.get("summary"), "detail": None, "date": d, "url": url, "rule_id": s.get("rule_id"),
                      "demand_tier": s.get("demand_tier"), "score": _fresh(TIER_SCORE.get(s.get("demand_tier") or "", 45), _age_days(d, now))})

    for x in list_signals:
        d = _day(x.get("date"))
        age = _age_days(d, now)
        if age is not None and age > MAX_AGE_DAYS:
            continue
        text = x.get("category") or x.get("event")
        if not text:
            continue
        cands.append({"kind": "lead_list", "text": text, "detail": x.get("event") if x.get("event") != text else None, "date": d, "url": x.get("source_url"),
                      "repeats": x.get("repeats", 1), "score": _fresh(LIST_CONFIDENCE.get(str(x.get("confidence") or "").strip().casefold(), 40), age)})

    for r in recent:
        d = _day(r.get("date"))
        age = _age_days(d, now)
        if not r.get("headline") or not r.get("source_url") or (age is not None and age > MAX_AGE_DAYS):
            continue
        cands.append({"kind": "news", "text": r["headline"], "detail": r.get("detail"), "date": d, "url": r["source_url"], "score": _fresh(50, age)})

    cands.sort(key=lambda c: (-c["score"], -(_ordinal(c.get("date")))))
    reasons: list[dict[str, Any]] = []
    urls: set[str] = set()
    for c in cands:
        k = url_key(c.get("url"))
        if k and k in urls:
            continue
        if k:
            urls.add(k)
        c["strength"] = _strength(c.pop("score"))
        reasons.append(c)
        if len(reasons) == MAX_REASONS:
            break

    return {"reasons": reasons, "next_step": _next_step(reasons, lead_signal, list_profile, opportunities)}


def _ordinal(d: str | None) -> int:
    if not d:
        return 0
    parts = [int(x) for x in d.split("-")] + [1, 1]
    try:
        return date(parts[0], parts[1], parts[2]).toordinal()
    except ValueError:
        return 0


# internal review steps (an analyst's or the delivery team's) are not a seller's next step
NOT_FOR_SELLERS = {"disqualify_review", "delivery_feasibility_check", "monitor"}


def _next_step(reasons: list[dict[str, Any]], lead_signal: dict[str, Any] | None, list_profile: dict[str, Any], opportunities: list[dict[str, Any]]) -> dict[str, Any] | None:
    tender = next((r for r in reasons if r["kind"] == "tender"), None)
    if tender:
        return {"kind": "tender", "date": tender.get("deadline"), "url": tender.get("url"), "text": None}
    action = str(list_profile.get("Next Action") or "").strip()
    if action:
        return {"kind": "lead_list", "text": action}
    q = (lead_signal or {}).get("commercial_questions") or []
    if q and str(q[0]).strip():
        return {"kind": "ask", "text": str(q[0]).strip()}
    for o in opportunities:
        a = o.get("next_action") or {}
        if a.get("action_type") and a["action_type"] not in NOT_FOR_SELLERS:
            return {"kind": "opportunity", "action_type": a["action_type"], "text": a.get("objective"), "counterparty": (o.get("route") or {}).get("counterparty"),
                    "hypothesis_id": o.get("hypothesis_id")}
    return None
