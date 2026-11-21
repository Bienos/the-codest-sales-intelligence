"""Role-based access + row scoping enforced IN SQL (spec 11 §3), never by hiding UI buttons.

BDM: accounts they own in Pipedrive + unowned accounts + explicit shares. Manager: their team's accounts + unowned.
Analyst/Admin: all business records (analyst: no CRM notes / no contact PII unless granted). Marketing: approved collateral only.
Compliance: audit scope only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PERMS: dict[str, set[str]] = {
    "bdm": {"accounts.read", "today.read", "hypothesis.review", "research.request", "draft.create", "crm.propose", "contacts.read", "explorer.read", "approvals.read_own", "approvals.decide"},
    "sales_manager": {"accounts.read", "today.read", "hypothesis.review", "research.request", "draft.create", "crm.propose", "contacts.read", "explorer.read", "approvals.read_all",
                      "approvals.decide", "team.read", "ownership.reprioritise", "approvals.spend"},
    "research_analyst": {"accounts.read", "today.read", "hypothesis.review", "research.request", "explorer.read", "identity.manage", "claims.validate", "initiatives.review", "sources.read"},
    "marketing": {"proof.manage"},
    "delivery_expert": {"accounts.read", "hypothesis.read", "delivery.validate", "explorer.read"},
    "admin": {"accounts.read", "today.read", "explorer.read", "research.request", "admin.sources", "admin.jobs", "admin.rules", "admin.costs", "admin.users", "admin.identity", "audit.read", "sources.read", "identity.manage"},
    "compliance": {"audit.read", "privacy.manage", "admin.costs"},
}


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    role: str
    team: str
    pipedrive_user_id: int | None
    perms: frozenset[str] = field(default_factory=frozenset)

    def can(self, perm: str) -> bool:
        return perm in self.perms


def principal_from_row(u: dict[str, Any]) -> Principal:
    return Principal(str(u["id"]), u["email"], u["role"], u["team"], u["pipedrive_user_id"], frozenset(PERMS.get(u["role"], set())))


def org_scope_sql(p: Principal, org_col: str = "o.id") -> tuple[str, list[Any]]:
    """WHERE fragment restricting `org_col` to accounts the principal may see. Applied to every account-bearing query."""
    if p.role in {"research_analyst", "admin", "delivery_expert"}:
        return "TRUE", []
    if p.role == "compliance" or p.role == "marketing":
        return "FALSE", []
    own_or_open = (f"(NOT EXISTS (SELECT 1 FROM crm_organizations cs WHERE cs.organization_id={org_col} AND cs.link_status='linked' AND cs.owner_id IS NOT NULL) "
                   f"OR EXISTS (SELECT 1 FROM account_shares sh WHERE sh.organization_id={org_col} AND sh.user_id=%s)")
    if p.role == "sales_manager":
        return (own_or_open + f" OR EXISTS (SELECT 1 FROM crm_organizations cm JOIN users tu ON tu.pipedrive_user_id=cm.owner_id WHERE cm.organization_id={org_col} AND tu.team=%s))",
                [p.user_id, p.team])
    return (own_or_open + f" OR EXISTS (SELECT 1 FROM crm_organizations cm WHERE cm.organization_id={org_col} AND cm.owner_id=%s))",
            [p.user_id, p.pipedrive_user_id])
