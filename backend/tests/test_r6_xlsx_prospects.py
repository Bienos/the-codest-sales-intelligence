"""Workbook upload/import path and the prospect board. All data here is SYNTHETIC."""
import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.db import Database
from ri.jobs.handlers import build_handlers
from ri.jobs.queue import run_one
from ri.sheets import contracts as C
from ri.sheets.client import XlsxSheetsClient, detect_workbook
from ri.sheets.importer import import_workbook
from test_r4_crm_notify import mk_user

SECRET = "x" * 32


def make_xlsx_a(rows_master, rows_log=()) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for ct in C.contracts_for("A"):
        ws = wb.create_sheet(ct.title)
        ws.append(list(ct.headers))
        if ct.title == "MASTER ACCOUNTS":
            for r in rows_master:
                ws.append([r.get(h, "") for h in ct.headers])
        if ct.title == "BUYING SIGNAL LOG":
            for r in rows_log:
                ws.append([r.get(h, "") for h in ct.headers])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


ROW = {"Company": "Synthetic Example Bank AG", "Country": "Germany", "Segment": "Banking", "Website": "https://example-bank.invalid",
       "Employees": 4200, "Signal Priority": "P1", "Buying Signal Score": 8, "Signal Type": "Core banking programme",
       "Verified Buying Signal": "Synthetic bank announced a core replacement programme", "Signal Date": "2026-11-10",
       "Primary Source URL": "https://example-bank.invalid/press/1", "Evidence Confidence": "High", "Target Buyer Role": "CTO"}


def test_detect_workbook_validates_and_identifies():
    assert detect_workbook(make_xlsx_a([ROW])) == "A"
    for bad in (b"hello", b"PK\x03\x04junk", b"PK\x03\x04" + b"\0" * 50):
        with pytest.raises(ValueError):
            detect_workbook(bad)
    wb = openpyxl.Workbook()
    wb.active.title = "Something else"
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError, match="unrecognised"):
        detect_workbook(buf.getvalue())
    with pytest.raises(ValueError, match="25 MB"):
        detect_workbook(b"PK\x03\x04" + b"0" * (26 * 1024 * 1024))


def test_xlsx_import_is_idempotent_and_imports_dates_as_text(conn):
    data = make_xlsx_a([ROW])
    for _ in range(2):
        res = import_workbook(conn, XlsxSheetsClient(C.WORKBOOK_IDS["A"], io.BytesIO(data)), "A")
        assert res.tabs["MASTER ACCOUNTS"]["status"] == "ok"
    n = lambda t: conn.execute(f"select count(*) c from {t}").fetchone()["c"]  # noqa: E731,S608
    assert n("organizations") == 1 and n("account_source_links") == 1 and n("legacy_account_research") == 1
    assert conn.execute("select original->>'Signal Date' d from legacy_account_research").fetchone()["d"] == "2026-11-10"


def test_header_drift_in_upload_pauses_the_tab(conn):
    wb = openpyxl.load_workbook(io.BytesIO(make_xlsx_a([ROW])))
    wb["MASTER ACCOUNTS"]["B1"] = "Land"            # renamed column
    buf = io.BytesIO()
    wb.save(buf)
    res = import_workbook(conn, XlsxSheetsClient(C.WORKBOOK_IDS["A"], io.BytesIO(buf.getvalue())), "A")
    assert res.tabs["MASTER ACCOUNTS"]["status"] == "drift" and res.status == "paused_schema_drift"


@pytest.fixture()
def api(conn, db_url):
    mk_user(conn, "admin@x", "admin")
    mk_user(conn, "bdm@x", "bdm", 555)
    mk_user(conn, "mkt@x", "marketing")
    conn.commit()
    db = Database(db_url, max_size=4)
    app = create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET, approval_ttl_hours=72), db=db)
    hdr = lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"}  # noqa: E731
    yield TestClient(app), hdr
    db.close()


def test_upload_rbac_validation_job_and_prospects(api, conn):
    c, h = api
    data = make_xlsx_a([ROW])
    assert c.post("/api/admin/import/upload", files={"file": ("a.xlsx", data)}).status_code == 401
    assert c.post("/api/admin/import/upload", headers=h("bdm"), files={"file": ("a.xlsx", data)}).status_code == 403
    rr = c.post("/api/admin/import/upload", headers=h("admin"), files={"file": ("a.xlsx", b"not a workbook")})
    assert rr.status_code == 400, rr.text
    r = c.post("/api/admin/import/upload", headers=h("admin"), files={"file": ("../../etc/a.xlsx", data)})
    assert r.status_code == 200 and r.json()["workbook"] == "A"
    assert c.get("/api/prospects", headers=h("bdm")).json()["total"] == 0           # nothing imported until the worker runs
    assert run_one(conn, "w", build_handlers(connector_factory=lambda s: None)) == "completed"
    up = conn.execute("select status, content is null gone, filename from import_uploads").fetchone()
    assert up["status"] == "completed" and up["gone"] and "/" not in up["filename"]       # bytes purged, filename sanitised
    items = c.get("/api/prospects", headers=h("bdm")).json()
    assert items["total"] == 1
    it = items["items"][0]
    assert it["organization"]["name"].startswith("Synthetic Example Bank") and it["verified_by_platform"] is False
    assert it["lane"] == "signals" and it["source_url"] == "https://example-bank.invalid/press/1" and it["age_days"] is not None
    assert c.get("/api/prospects", headers=h("mkt")).status_code == 403
    ov = c.get("/api/overview", headers=h("bdm")).json()
    assert ov["accounts"] == 1 and ov["verified_opportunities"] == 0
    listing = c.get("/api/admin/imports", headers=h("admin")).json()
    assert listing["uploads"][0]["status"] == "completed"
    assert c.get("/api/admin/imports", headers=h("bdm")).status_code == 403


def test_unsafe_source_url_schemes_are_dropped(api, conn):
    c, h = api
    bad = dict(ROW, **{"Primary Source URL": "javascript:alert(1)"})
    c.post("/api/admin/import/upload", headers=h("admin"), files={"file": ("a.xlsx", make_xlsx_a([bad]))})
    run_one(conn, "w", build_handlers(connector_factory=lambda s: None))
    assert c.get("/api/prospects", headers=h("bdm")).json()["items"][0]["source_url"] is None


# ---------------------------------------------------------------- source admin (URL, validate, activate)
def test_source_url_guards_validation_and_activation(api, conn, monkeypatch):
    from ri.sources import webwatch
    from ri.sources.base import HealthCheck
    from ri.sources.registry import seed_registry
    seed_registry(conn)
    conn.commit()
    c, h = api
    sid = "vendor-temenos-news"
    put = lambda url, who="admin": c.put(f"/api/admin/sources/{sid}/config", headers=h(who), json={"url": url})  # noqa: E731
    assert put("https://www.temenos.com/news", "bdm").status_code == 403
    for bad in ("http://www.temenos.com/news", "https://evil.example/news", "https://temenos.com.evil.example/x", "https://169.254.169.254/latest",
                "https://www.temenos.com:8443/x", "https://user:pw@www.temenos.com/x", "ftp://www.temenos.com/x"):
        assert put(bad).status_code == 400, bad
    assert c.put("/api/admin/sources/procurement-ted/config", headers=h("admin"), json={"url": "https://ted.europa.eu/x" + "y" * 5}).status_code == 400   # API source: not configurable here
    assert put("https://www.temenos.com/news").status_code == 200

    body = {"licence_note": "Public newsroom; robots.txt allows; reviewed", "confirm_permitted": True}
    act = lambda: c.post(f"/api/admin/sources/{sid}/activate", headers=h("admin"), json=body)  # noqa: E731
    assert act().status_code == 400                                   # never validated

    async def failing(self):
        return HealthCheck(False, "robots.txt disallows", "policy_block")
    monkeypatch.setattr(webwatch.PageWatchConnector, "health_check", failing)
    assert c.post(f"/api/admin/sources/{sid}/validate", headers=h("admin")).json()["ok"] is False
    assert act().status_code == 400                                   # failed validation cannot be activated

    async def passing(self):
        return HealthCheck(True, "ok")
    monkeypatch.setattr(webwatch.PageWatchConnector, "health_check", passing)
    assert c.post(f"/api/admin/sources/{sid}/validate", headers=h("admin")).json()["ok"] is True
    assert c.post(f"/api/admin/sources/{sid}/activate", headers=h("admin"), json={**body, "confirm_permitted": False}).status_code == 400
    assert act().json()["status"] == "active"
    row = conn.execute("select status, certification->>'technical_owner' o from sources where source_id=%s", (sid,)).fetchone()
    assert row["status"] == "active" and row["o"] == "admin@x"
    assert put("https://www.temenos.com/other").status_code == 200    # changing the URL pauses it until re-validated
    assert conn.execute("select status from sources where source_id=%s", (sid,)).fetchone()["status"] == "paused"
    ops = [r["operation"] for r in conn.execute("select operation from audit_events where record_id=%s order by id", (sid,))]
    assert {"source_set_url", "source_validate", "activate_source"} <= set(ops)


# ---------------------------------------------------------------- hardening regressions
def test_static_server_cannot_read_outside_the_build_folder_and_sets_security_headers(tmp_path, db_url):
    dist = tmp_path / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>shell</html>")
    (tmp_path / "secret.txt").write_text("TOP-SECRET")
    from ri.api.static import mount_frontend
    db = Database(db_url, max_size=2)
    try:
        app = create_app(Settings(env="production", auth_mode="oidc", oidc_issuer="https://i", oidc_audience="a", oidc_jwks_url="https://i/j", approval_ttl_hours=72), db=db)
        assert mount_frontend(app, dist)
        c = TestClient(app)
        for evil in ("../../secret.txt", "..%2f..%2fsecret.txt", "%2e%2e/%2e%2e/secret.txt", "..%2f..%2f..%2f..%2f..%2f..%2fetc/hostname", "/etc/hostname"):
            r = c.get("/" + evil.lstrip("/"))
            assert "TOP-SECRET" not in r.text and r.text == "<html>shell</html>", evil
        r = c.get("/today")
        assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"] and "Strict-Transport-Security" in r.headers
        assert c.get("/api/me").headers["Cache-Control"] == "no-store"
    finally:
        db.close()


# ---------------------------------------------------------------- user management
def test_admin_can_add_and_deactivate_users_with_guards(api, conn):
    c, h = api
    assert c.get("/api/admin/users", headers=h("bdm")).status_code == 403
    new = {"email": "New.Seller@thecodest.co", "display_name": "New Seller", "role": "bdm", "team": "dach"}
    assert c.post("/api/admin/users", headers=h("bdm"), json=new).status_code == 403
    r = c.post("/api/admin/users", headers=h("admin"), json=new)
    assert r.status_code == 200 and r.json()["email"] == "new.seller@thecodest.co"
    assert c.post("/api/admin/users", headers=h("admin"), json=new).status_code == 409
    assert c.post("/api/admin/users", headers=h("admin"), json={**new, "email": "x@gmail.com"}).status_code in (200, 400)   # domain rule applies when configured
    assert c.post("/api/admin/users", headers=h("admin"), json={**new, "email": "bad", "role": "root"}).status_code == 422
    uid = r.json()["id"]
    assert c.patch(f"/api/admin/users/{uid}", headers=h("admin"), json={"role": "sales_manager"}).json()["role"] == "sales_manager"
    assert c.patch(f"/api/admin/users/{uid}", headers=h("admin"), json={"active": False}).json()["active"] is False
    me = conn.execute("select id from users where email='admin@x'").fetchone()["id"]
    assert c.patch(f"/api/admin/users/{me}", headers=h("admin"), json={"active": False}).status_code == 400      # cannot lock yourself out
    assert c.patch(f"/api/admin/users/{me}", headers=h("admin"), json={"role": "bdm"}).status_code == 400
    ops = [x["operation"] for x in conn.execute("select operation from audit_events where operation like 'user_%'")]
    assert ops.count("user_add") >= 1 and "user_edit" in ops
