"""SYNTHETIC workbook factory. Headers mirror the live schemas (inspected 2026-11-20); all row DATA is invented.
No real prospect or contact data is stored in this repository."""
from __future__ import annotations

from ri.sheets import contracts as C
from ri.sheets.client import FixtureSheetsClient


def blank_row(ct):
    return [""] * len(ct.headers)


def make_row(ct, **by_header):
    row = blank_row(ct)
    for h, v in by_header.items():
        row[ct.headers.index(h.replace("__", " "))] = v
    return row


def master_row(company, country, segment="Banking", website="", employees=""):
    ct = next(c for c in C.CONTRACTS if c.title == "MASTER ACCOUNTS")
    return make_row(ct, Company=company, Country=country, Segment=segment, Website=website, Employees=employees,
                    **{"Research__Notes": "SYNTHETIC"})


def workbook_a(master_rows, extra=None):
    cts = {c.title: c for c in C.contracts_for("A")}
    tabs = {}
    for t, ct in cts.items():
        tabs[t] = [list(ct.headers)]
    tabs["MASTER ACCOUNTS"] += master_rows
    for t, rows in (extra or {}).items():
        tabs[t] += rows
    return tabs


def workbook_b(accounts=None, contacts=None, priority_blank_cols=28):
    cts = {c.title: c for c in C.contracts_for("B")}
    tabs = {t: [list(ct.headers)] for t, ct in cts.items()}
    tabs["Priority Outreach"][0] = list(cts["Priority Outreach"].headers) + [""] * priority_blank_cols
    tabs["Contacts"][0] = ["Tier", "Company", "Sector", "Contact", "Current role (reported)", "Buyer lane", "LinkedIn", "Work email",
                           "Market / HQ", "Buying / tech signal", "Signal date", "Evidence link", "Original source", "Note",
                           "Buyer Priority", "Company Buying Signal (verified)", "Matched Signal Date", "Matched Signal Source URL",
                           "Buyer Relevance", "Buyer Priority", "Suggested Outreach Angle (by role)", "Recommended The Codest Service",
                           "Company Opportunity Score", "Signal Confidence", "Research Updated", "Email Source",
                           "Email Verification Status", "Email Research Date"]   # NB duplicate 'Buyer Priority' as in the live sheet
    tabs["Accounts"] += accounts or []
    tabs["Contacts"] += contacts or []
    return tabs


def client_for(a=None, b=None, links=None):
    wbs = {}
    if a is not None:
        wbs[C.WORKBOOK_IDS["A"]] = a
    if b is not None:
        wbs[C.WORKBOOK_IDS["B"]] = b
    return FixtureSheetsClient(wbs, links)
