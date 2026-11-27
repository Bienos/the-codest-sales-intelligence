"""Was this useful? Votes on Today and Buying signals items: one per person per item, a reason when not useful, the rule and evidence filled
in by the server, the caller's scope respected, and the per-rule results the Library shows. Company names are invented."""
from test_r23_people_campaigns import market  # noqa: F401  (pytest fixture)

from ri.db import q1, qa


def _signal(conn, org):
    return q1(conn, "select s.id, s.rule_id, s.rule_version, s.event_id from signals s where s.organization_id=%s and s.rule_id not like 'NEG-%%' order by s.created_at limit 1", (org,))


def test_a_seller_answers_changes_and_takes_back_a_vote(conn, market):  # noqa: F811
    c, h, o = market["c"], market["h"], market["o"]
    s = _signal(conn, o["open"])
    key = f"signal:{o['open']}:{s['id']}"
    assert c.post("/api/feedback", json={"key": key, "useful": False}, headers=h("bdm")).status_code == 422          # "no" needs a reason
    r = c.post("/api/feedback", json={"key": key, "useful": False, "reason": "out_of_date", "shown_on": "signals"}, headers=h("bdm"))
    assert r.status_code == 200, r.text
    assert r.json() == {"key": key, "useful": False, "reason": "out_of_date"}
    row = q1(conn, "select * from signal_feedback where item_key=%s", (key,))
    assert (row["item_kind"], row["rule_id"], row["rule_version"], str(row["event_id"]), str(row["signal_id"]), row["model_version"], row["shown_on"]) == \
        ("signal", s["rule_id"], s["rule_version"], str(s["event_id"]), str(s["id"]), "test", "signals")
    assert c.get(f"/api/feedback/mine?keys={key},signal:x", headers=h("bdm")).json()["items"] == {key: {"useful": False, "reason": "out_of_date"}}
    # changing the answer keeps one row; a "yes" carries no reason
    assert c.post("/api/feedback", json={"key": key, "useful": True, "reason": "ghost_job"}, headers=h("bdm")).json() == {"key": key, "useful": True, "reason": None}
    assert q1(conn, "select count(*) n from signal_feedback")["n"] == 1
    assert {r["event"] for r in qa(conn, "select event from product_events")} >= {"signal_feedback"}
    assert c.delete(f"/api/feedback?key={key}", headers=h("bdm")).status_code == 200
    assert c.get(f"/api/feedback/mine?keys={key}", headers=h("bdm")).json()["items"] == {}


def test_votes_follow_the_callers_scope_and_unknown_items_are_refused(conn, market):  # noqa: F811
    c, h, o = market["c"], market["h"], market["o"]
    theirs = _signal(conn, o["theirs"])                     # another seller's account
    assert c.post("/api/feedback", json={"key": f"signal:{o['theirs']}:{theirs['id']}", "useful": True}, headers=h("bdm")).status_code == 404
    assert c.post("/api/feedback", json={"key": f"signal:{o['theirs']}:{theirs['id']}", "useful": True}, headers=h("bdm2")).status_code == 200
    for bad in ("signal:not-a-key", f"signal:{o['open']}:00000000-0000-0000-0000-000000000000", "reply:abc:20261127", "campaign:123"):
        assert c.post("/api/feedback", json={"key": bad, "useful": True}, headers=h("bdm")).status_code == 404, bad
    assert c.post("/api/feedback", json={"key": f"list:{o['open']}:row-7", "useful": False, "reason": "wrong_company"}, headers=h("bdm")).status_code == 200
    assert q1(conn, "select item_kind, rule_id from signal_feedback where item_key=%s", (f"list:{o['open']}:row-7",)) == {"item_kind": "lead_list", "rule_id": None}
    assert c.post("/api/feedback", json={"key": f"signal:{o['open']}:x", "useful": True}).status_code in (401, 403)


def test_the_library_shows_per_rule_results_without_names(conn, market):  # noqa: F811
    c, h, o = market["c"], market["h"], market["o"]
    s = _signal(conn, o["open"])
    key = f"signal:{o['open']}:{s['id']}"
    c.post("/api/feedback", json={"key": key, "useful": True}, headers=h("bdm"))
    c.post("/api/feedback", json={"key": key, "useful": False, "reason": "ghost_job"}, headers=h("mgr"))
    c.post("/api/feedback", json={"key": f"list:{o['open']}:row-1", "useful": False, "reason": "out_of_date"}, headers=h("bdm"))
    r = c.get("/api/feedback/by-rule", headers=h("bdm")).json()
    got = {i["key"]: i for i in r["items"]}
    rule = got[s["rule_id"]]
    assert (rule["votes"], rule["useful"], rule["not_useful"], rule["useful_share"], rule["people"], rule["reasons"]) == (2, 1, 1, 0.5, 2, {"ghost_job": 1})
    assert rule["name"] and got["lead_list"]["name"] == "Lead list research" and got["lead_list"]["reasons"] == {"out_of_date": 1}
    assert r["reasons"]["ghost_job"] and "bdm@x" not in str(r) and "mgr@x" not in str(r)
