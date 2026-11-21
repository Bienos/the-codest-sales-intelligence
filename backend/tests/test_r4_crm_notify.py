import base64
import json
from datetime import datetime, timezone

import httpx
import pytest

from ri import approvals as appr
from ri.config import Settings
from ri.crm import pipedrive as pd
from ri.notify.digest import ConsoleSink, SlackWebhookSink, send_digest, sign_link, verify_link
from ri.sheets.client import ConnectorBlocked
from test_r3_opportunity import env, make_hyp  # noqa: F401  (fixtures)
from ri.opportunity import engine

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def org_payload(i, name, owner=111, website=None, deleted=False, ut="2026-11-19T10:00:00Z"):
    return {"id": i, "name": name, "owner_id": owner, "update_time": ut, "is_deleted": deleted, "website": website, "address": {"value": "London"}, "custom_fields": {"abc": None}, "open_deals_count": 0}


def reader_with(pages):
    """Mock Pipedrive v2: cursor paging shaped like the live response (data[], additional_data.next_cursor)."""
    calls = []

    def handler(req: httpx.Request):
        calls.append(dict(req.url.params))
        key = req.url.path.rsplit("/", 1)[-1]
        seq = pages[key]
        cur = int(req.url.params.get("cursor", "0"))
        body = {"success": True, "data": seq[cur], "additional_data": ({"next_cursor": str(cur + 1)} if cur + 1 < len(seq) else {})}
        return httpx.Response(200, json=body)

    r = pd.PipedriveReader("example", "tok", http=httpx.Client(transport=httpx.MockTransport(handler)))
    r.calls = calls
    return r


def test_reader_blocked_without_credentials():
    with pytest.raises(ConnectorBlocked):
        pd.PipedriveReader(None, None)


def test_sync_pages_links_by_name_and_keeps_owner_from_crm(conn, env):
    pages = {"organizations": [[org_payload(1, "Example Bank", owner=555)], [org_payload(2, "Totally Unknown Co", owner=666)]], "persons": [[]], "deals": [[]]}
    out = pd.sync_incremental(conn, reader_with(pages))
    assert out["organizations"] == 2
    a = conn.execute("select organization_id, owner_id, link_status from crm_organizations where crm_id=1").fetchone()
    assert str(a["organization_id"]) == env["bank"] and a["owner_id"] == 555 and a["link_status"] == "linked"
    b = conn.execute("select organization_id, link_status from crm_organizations where crm_id=2").fetchone()
    assert b["organization_id"] is None and b["link_status"] == "unlinked"          # CRM-only org is never auto-created as a prospect
    assert conn.execute("select count(*) c from organizations").fetchone()["c"] == 1


def test_sync_uses_watermark_and_is_idempotent(conn, env):
    pages = {"organizations": [[org_payload(1, "Example Bank", ut="2026-11-19T10:00:00Z")]], "persons": [[]], "deals": [[]]}
    r = reader_with(pages)
    pd.sync_incremental(conn, r)
    pd.sync_incremental(conn, r)
    wm_calls = [c.get("updated_since") for c in r.calls if "updated_since" in c]
    assert "2026-11-19T10:00:00Z" in wm_calls
    assert conn.execute("select count(*) c from crm_organizations").fetchone()["c"] == 1


def test_owner_is_never_overwritten_by_sheets_import_and_crm_owner_changes_flow_in(conn, env):
    pd.sync_incremental(conn, reader_with({"organizations": [[org_payload(1, "Example Bank", owner=555)]], "persons": [[]], "deals": [[]]}))
    pd.sync_incremental(conn, reader_with({"organizations": [[org_payload(1, "Example Bank", owner=777, ut="2026-11-20T09:00:00Z")]], "persons": [[]], "deals": [[]]}))
    assert conn.execute("select owner_id from crm_organizations where crm_id=1").fetchone()["owner_id"] == 777


def test_deleted_crm_org_removed_from_mirror(conn, env):
    pd.upsert_org(conn, org_payload(1, "Example Bank"))
    pd.upsert_org(conn, org_payload(1, "Example Bank", deleted=True))
    assert conn.execute("select count(*) c from crm_organizations").fetchone()["c"] == 0


def test_crm_opt_out_propagates_suppression_to_canonical_person(conn, env):
    pid = conn.execute("insert into people(full_name) values ('Test Person') returning id").fetchone()["id"]
    pd.upsert_org(conn, org_payload(1, "Example Bank"))
    conn.execute("insert into crm_persons(crm_id, person_id, crm_org_id, name) values (50, %s, 1, 'Test Person')", (pid,))
    pd.upsert_person(conn, {"id": 50, "org_id": 1, "name": "Test Person", "marketing_status": "unsubscribed"})
    assert conn.execute("select suppressed from people where id=%s", (pid,)).fetchone()["suppressed"] is True
    pd.upsert_person(conn, {"id": 50, "org_id": 1, "name": "Test Person", "marketing_status": "subscribed"})      # CRM flips back: suppression is sticky until a human clears it
    assert conn.execute("select suppressed from crm_persons where crm_id=50").fetchone()["suppressed"] is True


def test_schema_drift_in_crm_response_is_an_error_not_silence():
    r = pd.PipedriveReader("example", "tok", http=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"oops": 1}))))
    with pytest.raises(pd.CrmError):
        list(r.paged("/organizations"))


def test_webhook_auth_dedup_and_quarantine(conn, env):
    good = "Basic " + base64.b64encode(b"pipedrive:s3cret").decode()
    assert pd.verify_basic_auth(good, "pipedrive", "s3cret") and not pd.verify_basic_auth(good, "pipedrive", "other") and not pd.verify_basic_auth(None, "a", "b")
    body = {"meta": {"entity": "organization", "action": "change", "webhook_id": "w1", "entity_id": 1}, "data": org_payload(1, "Example Bank", owner=999)}
    assert pd.receive_webhook(conn, body) == ("w1", True)
    assert pd.receive_webhook(conn, body) == ("w1", False)                        # duplicate delivery is a no-op
    bad = {"meta": {"entity": "organization", "action": "change", "webhook_id": "w2"}, "data": {"no_id": True}}
    pd.receive_webhook(conn, bad)
    out = pd.process_inbox(conn)
    assert out == {"processed": 1, "quarantined": 1}
    assert conn.execute("select owner_id from crm_organizations where crm_id=1").fetchone()["owner_id"] == 999
    assert conn.execute("select status from crm_webhook_inbox where dedup_key='w2'").fetchone()["status"] == "quarantined"


# ------------------------------------------------------------------ approved writes
class FakeWriter:
    def __init__(self, fail_after_landing=False):
        self.created, self.fail_after_landing = [], fail_after_landing

    def find_by_marker(self, org, key):
        return next((a for a in self.created if key in a["note"]), None)

    def create_activity(self, payload, key):
        a = {"id": 900 + len(self.created), "note": f"{payload.get('note', '')} [ri-idem:{key}]"}
        self.created.append(a)
        if self.fail_after_landing:
            raise httpx.ReadTimeout("timeout after server accepted")
        return a


def approved_write(conn, requester="bdm@x", approver="mgr@x", key="k1", payload=None, target="1"):
    a = appr.request(conn, kind="crm_write", target_system="pipedrive", target_id=target, operation="create_activity", payload=payload or {"subject": "Verify integration workstream owner", "type": "task"},
                     requested_by=requester, justification="owner confirmed", idempotency_key=key)
    return appr.decide(conn, str(a["id"]), approver=approver, approver_role="sales_manager", approve=True)


def writes_on():
    return Settings(env="development", auth_mode="dev", dev_auth_secret="x" * 32, crm_writes_enabled=True)


def test_kill_switch_blocks_even_with_valid_approval(conn, env):
    pd.upsert_org(conn, org_payload(1, "Example Bank"))
    a = approved_write(conn)
    w = FakeWriter()
    with pytest.raises(appr.ApprovalError, match="disabled"):
        pd.execute_approved(conn, str(a["id"]), w, Settings(env="development", auth_mode="dev", dev_auth_secret="x" * 32))
    assert w.created == []


def test_executor_requires_approved_unmodified_unexpired_and_single_use(conn, env):
    pd.upsert_org(conn, org_payload(1, "Example Bank"))
    a = approved_write(conn)
    w = FakeWriter()
    res = pd.execute_approved(conn, str(a["id"]), w, writes_on())
    assert res.status == "executed" and len(w.created) == 1 and "[ri-idem:k1]" in w.created[0]["note"]
    with pytest.raises(appr.ApprovalError):
        pd.execute_approved(conn, str(a["id"]), w, writes_on())                      # approval is single-use
    assert len(w.created) == 1
    # tampering with the stored payload after approval is detected
    b = approved_write(conn, key="k2")
    conn.execute("update approvals set payload=%s where id=%s", (json.dumps({"subject": "TAMPERED", "type": "task"}), b["id"]))
    with pytest.raises(appr.ApprovalError, match="payload differs"):
        pd.execute_approved(conn, str(b["id"]), FakeWriter(), writes_on())
    # pending (unapproved) approval cannot execute
    c = appr.request(conn, kind="crm_write", target_system="pipedrive", target_id="1", operation="create_activity", payload={"s": 1}, requested_by="u@x", justification="j", idempotency_key="k3")
    with pytest.raises(appr.ApprovalError, match="pending"):
        pd.execute_approved(conn, str(c["id"]), FakeWriter(), writes_on())


def test_ambiguous_timeout_then_retry_reconciles_without_double_write(conn, env):
    pd.upsert_org(conn, org_payload(1, "Example Bank"))
    a = approved_write(conn, key="kt")
    w = FakeWriter(fail_after_landing=True)
    with pytest.raises(httpx.ReadTimeout):
        pd.execute_approved(conn, str(a["id"]), w, writes_on())
    assert len(w.created) == 1                                                       # it DID land remotely
    assert conn.execute("select status from approvals where id=%s", (a["id"],)).fetchone()["status"] == "approved"      # handed back for safe retry
    res = pd.execute_approved(conn, str(a["id"]), w, writes_on())
    assert res.reconciled and len(w.created) == 1                                    # exactly once
    assert conn.execute("select status from approvals where id=%s", (a["id"],)).fetchone()["status"] == "executed"


def test_executor_unknown_target_and_unsupported_operation(conn, env):
    a = approved_write(conn, target="999")
    with pytest.raises(appr.ApprovalError, match="unknown to the mirror"):
        pd.execute_approved(conn, str(a["id"]), FakeWriter(), writes_on())
    b = appr.request(conn, kind="crm_write", target_system="pipedrive", target_id="1", operation="update_deal_stage", payload={"stage": 9}, requested_by="u@x", justification="j", idempotency_key="ks")
    appr.decide(conn, str(b["id"]), approver="m@x", approver_role="sales_manager", approve=True)
    pd.upsert_org(conn, org_payload(1, "Example Bank"))
    with pytest.raises(appr.ApprovalError, match="not supported"):
        pd.execute_approved(conn, str(b["id"]), FakeWriter(), writes_on())          # stage/owner changes are not executable at all


def test_writer_blocked_without_credential():
    with pytest.raises(ConnectorBlocked):
        pd.PipedriveWriter("example", None)


# ------------------------------------------------------------------ digest
def mk_user(conn, email, role="bdm", pd_id=None, team="t1"):
    return str(conn.execute("insert into users(email, display_name, role, team, pipedrive_user_id) values (%s,%s,%s,%s,%s) returning id", (email, email.split("@")[0], role, team, pd_id)).fetchone()["id"])


def test_digest_dedupes_by_material_fingerprint_and_is_pii_free(conn, env):
    mk_user(conn, "bdm@x", "bdm", 555)
    ini, hyps = make_hyp(conn, env, crm=True, owner=555)
    for h in hyps.values():
        engine.evaluate(conn, h, now=NOW, requester_crm_user=555)
    pid = conn.execute("insert into people(full_name, work_email) values ('Secret Person','secret@example.invalid') returning id").fetchone()["id"]
    conn.execute("insert into employment(person_id, organization_id, title) values (%s,%s,'CTO')", (pid, env["bank"]))
    sink = ConsoleSink()
    s1 = send_digest(conn, sink, base_url="https://ri.example", secret="s" * 32, now=NOW)
    assert s1["sent"] == 1 and len(sink.sent) == 1
    text = sink.sent[0][1]
    assert "secret@example.invalid" not in text and "Secret Person" not in text and "sig=" in text
    s2 = send_digest(conn, sink, base_url="https://ri.example", secret="s" * 32, now=NOW)
    assert s2["sent"] == 0 and len(sink.sent) == 1                                          # unchanged => no repeat


def test_urgent_item_stays_visible_daily_but_digest_never_repeats_it(conn, env):
    from test_r3_opportunity import tender_chain
    from datetime import timedelta
    from ri.notify.digest import send_urgent
    from ri.readmodel import today
    from ri.access import principal_from_row
    uid = mk_user(conn, "bdm@x", "bdm", 555)
    h = tender_chain(conn, env, NOW + timedelta(days=9))
    engine.evaluate(conn, h, now=NOW)
    sink = ConsoleSink()
    s1 = send_digest(conn, sink, base_url="https://ri.example", secret="s" * 32, now=NOW)
    assert s1["sent"] == 1
    s2 = send_digest(conn, sink, base_url="https://ri.example", secret="s" * 32, now=NOW)
    assert s2["sent"] == 0 and s2["skipped_duplicate"] >= 1                                 # still on Today (urgent) but not re-notified
    u = conn.execute("select * from users where email='bdm@x'").fetchone()
    c = today(conn, principal_from_row(u), NOW)["sections"]["critical"][0]
    assert send_urgent(conn, sink, c, recipient="bdm@x", base_url="https://ri.example", secret="s" * 32, fp=c["fingerprint"]) is False   # immediate alert deduped against digest


def test_signed_links_expire_and_resist_tampering():
    link = sign_link("k" * 32, "/opportunities/abc", ttl_s=60, now=1000)
    q = dict(x.split("=") for x in link.split("?")[1].split("&"))
    assert verify_link("k" * 32, "/opportunities/abc", int(q["exp"]), q["sig"], now=1010)
    assert not verify_link("k" * 32, "/opportunities/abc", int(q["exp"]), q["sig"], now=2000)
    assert not verify_link("k" * 32, "/opportunities/OTHER", int(q["exp"]), q["sig"], now=1010)


def test_slack_sink_blocked_without_webhook_not_faked():
    with pytest.raises(ConnectorBlocked):
        SlackWebhookSink(None)
