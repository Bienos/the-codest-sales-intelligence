# Import the two workbooks

1. In Google Sheets, open each workbook and choose **File, Download, Microsoft Excel (.xlsx)**.
2. Sign in as an administrator, open **Administration, Imports** and upload each file.
3. The page shows each upload as queued, importing, then completed. A full import takes a few minutes on the hosted database.
4. A tab whose header row differs from the contract shows `drift`. That tab is paused and the previous data stays. Fix the sheet or update the contract in `backend/src/ri/sheets/contracts.py`.
5. Re-uploading the same file changes nothing. Ambiguous company names go to **Identity review**.

The file is deleted after import. Only its hash, size, uploader and result are kept.

Automatic hourly sync from Google Sheets needs a read-only service account (`GOOGLE_APPLICATION_CREDENTIALS`) with each workbook shared to it. Until then, upload after each material edit.

Note: switching from uploads to the live Sheets API re-stages rows once, because the live API identifies tabs by Google's internal id and uploads identify them by title. Expect a one-off duplicate set of identity-review items.
