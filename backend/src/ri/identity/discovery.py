"""Companies discovered outside the master lists get a SEPARATE qualification workflow (master §2, spec 07 §10)."""
from __future__ import annotations

from ..db import Conn
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


CANDIDATE_EVENT_TYPES = ("programme_announced", "vendor_selected", "implementation_launched", "technology_leader_appointed", "funding_announced", "acquisition_announced",
                         "supplier_panel_opened", "procurement_published", "engineering_job_opened", "product_launch")


def promote_candidates(conn: Conn, limit: int = 40, tiers: tuple[str, ...] = ("core",)) -> dict[str, int]:
    """Proven events about companies that are not in our database become CANDIDATE accounts (private sector, main markets by default).
    They are not in the master list and wait for qualification. The relinking job then attaches their events, so signals and cards can form."""
    from ..db import qa
    made = skipped = 0
    rows = qa(conn, "SELECT e.id, e.subject_name_raw AS name, e.market_country AS country, e.event_type FROM events e "
                    "WHERE e.proven AND e.subject_org_id IS NULL AND e.lane='private' AND e.market_tier = ANY(%s) AND e.event_type = ANY(%s) "
                    "AND e.subject_name_raw IS NOT NULL AND coalesce(e.event_at, e.pub_at, e.first_observed_at) > now() - interval '120 days' "
                    "ORDER BY e.first_observed_at DESC LIMIT %s", (list(tiers), list(CANDIDATE_EVENT_TYPES), limit * 4))
    seen: set[str] = set()
    for r in rows:
        name = (r["name"] or "").strip()
        key = normalize_name(name)
        if len(name) < 3 or len(name) > 90 or not key or key in seen:
            skipped += 1
            continue
        seen.add(key)
        oid, how = register_discovered_company(conn, name=name, country=r["country"], domain=None, event_id=str(r["id"]))
        if how == "new_candidate_pending_qualification":
            conn.execute("UPDATE new_company_candidates SET icp_note=%s WHERE organization_id=%s", (f"Found through a proven {r['event_type'].replace('_', ' ')} event. Not yet qualified.", oid))
            made += 1
        else:
            skipped += 1
        if made >= limit:
            break
    return {"candidates_created": made, "skipped": skipped}
