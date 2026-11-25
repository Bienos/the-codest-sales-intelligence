"""Daily automatic test of every source, and safe automatic switch-on.

Every day each source that is not internal and not paused by a person is tested from the server (the same test as "Test all sources":
``registry.validate_and_maybe_activate``). A source is switched on by itself only when ALL of these hold:

* it is waiting for validation (never one a person paused, and never one that is already live);
* it is a public feed (RSS/Atom), a public page or a public API that needs no key (``auth_mode = none``);
* the registry gives no reason for a person to look first: not blocked by the site, not a commercial service, no key or entitlement,
  and its notes do not ask for the reuse terms to be checked (``verify terms``, ``terms vary``, ``needs an entitlement``...);
* it has an address, and the server test passes (robots.txt allows us, the address answers, the content can be read).

Everything else stays off and says exactly why and what a person needs to do. The verdict is kept on the source as ``config.auto`` and shown
in Administration, Sources.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..audit import audit
from ..db import Conn, jb, qa

BY = "system:daily-source-check"

# notes in the registry that mean a person has to check something before the source may run
TERMS_CHECK = re.compile(r"verify (?:reuse )?terms|verify during qualification|terms vary|terms must|needs? (?:a |an )?(?:paid )?entitlement|commercial|"
                         r"paid (?:licen|subscription|plan)|licen[cs]e (?:is )?required|needs? (?:written )?permission|key required|requires? (?:a |an )?(?:key|login|account)|"
                         r"per-employer|per country", re.I)
NOT_SCHEDULED = {"per_account": "Runs per account: the account scan finds and reads it. Nothing to switch on here.",
                 "built_in": "Built in: used on demand. Nothing to switch on here.",
                 "not_built": "Not built: there is no connector for it.",
                 "deferred": "Switched off until you approve it."}
FAIL = {
    "policy_block": ("blocked_by_site", "Blocked by the site (robots.txt or its AI rules do not allow our reader).",
                     "Ask the publisher for written permission or a licensed feed. Do not work around it."),
    "not_found": ("needs_address", "Address not found: the page or feed answered 404.", "Find the current feed or newsroom address and save it below."),
    "access_denied": ("blocked_by_site", "The site refused our reader (HTTP 403, usually a bot check).", "Ask the publisher for a feed or permission; we try again each day."),
    "auth": ("needs_credential", "The service asked for a login or a key.", "Add the key on the worker, or ask the provider for access."),
    "timeout": ("needs_retest", "The site did not answer in time.", "Nothing to do now: it is tested again tomorrow and switches on by itself when it answers."),
    "transient_network": ("needs_retest", "The site could not be reached.", "Nothing to do now: it is tested again tomorrow."),
    "server_error": ("needs_retest", "The site answered with a server error.", "Nothing to do now: it is tested again tomorrow. If it keeps failing, find another address."),
    "rate_limited": ("needs_retest", "The site asked us to slow down.", "Nothing to do now: it is tested again tomorrow."),
    "schema_drift": ("needs_address", "The address does not give a feed or page we can read.", "Check the address: it may point to a page that moved or a feed that changed."),
}


def policy(src: dict[str, Any], note: dict[str, Any] | None, *, paused_by_person: bool = False) -> dict[str, Any]:
    """Whether a source may be switched on without a person, and if not, why and what a person must do. No network calls.
    A source a person paused stays paused. One paused by the registry because its shipped address changed is tested again like a new one."""
    note = note or {}
    cfg = src.get("config") or {}
    st = src["status"]
    if st in ("active", "degraded"):
        return {"eligible": False, "state": "live", "reason": "Live: read on its schedule.", "todo": None}
    if st == "paused" and paused_by_person:
        return {"eligible": False, "state": "paused", "reason": "Paused by a person.", "todo": "Switch it on again by hand when it should run. It is never switched on automatically."}
    if src["collection_method"] in ("internal", "search") or note.get("state") in NOT_SCHEDULED:
        state = note.get("state") if note.get("state") in NOT_SCHEDULED else "built_in"
        return {"eligible": False, "state": state, "reason": NOT_SCHEDULED[state], "todo": note.get("todo")}
    if note.get("state") == "blocked_by_site":
        return {"eligible": False, "state": "blocked_by_site", "reason": "Blocked by the site." + (" " + note["todo"] if note.get("todo") else ""), "todo": note.get("where")}
    notes = " ".join(str(x) for x in (src.get("licence_notes"), (src.get("cost_profile") or {}).get("note")) if x)
    if src.get("auth_mode") and src["auth_mode"] != "none":
        if note.get("state") == "needs_credential" and re.search(r"commercial|entitlement|paid", f"{notes} {note.get('todo', '')}", re.I):
            return {"eligible": False, "state": "needs_credential", "reason": "Commercial service: it needs a paid entitlement before any use.",
                    "todo": note.get("where") or "Agree a contract with the provider first."}
        env = src.get("credential_ref") or "the provider's key"
        return {"eligible": False, "state": "needs_credential", "reason": f"Needs an API key: add {env} on the worker.",
                "todo": (note.get("where") and f"Get it from: {note['where']}. Then switch the source on here.") or "Then switch the source on here."}
    if note.get("state") == "needs_credential":
        return {"eligible": False, "state": "needs_credential", "reason": note.get("todo") or "Needs a key or a contract.", "todo": note.get("where")}
    m = TERMS_CHECK.search(notes)
    if m:
        return {"eligible": False, "state": "needs_terms", "reason": f"Needs a person to check the reuse terms. The registry note says: “{src.get('licence_notes')}”.",
                "todo": "Read the provider's terms. If reuse is allowed, test it and switch it on below."}
    if src["collection_method"] in ("feed", "change_monitor") and not cfg.get("url"):
        return {"eligible": False, "state": "needs_address", "reason": "Address not found: no feed or page address is set.", "todo": "Find the feed or newsroom address and save it below."}
    if st not in ("pending_connector_validation", "paused"):
        return {"eligible": False, "state": "needs_retest", "reason": f"Marked {st.replace('_', ' ')} by an earlier check, so it is not switched on automatically.",
                "todo": "Test it below; if the test passes, switch it on by hand."}
    return {"eligible": True, "state": "testing", "reason": "Public and keyless: switched on by itself once the daily test passes.", "todo": None}


def _notes() -> dict[str, dict[str, Any]]:
    from ..connections import _notes as notes
    return notes()


def person_paused(conn: Conn) -> set[str]:
    """Sources whose latest switch-on or pause was a pause by a person (the audit trail says who did what)."""
    return {r["record_id"] for r in qa(conn, "SELECT DISTINCT ON (record_id) record_id, operation FROM audit_events WHERE record_type='source' "
                                             "AND operation IN ('pause_source','activate_source') ORDER BY record_id, id DESC") if r["operation"] == "pause_source"}


def verdict_after_test(pol: dict[str, Any], res: dict[str, Any]) -> dict[str, Any]:
    if res.get("result") == "activated":
        return {"eligible": True, "state": "live", "reason": "Switched on automatically: public, keyless, and it passed the server test.", "todo": None}
    if res.get("result") == "failed":
        state, reason, todo = FAIL.get(res.get("error_class") or "", ("needs_retest", "The test failed.", "It is tested again tomorrow."))
        detail = str(res.get("detail") or "")
        if res.get("error_class") == "policy_block" and "Content-Signal" in detail:
            reason = "Blocked by the site: its robots.txt says its pages may not be given to AI models."
        return {**pol, "state": state if pol["eligible"] or state == "blocked_by_site" else pol["state"], "reason": reason if pol["eligible"] or state == "blocked_by_site" else pol["reason"],
                "todo": todo if pol["eligible"] or state == "blocked_by_site" else pol["todo"], "test": {"ok": False, "detail": detail[:160], "error_class": res.get("error_class")}}
    if res.get("result") == "passed":
        return {**pol, "test": {"ok": True}}
    return {**pol, "test": {"ok": None, "detail": res.get("reason")}}


def run_daily(conn: Conn, *, by: str = BY, only: list[str] | None = None, tester=None) -> dict[str, Any]:
    """Tests every source that is not internal and not paused; switches on the safe ones that pass. Commits per source.
    `tester(conn, source_id) -> result` replaces the network test in tests."""
    from . import registry
    notes = _notes()
    paused = person_paused(conn)
    rows = qa(conn, "SELECT * FROM sources WHERE (%s::text[] IS NULL OR source_id = ANY(%s)) ORDER BY source_tier, source_id", (only, only))
    summary: dict[str, int] = {}
    switched: list[str] = []
    for src in rows:
        sid = src["source_id"]
        pol = policy(src, notes.get(sid), paused_by_person=sid in paused)
        v = pol
        if pol["state"] not in ("live", "paused", "built_in", "per_account", "not_built", "deferred", "needs_address") and src["collection_method"] not in ("internal", "search"):
            try:
                res = tester(conn, sid) if tester else registry.validate_and_maybe_activate(conn, sid, activate=False, by=by)
                if pol["eligible"] and res.get("result") == "passed":
                    cfg = src["config"] or {}
                    url = cfg.get("url") or (f"https://{src['canonical_domain']}" if src["canonical_domain"] else sid)
                    now = datetime.now(timezone.utc)
                    cert = {"provider_doc_url": url, "auth_entitlement_verified": True, "verified_endpoints": [url],
                            "data_licence": (src["licence_notes"] or "public feed, page or open API") + " (switched on automatically: public, keyless, no terms check asked for, passed the server test)",
                            "technical_owner": by, "last_tested_date": now.date().isoformat(),
                            "limits": f"polls every {src['poll_interval_s']}s; robots.txt honoured; no login or paywall bypass",
                            "rollback_path": "Pause the source in Administration, Sources; collected documents stay as evidence. A paused source is never switched on automatically."}
                    registry.activate_source(conn, sid, cert, by)
                    res = {**res, "result": "activated"}
                    switched.append(sid)
                v = verdict_after_test(pol, res)
            except Exception as exc:  # noqa: BLE001 - one bad source never stops the rest
                conn.rollback()
                v = {**pol, "test": {"ok": None, "detail": f"{type(exc).__name__}"}}
        v["at"] = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE sources SET config = coalesce(config, '{}'::jsonb) || %s::jsonb WHERE source_id=%s", (jb({"auto": v}), sid))
        conn.commit()
        summary[v["state"]] = summary.get(v["state"], 0) + 1
    if switched:
        audit(conn, actor=by, operation="sources_switched_on_automatically", record_type="source", record_id=None, detail={"sources": switched})
        conn.commit()
    return {"summary": summary, "switched_on": switched}


def schedule(conn: Conn, now: datetime) -> int:
    """Once a day."""
    from ..jobs.queue import enqueue
    _, created = enqueue(conn, "sources_validate_daily", {"by": BY}, idempotency_key=f"sources-daily:{now.date().isoformat()}", max_attempts=1, correlation_id="sched-sources")
    return int(created)
