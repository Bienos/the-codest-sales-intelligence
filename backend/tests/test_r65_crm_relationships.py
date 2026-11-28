"""Relationship with us from Pipedrive deals (lane ``crm_relationships``): linked accounts only, never over a person's choice, kept in step with
Pipedrive. Company names are invented."""
from datetime import datetime, timedelta, timezone

from ri.crm.relationships import decide, fill_from_pipedrive
from ri.db import q1
from ri.scheduler import LANE, run_maintenance

NOW = datetime(2026, 11, 28, 10, 0, tzinfo=timezone.utc)


def account(conn, name, crm_id, *, linked=True):
    oid = str(conn.execute("insert into organizations(display_name, country_code, in_master_list) values (%s,'GB',true) returning id", (name,)).fetchone()["id"])
    conn.execute("insert into crm_organizations(crm_id, organization_id, name, link_status) values (%s,%s,%s,%s)", (crm_id, oid, name, "linked" if linked else "review"))
    return oid


def deal(conn, crm_id, org_crm, status, days_ago=10, reason=None):
    when = NOW - timedelta(days=days_ago)
    conn.execute("insert into crm_deals(crm_id, crm_org_id, title, status, update_time, won_time, lost_time, lost_reason) values (%s,%s,'Deal',%s,%s,%s,%s,%s)",
                 (crm_id, org_crm, status, when, when if status == "won" else None, when if status == "lost" else None, reason))


def rel(conn, oid):
    return q1(conn, "select relationship, relationship_note, relationship_set_by from organizations where id=%s", (oid,))


def test_the_rule():
    old = NOW - timedelta(days=500)
    assert decide(1, 1, 0, old, None, None, NOW)[0] == "client"
    assert decide(1, 0, 0, NOW - timedelta(days=30), None, None, NOW)[0] == "client"
    assert decide(1, 0, 2, old, None, None, NOW)[0] == "past_client"
    assert decide(0, 2, 5, None, None, None, NOW)[0] == "in_talks"
    status, note = decide(0, 0, 2, None, NOW - timedelta(days=3), "Budget moved to next year", NOW)
    assert status == "prospect" and "Budget moved to next year" in note and note.startswith("From Pipedrive: 0 won, 0 open, 2 lost deals.")
    assert decide(0, 0, 0, None, None, None, NOW) is None


def test_linked_accounts_get_a_relationship_and_a_persons_choice_is_kept(conn):
    won = account(conn, "Won Bank", 1)
    talks = account(conn, "Talks Bank", 2)
    lost = account(conn, "Lost Bank", 3)
    mine = account(conn, "Person Set Bank", 4)
    review = account(conn, "Unconfirmed Bank", 5, linked=False)
    deal(conn, 11, 1, "won", 40); deal(conn, 12, 2, "open"); deal(conn, 13, 3, "lost", 5, "Chose an incumbent")
    deal(conn, 14, 4, "won"); deal(conn, 15, 5, "won")
    conn.execute("update organizations set relationship='prospect', relationship_set_by='seller@x' where id=%s", (mine,))
    out = fill_from_pipedrive(conn, NOW)
    assert out == {"relationships_set": 3, "relationships_changed": 0, "accounts_with_deals": 3}
    assert rel(conn, won)["relationship"] == "client" and rel(conn, won)["relationship_set_by"] == "pipedrive"
    assert rel(conn, talks)["relationship"] == "in_talks"
    assert rel(conn, lost)["relationship"] == "prospect" and "Chose an incumbent" in rel(conn, lost)["relationship_note"]
    assert rel(conn, mine)["relationship"] == "prospect" and rel(conn, mine)["relationship_set_by"] == "seller@x"      # a person's choice stays
    assert rel(conn, review)["relationship"] is None                                                                   # not linked yet
    # Pipedrive moves on: the open deal is won; the next run follows it, and a run with nothing new changes nothing
    conn.execute("update crm_deals set status='won', won_time=%s where crm_id=12", (NOW,))
    assert fill_from_pipedrive(conn, NOW)["relationships_changed"] == 1 and rel(conn, talks)["relationship"] == "client"
    assert fill_from_pipedrive(conn, NOW) == {"relationships_set": 0, "relationships_changed": 0, "accounts_with_deals": 3}


def test_it_runs_as_an_hourly_lane(conn):
    assert LANE["crm_relationships"].every_s == 3600
    oid = account(conn, "Lane Bank", 7)
    deal(conn, 17, 7, "open")
    run_maintenance(conn, NOW)
    assert rel(conn, oid)["relationship"] == "in_talks"
    assert q1(conn, "select last_error from scheduler_lanes where lane='crm_relationships'")["last_error"] is None
