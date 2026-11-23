# Design review follow-through

The implementation starts with Claude's six frontend commits through `fd805aa`. The follow-through completes the shared tab panels and keyboard navigation, puts opportunities before supporting tables on Today, exposes the verdict and next action early on Opportunity, and finishes the Accounts, Feed and Renewals workflows. Administration now keeps provider budgets under Usage, formats approval details and outcomes, and exposes older audit entries through the existing API limit.

The dark pink button fill from the earlier pass was retained for contrast. Dark mode and changes to email reveal approval policy are open product decisions. The UI uses the data the current APIs provide. Owner names are only shown when supplied, and country filtering in Explorer uses exact country codes. The Accounts API does not have a dedicated pending-qualification filter. Renewals identifies TED as its source without making a UK coverage claim.

## Verification

- The production frontend build, including TypeScript, passed. The local API and built frontend smoke checks passed against a PostgreSQL database seeded with synthetic data.
- Browser checks exercised keyboard tab selection and panel relationships, a Today dismissal with the original reason code, a failed Feed request followed by a successful retry, the Renewals link to a prefilled account form, Accounts filters and research brief navigation, the Opportunity action order, and the mobile Administration menu. The dismissal request was intercepted in the browser.
- Chromium checked 94 combinations of screens, viewport sizes and applicable loading, empty and error states. This sweep found no browser exceptions, page-level horizontal overflow, or violations from the axe rules it ran. Twelve mobile combinations still recorded layout shift above 0.1, including the Today and Accounts errors, the Renewals empty state, and Accounts loading.
- The refreshed captures use a 1,440 px desktop viewport and a 390 px phone viewport at 2× pixel density. See [before](before/) and [after](after/). The new Account Background view combines the former Initiatives, Signals and evidence, Suppliers, Access and CRM, and Research and coverage tabs.

The [capture script](tooling/capture.js) expects the local Vite and API servers, Playwright with Chromium, and axe-core. Pass `TOKEN_FILE` for a local synthetic admin token and `ORG_ID` and `OPP_ID` for the demo account and opportunity. `ONLY` selects screens and `STATES` selects variants. `OUT`, `FORMAT=jpg`, and `DSF=2` control the output.
