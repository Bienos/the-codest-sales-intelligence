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
    s = Settings.from_env(require_auth=(a.cmd == "serve"))      # only the web app handles logins; worker/scheduler/migrate must not need them
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
            from .heartbeat import beat
            from .scheduler import schedule_discovery
            res = {"enqueued": schedule_due(conn), "discovery": schedule_discovery(conn, enabled=bool(s.exa_api_key and s.exa_monthly_cap_usd > 0)), **run_maintenance(conn)}
            conn.commit()
            if s.slack_webhook_url and os.environ.get("RI_BASE_URL") and os.environ.get("RI_LINK_SECRET"):     # the digest dedupes by material change, so calling it each tick is safe
                try:
                    from .notify.digest import SlackWebhookSink, send_digest
                    res["digest"] = send_digest(conn, SlackWebhookSink(s.slack_webhook_url), base_url=os.environ["RI_BASE_URL"], secret=os.environ["RI_LINK_SECRET"])
                    conn.commit()
                except Exception as exc:  # noqa: BLE001  (a notification failure must never stop scheduling)
                    conn.rollback()
                    res["digest_error"] = f"{type(exc).__name__}: {exc}"[:200]
            beat(conn, "scheduler", {k: v for k, v in res.items() if isinstance(v, (int, str, bool))})
            conn.commit()
            print(res)
        elif a.cmd == "worker":
            import httpx
            from .jobs.handlers import build_handlers
            from .jobs.queue import run_one
            from .sources.registry import get as get_source

            from .sources.factory import build_connector

            def factory(source_id: str):
                return build_connector(conn, source_id)
            from . import budget
            from .events.budgeted import BudgetedExtractor
            from .events.extract import AnthropicExtractor
            from .identity.resolver import resolve
            period = BudgetedExtractor.period()
            budget.set_cap(conn, "anthropic", "monthly", period, s.anthropic_monthly_token_cap, "tokens")
            budget.set_cap(conn, "exa", "monthly", period, s.exa_monthly_cap_usd, "usd")
            conn.commit()
            print(f"caps for {period}: anthropic={s.anthropic_monthly_token_cap} tokens, exa=${s.exa_monthly_cap_usd}")
            http = httpx.AsyncClient(timeout=60)

            def extractor_factory(c):
                if not s.anthropic_api_key:
                    return None
                return BudgetedExtractor(AnthropicExtractor(None, s.anthropic_api_key, model=s.anthropic_model), c, price_in=s.anthropic_price_in_per_mtok, price_out=s.anthropic_price_out_per_mtok)

            def resolve_factory(c):
                return lambda name: (lambda d: d.org_id if d.action == "auto_link" else None)(resolve(c, name=name, allow_create=False)) if name else None
            from .verify.run import sync_fetch

            def discovery_factory(c):
                if not (s.exa_api_key and s.exa_monthly_cap_usd > 0):
                    return None
                from .sources.exa import ExaClient
                budget.set_cap(c, "exa", "monthly", BudgetedExtractor.period(), s.exa_monthly_cap_usd, "usd")
                c.commit()
                return {"exa": ExaClient(None, s.exa_api_key), "max_searches": s.discovery_max_searches_per_run,
                        "max_new_requests": s.discovery_max_new_requests_per_run, "interval_days": s.discovery_interval_days, "est_cost": s.exa_est_cost_per_search}
            handlers = build_handlers(connector_factory=factory, extractor_factory=extractor_factory, resolve_org_factory=resolve_factory,
                                      fetch_factory=lambda: sync_fetch(lambda: None), discovery_factory=discovery_factory)
            from .heartbeat import beat
            print("worker started; polling queue")
            last_beat, done = 0.0, 0
            while True:
                r = run_one(conn, f"worker-{time.time_ns()}", handlers)
                done += r is not None
                if time.time() - last_beat > 60:
                    beat(conn, "worker", {"jobs_since_start": done})
                    conn.commit()
                    last_beat = time.time()
                if r is None:
                    time.sleep(5)
        elif a.cmd == "digest":
            from .notify.digest import SlackWebhookSink, send_digest
            from .sheets.client import ConnectorBlocked
            try:
                sink = SlackWebhookSink(s.slack_webhook_url)
            except ConnectorBlocked as exc:
                print(f"BLOCKED: {exc}", file=sys.stderr)
                return 3
            print(send_digest(conn, sink, base_url=os.environ["RI_BASE_URL"], secret=os.environ["RI_LINK_SECRET"]))
            conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
