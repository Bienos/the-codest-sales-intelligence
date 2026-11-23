# Design and plain-language review: internal screens

**Date:** 23 November 2026 · **Scope:** every route, tab, sub-tab and panel in `frontend/src` · **Status:** report only, no code changed. Waiting for approval.

## How this was done

- **Running the app:** I ran the real backend locally against a fresh Postgres, migrated and seeded with `backend/scripts/seed_demo.py`. That data is synthetic and development-only. It produced 1 account, 3 opportunities, 64 signals and 89 sources. Nothing touched production.
- **Fixture data:** screens the seed leaves empty (Leads list, Renewals, shortlist, deadlines, approvals, usage, verification, discovery, identity review, jobs, account brief and drafts) were filled through Playwright route interception. Every name in that data starts with `FIXTURE`. The fixture and capture scripts are in `docs/design-review-2026-11-23/tooling/`.
- **What was captured:** 51 screens and sub-tabs at 1440px and 390px (102 captures). Loading, error (HTTP 503) and empty states were captured for the screens with a single main data call (Today, Feed, Renewals, Search results, and others where an empty variant exists).
- **Automated checks on every capture:**
  - axe-core (WCAG 2 A/AA plus best practice);
  - horizontal overflow;
  - tap targets under 44px;
  - layout shift (CLS);
  - raw `snake_case` and internal codes in visible text.
- **Screenshots:** in `docs/design-review-2026-11-23/before/` as `<screen>__<width>.jpg`. They are 1× JPEGs to keep the repo small; I'll take 2× ones for the after-pairs.
- **Dark mode:** captured too, but every dark screenshot is byte-identical to the light one, because the app has no dark theme. See finding V1.
- **Code check:** every explanation proposed below was checked against `backend/src/ri/…`. File references are given where it matters. Anything I couldn't confirm is listed in section 4.

### Measured across the app

| Check | Result |
|---|---|
| Horizontal page scroll at 390px | **None.** Wide tables scroll inside their card instead. On phone that hides the Score column on Leads and dates on Today. |
| Tap targets under 44px at 390px | Worst on Leads (32), Leads add form (32), Admin › Connections (11), Search (8). These are chips, the star, pills and checkboxes. |
| Colour contrast (WCAG AA) | **Fails on every primary button:** white on `--pink` measures 4.16:1 at 12.5px, and 4.5:1 is needed. "Muted" text, made with `opacity: .75–.8`, falls to 3.97–4.49:1. |
| Focus visibility | `:focus-visible` is a 3px **navy** outline, which can't be seen on the navy sidebar or hero. Found by CSS inspection. |
| Headings | No page has an `<h1>`: page titles are `<h2>`. Flagged on all 51 screens. |
| Layout shift (CLS) | 0.31–0.39 on the Account page and most Admin tabs at 1440px (35 of 102 captures are over 0.1, the "good" limit). It comes from text-only "Loading" being swapped for content. |
| Raw codes on screen | `snake_case` leaks on 16 of 51 screens. Rule IDs (`PRG-01`) and gate IDs (`G4`) appear without names on 13. |
| Dark theme | **Doesn't exist.** `tokens.css` defines only light values and nothing reads `prefers-color-scheme`. |

---

## 1. Top 10 improvements, ranked by user impact against effort

| # | Change | Why it matters | Effort |
|---|---|---|---|
| 1 | **Scope `landing.css` to the sign-in page** (`.land …`). | Three bugs with one cause. Global `.sig`, `.gates` and `.tick` rules from the sign-in animation set `opacity: 0`. Result: **all 64 signals in Library › Signals are invisible**, Admin › Quality's metrics are blank, and the ticks in Today's "Why Today is quiet" checklist don't show. | **S** |
| 2 | **One label dictionary** in `ui.tsx` for every enum the API sends, replacing `humanize()`. It covers states, routes, demand, buyer status, action types, gate reasons, ICP bands, approval kinds, roles and contact statuses. | Removes `actionable_hypothesis`, `role_identified`, `strategic_5000_plus`, `prime_subcontract`, `create_activity`, raw JSON and Python lists from Today, Opportunity, Explorer, Approvals and Account. It's the single biggest "understand in 10 seconds" win. | **M** |
| 3 | **Name each concept once** and use the glossary in section 3 everywhere. Rename nav "Leads" to "Accounts", and the Feed's "Leads waiting" to "Links to check". | The same thing currently has 2–4 names. "Lead" means both "a company" and "an unchecked search hit", and "Existing list", "in master list" and "On your lists" all mean the same. | **S** |
| 4 | **Explain the three scores where they appear**, and show the opportunity score on the Opportunity page. | "Sheet score" (/100, from the workbook), "Account score" (no upper limit; Hot ≥ 9) and "Opportunity score" (/100, six parts) all appear as bare numbers. "Research complete 43%" actually means "3 of 7 checks answered". | **S** |
| 5 | **Today: lead with the work.** Merge cards for the same programme into one card that lists each route. Cut the hero to two tiles (Urgent, To review) and move source counts into the coverage line. | Today currently shows five stat tiles, a coverage notice, hot accounts and deadlines before the first card. On phone the first card starts about 1,300px down. The seed's three cards are the same programme with different routes, and read as duplicates. | **M** |
| 6 | **Opportunity: verdict, score and next action at the top.** Rewrite gate reasons in plain words, show unknown checks as amber (not red), and fold the trace away. | It's the page a seller acts from, but its first screen is seven terse gate codes with machine reasons. The verdict line Today shows ("Our read") isn't on this page at all. | **M** |
| 7 | **Explorer: curated columns per type**, with the clickable title first and tender notices as links. | Columns are generated from API keys ("Display name", "Score total"). The only link (opportunity title) is the 11th column, off-screen at 1440px. Tender URLs show as plain text. | **M** |
| 8 | **Accessibility pass:** AA-safe primary button colour (needs a brand decision, see V2); a solid `--muted` token instead of opacity; a visible focus ring on navy; one `<h1>` per page; scrolling tables made keyboard-focusable. | Fixes every axe "serious" issue found. The button contrast affects every primary action in the app. | **S–M** |
| 9 | **Shared loading, error and empty components.** Keep the page header during loading, show skeletons sized like the content, and make errors say what happened and offer "Try again". | Today: loading is the word "Loading" on a blank page, and an error is just "Service Unavailable" with no title, explanation or retry. These swaps also cause the 0.31–0.39 CLS. | **M** |
| 10 | **Phone tables become cards** under 640px (Leads, Today hot accounts and deadlines, Renewals winners), with 44px targets for chips, the star and pills. | On a 390px phone the Leads score is cut to "Ho…"/"Wa…", names break mid-word ("SYNTHE / TIC"), and 32 controls are under 44px. | **M** |

Just outside the top 10, but cheap:
- **Fix the Leads presets.** "Hot or warm" only returns Hot, because the backend matches `band` exactly. "UK banks and fintechs" returns every UK company. Rename them; no backend change is needed.
- **Fix the Usage chart**, which spills out of its card.
- **Stop Approvals** showing raw JSON as its success message.

---

## 2. Screen by screen

Tags: **[H]** Hierarchy · **[W]** Wording · **[E]** Explanation · **[V]** Visual · **[M]** Mobile · **[A]** Accessibility · **[P]** Performance

### 2.0 Shell and shared components (`App.tsx`, `components/ui.tsx`, CSS)

**Before:** any screen; the nav is visible in all of them.

| Tag | Issue | Fix | Effort |
|---|---|---|---|
| W | Nav labels don't match page titles or the Guide. "Leads" opens a page the Guide calls "Accounts". "Explorer" opens "Market Explorer". "How it works" is the Guide. | Nav: Today · Accounts · Market feed · Renewals · Explorer · Library · Knowledge base · How it works · Administration. Page titles match exactly. | S |
| H | Nav order mixes daily work with reference material. | Group with small separators: **Work** (Today, Accounts, Market feed, Renewals) · **Look up** (Explorer, Library, Knowledge base, How it works) · **Admin**. | S |
| W | Signed-in role shows as `sales manager` (underscores swapped for spaces). | Role labels: `bdm` → "Business development", `sales_manager` → "Sales manager", `research_analyst` → "Research analyst", `delivery_expert` → "Delivery expert", `admin` → "Administrator", `compliance` → "Compliance". | S |
| W | Before you're signed in, the app shows "Loading" on a blank page. | Keep the shell and say "Signing you in…". | S |
| A | Focus ring is navy-on-navy in the nav, and on the Today and Guide heroes. | Add `.nav :focus-visible, .hero :focus-visible { outline-color: var(--white); }`. | S |
| A | Page titles are `<h2>`, and no page has an `<h1>`. | `PageHeader` renders `<h1>` with the same class. Use `PageHeader` on every page; 9 pages currently hand-roll the header. | S |
| A | `GateBar` tiles are `tabIndex=0` with `title=` tooltips. Tooltips don't appear on touch, and `cursor: help` is the only hint they exist. | Show the question under the gate name. Drop `title` and the redundant tab stop. | S |
| V | `Badge` gives every status its own coloured dot, so a card can carry 4–6 coloured chips competing with the one that matters. | Use a dot only for status: ok, warn, bad. Plain facts (country, employees) become plain text, not badges. | M |
| V | `Err` shows the server's raw message (e.g. "Service Unavailable", `invalid token: PyJWKClientError`). | `Err` maps status to plain text and keeps the detail in a `<details>`. 401/403 → "Your session has ended. Sign in again." 404 → "This item no longer exists." 5xx/network → "The server didn't respond. Try again in a minute." Add a `onRetry` prop. | S |
| P | Six screens poll on a timer (Imports 8s, Verification 10s, Pipeline 15s, briefs, drafts and prospects 6s) even when the browser tab is hidden. | Pause when `document.hidden`. | S |

### 2.1 Today (`/today`)

**Before:** `today__1440.jpg`, `today__390.jpg`, `today__1440__loading.jpg`, `today__1440__error.jpg`, `today__1440__empty.jpg`

**Most important thing:** the first card to act on. **Currently most prominent:** five stat tiles in a navy hero.

| Tag | Issue | Fix | Effort |
|---|---|---|---|
| H | Five tiles, two of them system health ("Accounts loaded", "Sources live 2/83"), sit above all the work. Then come coverage, hot accounts and deadlines, and only then cards. | Hero keeps two tiles: **Urgent** and **To review** (card count). Source and account counts move into the coverage line. Order below: urgent cards → other cards → Hot accounts → Deadlines → From your lists. | M |
| H | Several cards for the same account and programme, differing only by route (vendor, prime, direct), read as duplicates. | Group by programme: one card, with a "Routes in" list whose rows each have their own next action and "Open". | M |
| W | Hero sub-text: "3 material changes across your accounts. Repeated coverage of one release is shown once, and dismissed items stay hidden until something material changes." | → "3 things to review. Each story is shown once. Anything you dismiss stays hidden unless it changes." | S |
| W | Empty hero: "1 accounts are loaded from your workbooks…" | → "1 account loaded…" (pluralise properly). | S |
| W | Tile "Verified opportunities". | → "To review". | S |
| W | Section "Developments in your existing accounts" / "New qualified discoveries outside the master lists". | → "Your accounts" / "New companies worth a look". | S |
| W | Section "Public sector tenders (separate lane: buyers follow procurement rules)". | → "Public tenders". Subtitle: "Public bodies buy through formal tenders, so these follow their own rules and deadlines." | S |
| W | Card badges "MEDIUM", "Existing list", "Ownership unverified". | → "Medium priority", "On your lists", "No owner in Pipedrive". | S |
| W | "Still unknown" lists raw gate reasons ("external demand unknown: not established", "buyer role_identified"). | Use the gate-reason dictionary (§3.2), e.g. "We don't know yet if they'll use outside help", "We know the role but not the person". | S |
| W | Fact "Route and access: vendor via Synthetic Platform Vendor: unknown". | → Label "How we'd get in", value "Through the platform vendor (Synthetic Platform Vendor) · not confirmed". | S |
| W | Next-action owner "Pipedrive user 555". | Show the person's name (`/api/admin/users` already returns `pipedrive_user_id`), else "Unassigned". | S |
| W | Next-action type in title case from the enum ("Investigate vendor route"). | Use the action dictionary: `investigate_vendor_route` → "Check the vendor route"; `qualify_external_demand` → "Ask if they'll use outside help"; and so on. | S |
| W | Dismiss reasons such as "no external demand" and "not commercially relevant". | → "Not relevant", "Wrong company", "Out of date", "Duplicate", "They won't outsource this", "Not worth pursuing", "Someone else owns it", "Other". | S |
| E | "Score" in Hot accounts is a bare number. | Header "Signal score" plus an info line: "Adds up recent verified signals; different kinds count for more than repeats. Hot is 9 or more, warm 4 or more. Not a probability." (`backend/src/ri/scoring.py`) | S |
| E | "Signals" column shows `4 · PRG, CAP, SRC`. | Show names: "4 · funded project, hiring, buying route". **Wording bug:** the frontend maps `REL` to "supplier change", but the backend group is "They already know us" (relationship). | S |
| E | Evidence confidence "medium" with no explanation. | Helper: "High: an official or company source, or two independent sources. Medium: one company source or two others. Low: one source." (`opportunity/model.py: evidence_confidence`) | S |
| E | Deadlines footnote "Each date was checked … by a second person." | Keep it, it's accurate (`DeadlinesAdmin` requires a second approver). Move it under the section title as the subtitle. | S |
| M | Hot accounts and deadlines tables scroll inside their card. The date is clipped and names break mid-word. | Card layout under 640px (§1 #10). | M |
| M | The hero takes about 470px on phone before any content. | Two tiles side by side, about 180px. | S |
| A | "Dismiss with reason" select plus a disabled Dismiss button is two steps with no label tying them together. | One "Dismiss…" button that opens the reason list; choosing a reason dismisses. | S |
| V | Inline styles: `fontSize: 18`, `font: "800 11px …"`, `padding: "0 24px"`. | Replace with `.stat .of` and `.k` classes. | S |
| P | `Loading` then full swap causes layout shift. Stats show "·" until `/api/overview` returns. | Hero skeleton with fixed tile heights. | S |
| — | **Error state:** "Service Unavailable" alone, with no header. | → Header stays. "Today couldn't load. The server didn't respond. [Try again]". | S |
| — | **Empty state:** good copy, but the checklist ticks are invisible (bug #1). | Fixed by #1. | — |

**From your lists** (`Prospects.tsx`, embedded in Today):

| Tag | Issue | Fix | Effort |
|---|---|---|---|
| W | "Starting point, not verified. These come from your two workbooks. The platform has not checked the sources yet…" | → "**From your workbooks. Not checked yet.** Open the source and confirm it before you act, or press *Check this source* and we'll read it for you." | S |
| W | Button "Verify this source", and "Verify top 10" (tooltip only). | → "Check this source" and "Check the top 10". Visible helper: "Reads the original page for the first 10 unchecked items in this list and keeps only what it can quote." (`api/intel.py` verify-batch) | S |
| E | Badge "Sheet score 86". | → "Workbook score 86/100", tooltip and helper "From the score column in your workbook. Not calculated here." (`api/lists.py`: "Opportunity score /100" / "Buying Signal Score") | S |
| W | "High (two sources) confidence" is built by splitting a sheet string. | → "Workbook confidence: High". | S |
| W | "Possible software vendor: partner route only". | → "Looks like a software vendor: approach as a partner". | S |
| M | The rank column takes a full-width navy strip on phone. | Make the rank an inline badge. | S |

### 2.2 How it works (`/guide`)

**Before:** `guide__1440.jpg`, `guide__390.jpg`. This is the clearest page today, so changes are small.

| Tag | Issue | Fix | Effort |
|---|---|---|---|
| W | "Where do I click?" omits Market feed and Renewals, and names Accounts and Explorer differently from the nav. | Add rows: Market feed ("Every verified finding, newest first, with why it could be work for us") and Renewals ("Public IT contracts likely to be re-tendered soon, and who holds them now"). Use nav names. | S |
| W | Glossary is missing the terms people actually trip on. | Add Account, Opportunity, Finding, the three scores, Priority, Route, Workbook. Use section 3 of this report. | S |
| V | The gate legend has an uneven top border: the first row gets the 4px `.g` border and the second doesn't. | `.gate-legend .g { border-top-width: 1px }`. | S |
| W | "Its scores are provisional weights that have not yet been calibrated against outcomes." | → "Scores are our best current weighting. They haven't yet been checked against deals won and lost, so treat them as a guide, not a forecast." | S |

### 2.3 Market feed (`/feed`)

**Before:** `feed__1440.jpg`, `feed__390.jpg`, `feed-market__1440.jpg`, `feed__1440__empty.jpg`, `feed__1440__error.jpg`

**Most important thing:** what happened, and whether it's work for us. **Currently most prominent:** the headline, which repeats the quote word for word.

| Tag | Issue | Fix | Effort |
|---|---|---|---|
| H | Headline and quote are identical on every item, so the same sentence appears twice. | When `summary == claim`, show the quote only as the source line under the headline. | S |
| H | Card order is type badge → company → headline → quote → dates → rule pills → fit box. The fit ("Likely work for The Codest") is the takeaway but comes last. | Order: company and headline → "Could be work for us: Team extension" line → quote → details (dates, signals) collapsed. | M |
| W | Intro: "Only events whose claims quote the original page appear here. Searches and sheet signals that have not been checked are listed as leads, never as findings." | → "Everything here was checked against the original page. Unchecked links wait on the right until they're read." | S |
| W | Type badge `vendor selected`, `procurement published`. | Event-type dictionary: "Vendor chosen", "Tender published", "New tech leader", "Hiring push", and so on. | S |
| W | Quote line "authority n/a · published unknown · quote found on the page". | → "Company website · quote found on the page". Drop "n/a" and "unknown" parts entirely. Authority is the source tier (S1–S5, `readmodel.py`), so show its name: "Official record", "Company's own site", "Vendor or integrator", "Trade press", "Search find". | S |
| W | **Contradiction:** the quote says "published unknown" while the line below says "published Nov 19". These come from different fields (claim vs event). | Show one publication date per card, from the event. | S |
| W | Signal pills "PRG-01 Core banking or ledger replacement…". | Drop the code from the label and keep it in the `title`. | S |
| W | "Leads waiting", "Found by search. Not findings until the original page is read and quoted." | → "Links to check", "Found by search. We'll read each page and only list it above if it holds up." | S |
| W | Programme link "Programme: Project Northstar" goes to `/today`. | Link to the opportunity (`/opportunities/:id`) or the account. | S |
| W | Tabs "Main markets", "Rest of Europe and US". | → "New companies: core markets", "New companies: elsewhere". "Your accounts" stays. | S |
| V | "Saw something we missed?" form: the second label sits beside the input because labels and inputs aren't stacked. Button uses `className="btn"`, not the primary style. | Stack label and input with `display: grid; gap: var(--space-1)`. | S |
| E | "Only likely work for us" checkbox has no explanation. | Helper: "Hides findings our sales plays don't match to any service." | S |
| — | **Empty state:** good, says why. | Keep. Add a link: "widen the time window". | S |
| M | Filters wrap into four rows on phone. | Put filters behind a "Filter" disclosure on phone. | S |

### 2.4 Renewals (`/renewals`)

**Before:** `renewals__1440.jpg`, `renewals-winners__1440.jpg`, `renewals__390.jpg`, `renewals__1440__empty.jpg`

| Tag | Issue | Fix | Effort |
|---|---|---|---|
| E | Intro says "The end date is the award date plus the contract length". The backend uses the **notice dispatch date**, falling back to the publication date (`renewals.py: awarded = dispatch-date or publication-date`). | → "**When do these contracts end?** We add the contract length in the official award notice to the date the notice was sent. If the notice gives no length, we assume 3 years and label it *estimated*." | S |
| E | Badges "Length stated in notice" / "Length assumed (36 months)" are equal weight. | Make the end date the label: **"Ends about Jan 2027"** for stated, **"Ends about Apr 2027 (estimated)"** for assumed. Assumed gets the warn dot. | S |
| W | Card line "Awarded 23 Jan 2024 · probably ends 22 Jan 2027". | → "Notice sent 23 Jan 2024 · ends about Jan 2027". Day precision is false precision for an estimate. | S |
| W | "TED award notice" isn't explained anywhere in the UI. | Add to the tab subtitle: "Source: TED, the EU's official tenders journal, and the UK equivalent." **Needs checking:** UK coverage; see §4. | S |
| H | The card leads with four badges, then the buyer. | Order: buyer (title) → time left → what it is → current supplier. | S |
| H | No next step on cards for buyers that aren't on your lists. | Secondary action "Add as account" pre-filling `AddCompany`. It needs a website, so offer it only when the buyer is linked, or open the form empty. Frontend only. | M |
| W | Tab "Who wins this work". | → "Current suppliers". Subtitle: "Who won the most public IT contracts in our markets in the last 3 years. A frequent winner is a likely competitor or partner." | S |
| — | **Empty state** "The contract history is still loading…" is good. Under it, "Nothing matches." duplicates it. | Show one or the other. | S |

### 2.5 Search (`/search`)

**Before:** `search-start__1440.jpg`, `search-results__1440.jpg`, `search-results__1440__empty.jpg`

| Tag | Issue | Fix | Effort |
|---|---|---|---|
| W | "Searches your accounts and what the sheets say about them, the 64 signals, every source and feed, verified events, pages read, and the knowledge base. All words must match." | → "Searches accounts, signals, sources, findings and the knowledge base. Every word must match." | S |
| W | Result meta shows `vendor_selected`, `pending_connector_validation`. | Use the dictionaries. | S |
| W | Group headings "Signals (the 64 rules)", "Accounts and what your sheets say". | → "Signals", "Accounts". | S |
| M | Example chips are 33px tall. | 44px on phone. | S |
| — | **Empty:** "Try fewer or shorter words." | Keep; good. | — |

### 2.6 Library (`/library`: Signals, Sources, URLs and feeds)

**Before:** `library-signals__1440.jpg` (shows the blank bug), `library-signal-open__1440.jpg`, `library-sources__1440.jpg`, `library-urls__1440.jpg`

| Tag | Issue | Fix | Effort |
|---|---|---|---|
| V | **Bug: the Signals tab is blank.** The global `.sig` rule in `landing.css` sets `opacity: 0; max-height: 190px`. | Top 10 #1. | S |
| W | "These are the 64 original signals, with the original wording kept. **Active** … **Defined** … gold-set test." | → "The 64 buying signals we look for. **On**: we detect it automatically from verified findings. **Not yet on**: written down, waiting for a source or a test." | S |
| W | Status filter "Defined, not yet switched on" vs the badge "Defined". | Both "Not yet on". | S |
| E | Strength squares (1–3) have no legend on screen; the label is in `aria-label` only. | Visible "Strength" column header in the summary row, and helper "How strongly this signal suggests outside engineering work: 1 to 3." | S |
| W | Sources groups "S1 Official records … S5 Discovery". | Keep the names and drop the codes, or show the code in small type after the name. | S |
| W | Source card "Collects by change monitor, every 1d", "Health unconfigured", "Feeds rules PRG-01 …". | → "Checks the page daily", "Not set up yet", "Feeds signals: Core banking replacement, …". | S |
| W | URLs table "Not yet tested" vs Sources "Pending" for the same status. | One label: "Not set up yet". | S |
| P | Sources renders about 90 cards. That's fine now, but cards have no `content-visibility`. | Add `content-visibility: auto` to `.lib-card` and `.sig`. No virtualisation needed at this size. | S |

### 2.7 Knowledge base (`/knowledge`)

**Before:** `knowledge__1440.jpg`

| Tag | Issue | Fix | Effort |
|---|---|---|---|
| W | "Not for prospects. A snapshot of the artifact at claude.ai/artifact/…; ask for a refresh when it changes." | → "Internal only: don't share with prospects. Copy taken from the original document; ask an administrator to refresh it when that changes." Keep the link, labelled "Original". | S |
| — | Empty "This document is not loaded yet" plus who can fix it. | Good. Keep. | — |
| A | The iframe has a `title`. Good. | — | — |

### 2.8 Accounts list (`/accounts`, nav "Leads")

**Before:** `leads__1440.jpg`, `leads__390.jpg`, `leads-add__1440.jpg`, `leads__1440__empty.jpg`

| Tag | Issue | Fix | Effort |
|---|---|---|---|
| W | Title "Leads", eyebrow "Your book". | → Title "Accounts", no eyebrow. Subtitle: "Every company we know about, strongest signals first." | S |
| H | 12 preset chips plus 7 dropdowns (19 controls) sit above the first row. | Keep 5 presets (Hot, Signal in last 30 days, Watching, Your shortlist, Needs a decision). Move region, size, segment and origin into a "More filters" row that's collapsed by default. | M |
| W | **Bug:** preset "Hot or warm" sends `band=hot`, and the backend matches exactly (`api/leads.py` line 60), so Warm never shows. | Rename to "Hot". Offer "Warm" separately. | S |
| W | **Bug:** preset "UK banks and fintechs" only sets `region=uk`. | Rename to "UK". | S |
| W | Presets "Added by people" / "Found by the platform". | → "Added by the team" / "Found by us". The origin dropdown uses the same words. | S |
| E | "Score" column has a bare number plus band. | Header "Signal score", with an info tooltip on the header: same text as Today. **Note:** the score is open-ended (1 decimal; Hot at 9+), so don't present it as /100. | S |
| W | "Stopped by a rule". | → "On hold: a stop signal was found". Tooltip: "Something we found says not to pursue now (for example a freeze or a recent award to a rival). The score stays at zero until it expires." **Needs checking:** example wording against the NEG rules; see §4. | S |
| W | Badge "estimate" next to employee count. | → "≈" prefix, with tooltip "Estimated headcount". | S |
| W | Candidate row "Not yet qualified · Qualify · Reject". | → "Found by us · Add to accounts / Not a fit". | S |
| E | Star button tooltip "Watch: re-read its site, news and jobs weekly". | Accurate as a target. The scheduler handles up to 25 watched accounts per run (`scheduler.py: schedule_watchlist`). If more are watched, some wait longer; see §4. | — |
| W | "Scan 3 selected" → "Queued 3 scans. New findings appear in the Market feed once verified." | → "Check 3 for news" → "Checking 3 accounts. New findings will appear in the Market feed once confirmed." | S |
| M | Table scrolls inside itself, so Score is cut and 32 controls are under 44px. | Card rows on phone: name, score and band, latest signal, star. | M |
| — | **Empty:** "Nothing matches these filters." with a hint when nothing is loaded. | Good. Add a "Clear filters" button. | S |

### 2.9 Account detail (`/accounts/:id`, 9 tabs)

**Before:** `account-overview__1440.jpg` plus one file per tab, e.g. `account-research-brief__1440.jpg`.

**Most important thing:** open opportunities and what to do next. **Currently most prominent:** two action buttons and nine tabs.

| Tag | Issue | Fix | Effort |
|---|---|---|---|
| H | Nine tabs, three of them ("Suppliers", "Access & CRM", "Research & coverage") rarely used. | Five tabs: **Overview** (opportunities plus score), **Research brief**, **Outreach**, **People** (Buyers), **Background** (Programmes, Signals, Suppliers, CRM, Coverage as sections). | M |
| H | No way back to the list. | Breadcrumb "Accounts / Northbridge Bank". | S |
| H | "Find signals now" is the primary (pink) button, but a seller's next step is usually the brief or an opportunity. | Primary: "Write research brief" (or "Open brief" once written). Secondary: "Check for news". | S |
| W | Badges "in master list", "Pipedrive owner 555", "? employees". | → "On your lists", owner's name, hide unknown employees. | S |
| W | Overview line "Project Northstar → prime subcontract via Synthetic Integrator" plus the machine `why_it_matters` string ("…route: vendor via … (unknown); external demand: unknown."). | Show `plain_reason` when present. Otherwise compose from dictionaries: "Through the integrator (Synthetic Integrator) · not confirmed". | M |
| E | Score card "Different kinds of signal agreeing count for more than the same kind repeated." | Keep; it's accurate (`scoring.py` diversity bonus up to +75%). Add: "Each signal fades as it ages." Freshness falls to 25% at expiry. | S |
| W | Tab "Initiatives", badge "analyst working title", "budget not verified". | → "Programmes", "Working title (ours)", "Budget not confirmed". | S |
| W | Signals tab columns "Rule", "Demand tier" (`D1`–`D4`), "Status". | → "Signal" (name), "What it says about demand". D1 "Asked for outside help", D2 "Likely to need outside help", D3 "Funded project, need unknown", D4 "Showing interest" (`opportunity/model.py: demand_status`; **D4 wording needs checking**, see §4). | S |
| W | Buyers table "Lane", "Role verified", "Email status" show raw values. | Dictionary values. "Role verified" → "Role confirmed". | S |
| W | Brief: "Written … by claude-haiku-5-5 from 7 stored sources." | → "Written 22 Nov from 7 sources we hold." Move the model name into a details tooltip. | S |
| W | Brief: "1 statement(s) were left out because their quote could not be found in the source." | → "We left out 1 statement we couldn't match to its source." | S |
| W | Outreach: "Claims used: C2 · built on F1". | Hide, or show the fact text on hover. | S |
| W | Outreach status "Passed the brand review". | Keep. "Rewritten 1 time(s)" → "Rewritten once to fix rule breaks". | S |
| W | Send panel: "Not usable yet: 0 found but unverified · 0 need manual review · 1 without an email · 0 suppressed." | Show only non-zero parts. "suppressed" → "opted out". | S |
| W | Find contacts: "Revealing an email costs an unset number of credit(s)". | → "The cost per email isn't set yet: ask an administrator." | S |
| W | Access & CRM: "mirror synced …". | → "Pipedrive data last copied 22 Nov, 08:47. Pipedrive is the source of truth for owner and deals." | S |
| W | Research & coverage table "Source family / State" shows raw states. | Dictionary values. Endpoint kind `careers_page` → "Careers page". | S |
| P | CLS 0.31 on every tab: "Loading…" text is swapped for the whole page. | Skeleton keeps the header and tab bar. | S |
| A | Tabs have `role="tab"` but no `aria-controls` or `role="tabpanel"`, and no arrow-key handling. | Shared `<Tabs>` component in `ui.tsx`. Used by Account, Feed, Renewals, Library, Usage and From your lists. | M |

### 2.10 Opportunity (`/opportunities/:id`)

**Before:** `opportunity__1440.jpg`, `opportunity-trace-open__1440.jpg`, `opportunity__390.jpg`

**Most important thing:** should I pursue this, and what do I do next? **Currently most prominent:** a seven-column gate bar of codes.

| Tag | Issue | Fix | Effort |
|---|---|---|---|
| H | The verdict ("Our read") from the Today card is missing. | Put the verdict line directly under the title, using the same `verdict()` as Today. | S |
| H | The overall score (e.g. 61) isn't shown anywhere, only its breakdown inside a collapsed trace. | Header row: "Medium priority · Score 61/100 · 3 of 7 checks answered". | S |
| H | On phone, Next action comes after all the evidence sections. | On narrow screens, render the action panel before "Problem and evidence". | S |
| W | Header badges "State: actionable hypothesis", "Via Synthetic Platform Vendor", "Evidence confidence: medium", "Research complete: 43%". | → "Ready to act on", "Through the platform vendor", "Evidence: medium", "**3 of 7 checks answered**". `research_completeness` is answered gates ÷ 7 (`opportunity/engine.py` line 69). | S |
| W | Gate reasons are raw: "ICP band strategic_5000_plus", "core_banking_customisation (confirmed_scope)", "buyer role_identified", "route unknown". | Gate-reason dictionary (§3.2), e.g. G1 "5,000+ staff: in our target range", G3 "Core banking customisation: scope confirmed", G6 "We know the role but not the person". | M |
| E | Unknown gates have an orange top bar and a red-looking ? icon, so four "unknown" checks look like failures. | Unknown uses the warn dot and a hollow icon with "Not known yet". Keep red for fail. Legend under the bar: "Green: confirmed · Amber: not known yet · Red: blocked". | S |
| E | Note "…never commercially qualified while gates G4 to G7 are unresolved. Prioritisation weights are provisional hypotheses, NOT win probabilities." | → "You can act on this now. It isn't qualified until checks 4–7 are answered, and most of those need a conversation with the buyer. The score ranks what to look at first; it isn't a chance of winning." | S |
| W | "Missing buying-committee roles: problem_owner, programme_owner, economic_buyer, technical_evaluator, procurement_owner, risk_security". | → "Not yet identified: problem owner, programme owner, budget holder, technical evaluator, procurement, risk and security". | S |
| W | "Play: SP-05 Banking modernisation. Primary buyer: … active signal(s) ['PRG-01', 'SRC-05']." This is a Python list printed into the sentence by the backend. | Frontend strips the bracketed list and shows signal names as chips. The underlying string comes from the playbook API; see §4. | S |
| W | "Current corporate role does not prove budget authority; buyer_authority_inferred stays true until buyer-confirmed." | → "A job title doesn't prove they hold the budget. We treat budget authority as a guess until the buyer confirms it." | S |
| W | Discovery questions are tagged `G4`, `G5`, `play`. | Tag with names: "External demand", "Supplier access", "Sales play". | S |
| W | Section "Deal progress (Pipedrive is authoritative)". | → "Pipedrive deal". The sentence "A recommendation is not a deal; creating one needs an approved CRM action." → "There's no Pipedrive deal yet. Propose one below; a colleague approves it first." | S |
| W | Trace: "Trace: scoring rule 1.0.0, initiative revision 3", "observed to verified by system:opportunity-engine (auto: evidence checklist satisfied)". | Summary "How this was scored". State history → "22 Nov 08:47: moved to *Ready to act on* automatically (evidence complete)". Keep versions in small print. | S |
| E | Score breakdown dimensions `icp`, `access`, `timing`… with basis text such as `strategic_5000_plus`. | Labels: "Company fit (15)", "Relevance to our engineering (25)", "Outside demand (20)", "Access (20)", "Timing (10)", "Relationship and delivery (10)". Basis via dictionaries. (`opportunity/model.py: score`) | S |
| W | CRM proposal fields "Activity subject" / "Justification", placeholder "A specific purpose, not 'follow up'". | Labels above inputs: "What is the activity for?" and "Why now?". On success, show a plain summary instead of a JSON `<pre>`. | S |
| A | Inputs use `aria-label` only, with no visible label. | Visible labels. | S |

### 2.11 Explorer (`/explorer`)

**Before:** `explorer__1440.jpg`, `explorer-companies__1440.jpg`, `explorer__390.jpg`, `explorer__1440__empty.jpg`

| Tag | Issue | Fix | Effort |
|---|---|---|---|
| H | Columns come from API keys. The opportunity title, the only link, is column 11 and off-screen at 1440px. | Fixed columns per type. **Opportunities:** Programme (link) · Account · Priority · Score · Route · Evidence. **Companies:** Name (link) · Country · Segment · Size · On your lists. **Tenders:** Title (link to notice) · Buyer · Status · Deadline. **Findings:** Summary · Type · Date · Checked. | M |
| W | Values `prime_subcontract`, `actionable_hypothesis` (breaking mid-word), `unknown`. | Dictionaries. | S |
| W | Title "Market Explorer", eyebrow "Search the market", entity select "Entity". | Title "Explorer". Select label "Show". Options: "Opportunities", "Companies", "Public tenders", "Findings". | S |
| W | "Ranking: opportunity prioritisation score (provisional weights; not a probability). Confidence is a separate column." comes from the API. | Frontend label per type: "Sorted by score, highest first". | S |
| W | Country filter is free text "Country (GB, DE…)". | Use the same region select as Accounts. | S |
| H | The coverage banner duplicates Today's. | Show only when unhealthy, as one line with "See what's running". | S |
| — | **Empty:** "No results." plus the API's coverage note. | → "Nothing matches. Remember we only see the sources we watch, so no result doesn't mean nothing is happening." | S |

### 2.12 Administration (`/admin/*`, 20 tabs)

**Before:** `admin-<tab>__1440.jpg` for each tab.

**Shell**

| Tag | Issue | Fix | Effort |
|---|---|---|---|
| H | 20 tabs in two unlabelled rows. "Costs" has a route but no tab, so it's orphaned. | Group as a left sub-nav. **Status:** Pipeline, Jobs, Usage. **Data:** Sources, Connections, Imports, Knowledge, Deadlines. **Review queues:** Approvals, Identity review, Verification. **Quality:** Coverage, Precision, Quality, Rules, Discovery. **Access:** People, Integrations, Audit. Fold Costs into Usage. | M |
| W | Eyebrow "Control plane". | Remove. | S |
| H | Approvals is where sellers are sent from Account and Opportunity, but it's buried as tab 9 of 20. | Pin "Approvals (n waiting)" first in the sub-nav, with a count. Also link from the nav when something is waiting. | S |

**Per tab** (each S unless noted)

| Tab | Issues and fixes |
|---|---|
| **Pipeline** | [H] "Do next" is the takeaway but sits in the right column: move it to the top. [W] Mode badges "automatic once a source is live", "off/Off" twice: drop the duplicate. [W] Error detail "server_error (3 failures)" → "The site returned an error 3 times in a row". [E] Funnel "Where things stop" is good; keep it. |
| **Sources** | [W] "Test from server", "Activate source", "Licence and permission note". Keep the labels; they're for admins. [W] "URL basis:" → "Why this address:". [A] The URL input has `minWidth: 260` inline, so it wraps oddly on phone: use the token class. |
| **Connections** | [A] 64 contrast failures: `.conn-meta` and `.conn-addr` use opacity. Use the solid `--muted` token. [W] Card meta "attention · tier S4" → "News and trade press · Trade press". |
| **Integrations** | [W] Env var names (`RI_OUTBOUND_ENABLED`) are right for admins. [W] Apollo "Price per reveal: not set (RI_APOLLO_CREDITS_PER_REVEAL)": keep. Fine. |
| **Imports** | Clear. [W] "Workbook A / B" → use workbook names if known ("Accounts workbook", "Signals workbook"); **needs checking**, see §4. [W] Status badge `completed` → "Imported". |
| **People** | [W] Roles via the role dictionary. [A] Empty `<th>` for the action column: add `<span class="sr">Actions</span>`. |
| **Knowledge** | Clear. |
| **Identity review** | [W] Reason `ambiguous_match` → "Could be more than one company". Note field: make the "required" visible, and explain "Every decision is logged with your note." |
| **Approvals** | [W] Title "crm activity: create_activity" → "Pipedrive activity: Ask vendor about partner programme". [W] JSON payload → key/value list in plain words. [W] "Target pipedrive 9001" → "Northbridge Bank in Pipedrive". [W] Success message is `JSON.stringify(response)` → "Approved. It will run when someone presses *Run now*." [W] "Execute approved action" → "Run now". [E] On CRM writes, messages and enrolments add: "Someone other than the requester must approve this." `approvals.py` blocks self-approval for these (`TWO_PERSON`) but **not** for email reveals, so don't show it there. **M** |
| **Jobs** | [W] Status chips `permanent_failed: 2` → "Failed for good: 2". Dead letters table has no header row: add "Job · Error · Detail · Action". |
| **Verification** | [W] Intro → "Each row is a page we were asked to check. We read it and keep only events we can quote from it." Chip labels via dictionary (`no_event` → "Nothing quotable"). |
| **Discovery** | [W] "Exa discovery" → "Web search discovery (Exa)". [W] "Key is set on the worker only" → "Key not visible here (set on the worker)". [W] `rate_limited` error → "Search provider asked us to slow down". |
| **Usage** | [V] **Bug:** the daily chart spills out of its card (fixed 16px columns × days). Use `flex: 1 1 0; min-width: 2px`. [H] Lead with money: "$17 of about $40" for Anthropic, not "122,000,000 of 250,000,000 tokens". [W] Currency to 2 decimals (`$42.30`, not `$42.300` / `$17.0000`). [H] "Would exceed cap" is the alert: make it a notice at the top. [E] Add an axis or day labels: `.chart-col span` is `display: none`. |
| **Rules** | [W] Columns "Str.", "Fresh" → "Strength", "Stays fresh for (days)". |
| **Precision** | [E] Good copy. "95% range" → "Likely range (95%)". Flag rule: "Flagged when over 60% are dismissed, once there are at least 10 decisions" (`precision.py`: `FLAG_DISMISSAL_RATE`, `MIN_DECISIONS`). |
| **Coverage** | [H] The red banner lists all 19 empty cells, duplicating the grid below. Replace with "19 of 20 market and topic combinations found nothing in 14 days." [V] An empty "reported as missed" table shows headers only: hide it when there are 0 items. [E] Subtitle is accurate (`recall.py`); keep it. |
| **Deadlines** | Clear. [W] Status `proposed` → "Waiting for a second approver". |
| **Quality** | [V] **Bug:** metric tiles invisible (`.gates` from `landing.css`, #1). They also use an undefined `.gate` class: switch to the `stat` or `kpi` pattern. [W] Metric keys pass through `humanize()`: map each to a label. |
| **Audit** | [W] Operation `evaluate_hypothesis` → "Scored opportunity". Record IDs truncated to 8 characters with no link: link where a page exists. [P] No paging: add "Older". |

### 2.13 Sign-in (`Landing.tsx`)

Out of the "internal screens" brief, so I only checked it for the CSS collisions it causes (#1). One wording note: the "Sign-in is not configured. An administrator must set RI_GOOGLE_CLIENT_ID." message is right for admins.

### 2.14 Design tokens and visual consistency (theme for one commit)

| Tag | Issue | Fix | Effort |
|---|---|---|---|
| V | **23 distinct font sizes** (9px to 38px) and **167 inline `style={{}}`** blocks across pages. | Add a type scale to `tokens.css`: `--fs-xs 12px`, `--fs-sm 13px`, `--fs-base 15px`, `--fs-md 17px`, `--fs-lg 22px`, `--fs-xl 30px`, `--fs-2xl 38px`. Map every size to the nearest step. Replace repeated inline styles with utility classes (`.mt-0`, `.mb-3`, `.stack-2`) that use `--space-*`. | M |
| V | About 70 hard-coded colours outside `tokens.css`. `#fff` alone appears 47 times, plus `#B9BCE3`, `#D6D8F2`, `#A9ABC9`, `#E5F4EC` and nine `rgba(255,255,255,…)`. `src/components.css` uses `var(--muted, #5a5d80)` and `var(--tint, #F6F7FF)`: neither token exists, so the fallbacks always apply. It also has its own green `#1f8a5b` that differs from `--dot-ok`. | Add tokens: `--muted`, `--on-navy`, `--on-navy-muted`, `--line-on-navy`, `--off` (for `#A9ABC9`), `--ok-tint`. Replace every literal, and fix `components.css` to use the real tokens. | M |
| V | Muted text is made with `opacity`, so it changes colour depending on the background, and fails AA. | `--muted` as a solid colour that passes 4.5:1 on `--white`, `--tint-1` and `--tint-2`. | S |
| V1 | **No dark theme.** You asked for dark-mode screenshots; they're identical to light. | **Decision for you:** build a dark theme (L: needs the token work above first, then a `prefers-color-scheme` block plus a toggle), or leave it out of this round. I recommend leaving it until the token cleanup lands. | L |
| V2 | Primary button white on `--pink` = 4.16:1 at 12.5px, which fails AA. | **Decision for you (brand):** (a) use `--pink-hover` `#CC085D` as the button fill (≈5.2:1, passes) and darken hover; or (b) keep the pink and raise the label to bold 14pt+ (large-text threshold 3:1), which changes the button look. I recommend (a). | S |
| V | Status colour for "unknown" is orange in the gate bar, and the same orange marks "warn" elsewhere. | Keep the dot semantics (ok, warn, bad, info) and document them in `previews/badges.html`. | S |
| A | Scrolling tables aren't keyboard-focusable (axe: `scrollable-region-focusable`). | `.table-wrap` gets `tabindex="0"`, `role="region"` and an `aria-label`. | S |

### 2.15 Performance hygiene

| Tag | Issue | Fix | Effort |
|---|---|---|---|
| P | CLS 0.31–0.39 on Account and Admin. Text-only loading on 20+ screens. | `<Skeleton>` that matches the layout, and the header always rendered. | M |
| P | Polling while the tab is hidden (see 2.0). | Pause on `visibilitychange`. | S |
| P | List sizes. Leads is server-paginated at 50; Feed is unbounded within the chosen window; Library has about 90 cards; Audit has no paging. | None needs virtualisation at current volumes. Add "Show more" paging to Feed (30 at a time) and Audit. Add `content-visibility: auto` to repeated cards. | S |
| P | `Accounts` updates whole-list state on every star toggle. | Fine at 50 rows; no change. | — |

---

## 3. Terminology

### 3.1 Glossary: one name per concept

| Use this | Not these | Meaning, as written for the UI |
|---|---|---|
| **Account** | lead, company (in nav), book | A company in our database, whether from your workbooks, added by someone, or found by us. |
| **New company** | candidate, discovery, "found by the platform" | An account we found ourselves that nobody has reviewed yet. |
| **Opportunity** | hypothesis, recommendation, "verified opportunity" | A specific piece of work we think we could win at an account, with one route in. |
| **Deal** | — | Only a Pipedrive deal. Never used for our opportunities. |
| **Programme** | initiative | A real project at the company, such as a core platform replacement. Many findings can belong to one. |
| **Finding** | event (in UI), development | A dated fact we checked against the original page. "Event" stays in code only. |
| **Signal** | rule, rule ID | One of the 64 patterns that suggest a company may need outside engineering help. Show its name, never only `PRG-01`. |
| **Link to check** | lead (in Feed), search hit | A page found by search or reported by someone, not yet read. |
| **Checked** / **Not checked yet** | verified / unverified (both are fine in Admin) | We read the original page and found the quote. |
| **Workbook** | sheet, master list | Your two Google Sheets workbooks. "On your lists" means the account is in one of them. |
| **Signal score** | account score, score | How strong an account's recent signals are. Open-ended; Hot is 9+, Warm 4+, Watch above 0. Not a probability. |
| **Opportunity score** | score total, prioritisation score | 0–100 across company fit, engineering relevance, outside demand, access, timing, and relationship. Ranks what to look at first. |
| **Workbook score** | sheet score | The score column from your workbook (/100). We don't calculate it. |
| **Priority** | — | Urgent (tender closing within 14 days), High, Medium, Low, Blocked. |
| **Check** (the seven) | gate (Admin and Guide may say "gate") | One of the seven qualification questions, each Confirmed, Not known yet, or Blocked. |
| **Route** | access route, route type | How we'd get in: Direct, Through the vendor, Through an integrator (prime), Public tender, Existing relationship. |
| **Source health** | coverage (on Today, Feed, Explorer) | Whether the sources we watch are working. A quiet screen only means "nothing happened" when this is healthy. |
| **Coverage check** | coverage (Admin), recall | Admin view of whether we're finding things in every market and topic. |
| **Next action** | — | The one thing to do next on an opportunity. |
| **Approval** | — | A second person's OK before anything is written to Pipedrive, an email is revealed, or contacts are enrolled. |

### 3.2 Value dictionaries (frontend only, in `ui.tsx`)

These replace `humanize()`. The API values below were confirmed in `backend/src/ri/opportunity/model.py`, `readmodel.py`, `api/*.py` and the fixtures. A value without a mapping falls back to sentence case and logs once in development, so new enums get noticed.

- **Opportunity state:** `actionable_hypothesis` → "Ready to act on"; `observed` → "Just spotted"; `verified` → "Checked"; `relevant` → "Relevant"; `investigating` → "Being researched"; `engaged` → "In conversation"; `buyer_need_confirmed` → "Buyer confirmed the need"; `commercially_qualified` → "Qualified"; `proposal_procurement` → "Proposal or tender"; `contracted_delivery` → "Won"; `monitor` → "Watching"; `blocked` → "Blocked"; `disqualified` → "Ruled out"; `expired` → "Expired"; `superseded` → "Replaced by a newer card" (`opportunity/states.py`).
- **Route type:** `direct` → "Direct"; `vendor` → "Through the vendor"; `prime_subcontract` → "Through an integrator"; `procurement` → "Public tender"; `relationship` → "Existing relationship".
- **Access status:** `verified` → "Confirmed"; `potential` → "Possible"; `unknown` → "Not confirmed"; `restricted` → "Restricted"; `blocked` → "Blocked".
- **External demand:** `explicit` → "They've asked for help"; `buyer_confirmed` → "Buyer confirmed"; `inferred` → "Likely"; `unknown` → "Not known yet".
- **Buyer status:** `buyer_confirmed` → "Buyer confirmed"; `person_verified` → "Named person confirmed"; `role_identified` → "Role known, person not"; `none` → "No buyer yet".
- **ICP band:** `strategic_5000_plus` → "5,000+ staff"; `enterprise_1000_plus` → "1,000–4,999 staff"; `scaleup_500_999` → "500–999 staff"; `below_threshold` → "Under 500 staff"; `unknown` → "Size unknown".
- **Gate reasons (patterns from `model.py: gates`):**
  - "ICP band X" → "{band}: in our target range";
  - "size unknown or estimate straddles an ICP boundary" → "Size unclear: near a size cut-off";
  - "stage X per cited event" → "Programme is at {stage}";
  - "external demand X: not established" → "Not known yet if they'll use outside help";
  - "route X" → "Route {access}";
  - "buyer X" → buyer status label;
  - "internal delivery feasibility not confirmed" → "Delivery team hasn't confirmed we can staff it".

---

## 4. Explanations I couldn't fully verify

Each needs either a quick backend read I couldn't complete, or a product decision. I haven't written copy that depends on a guess.

1. **Renewals UK coverage.** The code queries TED (EU journal) for GBR among others (`renewals.py: CORE_ISO3`). I couldn't confirm whether post-2021 UK notices (Find a Tender) are included, so "and the UK equivalent" in 2.4 must be dropped or confirmed.
2. **Demand tier D4 meaning.** `model.py` comments call D3 "a verified programme" and D4 "attention". My label "Showing interest" for D4 is an inference from `REL-*` signals (webinar, report download). Please confirm.
3. **"Stopped by a rule" examples.** The score is zeroed by an active `negative_findings` entry with effect `stop`. The example reasons in my tooltip (freeze, rival award) need checking against the `NEG-*` rule wording in `reference/signals`.
4. **Self-approval (partly verified).** `approvals.py` blocks the requester from approving `crm_write`, `outbound_message`, `sequence_enrolment`, `supplier_application`, `quote_or_commitment` and `proof_release`. Email reveals aren't in that set, so the requester can approve their own. The current UI copy is consistent with this. **Open question for you:** is self-approval of paid email reveals intended?
5. **Workbook names.** The UI only knows "workbook A" and "workbook B". I don't know which is the accounts workbook and which is the signals workbook.
6. **Watch cadence.** The Watch star promises "weekly". The scheduler processes up to 25 watched accounts per run, every 7 days (`scheduler.py: schedule_watchlist`). I couldn't confirm how often that run fires, so with more than 25 watched accounts "weekly" may not hold.
7. **Playbook text from the API.** Some sentences on the Opportunity page are built server-side, e.g. "active signal(s) ['PRG-01', 'SRC-05']", "buyer_authority_inferred stays true…" and the `why_it_matters` string. I can tidy them in the frontend by pattern, which is brittle. The clean fix is backend wording, which is out of scope unless you say so.
8. **Feed "fit" labels and sales plays.** The offer and play text ("Team Extension with delivery responsibility", "Usual buyer: …") comes from the API. I reworded only the frame around it, not the mapping itself.
9. **Evidence "published unknown".** The quote-level `published_at` is null in the seed even when the event has a publication date. I recommend showing the event date, but haven't confirmed whether real claims usually carry their own date.
10. **Enrichment waterfall (E1–E6).** It exists in `enrichment/waterfall.py` but isn't shown anywhere in the UI, so there's nothing to explain yet. If you want it surfaced (e.g. on Account › People), that's new UI, not a review fix.

---

## Proposed batches after approval (one commit each, frontend only)

1. **CSS collision fix**: scope `landing.css` (#1).
2. **Label dictionaries and wording pass**: `ui.tsx` dictionaries, then copy changes screen by screen (#2, #3, glossary).
3. **Scores and explanations** (#4, plus E-tagged items).
4. **Accessibility and tokens**: type scale, colour tokens, `--muted`, focus ring, `<h1>`, table regions. Needs decisions V1 and V2.
5. **Loading, error and empty components plus skeletons** (#9, CLS).
6. **Today hierarchy and grouping** (#5).
7. **Opportunity page** (#6).
8. **Explorer columns** (#7).
9. **Phone tables and tap targets** (#10).
10. **Accounts list and account tabs** (presets, filter collapse, tab consolidation).
11. **Admin grouping, Approvals, Usage chart**, plus the remaining per-tab fixes.

Each batch: `npm run build` (includes `tsc --noEmit`) must pass, then retake the affected screenshots and add before/after pairs to `docs/design-review-2026-11-23/after/`.

**Decisions I need from you before batch 4:** V1 (dark theme now or later) and V2 (primary button colour).
