# Security review, 21 Nov 2026

Scope: the deployed API, worker and SPA after the sheet-import and source-admin work. Method: code review of every route and the static file handler, targeted exploit attempts against a local instance, dependency audit, secret scan, full test run (346 passing).

## Fixed in this change
| # | Severity | Finding | Fix | Regression test |
|---|---|---|---|---|
| 1 | **Critical** | The SPA fallback joined the request path onto the build folder. `GET /..%2f..%2f..%2f..%2f..%2f..%2fetc/hostname` returned the file. Any readable file on the container (source, mounted secrets) could be fetched without signing in. | Resolve the path and serve only files inside the build folder. | `test_static_server_cannot_read_outside_the_build_folder...` |
| 2 | High | No security headers. | CSP (self plus Google Sign-In only), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, HSTS in production, `Cache-Control: no-store` on every `/api` response. Verified in a real browser: fonts, sign-in script and app all load with no violations. | same test |
| 3 | Medium | New upload and source-URL features would have been an abuse surface. | Upload: admin only, 25 MB cap, zip signature and expansion check, workbook recognised by tab names, filename sanitised, bytes deleted after import. Source URLs: HTTPS only, host pinned to the source's own domain, no IP literals, ports or credentials, no redirects followed, a passing server-side test required before activation. | `test_upload_rbac...`, `test_source_url_guards...` |
| 4 | Medium | `react-router` 6.26 had a high-severity advisory. | Upgraded to 6.30.6. |  |

## Accepted or open
| Item | Why | What would close it |
|---|---|---|
| 2 moderate advisories remain in `react-router` 6.x (backslash open redirect in `<Link>`; SSR hydration). | The app has no server-side rendering and never navigates to user-supplied targets. | Migrate to React Router 7 (breaking). |
| No request rate limiting. | Sign-in is delegated to Google and every route needs a verified token, but a valid user could hammer the API. | Add a limit at the Render edge or a small in-app limiter. |
| Session token lives in `sessionStorage`. | Standard for an SPA with Google ID tokens, cleared on tab close. The CSP removes the main route to steal it (injected script). | A cookie session behind a backend-for-frontend. |
| Ownership is empty until Pipedrive is connected. | Unowned accounts are visible to every seller by design. | Connect Pipedrive. |
| Lint backlog (about 250 style findings, mostly FastAPI `Depends` defaults). | Style, not defects. | Configure ruff to ignore B008 and fix the rest. |

## Checked and fine
Webhook uses constant-time Basic auth and is audited on failure. All SQL is parameterised; the only f-strings insert fixed scope fragments. API docs are disabled in production. `/metrics` requires an admin permission. Row scoping returns 404, not 403, outside a seller's scope. No secrets in the repository (CI also greps for them). Supabase tables have row-level security enabled with no policies, and the upload table follows the same rule.
