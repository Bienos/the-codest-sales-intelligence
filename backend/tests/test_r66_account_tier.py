"""One account tier (ri/tiers.py): lead-list fit crossed with timing, the same on Accounts, Buying signals, Today and the account page."""
from fastapi.testclient import TestClient
from test_r23_people_campaigns import market  # noqa: F401  (pytest fixture)

from ri.tiers import MATRIX, tier, tiers_for


def test_the_matrix_and_the_reasons():
    assert tier("P1", "warm")["n"] == 1 and tier("P1", None)["n"] == 2 and tier("P2", "hot")["n"] == 1 and tier("P2", "warm")["n"] == 2
    assert tier("P3", "hot")["n"] == 2 and tier("P3", "watch")["n"] == 4 and tier(None, "hot")["n"] == 3 and tier("X", None)["n"] == 4
    t = tier("P2", None, "in_talks")
    assert t["n"] == 1 and t["timing"] == "now" and t["why"] == "P2 on your lead list · in talks with us" and t["label"] == "Act now"
    s = tier("P1", "hot", stopped=True)
    assert s["n"] == 4 and s["timing"] == "stopped" and "stop or wait" in s["why"]
    assert {n for row in MATRIX.values() for n in row.values()} == {1, 2, 3, 4}


def test_lists_and_the_account_page_carry_the_same_tier(conn, market):  # noqa: F811
    c, h, o = market["c"], market["h"], market["o"]
    conn.execute("update organizations set list_priority='P1', in_master_list=true, list_eligibility='Eligible' where id=%s", (o["open"],))
    conn.commit()
    want = tiers_for(conn, [o["open"]])[o["open"]]
    assert want["fit"] == "P1"
    leads = {r["id"]: r for r in c.get("/api/leads", headers=h("bdm")).json()["items"]}
    signals = c.get("/api/buying-signals", headers=h("bdm")).json()
    account = c.get(f"/api/accounts/{o['open']}", headers=h("bdm")).json()
    got = [leads[o["open"]]["tier"], next(x["organization"]["tier"] for x in signals["companies"] if x["organization"]["id"] == o["open"]), account["tier"]]
    assert all(g == want for g in got), got
    ordered = [r["tier"]["n"] for r in c.get("/api/leads?sort=tier", headers=h("bdm")).json()["items"]]
    assert ordered == sorted(ordered)
