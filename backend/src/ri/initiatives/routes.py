"""Route hypotheses (spec 05 §7, 07 §3): ONE initiative -> separate buyer-direct / vendor / prime routes.

They are never summed into three 'confirmed opportunities'. Route status comes from scoped evidence + scoped negatives.
"""
from __future__ import annotations

from ..db import Conn, q1, qa


def derive_routes(conn: Conn, initiative_id: str) -> list[str]:
    ini = q1(conn, "SELECT * FROM initiatives WHERE id=%s", (initiative_id,))
    assert ini
    owner = q1(conn, "SELECT display_name FROM organizations WHERE id=%s", (ini["owner_org_id"],))
    edges = qa(conn, "SELECT * FROM supplier_edges WHERE initiative_id=%s", (initiative_id,))
    ids: list[str] = []

    def upsert(route_type: str, name: str, org_id, status: str, restriction: str | None) -> None:
        row = conn.execute(
            "INSERT INTO procurement_routes(initiative_id, route_type, counterparty_org_id, counterparty_name, access_status, restriction) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (initiative_id, route_type, counterparty_name) DO UPDATE SET "
            "restriction=EXCLUDED.restriction, updated_at=now(), "
            "access_status=CASE WHEN procurement_routes.access_status IN ('verified') THEN procurement_routes.access_status ELSE EXCLUDED.access_status END "
            "RETURNING id", (initiative_id, route_type, org_id, name, status, restriction)).fetchone()
        ids.append(str(row["id"]))  # type: ignore[index]

    # buyer-direct: owner entity only. Blocked by scoped negatives on THIS entity; restricted if a prime owns the scope.
    status, why = "unknown", None
    blocks = qa(conn, "SELECT id, rule_id, effect FROM negative_findings WHERE scope_type='route' AND scope_id=%s AND status='active'",
                (f"{ini['owner_org_id']}:direct",))
    prime_dev = [e for e in edges if e["supplier_role"] in {"si", "prime"} and e["relation"] in {"selected", "contracted"} and "development" in (e["scope_text"] or "")]
    if blocks:
        status, why = "blocked", f"scoped negative {blocks[0]['rule_id']} ({blocks[0]['effect']}) on the direct route"
    elif prime_dev:
        status, why = "restricted", f"{prime_dev[0]['supplier_name']} holds stated development scope; separable workstreams unconfirmed"
    upsert("buyer_direct", owner["display_name"], ini["owner_org_id"], status, why)
    if blocks:
        conn.execute("UPDATE procurement_routes SET blocked_by_negative_id=%s WHERE initiative_id=%s AND route_type='buyer_direct'", (blocks[0]["id"], initiative_id))
    for e in edges:
        if e["supplier_role"] in {"vendor", "technology_provider"}:
            upsert("vendor_implementation", e["supplier_name"], e["supplier_org_id"], "unknown",
                   "vendor-owned rollout not established; subcontract/implementation-partner eligibility unverified")
        elif e["supplier_role"] in {"si", "prime"}:
            upsert("prime_subcontract", e["supplier_name"], e["supplier_org_id"], "unknown",
                   "prime subcontracting/specialist-partner programme and eligibility unverified")
    return ids


def route_summary(conn: Conn, initiative_id: str) -> list[dict]:
    return qa(conn, "SELECT route_type, counterparty_name, access_status, restriction, portal_exists, procurement_open FROM procurement_routes "
                    "WHERE initiative_id=%s ORDER BY route_type, counterparty_name", (initiative_id,))
