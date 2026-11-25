"""Workbook A "Leads Master List" layout (v2): MASTER ACCOUNTS / MASTER CONTACTS / MASTER BUYING SIGNALS joined by Account ID.
All data here is SYNTHETIC (names are invented, addresses use example.invalid)."""
import io

import openpyxl

from ri.sheets import contracts as C
from ri.sheets.client import XlsxValuesClient, detect_workbook, xlsx_client
from ri.sheets.importer import import_workbook
from ri.target_market import target_sql

V2 = {ct.title: ct for ct in C.contracts_for("A", "v2")}

ACCOUNTS = [
    {"Account ID": "A00001", "Company": "Kreissparkasse Synthbach", "Region": "DACH", "Country": "Germany", "Industry": "Banking",
     "Employees": 1200, "ICP Segment": "Enterprise", "Website": "https://ks-synthbach.example.invalid", "Account Priority": "P1",
     "Opportunity Score": 81, "Strongest Buying Signal": "Synthbach starts a core banking replacement", "Signal Date": "2026-11-12",
     "Eligibility": "Eligible"},
    # a look-alike name that is a different account in the list: must not be held for a manual match
    {"Account ID": "A00002", "Company": "Kreissparkasse Synthdorf", "Region": "DACH", "Country": "Germany", "Industry": "Banking",
     "Employees": 900, "Website": "https://ks-synthdorf.example.invalid", "Account Priority": "P2", "Opportunity Score": 64,
     "Eligibility": "Review"},
    {"Account ID": "A00003", "Company": "Synthetic Adtech GmbH", "Country": "Germany", "Industry": "AdTech", "Employees": 300,
     "Eligibility": "Out of scope"},
]
CONTACTS = [
    {"Contact ID": "C00001", "Full Name": "Ada Example", "Job Title": "CTO", "Company": "Kreissparkasse Synthbach", "Account ID": "A00001",
     "Persona Category": "C-Level Technology", "Seniority": "Executive", "LinkedIn URL": "https://linkedin.com/in/ada-example",
     "Work Email": "ada@ks-synthbach.example.invalid", "Email Verification Status": "Verified", "Contact Priority": "P1"},
    {"Contact ID": "C00002", "Full Name": "Ben Example", "Job Title": "Head of Procurement", "Company": "Kreissparkasse Synthbach",
     "Account ID": "A00001", "Work Email": "ben@ks-synthbach.example.invalid", "Email Verification Status": "Unverified"},
    {"Contact ID": "C00003", "Full Name": "Cy Example", "Job Title": "VP Engineering", "Company": "Kreissparkasse Synthdorf",
     "Account ID": "A00002", "Work Email": "", "Email Verification Status": "Missing"},
    # the same person listed twice (same address): one person, not two
    {"Contact ID": "C00004", "Full Name": "Ada Example", "Job Title": "Chief Technology Officer", "Company": "Kreissparkasse Synthbach",
     "Account ID": "A00001", "Work Email": "ADA@ks-synthbach.example.invalid", "Email Verification Status": "Verified"},
]
SIGNALS = [
    {"Signal ID": "S00001", "Company": "Kreissparkasse Synthbach", "Account ID": "A00001", "Country": "Germany",
     "Signal Category": "Platform modernisation", "Verified Event": "Synthbach starts a core banking replacement",
     "Event Date": "2026-11-12", "Source URL": "https://ks-synthbach.example.invalid/press/core", "Evidence Confidence": "High"},
]


def make_v2(extra_tabs=("ARCHIVE — MASTER ACCOUNTS", "UK — CONTACTS")) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in (("MASTER ACCOUNTS", ACCOUNTS), ("MASTER CONTACTS", CONTACTS), ("MASTER BUYING SIGNALS", SIGNALS)):
        ws = wb.create_sheet(title)
        ws.append(list(V2[title].headers))
        for r in rows:
            ws.append([r.get(h, "") for h in V2[title].headers])
        for _ in range(5):
            ws.append([None] * len(V2[title].headers))      # exports carry empty tails
    for t in extra_tabs:
        wb.create_sheet(t).append(["Company", "Country"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _import(conn, data):
    return import_workbook(conn, xlsx_client("A", C.WORKBOOK_IDS["A"], data), "A")


def test_v2_layout_is_detected_and_read_without_holding_unused_tabs():
    data = make_v2()
    assert detect_workbook(data) == "A"
    assert C.detect_layout("A", ["MASTER ACCOUNTS", "MASTER CONTACTS", "MASTER BUYING SIGNALS", "DASHBOARD"]) == "v2"
    assert C.detect_layout("A", ["MASTER ACCOUNTS", "BUYING SIGNAL LOG"]) == "v1"
    client = xlsx_client("A", C.WORKBOOK_IDS["A"], data)
    assert isinstance(client, XlsxValuesClient)
    tabs = {t.title: t for t in client.get_tabs(C.WORKBOOK_IDS["A"])}
    assert tabs["MASTER ACCOUNTS"].row_count == 1 + len(ACCOUNTS)       # header + rows, empty tail dropped
    assert tabs["UK — CONTACTS"].row_count == 0                         # listed, never held


def test_v2_import_creates_accounts_people_and_signals(conn):
    res = _import(conn, make_v2())
    assert res.status == "completed" and res.layout == "v2", res.tabs
    assert res.tabs["UK — CONTACTS"]["status"] == "unknown_tab"
    assert not res.reconcile["errors"] and res.reconcile["reviews"] == 0
    orgs = {r["list_account_id"]: r for r in conn.execute("select * from organizations where list_account_id is not null")}
    assert set(orgs) == {"A00001", "A00002", "A00003"}                  # the look-alike is its own company
    a1 = orgs["A00001"]
    assert (a1["list_eligibility"], a1["list_priority"], float(a1["list_score"]), a1["country_code"]) == ("Eligible", "P1", 81.0, "DE")
    people = {r["full_name"]: r for r in conn.execute("select * from people")}
    assert len(people) == 3                                              # Ada listed twice with the same address
    assert people["Ada Example"]["email_verification_status"] == "Verified"
    assert people["Ada Example"]["linkedin_url"] == "https://linkedin.com/in/ada-example"
    assert people["Ben Example"]["email_verification_status"] == "Unverified"
    assert people["Cy Example"]["work_email"] is None
    at = conn.execute("select e.organization_id from employment e join people p on p.id=e.person_id where p.full_name='Cy Example'").fetchone()
    assert str(at["organization_id"]) == str(orgs["A00002"]["id"])      # placed by Account ID
    src = conn.execute("select detail from person_sources where system='sheets' and external_id='C00001'").fetchone()
    assert src["detail"]["seniority"] == "Executive"
    master = conn.execute("select original from legacy_account_research where kind='master_account' and organization_id=%s",
                          (a1["id"],)).fetchone()["original"]
    # read by the Leads tab under the v1 column names; the source comes from the matching signal row
    assert master["Verified Buying Signal"] == "Synthbach starts a core banking replacement"
    assert master["Buying Signal Score"] == "81" and master["Signal Priority"] == "P1"
    assert master["Primary Source URL"] == "https://ks-synthbach.example.invalid/press/core"
    log = conn.execute("select original from legacy_account_research where kind='buying_signal_log'").fetchone()["original"]
    assert log["Verified event"] == "Synthbach starts a core banking replacement" and log["Source URL"].endswith("/press/core")


def test_v2_import_is_idempotent(conn):
    data = make_v2()
    _import(conn, data)
    res = _import(conn, data)
    assert res.tabs["MASTER CONTACTS"]["unchanged"] == len(CONTACTS)
    n = lambda t: conn.execute(f"select count(*) c from {t}").fetchone()["c"]  # noqa: E731,S608
    assert n("organizations") == 3 and n("people") == 3 and n("employment") == 4


def test_list_eligibility_decides_target_accounts(conn):
    _import(conn, make_v2())
    q = lambda review: {r["list_account_id"] for r in conn.execute(  # noqa: E731
        f"select o.list_account_id from organizations o where {target_sql('o', review=review)}")}  # noqa: S608
    # no ownership source needed for listed accounts; "Out of scope" is neither a target nor waiting for review
    assert q(False) == {"A00001"} and q(True) == {"A00002"}
