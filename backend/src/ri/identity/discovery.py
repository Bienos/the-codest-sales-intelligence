"""Companies discovered outside the master lists get a SEPARATE qualification workflow (master §2, spec 07 §10)."""
from __future__ import annotations

from ..db import Conn, jb, q1
from ..identity.resolver import resolve
from ..sheets.parsers import normalize_name


def register_discovered_company(conn: Conn, *, name: str, country: str | None, domain: str | None, event_id: str | None,
                                employees: int | None = None) -> tuple[str | None, str]:
    """Never creates a duplicate of a known account; ambiguous matches stay unresolved for review. New orgs are NOT in the master list."""
    d = resolve(conn, name=name, country=country, domain=domain, allow_create=True)
    if d.action == "auto_link":
        return d.org_id, "existing_account"
    if d.action != "create_new":
        return None, f"needs_identity_review:{d.reason}"
    row = conn.execute("INSERT INTO organizations(display_name, canonical_domain, country_code, employees_estimate, employees_basis, in_master_list) "
                       "VALUES (%s,%s,%s,%s,%s,false) RETURNING id", (name, domain, country, employees, "provider_estimate" if employees else None)).fetchone()
    oid = str(row["id"])  # type: ignore[index]
    conn.execute("INSERT INTO organization_aliases(organization_id, alias, alias_norm, source_ref) VALUES (%s,%s,%s,'discovery') ON CONFLICT DO NOTHING", (oid, name, normalize_name(name)))
    conn.execute("INSERT INTO new_company_candidates(organization_id, discovered_via_event_id) VALUES (%s,%s)", (oid, event_id))
    return oid, "new_candidate_pending_qualification"
