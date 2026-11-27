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
        writer_factory = None
        if s.pipedrive_write_token and s.pipedrive_domain:
            from .crm.pipedrive import PipedriveWriter

            def writer_factory():          # noqa: E306  (built per call so a rotated token is picked up without a restart)
                return PipedriveWriter(s.pipedrive_domain, s.pipedrive_write_token)
        app = create_app(s, writer_factory=writer_factory)
        mount_frontend(app)
        import logging

        from .api.app import RedactSecretsInAccessLog
        logging.getLogger("uvicorn.access").addFilter(RedactSecretsInAccessLog())     # the Lemlist webhook secret travels in the path
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
            from .scheduler import crm_sync_step, exa_budget_set, lemlist_sync_step, run_maintenance, schedule_due
            from .heartbeat import beat
            from .scheduler import schedule_discovery
            res = {"enqueued": schedule_due(conn), "discovery": schedule_discovery(conn, enabled=exa_budget_set(conn)), **run_maintenance(conn)}
            try:      # missing company facts: an hourly Exa fill-in while companies wait (the run itself caps the work and the spend)
                from .research.company_facts import schedule as schedule_company_facts
                with conn.transaction():
                    res["company_facts_queued"] = schedule_company_facts(conn)
            except Exception as exc:  # noqa: BLE001  (never stops scheduling)
                res["company_facts_error"] = f"{type(exc).__name__}: {exc}"[:200]
            res.update(crm_sync_step(conn, s))
            res.update(lemlist_sync_step(conn, s))
            conn.commit()
            try:
                from .canary import run_canary
                from .notify.digest import SlackWebhookSink
                sink = SlackWebhookSink(s.slack_webhook_url) if s.slack_webhook_url else None
                res.update(run_canary(conn, s, sink=sink))
                from .precision import weekly_review_step
                res.update(weekly_review_step(conn, s, sink=sink))
                conn.commit()
            except Exception as exc:  # noqa: BLE001  (the self-test must never stop scheduling)
                conn.rollback()
                res["canary_error"] = f"{type(exc).__name__}: {exc}"[:200]
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
            from .jobs.handlers import build_handlers
            from .jobs.queue import run_one

            from .sources.factory import build_connector

            from . import budget
            from .events.budgeted import BudgetedExtractor
            from .events.extract import AnthropicExtractor
            from .identity.resolver import resolve
            from .heartbeat import beat
            from .verify.run import sync_fetch
            import threading
            period = BudgetedExtractor.period()
            budget.set_cap(conn, "anthropic", "monthly", period, s.anthropic_monthly_token_cap, "tokens")
            budget.set_cap(conn, "exa", "monthly", period, s.exa_monthly_cap_usd, "usd")
            conn.commit()
            print(f"caps for {period}: anthropic={s.anthropic_monthly_token_cap} tokens, exa=${s.exa_monthly_cap_usd}")

            def make_handlers(c):
                """Handlers bound to one database connection: each lane has its own, so a long background job never holds up a button press."""
                def extractor_factory(cc):
                    if not s.anthropic_api_key:
                        return None
                    return BudgetedExtractor(AnthropicExtractor(None, s.anthropic_api_key, model=s.anthropic_model), cc, price_in=s.anthropic_price_in_per_mtok, price_out=s.anthropic_price_out_per_mtok)

                def resolve_factory(cc):
                    return lambda name: (lambda d: d.org_id if d.action == "auto_link" else None)(resolve(cc, name=name, allow_create=False)) if name else None

                def discovery_factory(cc):
                    if not (s.exa_api_key and s.exa_monthly_cap_usd > 0):
                        return None
                    from .sources.exa import ExaClient
                    budget.set_cap(cc, "exa", "monthly", BudgetedExtractor.period(), s.exa_monthly_cap_usd, "usd")
                    cc.commit()
                    return {"exa": ExaClient(None, s.exa_api_key), "max_searches": s.discovery_max_searches_per_run,
                            "max_new_requests": s.discovery_max_new_requests_per_run, "interval_days": s.discovery_interval_days, "est_cost": s.exa_est_cost_per_search}
                from .research import company_facts
                return {**build_handlers(connector_factory=lambda sid: build_connector(c, sid), extractor_factory=extractor_factory, resolve_org_factory=resolve_factory,
                                         fetch_factory=lambda: sync_fetch(lambda: None), discovery_factory=discovery_factory),
                        company_facts.KIND: company_facts.job}      # background lane: fills in missing company facts with Exa

            # lanes: what a person is waiting for runs on its own worker; scheduled reading and maintenance run on the other
            INTERACTIVE = ["account_scan", "verify_url", "sheet_import_xlsx", "sheet_sync", "sources_validate_all", "evaluate_event", "add_company", "account_brief", "campaign_draft",
                           "account_profile", "vendor_intel", "person_profile", "people_search"]

            def lane(name: str, own_conn, kinds_fn):
                handlers = make_handlers(own_conn)
                kinds = kinds_fn(list(handlers))
                print(f"lane {name} started; jobs: {kinds}")
                last_beat, done = 0.0, 0
                while True:
                    try:
                        r = run_one(own_conn, f"{name}-{time.time_ns()}", handlers, kinds)
                    except Exception as exc:  # noqa: BLE001 - a lane never dies: log, reconnect-safe rollback, carry on
                        print(f"lane {name} error: {type(exc).__name__}: {exc}")
                        try:
                            own_conn.rollback()
                        except Exception:  # noqa: BLE001
                            pass
                        time.sleep(5)
                        continue
                    done += r is not None
                    if time.time() - last_beat > 60:
                        beat(own_conn, "worker" if name == "background" else f"worker-{name}", {"jobs_since_start": done,
                             # what this worker can do: the web app enables research buttons from this, since the keys live here
                             "can": {"exa": bool(s.exa_api_key and s.exa_monthly_cap_usd > 0), "ai": bool(s.anthropic_api_key), "vault": bool(s.vault_key), "gmail": bool(s.vault_key and s.google_client_id and s.google_client_secret)}})
                        own_conn.commit()
                        last_beat = time.time()
                    if r is None:
                        time.sleep(3 if name == "interactive" else 5)

            threading.Thread(target=lane, args=("interactive", connect(s.database_url), lambda all_: [k for k in INTERACTIVE if k in all_]), daemon=True).start()
            lane("background", conn, lambda all_: [k for k in all_ if k not in INTERACTIVE])
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
