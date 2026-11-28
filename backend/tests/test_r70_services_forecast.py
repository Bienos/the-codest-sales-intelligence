"""IT-services forecast: the Pipedrive fields the app sets up, reading their values and deal products, invoiced revenue by month
(backlog, renewals, open roles, funnel), forecast categories next to the model and data quality per owner."""
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import numpy as np

from ri.config import Settings
from ri.crm import fields as pdf
from ri.crm import pipedrive as pd
from ri.forecast import engine, service
from test_r64_forecast import NOW, api, deal, seed_stages  # noqa: F401  (api is a fixture)


class FakeReader:
    """Pipedrive v1 field lists and field creation, recorded."""

    def __init__(self, deal_fields, org_fields):
        self.base, self.token = "https://example.pipedrive.com/api/v2", "tok"
        self.fields = {"dealFields": deal_fields, "organizationFields": org_fields}
        self.created = []

        def handler(req):
            kind = req.url.path.rsplit("/", 1)[-1]
            if req.method == "POST":
                body = json.loads(req.content)
                key = f"k{len(self.created) + 1}"
                f = {"key": key, "name": body["name"], "field_type": body["field_type"],
                     "options": [{"id": 100 + i, "label": o["label"]} for i, o in enumerate(body.get("options") or [])]}
                self.fields[kind].append(f)
                self.created.append(body["name"])
                return httpx.Response(201, json={"success": True, "data": f})
            return httpx.Response(200, json={"success": True, "data": self.fields[kind], "additional_data": {"pagination": {"more_items_in_collection": False}}})
        self.http = httpx.Client(transport=httpx.MockTransport(handler))

    def _get_url(self, url, params):
        r = self.http.get(url, params=params)
        return r.json()


def test_fields_are_created_once_and_existing_ones_reused(conn):
    r = FakeReader([{"key": "abc", "name": "Deal type", "field_type": "enum", "options": [{"id": 1, "label": "New client"}]}], [])
    out = pdf.ensure_fields(conn, r)
    assert "Deal type" in out["found"] and "Deal type" not in r.created
    assert len(r.created) == len(pdf.SPEC) - 1 and "Contract end" in r.created
    m = {x["role"]: x for x in conn.execute("select * from crm_field_map").fetchall()}
    assert m["deal_type"]["field_key"] == "abc" and not m["deal_type"]["created_by_app"] and m["forecast_category"]["created_by_app"]
    labels = {x["label"] for x in conn.execute("select label from crm_field_options where field_key=%s", (m["forecast_category"]["field_key"],)).fetchall()}
    assert labels == {"Pipeline", "Best case", "Commit"}
    # someone deletes a field in Pipedrive: later runs only look up, they never create again
    r.fields["dealFields"] = [f for f in r.fields["dealFields"] if f["name"] != "Skills"]
    again = pdf.ensure_fields(conn, r)
    assert again["missing"] == ["Skills"] and len(r.created) == len(pdf.SPEC) - 1
    # asked explicitly (the button on the page): it is created again
    assert pdf.ensure_fields(conn, r, create=True)["created"] == ["Skills"]


def _map(conn):
    for role, entity, key, ftype in [("deal_type", "deal", "kt", "enum"), ("forecast_category", "deal", "kc", "enum"), ("start_date", "deal", "ks", "date"),
                                     ("duration_months", "deal", "kd", "double"), ("fte", "deal", "kf", "double"), ("monthly_value", "deal", "km", "monetary"),
                                     ("q_pain", "deal", "kp", "text"), ("q_champion", "deal", "kch", "varchar"), ("skills", "deal", "ksk", "set"),
                                     ("contract_end", "organization", "kce", "date"), ("client_status", "organization", "kcs", "enum")]:
        conn.execute("insert into crm_field_map(role, entity, field_key, name, field_type) values (%s,%s,%s,%s,%s)", (role, entity, key, role, ftype))
    for oid, label in [("1", "Pipeline"), ("2", "Best case"), ("3", "Commit")]:
        conn.execute("insert into crm_field_options(field_key, option_id, label) values ('kc',%s,%s)", (oid, label))


def test_deal_and_org_field_values_are_read(conn):
    seed_stages(conn)
    _map(conn)
    d = {**deal(1, 13), "custom_fields": {"kt": 7, "kc": 3, "ks": "2027-02-01", "kd": 9, "kf": 2.5, "km": {"value": 18000, "currency": "EUR"},
                                          "kp": "Needs 3 Java devs by Q1", "kch": "", "ksk": [4, 9]}}
    pd.upsert_deal(conn, d)
    r = conn.execute("select * from crm_deals where crm_id=1").fetchone()
    assert (r["deal_type_raw"], r["forecast_category_raw"], r["start_date"], float(r["duration_months"]), float(r["fte"]), float(r["monthly_value"])) == \
        ("7", "3", date(2027, 2, 1), 9.0, 2.5, 18000.0)
    assert r["skills"] == ["4", "9"] and r["qualification"] == {"pain": "Needs 3 Java devs by Q1"}
    pd.upsert_org(conn, {"id": 50, "name": "Bank", "custom_fields": {"kce": "2027-01-31", "kcs": 1}})
    o = conn.execute("select contract_end, client_status_raw from crm_organizations where crm_id=50").fetchone()
    assert o["contract_end"] == date(2027, 1, 31) and o["client_status_raw"] == "1"


def test_products_give_monthly_value_start_and_length():
    s = pdf.summarize_products([
        {"quantity": 2, "item_price": 9000, "billing_frequency": "monthly", "billing_frequency_cycles": 12, "billing_start_date": "2027-01-01"},
        {"quantity": 1, "item_price": 30000, "billing_frequency": "quarterly", "billing_frequency_cycles": 2, "billing_start_date": "2026-12-15", "discount": 10},
        {"quantity": 1, "item_price": 5000, "billing_frequency": "one-time"}])
    assert s["monthly"] == 18000 + 9000 and s["start"] == "2026-12-15" and s["months"] == 12 and s["fte"] == 3
    assert s["total"] == 2 * 9000 * 12 + 27000 * 2 + 5000
    assert pdf.summarize_products([]) is None


def test_engine_spreads_won_value_over_the_contract_months():
    cal = engine.Calendar(date(2026, 12, 1), 2027)
    inp = engine.Inputs(conv=[1.0], days=[10.0], open_stage=[0], open_value=[120000.0], open_age=[0.0], streams=[], deal_value=1.0,
                        lag_days=20.0, open_months=[12.0], new_months=12.0)
    res = engine.simulate(inp, cal, engine.Draws(400, 1, 1, 0, len(cal.months), 0.0))
    inv = res["invoiced"]
    assert inv["months"][0] == "2026-12-01" and len(inv["months"]) == 13
    assert abs(sum(inv["open"]) - 120000) < 120000 * 0.12          # nearly all of it falls before the end of 2027 (value spread ±VSIG)
    assert inv["open"][0] < inv["open"][3]                          # December is mostly before the start
    assert res["_win_by_deal"] == [1.0]


def _delivery(conn):
    conn.execute("insert into crm_pipelines(crm_id, name) values (2, 'Business Pipeline'), (9, 'Delivery Clients')")
    for sid, name, order in [(66, "MUFG", 1), (71, "OTHERS", 3), (68, "OPEN ROLES", 4), (72, "On hold", 5)]:
        conn.execute("insert into crm_stages(crm_id, pipeline_id, name, order_nr) values (%s,9,%s,%s)", (sid, name, order))
    conn.execute("insert into crm_organizations(crm_id, name, contract_end) values (1,'Bank', %s), (2,'Shop', null)", (date(2027, 1, 31),))
    add = NOW - timedelta(days=200)
    for i, stage, org, value, monthly, status in [(101, 66, 1, 0, 20000, "open"), (102, 71, 2, 120000, None, "open"), (103, 68, 1, 48000, None, "open"),
                                                   (104, 72, 1, 30000, None, "open"), (105, 71, 2, 0, None, "open")]:
        conn.execute("insert into crm_deals(crm_id, title, crm_org_id, pipeline_id, stage_id, stage_name, status, value, currency, add_time, monthly_value) "
                     "values (%s,%s,%s,9,%s,'x',%s,%s,'EUR',%s,%s)", (i, f"Role {i}", org, stage, status, value, add, monthly))
    # the signed sales deal of a client run from the delivery pipeline: not counted twice
    conn.execute("insert into crm_deals(crm_id, title, crm_org_id, pipeline_id, stage_id, status, value, currency, add_time, won_time) "
                 "values (200,'Bank framework',1,2,15,'won',500000,'EUR',%s,%s)", (add, add))


def test_revenue_by_month_backlog_renewals_roles_and_renewal_alerts(conn):
    seed_stages(conn)
    _delivery(conn)
    p = service.build(conn, Settings(pipedrive_pipeline_id=2, pipedrive_stage_id=7), now=NOW, use_cache=False)
    rv = p["revenue"]
    assert "error" not in rv, rv
    assert rv["delivery_pipeline"]["found"] and rv["months"][0] == "2026-11-01" and rv["months"][-1] == "2027-12-01"
    eng = {e["crm_id"]: e for e in rv["engagements"]}
    assert set(eng) == {101, 102}
    assert eng[101]["monthly"] == 20000 and eng[101]["end"] == "2027-01-31" and eng[101]["end_source"] == "contract end"
    assert eng[102]["monthly"] == 10000 and eng[102]["how"] == "value / default length"
    assert [r["crm_id"] for r in rv["open_roles"]] == [103] and rv["open_roles"][0]["monthly"] == 4000
    assert rv["paused"]["n"] == 1 and [m["crm_id"] for m in rv["missing_value"]] == [105]
    assert [c["crm_id"] for c in rv["counted_in_delivery"]] == [200]
    dec = rv["months"].index("2026-12-01")
    assert abs(rv["layers"]["backlog"][dec] - 30000) < 1e-6                     # both engagements run all December
    feb = rv["months"].index("2027-02-01")
    assert abs(rv["layers"]["backlog"][feb] - 10000) < 1e-6 and abs(rv["layers"]["renewals"][feb] - 20000 * 0.6) < 1e-6
    assert {e["crm_id"] for e in rv["renewals_due"]} >= {101} and rv["orgs_ending"][0]["org"] == "Bank"
    assert rv["year_total"]["by_layer"]["backlog"] > 0 and rv["concentration"][0]["org"] in ("Bank", "Shop")
    assert "invoiced" not in p["result"] and "error" not in p["categories"] and "error" not in p["quality"]


def test_categories_and_data_quality(conn):
    seed_stages(conn)
    _map(conn)
    conn.execute("insert into crm_users(crm_id, name) values (555, 'Ola')")
    t0 = NOW - timedelta(days=20)
    pd.upsert_deal(conn, {**deal(1, 14, add=t0, moved=t0), "custom_fields": {"kc": 3}})          # Commit at the last stage
    pd.upsert_deal(conn, {**deal(2, 14, add=t0, moved=t0), "custom_fields": {}})                 # last stage, no category
    pd.upsert_deal(conn, {**deal(3, 10, add=t0, moved=t0, value=0)})
    p = service.build(conn, Settings(pipedrive_pipeline_id=2, pipedrive_stage_id=7), now=NOW, use_cache=False)
    c = p["categories"]
    assert c["field_set_up"] and {g["category"] for g in c["groups"]} == {"Commit", "Not set"}
    q = p["quality"]
    own = q["owners"][0]
    assert own["name"] == "Ola" and own["deals"] == 3 and 0 < own["score"] < 1
    miss = {d["crm_id"]: d["missing"] for d in q["deals"]}
    assert "value" in miss[3] and "category" in miss[2] and "category" not in miss.get(1, [])
    assert "close_date" in miss[1] and "start" in miss[1]


def test_fields_endpoint_needs_manage_and_a_token(api):
    c, h = api
    assert c.post("/api/forecast/pipedrive-fields", headers=h("bdm")).status_code == 403
    assert c.post("/api/forecast/pipedrive-fields", headers=h("mgr")).status_code == 409        # no Pipedrive token in this app


def test_np_add_at_is_used_correctly():
    rev = np.zeros((2, 2))
    engine._spread(rev, np.array([0, 0, 1]), np.array([0.0, 0.0, 0.0]), np.array([30.44, 30.44, 60.88]), np.array([1.0, 1.0, 2.0]), 0.0,
                   np.array([[0.0, 30.44], [30.44, 60.88]]))
    assert np.allclose(rev, [[60.88, 0.0], [30.44, 30.44]])
