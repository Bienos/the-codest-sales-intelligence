"""All findings: "not relevant" answers, the watchlist filter and the buying-signal chips (round 37)."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from test_r4_crm_notify import mk_user

from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.db import Database
from ri.events.store import add_claim, store_event
from ri.events.verify import verify_event
from ri.ingest.pipeline import upsert_document
from ri.sources.base import SourceDocumentDraft
from ri.sources.registry import seed_registry

SECRET = "x" * 32


def _seed(conn):
    seed_registry(conn)
    now = datetime.now(UTC)
    mk_user(conn, "admin@example.test", "admin")
    mk_user(conn, "other@example.test", "admin")
    mk_user(conn, "seller@example.test", "bdm", 700)
    mk_user(conn, "marketing@example.test", "marketing")

    def org(label, country="GB"):
        return str(conn.execute("""
            INSERT INTO organizations(display_name, country_code, company_ownership, employees_estimate, segment, entity_type,
                company_profile_source_url, canonical_domain)
            VALUES (%s,%s,'private',900,'Banking','company','https://bank.example/ownership','bank.example') RETURNING id
        """, (label, country)).fetchone()["id"])

    def finding(label, buyer, *, age=1, rule=None):
        doc, _ = upsert_document(conn, "procurement-ted", SourceDocumentDraft(uuid4().hex, "1", f"https://bank.example/{uuid4().hex}",
                                                                             label, now - timedelta(days=age), label, label), "excerpt")
        claim = add_claim(conn, subject=label, predicate="announced", obj={}, doc_id=doc, anchor=label, kind="quote", anchor_verified=True)
        at = now - timedelta(days=age)
        event, _ = store_event(conn, event_type="programme_announced", subject_name=label, subject_org_id=buyer, summary=label,
                               attrs={"dedup_key": label}, key_parts=(uuid4().hex,), event_at=at, pub_at=at, claim_ids=[claim], document_ids=[doc])
        verify_event(conn, event)
        if rule:
            conn.execute("""INSERT INTO signals(event_id, rule_id, rule_version, organization_id, strength, demand_tier, verification_status,
                            effective_from, reason_code) VALUES (%s,%s,'1',%s,2,'D3','verified',now(),'test')""", (event, rule, buyer))
        return str(event)

    bank, insurer, payments = org("Pilot Bank"), org("Harbour Insurance"), org("Isar Payments", "DE")
    ids = {
        "bank_new": finding("Pilot Bank appoints a new CTO", bank, age=1, rule="MAN-01"),
        "bank_old": finding("Pilot Bank moves its data to the cloud", bank, age=20, rule="PRG-02"),
        "insurer": finding("Harbour Insurance raises a funding round", insurer, age=3, rule="COM-01"),
        "payments": finding("Isar Payments raises a second funding round", payments, age=5, rule="COM-01"),
        "news_only": finding("Harbour Insurance opens an office", insurer, age=2),
    }
    conn.execute("INSERT INTO watchlist(organization_id, user_email) VALUES (%s,'admin@example.test')", (bank,))
    conn.commit()
    return ids, {"bank": bank, "insurer": insurer, "payments": payments}


def test_all_findings_dismiss_watchlist_and_kinds(conn, db_url):
    ids, orgs = _seed(conn)
    db = Database(db_url)
    settings = Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET, database_url=db_url)
    try:
        with TestClient(create_app(settings, db=db)) as client:
            def auth(user="admin"):
                return {"Authorization": f"Bearer {make_dev_token(SECRET, user + '@example.test')}"}

            def get(path="", user="admin"):
                return client.get(f"/api/findings?days=90&qualification=all{path}", headers=auth(user))

            page = get().json()
            assert [r["id"] for r in page["items"]] == [ids["bank_new"], ids["news_only"], ids["insurer"], ids["payments"], ids["bank_old"]]
            assert page["watchlist"] == 1 and page["hidden_count"] == 0
            assert [r["watched"] for r in page["items"]] == [True, False, False, False, True]
            assert all(r["dismissed"] is None for r in page["items"])
            # the chips: buying signals behind the findings in the window, most frequent first; news with no signal has no chip
            kinds = {k["rule_id"]: k["findings"] for k in page["kinds"]}
            assert kinds == {"COM-01": 2, "MAN-01": 1, "PRG-02": 1}
            assert page["kinds"][0]["rule_id"] == "COM-01"
            assert {k["rule_id"] for k in get("&region=dach").json()["kinds"]} == {"COM-01"}
            assert {k["rule_id"] for k in get("&watched=true").json()["kinds"]} == {"MAN-01", "PRG-02"}
            assert [k["rule_id"] for k in client.get("/api/findings?days=10&qualification=all", headers=auth()).json()["kinds"]] == ["COM-01", "MAN-01"]

            # only my watchlist: newest first, paged like the full list
            watched = get("&watched=true").json()
            assert [r["id"] for r in watched["items"]] == [ids["bank_new"], ids["bank_old"]]
            first = get("&watched=true&limit=1").json()
            assert [r["id"] for r in first["items"]] == [ids["bank_new"]] and first["has_more"] is True
            second = get("&watched=true&limit=1&page=2").json()
            assert [r["id"] for r in second["items"]] == [ids["bank_old"]] and second["has_more"] is False
            assert [r["id"] for r in get("&watched=true&rule=PRG-02").json()["items"]] == [ids["bank_old"]]
            assert get("&watched=true", "other").json()["items"] == []        # a watchlist is per person

            # "not relevant" hides it for this person only, on All findings and on Today, and can be undone
            r = client.post(f"/api/findings/{ids['insurer']}/dismiss", json={"reason": "wrong_entity"}, headers=auth())
            assert r.status_code == 200 and r.json() == {"dismissed": True, "reason": "wrong_entity"}
            after = get().json()
            assert ids["insurer"] not in {x["id"] for x in after["items"]} and after["hidden_count"] == 1
            assert {k["rule_id"]: k["findings"] for k in after["kinds"]}["COM-01"] == 1                  # the chips follow what is shown
            assert {k["rule_id"]: k["findings"] for k in get("&hidden=true").json()["kinds"]}["COM-01"] == 2
            assert ids["insurer"] not in {x["id"] for x in get("&scope=today").json()["items"]}
            assert ids["insurer"] in {x["id"] for x in get("", "other").json()["items"]}
            shown = {x["id"]: x["dismissed"] for x in get("&hidden=true").json()["items"]}
            assert shown[ids["insurer"]] == "wrong_entity" and shown[ids["bank_new"]] is None
            assert get(f"&event={ids['insurer']}").json()["items"][0]["dismissed"] == "wrong_entity"    # a direct link still opens it
            account = client.get(f"/api/findings?scope=account&organization={orgs['insurer']}&days=90", headers=auth()).json()
            assert ids["insurer"] in {x["id"] for x in account["items"]}                               # the company timeline keeps its history
            assert client.delete(f"/api/findings/{ids['insurer']}/dismiss", headers=auth()).json() == {"dismissed": False, "reason": None}
            assert ids["insurer"] in {x["id"] for x in get().json()["items"]} and get().json()["hidden_count"] == 0

            # every answer is kept as feedback and tracked
            answers = conn.execute("SELECT judgement, reason FROM feedback WHERE target_type='finding' ORDER BY created_at, id").fetchall()
            assert [(a["judgement"], a["reason"]) for a in answers] == [("not_relevant", "wrong_entity"), ("restored", None)]
            tracked = conn.execute("SELECT event FROM product_events WHERE event LIKE 'finding_%%' ORDER BY id").fetchall()
            assert [t["event"] for t in tracked] == ["finding_dismissed", "finding_restored"]

            # guards: unknown reasons, unknown findings and people without access
            assert client.post(f"/api/findings/{ids['bank_new']}/dismiss", json={"reason": "boring"}, headers=auth()).status_code == 422
            assert client.post(f"/api/findings/{uuid4()}/dismiss", json={}, headers=auth()).status_code == 404
            assert client.post(f"/api/findings/{ids['bank_new']}/dismiss", json={}, headers=auth("marketing")).status_code == 403
            assert client.post(f"/api/findings/{ids['bank_new']}/dismiss", json={}, headers=auth("seller")).status_code == 200
    finally:
        db.close()
