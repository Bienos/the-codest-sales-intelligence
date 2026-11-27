"""The conversations timeline on the person page and the company page: same permissions and row scope as those pages, an owner-only email shows THAT it
happened but never its subject or snippet to anybody else (administrators included), erased and suppressed people return nothing.
All names and addresses are invented."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.connections import store
from ri.db import Database
from test_r4_crm_notify import mk_user

SECRET = "s" * 40
NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
SUBJECT, SNIPPET = "Re: Secret pricing for the pilot", "We can go to 40 percent off if you sign this week"
BOBS_SUBJECT = "Bob's private subject line"


def settings(**kw):
    return Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET, approval_ttl_hours=72, **kw)


def org(conn, name, domain, *, owner=None):
    oid = str(conn.execute("insert into organizations(display_name, canonical_domain, country_code, segment, in_master_list) values (%s,%s,'GB','Payments',true) returning id",
                           (name, domain)).fetchone()["id"])
    if owner is not None:
        crm = 100 + conn.execute("select count(*) n from crm_organizations").fetchone()["n"]
        conn.execute("insert into crm_organizations(crm_id, organization_id, name, owner_id, link_status) values (%s,%s,%s,%s,'linked')", (crm, oid, name, owner))
    return oid


def person(conn, oid, name, email, *, suppressed=False, erased=False):
    pid = str(conn.execute("insert into people(full_name, work_email, suppressed, erased_at) values (%s,%s,%s,%s) returning id",
                           (name, email, suppressed, NOW if erased else None)).fetchone()["id"])
    conn.execute("insert into employment(person_id, organization_id, title) values (%s,%s,'CTO')", (pid, oid))
    return pid


def row(conn, user, channel, ext, when, *, person=None, org=None, visibility="team", direction=None, title=None, gist=None):
    store.upsert_interaction(conn, user_id=user, channel=channel, external_id=ext, occurred_at=when, visibility=visibility, person_id=person, organization_id=org,
                             direction=direction, title=title, gist=gist)


@pytest.fixture()
def env(conn, db_url):
    u = {"ann": mk_user(conn, "ann@x", "bdm", 555), "bob": mk_user(conn, "bob@x", "bdm", 666), "admin": mk_user(conn, "admin@x", "admin"),
         "analyst": mk_user(conn, "analyst@x", "research_analyst"), "comp": mk_user(conn, "comp@x", "compliance")}
    conn.execute("update users set display_name='Ann Example' where email='ann@x'")
    conn.execute("update users set display_name='Bob Example' where email='bob@x'")
    o = {"open": org(conn, "Kestrel Pay Ltd", "kestrel.example"), "other": org(conn, "Lantern Bank", "lantern.example"), "bobs": org(conn, "Harbour Bank", "harbour.example", owner=666)}
    p = {"ada": person(conn, o["open"], "Ada Clarke", "ada@kestrel.example"), "dan": person(conn, o["open"], "Dan Nowak", "dan@kestrel.example"),
         "gus": person(conn, o["open"], "Gus Suppressed", "gus@kestrel.example", suppressed=True), "hal": person(conn, o["open"], "Hal Erased", "hal@kestrel.example", erased=True),
         "fay": person(conn, o["bobs"], "Fay Dahl", "fay@harbour.example")}
    ann, bob = u["ann"], u["bob"]
    row(conn, ann, "email", "a1", NOW - timedelta(days=2), person=p["ada"], visibility="owner", direction="out", title=SUBJECT, gist=SNIPPET)
    row(conn, ann, "email", "a2", NOW - timedelta(days=9), person=p["ada"], visibility="team", direction="in", title="Intro call", gist="Thanks for the intro")
    row(conn, bob, "email", "b1", NOW - timedelta(days=1), person=p["ada"], visibility="owner", direction="in", title=BOBS_SUBJECT, gist="Bob's snippet")
    row(conn, bob, "lemlist", "l1", NOW - timedelta(days=30), person=p["ada"], direction="out", title="Payments rail Q4", gist="Sent")
    row(conn, ann, "meeting", "m1:" + p["ada"], NOW - timedelta(days=5), person=p["ada"], org=o["open"], direction="both", title="Pilot review", gist="Agreed to scope a pilot")
    row(conn, ann, "meeting", "m1:" + p["dan"], NOW - timedelta(days=5), person=p["dan"], org=o["open"], direction="both", title="Pilot review", gist="Agreed to scope a pilot")
    row(conn, bob, "meeting", "m2", NOW - timedelta(days=60), org=o["open"], direction="both", title="Account kick-off", gist="No contact matched")
    row(conn, ann, "email", "s1", NOW, person=p["gus"], visibility="team", title="About a suppressed person", gist="x")
    row(conn, ann, "email", "e1", NOW, person=p["hal"], visibility="team", title="About an erased person", gist="x")
    row(conn, bob, "email", "o1", NOW, person=p["ada"], org=o["other"], visibility="team", title="From her time at Lantern", gist="x")           # matched to another company
    row(conn, bob, "email", "f1", NOW - timedelta(days=3), person=p["fay"], visibility="team", direction="out", title="Harbour intro", gist="Hello Fay")
    conn.commit()
    db = Database(db_url, max_size=3)
    hdr = lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"}  # noqa: E731
    yield {"c": TestClient(create_app(settings(), db=db)), "h": hdr, "u": u, "o": o, "p": p, "conn": conn}
    db.close()


def person_url(env, who):
    return f"/api/people/{env['p'][who]}/conversations"


def account_url(env, which):
    return f"/api/accounts/{env['o'][which]}/conversations"


# ------------------------------------------------------------------ the person page
def test_the_person_timeline_has_the_contract_shape_newest_first_with_a_summary(env):
    r = env["c"].get(person_url(env, "ada"), headers=env["h"]("ann"))
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"summary", "items"}
    assert [i["channel"] for i in body["items"]] == ["email", "email", "email", "meeting", "email", "lemlist"]             # o1, b1, a1, m1, a2, l1: newest first
    times = [i["occurred_at"] for i in body["items"]]
    assert times == sorted(times, reverse=True)                                                  # all six are Ada's rows, whatever company they were matched to
    for i in body["items"]:
        assert set(i) == {"id", "channel", "occurred_at", "direction", "title", "gist", "owner", "mine", "detail_hidden", "person"}
        assert set(i["owner"]) == {"name", "email"} and i["person"] == {"id": env["p"]["ada"], "name": "Ada Clarke"} and i["channel"] in ("email", "lemlist", "meeting")
    s = body["summary"]
    assert set(s) == {"last_contact_at", "last_contact_by", "emails", "meetings", "campaign_events"}
    assert (s["emails"], s["meetings"], s["campaign_events"]) == (4, 1, 1)
    assert s["last_contact_at"] == NOW.isoformat() and s["last_contact_by"] == {"name": "Bob Example", "email": "bob@x"}


def test_an_owner_only_email_shows_that_it_happened_but_never_its_text_to_anybody_else(env):
    for who in ("ann", "bob", "admin"):
        r = env["c"].get(person_url(env, "ada"), headers=env["h"](who))
        mine_ann = next(i for i in r.json()["items"] if i["owner"]["email"] == "ann@x" and i["direction"] == "out" and i["channel"] == "email")
        theirs_bob = next(i for i in r.json()["items"] if i["owner"]["email"] == "bob@x" and i["channel"] == "email" and i["direction"] == "in")
        if who == "ann":
            assert mine_ann["title"] == SUBJECT and mine_ann["gist"] == SNIPPET and mine_ann["detail_hidden"] is False and mine_ann["mine"] is True
            assert theirs_bob["title"] is None and theirs_bob["gist"] is None and theirs_bob["detail_hidden"] is True and theirs_bob["mine"] is False
            assert BOBS_SUBJECT not in r.text and "Bob's snippet" not in r.text
        elif who == "bob":
            assert theirs_bob["title"] == BOBS_SUBJECT and theirs_bob["detail_hidden"] is False and theirs_bob["mine"] is True
            assert mine_ann["title"] is None and mine_ann["detail_hidden"] is True and SUBJECT not in r.text and SNIPPET not in r.text
        else:                                           # an administrator is nobody's owner: both hidden
            assert mine_ann["detail_hidden"] and theirs_bob["detail_hidden"] and mine_ann["title"] is None and theirs_bob["gist"] is None
            assert SUBJECT not in r.text and SNIPPET not in r.text and BOBS_SUBJECT not in r.text and "Bob's snippet" not in r.text
        assert mine_ann["owner"] == {"name": "Ann Example", "email": "ann@x"}                      # everybody still sees WHO and WHEN
        assert mine_ann["direction"] == "out" and mine_ann["occurred_at"] == (NOW - timedelta(days=2)).isoformat()


def test_team_visible_rows_show_their_text_to_everybody(env):
    for who in ("ann", "bob", "admin"):
        items = env["c"].get(person_url(env, "ada"), headers=env["h"](who)).json()["items"]
        meeting = next(i for i in items if i["channel"] == "meeting")
        lemlist = next(i for i in items if i["channel"] == "lemlist")
        intro = next(i for i in items if i["title"] == "Intro call")
        assert meeting["title"] == "Pilot review" and meeting["gist"] == "Agreed to scope a pilot" and meeting["detail_hidden"] is False and meeting["direction"] == "both"
        assert lemlist["title"] == "Payments rail Q4" and lemlist["gist"] == "Sent" and intro["gist"] == "Thanks for the intro"


def test_the_summary_counts_everything_while_the_list_stops_at_100_newest(env):
    conn = env["conn"]
    for i in range(105):
        row(conn, env["u"]["bob"], "lemlist", f"bulk{i}", NOW - timedelta(days=100) - timedelta(minutes=i), person=env["p"]["dan"], direction="out", title="Bulk", gist="Opened")
    conn.commit()
    body = env["c"].get(person_url(env, "dan"), headers=env["h"]("ann")).json()
    assert len(body["items"]) == 100 and body["summary"]["campaign_events"] == 105 and body["summary"]["meetings"] == 1
    times = [i["occurred_at"] for i in body["items"]]
    assert times == sorted(times, reverse=True)
    assert body["summary"]["last_contact_at"] == (NOW - timedelta(days=5)).isoformat() and body["summary"]["last_contact_by"]["email"] == "ann@x"


def test_nobody_found_gives_an_empty_summary(env):
    conn = env["conn"]
    lone = person(conn, env["o"]["open"], "Lee Lonely", "lee@kestrel.example")
    conn.commit()
    r = env["c"].get(f"/api/people/{lone}/conversations", headers=env["h"]("ann"))
    assert r.json() == {"summary": {"last_contact_at": None, "last_contact_by": None, "emails": 0, "meetings": 0, "campaign_events": 0}, "items": []}


def test_erased_and_suppressed_people_return_nothing(env):
    erased = env["c"].get(person_url(env, "hal"), headers=env["h"]("ann"))
    assert erased.status_code == 404 and "erased" not in erased.text.lower()
    r = env["c"].get(person_url(env, "gus"), headers=env["h"]("ann"))
    assert r.status_code == 200 and r.json()["items"] == [] and r.json()["summary"]["emails"] == 0 and "suppressed person" not in r.text


def test_the_person_timeline_follows_the_person_pages_scope_and_permissions(env):
    # Bob owns Harbour Bank, so Ann cannot see Fay at all: not found, not forbidden
    assert env["c"].get(person_url(env, "fay"), headers=env["h"]("ann")).status_code == 404
    assert env["c"].get(person_url(env, "fay"), headers=env["h"]("bob")).status_code == 200
    assert env["c"].get(person_url(env, "fay"), headers=env["h"]("admin")).status_code == 200
    assert env["c"].get("/api/people/not-a-uuid/conversations", headers=env["h"]("ann")).status_code == 404
    assert env["c"].get("/api/people/00000000-0000-0000-0000-000000000000/conversations", headers=env["h"]("ann")).status_code == 404
    assert env["c"].get(person_url(env, "ada"), headers=env["h"]("analyst")).status_code == 403      # no contact permission
    assert env["c"].get(person_url(env, "ada"), headers=env["h"]("comp")).status_code == 403
    assert env["c"].get(person_url(env, "ada")).status_code == 401


def test_reading_a_timeline_is_audited_without_any_text(env):
    env["c"].get(person_url(env, "ada"), headers=env["h"]("ann"))
    env["c"].get(account_url(env, "open"), headers=env["h"]("ann"))
    rows = env["conn"].execute("select actor, operation, record_type, detail from audit_events where operation='pii_read' order by id").fetchall()
    assert [(r["actor"], r["record_type"]) for r in rows] == [("ann@x", "person"), ("ann@x", "organization_conversations")]
    assert SUBJECT not in str(rows) and SNIPPET not in str(rows)


# ------------------------------------------------------------------ the company page
def test_the_company_timeline_joins_its_people_and_rows_matched_to_the_company(env):
    r = env["c"].get(account_url(env, "open"), headers=env["h"]("ann"))
    assert r.status_code == 200
    body = r.json()
    titles = {i["title"] for i in body["items"] if not i["detail_hidden"]}
    assert {"Intro call", "Pilot review", "Account kick-off", "Payments rail Q4"} <= titles          # people's rows, company-matched meetings, a Lemlist event
    assert "From her time at Lantern" not in titles                                                  # matched to another company: not here
    assert "About a suppressed person" not in titles and "About an erased person" not in titles
    assert "Harbour intro" not in titles                                                              # another company's person
    kickoff = next(i for i in body["items"] if i["title"] == "Account kick-off")
    assert kickoff["person"] is None and kickoff["owner"]["email"] == "bob@x"                         # matched to the company only
    times = [i["occurred_at"] for i in body["items"]]
    assert times == sorted(times, reverse=True)
    assert BOBS_SUBJECT not in r.text and "Bob's snippet" not in r.text and SNIPPET in r.text         # Ann sees her own text, not Bob's owner-only text


def test_a_meeting_with_two_known_attendees_is_one_meeting_on_the_company_page(env):
    body = env["c"].get(account_url(env, "open"), headers=env["h"]("ann")).json()
    reviews = [i for i in body["items"] if i["title"] == "Pilot review"]
    assert len(reviews) == 1 and sorted(x["name"] for x in reviews[0]["people"]) == ["Ada Clarke", "Dan Nowak"] and reviews[0]["person"] is not None
    assert body["summary"]["meetings"] == 2                                                           # the review and the kick-off
    on_the_person_page = env["c"].get(person_url(env, "ada"), headers=env["h"]("ann")).json()["items"]
    assert len([i for i in on_the_person_page if i["channel"] == "meeting"]) == 1


def test_the_company_summary_counts_and_last_contact(env):
    s = env["c"].get(account_url(env, "open"), headers=env["h"]("ann")).json()["summary"]
    assert (s["emails"], s["meetings"], s["campaign_events"]) == (3, 2, 1)                            # a1, a2, b1 | review, kick-off | l1
    assert s["last_contact_at"] == (NOW - timedelta(days=1)).isoformat() and s["last_contact_by"] == {"name": "Bob Example", "email": "bob@x"}


def test_the_company_timeline_hides_owner_only_text_from_administrators_too(env):
    r = env["c"].get(account_url(env, "open"), headers=env["h"]("admin"))
    assert r.status_code == 200 and SUBJECT not in r.text and SNIPPET not in r.text and BOBS_SUBJECT not in r.text
    hidden = [i for i in r.json()["items"] if i["detail_hidden"]]
    assert len(hidden) == 2 and all(i["title"] is None and i["gist"] is None and i["channel"] == "email" for i in hidden)


def test_the_company_timeline_follows_the_company_pages_scope_and_permissions(env):
    assert env["c"].get(account_url(env, "bobs"), headers=env["h"]("ann")).status_code == 404
    body = env["c"].get(account_url(env, "bobs"), headers=env["h"]("bob")).json()
    assert [i["title"] for i in body["items"]] == ["Harbour intro"]
    assert env["c"].get("/api/accounts/not-a-uuid/conversations", headers=env["h"]("ann")).status_code == 404
    assert env["c"].get(account_url(env, "open"), headers=env["h"]("analyst")).status_code == 403    # sees companies but not people: the timeline names people
    assert env["c"].get(account_url(env, "open"), headers=env["h"]("comp")).status_code == 403
    assert env["c"].get(account_url(env, "open")).status_code == 401


def test_a_company_with_no_contacts_returns_an_empty_timeline(env):
    r = env["c"].get(account_url(env, "other"), headers=env["h"]("ann"))
    assert r.status_code == 200 and [i["title"] for i in r.json()["items"]] == ["From her time at Lantern"]
    conn = env["conn"]
    empty = org(conn, "Quiet Co", "quiet.example")
    conn.commit()
    assert env["c"].get(f"/api/accounts/{empty}/conversations", headers=env["h"]("ann")).json()["items"] == []


def test_the_timeline_queries_use_the_person_and_company_indexes(env):
    conn = env["conn"]
    conn.execute("SET enable_seqscan = off")
    plan = lambda sql, params: "\n".join(next(iter(r.values())) for r in conn.execute("EXPLAIN " + sql, params).fetchall())  # noqa: E731
    person_plan = plan("SELECT ci.id FROM contact_interactions ci WHERE ci.person_id = %s ORDER BY ci.occurred_at DESC LIMIT 100", [env["p"]["ada"]])
    org_plan = plan("SELECT ci.id FROM contact_interactions ci WHERE (ci.organization_id = %s OR ci.person_id = ANY(%s::uuid[])) "
                    "AND (ci.organization_id IS NULL OR ci.organization_id = %s) ORDER BY ci.occurred_at DESC LIMIT 100", [env["o"]["open"], [env["p"]["ada"]], env["o"]["open"]])
    conn.execute("RESET enable_seqscan")
    assert "contact_interactions_person_idx" in person_plan and "Seq Scan" not in person_plan
    assert "contact_interactions_org_idx" in org_plan and "contact_interactions_person_idx" in org_plan
