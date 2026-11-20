"""Operational CLI:  python -m ri.cli <command>   (migrate | seed | import-sheets A|B | sync-crm | schedule | worker | digest | serve)"""
from __future__ import annotations

import argparse
import os
import sys
import time

from .config import Settings
from .db import connect


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ri")
    ap.add_argument("cmd", choices=["migrate", "seed", "import-sheets", "sync-crm", "schedule", "worker", "digest", "serve"])
    ap.add_argument("arg", nargs="?")
    a = ap.parse_args(argv)
    s = Settings.from_env()
    if a.cmd == "migrate":
        from .migrate import migrate
        print("applied:", migrate(s.database_url) or "nothing to do")
        return 0
    if a.cmd == "serve":
        import uvicorn
        from .api.app import create_app
        from .api.static import mount_frontend
        app = create_app(s)
        mount_frontend(app)
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))  # noqa: S104
        return 0
    conn = connect(s.database_url)
    try:
        if a.cmd == "seed":
            from .signals.baseline import import_baseline
            from .signals.persist import sync_rule_versions
            from .sources.registry import seed_registry
            for em in s.bootstrap_admin_emails:
                conn.execute("INSERT INTO users(email, display_name, role, team) VALUES (%s,%s,'admin','default') ON CONFLICT (email) DO NOTHING", (em, em.split("@")[0]))
            print({"bootstrap_admins": list(s.bootstrap_admin_emails), "legacy_signals_inserted": import_baseline(conn), "rules": sync_rule_versions(conn), "sources_seeded": seed_registry(conn)})
            conn.commit()
        elif a.cmd == "import-sheets":
            from .sheets.client import ConnectorBlocked, GoogleSheetsClient
            from .sheets.importer import import_workbook
            try:
                client = GoogleSheetsClient(s.google_credentials_file)
            except ConnectorBlocked as exc:
                print(f"BLOCKED: {exc}\nSee docs/runbooks/connect-google-sheets.md", file=sys.stderr)
                return 3
            r = import_workbook(conn, client, a.arg or "A")
            print(r.status, r.tabs)
        elif a.cmd == "sync-crm":
            import os
            from .crm.pipedrive import PipedriveReader, process_inbox, sync_incremental
            try:
                reader = PipedriveReader(os.environ.get("PIPEDRIVE_DOMAIN"), s.pipedrive_api_token)
            except Exception as exc:  # noqa: BLE001
                print(f"BLOCKED: {exc}", file=sys.stderr)
                return 3
            print(sync_incremental(conn, reader), process_inbox(conn))
            conn.commit()
        elif a.cmd == "schedule":
            from .scheduler import run_maintenance, schedule_due
            print({"enqueued": schedule_due(conn), **run_maintenance(conn)})
            conn.commit()
        elif a.cmd == "worker":
            import httpx
            from .jobs.handlers import build_handlers
            from .jobs.queue import run_one
            from .sources.registry import get as get_source

            def factory(source_id: str):
                from .sources.procurement import FindTenderConnector, TedConnector
                src = get_source(conn, source_id)
                client = httpx.AsyncClient(timeout=30)
                if source_id == "procurement-ted":
                    return TedConnector(client, query=src["config"]["query"])
                if source_id == "procurement-uk-fts":
                    return FindTenderConnector(client)
                raise NotImplementedError(f"no connector implementation wired for {source_id}")
            handlers = build_handlers(connector_factory=factory)
            print("worker started; polling queue")
            while True:
                if run_one(conn, f"worker-{time.time_ns()}", handlers) is None:
                    time.sleep(5)
        elif a.cmd == "digest":
            from .notify.digest import SlackWebhookSink, send_digest
            from .sheets.client import ConnectorBlocked
            try:
                sink = SlackWebhookSink(s.slack_webhook_url)
            except ConnectorBlocked as exc:
                print(f"BLOCKED: {exc}", file=sys.stderr)
                return 3
            import os
            print(send_digest(conn, sink, base_url=os.environ["RI_BASE_URL"], secret=os.environ["RI_LINK_SECRET"]))
            conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
