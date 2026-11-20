"""SYNTHETIC demo data (invented entities, example.invalid URLs). For UI development and acceptance walkthroughs ONLY.
Never run against production: refuses unless RI_ENV=development."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from ri.config import Settings  # noqa: E402
from ri.db import connect  # noqa: E402
from ri.ingest.process import finish_event  # noqa: E402
from ri.opportunity import engine  # noqa: E402
from ri.signals.baseline import import_baseline  # noqa: E402
from ri.signals.persist import sync_rule_versions  # noqa: E402
from ri.sources.registry import seed_registry  # noqa: E402

if os.environ.get("RI_ENV", "development") != "development":
    sys.exit("refusing to seed demo data outside development")
s = Settings.from_env()
conn = connect(s.database_url)
now = datetime.now(timezone.utc)
import_baseline(conn); sync_rule_versions(conn); seed_registry(conn)
for email, name, role, team, pd_id in (("bdm@demo.invalid", "Demo BDM", "bdm", "t1", 555), ("manager@demo.invalid", "Demo Manager", "sales_manager", "t1", 700),
                                       ("admin@demo.invalid", "Demo Admin", "admin", "t1", None), ("compliance@demo.invalid", "Demo Compliance", "compliance", "t1", None),
                                       ("marketing@demo.invalid", "Demo Marketing", "marketing", "t1", None)):
    conn.execute("INSERT INTO users(email, display_name, role, team, pipedrive_user_id) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", (email, name, role, team, pd_id))
bank = str(conn.execute("INSERT INTO organizations(display_name, country_code, employees_estimate, employees_basis, segment, in_master_list) VALUES ('SYNTHETIC Northbridge Bank','GB',42000,'sheet_recorded','Banking',true) RETURNING id").fetchone()["id"])
conn.execute("INSERT INTO organization_aliases(organization_id, alias, alias_norm) VALUES (%s,'SYNTHETIC Northbridge Bank','synthetic northbridge bank')", (bank,))
conn.execute("INSERT INTO crm_organizations(crm_id, organization_id, name, owner_id, link_status) VALUES (9001,%s,'SYNTHETIC Northbridge Bank',555,'linked')", (bank,))
pid = conn.execute("INSERT INTO people(full_name) VALUES ('Synthetic Contact') RETURNING id").fetchone()["id"]
conn.execute("INSERT INTO employment(person_id, organization_id, title, buyer_lane) VALUES (%s,%s,'Chief Technology Officer','Technology')", (pid, bank))
n = [0]


def ev(etype, attrs, others=None, days=5, summary="s", tier="S3"):
    n[0] += 1
    row = conn.execute("INSERT INTO events(event_key,event_type,subject_org_id,pub_at,event_at,event_at_known,summary,attrs,other_entity_roles,extraction_model_version,verification_status,independent_origin_group) "
                       "VALUES (%s,%s,%s,%s,%s,true,%s,%s,%s,'synthetic-demo','verified',%s) RETURNING id",
                       (f"demo{n[0]}", etype, bank, now - timedelta(days=days), now - timedelta(days=days), summary, json.dumps({"source_tier": tier, **attrs}), json.dumps(others or []), f"demo-g{n[0]}")).fetchone()["id"]
    c = conn.execute("INSERT INTO claims(subject,predicate,object_json,evidence_anchor,anchor_verified,claim_kind,status) VALUES ('Northbridge','announced_programme',%s,%s,true,'structured','supported') RETURNING id",
                     (json.dumps({"url": "https://example.invalid/news/northbridge-core"}), summary)).fetchone()["id"]
    conn.execute("INSERT INTO event_claims(event_id, claim_id) VALUES (%s,%s)", (row, c))
    return str(row)


core = {"programme_domain": "core_banking", "named_systems": ["Synthetic Core Platform"], "has_dated_milestones": True, "programme_status": "implementation", "programme_name": "Project Northstar"}
ev("vendor_selected", {**core, "counterparty_selected": True}, [{"name": "Synthetic Platform Vendor", "role": "vendor"}], summary="Northbridge Bank selects Synthetic Platform Vendor to replace its core banking platform")
e2 = ev("vendor_selected", {**core, "counterparty_selected": True, "award_scope_includes_dev": True}, [{"name": "Synthetic Integrator", "role": "si"}], days=4, summary="Synthetic Integrator appointed systems integrator with development scope for Project Northstar")
ini = None
for e in conn.execute("SELECT id FROM events ORDER BY first_observed_at").fetchall():
    r = finish_event(conn, str(e["id"]), now=now)
    ini = r["initiative_id"] or ini
dl = now + timedelta(days=9)
ev("procurement_published", {"notice_id": "SYN-TENDER-1", "notice_status": "open", "buyer_entity": "SYNTHETIC Northbridge Bank", "scope": "Payments API software development services", "authoritative_url": "https://example.invalid/tender/1",
                              "submission_deadline": dl.isoformat(), "relevant_services": True, "programme_domain": "payments_rail", "named_systems": ["SEPA Instant"], "programme_name": "Instant Payments Build"}, tier="S1", summary="Open tender: Payments API software development services")
conn.execute("INSERT INTO procurement_notices(source_id,notice_id,buyer_name,buyer_org_id,title,status,deadline_at,deadline_tz,last_status_check_at) VALUES ('procurement-ted','SYN-TENDER-1','SYNTHETIC Northbridge Bank',%s,'Payments API software development services','open',%s,'+01:00',now())", (bank, dl))
conn.execute("UPDATE events SET verification_status='unverified' WHERE false")
for e in conn.execute("SELECT id FROM events WHERE event_key='demo4'").fetchall():
    finish_event(conn, str(e["id"]), now=now)
for i in conn.execute("SELECT id FROM initiatives").fetchall():
    engine.build_for_initiative(conn, str(i["id"]))
for h in conn.execute("SELECT id FROM opportunity_hypotheses").fetchall():
    engine.evaluate(conn, str(h["id"]), now=now, requester_crm_user=555)
conn.execute("UPDATE sources SET status='active' WHERE source_id IN ('procurement-ted','si-gft-news')")
conn.execute("UPDATE source_health SET state='healthy', last_success_at=now() WHERE source_id IN ('procurement-ted')")
conn.execute("UPDATE source_health SET state='degraded', error_class='server_error', consecutive_failures=3 WHERE source_id='si-gft-news'")
conn.commit()
print("demo seeded:", {"organizations": 1, "hypotheses": conn.execute("SELECT count(*) n FROM opportunity_hypotheses").fetchone()["n"]})
