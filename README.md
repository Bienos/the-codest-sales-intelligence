# The Codest Revenue Intelligence Platform

Evidence-first revenue intelligence: monitored sources → verified events → signals → technology initiatives → supplier/buyer routes → qualified, human-approved sales actions in Pipedrive. Design package: `docs/spec-package/` (source of truth). Status and gaps: `docs/releases/RELEASE_REPORT.md`. **Not production-ready — read the report.**

```
backend/   FastAPI + PostgreSQL domain (src/ri), SQL migrations, rules/*.yaml, 330 tests
frontend/  React SPA (Today, Accounts, Opportunity, Market Explorer, Administration)
deploy/    Compose reference, backup + restore drill, env template
docs/      architecture validation, traceability, integration inventory, risks, ADRs, runbooks
reference/ original 64-rule tracker (unmodified, SHA-256 checked)
```

## Run locally
```bash
cd backend && pip install -e ".[dev]"
export DATABASE_URL=postgresql://postgres@localhost/ri RI_ENV=development RI_AUTH_MODE=dev RI_DEV_AUTH_SECRET=$(openssl rand -hex 24)
python -m ri.cli migrate && python -m ri.cli seed
PYTHONPATH=src python scripts/seed_demo.py        # SYNTHETIC data, development only
cd ../frontend && npm ci && npm run build && cd ../backend && python -m ri.cli serve
TEST_DATABASE_URL=postgresql://postgres@localhost/postgres pytest -q
```
Safety defaults: CRM writes, outbound, paid enrichment are **off**; provider budgets are **zero**; every source is `pending_connector_validation`; nothing is sent from this system.
