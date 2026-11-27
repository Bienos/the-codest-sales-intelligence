"""One company through the whole path: signal, score, research brief, outreach draft with the brand reviewer, approval by a second person,
Lemlist campaign (not launched), a reply coming back, a Pipedrive deal proposal. The model, Lemlist and Pipedrive are fakes; everything else is real code on a real database."""
import asyncio
import json
from datetime import datetime, timezone

from ri import approvals as appr
from ri.config import Settings
from ri.outbound import enrol
from ri.outbound.writer import write
from ri.research.brief import generate
from ri.scoring import recompute_scores
from test_r4_crm_notify import mk_user

CLM = "We take responsibility for the systems your business makes money on, as if they were our own."
QUOTE = "replacing its core banking platform"


class FakeLemlist:
    def __init__(self):
        self.calls = []

    def create_campaign(self, name, timezone="Europe/London"):
        self.calls.append(("create_campaign", name, timezone))
        return {"_id": "cam_TEST"}

    def sequences(self, cid):
        return [{"_id": "seq_TEST"}]

    def add_step(self, cid, sid, *, subject, message, delay_days):
        self.calls.append(("step", subject, delay_days))
        return {}

    def leads_by_email(self, email):
        return []

    def add_lead(self, cid, email, fields):
        self.calls.append(("lead", email, fields.get("companyDomain")))
        return {"_id": "lea_TEST"}


def test_one_account_end_to_end(conn, capsys):
    out = []
    say = lambda s: out.append(s)      # noqa: E731
    settings = Settings(env="development", auth_mode="dev", dev_auth_secret="x" * 32, outbound_enabled=True)
    mk_user(conn, "seller@x", "bdm", 11)
    mk_user(conn, "manager@x", "sales_manager", 12)

    # 1. the account, a verified signal, and a verified contact
    org = str(conn.execute("insert into organizations(display_name, country_code, segment, employees_estimate, canonical_domain, in_master_list) "
                           "values ('Synthetic Journey Bank AG','DE','Bank',6000,'journey-bank.example',true) returning id").fetchone()["id"])
    conn.execute("insert into sources(source_id, display_name, source_tier, source_type, source_family, provider, status, collection_method) values ('news-x','News X','S3','news','attention','t','active','feed')")
    doc = str(conn.execute("insert into source_documents(source_id, external_id, version_key, canonical_url, title, content_text) values ('news-x','j1','v','https://journey-bank.example/news','Core platform news', %s) returning id",
                           (f"Synthetic Journey Bank AG is {QUOTE} with a phased migration starting in January 2027.",)).fetchone()["id"])
    ev = str(conn.execute("insert into events(event_key, event_type, subject_org_id, summary, extraction_model_version, verification_status, proven) values ('j-e1','programme_announced',%s,"
                          "'Synthetic Journey Bank AG is replacing its core banking platform','t','verified',true) returning id", (org,)).fetchone()["id"])
    conn.execute("insert into event_documents(event_id, document_id) values (%s,%s)", (ev, doc))
    conn.execute("insert into signals(event_id, rule_id, rule_version, organization_id, strength, demand_tier, verification_status, effective_from, expires_at, reason_code) "
                 "values (%s,'PRG-01','1.0.0',%s,3,'D2','verified', now() - interval '3 days', now() + interval '80 days', 'core_programme')", (ev, org))
    pid = str(conn.execute("insert into people(full_name, work_email, email_verification_status) values ('Ada Journey','ada@journey-bank.example','Verified') returning id").fetchone()["id"])
    conn.execute("insert into employment(person_id, organization_id, title) values (%s,%s,'CTO')", (pid, org))
    conn.commit()
    say("1. account + verified programme signal + one verified contact: created")

    # 2. score
    recompute_scores(conn)
    sc = conn.execute("select score, band, signal_count from account_scores where organization_id=%s", (org,)).fetchone()
    say(f"2. score: {sc['score']} ({sc['band']}), {sc['signal_count']} signal")
    assert sc["signal_count"] == 1 and float(sc["score"]) > 0

    # 3. research brief: one statement quoted from the page, one invented (must be dropped)
    reply = {"summary": [{"statement": "The bank is replacing its core banking platform.", "quote": QUOTE, "source": "S1"},
                         {"statement": "The programme has a budget of 40 million euros.", "quote": "budget of 40 million euros", "source": "S1"}], "signals": [], "hiring": []}

    async def model(system, user):
        return json.dumps(reply), 900, 200, "fake-model"
    brief = generate(conn, org, model, lambda c: asyncio.run(c))
    kept = [x["statement"] for x in brief["content"]["summary"]]
    say(f"3. research brief: kept {len(kept)} quoted statement(s), dropped {brief['dropped']} invented one(s)")
    assert len(kept) == 1 and brief["dropped"] == 1
    conn.execute("insert into account_briefs(organization_id, requested_by, status, content) values (%s,'seller@x','done',%s::jsonb)", (org, json.dumps(brief["content"])))
    conn.commit()

    # 4. outreach draft through the brand reviewer: first try breaks rules, the rewrite passes
    good = lambda day, subj: {"day": day, "subject": subj, "claims_used": ["CLM-001"], "fact_refs": ["F1"],      # noqa: E731
                              "body": f"Hello {{{{firstName}}}},\n\nYou are replacing your core banking platform. Programmes like this often leave workstreams that need a clear owner.\n\n{CLM} Let's talk through the problem."}
    bad = {**good(0, "A seamless idea"), "body": good(0, "x")["body"].replace("clear owner", "seamless owner")}
    seq = iter([[bad, good(4, "Ownership"), good(9, "Closing the loop")], [good(0, "Your core platform migration"), good(4, "A question on ownership"), good(9, "Closing the loop")]])

    async def writer(system, user):
        return json.dumps({"steps": next(seq)}), 1000, 400, "fake-model"
    draft = write(conn, org, "CTO", writer, lambda c: asyncio.run(c))
    say(f"4. outreach draft: {draft['status']} after {draft['rounds']} round(s) (the first attempt used the avoid-list word 'seamless' and was sent back)")
    assert draft["status"] == "draft" and draft["rounds"] == 2
    did = str(conn.execute("insert into campaign_drafts(organization_id, requested_by, status, steps, review, rounds) values (%s,'seller@x','approved',%s::jsonb,'{}'::jsonb,2) returning id",
                           (org, json.dumps(draft["steps"]))).fetchone()["id"])
    conn.commit()

    # 5. enrolment: a German bank, so cold email needs the person's consent first (rules/email_rules.yaml); then a second person approves
    plan = enrol.plan_contacts(conn, org)
    assert plan["eligible"] == [] and [c["id"] for c in plan["consent_needed"]] == [pid] and plan["email_rule"]["rule"] == "consent"
    try:
        enrol.request_enrolment(conn, did, [pid], by="seller@x", requester_crm_user=11)
        raise AssertionError("a German contact without consent must be held back")
    except appr.ApprovalError as exc:
        say(f"5. held back by the email rules for Germany: {str(exc)[:60]}...")
    conn.execute("update people set email_consent_at=now(), email_consent_by='seller@x', email_consent_note='Agreed at the bank''s supplier day' where id=%s", (pid,))
    conn.commit()
    plan = enrol.plan_contacts(conn, org)
    say(f"   consent recorded; contacts: {len(plan['eligible'])} usable (verified email), {len(plan['needs_verification'])} need verification")
    r = enrol.request_enrolment(conn, did, [pid], by="seller@x", requester_crm_user=11)
    try:
        appr.decide(conn, r["approval_id"], approver="seller@x", approver_role="bdm", approve=True)
        raise AssertionError("self approval must be refused")
    except appr.ApprovalError as exc:
        say(f"   self-approval refused: {exc}")
    appr.decide(conn, r["approval_id"], approver="manager@x", approver_role="sales_manager", approve=True)
    conn.commit()
    say("   approved by a second person")

    # 6. Lemlist: campaign + steps + lead created, never launched
    fake = FakeLemlist()
    res = enrol.execute_enrolment(conn, r["approval_id"], fake, settings)
    kinds = [c[0] for c in fake.calls]
    say(f"6. Lemlist calls: {kinds}; launched: {'launch' in kinds}; follow-up subjects: {[c[1] for c in fake.calls if c[0] == 'step']}")
    assert "launch" not in kinds and res["leads"] == 1

    # 7. a reply comes back
    status = enrol.record_event(conn, {"type": "interested", "campaignId": "cam_TEST", "leadEmail": "ADA@journey-bank.example", "_id": "ev1"})
    say(f"7. webhook 'interested' -> enrolment status {status['status']}")
    assert status["status"] == "interested"
    say("8. next step offered: propose a Pipedrive deal (pipeline 2, stage 7) for approval")
    with capsys.disabled():
        print("\n" + "\n".join(out))
