"""R2 acceptance scenarios A-G (spec 05 §13) + review/lifecycle behaviour."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from ri.ingest.process import finish_event
from ri.initiatives import link as L
from ri.initiatives.lifecycle import refresh
from ri.initiatives.routes import route_summary
from ri.signals.baseline import import_baseline
from ri.signals.persist import sync_rule_versions
from ri.sources import registry

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def env(conn):
    import_baseline(conn); sync_rule_versions(conn); registry.seed_registry(conn)
    def org(name, cc="GB", parent=None):
        return str(conn.execute("insert into organizations(display_name,country_code,parent_id) values (%s,%s,%s) returning id", (name, cc, parent)).fetchone()["id"])
    bank = org("Example Bank")
    conn.commit()
    return {"org": org, "bank": bank, "n": [0]}


def mk_event(conn, env, org, etype, attrs, others=None, days_ago=5, group=None, summary="s"):
    env["n"][0] += 1
    attrs = {"source_tier": "S3", **attrs}
    row = conn.execute(
        "insert into events(event_key,event_type,subject_org_id,pub_at,event_at,event_at_known,summary,attrs,other_entity_roles,extraction_model_version,verification_status,independent_origin_group) "
        "values (%s,%s,%s,%s,%s,true,%s,%s,%s,'t','verified',%s) returning id",
        (f"k{env['n'][0]}", etype, org, NOW - timedelta(days=days_ago), NOW - timedelta(days=days_ago), summary, json.dumps(attrs), json.dumps(others or []), group or f"g{env['n'][0]}")).fetchone()["id"]
    c = conn.execute("insert into claims(subject,predicate,claim_kind,status) values ('x','p','structured','supported') returning id").fetchone()["id"]
    conn.execute("insert into event_claims(event_id,claim_id) values (%s,%s)", (row, c))
    return str(row)


CORE = {"programme_domain": "core_banking", "named_systems": ["Temenos Transact"], "has_dated_milestones": True, "programme_status": "implementation"}
OTHERS = [{"name": "Temenos", "role": "vendor"}, {"name": "GFT", "role": "si"}]


def test_A_bank_vendor_and_si_one_initiative_three_separate_routes(conn, env):
    e1 = mk_event(conn, env, env["bank"], "vendor_selected", {**CORE, "counterparty_selected": True, "programme_name": "Project Atlas"}, [OTHERS[0]])
    e2 = mk_event(conn, env, env["bank"], "vendor_selected", {**CORE, "counterparty_selected": True, "award_scope_includes_dev": True, "programme_name": "Project Atlas"}, [OTHERS[1]])
    r1, r2 = finish_event(conn, e1, now=NOW), finish_event(conn, e2, now=NOW)
    assert r1["initiative_id"] == r2["initiative_id"]
    assert conn.execute("select count(*) c from initiatives").fetchone()["c"] == 1
    roles = {(x["supplier_name"], x["supplier_role"]) for x in conn.execute("select supplier_name, supplier_role from supplier_edges")}
    assert roles == {("Temenos", "vendor"), ("GFT", "si")}
    routes = {x["route_type"]: x for x in route_summary(conn, r1["initiative_id"])}
    assert set(routes) == {"buyer_direct", "vendor_implementation", "prime_subcontract"}
    assert routes["buyer_direct"]["access_status"] == "restricted"                    # SI holds stated dev scope
    assert routes["vendor_implementation"]["access_status"] == "unknown"             # nothing is 'confirmed'
    assert routes["prime_subcontract"]["access_status"] == "unknown"
    assert not any(r["access_status"] == "verified" for r in routes.values())


def test_B_two_unrelated_cloud_programmes_same_bank_stay_two_initiatives(conn, env):
    a = mk_event(conn, env, env["bank"], "programme_announced", {"programme_domain": "cloud_data", "named_systems": ["AWS"], "programme_name": "Cloud Landing Zone"})
    b = mk_event(conn, env, env["bank"], "programme_announced", {"programme_domain": "cloud_data", "named_systems": ["Snowflake"], "programme_name": "Risk Data Lake"})
    finish_event(conn, a, now=NOW); finish_event(conn, b, now=NOW)
    assert conn.execute("select count(*) c from initiatives where status='active'").fetchone()["c"] == 2


def test_B2_unnamed_same_domain_without_overlap_creates_duplicate_review_not_merge(conn, env):
    a = mk_event(conn, env, env["bank"], "programme_announced", {"programme_domain": "cloud_data", "named_systems": ["AWS"]})
    b = mk_event(conn, env, env["bank"], "programme_announced", {"programme_domain": "cloud_data", "named_systems": ["Snowflake"]})
    finish_event(conn, a, now=NOW)
    r = finish_event(conn, b, now=NOW)
    assert conn.execute("select count(*) c from initiatives where status='active'").fetchone()["c"] == 2
    assert conn.execute("select count(*) c from initiative_reviews where status='open' and kind='possible_duplicate_initiative'").fetchone()["c"] == 1
    assert "analyst working title" in conn.execute("select title from initiatives order by created_at limit 1").fetchone()["title"]


def test_B3_unrelated_ai_initiatives_never_combined(conn, env):
    a = mk_event(conn, env, env["bank"], "programme_announced", {"programme_domain": "ai_use_case", "named_systems": ["doc-intelligence"]})
    b = mk_event(conn, env, env["bank"], "programme_announced", {"programme_domain": "ai_use_case", "named_systems": ["support-assistant"]})
    finish_event(conn, a, now=NOW); finish_event(conn, b, now=NOW)
    assert conn.execute("select count(*) c from initiatives").fetchone()["c"] == 2


def test_C_completion_updates_stage_and_bumps_revision_for_reevaluation(conn, env):
    e1 = mk_event(conn, env, env["bank"], "implementation_launched", {**CORE, "programme_name": "Project Atlas"}, [OTHERS[0]])
    r = finish_event(conn, e1, now=NOW)
    rev0 = conn.execute("select revision from initiatives").fetchone()["revision"]
    e2 = mk_event(conn, env, env["bank"], "programme_completed", {"programme_domain": "core_banking", "programme_status": "completed", "programme_name": "Project Atlas"}, days_ago=1)
    finish_event(conn, e2, now=NOW)
    i = conn.execute("select stage, revision from initiatives").fetchone()
    assert i["stage"] == "completed" and i["revision"] > rev0
    assert conn.execute("select count(*) c from initiative_stage_history where initiative_id=%s", (r["initiative_id"],)).fetchone()["c"] == 2


def test_C2_terminal_stage_not_reopened_by_stale_event_only_by_new_progress(conn, env):
    e1 = mk_event(conn, env, env["bank"], "programme_completed", {"programme_domain": "core_banking", "programme_status": "completed", "programme_name": "Atlas"}, days_ago=20)
    finish_event(conn, e1, now=NOW)
    e2 = mk_event(conn, env, env["bank"], "programme_announced", {"programme_domain": "core_banking", "programme_name": "Atlas", "programme_status": "announced"}, days_ago=10)
    finish_event(conn, e2, now=NOW)
    assert conn.execute("select stage from initiatives").fetchone()["stage"] == "completed"       # an 'announced' echo does not resurrect it
    e3 = mk_event(conn, env, env["bank"], "implementation_launched", {"programme_domain": "core_banking", "programme_name": "Atlas", "programme_status": "implementation"}, days_ago=1)
    finish_event(conn, e3, now=NOW)
    assert conn.execute("select stage from initiatives").fetchone()["stage"] == "implementation"   # new official progress reopens


def test_D_parent_announcement_subsidiary_procurement_no_shared_eligibility(conn, env):
    sub = env["org"]("Example Bank Poland", "PL", env["bank"])
    e1 = mk_event(conn, env, env["bank"], "vendor_selected", {**CORE, "counterparty_selected": True, "award_scope_includes_dev": True, "programme_name": "Group Core"}, [OTHERS[1]])
    e2 = mk_event(conn, env, sub, "procurement_published", {"notice_status": "open", "programme_domain": "core_banking", "programme_name": "Group Core",
                                                                 "submission_deadline": "2026-12-20T12:00:00+00:00"})
    r1, r2 = finish_event(conn, e1, now=NOW), finish_event(conn, e2, now=NOW)
    assert r1["initiative_id"] and r2["initiative_id"] and r1["initiative_id"] != r2["initiative_id"]                                      # different legal entities => different initiatives
    # the parent's supplier edge is scoped to the parent and is NOT inherited by the subsidiary
    assert conn.execute("select count(*) c from supplier_edges where buyer_org_id=%s", (sub,)).fetchone()["c"] == 0
    sub_routes = route_summary(conn, r2["initiative_id"])
    assert [x["access_status"] for x in sub_routes if x["route_type"] == "buyer_direct"] == ["unknown"]    # not 'restricted' by the parent's SI


def test_E_four_syndicated_articles_are_one_event_one_link(conn, env):
    e = mk_event(conn, env, env["bank"], "vendor_selected", {**CORE, "counterparty_selected": True, "programme_name": "Atlas"}, [OTHERS[0]], group="shared")
    for _ in range(4):
        finish_event(conn, e, now=NOW)
    assert conn.execute("select count(*) c from initiative_events").fetchone()["c"] == 1
    assert conn.execute("select count(*) c from initiatives").fetchone()["c"] == 1


def test_F_hiring_cluster_is_capacity_evidence_with_caveat_demand_stays_unconfirmed(conn, env):
    r = finish_event(conn, mk_event(conn, env, env["bank"], "implementation_launched", {**CORE, "programme_name": "Atlas"}, [OTHERS[0]]), now=NOW)
    job = mk_event(conn, env, env["bank"], "contractor_job_opened", {"title": "Contract Temenos Transact Engineer", "team": "Core Banking", "tech_keywords": ["temenos transact"],
                                                                   "employment_type": "contract", "open_status": "open", "source_tier": "S2"}, summary="job")
    out = finish_event(conn, job, now=NOW)
    link = conn.execute("select role, caveat from initiative_events where event_id=%s", (job,)).fetchone()
    assert link["role"] == "capacity_evidence" and "NOT established" in link["caveat"]
    ws = conn.execute("select resource_evidence from workstreams where initiative_id=%s order by created_at limit 1", (r["initiative_id"],)).fetchone()
    assert ws["resource_evidence"][0]["caveat"].startswith("staffing demand unconfirmed")
    assert conn.execute("select stage from initiatives").fetchone()["stage"] == "implementation"      # capacity evidence never moves stage / demand


def test_G_no_budget_source_means_budget_verified_false(conn, env):
    finish_event(conn, mk_event(conn, env, env["bank"], "programme_announced", {**CORE, "programme_name": "Atlas"}), now=NOW)
    i = conn.execute("select budget_verified, research_gaps from initiatives").fetchone()
    assert i["budget_verified"] is False and any("budget_verified=false" in g for g in i["research_gaps"])


def test_workstream_scope_confidence_never_overstated(conn, env):
    finish_event(conn, mk_event(conn, env, env["bank"], "programme_announced", {"programme_domain": "core_banking", "programme_name": "Atlas"}), now=NOW)
    assert {w["scope_confidence"] for w in conn.execute("select scope_confidence from workstreams")} == {"inferred_possible_external"}
    finish_event(conn, mk_event(conn, env, env["bank"], "vendor_selected", {**CORE, "award_scope_includes_dev": True, "counterparty_selected": True, "programme_name": "Atlas"}, [OTHERS[1]]), now=NOW)
    sc = {w["kind"]: w["scope_confidence"] for w in conn.execute("select kind, scope_confidence from workstreams")}
    assert sc["core_banking_customisation"] == "confirmed_scope" and sc["data_migration"] == "implied_engineering_activity"


def test_route_block_from_closed_panel_blocks_direct_only(conn, env):
    r = finish_event(conn, mk_event(conn, env, env["bank"], "vendor_selected", {**CORE, "counterparty_selected": True, "programme_name": "Atlas"}, OTHERS), now=NOW)
    n = mk_event(conn, env, env["bank"], "supplier_panel_closed", {"panel_closed": True}, days_ago=2)
    finish_event(conn, n, now=NOW)
    routes = {x["route_type"]: x for x in route_summary(conn, r["initiative_id"])}
    assert routes["buyer_direct"]["access_status"] == "blocked"
    assert routes["prime_subcontract"]["access_status"] != "blocked"        # alternative partner route stays open for investigation


def test_unverified_or_unresolved_events_never_create_initiatives(conn, env):
    e = mk_event(conn, env, None, "programme_announced", {**CORE})
    assert finish_event(conn, e, now=NOW)["initiative_id"] is None
    e2 = mk_event(conn, env, env["bank"], "programme_announced", {**CORE})
    conn.execute("update events set verification_status='unverified' where id=%s", (e2,))
    assert L.link_event(conn, e2).reason == "event_not_verified"
    assert conn.execute("select count(*) c from initiatives").fetchone()["c"] == 0


def test_duplicate_review_merge_preserves_events_aliases_audit(conn, env):
    a = mk_event(conn, env, env["bank"], "programme_announced", {"programme_domain": "cloud_data", "named_systems": ["AWS"]})
    b = mk_event(conn, env, env["bank"], "programme_announced", {"programme_domain": "cloud_data", "named_systems": ["Snowflake"]})
    finish_event(conn, a, now=NOW); finish_event(conn, b, now=NOW)
    rv = conn.execute("select id from initiative_reviews").fetchone()["id"]
    with pytest.raises(ValueError):
        L.resolve_duplicate(conn, str(rv), "merge", "analyst", "  ")                  # reason mandatory
    L.resolve_duplicate(conn, str(rv), "merge", "analyst", "same programme per annual report")
    assert conn.execute("select count(*) c from initiatives where status='active'").fetchone()["c"] == 1
    assert conn.execute("select count(distinct event_id) c from initiative_events").fetchone()["c"] == 2
    assert conn.execute("select count(*) c from audit_events where operation='initiative_merged'").fetchone()["c"] == 1
    assert conn.execute("select merged_into from initiatives where status='merged'").fetchone()["merged_into"] is not None


def test_refresh_retires_passed_milestones_and_flags_stale(conn, env):
    finish_event(conn, mk_event(conn, env, env["bank"], "implementation_launched", {**CORE, "programme_name": "Atlas", "milestone_date": "2026-11-01"}, days_ago=300), now=NOW)
    out = refresh(conn, NOW)
    assert out["milestones_retired"] == 1 and out["stale_flagged"] == 1
    assert conn.execute("select next_milestone_at from initiatives").fetchone()["next_milestone_at"] is None
