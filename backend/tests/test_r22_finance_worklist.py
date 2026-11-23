"""Seller-facing sector and evidence guarantees on the normal HTTP endpoints."""
from fastapi.testclient import TestClient

from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.db import Database
from ri.signals.baseline import import_baseline
from ri.signals.persist import sync_rule_versions
from test_r4_crm_notify import mk_user


def test_finance_worklist_shows_exact_account_signal_with_source(conn, db_url):
    import_baseline(conn)
    sync_rule_versions(conn)
    mk_user(conn, "seller@demo.invalid", "bdm", 555)
    orgs = {}
    for segment in ("Banking", "Banking software vendor", None):
        row = conn.execute(
            "INSERT INTO organizations (display_name, segment, country_code) VALUES (%s,%s,'GB') RETURNING id",
            (f"Synthetic {segment}", segment),
        ).fetchone()
        orgs[segment] = row["id"]
    finance = orgs["Banking"]
    other = orgs["Banking software vendor"]
    for subject, signal_account in ((finance, finance), (finance, other)):
        event = conn.execute("""
            INSERT INTO events(event_key,event_type,subject_org_id,summary,extraction_model_version,verification_status)
            VALUES (%s,'vendor_selected',%s,'Core banking platform selection','fixture','verified') RETURNING id
            """, (str(signal_account), subject)).fetchone()["id"]
        claim = conn.execute("""
            INSERT INTO claims (subject,predicate,evidence_anchor,anchor_verified,claim_kind,status,object_json)
            VALUES ('Synthetic Banking','vendor_selected','Selected a new core platform',true,'structured','supported',
                '{"official_url":"https://example.invalid/buying-proof"}') RETURNING id
            """).fetchone()["id"]
        conn.execute("INSERT INTO event_claims(event_id,claim_id) VALUES (%s,%s)", (event, claim))
        rule = conn.execute("SELECT version FROM signal_rule_versions WHERE rule_id='PRG-01' AND status='active'").fetchone()
        conn.execute("""
            INSERT INTO signals(event_id,rule_id,rule_version,organization_id,strength,demand_tier,
                                verification_status,effective_from,evidence_claim_ids,reason_code)
            VALUES (%s,'PRG-01',%s,%s,2,'D3','verified',now(),%s,'fixture')
            """, (event, rule["version"], signal_account, [claim]))
    conn.commit()

    db = Database(db_url)
    try:
        secret = "x" * 32
        app = create_app(Settings(database_url=db_url, auth_mode="dev", dev_auth_secret=secret), db=db)
        with TestClient(app) as client:
            auth = {"Authorization": f"Bearer {make_dev_token(secret, 'seller@demo.invalid')}"}
            result = client.get("/api/leads?focus=finance", headers=auth)
            assert result.status_code == 200
            (lead,) = result.json()["items"]
            assert lead["id"] == str(finance)
            assert lead["buying_signal"]["source_url"] == "https://example.invalid/buying-proof"
            assert lead["buying_signal"]["demand_tier"] == "D3"
            vendor = client.get("/api/leads?focus=other", headers=auth).json()["items"]
            assert [r["id"] for r in vendor] == [str(other)]
            assert vendor[0]["buying_signal"] is None
            csv = client.get("/api/leads.csv?focus=finance", headers=auth).text
            assert "Synthetic Banking," in csv and "software vendor" not in csv
            feed = client.get("/api/feed?focus=finance", headers=auth)
            assert feed.status_code == 200
            assert feed.json()["count"] == 2
            assert all(item["organization"]["id"] == str(finance) for item in feed.json()["items"])
            assert sum(bool(item["signals"]) for item in feed.json()["items"]) == 1
    finally:
        db.close()
