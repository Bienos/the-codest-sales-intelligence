"""Reconcile immutable staging rows into canonical records, preserving source-row lineage (spec 04 §6, FR-001).

Never writes to the source sheets. Never overwrites CRM-owned facts. Idempotent: re-running changes nothing.
"""
from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import Any

from ..audit import audit
from ..db import Conn, jb, q1, qa
from ..identity.resolver import Decision, resolve
from . import contracts as C
from .parsers import normalize_domain, normalize_name, parse_country, parse_employees, split_alternatives

ICP_STRATEGIC, ICP_ENTERPRISE, ICP_SCALEUP = 5000, 1000, 500
ORG_RESEARCH_KINDS = {"master_account", "outreach_account", "priority_outreach", "new_account_research",
                      "buying_signal_log", "buyer_research_status", "contact_research_queue"}
CONTACT_KINDS = {"buyer_contact", "outreach_contact", "outreach_contact_compact", "master_contact"}
# Layout v2 ("Leads Master List") rows are stored under the v1 kinds the rest of the app already reads, with the v1 column
# names filled in from their v2 equivalents; the v2 columns are kept alongside, unchanged.
V2_RESEARCH_KIND = {"master_account_v2": "master_account", "master_signal": "buying_signal_log"}
V2_LEGACY_KEYS = {
    "master_account_v2": {"Segment": "Industry", "Signal Priority": "Account Priority", "Buying Signal Score": "Opportunity Score",
                          "Verified Buying Signal": "Strongest Buying Signal", "Potential Engineering Need": "Potential Opportunity",
                          "Procurement / Vendor Route": "Procurement Route", "Research Notes": "Coverage Gap"},
    "master_signal": {"Signal type": "Signal Category", "Verified event": "Verified Event", "Event date": "Event Date",
                      "Source publication date": "Publication Date", "Potential engineering relevance": "Commercial Implication",
                      "Evidence confidence": "Evidence Confidence", "Research date": "Last Verified", "Account reference": "Account ID"},
}


@dataclass
class Counters:
    rows: int = 0
    blank: int = 0
    linked_new_org: int = 0
    linked_existing: int = 0
    unchanged: int = 0
    reviews: int = 0
    contacts: int = 0
    conflicts: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


CORE_MARKETS = {"GB", "DE", "AT", "CH", "SE", "NO", "DK", "FI", "IS"}      # UK, DACH, Nordics
VENDOR_WORDS = re.compile(r"software house|outsourc|nearshore|offshore|it services|it consult|digital agency|dev shop", re.I)


def is_bank(segment: str | None) -> bool:
    return bool(segment and re.search(r"\bbank", segment, re.I))


def market_focus(country: str | None, segment: str | None) -> dict[str, str]:
    """Sales field guide: main markets are DACH, Nordics and the UK, plus Poland for banks; the US is a backup pool only."""
    c = (country or "").upper()
    if not c:
        return {"fit": "unknown", "label": "Country unknown"}
    if c in CORE_MARKETS:
        return {"fit": "core", "label": "Main market"}
    if c == "PL":
        return {"fit": "core", "label": "Main market (Poland, banks)"} if is_bank(segment) else {"fit": "outside", "label": "Poland is a bank-only market"}
    if c == "US":
        return {"fit": "backup", "label": "Backup pool only (US)"}
    return {"fit": "outside", "label": "Outside the main markets"}


def icp_band(employees: int | None, uncertain: bool, segment: str | None = None) -> dict[str, Any]:
    """Internal ICP baseline (master §2 and the ICP map): banks 5,000+, enterprises 1,000+, scale-ups 500+. Smaller only by approved exception.
    Estimates near a threshold are flagged, never silently rounded."""
    if employees is None:
        return {"band": "unknown", "boundary_uncertain": False}
    if is_bank(segment) and employees < ICP_STRATEGIC:
        near_bank = bool(uncertain and abs(employees - ICP_STRATEGIC) <= max(25, 0.1 * ICP_STRATEGIC))
        return {"band": "below_threshold", "boundary_uncertain": near_bank, "reason": "bank below the 5,000 employee threshold"}
    if employees >= ICP_STRATEGIC:
        band = "strategic_5000_plus"
    elif employees >= ICP_ENTERPRISE:
        band = "enterprise_1000_plus"
    elif employees >= ICP_SCALEUP:
        band = "scaleup_500_999"
    else:
        band = "below_threshold"
    near = any(abs(employees - t) <= max(25, 0.1 * t) for t in (ICP_STRATEGIC, ICP_ENTERPRISE, ICP_SCALEUP))
    return {"band": band, "boundary_uncertain": bool(uncertain and near)}


def _row_dict(headers: list[str], raw: list[str]) -> dict[str, str]:
    return {h: (raw[i] if i < len(raw) else "") for i, h in enumerate(headers)}


def _create_org(conn: Conn, raw_name: str, country: str | None, domain: str | None, segment: str | None,
                emp: tuple[int | None, str, bool]) -> str:
    alts = split_alternatives(raw_name) or [raw_name]
    display = alts[0]
    value, basis, uncertain = emp
    row = conn.execute(
        "INSERT INTO organizations(display_name, canonical_domain, country_code, segment, employees_estimate, "
        "employees_basis, employees_uncertain, in_master_list) VALUES (%s,%s,%s,%s,%s,%s,%s,true) RETURNING id",
        (display, domain, country, segment or None, value, basis if value else None, uncertain)).fetchone()
    oid = str(row["id"])  # type: ignore[index]
    for alt in alts:
        n = normalize_name(alt)
        if n:
            conn.execute("INSERT INTO organization_aliases(organization_id, alias, alias_norm, source_ref) VALUES (%s,%s,%s,%s) "
                         "ON CONFLICT DO NOTHING", (oid, alt, n, "sheet"))
    return oid


def _enrich_org(conn: Conn, oid: str, link_id: str, country, domain, segment, emp, raw_name: str, c: Counters) -> None:
    """Fill gaps; record disagreements as data_conflicts instead of overwriting."""
    cur = q1(conn, "SELECT * FROM organizations WHERE id=%s", (oid,))
    assert cur
    sets, vals = [], []
    if domain and not cur["canonical_domain"]:
        sets.append("canonical_domain=%s"); vals.append(domain)
    elif domain and cur["canonical_domain"] and domain != cur["canonical_domain"]:
        _conflict(conn, oid, "canonical_domain", cur["canonical_domain"], domain, link_id, c)
    if country and not cur["country_code"]:
        sets.append("country_code=%s"); vals.append(country)
    if segment and not cur["segment"]:
        sets.append("segment=%s"); vals.append(segment)
    value, basis, uncertain = emp
    if value:
        if cur["employees_estimate"] is None or (cur["employees_basis"] == "provider_estimate" and basis == "sheet_recorded"):
            sets += ["employees_estimate=%s", "employees_basis=%s", "employees_uncertain=%s"]
            vals += [value, basis, uncertain]
        elif abs(value - cur["employees_estimate"]) > 0.2 * max(value, cur["employees_estimate"]):
            _conflict(conn, oid, "employees_estimate", str(cur["employees_estimate"]), str(value), link_id, c)
    for alt in split_alternatives(raw_name) or [raw_name]:
        n = normalize_name(alt)
        if n:
            conn.execute("INSERT INTO organization_aliases(organization_id, alias, alias_norm, source_ref) VALUES (%s,%s,%s,%s) "
                         "ON CONFLICT DO NOTHING", (oid, alt, n, "sheet"))
    if sets:
        conn.execute(f"UPDATE organizations SET {', '.join(sets)}, updated_at=now() WHERE id=%s", (*vals, oid))  # noqa: S608


def _conflict(conn: Conn, oid: str, field_: str, existing: str, incoming: str, link_id: str, c: Counters) -> None:
    cur = conn.execute(
        "INSERT INTO data_conflicts(organization_id, field, existing_value, incoming_value, source_link_id) "
        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id", (oid, field_, existing, incoming, link_id)).fetchone()
    if cur:
        c.conflicts += 1


def _upsert_link(conn: Conn, ct: C.SheetContract, st: dict[str, Any], oid: str | None, raw_name: str) -> tuple[str, bool, str | None]:
    """Returns (link_id, was_unchanged, existing_org_id). Identity (not row number) is the lineage key."""
    existing = q1(conn, "SELECT id, imported_hash, organization_id FROM account_source_links WHERE source_system=%s AND "
                        "source_file_id=%s AND sheet_id=%s AND row_identity=%s",
                  (C.SOURCE_SYSTEM, st["workbook_id"], st["sheet_id"], st["row_identity"]))
    if existing:
        unchanged = existing["imported_hash"] == st["row_hash"]
        conn.execute("UPDATE account_source_links SET row_number=%s, imported_hash=%s, observed_at=%s WHERE id=%s",
                     (st["row_number"], st["row_hash"], st["observed_at"], existing["id"]))
        if oid and not existing["organization_id"]:
            conn.execute("UPDATE account_source_links SET organization_id=%s WHERE id=%s", (oid, existing["id"]))
        return str(existing["id"]), unchanged, (str(existing["organization_id"]) if existing["organization_id"] else None)
    row = conn.execute(
        "INSERT INTO account_source_links(organization_id, source_system, source_file_id, sheet_id, sheet_title, row_identity, "
        "row_number, imported_hash, raw_company_name, observed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (oid, C.SOURCE_SYSTEM, st["workbook_id"], st["sheet_id"], ct.title, st["row_identity"], st["row_number"],
         st["row_hash"], raw_name, st["observed_at"])).fetchone()
    return str(row["id"]), False, None  # type: ignore[index]


def _open_review(conn: Conn, link_id: str, raw_name: str, d: Decision, c: Counters) -> None:
    cur = conn.execute(
        "INSERT INTO identity_reviews(source_link_id, raw_company_name, candidate_org_ids, reason, features) "
        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (source_link_id) WHERE status='open' DO NOTHING RETURNING id",
        (link_id, raw_name, d.candidates, d.reason, jb(d.features))).fetchone()
    if cur:
        c.reviews += 1


def reconcile_staged_row(conn: Conn, ct: C.SheetContract, st: dict[str, Any], headers: list[str], c: Counters) -> None:
    c.rows += 1
    row = _row_dict(headers, st["raw_values"])
    raw_name = row.get(ct.company, "").strip()
    if not raw_name:
        c.blank += 1
        return
    country = parse_country(row.get(ct.country)) if ct.country else None
    domain = normalize_domain(row.get(ct.website)) if ct.website else None
    segment = row.get(ct.segment, "").strip() if ct.segment else None
    emp = parse_employees(row.get(ct.employees)) if ct.employees else parse_employees(None)
    emp_t = (emp.value, emp.basis, emp.uncertain)

    # lineage first: a known source row keeps its canonical org (stable across edits/moves)
    known = q1(conn, "SELECT id, organization_id FROM account_source_links WHERE source_file_id=%s AND sheet_id=%s AND row_identity=%s",
               (st["workbook_id"], st["sheet_id"], st["row_identity"]))
    oid: str | None = str(known["organization_id"]) if known and known["organization_id"] else None
    if oid is None and ct.layout == "v2" and not ct.creates_orgs and row.get("Account ID", "").strip():
        # v2 contacts and signals name their account by Account ID: no fuzzy name matching needed
        hit = q1(conn, "SELECT id FROM organizations WHERE list_account_id=%s ORDER BY created_at LIMIT 1", (row["Account ID"].strip(),))
        oid = str(hit["id"]) if hit else None
    decision: Decision | None = None
    if oid is None and ct.kind == "master_account_v2" and row.get("Account ID", "").strip():
        hit = q1(conn, "SELECT id FROM organizations WHERE list_account_id=%s ORDER BY created_at LIMIT 1", (row["Account ID"].strip(),))
        oid = str(hit["id"]) if hit else None
    if oid is None and not (known and _has_open_review(conn, str(known["id"]))):
        decision = resolve(conn, name=raw_name, country=country, domain=domain, allow_create=ct.creates_orgs)
        if ct.kind == "master_account_v2":
            decision = _list_account_decision(conn, decision, row.get("Account ID", "").strip())
        if decision.action == "auto_link":
            oid = decision.org_id
        elif decision.action == "create_new":
            oid = _create_org(conn, raw_name, country, domain, segment, emp_t)
            c.linked_new_org += 1
    link_id, unchanged, _ = _upsert_link(conn, ct, st, oid, raw_name)
    if oid is None:
        if decision is not None:
            _open_review(conn, link_id, raw_name, decision, c)
        return
    if unchanged and known:
        c.unchanged += 1
        return
    if decision is None or decision.action != "create_new":
        c.linked_existing += 1
    if ct.creates_orgs:
        _enrich_org(conn, oid, link_id, country, domain, segment, emp_t, raw_name, c)
    if ct.kind == "master_account_v2":
        _set_list_fields(conn, oid, row)
    if ct.kind in V2_RESEARCH_KIND:
        original = {**{k: row.get(v, "") for k, v in V2_LEGACY_KEYS[ct.kind].items()}, **row}
        conn.execute(
            "INSERT INTO legacy_account_research(organization_id, source_link_id, kind, original, observed_at) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (source_link_id, kind) DO UPDATE SET original=EXCLUDED.original, observed_at=EXCLUDED.observed_at, organization_id=EXCLUDED.organization_id",
            (oid, link_id, V2_RESEARCH_KIND[ct.kind], jb(original), st["observed_at"]))
        if ct.kind == "master_signal":
            _attach_signal_source(conn, oid, row)
    elif ct.kind in ORG_RESEARCH_KINDS or ct.kind == "company_list":
        conn.execute(
            "INSERT INTO legacy_account_research(organization_id, source_link_id, kind, original, observed_at) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (source_link_id, kind) DO UPDATE SET original=EXCLUDED.original, observed_at=EXCLUDED.observed_at, organization_id=EXCLUDED.organization_id",
            (oid, link_id, ct.kind, jb(row), st["observed_at"]))
    if ct.kind in CONTACT_KINDS:
        _upsert_contact(conn, ct, row, oid, link_id, c)
    audit(conn, actor="system:sheet-reconcile", operation="reconcile_row", record_type="account_source_link", record_id=link_id,
          after={"org": oid, "hash": st["row_hash"]})


def _list_account_decision(conn: Conn, d: Decision, account_id: str) -> Decision:
    """The lead list is already de-duplicated and gives every account its own Account ID, so a list row is never held for a
    look-alike name ("Kreissparkasse Böblingen" is not "Kreissparkasse Waiblingen"): it links on an exact match and is
    otherwise a company of its own. An exact match that already carries a different Account ID is a different list account."""
    if d.action == "auto_link" and d.org_id:
        other = q1(conn, "SELECT list_account_id FROM organizations WHERE id=%s", (d.org_id,))
        if not (other and other["list_account_id"] and other["list_account_id"] != account_id):
            return d
    elif d.action == "create_new":
        return d
    return Decision("create_new", None, d.candidates, f"lead list account {account_id}: {d.reason or 'distinct list account'}", d.features)


def _score(v: str | None) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", str(v or ""))
    return float(m.group()) if m else None


def _set_list_fields(conn: Conn, oid: str, row: dict[str, str]) -> None:
    """The lead list's own judgement of the account (its ID, eligibility, priority and score). The list is the owner of these
    fields, so the latest import always wins."""
    conn.execute(
        "UPDATE organizations SET list_account_id=%s, list_eligibility=%s, list_priority=%s, list_score=%s, list_icp_segment=%s, "
        "list_region=%s, updated_at=now() WHERE id=%s",
        (row.get("Account ID", "").strip() or None, row.get("Eligibility", "").strip() or None, row.get("Account Priority", "").strip() or None,
         _score(row.get("Opportunity Score")), row.get("ICP Segment", "").strip() or None, row.get("Region", "").strip() or None, oid))


def _attach_signal_source(conn: Conn, oid: str, row: dict[str, str]) -> None:
    """Give the account's list row the source of its strongest signal (the accounts tab has no URL of its own)."""
    url = row.get("Source URL", "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return
    conn.execute(
        "UPDATE legacy_account_research SET original = original || jsonb_build_object('Primary Source URL', %s::text, "
        "'Signal Type', %s::text, 'Evidence Confidence', %s::text) "
        "WHERE organization_id=%s AND kind='master_account' AND original->>'Account ID' = %s "
        "AND coalesce(original->>'Primary Source URL', '') = '' "
        "AND (lower(original->>'Strongest Buying Signal') = lower(%s) OR coalesce(original->>'Strongest Buying Signal', '') = '')",
        (url, row.get("Signal Category", ""), row.get("Evidence Confidence", ""), oid, row.get("Account ID", "").strip(), row.get("Verified Event", "").strip()))


def _has_open_review(conn: Conn, link_id: str) -> bool:
    return bool(q1(conn, "SELECT 1 FROM identity_reviews WHERE source_link_id=%s AND status='open'", (link_id,)))


def _upsert_contact(conn: Conn, ct: C.SheetContract, row: dict[str, str], oid: str, link_id: str, c: Counters) -> None:
    name = row.get(ct.person or "", "").strip()
    if not name:
        return
    if ct.kind == "master_contact":
        _upsert_master_contact(conn, row, name, oid, link_id, c)
        return
    if ct.kind == "buyer_contact":
        title, lane, email, status = row.get("Current role"), row.get("Buyer lane"), row.get("Work email (unverified)"), "unverified"
        linkedin, evidence = row.get("LinkedIn Profile URL"), row.get("Evidence URL")
    elif ct.kind == "outreach_contact":
        title, lane, email = row.get("Current role (reported)"), row.get("Buyer lane"), row.get("Work email")
        status = row.get("Email Verification Status") or "unknown"   # preserved verbatim, never promoted (spec 04 §6)
        linkedin, evidence = row.get("LinkedIn"), row.get("Evidence link")
    else:
        title, lane, email, status = row.get("Role"), row.get("Buyer Lane"), row.get("Email"), "unknown"
        linkedin, evidence = row.get("LinkedIn"), row.get("Source")
    person = q1(conn, "SELECT p.id FROM people p JOIN employment e ON e.person_id=p.id WHERE e.organization_id=%s AND lower(p.full_name)=lower(%s) LIMIT 1",
                (oid, name))
    if person:
        pid = str(person["id"])
        if email and status:   # never downgrade/upgrade: keep first non-empty email; record status only if still unknown
            conn.execute("UPDATE people SET work_email=COALESCE(work_email,%s), email_verification_status=CASE WHEN email_verification_status='unknown' THEN %s ELSE email_verification_status END WHERE id=%s",
                         (email or None, status, pid))
    else:
        pid = str(conn.execute("INSERT INTO people(full_name, work_email, email_verification_status, linkedin_url) VALUES (%s,%s,%s,%s) RETURNING id",
                               (name, email or None, status, (linkedin or None) if (linkedin or "").startswith("http") else None)).fetchone()["id"])  # type: ignore[index]
        c.contacts += 1
    exists = q1(conn, "SELECT 1 FROM employment WHERE person_id=%s AND organization_id=%s AND coalesce(title,'')=coalesce(%s,'')", (pid, oid, title or None))
    if not exists:
        conn.execute("INSERT INTO employment(person_id, organization_id, title, buyer_lane, evidence_url, source_link_id) VALUES (%s,%s,%s,%s,%s,%s)",
                     (pid, oid, title or None, lane or None, evidence if (evidence or "").startswith("http") else None, link_id))


def _upsert_master_contact(conn: Conn, row: dict[str, str], name: str, oid: str, link_id: str, c: Counters) -> None:
    """Layout v2 contact: matched by work email first (the same person may sit in several tabs or systems), then by name at the
    account. The list's email and its verification status are taken as written; an address already on file is never replaced."""
    from ..people import record_source

    email = row.get("Work Email", "").strip()
    email = email if "@" in email else ""
    status = row.get("Email Verification Status", "").strip() or ("Unverified" if email else "Missing")
    linkedin = row.get("LinkedIn URL", "").strip()
    linkedin = linkedin if linkedin.lower().startswith(("http://", "https://")) else ""
    title = row.get("Job Title", "").strip() or None
    lane = row.get("Persona Category", "").strip() or None
    evidence = next((u for u in re.findall(r"https?://\S+", row.get("Verification Source", "")) if u), None)
    person = q1(conn, "SELECT id, work_email, email_verification_status FROM people WHERE erased_at IS NULL AND lower(work_email)=lower(%s) LIMIT 1",
                (email,)) if email else None
    if not person:
        person = q1(conn, "SELECT p.id, p.work_email, p.email_verification_status FROM people p JOIN employment e ON e.person_id=p.id "
                          "WHERE e.organization_id=%s AND lower(p.full_name)=lower(%s) AND p.erased_at IS NULL LIMIT 1", (oid, name))
    if person:
        pid = str(person["id"])
        current = (person["work_email"] or "").lower()
        if email and not current:
            conn.execute("UPDATE people SET work_email=%s, email_verification_status=%s WHERE id=%s", (email, status, pid))
        elif email and current == email.lower() and status == "Verified" and person["email_verification_status"] != "Verified":
            conn.execute("UPDATE people SET email_verification_status='Verified' WHERE id=%s", (pid,))
        if linkedin:
            conn.execute("UPDATE people SET linkedin_url=%s WHERE id=%s AND linkedin_url IS NULL", (linkedin, pid))
    else:
        pid = str(conn.execute("INSERT INTO people(full_name, work_email, email_verification_status, linkedin_url) VALUES (%s,%s,%s,%s) RETURNING id",
                               (name, email or None, status, linkedin or None)).fetchone()["id"])  # type: ignore[index]
        c.contacts += 1
    exists = q1(conn, "SELECT 1 FROM employment WHERE person_id=%s AND organization_id=%s AND coalesce(title,'')=coalesce(%s,'')", (pid, oid, title))
    if not exists:
        conn.execute("INSERT INTO employment(person_id, organization_id, title, buyer_lane, evidence_url, source_link_id) VALUES (%s,%s,%s,%s,%s,%s)",
                     (pid, oid, title, lane, evidence, link_id))
    record_source(conn, pid, "sheets", row.get("Contact ID", "").strip(), {
        "seniority": row.get("Seniority", ""), "committee_role": row.get("Buying Committee Role", ""), "priority": row.get("Contact Priority", ""),
        "persona": row.get("Persona Category", ""), "angle": row.get("Suggested Outreach Angle", ""), "signal": row.get("Relevant Buying Signal", ""),
        "eligibility": row.get("Eligibility", "")})


def reconcile_run(conn: Conn, run_id: str, workbook: str, layout: str | None = None) -> Counters:
    c = Counters()
    if layout is None:
        titles = [s["sheet_title"] for s in qa(conn, "SELECT sheet_title FROM sheet_schema_snapshots WHERE import_run_id=%s", (run_id,))]
        layout = C.detect_layout(workbook, titles)
    cts = {ct.title: ct for ct in C.contracts_for(workbook, layout)}
    order = {t: ct.order for t, ct in cts.items()}
    snaps = qa(conn, "SELECT * FROM sheet_schema_snapshots WHERE import_run_id=%s AND status='ok'", (run_id,))
    for snap in sorted(snaps, key=lambda s: order.get(s["sheet_title"], 99)):
        ct = cts[snap["sheet_title"]]
        for st in conn.execute("SELECT * FROM staging_sheet_rows WHERE import_run_id=%s AND sheet_id=%s ORDER BY row_number",
                               (run_id, snap["sheet_id"])):
            try:
                with conn.transaction():          # savepoint: one bad row cannot poison the batch
                    reconcile_staged_row(conn, ct, st, list(snap["headers"]), c)
            except Exception as exc:  # noqa: BLE001
                c.errors.append(f"{ct.title}#{st['row_number']}: {type(exc).__name__}: {exc}")
    return c
