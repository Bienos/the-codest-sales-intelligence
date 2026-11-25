"""Campaigns redesign: the list before a draft exists, the four stages (draft, approval, Lemlist, running/done) and the results read back from Lemlist.
Nothing here calls Lemlist: pushes go through the fake client, activity is written the way the hourly sync writes it."""
import json

from test_r14_outbound import FakeLemlist
from test_r23_people_campaigns import market, settings  # noqa: F401  (pytest fixture)

from ri.db import q1
from ri.outbound import signal_campaigns as sc
from ri.people import apply_lemlist_activity


def activity(conn, aid, typ, email, campaign="cam_1"):
    conn.execute("insert into lemlist_activities(id, type, email, campaign_id, happened_at) values (%s,%s,%s,%s,now())", (aid, typ, email, campaign))
    conn.commit()


def test_preview_shows_the_list_a_new_campaign_starts_with_and_saves_nothing(conn, market):  # noqa: F811
    c, h = market["c"], market["h"]
    before = q1(conn, "select count(*) n from signal_campaigns")["n"]
    r = c.post("/api/signal-campaigns/preview", json={"rule_ids": ["COM-01", "PRG-06"], "per_account": 2}, headers=h("bdm"))
    assert r.status_code == 200, r.text
    d = r.json()
    # Kestrel, Northbridge, Fenwick and Quiet Bank have a live signal; Harbour is another seller's, Old News is too old
    assert (d["companies"], d["reachable"], d["people"], d["left_out"]) == (4, 3, 4, 1)
    names = {p["name"] for i in d["items"] for p in i["people"]}
    assert names == {"Ada Clarke", "Ben Weber", "Dan Nowak", "Hal Sousa"}                       # technology leaders first, HR and the opted-out CIO not
    fenwick = next(i for i in d["items"] if i["company"] == "Fenwick")
    assert fenwick["people"] == [] and "open deal" in fenwick["reason"]
    assert d["items"][-1]["company"] == "Fenwick"                                                # companies with someone to write to come first
    kestrel = next(i for i in d["items"] if i["company"] == "Kestrel Pay")
    assert "Series B" in kestrel["hook"] and kestrel["signal"]["rule_id"] == "COM-01"
    assert "Pipedrive" not in r.text and "@" not in r.text
    assert q1(conn, "select count(*) n from signal_campaigns")["n"] == before                  # a preview saves nothing

    # the draft it creates has exactly those people
    cid = c.post("/api/signal-campaigns", json={"rule_ids": ["COM-01", "PRG-06"], "per_account": 2}, headers=h("bdm")).json()["id"]
    det = c.get(f"/api/signal-campaigns/{cid}", headers=h("bdm")).json()
    assert {m["name"] for m in det["members"] if m["included"]} == names
    assert det["summary"] == {"companies_with_signal": 4, "companies_in": 3, "companies_without_people": 1, "people_in": 4, "people_left_out": 0}

    assert c.post("/api/signal-campaigns/preview", json={"rule_ids": ["COM-01", "PRG-06"], "per_account": 1}, headers=h("bdm")).json()["people"] == 3
    assert c.post("/api/signal-campaigns/preview", json={"rule_ids": ["COM-01"], "region": "dach"}, headers=h("bdm")).json()["companies"] == 0
    assert c.post("/api/signal-campaigns/preview", json={"rule_ids": ["XXX-99"]}, headers=h("bdm")).status_code == 422


def test_exclusion_reasons_never_name_the_crm(conn, market):  # noqa: F811
    c, h = market["c"], market["h"]
    # the administrator sees every account, so Northbridge (another seller's) and Fenwick (open deal) are both left out with a reason
    cid = c.post("/api/signal-campaigns", json={"rule_ids": ["COM-01", "PRG-06"]}, headers=h("admin")).json()["id"]
    det = c.get(f"/api/signal-campaigns/{cid}", headers=h("admin")).json()
    reasons = [m["exclusion"] for m in det["members"] if m["exclusion"]]
    assert any("another seller" in r for r in reasons) and any("open deal" in r for r in reasons)
    assert "Pipedrive" not in json.dumps(det["members"])
    assert "Pipedrive" not in c.post("/api/signal-campaigns/preview", json={"rule_ids": ["COM-01", "PRG-06"]}, headers=h("admin")).text


def test_a_campaign_moves_through_the_stages_and_shows_what_came_back_from_lemlist(conn, market):  # noqa: F811
    c, h = market["c"], market["h"]
    cid = c.post("/api/signal-campaigns", json={"rule_ids": ["COM-01"], "per_account": 2}, headers=h("bdm")).json()["id"]
    item = lambda who="bdm": next(i for i in c.get("/api/signal-campaigns", headers=h(who)).json()["items"] if i["id"] == cid)  # noqa: E731
    detail = lambda who="bdm": c.get(f"/api/signal-campaigns/{cid}", headers=h(who)).json()  # noqa: E731

    it = item()
    assert (it["stage"], it["status"], it["review_passed"], it["issues"]) == ("draft", "draft", True, 0)
    assert (it["people"], it["accounts"], it["accounts_with_signal"], it["accounts_without_contact"]) == (3, 2, 3, 1)
    assert it["results"]["in_lemlist"] == 0 and not it["can_approve"]

    bad = c.put(f"/api/signal-campaigns/{cid}/steps", json={"steps": [{"day": 0, "subject": "Hi", "body": "We are world-class! Contact us."}]}, headers=h("bdm")).json()
    assert not bad["review"]["passed"] and item()["issues"] > 0 and not item()["review_passed"]
    ok = [{k: s[k] for k in ("day", "subject", "body")} | {"claims_used": s.get("claims_used", [])} for s in sc.DEFAULT_STEPS]
    assert c.put(f"/api/signal-campaigns/{cid}/steps", json={"steps": ok}, headers=h("bdm")).json()["review"]["passed"]

    aid = c.post(f"/api/signal-campaigns/{cid}/request-approval", headers=h("bdm")).json()["approval_id"]
    assert item()["stage"] == "approval" and item("mgr")["can_approve"] and not item()["can_approve"]
    assert detail("mgr")["can_approve"] and not detail()["can_approve"] and not detail()["can_send"]
    assert detail()["approval"]["requested_by"] == "bdm@x" and detail()["approval"]["approved_people"] == 3

    assert c.post(f"/api/approvals/{aid}/decision", json={"approve": True}, headers=h("mgr")).status_code == 200
    d = detail()
    assert (d["campaign"]["status"], d["campaign"]["stage"], d["can_send"]) == ("approved", "lemlist", True)       # next step: send it to Lemlist
    assert c.get(f"/api/signal-campaigns/{cid}", headers=h("bdm2")).status_code == 404                            # another seller cannot open it

    out = sc.execute_push(conn, aid, FakeLemlist(), settings(outbound_enabled=True))
    conn.commit()
    assert out["leads"] == 3
    d = detail()
    assert (d["campaign"]["status"], d["campaign"]["stage"]) == ("pushed", "lemlist")                             # in Lemlist, waiting for Launch
    assert d["lemlist"]["campaign_name"] == "RI signals: Funding round" and d["lemlist"]["leads"] == 3 and d["lemlist"]["emails"] == 3
    assert d["results"]["in_lemlist"] == 3 and d["results"]["sent"] == 0 and not d["results"]["done"]

    # Launch pressed in Lemlist: the hourly sync brings the sends, opens and replies back
    activity(conn, "s1", "emailsSent", "ada@kestrel.example")
    activity(conn, "s2", "emailsSent", "ben@kestrel.example")
    activity(conn, "x1", "emailsSent", "ben@kestrel.example", campaign="another_campaign")                       # another Lemlist campaign does not count
    it = item()
    assert it["stage"] == "running" and it["results"]["sent"] == 2 and it["results"]["first_sent_at"]
    activity(conn, "o1", "emailsOpened", "ada@kestrel.example")
    apply_lemlist_activity(conn, {"_id": "r1", "type": "emailsReplied", "leadEmail": "ada@kestrel.example", "campaignId": "cam_1"})
    conn.commit()
    d = detail()
    ada = next(m for m in d["members"] if m["name"] == "Ada Clarke")
    ben = next(m for m in d["members"] if m["name"] == "Ben Weber")
    assert ada["engagement"] == "replied" and ben["engagement"] == "sent" and ben["emails_sent"] == 1
    assert (d["results"]["opened"], d["results"]["replied"], d["results"]["finished"]) == (1, 1, 1) and d["campaign"]["stage"] == "running"

    # everyone has had every email, replied, or bounced: done
    activity(conn, "s3", "emailsSent", "ben@kestrel.example")
    activity(conn, "s4", "emailsSent", "ben@kestrel.example")
    apply_lemlist_activity(conn, {"_id": "b1", "type": "emailsBounced", "leadEmail": "hal@quiet.example", "campaignId": "cam_1"})
    conn.commit()
    it = item()
    assert it["stage"] == "done" and it["results"]["done"] and it["results"]["bounced"] == 1 and it["results"]["sent"] == 3
    assert "@" not in json.dumps(detail()["members"])
