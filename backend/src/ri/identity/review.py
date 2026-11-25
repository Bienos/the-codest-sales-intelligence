"""Applying a company-match decision (an identity review). ONE code path for a person's decision and an automatic one.

A decision says which company a workbook row's company name belongs to:

* ``link``  the row is one of the candidate companies;
* ``new``   the row is a company of its own (a new account is created, with the row's country and website);
* ``reject`` the row is ignored.

After a link or a new company, the row is reconciled again (``reapply_row``): its aliases, list fields, research and contacts land on the
company exactly as if the import had matched it in the first place. Before this, a decision only set the row's company, so a matched
row's contacts and research never arrived.

An automatic decision can be undone (``undo_decision``): the row is unlinked again, what the decision added from that row is removed, the
review goes back to a person and is never decided automatically again.
"""
from __future__ import annotations

from typing import Any

from ..audit import audit
from ..db import Conn, jb, q1, qa


class ReviewError(Exception):
    status = 409


class ReviewNotFound(ReviewError):
    status = 404


class ReviewInvalid(ReviewError):
    status = 422


STATUS = {"link": "resolved_link", "new": "resolved_new", "reject": "resolved_reject"}


def _contract_for(conn: Conn, st: dict[str, Any]):
    from ..sheets import contracts as C
    run = q1(conn, "SELECT workbook_key, stats FROM import_runs WHERE id=%s", (st["import_run_id"],))
    if not run:
        return None, None
    snap = q1(conn, "SELECT headers FROM sheet_schema_snapshots WHERE import_run_id=%s AND sheet_id=%s AND status='ok'", (st["import_run_id"], st["sheet_id"]))
    if not snap:
        return None, None
    layout = (run.get("stats") or {}).get("layout")
    if not layout:
        titles = [s["sheet_title"] for s in qa(conn, "SELECT sheet_title FROM sheet_schema_snapshots WHERE import_run_id=%s", (st["import_run_id"],))]
        layout = C.detect_layout(run["workbook_key"], titles)
    ct = next((c for c in C.contracts_for(run["workbook_key"], layout) if c.title == st["sheet_title"]), None)
    return ct, list(snap["headers"])


def _aliases(conn: Conn, org_id: str) -> set[str]:
    return {r["alias_norm"] for r in qa(conn, "SELECT alias_norm FROM organization_aliases WHERE organization_id=%s", (org_id,))}


def reapply_row(conn: Conn, link_id: str) -> dict[str, Any]:
    """Reconcile the latest staged copy of this row again, now that it has a company. Never raises: the decision stands either way."""
    from ..sheets.reconcile import Counters, reconcile_staged_row
    link = q1(conn, "SELECT * FROM account_source_links WHERE id=%s", (link_id,))
    if not link or not link["organization_id"]:
        return {"reapplied": False, "why": "row has no company"}
    st = q1(conn, "SELECT * FROM staging_sheet_rows WHERE workbook_id=%s AND sheet_id=%s AND row_identity=%s ORDER BY id DESC LIMIT 1",
            (link["source_file_id"], link["sheet_id"], link["row_identity"]))
    if not st:
        return {"reapplied": False, "why": "the imported row is no longer stored"}
    ct, headers = _contract_for(conn, st)
    if ct is None:
        return {"reapplied": False, "why": "the tab's layout is not known"}
    c = Counters()
    try:
        with conn.transaction():                  # savepoint: a row that cannot be applied leaves the decision itself intact
            conn.execute("UPDATE account_source_links SET imported_hash='' WHERE id=%s", (link_id,))   # otherwise the row reads as unchanged
            reconcile_staged_row(conn, ct, st, headers, c)
    except Exception as exc:  # noqa: BLE001
        return {"reapplied": False, "why": f"{type(exc).__name__}: {exc}"[:200]}
    return {"reapplied": True, "tab": ct.title, "contacts_added": c.contacts, "conflicts": c.conflicts}


def apply_decision(conn: Conn, rid: str, decision: str, *, actor: str, note: str | None = None, organization_id: str | None = None,
                   resolved_by: str = "person", reason: str | None = None, confidence: float | None = None, evidence: dict[str, Any] | None = None,
                   allow_new_candidate: bool = False, new_domain: str | None = None) -> dict[str, Any]:
    """Applies one decision. `allow_new_candidate` lets the automatic matcher link to a company it found by its website that was not on the
    review's candidate list (it is added to the list, so the record shows it). A person picks from the list."""
    if decision not in STATUS:
        raise ReviewInvalid("decision must be link, new or reject")
    r = q1(conn, "SELECT * FROM identity_reviews WHERE id::text=%s AND status='open' FOR UPDATE", (rid,))
    if not r:
        raise ReviewNotFound("review not found")
    cands = [str(x) for x in r["candidate_org_ids"]]
    feats = r["features"] or {}
    applied: dict[str, Any] = {}
    link_id = str(r["source_link_id"]) if r["source_link_id"] else None
    if decision == "link":
        if not organization_id:
            raise ReviewInvalid("organization_id must be one of the candidates")
        if organization_id not in cands:
            if not (allow_new_candidate and q1(conn, "SELECT 1 FROM organizations WHERE id::text=%s", (organization_id,))):
                raise ReviewInvalid("organization_id must be one of the candidates")
            conn.execute("UPDATE identity_reviews SET candidate_org_ids = array_append(candidate_org_ids, %s::uuid) WHERE id=%s", (organization_id, r["id"]))
            applied["candidate_added"] = organization_id
        org_id: str | None = organization_id
    elif decision == "new":
        country = feats.get("country") if isinstance(feats.get("country"), str) else None
        domain = new_domain or (feats.get("domain") if isinstance(feats.get("domain"), str) else None)
        org_id = str(conn.execute("INSERT INTO organizations(display_name, country_code, canonical_domain, in_master_list) VALUES (%s,%s,%s,true) RETURNING id",
                                  (r["raw_company_name"], country, domain)).fetchone()["id"])  # type: ignore[index]
        applied["org_created"] = org_id
    else:
        org_id = None
    if org_id:
        before = _aliases(conn, org_id)
        if link_id:
            conn.execute("UPDATE account_source_links SET organization_id=%s WHERE id=%s", (org_id, link_id))
            applied.update(reapply_row(conn, link_id))
        # the decision teaches the resolver: this spelling means this company, so the next import links it by itself
        from ..sheets.parsers import normalize_name, split_alternatives
        for alt in split_alternatives(r["raw_company_name"]) or [r["raw_company_name"]]:
            n = normalize_name(alt)
            if n:
                conn.execute("INSERT INTO organization_aliases(organization_id, alias, alias_norm, source_ref) VALUES (%s,%s,%s,'identity_review') ON CONFLICT DO NOTHING",
                             (org_id, alt, n))
        applied["aliases_added"] = sorted(_aliases(conn, org_id) - before)
    status = STATUS[decision]
    ev = dict(evidence or {})
    ev["applied"] = applied
    conn.execute("UPDATE identity_reviews SET status=%s, resolved_org_id=%s, reviewer=%s, reviewer_note=%s, resolved_at=now(), resolved_by=%s, "
                 "decision_reason=%s, confidence=%s, evidence = evidence || %s WHERE id=%s",
                 (status, org_id, actor, note, resolved_by, reason or note, confidence, jb(ev), r["id"]))
    audit(conn, actor=actor, operation="identity_review_resolved", record_type="identity_review", record_id=str(r["id"]),
          detail={"decision": decision, "note": note, "resolved_by": resolved_by, "organization_id": org_id, "confidence": confidence,
                  "reason": reason, "evidence_url": (evidence or {}).get("evidence_url"), "applied": applied})
    return {"status": status, "organization_id": org_id, "applied": applied}


# tables a user can attach to a company by hand: a company created by an automatic match is only removed on undo when none of them use it
_USER_TABLES = ("watchlist", "account_profiles", "account_briefs", "campaign_drafts", "people_searches", "outbound_enrolments", "signal_campaign_members",
                "contact_candidates", "account_endpoints", "account_context", "account_shares", "people_candidates")


def undo_decision(conn: Conn, rid: str, *, actor: str, note: str | None = None) -> dict[str, Any]:
    """Undo an automatic decision: the row is unlinked, what the decision brought in from the row is removed, the review is open again and
    stays with a person from now on (never decided automatically again)."""
    r = q1(conn, "SELECT * FROM identity_reviews WHERE id::text=%s AND status<>'open' FOR UPDATE", (rid,))
    if not r:
        raise ReviewNotFound("decision not found")
    if r["resolved_by"] != "auto":
        raise ReviewInvalid("only automatic decisions can be undone here")
    link_id = str(r["source_link_id"]) if r["source_link_id"] else None
    if link_id and q1(conn, "SELECT 1 FROM identity_reviews WHERE source_link_id=%s AND status='open'", (link_id,)):
        raise ReviewError("this row already has an open review")
    org = str(r["resolved_org_id"]) if r["resolved_org_id"] else None
    applied = (r["evidence"] or {}).get("applied") or {}
    done: dict[str, Any] = {}
    if link_id:
        done["research_removed"] = conn.execute("DELETE FROM legacy_account_research WHERE source_link_id=%s", (link_id,)).rowcount
        done["roles_removed"] = conn.execute("DELETE FROM employment WHERE source_link_id=%s", (link_id,)).rowcount
        conn.execute("DELETE FROM data_conflicts WHERE source_link_id=%s", (link_id,))
        conn.execute("UPDATE account_source_links SET organization_id=NULL WHERE id=%s", (link_id,))
    if org and applied.get("aliases_added"):
        # only the spellings this decision added (computed when it was applied); spellings the company already had stay
        conn.execute("DELETE FROM organization_aliases WHERE organization_id=%s AND alias_norm = ANY(%s)", (org, applied["aliases_added"]))
    conn.execute("UPDATE identity_reviews SET resolved_org_id=NULL WHERE id=%s", (r["id"],))      # the review no longer points at the company
    if org and applied.get("org_created") == org:
        done["company_removed"] = _drop_created_org(conn, org)
    conn.execute("UPDATE identity_reviews SET status='open', resolved_org_id=NULL, resolved_at=NULL, reviewer=NULL, reviewer_note=NULL, resolved_by=NULL, "
                 "auto_hold=true, undone_at=now(), undone_by=%s, evidence = evidence || %s WHERE id=%s",
                 (actor, jb({"undone": {"by": actor, "note": note, "was": r["status"], "organization_id": org, "reason": r["decision_reason"],
                                        "confidence": float(r["confidence"]) if r["confidence"] is not None else None, "removed": done}}), r["id"]))
    audit(conn, actor=actor, operation="identity_review_undone", record_type="identity_review", record_id=str(r["id"]),
          detail={"was": r["status"], "organization_id": org, "note": note, "removed": done})
    return {"status": "open", "removed": done}


def _drop_created_org(conn: Conn, org: str) -> bool:
    """Removes a company the automatic match created, only while nothing else uses it. Any reference (a finding, a star, research) keeps it."""
    if q1(conn, "SELECT 1 FROM account_source_links WHERE organization_id=%s", (org,)):
        return False
    for t in _USER_TABLES:
        if q1(conn, "SELECT to_regclass(%s) AS t", (t,))["t"] and q1(conn, f"SELECT 1 FROM {t} WHERE organization_id=%s LIMIT 1", (org,)):  # type: ignore[index]  # noqa: S608
            return False
    try:
        with conn.transaction():
            for t in ("account_scores", "account_source_coverage", "organization_aliases"):
                if q1(conn, "SELECT to_regclass(%s) AS t", (t,))["t"]:  # type: ignore[index]
                    conn.execute(f"DELETE FROM {t} WHERE organization_id=%s", (org,))      # noqa: S608 - fixed names
            conn.execute("DELETE FROM organizations WHERE id=%s", (org,))
    except Exception:  # noqa: BLE001 - something already refers to it (a finding, a signal): keep the company
        return False
    return True
