import pytest
from fixtures.sheets_factory import client_for, master_row, workbook_a, workbook_b, make_row
from ri.identity.resolver import resolve
from ri.sheets import contracts as C
from ri.sheets.client import ConnectorBlocked, GoogleSheetsClient
from ri.sheets.importer import import_workbook, normalise_headers
from ri.sheets.parsers import (normalize_domain, normalize_name, parse_country, parse_date, parse_employees)
from ri.sheets.reconcile import icp_band

ACC = next(c for c in C.CONTRACTS if c.title == "Accounts")
CON = next(c for c in C.CONTRACTS if c.title == "Contacts")


def acc(company, market, **kw):
    return make_row(ACC, Company=company, **{"Market__/__HQ": market}, **kw)


# ---------------------------------------------------------------- parsers (real formats seen in live sheets)
def test_employee_formats():
    assert parse_employees("218 271").value == 218271
    e = parse_employees("1,027 employees (Exa estimate, reverify)")
    assert (e.value, e.basis, e.uncertain) == (1027, "provider_estimate", True)
    assert parse_employees("580 Exa estimate").basis == "provider_estimate"
    assert parse_employees("").value is None


def test_dates_countries_domains():
    assert str(parse_date("2026-11-13 (amended EU notice)")) == "2026-11-13"
    assert parse_date("sometime") is None            # unknown stays unknown, never guessed
    assert parse_country("Sweden; Stockholm") == "SE"
    assert parse_country("United Kingdom — London") == "GB"
    assert normalize_domain("https://www.hsbc.com") == "hsbc.com"
    assert normalize_domain("https://www.about.hsbc.com.hk/x") == "hsbc.com.hk"
    assert normalize_name("Deutsche Bank AG") == normalize_name("Deutsche Bank")
    assert normalize_name("Bupa UK") != normalize_name("Bupa")     # division != parent


def test_icp_boundary_flagged_when_estimate_straddles():
    assert icp_band(498, True)["boundary_uncertain"] is True
    assert icp_band(498, False)["boundary_uncertain"] is False
    assert icp_band(93000, False)["band"] == "strategic_5000_plus"


def test_duplicate_headers_made_unique():
    assert normalise_headers(["A", "Buyer Priority", "Buyer Priority", ""]) == ["A", "Buyer Priority", "Buyer Priority#2", ""]


# ---------------------------------------------------------------- import + lineage
def test_two_workbooks_reconcile_to_one_identity_with_both_lineages(conn):
    a = workbook_a([master_row("Alpha Bank plc", "United Kingdom", website="https://www.alphabank.com", employees="12 000"),
                    master_row("Beta Payments", "Germany")])
    b = workbook_b(accounts=[acc("Alpha Bank", "United Kingdom; London", **{"Employees__(est.)": "12,000"}),
                             acc("Gamma Fintech", "Sweden; Stockholm", **{"Employees__(est.)": "580 Exa estimate"})])
    ra = import_workbook(conn, client_for(a=a), "A")
    rb = import_workbook(conn, client_for(b=b), "B")
    assert ra.status == "completed", ra
    assert rb.status == "completed", rb
    orgs = conn.execute("select id, display_name from organizations where display_name ilike 'alpha%'").fetchall()
    assert len(orgs) == 1
    links = conn.execute("select source_file_id, sheet_title, row_number from account_source_links where organization_id=%s", (orgs[0]["id"],)).fetchall()
    assert {l["source_file_id"] for l in links} == {C.WORKBOOK_IDS["A"], C.WORKBOOK_IDS["B"]}
    g = conn.execute("select employees_estimate, employees_basis, employees_uncertain, country_code from organizations where display_name='Gamma Fintech'").fetchone()
    assert (g["employees_estimate"], g["employees_basis"], g["employees_uncertain"], g["country_code"]) == (580, "provider_estimate", True, "SE")


def test_replay_is_idempotent(conn):
    a = workbook_a([master_row("Alpha Bank plc", "United Kingdom"), master_row("Beta Payments", "Germany")])
    cl = client_for(a=a)
    import_workbook(conn, cl, "A")
    n_org = conn.execute("select count(*) c from organizations").fetchone()["c"]
    n_link = conn.execute("select count(*) c from account_source_links").fetchone()["c"]
    n_stg = conn.execute("select count(*) c from staging_sheet_rows").fetchone()["c"]
    r2 = import_workbook(conn, cl, "A")
    assert r2.tabs["MASTER ACCOUNTS"]["unchanged"] == 2 and r2.tabs["MASTER ACCOUNTS"]["new"] == 0
    assert conn.execute("select count(*) c from organizations").fetchone()["c"] == n_org
    assert conn.execute("select count(*) c from account_source_links").fetchone()["c"] == n_link
    assert conn.execute("select count(*) c from staging_sheet_rows").fetchone()["c"] == n_stg


def test_row_move_keeps_lineage_and_org(conn):
    rows = [master_row("Alpha Bank plc", "United Kingdom"), master_row("Beta Payments", "Germany")]
    import_workbook(conn, client_for(a=workbook_a(rows)), "A")
    oid = conn.execute("select organization_id o from account_source_links where raw_company_name='Beta Payments' and sheet_title='MASTER ACCOUNTS'").fetchone()["o"]
    import_workbook(conn, client_for(a=workbook_a(list(reversed(rows)))), "A")      # sheet re-sorted
    r = conn.execute("select organization_id o, row_number n from account_source_links where raw_company_name='Beta Payments' and sheet_title='MASTER ACCOUNTS'").fetchone()
    assert r["o"] == oid and r["n"] == 2


def test_edited_row_updates_staging_not_duplicate_org(conn):
    import_workbook(conn, client_for(a=workbook_a([master_row("Alpha Bank plc", "United Kingdom", employees="1 000")])), "A")
    r = import_workbook(conn, client_for(a=workbook_a([master_row("Alpha Bank plc", "United Kingdom", employees="1 000", website="https://alpha.com")])), "A")
    assert r.tabs["MASTER ACCOUNTS"]["changed"] == 1
    assert conn.execute("select count(*) c from organizations").fetchone()["c"] == 1
    assert conn.execute("select count(*) c from staging_sheet_rows where row_identity like 'alpha bank%'").fetchone()["c"] == 2  # history kept


def test_schema_drift_pauses_tab_and_keeps_prior_data(conn):
    a = workbook_a([master_row("Alpha Bank plc", "United Kingdom")])
    import_workbook(conn, client_for(a=a), "A")
    a2 = workbook_a([["x"] * 2])
    a2["MASTER ACCOUNTS"][0][4] = "Headcount"                      # renamed column
    a2["MASTER ACCOUNTS"][0] = [("Headcount" if h == "Employees" else h) for h in C.contracts_for("A")[0].headers]
    r = import_workbook(conn, client_for(a=a2), "A")
    assert r.tabs["MASTER ACCOUNTS"]["status"] == "drift"
    assert r.tabs["MASTER ACCOUNTS"]["missing"] == ["Employees"]
    assert r.status == "paused_schema_drift"
    assert conn.execute("select count(*) c from organizations").fetchone()["c"] == 1     # prior snapshot untouched
    assert conn.execute("select count(*) c from audit_events where operation='schema_drift'").fetchone()["c"] >= 1


def test_priority_outreach_blank_trailing_columns_tolerated_but_not_with_data(conn):
    b = workbook_b()
    r = import_workbook(conn, client_for(b=b), "B")
    assert r.tabs["Priority Outreach"]["status"] == "ok"
    b2 = workbook_b()
    po = b2["Priority Outreach"]
    po.append(["1", "Zeta GmbH"] + [""] * 17 + ["stray data"] + [""] * 27)
    r2 = import_workbook(conn, client_for(b=b2), "B")
    assert r2.tabs["Priority Outreach"]["status"] == "drift"


def test_duplicate_header_in_contacts_tab_is_handled(conn):
    b = workbook_b(accounts=[acc("Alpha Bank", "United Kingdom")],
                   contacts=[make_row(CON, Company="Alpha Bank", Contact="Test Person", **{"Email__Verification__Status": "Found — Unverified",
                                                                                         "Work__email": "t@example.invalid"})])
    r = import_workbook(conn, client_for(b=b), "B")
    assert r.tabs["Contacts"]["status"] == "ok", r.tabs["Contacts"]
    p = conn.execute("select email_verification_status s, work_email e from people").fetchone()
    assert p["s"] == "Found — Unverified"          # original verification label preserved, never promoted


def test_hyperlink_targets_preserved_in_staging(conn):
    b = workbook_b(accounts=[acc("Alpha Bank", "United Kingdom", **{"Source__link": "Source"})])
    idx = ACC.headers.index("Source link") + 1
    cl = client_for(b=b, links={(C.WORKBOOK_IDS["B"], "Accounts", idx, 2): "https://example.invalid/press"})
    import_workbook(conn, cl, "B")
    row = conn.execute("select raw_values, links from staging_sheet_rows where sheet_title='Accounts'").fetchone()
    assert row["raw_values"][idx - 1] == "Source"                                   # original untouched
    assert row["links"] == {"Source link": "https://example.invalid/press"}


def test_blocked_without_credentials(conn):
    with pytest.raises(ConnectorBlocked):
        GoogleSheetsClient(None)
    from ri.sheets.client import FixtureSheetsClient
    r = import_workbook(conn, FixtureSheetsClient({}), "A")
    assert r.status == "blocked"
    assert conn.execute("select status from import_runs where id=%s", (r.run_id,)).fetchone()["status"] == "blocked"


def test_sheets_client_has_no_write_surface():
    from ri.sheets import client
    assert client.SCOPE.endswith("spreadsheets.readonly")
    for cls in (client.GoogleSheetsClient, client.FixtureSheetsClient):
        assert not [m for m in dir(cls) if m.startswith(("update", "append", "write", "set_", "clear"))]


# ---------------------------------------------------------------- identity policy
def _org(conn, name, country=None, domain=None, ident=None):
    o = conn.execute("insert into organizations(display_name,country_code,canonical_domain) values (%s,%s,%s) returning id", (name, country, domain)).fetchone()["id"]
    conn.execute("insert into organization_aliases(organization_id,alias,alias_norm) values (%s,%s,%s)", (o, name, normalize_name(name)))
    if ident:
        conn.execute("insert into organization_identifiers(organization_id,scheme,jurisdiction,identifier) values (%s,%s,%s,%s)", (o, *ident))
    return str(o)


def test_parent_and_subsidiary_not_merged(conn):
    parent = _org(conn, "Bupa Group", "GB")
    d = resolve(conn, name="Bupa UK", country="GB", allow_create=True)
    assert d.action in ("review", "create_new") and d.action != "auto_link"
    assert d.org_id is None


def test_exact_name_different_country_is_a_different_entity(conn):
    _org(conn, "Nova Bank", "GB")
    d = resolve(conn, name="Nova Bank", country="DE", allow_create=True)
    assert d.action == "create_new"


def test_contradictory_legal_ids_block_autolink(conn):
    o = _org(conn, "Delta Bank", "GB", ident=("LEI", "", "AAAA0000000000000001"))
    ok = resolve(conn, name="Delta Bank", country="GB", identifiers=[("LEI", "", "AAAA0000000000000001")])
    assert ok.action == "auto_link" and ok.reason == "legal_identifier"
    # same org presented with a *different* LEI -> contradiction, never auto-link
    bad = resolve(conn, name="Delta Bank", country="GB", identifiers=[("LEI", "", "AAAA0000000000000001"), ("LEI", "", "BBBB0000000000000002")])
    assert bad.action == "review" and bad.reason == "contradictory_ids"
    # two ids that resolve to two different orgs -> contradiction too
    o2 = _org(conn, "Other Bank", "GB", ident=("LEI", "", "DDDD0000000000000004"))
    both = resolve(conn, name="Delta Bank", country="GB", identifiers=[("LEI", "", "AAAA0000000000000001"), ("LEI", "", "DDDD0000000000000004")])
    assert both.action == "review" and sorted(both.candidates) == sorted([o, o2])


def test_shared_group_domain_alone_never_links(conn):
    _org(conn, "Epsilon Holdings", "GB", domain="epsilon.com")
    d = resolve(conn, name="Epsilon Insurance Services", country="GB", domain="epsilon.com", allow_create=True)
    assert d.action == "review"          # domain candidate -> human review, not auto-link


def test_ambiguous_exact_matches_queue_review(conn):
    _org(conn, "Zeta Capital", None)
    _org(conn, "Zeta Capital", None)
    d = resolve(conn, name="Zeta Capital", country="GB")
    assert d.action == "review" and d.reason == "ambiguous_match" and len(d.candidates) == 2


def test_unresolvable_contact_company_opens_review_not_org(conn):
    b = workbook_b(contacts=[make_row(CON, Company="Unknown Corp", Contact="Test Person")])
    import_workbook(conn, client_for(b=b), "B")
    assert conn.execute("select count(*) c from organizations").fetchone()["c"] == 0
    assert conn.execute("select count(*) c from identity_reviews where status='open'").fetchone()["c"] == 1
    assert conn.execute("select count(*) c from people").fetchone()["c"] == 0
