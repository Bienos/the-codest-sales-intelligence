"""Competitor tracker: the research seed, scores worked out from the rows, where we meet rivals (row-scoped), head-to-head, reviewing moves, and access."""
import json

import pytest
from fastapi.testclient import TestClient

from ri import competitors as ct
from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.crm import pipedrive as pd
from ri.db import Database

SECRET = "x" * 32


def _org(conn, name, owner=None, crm_id=None):
    oid = conn.execute("insert into organizations(display_name) values (%s) returning id", (name,)).fetchone()["id"]
    if crm_id:
        conn.execute("insert into crm_organizations(crm_id, organization_id, name, owner_id, link_status) values (%s,%s,%s,%s,'linked')", (crm_id, oid, name, owner))
    return str(oid)


def _user(conn, email, role, pd_id=None):
    conn.execute("insert into users(email, display_name, role, team, pipedrive_user_id) values (%s,%s,%s,'t1',%s)", (email, email.split("@")[0], role, pd_id))


def _vendor_intel(conn, org_id, vendors):
    conn.execute("insert into account_vendor_intel(organization_id, requested_by, status, content, finished_at) values (%s,'test','done',%s,now())",
                 (org_id, json.dumps({"vendors": vendors})))


# ------------------------------------------------------------------ the seed
def test_seed_loads_once_and_never_overwrites_a_persons_edit(conn):
    first = ct.load_seed(conn)
    assert first["competitors"] == 37 and first["moves"] >= 60 and first["market"] == 4         # 36 firms plus our own row
    assert ct.load_seed(conn)["moves"] == 0                                                     # nothing new the second time
    ct.update_profile(conn, "neontri", {"oneLiner": "Edited by hand."}, "mkt@x")
    conn.commit()
    ct.load_seed(conn)
    row = conn.execute("select profile, updated_by from competitors where key='neontri'").fetchone()
    assert row["profile"]["oneLiner"] == "Edited by hand." and row["updated_by"] == "mkt@x"
    seed = json.loads(ct.SEED_PATH.read_text(encoding="utf-8"))
    for c in [seed["us"], *seed["competitors"]]:                    # every move in the seed has its source, and medium/high moves a "So what"
        for m in c.get("moves") or []:
            assert m["source"].startswith("https://") and m["type"] in ct.MOVE_TYPES
            assert m["impact"] == 1 or m["sowhat"].strip()


def test_tiers_scores_and_patterns_are_worked_out_from_the_rows(conn):
    ct.load_seed(conn)
    t = ct.tracker(conn)
    comps = t["competitors"]
    assert len([c for c in comps if c["tier"] == 1]) == ct.TIER1_SIZE
    assert all(c["tier"] == 3 for c in comps if c["reference"])
    for c in comps:
        o = c["overlap"]
        assert o["total"] == o["buyers"] + o["selling"] + o["markets"] + o["size"] and 0 <= o["total"] <= 100
    t1_min = min(c["overlap"]["total"] for c in comps if c["tier"] == 1)
    assert all(c["overlap"]["total"] <= t1_min for c in comps if c["tier"] == 2)
    assert all("{" not in b["text"] and "{" not in b["action"] for b in t["briefing"]) and "{" not in t["lede"]      # every placeholder filled
    ind = t["indicators"]
    assert ind["fs2"]["of"] == len(comps) and ind["fs2"]["count"] == len([c for c in comps if c["fs"]["score"] >= 2])
    assert any(p.get("indicator", {}).get("count") for p in t["patterns"])
    assert {r["what"] for r in t["standing"]} >= {"Clutch reviews", "Our four markets covered", "Headcount"}
    # a change to the rows changes the scores at once: Boldare becomes a finance specialist in all our markets
    before = next(c for c in comps if c["key"] == "boldare")["overlap"]["total"]
    ct.update_profile(conn, "boldare", {"fs": {"score": 3, "note": "test"}, "markets": ["UK", "DACH", "Nordics", "PL"]}, "mkt@x")
    after = next(c for c in ct.tracker(conn)["competitors"] if c["key"] == "boldare")
    assert after["overlap"]["total"] == before + 30 + 20 and after["tier"] == 1


def test_profile_changes_keep_the_evidence_rules(conn):
    ct.load_seed(conn)
    with pytest.raises(ct.InvalidChange):
        ct.update_profile(conn, "neontri", {"headcount": {"value": "300", "n": 300}}, "mkt@x")                          # no source, grade or date
    with pytest.raises(ct.InvalidChange):
        ct.update_profile(conn, "neontri", {"secret_field": 1}, "mkt@x")
    with pytest.raises(ct.InvalidChange):
        ct.update_profile(conn, "neontri", {"pos": {"x": 12, "y": 3, "why": "x"}}, "mkt@x")
    ct.update_profile(conn, "neontri", {"headcount": {"value": "300", "n": 300, "source": "https://neontri.com/", "asOf": "2026-12", "grade": "official"}}, "mkt@x")
    ct.update_profile(conn, "polcode", {"retired": True}, "mkt@x")
    keys = {c["key"] for c in ct.tracker(conn)["competitors"]}
    assert "polcode" not in keys and "neontri" in keys


# ------------------------------------------------------------------ where we meet them
def test_presence_matches_names_and_respects_the_persons_accounts(conn):
    ct.load_seed(conn)
    _user(conn, "bdm@x", "bdm", 555)
    _user(conn, "bdm2@x", "bdm", 666)
    mine = _org(conn, "Kestrel Bank", owner=555, crm_id=1)
    theirs = _org(conn, "Other Bank", owner=666, crm_id=2)
    _vendor_intel(conn, mine, [{"name": "Software Mind S.A.", "category": "engineering_partner", "relationship": "current", "evidence": [{"source_url": "https://ex.example/a", "quote": "q"}]},
                               {"name": "Thought Machine", "category": "core_platform", "relationship": "current", "evidence": []}])
    _vendor_intel(conn, theirs, [{"name": "Avenga", "category": "engineering_partner", "relationship": "current", "evidence": []}])
    conn.execute("insert into awarded_contracts(notice_id, buyer_name, buyer_country, winners, awarded_on, expected_end, end_basis, relevant) "
                 "values ('n1','Urząd','PL','{\"Endava Ltd\",\"Someone Else\"}', '2025-01-01','2028-01-01','stated',true)")
    conn.commit()
    from ri.access import principal_from_row, org_scope_sql
    p = principal_from_row(conn.execute("select * from users where email='bdm@x'").fetchone())
    scope, args = org_scope_sql(p, "o.id")
    comps = ct.tracker(conn, scope, args)["competitors"]
    by = {c["key"]: c["presence"] for c in comps}
    assert by["software-mind"] == {"accounts": 1, "awards": 0}            # "Software Mind S.A." is Software Mind; Thought Machine is not tracked
    assert by["avenga"]["accounts"] == 0                                  # another seller's account stays out of sight
    assert by["endava"]["awards"] == 1                                    # "Endava Ltd" won a public contract
    admin_scope = ct.tracker(conn)["competitors"]
    assert {c["key"]: c["presence"]["accounts"] for c in admin_scope}["avenga"] == 1


def test_head_to_head_counts_and_win_rate_only_after_enough_decided_deals(conn):
    ct.load_seed(conn)
    for i, status in enumerate(["won", "won", "lost", "lost", "lost", "open"], start=1):
        conn.execute("insert into crm_deals(crm_id, title, status) values (%s,%s,%s)", (i, f"Deal {i}", status))
        ct.record_deal_competitor(conn, i, "software-mind", "unknown", "Price" if status == "lost" else None, "bdm@x")
    ct.record_deal_competitor(conn, 1, "neontri", "shortlisted", None, "bdm@x")
    h = ct.head_to_head(conn)
    sm, ne = h["by_competitor"]["software-mind"], h["by_competitor"]["neontri"]
    assert h["deals"] == 6 and sm["met"] == 6 and sm["won"] == 2 and sm["lost"] == 3 and sm["win_rate"] == 0.4 and sm["top_lost_reason"] == "Price"
    assert sm["share"] == 1.0 and sm["threat"] == 2.5
    assert ne["win_rate"] is None and ne["threat"] is None                # one decided deal: too few to show a rate


def test_a_pipedrive_deal_field_names_the_competitors(conn, monkeypatch):
    ct.load_seed(conn)
    monkeypatch.setenv("RI_PIPEDRIVE_COMPETITOR_FIELD", "abc123")
    pd.upsert_deal(conn, {"id": 77, "title": "Core banking team", "status": "lost", "lost_reason": "Went with a larger supplier", "abc123": "Software Mind"})
    pd.upsert_deal(conn, {"id": 78, "title": "Payments", "status": "open", "abc123": "Neontri; The Software House, Unknown Firm"})
    rows = {(r["crm_deal_id"], r["competitor_key"]): r for r in conn.execute("select * from deal_competitors")}
    assert rows[(77, "software-mind")]["role"] == "lost_to" and rows[(77, "software-mind")]["reason"] == "Went with a larger supplier"
    assert {k for d, k in rows if d == 78} == {"neontri", "the-software-house"}


# ------------------------------------------------------------------ the API
@pytest.fixture()
def api(conn, db_url):
    ct.load_seed(conn)
    for email, role, pid in (("bdm@x", "bdm", 555), ("bdm2@x", "bdm", 666), ("mkt@x", "marketing", None), ("comp@x", "compliance", None), ("admin@x", "admin", None)):
        _user(conn, email, role, pid)
    _org(conn, "Kestrel Bank", owner=555, crm_id=1)
    _org(conn, "Other Bank", owner=666, crm_id=2)
    conn.execute("insert into crm_deals(crm_id, crm_org_id, title, status) values (10, 1, 'Kestrel team', 'open'), (20, 2, 'Other deal', 'open')")
    conn.commit()
    db = Database(db_url, max_size=4)
    app = create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET), db=db)
    c = TestClient(app)
    yield c, (lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"})
    db.close()


def test_api_reading_and_access(api):
    c, h = api
    assert c.get("/api/competitors", headers=h("comp")).status_code == 403
    d = c.get("/api/competitors", headers=h("bdm")).json()
    assert len(d["competitors"]) == 36 and d["us"]["name"] == "The Codest" and d["can"] == {"manage": False, "record": True} and "waiting" not in d
    assert c.get("/api/competitors", headers=h("mkt")).json()["can"]["manage"] is True
    assert c.get("/api/competitors/software-mind/presence", headers=h("bdm")).json() == {"accounts": [], "awards": []}
    assert c.get("/api/competitors/nobody/presence", headers=h("bdm")).status_code == 404


def test_api_moves_are_suggested_then_reviewed(api):
    c, h = api
    move = {"date": "2026-11", "type": "pricing", "headline": "Publishes a day rate for AI pods", "source": "https://example.com/rates", "grade": "official", "impact": 3,
            "confirm": True}
    r = c.post("/api/competitors/neontri/moves", headers=h("bdm"), json=move).json()
    assert r["status"] == "candidate"                                     # a seller can suggest, not confirm
    assert c.post("/api/competitors/neontri/moves", headers=h("bdm"), json=move).status_code == 422          # already recorded
    assert c.get("/api/competitors/moves/waiting", headers=h("bdm")).status_code == 403
    waiting = c.get("/api/competitors/moves/waiting", headers=h("mkt")).json()["items"]
    assert [w["id"] for w in waiting] == [r["id"]]
    assert c.post(f"/api/competitors/moves/{r['id']}/review", headers=h("mkt"), json={"status": "confirmed"}).status_code == 422   # high impact needs a "So what"
    assert c.post(f"/api/competitors/moves/{r['id']}/review", headers=h("mkt"), json={"status": "confirmed", "sowhat": "Buyers will ask us for a pod price."}).status_code == 200
    neontri = next(x for x in c.get("/api/competitors", headers=h("bdm")).json()["competitors"] if x["key"] == "neontri")
    assert neontri["moves"][0]["headline"] == "Publishes a day rate for AI pods"
    bad = {**move, "headline": "Something else", "source": "http://insecure.example"}
    assert c.post("/api/competitors/neontri/moves", headers=h("mkt"), json=bad).status_code == 422


def test_api_deal_competitors_follow_the_sellers_accounts(api):
    c, h = api
    assert [d["id"] for d in c.get("/api/competitors/deals", headers=h("bdm")).json()["items"]] == [10]
    assert c.put("/api/competitors/deals/20", headers=h("bdm"), json={"competitor": "neontri", "role": "shortlisted"}).status_code == 404   # not their account
    assert c.put("/api/competitors/deals/10", headers=h("bdm"), json={"competitor": "codest", "role": "shortlisted"}).status_code == 422    # not a competitor
    assert c.put("/api/competitors/deals/10", headers=h("bdm"), json={"competitor": "neontri", "role": "shortlisted"}).status_code == 200
    assert c.get("/api/competitors/deals", headers=h("bdm")).json()["items"][0]["competitors"] == ["neontri"]
    assert c.get("/api/competitors", headers=h("bdm")).json()["headToHead"]["deals"] == 1
    assert c.put("/api/competitors/deals/10", headers=h("mkt"), json={"competitor": "neontri"}).status_code == 403
    assert c.delete("/api/competitors/deals/10/neontri", headers=h("bdm")).status_code == 200


def test_api_profile_edits_and_new_firms_are_audited(api, conn):
    c, h = api
    assert c.patch("/api/competitors/neontri", headers=h("bdm"), json={"oneLiner": "x"}).status_code == 403
    assert c.patch("/api/competitors/neontri", headers=h("mkt"), json={"headcount": {"value": "200"}}).status_code == 422
    ok = {"clutch": {"rating": 4.9, "reviews": 9, "rate": "$50–99/hr", "min": "$10,000+", "source": "https://clutch.co/profile/neontri", "asOf": "2026-12", "grade": "reported"}}
    assert c.patch("/api/competitors/neontri", headers=h("mkt"), json=ok).status_code == 200
    assert c.post("/api/competitors", headers=h("mkt"), json={"name": "Neontri", "origin": "PL", "url": "https://neontri.com/"}).status_code == 409
    r = c.post("/api/competitors", headers=h("mkt"), json={"name": "New Rival Labs", "origin": "PL", "url": "https://newrival.example/"})
    assert r.json()["key"] == "new-rival-labs"
    assert "new-rival-labs" in {x["key"] for x in c.get("/api/competitors", headers=h("bdm")).json()["competitors"]}
    ops = {r["operation"] for r in conn.execute("select operation from audit_events")}
    assert {"competitor_updated", "competitor_added"} <= ops


def test_scorecard_gives_every_comparison_the_same_numbers(conn):
    ct.load_seed(conn)
    d = ct.tracker(conn)
    us, t1 = d["us"]["score"], [c for c in d["competitors"] if c["tier"] == 1]
    assert us["markets"] == 4 and us["moves"] is None and set(us["flags"]) == {"security", "entry", "beyond_hours", "ai_badge"}
    assert all(0 <= c["score"]["proof"] <= 4 and c["score"]["proof"] == sum(c["score"]["flags"].values()) for c in d["competitors"])
    m = {x["key"]: x for x in d["measures"]}
    assert set(m) == {k for k, _, _ in ct.MEASURES} and m["fs"]["max"] == 3
    reviews = sorted(c["score"]["reviews"] for c in t1 if c["score"]["reviews"] is not None)
    assert m["reviews"]["t1"]["min"] == reviews[0] and m["reviews"]["t1"]["max"] == reviews[-1] and m["reviews"]["t1"]["n"] == len(reviews)
