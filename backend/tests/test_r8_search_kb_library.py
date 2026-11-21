"""Keyword search, knowledge-base signed links and the signal/URL library. Data is SYNTHETIC."""
import time

import pytest
from fastapi.testclient import TestClient

from ri import kb
from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.db import Database, jb
from ri.sources.registry import seed_registry
from test_r4_crm_notify import mk_user

SECRET = "x" * 32


@pytest.fixture()
def api(conn, db_url):
    seed_registry(conn)
    from ri.signals.baseline import import_baseline
    from ri.signals.persist import sync_rule_versions
    import_baseline(conn)
    sync_rule_versions(conn)
    mk_user(conn, "admin@x", "admin")
    mk_user(conn, "bdm@x", "bdm", 555)
    mk_user(conn, "mkt@x", "marketing")
    org = str(conn.execute("insert into organizations(display_name, country_code, segment, in_master_list) values ('Synthetic Harbour Bank','GB','Banking',true) returning id").fetchone()["id"])
    link = conn.execute("insert into account_source_links(organization_id, source_system, source_file_id, sheet_id, sheet_title, row_identity, row_number, imported_hash, raw_company_name, observed_at) "
                        "values (%s,'google_sheets','wb','s','Priority Outreach','r1',2,'h','Synthetic Harbour Bank',now()) returning id", (org,)).fetchone()["id"]
    conn.execute("insert into legacy_account_research(organization_id, source_link_id, kind, original, observed_at) values (%s,%s,'priority_outreach',%s, now())",
                 (org, link, jb({"Rank": "1", "Strongest buying signal": "Harbour selects a payments orchestration platform for instant payments", "Primary source URL": "https://harbour.invalid/news/1",
                                 "Verified LinkedIn profile": "https://linkedin.com/in/someone"})))
    conn.commit()
    db = Database(db_url, max_size=4)
    app = create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET, approval_ttl_hours=72, database_url=db_url), db=db)
    hdr = lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"}  # noqa: E731
    yield TestClient(app), hdr, org, db_url
    db.close()


def test_search_finds_accounts_by_sheet_text_signals_sources_and_kb(api):
    c, h, org, _ = api
    assert c.get("/api/search?q=payments", headers=h("bdm")).status_code == 200
    d = c.get("/api/search?q=payments orchestration", headers=h("bdm")).json()
    keys = {g["key"] for g in d["groups"]}
    assert "accounts" in keys
    acc = next(g for g in d["groups"] if g["key"] == "accounts")["items"][0]
    assert acc["title"] == "Synthetic Harbour Bank" and "orchestration" in acc["snippet"] and acc["link"] == f"/accounts/{org}"
    d = c.get("/api/search?q=tender", headers=h("bdm")).json()
    assert "signals" in {g["key"] for g in d["groups"]}                      # the 64 original rules are searchable by keyword
    d = c.get("/api/search?q=ted.europa.eu", headers=h("bdm")).json()
    assert any(g["key"] == "sources" for g in d["groups"])
    d = c.get("/api/search?q=procurement sponsor", headers=h("bdm")).json()
    assert any(g["key"] == "kb" for g in d["groups"])
    assert c.get("/api/search?q=zzzqqqxxx", headers=h("bdm")).json()["total"] == 0
    assert c.get("/api/search?q=a", headers=h("bdm")).json()["groups"] == []        # single characters are ignored
    assert c.get("/api/search?q=payments").status_code == 401
    assert c.get("/api/search?q=payments", headers=h("mkt")).status_code == 403


def test_search_is_injection_safe_and_wildcards_are_literal(api):
    c, h, _, _ = api
    for evil in ["'; drop table users; --", "%", "_%_", "\\", "a' OR '1'='1", "x\" ) ; select pg_sleep(5) --"]:
        r = c.get("/api/search", params={"q": evil}, headers=h("bdm"))
        assert r.status_code == 200, evil
    assert c.get("/api/search", params={"q": "%%"}, headers=h("bdm")).json()["total"] == 0      # '%' is a character, not a wildcard


def test_search_respects_account_scope(api, conn):
    c, h, org, _ = api
    conn.execute("insert into crm_organizations(crm_id, name, owner_id, link_status, organization_id) values (900,'Synthetic Harbour Bank',777,'linked',%s)", (org,))
    conn.commit()
    d = c.get("/api/search?q=orchestration", headers=h("bdm")).json()           # owned by someone else: not visible to this seller
    assert not any(g["key"] == "accounts" for g in d["groups"])
    d = c.get("/api/search?q=orchestration", headers=h("admin")).json()
    assert any(g["key"] == "accounts" for g in d["groups"])


def test_library_signals_filters_and_urls_exclude_social_and_scope(api):
    c, h, org, _ = api
    s = c.get("/api/library/signals", headers=h("bdm")).json()
    assert len(s["items"]) == 64 and s["groups"]
    ids = {i["rule_id"] for i in s["items"]}
    assert "SRC-01" in ids
    active = c.get("/api/library/signals?status=active", headers=h("bdm")).json()["items"]
    assert 0 < len(active) < 64 and {i["status"] for i in active} == {"active"}
    assert any(i["fed_by"] for i in s["items"] if i["rule_id"] == "SRC-01")        # which sources feed each rule
    u = c.get("/api/library/urls", headers=h("bdm")).json()
    urls = [i["url"] for i in u["items"]]
    assert "https://harbour.invalid/news/1" in urls and not any("linkedin" in x for x in urls)
    assert u["feeds"] and any(f["url"] for f in u["feeds"])
    assert c.get("/api/library/urls?q=harbour", headers=h("bdm")).json()["items"][0]["organization"] == "Synthetic Harbour Bank"
    assert c.get("/api/library/urls", headers=h("mkt")).status_code == 403


def test_kb_links_are_signed_short_lived_per_document_and_sandboxed(api):
    c, h, _, url = api
    assert c.post("/api/kb/icp/link").status_code == 401
    assert c.post("/api/kb/nope/link", headers=h("bdm")).status_code == 404
    link = c.post("/api/kb/icp/link", headers=h("bdm")).json()["url"]
    r = c.get(link)
    assert r.status_code == 200 and "ICP" in r.text
    assert "sandbox" in r.headers["Content-Security-Policy"] and "connect-src 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN" and r.headers["Cache-Control"] == "no-store"
    assert c.get("/kb/view/icp?t=garbage").status_code == 404
    assert c.get("/kb/view/icp").status_code == 422 or c.get("/kb/view/icp").status_code == 404
    token = link.split("t=")[1]
    assert c.get(f"/kb/view/field-guide?t={token}").status_code == 404                 # token is bound to one document
    old = kb.sign_link(url, "icp", "u", now=time.time() - 3600)
    assert c.get(f"/kb/view/icp?t={old}").status_code == 404                           # expired
    other = kb.sign_link("postgresql://someone-else/db", "icp", "u")
    assert c.get(f"/kb/view/icp?t={other}").status_code == 404                         # signed with another key
    assert c.get("/api/kb", headers=h("bdm")).json()["items"][0]["id"] in kb.DOCS


def test_kb_files_are_not_public_static_files(tmp_path):
    from ri.api.static import mount_frontend
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("shell")
    from fastapi import FastAPI
    app = FastAPI()
    mount_frontend(app, dist)
    c = TestClient(app)
    assert c.get("/kb/icp.html").status_code == 404 and c.get("/kb/view/icp").status_code == 404


def test_missing_kb_files_are_a_normal_state(api, tmp_path, monkeypatch):
    c, h, _, _ = api
    monkeypatch.setattr(kb, "KB_DIR", tmp_path)
    kb.chunks.cache_clear()
    try:
        items = c.get("/api/kb", headers=h("bdm")).json()["items"]
        assert items and not any(i["available"] for i in items)
        assert c.post("/api/kb/icp/link", headers=h("bdm")).status_code == 404
        assert c.get("/api/search?q=procurement sponsor", headers=h("bdm")).status_code == 200      # search must not break
        assert not any(g["key"] == "kb" for g in c.get("/api/search?q=procurement sponsor", headers=h("bdm")).json()["groups"])
    finally:
        monkeypatch.undo()
        kb.chunks.cache_clear()
