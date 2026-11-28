"""Hiring signals from stored job ads: hard-to-fill roles (CAP-03, the daily lane), two roles in our stacks (CAP-04, a cluster that counts only
matching ads) and QA or reliability roles (CAP-06). Company names are invented; nothing here reads a job board."""
from datetime import datetime, timedelta, timezone

import pytest

from ri.db import q1, qa
from ri.events.structured import emit_job_event
from ri.ingest.process import finish_event
from ri.scheduler import run_maintenance
from ri.signals import hiring
from ri.signals.baseline import import_baseline
from ri.signals.persist import sync_rule_versions

NOW = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def org(conn):
    import_baseline(conn); sync_rule_versions(conn)
    oid = str(conn.execute("insert into organizations(display_name, country_code, segment, in_master_list) values ('Synthetic Fjord Bank','NO','Banking',true) returning id").fetchone()["id"])
    conn.commit()
    return oid


def ad(conn, org, job_id, title, *, posted_days=None, seen_days=1, first_days=None, open_status="open", closed_days=None, tech=(), etype="permanent"):
    posted = NOW - timedelta(days=posted_days) if posted_days is not None else None
    first = NOW - timedelta(days=first_days if first_days is not None else (posted_days if posted_days is not None else 1))
    closed = NOW - timedelta(days=closed_days) if closed_days is not None else None
    return str(conn.execute(
        "insert into job_postings(provider, board, job_id, organization_id, employer_name, title, employment_type, posted_at, open_status, closed_at, url, tech_keywords, "
        "first_seen_at, last_seen_at) values ('greenhouse','fjordbank',%s,%s,'Synthetic Fjord Bank',%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
        (job_id, org, title, etype, posted, open_status, closed, f"https://boards.example.invalid/fjordbank/{job_id}", list(tech), first, NOW - timedelta(days=seen_days))).fetchone()["id"])


def signals(conn, rule):
    return qa(conn, "select s.*, e.attrs, e.summary from signals s join events e on e.id=s.event_id where s.rule_id=%s order by e.summary", (rule,))


# ---------------------------------------------------------------- CAP-03: hard-to-fill roles
def test_the_lane_turns_long_open_and_reposted_roles_into_signals_once(conn, org):
    ad(conn, org, "1", "Senior Java Engineer, Payments", posted_days=80, tech=["java"])                       # open 80 days: hard to fill
    ad(conn, org, "2", "Backend Engineer", posted_days=10)                                                     # new: not yet
    ad(conn, org, "3", "Senior Java Developer (m/w/d)", posted_days=120, open_status="closed", closed_days=20)  # the first ad, closed
    ad(conn, org, "4", "Senior Java-Developer (f/m/x)", posted_days=5)                                         # the same role posted again
    ad(conn, org, "5", "Platform Engineer", posted_days=90, seen_days=12)                                     # no longer listed
    ad(conn, org, "6", "Talent Pool Software Engineering", posted_days=200)                                   # always open by design
    ad(conn, org, "7", "HR Business Partner", posted_days=200)                                                 # not an engineering role
    ad(conn, org, "9", "Senior Java Engineer, Core", posted_days=900)                                         # open 2.5 years: a standing ad
    conn.execute("insert into job_postings(provider, board, job_id, employer_name, title, posted_at, first_seen_at, last_seen_at) "
                 "values ('greenhouse','other','8','Unknown Co','Java Engineer',%s,%s,%s)", (NOW - timedelta(days=90), NOW - timedelta(days=90), NOW))   # no account
    conn.commit()

    out = hiring.emit_hard_to_fill(conn, NOW)
    conn.commit()
    assert out == {"hard_to_fill_events": 2, "hard_to_fill_signals": 2, "hard_to_fill_more": 0}
    got = {s["attrs"]["job_id"]: s for s in signals(conn, "CAP-03")}
    assert set(got) == {"1", "4"}
    assert got["1"]["attrs"]["days_open"] == 80 and not got["1"]["attrs"]["reposted"] and got["1"]["summary"].endswith("still open after 80 days.")
    assert got["4"]["attrs"]["reposted"] and got["4"]["attrs"]["previous_closed_at"] and "posted again" in got["4"]["summary"]
    assert got["1"]["demand_tier"] == "D3" and got["1"]["strength"] == 1
    a = got["1"]["attrs"]
    assert a["fit"]["service"] == "team_extension" and a["summary_pl"].endswith("otwarte od 80 dni.") and a["summary_pl_checked"]
    ev = q1(conn, "select verification_status, proven from events where event_type='job_hard_to_fill' and attrs->>'job_id'='1'")
    assert ev["verification_status"] == "verified" and ev["proven"]
    from ri.events.fit import pending
    assert pending(conn, 100) == [] or all(r["event_type"] != "job_hard_to_fill" for r in pending(conn, 100))     # no AI fit spend on these

    again = hiring.emit_hard_to_fill(conn, NOW + timedelta(days=1))
    assert again["hard_to_fill_events"] == 0 and len(signals(conn, "CAP-03")) == 2                        # one event per ad, ever


def test_a_closed_role_ends_its_hard_to_fill_signal(conn, org):
    rid = ad(conn, org, "1", "Senior Java Engineer", posted_days=70)
    conn.commit()
    hiring.emit_hard_to_fill(conn, NOW)
    conn.execute("update job_postings set open_status='closed', closed_at=%s where id=%s", (NOW, rid))
    eid = emit_job_event(conn, rid, "closed", now=NOW)
    finish_event(conn, eid, now=NOW)
    conn.commit()
    assert [s["status"] for s in signals(conn, "CAP-03")] == ["expired"]


def test_the_scheduler_runs_it_as_a_daily_lane(conn, org):
    ad(conn, org, "1", "Senior Java Engineer", posted_days=70)
    conn.commit()
    run_maintenance(conn, NOW)
    lane = q1(conn, "select * from scheduler_lanes where lane='hard_to_fill_roles'")
    assert lane["last_error"] is None and lane["last_result"]["hard_to_fill_events"] == 1
    run_maintenance(conn, NOW + timedelta(hours=3))                       # daily: not again three hours later
    assert q1(conn, "select runs from scheduler_lanes where lane='hard_to_fill_roles'")["runs"] == 1


def test_titles_compare_without_gender_tags_punctuation_or_case():
    assert hiring.norm_title("Senior Java Developer (m/w/d)") == hiring.norm_title("senior java-developer (f/m/x)") == "senior java developer"
    assert hiring.norm_title("QA Engineer (all genders)") == "qa engineer"


# ---------------------------------------------------------------- CAP-04 and CAP-06 on new ads
def _open(conn, org, job_id, title, tech, *, days=3):
    rid = ad(conn, org, job_id, title, posted_days=days, tech=tech)
    eid = emit_job_event(conn, rid, "new", now=NOW)          # None when the title is not an engineering role
    out = finish_event(conn, eid, now=NOW) if eid else None
    conn.commit()
    return out


def test_two_roles_in_our_stacks_make_one_signal_and_other_stacks_do_not_count(conn, org):
    _open(conn, org, "1", ".NET Developer", [".net", "azure"])
    _open(conn, org, "2", "Java Developer", ["java"])
    assert signals(conn, "CAP-04") == []                                  # one role in our stacks; the .NET role does not count
    _open(conn, org, "3", "Python Data Engineer", ["python"])
    got = signals(conn, "CAP-04")
    assert len(got) == 1 and got[0]["attrs"]["job_id"] == "3" and got[0]["demand_tier"] == "D3" and got[0]["strength"] == 2
    _open(conn, org, "4", "React Frontend Engineer", ["react", "typescript"])
    assert len(signals(conn, "CAP-04")) == 1                              # fires once, on the ad that completes the pair


def test_qa_and_reliability_roles_make_a_quality_signal(conn, org):
    _open(conn, org, "1", "QA Automation Engineer (m/w/d)", ["playwright"])
    _open(conn, org, "2", "Penetration Tester", [])
    got = signals(conn, "CAP-06")
    assert [s["attrs"]["job_id"] for s in got] == ["1"]


def test_the_new_rules_count_for_the_qa_and_team_extension_fit():
    from ri.solutions import RULE_SERVICE
    assert RULE_SERVICE["CAP-06"]["qa"] == 1.0 and RULE_SERVICE["CAP-03"]["team_extension"] > 0.5 and RULE_SERVICE["CAP-04"]["team_extension"] == 1.0
