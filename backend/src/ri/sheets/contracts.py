"""Sheet schema contracts, derived from the LIVE workbook metadata inspected 2026-11-20 (not from link gids).

A tab whose normalised header row differs from its contract is NOT imported ("schema drift -> paused import
with alert, not best-effort column guessing", spec 04 §6). An admin approves a new header hash explicitly.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

WORKBOOK_IDS = {
    "A": "1J9vDQK3lQzZLP2tx2TkoHnQQQSFEA3-qRmXtoU4TX9Y",
    "B": "1wJKkovlp4oFGo8aj3ayRlAH-oXZ4O-RGDK8LZme9EMc",
}
SOURCE_SYSTEM = "google_sheets"

_LIST_HEADERS = ["Company", "Country", "Segment", "Website", "Employees"]


@dataclass(frozen=True)
class SheetContract:
    workbook: str
    title: str
    kind: str
    headers: tuple[str, ...]
    company: str                      # header holding the company name
    identity: tuple[str, ...]         # headers forming the composite row identity (no stable row IDs exist)
    country: str | None = None
    website: str | None = None
    segment: str | None = None
    employees: str | None = None
    creates_orgs: bool = False        # may this tab introduce a canonical organisation?
    person: str | None = None         # header with a person's name (contact tabs)
    hyperlink_cols: tuple[str, ...] = field(default_factory=tuple)
    order: int = 0                    # processing order: org-creating tabs first

    @property
    def header_hash(self) -> str:
        return header_hash(list(self.headers))


def header_hash(headers: list[str]) -> str:
    return hashlib.sha256("\x1f".join(headers).encode()).hexdigest()


def _c(*a, **k) -> SheetContract:
    return SheetContract(*a, **k)


CONTRACTS: list[SheetContract] = [
    # ------------------------------------------------------------------ Workbook A
    _c("A", "MASTER ACCOUNTS", "master_account", tuple([
        "Company", "Country", "Segment", "Website", "Employees", "Signal Priority", "Buying Signal Score",
        "Signal Type", "Verified Buying Signal", "Signal Date", "Primary Source URL", "Secondary Source URL",
        "Evidence Confidence", "Project / Initiative", "Potential Engineering Need", "Outsourcing Relevance",
        "The Codest Service Fit", "Target Buyer Role", "Named Decision-Maker", "Decision-Maker Profile",
        "Procurement / Vendor Route", "Recommended Outreach Angle", "Opportunity Timing", "Last Researched",
        "Research Status", "Research Notes"]),
       company="Company", identity=("Company", "Country"), country="Country", website="Website",
       segment="Segment", employees="Employees", creates_orgs=True, order=10),
    *[
        _c("A", t, "company_list", tuple(_LIST_HEADERS), company="Company", identity=("Company", "Country"),
           country="Country", website="Website", segment="Segment", employees="Employees", creates_orgs=True,
           order=20)
        for t in ("5000+ CORPORATIONS", "UK", "NORDICS", "DACH", "POLAND")
    ],
    _c("A", "BUYER CONTACTS", "buyer_contact", tuple([
        "Market", "Company (ICP master)", "Contact", "Current role", "Buyer lane", "Evidence URL", "Evidence type",
        "Research context", "Work email (unverified)", "Account employees", "LinkedIn Profile URL",
        "Buyer Priority (P1/P2/P3)", "Buying Committee Role", "Verification Confidence", "Secondary Evidence URL",
        "Date Last Verified", "Relevant Buying Signal", "Recommended Contact Angle", "Priority Score /20",
        "The Codest Buyer Persona", "Buying Committee / Authority Notes"]),
       company="Company (ICP master)", identity=("Company (ICP master)", "Contact"), person="Contact", order=40),
    _c("A", "BUYING SIGNAL LOG", "buying_signal_log", tuple([
        "Company", "Country", "Signal type", "Verified event", "Event date", "Source URL", "Source publication date",
        "Potential engineering relevance", "Evidence confidence", "Research date", "Account reference"]),
       company="Company", identity=("Company", "Event date", "Signal type", "Source URL"), country="Country", order=50),
    _c("A", "BUYER RESEARCH STATUS", "buyer_research_status", tuple([
        "Company (MASTER ACCOUNTS)", "Country", "Market", "Employees (master estimate)", "Signal Priority",
        "Signal Score", "Prior Contacts", "New Contacts", "Contacts Total", "Technology", "Procurement",
        "Engineering", "Transformation", "Research Status", "Coverage Gaps / ICP Notes", "Last Research Date",
        "Recommended Next Research", "Buying Signal (MASTER)"]),
       company="Company (MASTER ACCOUNTS)", identity=("Company (MASTER ACCOUNTS)", "Country"), country="Country",
       order=60),
    # ------------------------------------------------------------------ Workbook B
    _c("B", "Accounts", "outreach_account", tuple([
        "Tier", "Company", "Sector", "Market / HQ", "Employees (est.)", "ICP status", "Score (original scale)",
        "Buying / tech signal", "Signal date", "Source link", "The Codest fit", "Lead contact", "Contacts", "Website",
        "Source / qualification note", "Research score /100", "Detailed ICP fit",
        "External engineering supplier evidence", "Supplier evidence URL", "Research buyer", "Research buyer role",
        "Research buyer LinkedIn", "Suggested outreach opener", "Additional signals and sources",
        "Research qualification caveat", "Latest Buying Signal (2026 verified)", "Signal Category",
        "Verified Signal Date", "Signal Source URL", "Second Buying Signal", "Second Signal Source URL",
        "Third Buying Signal", "Third Signal Source URL", "Potential Engineering Need", "The Codest Opportunity",
        "Supplier / Procurement Route", "Buying Signal Score /100", "Buying Signal Priority (A-D)",
        "Best Buyer (matched)", "Best Buyer Role", "Best Buyer LinkedIn", "Second Buyer (matched)",
        "Personalised Outreach Angle (verified)", "Research Confidence", "Last Research Date",
        "Recommended Action", "Research Status", "Score Breakdown (25/20/15/20/10/10)"]),
       company="Company", identity=("Company", "Market / HQ"), country="Market / HQ", website="Website",
       segment="Sector", employees="Employees (est.)", creates_orgs=True,
       hyperlink_cols=("Source link",), order=30),
    _c("B", "Contacts", "outreach_contact", tuple([
        "Tier", "Company", "Sector", "Contact", "Current role (reported)", "Buyer lane", "LinkedIn", "Work email",
        "Market / HQ", "Buying / tech signal", "Signal date", "Evidence link", "Original source", "Note",
        "Buyer Priority", "Company Buying Signal (verified)", "Matched Signal Date", "Matched Signal Source URL",
        "Buyer Relevance", "Buyer Priority#2", "Suggested Outreach Angle (by role)", "Recommended The Codest Service",
        "Company Opportunity Score", "Signal Confidence", "Research Updated", "Email Source",
        "Email Verification Status", "Email Research Date"]),
       company="Company", identity=("Company", "Contact"), country="Market / HQ", person="Contact",
       hyperlink_cols=("LinkedIn", "Evidence link"), order=45),
    _c("B", "Contacts — Compact", "outreach_contact_compact", tuple([
        "Company", "Contact", "Role", "Buyer Lane", "LinkedIn", "Email", "Country", "Priority", "Buying Signal",
        "Signal Date", "Source"]),
       company="Company", identity=("Company", "Contact"), country="Country", person="Contact",
       hyperlink_cols=("LinkedIn",), order=46),
    _c("B", "New Account Research — Nov 20", "new_account_research", tuple([
        "Company", "Market / HQ", "Employee count (estimate)", "Named contacts", "ICP fit (0–2)",
        "Recent signal (0–2)", "Impact (0–2)", "Buyer access (0–2)", "Delivery fit (0–2)", "Score /10",
        "Research priority", "Signal date", "2026 buying/technology signal", "Evidence URL",
        "Supplier / vendor route", "The Codest service fit", "Research date", "Qualification caveat"]),
       company="Company", identity=("Company", "Market / HQ"), country="Market / HQ",
       employees="Employee count (estimate)", creates_orgs=True, order=35),
    _c("B", "Contact Research Queue", "contact_research_queue", tuple([
        "Company", "Country", "Industry", "Employee count (existing)", "Current contacts", "Gap to 3",
        "Buyer lane gaps (research snapshot)", "Existing signal score /100", "Existing tier",
        "Existing published signal", "Signal date (recorded)", "Existing source URL", "Review urgency",
        "Research notes (snapshot)"]),
       company="Company", identity=("Company", "Country"), country="Country", segment="Industry",
       employees="Employee count (existing)", creates_orgs=True, order=36),
    _c("B", "Priority Outreach", "priority_outreach", tuple([
        "Rank", "Company", "Sector", "Headquarters", "Employees (Exa)", "Opportunity score /100",
        "Evidence confidence", "Strongest buying signal", "Event date", "Primary source URL",
        "External engineering vendor evidence", "Why this matters for The Codest (inference)",
        "The Codest capability", "First decision-maker to approach", "Position / buyer role",
        "Verified LinkedIn profile", "Suggested first outreach sentence", "Recommended sequence / channel",
        "Qualification caveats"]),
       company="Company", identity=("Company", "Headquarters"), country="Headquarters", segment="Sector",
       employees="Employees (Exa)", creates_orgs=True, hyperlink_cols=("Verified LinkedIn profile",), order=37),
]


def contracts_for(workbook: str) -> list[SheetContract]:
    return sorted([c for c in CONTRACTS if c.workbook == workbook], key=lambda c: c.order)
