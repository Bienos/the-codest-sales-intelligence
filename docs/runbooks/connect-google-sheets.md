# Connect Google Sheets (read-only)
1. Create a Google Cloud service account (no keys in the repo). Enable the Sheets API.
2. Share **both** workbooks with the service-account email as **Viewer** (Workbook A `1J9vDQK3…`, B `1wJKkovl…`).
3. Mount the JSON key as a secret and set `GOOGLE_APPLICATION_CREDENTIALS`.
4. `python -m ri.cli seed` then `python -m ri.cli import-sheets A` and `B`.
5. Expect `completed`, or `paused_schema_drift` naming the tab/columns (fix the contract in `sheets/contracts.py` via review) or `blocked` (credentials/permission).
6. Review `/admin/identity` for ambiguous matches before enabling any monitoring on imported accounts.
The client has no write method and requests only `spreadsheets.readonly`.
