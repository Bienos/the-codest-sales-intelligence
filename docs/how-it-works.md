# How the platform works, and what runs by itself

Written 21 Nov 2026. The live version of this table is **Administration, Pipeline**, which reads real heartbeats.

## The flow in one line
**Source or sheet, then a page is read, then a quoted claim, then a verified event, then a signal, then a programme, then seven gates, then an opportunity card on Today, then a draft or CRM proposal that a second person approves.**

## What triggers what

| Step | What starts it | Runs by itself? | Where it happens |
|---|---|---|---|
| Scheduler tick | Render cron, every 5 minutes | Yes | `ri.cli schedule` |
| Watch a source (tender feed, RSS, newsroom page) | Scheduler, when the source is **active** and its interval has passed | Yes, once a source is activated | worker, job `source_run` |
| Activate a source | An administrator tests it from the server, then activates it | **No, a person does this once per source** | Administration, Sources |
| Exa discovery | Scheduler, once a day, if the Exa key and a monthly cap are set | Yes | worker, job `discovery_run` |
| Verify a claim | A seller presses Verify, or a discovery hit is queued | Partly: automatic after the trigger | worker, job `verify_url` |
| Read the page and extract events | The verification job or a source run | Yes | worker |
| Keep only quoted claims | Always, as part of extraction | Yes | `events/extract.py` |
| Event, signal, programme, opportunity, score | Every verified event, immediately | **Yes, no manual step** (added 21 Nov; before that cards were never built from live data) | `ingest/process.py` then `opportunity/engine.py` |
| Re-score a moved programme | Scheduler maintenance and the pipeline | Yes | scheduler |
| Expire old signals, flag stale programmes | Scheduler maintenance | Yes | scheduler |
| Daily digest to Slack | Scheduler, only if the Slack webhook is configured | Yes if configured, otherwise off | scheduler |
| Import the two workbooks | A person uploads the .xlsx exports | **No, manual** until a Google service account exists | Administration, Imports |
| Pipedrive sync and CRM writes | Not connected | **Off** | not wired |
| Send email or messages | Nothing. The system never sends | Never | none |
| Approve a draft or CRM change | A second person | **No, always a person** | Administration, Approvals |

## Cost and safety controls on the automatic parts
- Anthropic: monthly token cap (`RI_ANTHROPIC_MONTHLY_TOKEN_CAP`). Calls reserve budget first and stop at the cap.
- Exa: monthly dollar cap (`RI_EXA_MONTHLY_CAP_USD`), at most 8 searches and 3 new checks per run, each plan at most weekly.
- Discovery never uses the last 30% of the model budget, so seller checks always have room.
- Every call is recorded in Administration, Usage with what it was for.
- Pages are fetched only from public addresses, with robots.txt honoured, a size cap, and no social networks.

## What makes it start producing opportunities
1. **Activate sources.** 28 sources have an address. Test each from the server and activate (Administration, Sources). Until one is live, only discovery and manual verification feed the platform.
2. **Upload the two workbooks** (Administration, Imports) so Today has accounts and a shortlist.
3. **Check Usage after the first verifications** to see the real cost per check.
4. Optional: Slack webhook for the digest; a Google service account for hourly sheet sync; Pipedrive for ownership.

## Known limits
- Scores are provisional weights, not win probabilities.
- PDFs are not read yet.
- German, Polish and Nordic extraction has not been tested against a gold set.
- Sources found by web search are unproven until tested from the server.
