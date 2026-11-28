# How the platform works, and what runs by itself

Updated 27 Nov 2026. The live version of the source and task tables below is **Administration, Sources & data, Data health**, which reads real figures.

## The flow in one line
**A source or workbook, then a page is read, then a quoted claim, then a verified finding, then a buying signal, then a programme, then seven gates, then an opportunity card on Today, then a draft that a second person approves.**

## Where the information comes from
The registry holds 98 sources. On 26 Nov 2026, 70 were live. The rest are internal or search sources used on request, or are waiting for a key, an address or a terms check. Data health shows the live number today.

| Channel | What it reads | How often | Cost |
|---|---|---|---|
| Public tenders | EU (TED), UK Find a Tender, UK Contracts Finder, Poland (e-Zamówienia). Award notices of the last five weeks, for renewals. | Daily | Free |
| News and trade press | About 35 news feeds. A cheap word check skips off-topic articles; the AI reads the rest. | Daily, some every two days | AI tokens |
| Regulators, vendors, integrators | About 29 single pages (their news pages), compared with last time. | Daily or every two days | AI tokens, only when a page changes |
| Job ads | Open job APIs for Sweden and Germany, Austria and Switzerland. Company career pages through the company website checks. | Daily | Free |
| Company websites | An account's own news, careers and supplier pages. The top 25 accounts daily, starred accounts weekly, every other target account in rotation (about 20 accounts a day, so each roughly every six weeks; P1 and P2 accounts never checked go first, at up to 60 a day until all have been checked), and any account on request. | As described | Free; the AI reads changed pages |
| Web search (Exa) | Saved searches, and searches for target accounts that have had no finding for 90 days. Finds pages about companies that no feed covers. Every hit is only a lead: it is queued as a page check. | Daily run | Paid, capped (see below) |
| Page checks | One page, when a seller presses a button or web search finds it. | On request | AI tokens |
| Account lists | The lead-list workbooks (upload, or Google Sheets sync when set up). | On request | Free |
| Lemlist | Replies, opens and opt-outs (read only). | Hourly | Free |

Rules for every page: robots.txt and AI content signals are obeyed, no login is ever used, social networks are not read, and a site that blocks the app is skipped, never worked around. Blocked or broken feeds are listed as such in Data health.

**Pipedrive** deals are read into the app (another round connected it). Every hour, an account linked to a Pipedrive organisation gets its relationship with us from its deals: client, past client, in talks, or a prospect with lost deals. A relationship a person set is never changed.

## What starts what

| Step | What starts it | Runs by itself? |
|---|---|---|
| Scheduler tick | Render cron, every 5 minutes. It only queues work; the worker does it. | Yes |
| Read a source | The scheduler, when the source is live and its interval has passed | Yes |
| Switch a source on | A daily test from the server switches safe public sources on. Sources that need a key, an address or a terms check wait for a person. | Mostly |
| Check company websites | Daily, weekly and hourly rotation lanes | Yes |
| Web search | Once a day, when the Exa key and a monthly cap are set | Yes |
| Read a page, keep only quoted claims | Every source run and page check | Yes |
| Finding, signal, programme, card, score | Every verified finding, straight away | Yes |
| Company research for P1 and P2 | Scheduler, every hour: Exa research for two P1 accounts without a profile (then P2), at most 12 a day, while automatic web search is under 60% of the month's Exa budget; it pauses after three failures in a row | Yes |
| Find hard-to-fill roles | Scheduler, once a day: engineering ads at our accounts still open after 60 days, or posted again after an earlier ad for the same role closed (rule CAP-03) | Yes |
| Expire old signals, move programmes on | Scheduler | Yes |
| Daily digest to Slack | Scheduler, only if a Slack webhook is set | If set |
| Import the workbooks | A person uploads them, or Google Sheets sync | Manual unless sync is set up |
| Send an email | Nothing. The platform never sends | Never |
| Add someone to a Lemlist campaign | A seller, then a second person approves. The country email rules are checked when it is requested and again just before the push | Never by itself |
| Approve a draft | A second person | Always a person |

## Suppliers and what is changing
On a company page, **Suppliers and what is changing** shows who the company already buys technology and engineering from, and what is happening to those relationships. It works in layers, strongest first:
1. **Our own records, free and immediate:** suppliers named in verified findings, public IT contract awards where the company is the buyer (with the contract end date, labelled *stated* or *assumed 36 months*), supply events (a vendor chosen, a go-live, a supplier exit, a delay) and the technology named in their open job ads.
2. **"Find suppliers with Exa"** (about ten searches, around 15 US cents, from the interactive part of the Exa budget). Five searches look by who is writing: the supplier's customer stories and press releases, an integrator's or engineering firm's client stories, the company's own partner pages, independent news, and news of a change or problem. A second round then looks at the top three suppliers (an engineering partner first, then an integrator, then a platform): news about the supplier and the company together, and news about the supplier itself (acquired, product retired, cuts, outage).
3. **One AI reading** (a second one after the second round). Every supplier and every happening must quote a page that was really returned, that names the supplier and is about this company. Anything else is dropped and counted.

Each supplier shows how far its evidence can be trusted, by who wrote it: *their own site or an official record*, *press or our verified findings*, or *the supplier's own claim*. A supplier is never called current on evidence older than two years, or on undated evidence that is not official.

**Why this could matter to us** lists plain suggestions worked out by code (not the AI) from the verified items, each naming the tracker rule it relates to (for example a platform implementation under way relates to SRC-05, a contract nearing its end to SRC-03). They are suggestions for a seller, not findings.

The pages behind a supplier or a happening are also queued as ordinary page checks, so the normal pipeline can turn them into findings and signals. The supplier names and categories come from `backend/rules/vendor_lexicon.yaml`, which sales leadership can extend; a name that is not listed is still found and shown as "Other supplier". See `docs/buying-signals-expansion.md` for the thinking behind the signals and what to build next.

## Email rules by country
Before anyone is added to a Lemlist campaign, the company's country is checked against `backend/rules/email_rules.yaml`. Cold email is allowed with an opt-out in the UK and Sweden. Germany, Austria, Switzerland, Denmark, the Netherlands and Poland need the person's prior consent, and Finland and Norway need it for a personal address. A seller records consent on the person's page, with how and when it was given. A country the file does not list, or a company with no country, is held back until it is added. The file is a summary of public guidance, not legal advice: sales leadership changes it after counsel confirms. Replies to someone who wrote to us are not checked.

## One account tier
Every account carries one tier, the same on Today, Accounts, Buying signals and its own page. It crosses the lead list's priority (P1, P2, P3) with timing: hot signals or being in talks with us is "now", warm signals "soon", a weak signal "watch". Tier 1 is "Act now" (P1 or P2 with timing now, or P1 with warm signals), 2 "Work this month", 3 "Keep warm", 4 "Monitor". An account with a reason to stop or wait is tier 4. The reason is shown with the badge. It orders work and is not a win probability.

## Was this useful?
Every buying signal and tender on Today and Buying signals has "Was this useful?" under its evidence. "No" asks why (wrong company, out of date, not our kind of work, job ad not real, already talking to them, no real need). The answer never hides the item. Library, Signals shows per rule how often sellers found it useful and the reasons against it, for tuning the rules. Replies and meetings count for more than these answers.

## Limits on the parts that cost money
- **Exa: $20 a month.** Automatic searches stop at 60% of it ($12). The rest is kept for sellers pressing "Research with Exa" or "Find suppliers with Exa", so those buttons always work.
- **AI: 30 million tokens a month.** Background reading stops at 70%. Sellers' own checks keep the last 30%.
- **Apollo:** subscription credits only. The platform never buys more.
- Every paid call is recorded with what it was for (Administration, Usage). Feeds, tender APIs, company pages and job APIs cost nothing to read.

## What Data health shows
- **Every source:** how often it is read, when it was last read and will be next, what it found in 30 days and how much of that is about target accounts, with a plain state such as working, late, failing or no findings.
- **Every automatic task** (scheduler lane): last run, result, next run. Tasks that start work can be paused and some can be run at the next tick. Tasks that keep the data correct cannot be paused.
- **Background jobs** of the last 7 days, with the cause of a failure in plain words.
- **What is waiting:** pages to read, page checks, company names to match.
- **The AI limit used so far** and the 70% line.
- **How big the stored tables are.**

The Library (Reference, Library, Sources) shows the same for sellers in one line per source: what it brought in the last 30 days, how many findings were about target accounts, and a quiet label when a source reads many pages but finds little for our accounts, or is paused after a review.

## Setting up
1. Upload the lead-list workbooks (Administration, Sources & data) so Today has accounts.
2. Look at Data health once a week: failing or late sources first.
3. Optional: a Slack webhook for the digest; a Google service account for hourly sheet sync.

## Known limits
- Scores are provisional weights, not win probabilities.
- PDFs are not read yet.
- German, Polish and Nordic extraction has not been tested against a gold set.
- Tenders mostly come from public buyers that are not our accounts. They appear on the Tenders page, but few become findings about an account.
- Only about a tenth of the target accounts had a finding in the last 90 days when this was written. Rotating company website checks and web search for accounts with no finding are meant to change that.
