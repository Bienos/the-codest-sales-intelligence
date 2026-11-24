"""Findings acceptance examples using the actual database and seller HTTP routes."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from test_r4_crm_notify import mk_user

from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.db import Database
from ri.events.store import add_claim, store_event
from ri.events.structured import emit_procurement_events
from ri.events.verify import verify_event
from ri.ingest.pipeline import upsert_document, upsert_notice
from ri.sources.base import SourceDocumentDraft
from ri.sources.registry import seed_registry


def test_findings_acceptance_matrix(conn, db_url):
    seed_registry(conn)
    now = datetime.now(UTC)
    mk_user(conn, "admin@example.test", "admin")
    mk_user(conn, "seller@example.test", "bdm", 700)
    mk_user(conn, "marketing@example.test", "marketing")
    secret = "x" * 32

    def org(label, *, country="GB", ownership="private", size=900, sector="Banking", entity="company", proof=True):
        return conn.execute("""
            INSERT INTO organizations(display_name, country_code, company_ownership, employees_estimate, segment,
                entity_type, company_profile_source_url, canonical_domain)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (label, country, ownership, size, sector, entity, "https://bank.example/ownership" if proof else None,
              "bank.example")).fetchone()["id"]

    def source(label, url=None):
        external = uuid4().hex
        doc, _ = upsert_document(conn, "procurement-ted",
                                 SourceDocumentDraft(external, "1", url or f"https://bank.example/{label}",
                                                     label, now - timedelta(days=1), label, label), "excerpt")
        return doc

    def signal(label, buyer, *, age=1, verified=True, fit="strong", url=None, replay=False, lifecycle="active",
               kind="quote", anchor=True, key=None, type_="programme_announced"):
        doc = source(label, url)
        claim = add_claim(conn, subject=label, predicate="announced", obj={}, doc_id=doc, anchor=label, kind=kind, anchor_verified=anchor)
        if kind == "inferred":
            conn.execute("UPDATE claims SET status='supported' WHERE id=%s", (claim,))
        at = now - timedelta(days=age)
        event, _ = store_event(conn, event_type=type_, subject_name=label, subject_org_id=str(buyer), summary=label,
                               attrs={"dedup_key": key or label, "fit": {"level": fit}, "summary_pl": f"PL {label}"},
                               key_parts=(uuid4().hex,), event_at=at, pub_at=at, claim_ids=[claim],
                               document_ids=[doc], replay=replay, lifecycle=lifecycle)
        if verified:
            verify_event(conn, event)
        return event, doc

    def notice(label, buyer, *, status="open", offset=5, checked=now, title="Development of a payments platform",
               publisher=None, relevant=True):
        doc = source(label)
        record = dict(notice_id=label, buyer_name=label, title=title, status=status, country="GB",
                      published_at=now-timedelta(days=1), deadline_at=now+timedelta(days=offset) if offset else None,
                      official_url=f"https://bank.example/{label}", service_codes=["72260000"] if relevant else [])
        nid, change = upsert_notice(conn, "procurement-ted", record, doc, buyer if publisher is None else publisher)
        event = emit_procurement_events(conn, nid, change)
        if event:
            verify_event(conn, event)
        conn.execute("UPDATE procurement_notices SET last_status_check_at=%s WHERE id=%s", (checked, nid))
        return event, nid

    target = org("Pilot Bank")
    dach = org("DACH Payments", country="DE", sector="Payments")
    nordic = org("Nordic Insurance", country="SE", sector="Insurance")
    international = org("Canadian Lender", country="CA", size=2001, sector="Lending")
    restricted = org("Restricted Lender")
    conn.execute("""INSERT INTO crm_organizations(crm_id,organization_id,owner_id,name,link_status)
                    VALUES (1001,%s,999,'Restricted Lender','linked')""", (restricted,))

    first, primary_doc = signal("Core migration confirmed", target, key="replacement")
    mirror, _ = signal("Core migration repeated", target, key="replacement")
    intl, _ = signal("Canadian migration", international)
    nord, _ = signal("Nordic migration", nordic)
    de, _ = signal("DACH migration", dach)
    limited, _ = signal("Restricted migration", restricted)
    archive_only, _ = signal("Earlier engineering news", target, age=40)
    weak, _ = signal("Fintech raises funding", target, fit="possible")
    for label, options in [
        ("Small UK bank", {"size": 500}), ("Small US bank", {"country": "US", "size": 2000}),
        ("Listed bank", {"ownership": "listed"}), ("State bank", {"ownership": "state_owned"}),
        ("Unknown ownership", {"ownership": "unknown", "proof": False}), ("Missing proof", {"proof": False}),
        ("Public body", {"entity": "public_body"}), ("ZUS", {}), ("Software vendor", {"sector": "Banking software"}),
        ("Unverified country", {"country": "ZZ"}),
    ]:
        signal(label, org(label, **options))
    signal("Unverified extract", target, verified=False)
    signal("Historical replay", target, replay=True)
    signal("Expired event", target, lifecycle="expired")
    signal("Future event", target, age=-1)
    signal("Bad evidence link", target, url="urn:example:old")
    signal("Unanchored quote", target, anchor=False)
    signal("Inferred fact", target, kind="inferred")
    signal("Internal CRM note", target, type_="crm_buyer_response")
    signal("Unstructured deadline", target, type_="procurement_published")

    open_event, open_notice = notice("active-rfp", target, offset=3)
    later_event, _ = notice("other-rfp", dach, offset=7)
    notice("expired-rfp", target, offset=-1)
    notice("missing-deadline", target, offset=0)
    notice("awarded-rfp", target, status="awarded")
    notice("stale-status", target, checked=now-timedelta(days=3))
    notice("hardware-rfp", target, title="Supply of laptops", relevant=False)
    notice("public-rfp", org("Public authority", entity="public_body"))
    notice("cancelled-rfp", target, status="cancelled")
    conn.execute("""INSERT INTO verification_requests(organization_id,url,origin,status,document_id,claimed_text,requested_by)
                    VALUES (%s,'https://bank.example/core','sheet_signal','verified',%s,'Workbook lead','test'),
                           (%s,'https://bank.example/pending','exa_discovery','queued',NULL,'Pending lead','test'),
                           (%s,'https://bank.example/wrong-buyer','manual','verified',%s,'Mismatched lead','test')""",
                 (target, primary_doc, target, nordic, primary_doc))
    conn.execute("UPDATE sources SET status='active' WHERE source_id='procurement-ted'")
    conn.execute("UPDATE source_health SET state='healthy',last_success_at=now()-interval '3 days' WHERE source_id='procurement-ted'")
    conn.commit()

    db = Database(db_url)
    settings = Settings(env="development", auth_mode="dev", dev_auth_secret=secret, database_url=db_url)
    try:
        with TestClient(create_app(settings, db=db)) as client:
            def request(path="", user="admin"):
                auth = {"Authorization": f"Bearer {make_dev_token(secret, user+'@example.test')}"}
                return client.get(f"/api/findings{path}", headers=auth)

            today = request("?scope=today&days=14").json()
            assert [r["id"] for r in today["items"][:2]] == [open_event, later_event]
            seen = {i for r in today["items"] for i in r["event_ids"]}
            assert seen == {open_event, later_event, first, mirror, intl, nord, de, limited}
            assert today["items"][0]["tender"]["url"] == "https://bank.example/active-rfp"
            assert today["coverage"]["unhealthy"][0]["state"] == "stale"

            archive = request("?days=60").json()
            assert {i for r in archive["items"] for i in r["event_ids"]} == seen | {archive_only, weak}
            assert len([r for r in archive["items"] if first in r["event_ids"]]) == 1
            assert {r["organization"]["region"] for r in request("?region=dach").json()["items"]} == {"dach"}
            assert {r["id"] for r in request("?region=international").json()["items"]} == {intl}
            assert {r["id"] for r in request("?kind=tender").json()["items"]} == {open_event, later_event}
            assert open_event not in {r["id"] for r in request("?kind=signal").json()["items"]}
            hit = request(f"?event={first}").json()["items"]
            assert len(hit) == 1 and set(hit[0]["event_ids"]) == {first, mirror}
            assert hit[0]["summary_pl"].startswith("PL Core migration")
            assert request("?limit=2").json()["has_more"] is True
            assert request("?limit=2").json()["items"][0]["id"] != request("?limit=2&page=2").json()["items"][0]["id"]
            assert len(request("?q=core%20migration").json()["items"]) == 1

            account = request(f"?scope=account&organization={target}&event={first}").json()
            assert account["items"][0]["event_ids"] == hit[0]["event_ids"]
            assert {lead["claimed_text"] for lead in account["leads"]} == {"Workbook lead", "Pending lead"}
            assert next(lead for lead in account["leads"] if lead["status"] == "verified")["event_id"] == first
            wrong = request(f"?scope=account&organization={nordic}").json()["leads"]
            assert len(wrong) == 1 and wrong[0]["event_id"] is None
            assert request(f"?scope=account&organization={restricted}", "seller").status_code == 404
            assert limited not in {i for r in request("?scope=today", "seller").json()["items"] for i in r["event_ids"]}
            assert request("?scope=today", "marketing").status_code == 403
            assert request("?scope=account").status_code == 422

            amended, _ = notice("active-rfp", target, status="amended", offset=4)
            conn.commit()
            new = request("?scope=today&kind=tender").json()["items"]
            assert [r["id"] for r in new] == [amended, later_event]
            assert new[0]["tender"]["status"] == "amended"
            conn.execute("UPDATE procurement_notices SET status='cancelled' WHERE id=%s", (open_notice,))
            conn.commit()
            assert [r["id"] for r in request("?scope=today&kind=tender").json()["items"]] == [later_event]
    finally:
        db.close()


def test_buyer_notice_is_promoted_only_from_an_anchored_first_party_invitation(conn):
    from ri.access import Principal
    from ri.events.extract import process_document
    from ri.findings import findings

    seed_registry(conn)
    org = conn.execute("""INSERT INTO organizations(display_name,canonical_domain,country_code,segment,
                          employees_estimate,company_ownership,company_profile_source_url)
                          VALUES ('Synthetic Bank','bank.example','GB','Banking',900,'private','https://bank.example/profile')
                          RETURNING id""").fetchone()["id"]
    future = (datetime.now(UTC) + timedelta(days=4)).strftime("%Y-%m-%dT15:00:00+00:00")
    scope = "Development of a payments platform"
    invitation = f"Submit bids by {future}"
    reference = "RFP-2026-E2E"

    class Extractor:
        version = "test"

        async def extract(self, **_):
            return {"events": [{"event_type": "procurement_published", "subject_name": "Synthetic Bank",
                                "summary": scope, "summary_pl": "Budowa platformy płatniczej",
                                "attrs": {"deadline_quote": invitation, "scope_quote": scope, "notice_reference": reference},
                                "claims": [{"predicate": "asks_for", "anchor": scope}]}]}

    for index, (host, original_quote) in enumerate((("news.example", invitation), ("bank.example", "A date will follow"),
                                                 ("bank.example", invitation))):
        url = f"https://{host}/tender/{index}"
        text = f"Synthetic Bank, {reference}. {scope}. {original_quote}."
        doc, _ = upsert_document(conn, "verify-company-page",
                                 SourceDocumentDraft(str(index), "1", url, scope, datetime.now(UTC), text, text), "excerpt")
        result = process_document(conn, doc, Extractor(), resolve_org=lambda _: str(org))
        assert result["events"] + result["linked_existing"] == 1
        row = conn.execute("SELECT event_id FROM event_documents WHERE document_id=%s", (doc,)).fetchone()
        verify_event(conn, str(row["event_id"]))

    principal = Principal(str(uuid4()), "admin@example.test", "admin", "", None)
    rows = findings(conn, principal, scope="today", days=14, limit=10)
    assert len(rows) == 1
    assert rows[0]["tender"]["url"] == "https://bank.example/tender/2"
    assert rows[0]["summary_pl"] == "Budowa platformy płatniczej"
