"""Administration endpoints that let the platform do its own housekeeping, with a person only where a person is needed.

* Company matches: what the automatic matcher decided (with the reason and an undo) and the few reviews that still need a person.
* Sources: counts by state, what the daily automatic test switched on, and exactly why the rest cannot be switched on automatically.
* Failed jobs: grouped by cause in plain English, with whether the cause is still happening, and bulk retry or dismiss per group.
* Usage: monthly gauges with a forecast, a daily spend series, and spend by purpose with the cost of each useful result.
"""
from __future__ import annotations

import calendar
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ..access import Principal
from ..audit import audit
from ..db import Conn, canonical_json, q1, qa
from .auth import require


class GroupIn(BaseModel):
    key: str = Field(min_length=2, max_length=200)
    note: str | None = Field(default=None, max_length=300)


class UndoIn(BaseModel):
    note: str | None = Field(default=None, max_length=300)


# ====================================================================== failed jobs, grouped by cause

CAUSES: list[tuple[str, str, str]] = [
    ("ai_credit", "AI credit ran out", "The AI provider's account ran out of credit, so pages, summaries and checks could not be read. Nothing was wrong with the work itself."),
    ("budget", "Monthly budget used up", "The monthly limit for AI or web search was reached, so the work stopped as designed. A new month, or a higher limit, lets it run."),
    ("fit_bug", "Old fit-check bug", "A bug in the fit check left a database transaction broken, so the whole batch stopped. The bug is fixed; these can be retried."),
    ("empty_output", "The AI returned an empty answer", "The model answered with nothing usable, usually for a very long page or one it would not summarise. A retry usually works."),
    ("timeout", "Timeouts", "The other side did not answer in time. Usually a slow or busy site; a retry later normally works."),
    ("http_400", "{source} returned 400 (bad request)", "The service rejected our request as malformed. Usually a change on their side, such as a query or field it no longer accepts."),
    ("rate_limited", "{source} asked us to slow down", "Too many requests in a short time. A retry later works."),
    ("server_error", "{source} had server errors", "The other side failed (an error 5xx). That is their problem, not ours; a retry later normally works."),
    ("not_found", "{source}: address not found", "A page or record we asked for no longer exists (404)."),
    ("blocked", "{source} refused or blocked us", "The site refused access (robots.txt, a bot check or a missing permission). Retrying will not help until access is sorted out."),
    ("format", "{source} answered in an unexpected format", "The answer did not look like what the reader expects, often after a site redesign. The reader may need a fix."),
    ("no_handler", "Jobs no worker could run", "The job's type was not known to the worker or its details were incomplete."),
    ("other", "{kind}: {error_class}", "A failure that does not match a known cause. The error text below says what happened."),
]
CAUSE = {k: (t, e) for k, t, e in CAUSES}
NOT_RETRYABLE_KINDS = {"sheet_import_xlsx", "approval_gate"}         # the uploaded file is deleted after the attempt; approvals have their own flow


def classify(kind: str, ec: str, err: str) -> str:
    e = err or ""
    if ec == "provider_billing" or re.search(r"credit balance|out of credit|insufficient (?:credit|funds)|payment required|billing", e, re.I):
        return "ai_credit"
    if ec == "budget_exceeded" or "would exceed the hard cap" in e:
        return "budget"
    if kind == "assess_fit" and re.search(r"transaction|InFailedSqlTransaction|aborted", e, re.I):
        return "fit_bug"
    if re.search(r"empty model output", e, re.I):
        return "empty_output"
    if ec == "timeout" or re.search(r"timeout|timed out|ReadTimeout|ConnectTimeout|PoolTimeout", e, re.I):
        return "timeout"
    if ec == "quarantined" or ec == "invalid_payload" and re.search(r"no handler", e):
        return "no_handler"
    if ec == "rate_limited" or re.search(r"\b429\b", e):
        return "rate_limited"
    if re.search(r"\b400\b|bad request", e, re.I):
        return "http_400"
    if ec == "server_error" or re.search(r"\b5\d\d\b", e) and re.search(r"status|error|HTTP", e, re.I):
        return "server_error"
    if ec == "not_found" or re.search(r"\b404\b", e):
        return "not_found"
    if ec in ("policy_block", "access_denied", "auth") or re.search(r"robots|\b403\b|forbidden", e, re.I):
        return "blocked"
    if ec == "schema_drift":
        return "format"
    return "other"


def _subject_source(kind: str, payload: dict[str, Any]) -> str | None:
    if kind in ("source_run",) and isinstance(payload.get("source_id"), str):
        return payload["source_id"]
    if kind == "ted_awards_window":
        return "procurement-ted"
    return None


def _letters(conn: Conn, *, open_only: bool) -> list[dict[str, Any]]:
    return qa(conn, "SELECT id, job_id, kind, payload, error_class, error, created_at, replayed_at, dismissed_at, dismissed_by, dismiss_note FROM dead_letters "
                    + ("WHERE replayed_at IS NULL AND dismissed_at IS NULL " if open_only else "") + "ORDER BY created_at DESC LIMIT 3000")


def _key(cause: str, kind: str, payload: dict[str, Any]) -> str:
    src = _subject_source(kind, payload) if cause in ("http_400", "rate_limited", "server_error", "not_found", "blocked", "format") else None
    if cause == "other":
        return f"other:{kind}"
    return f"{cause}:{src}" if src else cause


def _state(conn: Conn, cause: str, kinds: set[str], source: str | None, last_at: datetime) -> tuple[str, str, datetime | None]:
    """resolved (the cause is gone), ongoing (failed in the last two days), quiet (not seen for a while)."""
    def worked_since(kind_list: list[str]) -> datetime | None:
        r = q1(conn, "SELECT max(updated_at) AS t FROM jobs WHERE kind = ANY(%s) AND status='completed' AND updated_at > %s "
                     "AND (%s::text IS NULL OR payload->>'source_id' = %s)", (kind_list, last_at, source, source))
        return r["t"] if r else None
    if cause == "ai_credit":
        r = q1(conn, "SELECT min(at) AS t FROM provider_usage WHERE provider='anthropic' AND status='ok' AND at > %s", (last_at,))
        if r and r["t"]:
            return "resolved", "ai_ok", r["t"]
    elif cause == "budget":
        if last_at.strftime("%Y-%m") != datetime.now(timezone.utc).strftime("%Y-%m"):
            return "resolved", "new_month", None
    elif cause == "fit_bug":
        t = worked_since(["assess_fit"])
        if t:
            return "resolved", "job_ok", t
    else:
        if "extract_document" in kinds:
            r = q1(conn, "SELECT min(at) AS t FROM provider_usage WHERE provider='anthropic' AND operation='extract' AND status='ok' AND at > %s", (last_at,))
            if r and r["t"]:
                return "resolved", "pages_ok", r["t"]
        t = worked_since(sorted(kinds))
        if t:
            return "resolved", "job_ok", t
    if datetime.now(timezone.utc) - last_at < timedelta(hours=48):
        return "ongoing", "recent", last_at
    return "quiet", "quiet", last_at


def _subjects(conn: Conn, letters: list[dict[str, Any]]) -> dict[str, str]:
    """A short plain name for what each failed job was about (a source, a page, a company)."""
    src_ids = {lt["payload"].get("source_id") for lt in letters if isinstance(lt["payload"].get("source_id"), str)}
    srcs = {r["source_id"]: r["display_name"] for r in qa(conn, "SELECT source_id, display_name FROM sources WHERE source_id = ANY(%s)", (list(src_ids),))} if src_ids else {}
    doc_ids = [lt["payload"].get("document_id") for lt in letters if isinstance(lt["payload"].get("document_id"), str)]
    docs = {str(r["id"]): (r["title"] or r["canonical_url"] or "") for r in qa(conn, "SELECT id, title, canonical_url FROM source_documents WHERE id::text = ANY(%s)", (doc_ids,))} if doc_ids else {}
    org_ids = [lt["payload"].get("organization_id") for lt in letters if isinstance(lt["payload"].get("organization_id"), str)]
    orgs = {str(r["id"]): r["display_name"] for r in qa(conn, "SELECT id, display_name FROM organizations WHERE id::text = ANY(%s)", (org_ids,))} if org_ids else {}
    out: dict[str, str] = {}
    for lt in letters:
        p = lt["payload"] or {}
        s = srcs.get(p.get("source_id")) or docs.get(p.get("document_id") or "") or orgs.get(p.get("organization_id") or "") or p.get("url") or p.get("workbook") or ""
        out[str(lt["id"])] = str(s)[:140]
    return out


def _span(a: datetime, b: datetime) -> str:
    if a.date() == b.date():
        return f"{a:%d %b}".lstrip("0")
    if a.month == b.month:
        return f"{a.day}–{b.day} {b:%b}"
    return f"{a:%d %b} – {b:%d %b}"


def failure_groups(conn: Conn) -> dict[str, Any]:
    letters = _letters(conn, open_only=False)
    names = {r["source_id"]: r["display_name"] for r in qa(conn, "SELECT source_id, display_name FROM sources")}
    groups: dict[str, dict[str, Any]] = {}
    for lt in letters:
        p = lt["payload"] or {}
        cause = classify(lt["kind"], lt["error_class"], lt["error"])
        key = _key(cause, lt["kind"], p)
        g = groups.setdefault(key, {"key": key, "cause": cause, "kinds": set(), "source": None, "letters": [], "open": [], "error_classes": set()})
        g["kinds"].add(lt["kind"])
        g["error_classes"].add(lt["error_class"])
        if key.startswith(cause + ":") and cause != "other":
            g["source"] = key.split(":", 1)[1]
        g["letters"].append(lt)
        if lt["replayed_at"] is None and lt["dismissed_at"] is None:
            g["open"].append(lt)
    subjects = _subjects(conn, [lt for g in groups.values() for lt in g["open"][:30]])
    out = []
    for g in groups.values():
        allx = g["letters"]
        first, last = min(x["created_at"] for x in allx), max(x["created_at"] for x in allx)
        title_t, explain = CAUSE[g["cause"]]
        kind0 = sorted(g["kinds"])[0]
        title = title_t.format(source=names.get(g["source"] or "", g["source"] or "The service"), kind=kind0, error_class=sorted(g["error_classes"])[0].replace("_", " "))
        state, basis, since = _state(conn, g["cause"], g["kinds"], g["source"], last) if g["open"] else ("handled", "handled", None)
        retried = sum(1 for x in allx if x["replayed_at"])
        dismissed = sum(1 for x in allx if x["dismissed_at"])
        samples = list(dict.fromkeys(re.sub(r"\s+", " ", x["error"])[:220] for x in (g["open"] or allx)))[:3]
        out.append({"key": g["key"], "cause": g["cause"], "title": title, "explain": explain, "kinds": sorted(g["kinds"]), "source": g["source"],
                    "open": len(g["open"]), "total": len(allx), "retried": retried, "dismissed": dismissed, "first_at": first, "last_at": last, "when": _span(first, last),
                    "source_name": names.get(g["source"] or "") if g["source"] else None, "state": state, "state_basis": basis, "state_since": since, "samples": samples,
                    "retryable": bool(g["open"]) and not (g["kinds"] <= NOT_RETRYABLE_KINDS) and g["cause"] not in ("no_handler",),
                    "items": [{"id": str(x["id"]), "kind": x["kind"], "subject": subjects.get(str(x["id"]), ""), "error": re.sub(r"\s+", " ", x["error"])[:300],
                               "error_class": x["error_class"], "created_at": x["created_at"]} for x in g["open"][:30]]})
    order = {"ongoing": 0, "quiet": 1, "resolved": 2, "handled": 3}
    out.sort(key=lambda g: (order[g["state"]], -g["open"], -g["last_at"].timestamp()))
    retrying = q1(conn, "SELECT count(*) AS n, count(*) FILTER (WHERE last_error_class='provider_billing') AS billing FROM jobs WHERE status='retryable_failed'") or {}
    return {"groups": out, "open": sum(g["open"] for g in out), "open_groups": sum(1 for g in out if g["open"]),
            "still_happening": sum(g["open"] for g in out if g["state"] == "ongoing"), "retrying_now": retrying.get("n", 0), "retrying_billing": retrying.get("billing", 0)}


def _open_in_group(conn: Conn, key: str) -> list[dict[str, Any]]:
    return [lt for lt in _letters(conn, open_only=True) if _key(classify(lt["kind"], lt["error_class"], lt["error"]), lt["kind"], lt["payload"] or {}) == key]


def retry_group(conn: Conn, key: str, actor: str, limit: int = 200) -> dict[str, Any]:
    """Re-queues the group's open failures safely: each distinct piece of work once (duplicates are marked as covered), pages that failed to be
    read are queued for reading again, and kinds that cannot be repeated are left alone."""
    from ..jobs.queue import replay_dead_letter
    letters = _open_in_group(conn, key)[:limit]
    seen: dict[str, str] = {}
    queued = covered = requeued_pages = skipped = 0
    for lt in letters:                                  # newest first
        if lt["kind"] in NOT_RETRYABLE_KINDS:
            skipped += 1
            continue
        h = hashlib.sha256((lt["kind"] + canonical_json(lt["payload"] or {})).encode()).hexdigest()
        if h in seen:
            conn.execute("UPDATE dead_letters SET replayed_at=now(), dismiss_note=%s WHERE id=%s", (f"covered by the retry of {seen[h]}", lt["id"]))
            covered += 1
            continue
        seen[h] = str(lt["id"])
        if lt["kind"] == "extract_document":
            doc = (lt["payload"] or {}).get("document_id")
            ok = doc and conn.execute("UPDATE source_documents SET raw_meta = coalesce(raw_meta,'{}'::jsonb) || '{\"pending_extraction\": true}'::jsonb "
                                      "WHERE id::text=%s AND content_text IS NOT NULL AND NOT EXISTS (SELECT 1 FROM event_documents ed WHERE ed.document_id=source_documents.id)",
                                      (doc,)).rowcount
            if ok:
                conn.execute("UPDATE dead_letters SET replayed_at=now(), dismiss_note='page queued to be read again' WHERE id=%s", (lt["id"],))
                requeued_pages += 1
            else:
                conn.execute("UPDATE dead_letters SET dismissed_at=now(), dismissed_by=%s, dismiss_note='page text no longer stored or already read; nothing to retry' WHERE id=%s", (actor, lt["id"]))
                skipped += 1
            continue
        replay_dead_letter(conn, str(lt["id"]), actor)
        queued += 1
    audit(conn, actor=actor, operation="dead_letters_retried", record_type="dead_letter_group", record_id=key[:120],
          detail={"queued": queued, "pages_requeued": requeued_pages, "covered": covered, "skipped": skipped})
    return {"queued": queued, "pages_requeued": requeued_pages, "covered": covered, "skipped": skipped}


def dismiss_group(conn: Conn, key: str, actor: str, note: str | None) -> dict[str, Any]:
    ids = [lt["id"] for lt in _open_in_group(conn, key)]
    if ids:
        conn.execute("UPDATE dead_letters SET dismissed_at=now(), dismissed_by=%s, dismiss_note=%s WHERE id = ANY(%s)", (actor, note or "marked handled", ids))
    audit(conn, actor=actor, operation="dead_letters_dismissed", record_type="dead_letter_group", record_id=key[:120], detail={"count": len(ids), "note": note})
    return {"dismissed": len(ids)}


# ====================================================================== usage insights

PURPOSES: dict[str, tuple[str, str]] = {
    "extract": ("reading", "Reading pages"), "search": ("web_search", "Web search"), "tender_summary": ("tenders", "Tender summaries"),
    "account_profile": ("company", "Company research"), "account_brief": ("company", "Company research"), "person_profile": ("person", "Person research"),
    "people_search": ("find_people", "Finding people"), "assess_fit": ("fit", "Fit reasons"), "campaign_draft": ("emails", "Email drafts"),
    "identity_match": ("matching", "Company matching"),
}
USEFUL_LABEL = {"reading": "findings", "web_search": "pages found", "tenders": "summaries", "company": "profiles", "person": "profiles", "find_people": "people found",
                "fit": "findings explained", "emails": "drafts", "matching": "companies matched"}


def _useful(conn: Conn, start: datetime) -> dict[str, int | None]:
    queries = {
        "reading": "SELECT count(*) AS n FROM events WHERE first_observed_at >= %(s)s AND NOT is_historical_replay",
        "web_search": "SELECT count(*) AS n FROM verification_requests WHERE origin='exa_discovery' AND created_at >= %(s)s",
        "tenders": "SELECT count(*) AS n FROM procurement_notices WHERE summary IS NOT NULL AND summary_at >= %(s)s",
        "company": "SELECT (SELECT count(*) FROM account_profiles WHERE status='done' AND finished_at >= %(s)s) + (SELECT count(*) FROM account_briefs WHERE status='done' AND finished_at >= %(s)s) AS n",
        "person": "SELECT count(*) AS n FROM person_profiles WHERE status='done' AND finished_at >= %(s)s",
        "find_people": "SELECT count(*) AS n FROM people_candidates WHERE created_at >= %(s)s",
        "fit": "SELECT coalesce(sum((result->>'assessed')::int),0) AS n FROM jobs WHERE kind='assess_fit' AND status='completed' AND updated_at >= %(s)s",
        "emails": "SELECT count(*) AS n FROM campaign_drafts WHERE status <> 'failed' AND finished_at >= %(s)s",
        "matching": "SELECT count(*) AS n FROM identity_reviews WHERE resolved_by='auto' AND resolved_at >= %(s)s",
    }
    out: dict[str, int | None] = {}
    for k, sql in queries.items():
        try:
            with conn.transaction():
                r = conn.execute(sql, {"s": start}).fetchone()
            out[k] = int(r["n"] or 0) if r else 0
        except Exception:  # noqa: BLE001 - a table or column this deployment lacks: just no figure
            out[k] = None
    return out


def usage_insights(conn: Conn, settings: Any, now: datetime | None = None) -> dict[str, Any]:
    from .. import usage
    now = now or datetime.now(timezone.utc)
    period = now.strftime("%Y-%m")
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    days_in = calendar.monthrange(now.year, now.month)[1]
    elapsed = (now - start).total_seconds() / 86400
    days_left = max(days_in - elapsed, 0.0)
    budgets = {r["provider"]: r for r in qa(conn, "SELECT provider, cap, reserved, spent FROM provider_budgets WHERE kind='monthly' AND period=%s", (period,))}
    daily_rows = qa(conn, "SELECT (at AT TIME ZONE 'UTC')::date AS day, provider, coalesce(sum(cost_usd),0) AS usd, coalesce(sum(units),0) AS units, count(*) AS calls "
                          "FROM provider_usage WHERE period=%s GROUP BY 1,2", (period,))
    by_day: dict[str, dict[str, Any]] = {}
    for i in range(int(now.day)):
        d = (start + timedelta(days=i)).date().isoformat()
        by_day[d] = {"day": d, "ai_usd": 0.0, "exa_usd": 0.0, "ai_tokens": 0, "searches": 0, "calls": 0}
    for r in daily_rows:
        d = by_day.setdefault(r["day"].isoformat(), {"day": r["day"].isoformat(), "ai_usd": 0.0, "exa_usd": 0.0, "ai_tokens": 0, "searches": 0, "calls": 0})
        if r["provider"] == "anthropic":
            d["ai_usd"] += float(r["usd"]); d["ai_tokens"] += int(r["units"])
        elif r["provider"] == "exa":
            d["exa_usd"] += float(r["usd"]); d["searches"] += int(r["units"])
        d["calls"] += int(r["calls"])
    daily = sorted(by_day.values(), key=lambda x: x["day"])
    last7 = [d for d in daily if d["day"] >= (now - timedelta(days=7)).date().isoformat()]
    span7 = max(min(7.0, elapsed), 0.5)

    def gauge(provider: str, unit: str) -> dict[str, Any]:
        b = budgets.get(provider)
        cap = float(b["cap"]) if b else 0.0
        used = float(b["spent"]) + float(b["reserved"]) if b else 0.0
        key_units, key_usd = ("ai_tokens", "ai_usd") if provider == "anthropic" else ("exa_usd", "exa_usd")
        rate = sum(float(d[key_units]) for d in last7) / span7
        rate_usd = sum(float(d[key_usd]) for d in last7) / span7
        month_usd = sum(float(d[key_usd]) for d in daily)
        forecast = used + rate * days_left
        out = {"provider": provider, "unit": unit, "cap": cap, "used": used, "spent_usd": round(month_usd, 4), "rate_per_day": round(rate, 4),
               "rate_usd_per_day": round(rate_usd, 4), "forecast": round(forecast, 4), "forecast_usd": round(month_usd + rate_usd * days_left, 4),
               "will_exceed": bool(cap > 0 and forecast > cap), "runs_out_on": None, "pct": round(used / cap * 100, 1) if cap > 0 else None}
        if cap > 0 and rate > 0 and forecast > cap:
            out["runs_out_on"] = (now + timedelta(days=max(cap - used, 0) / rate)).date().isoformat()
        return out
    ai, exa = gauge("anthropic", "tokens"), gauge("exa", "usd")
    if ai["cap"] > 0 and ai["used"] > 0 and ai["spent_usd"] > 0:
        ai["cap_usd_estimate"] = round(ai["cap"] * ai["spent_usd"] / max(sum(d["ai_tokens"] for d in daily), 1), 2)
    elif ai["cap"] > 0 and settings.anthropic_price_in_per_mtok is not None:
        blended = (0.85 * settings.anthropic_price_in_per_mtok + 0.15 * settings.anthropic_price_out_per_mtok) / 1_000_000
        ai["cap_usd_estimate"] = round(ai["cap"] * blended, 2)
    else:
        ai["cap_usd_estimate"] = None

    ops = qa(conn, "SELECT provider, operation, count(*) AS calls, count(*) FILTER (WHERE status<>'ok') AS failed, coalesce(sum(cost_usd),0) AS usd, "
                   "coalesce(sum(units),0) AS units FROM provider_usage WHERE period=%s GROUP BY 1,2", (period,))
    useful = _useful(conn, start)
    purposes: dict[str, dict[str, Any]] = {}
    for r in ops:
        key, label = PURPOSES.get(r["operation"], ("other", "Other"))
        if r["provider"] == "exa" and r["operation"] == "search":
            key, label = "web_search", "Web search"
        p = purposes.setdefault(key, {"key": key, "label": label, "calls": 0, "failed": 0, "usd": 0.0, "ai_usd": 0.0, "exa_usd": 0.0, "tokens": 0, "searches": 0})
        p["calls"] += int(r["calls"]); p["failed"] += int(r["failed"]); p["usd"] += float(r["usd"])
        if r["provider"] == "anthropic":
            p["ai_usd"] += float(r["usd"]); p["tokens"] += int(r["units"])
        else:
            p["exa_usd"] += float(r["usd"]); p["searches"] += int(r["units"])
    for k, p in purposes.items():
        n = useful.get(k)
        p["useful"] = n
        p["useful_label"] = USEFUL_LABEL.get(k)
        p["cost_per_useful"] = round(p["usd"] / n, 4) if n and p["usd"] > 0 else None
    total = sum(p["usd"] for p in purposes.values())
    prev_period = (start - timedelta(days=1)).strftime("%Y-%m")
    prev = q1(conn, "SELECT coalesce(sum(cost_usd),0) AS usd, count(*) AS calls FROM provider_usage WHERE period=%s", (prev_period,)) or {"usd": 0, "calls": 0}
    return {"period": period, "day": now.day, "days_in_month": days_in, "days_left": round(days_left, 2), "ai": ai, "exa": exa, "daily": daily,
            "purposes": sorted(purposes.values(), key=lambda p: -p["usd"]), "total_usd": round(total, 4),
            "forecast_usd": round(ai["forecast_usd"] + exa["forecast_usd"], 4), "previous": {"period": prev_period, "usd": float(prev["usd"]), "calls": int(prev["calls"])},
            "notes": ["Web search costs are what Exa reports on each answer.", "AI costs are tokens at list price for the model used.",
                      "The forecast adds the average daily spend of the last 7 days for each day left in the month."]}


# ====================================================================== company matches

def identity_overview(conn: Conn) -> dict[str, Any]:
    counts = q1(conn, "SELECT count(*) FILTER (WHERE status='open') AS open, "
                      "count(*) FILTER (WHERE status='open' AND (auto_hold OR auto_attempts > 0)) AS needs_person, "
                      "count(*) FILTER (WHERE status='open' AND NOT auto_hold AND auto_attempts = 0) AS waiting, "
                      "count(*) FILTER (WHERE resolved_by='auto') AS auto_total, "
                      "count(*) FILTER (WHERE resolved_by='auto' AND resolved_at > now() - interval '7 days') AS auto_week, "
                      "count(*) FILTER (WHERE resolved_by='auto' AND status='resolved_link') AS auto_linked, "
                      "count(*) FILTER (WHERE resolved_by='auto' AND status='resolved_new') AS auto_new, "
                      "count(*) FILTER (WHERE undone_at IS NOT NULL) AS undone FROM identity_reviews") or {}
    need = qa(conn, "SELECT r.id, r.raw_company_name, r.reason, r.candidate_org_ids, r.features, r.evidence, r.auto_checked_at, r.auto_hold, r.undone_by, r.created_at, "
                    "l.sheet_title, l.row_number FROM identity_reviews r LEFT JOIN account_source_links l ON l.id=r.source_link_id "
                    "WHERE r.status='open' AND (r.auto_hold OR r.auto_attempts > 0) ORDER BY r.auto_hold DESC, r.auto_checked_at DESC NULLS LAST LIMIT 150")
    waiting = qa(conn, "SELECT r.id, r.raw_company_name, r.reason, r.candidate_org_ids, r.features, r.evidence, r.auto_checked_at, r.auto_hold, r.undone_by, r.created_at, "
                       "l.sheet_title, l.row_number FROM identity_reviews r LEFT JOIN account_source_links l ON l.id=r.source_link_id "
                       "WHERE r.status='open' AND NOT r.auto_hold AND r.auto_attempts = 0 ORDER BY r.created_at LIMIT 50")
    ids = {str(x) for r in need + waiting for x in r["candidate_org_ids"]}
    for r in need + waiting:
        ai = ((r["evidence"] or {}).get("auto") or {}).get("ai") or {}
        if ai.get("candidate_id"):
            ids.add(str(ai["candidate_id"]))
    orgs = {str(o["id"]): dict(o, id=str(o["id"])) for o in qa(conn, "SELECT id, display_name, country_code, canonical_domain FROM organizations WHERE id::text = ANY(%s)", (list(ids),))} if ids else {}

    def shape(r: dict[str, Any]) -> dict[str, Any]:
        ev = r["evidence"] or {}
        web = ev.get("web") or {}
        return {"id": str(r["id"]), "raw_company_name": r["raw_company_name"], "reason": r["reason"], "sheet_title": r["sheet_title"], "row_number": r["row_number"],
                "country": (r["features"] or {}).get("country"), "website": (r["features"] or {}).get("domain"),
                "candidates": [orgs[str(x)] for x in r["candidate_org_ids"] if str(x) in orgs], "candidate_org_ids": [str(x) for x in r["candidate_org_ids"]],
                "auto": ev.get("auto"), "web": {"query": web.get("query"), "official": web.get("official"), "results": (web.get("results") or [])[:3]} if web else None,
                "undone": ev.get("undone"), "auto_hold": r["auto_hold"], "checked_at": r["auto_checked_at"], "created_at": r["created_at"]}
    recent = qa(conn, "SELECT r.id, r.raw_company_name, r.status, r.resolved_org_id, r.decision_reason, r.confidence, r.evidence, r.resolved_at, r.reviewer, "
                      "o.display_name AS org_name, o.country_code AS org_country, o.canonical_domain AS org_domain, l.sheet_title, l.row_number "
                      "FROM identity_reviews r LEFT JOIN organizations o ON o.id=r.resolved_org_id LEFT JOIN account_source_links l ON l.id=r.source_link_id "
                      "WHERE r.resolved_by='auto' ORDER BY r.resolved_at DESC LIMIT 60")
    last = q1(conn, "SELECT status, updated_at, result, last_error FROM jobs WHERE kind='identity_autoresolve' ORDER BY created_at DESC LIMIT 1")
    queued = q1(conn, "SELECT 1 FROM jobs WHERE kind='identity_autoresolve' AND status IN ('scheduled','claimed','retryable_failed')")
    from ..research.exa_search import worker_can
    can = _worker_can(conn) or worker_can(conn)
    return {"counts": counts, "needs_person": [shape(r) for r in need], "waiting": [shape(r) for r in waiting],
            "recent": [{"id": str(r["id"]), "raw_company_name": r["raw_company_name"], "decision": "link" if r["status"] == "resolved_link" else "new",
                        "organization_id": str(r["resolved_org_id"]) if r["resolved_org_id"] else None, "org_name": r["org_name"], "org_country": r["org_country"],
                        "org_domain": r["org_domain"], "reason": r["decision_reason"], "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
                        "evidence_url": (r["evidence"] or {}).get("evidence_url"), "rule": ((r["evidence"] or {}).get("auto") or {}).get("rule"),
                        "resolved_at": r["resolved_at"], "sheet_title": r["sheet_title"], "row_number": r["row_number"]} for r in recent],
            "last_run": {"status": last["status"], "at": last["updated_at"], "result": last["result"], "error": last["last_error"]} if last else None,
            "queued": bool(queued), "can": {"web_search": bool(can.get("exa")), "ai": bool(can.get("ai"))},
            "min_confidence": 0.85, "per_run": 40}


def _worker_can(conn: Conn) -> dict[str, bool]:
    """The background worker runs the matcher; its heartbeat says whether it has the Exa and AI keys."""
    row = q1(conn, "SELECT detail FROM system_heartbeats WHERE component='worker' AND at > now() - interval '15 minutes'")
    can = (row or {}).get("detail", {}).get("can") if row else None
    return can if isinstance(can, dict) else {}


# ====================================================================== sources

def sources_overview(conn: Conn) -> dict[str, Any]:
    from ..connections import LABELS, _notes, directory
    from ..sources.autovalidate import BY, person_paused, policy
    rows = [r for r in directory(conn) if not re.search(r"pipedrive", f"{r['source_id']} {r['name']}", re.I)]
    raw = {r["source_id"]: r for r in qa(conn, "SELECT source_id, status, collection_method, auth_mode, credential_ref, licence_notes, cost_profile, config, canonical_domain, "
                                               "certification->>'technical_owner' AS activated_by, reviewed_at FROM sources")}
    notes, paused = _notes(), person_paused(conn)
    counts: dict[str, int] = {}
    groups: dict[str, list[dict[str, Any]]] = {}
    auto_on = []
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
        s = raw.get(r["source_id"]) or {}
        v = (s.get("config") or {}).get("auto") or (policy(s, notes.get(r["source_id"]), paused_by_person=r["source_id"] in paused) if s else {})
        if s.get("status") == "active" and s.get("activated_by") == BY:
            auto_on.append({"source_id": r["source_id"], "name": r["name"], "at": s.get("reviewed_at")})
        if r["state"] in ("live", "built_in", "per_account", "not_built", "deferred"):
            continue
        groups.setdefault(r["state"], []).append({"source_id": r["source_id"], "name": r["name"], "family": r["family"], "state": r["state"], "code": v.get("code"), "param": v.get("param"),
                                                 "reason": v.get("reason") or r.get("what_to_do"),
                                                 "todo": v.get("todo"), "where": r.get("where_to_get"), "test": v.get("test"), "checked_at": v.get("at"),
                                                 "automatic": bool(v.get("eligible"))})
    last = q1(conn, "SELECT status, updated_at, result, last_error FROM jobs WHERE kind='sources_validate_daily' ORDER BY created_at DESC LIMIT 1")
    order = ["needs_credential", "needs_terms", "blocked_by_site", "needs_address", "needs_retest", "paused", "testing"]
    return {"counts": counts, "labels": LABELS, "total": len(rows),
            "groups": [{"state": k, "label": LABELS.get(k, k), "items": groups[k]} for k in order if k in groups] + [{"state": k, "label": LABELS.get(k, k), "items": v} for k, v in groups.items() if k not in order],
            "auto_on": sorted(auto_on, key=lambda x: str(x["at"] or ""), reverse=True)[:30],
            "last_run": {"status": last["status"], "at": last["updated_at"], "result": last["result"], "error": last["last_error"]} if last else None}


# ====================================================================== routes

def register(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    # ---- company matches
    @app.get("/api/admin/identity/overview")
    def identity_auto(request: Request, p: Principal = Depends(require("identity.manage"))):
        with tx(request) as conn:
            return identity_overview(conn)

    @app.post("/api/admin/identity/auto/run")
    def identity_auto_run(request: Request, p: Principal = Depends(require("identity.manage"))):
        from ..jobs.queue import enqueue
        if p.role != "admin":
            raise HTTPException(403, "Only an administrator can start automatic matching.")
        with tx(request) as conn:
            jid, created = enqueue(conn, "identity_autoresolve", {"limit": 40, "requested_by": p.email},
                                   idempotency_key=f"identity-auto:manual:{datetime.now(timezone.utc):%Y%m%d%H%M}", max_attempts=1, correlation_id="manual")
            audit(conn, actor=p.email, operation="identity_auto_requested", record_type="job", record_id=jid, detail={})
            return {"job_id": jid, "queued": created}

    @app.post("/api/admin/identity-reviews/{rid}/undo")
    def identity_undo(rid: str, body: UndoIn, request: Request, p: Principal = Depends(require("identity.manage"))):
        from ..identity.review import ReviewError, undo_decision
        with tx(request) as conn:
            try:
                return undo_decision(conn, rid, actor=p.email, note=body.note)
            except ReviewError as exc:
                raise HTTPException(exc.status, str(exc)) from exc

    # ---- sources
    @app.get("/api/admin/sources/auto")
    def sources_auto(request: Request, p: Principal = Depends(require("sources.read"))):
        with tx(request) as conn:
            return sources_overview(conn)

    @app.post("/api/admin/sources/auto/run")
    def sources_auto_run(request: Request, p: Principal = Depends(require("admin.sources"))):
        from ..jobs.queue import enqueue
        with tx(request) as conn:
            jid, created = enqueue(conn, "sources_validate_daily", {"by": "system:daily-source-check", "requested_by": p.email},
                                   idempotency_key=f"sources-daily:manual:{datetime.now(timezone.utc):%Y%m%d%H%M}", max_attempts=1, correlation_id="manual")
            audit(conn, actor=p.email, operation="sources_auto_check_requested", record_type="job", record_id=jid, detail={})
            return {"job_id": jid, "queued": created}

    # ---- failed jobs
    @app.get("/api/admin/failures")
    def failures(request: Request, p: Principal = Depends(require("admin.jobs"))):
        with tx(request) as conn:
            return failure_groups(conn)

    @app.post("/api/admin/failures/retry")
    def failures_retry(body: GroupIn, request: Request, p: Principal = Depends(require("admin.jobs"))):
        with tx(request) as conn:
            r = retry_group(conn, body.key, p.email)
            if not any(r.values()):
                raise HTTPException(404, "nothing left to retry in this group")
            return r

    @app.post("/api/admin/failures/dismiss")
    def failures_dismiss(body: GroupIn, request: Request, p: Principal = Depends(require("admin.jobs"))):
        with tx(request) as conn:
            r = dismiss_group(conn, body.key, p.email, body.note)
            if not r["dismissed"]:
                raise HTTPException(404, "nothing left to dismiss in this group")
            return r

    # ---- usage
    @app.get("/api/admin/usage/insights")
    def insights(request: Request, p: Principal = Depends(require("admin.costs"))):
        with tx(request) as conn:
            return usage_insights(conn, request.app.state.settings)


__all__ = ["register", "classify", "failure_groups", "retry_group", "dismiss_group", "usage_insights", "identity_overview", "sources_overview"]
