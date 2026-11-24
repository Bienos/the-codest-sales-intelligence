"""Target-market rules apply consistently across the seller workspace."""
from fastapi.testclient import TestClient
from test_r4_crm_notify import mk_user

from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.db import Database, jb


def test_private_finance_target_and_workbook_leads(conn, db_url):
    email = "research@x"
    secret = "x" * 32
    mk_user(conn, email, "admin")
    records = [
        ("Example UK Bank", "GB", "Banking", 501, "private", "company"),
        ("Example Swiss Payments", "CH", "Payments", 2200, "private", "company"),
        ("Example Nordic Insurance", "SE", "Insurance", 700, "private", "company"),
        ("Example Polish Bank", "PL", "Banking", 800, "private", "company"),
        ("Example French Asset Manager", "FR", "Asset management", 600, "private", "company"),
        ("Example International Lender", "CA", "Lending", 2001, "private", "company"),
        ("Example Too Small UK Bank", "GB", "Banking", 500, "private", "company"),
        ("Example Too Small International Bank", "US", "Banking", 2000, "private", "company"),
        ("Example Listed Bank", "GB", "Banking", 6000, "listed", "company"),
        ("Example State-owned Bank", "DE", "Banking", 6000, "state_owned", "company"),
        ("Example Unknown Ownership Bank", "NO", "Banking", 6000, "unknown", "company"),
        ("Example Public Body", "PL", "Insurance", 6000, "private", "public_body"),
        ("ZUS Example", "PL", "Insurance", 6000, "private", "company"),
        ("Zakład Ubezpieczeń Społecznych", "PL", "Insurance", 6000, "private", "company"),
        ("Example Bank Software Vendor", "GB", "Banking software", 9000, "private", "company"),
        ("Example Retailer", "GB", "Retail", 9000, "private", "company"),
        ("Example Unknown Location Bank", None, "Banking", 9000, "private", "company"),
        ("Example Unknown Size Bank", "GB", "Banking", None, "private", "company"),
    ]
    ids = {}
    for index, (name, country, segment, headcount, owner, kind) in enumerate(records, 1):
        org = conn.execute(
            "INSERT INTO organizations(display_name, country_code, segment, employees_estimate, company_ownership, "
            "entity_type, company_profile_source_url, in_master_list) VALUES (%s,%s,%s,%s,%s,%s,%s,true) RETURNING id",
            (name, country, segment, headcount, owner, kind, "https://example.com/company" if owner != "unknown" else None),
        ).fetchone()["id"]
        ids[name] = str(org)
        link = conn.execute(
            "INSERT INTO account_source_links(organization_id, source_system, source_file_id, sheet_id, sheet_title, "
            "row_identity, row_number, imported_hash, raw_company_name, observed_at) "
            "VALUES (%s,'google_sheets','workbook','sheet','Shortlist',%s,%s,'hash',%s,now()) RETURNING id",
            (org, str(index), index, name),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO legacy_account_research(organization_id, source_link_id, kind, original, observed_at) "
            "VALUES (%s,%s,'priority_outreach',%s,now())",
            (org, link, jb({"Strongest buying signal": "Replacing its payments platform",
                           "Primary source URL": f"https://example.com/news/{index}"})),
        )
    conn.commit()
    settings = Settings(env="development", auth_mode="dev", dev_auth_secret=secret, database_url=db_url)
    headers = {"Authorization": f"Bearer {make_dev_token(secret, email)}"}
    db = Database(db_url)
    try:
        with TestClient(create_app(settings, db=db), headers=headers) as client:
            expected = {record[0] for record in records[:6]}
            response = client.get("/api/leads?scope=target")
            assert response.status_code == 200
            assert {r["name"] for r in response.json()["items"]} == expected
            assert response.json()["facets"]["region"] == dict.fromkeys(["uk", "dach", "nordics", "poland", "europe", "international"], 1)
            assert [r["name"] for r in client.get("/api/leads?scope=target&region=international").json()["items"]] == ["Example International Lender"]
            export = client.get("/api/leads.csv?scope=target&q=Swiss%20payments").text
            assert "Example Swiss Payments" in export
            assert "Example UK Bank" not in export
            leads = client.get("/api/prospects?target=true&lane=shortlist&region=dach&q=payments%20platform")
            assert leads.status_code == 200
            assert [r["organization"]["name"] for r in leads.json()["items"]] == ["Example Swiss Payments"]
            review = client.get("/api/leads?scope=review").json()["items"]
            assert [r["name"] for r in review] == ["Example Unknown Ownership Bank"]
            profile = f"/api/accounts/{ids['Example Unknown Ownership Bank']}/company-profile"
            assert client.put(profile, json={"ownership": "private"}).status_code == 422
            assert client.put(profile, json={"ownership": "private", "source_url": "https://example.com/ownership"}).status_code == 200
            assert client.get("/api/leads?scope=target&region=nordics").json()["total"] == 2
            assert client.get("/api/accounts/" + ids["ZUS Example"] + "?target=true").status_code == 404
            explorer = client.get("/api/explorer?target=true&entity=companies").json()
            assert explorer["count"] == 7
            search = client.get("/api/search?target=true&q=Example%20Listed%20Bank").json()
            assert all(group["key"] != "accounts" for group in search["groups"])
            for path in ("/api/today", "/api/overview", "/api/feed", "/api/explorer?entity=events", "/api/explorer?entity=opportunities"):
                delimiter = "&" if "?" in path else "?"
                assert client.get(f"{path}{delimiter}target=true").status_code == 200
    finally:
        db.close()
