# Buying signals: what we track, what we added, what to build next

Written 27 Nov 2026. Companion to the original 64-rule tracker (`reference/signals/`, never modified) and the executable rules in `backend/rules/signal_rules.yaml`.

## 1. Where we stood, and what changed today

Of the 64 tracker rules, 28 were active. Today 8 more were switched on, each with the fixture coverage the CI gate demands (3 positive, 3 negative, 2 ambiguous, 1 expiry case):

| Rule | What it catches | Why it was cheap to add |
|---|---|---|
| **SRC-03** | An incumbent IT or engineering supplier leaves, is replaced, or its contract ends | The extractor already had the `supplier_exit` event type and nothing used it. The prompt now tells it how to set `it_services_scope` for exits. |
| **PRG-03** | Digital channel rebuild (mobile and web banking, onboarding) with named systems and dates | The extractor already labels `programme_domain`. Same shape as PRG-01 and PRG-02. |
| **PRG-04** | Lending platform build | same |
| **PRG-08** | Fraud, AML or sanctions platform replacement | same |
| **PRG-10** | AI use case moving into production (status `implementation`, named use case) | same |
| **PRG-11** | API or open-banking rebuild with a dated migration | same |
| **PRG-16** | Programme publicly delayed, with a dated recovery plan | The `programme_delayed` event type was extracted but no rule read it. |
| **NEG-07** | Acquisition pending approval: park the account, re-check after close | `deal_status: pending_approval` was already extracted. COM-02 already withholds its signal in that state; NEG-07 makes the pause visible. |

36 of 64 are now active. The sales plays follow: SP-02 also fires on PRG-16, SP-03 on PRG-03/04/08/11, SP-07 on PRG-10, SP-09 on SRC-03.

**Already in place, so not proposed again:** the account score rewards signals from *different* families (a 25% bonus per extra family, up to three) and counts a rule once however often it fires (`scoring.py`). Independent origins upgrade a signal's tier. Negative rules can stop or halve a score. "Was this useful?" feedback per rule is collected.

## 2. The vendor picture (the new function)

"Who do they already buy from, and is something happening to that relationship" is a signal family of its own. It is now an on-page research function (see `docs/how-it-works.md`). The signals it surfaces, each tied to a tracker rule where one exists:

| Vendor signal | Reads as | Tracker rule | How we see it |
|---|---|---|---|
| A named **engineering partner** (an EPAM, Endava, Netcompany type) | The firm to displace or complement. Not an automatic "no": ask what it does not cover. | none yet | Customer stories and press on the vendor's or integrator's own site, the company's own partner pages |
| An **integrator holds delivery** | The way in is the integrator's partner route | SRC-04, NEG-03 | Same pages, `supplier_edges`, contract awards |
| A **platform vendor was chosen or went live** | Implementations need integration and custom-connector capacity | SRC-05, PRG-15 | Vendor press releases and customer stories |
| A **contract is nearing its end** | Often re-tendered before it ends. The end date is labelled *stated* or *assumed (36 months)* | SRC-03 | Public award notices (the renewal radar) |
| A **supplier change** (replaced, re-tendered, exit) | The moment a company is most open to a new supplier | SRC-03 (now active) | News, notices |
| **Delivery trouble** (delay, dispute, outage) | Teams under strain take extra engineers or a recovery team. Never quoted in outreach. | PRG-16 (now active) | News about the company and its supplier together |
| **The supplier itself changes** (acquired, product retired, cuts) | Customers re-platform, add capacity or re-check support | PRG-12 | News about the supplier alone |
| **A staffing agency or MSP** is the labour route | Contingent labour goes through a programme: check supplier-tier access | SRC-06 | Same pages |
| **Supplier list reduced** | Go in through a member | NEG-05 | News, notices |

**The trust rule that matters most:** how far a page can be believed depends on *who wrote it*. The research therefore labels every item `official` (the company's own site, an award notice), `reported` (independent press, our verified findings, or two independent supplier-side sites) or `vendor_claim` (only the supplier's own marketing). A supplier is never called "current" on evidence older than two years, or on undated evidence that is not official.

**How it feeds the real signals:** the research never creates a signal itself. The pages behind a supplier or a happening are queued as ordinary page checks, so the full pipeline (original page fetched, quote verified, event, rules) decides whether they become findings.

## 3. Signals worth building next, in priority order

Effort: S = a rule over data we already extract; M = a new reader or event type; L = a new data source with terms or cost questions. "Precision risk" is how likely the signal is to fire without a real need.

### A. High value, low effort (do first)

| # | Signal | Why it is good | Data | Effort | Precision risk |
|---|---|---|---|---|---|
| A1 | **Stack ageing.** A technology in their open job ads is at or past end of support (a Rails, Java, .NET, Python or Angular version), or a stack appears for the first time (first Kotlin or Kafka role) | A dated, forced reason to modernise, and a conversation opener. PRG-12 exists in the tracker with no detector. | `job_postings.tech_keywords` plus an end-of-life table kept from a public source | S–M | Medium: ads list versions loosely. Needs the version in the ad. |
| A2 | **Convergence alert.** Three or more different families active in 90 days (for example a new CTO, an engineering hiring cluster, and a platform vendor chosen) | The score already rewards this. An explicit alert makes it visible on Today. | `signals`, `account_scores.groups` | S | Low |
| A3 | **Rescue pattern.** A delayed programme (PRG-16) plus a named engineering partner or integrator plus contract-role ads (CAP-02) | Three weak facts that together mean "needs capacity now" | Vendor research plus existing rules | S | Low–medium |
| A4 | **Warm path from our own mail.** The connected Gmail already holds past threads. A prior thread with the company's domain makes any signal there warmer (REL-04) | We already sync it; nothing to buy | `connections` (Gmail), `employment` | S | Low |
| A5 | **Closed-lost, new trigger (REL-06).** A lost or no-decision account gets a new signal in any family | Strongest warm-up there is; the lost reason tells us what to say | CRM or workbook outcomes plus signals | S | Low |
| A6 | **Reply and meeting signals as rules (REL-01, REL-02, REL-07).** Replies and clicks from Lemlist and meetings from Bluedot are already stored | Replies and meetings count for more than any rule, and the feedback loop already says so | `lemlist_activities`, conversations | S | Low (scanner clicks must not count: replies and human actions only) |

### B. High value, medium effort

| # | Signal | Why | Data | Effort |
|---|---|---|---|---|
| B1 | **Leading procurement notices.** Prior-information notices, planned procurements, framework agreements nearing expiry | Months before the tender opens. Extends SRC-01/02/03 to the pre-tender stage. | TED and Find a Tender prior-information notices; Polish annual procurement plans | M |
| B2 | **Consultancy engaged before delivery.** A strategy or target-operating-model firm is named for a technology change | Delivery typically follows. The consultancy is a partner route and a source of scope. | Vendor-picture research (category `system_integrator`) over news and the consultancy's own pages | M |
| B3 | **Senior engineering roles open or created (MAN-02, MAN-03).** VP Engineering, Head of Platform, Head of AI, Chief Data Officer | A new leader reviews suppliers 30–120 days in (MAN-01 already uses this window) | `job_postings`, news | S–M |
| B4 | **Leader says it in public (MAN-04).** A talk, podcast or post naming a priority programme | The best opening line, and it names scope | Person research already reads talks and articles; promote it to an event | M |
| B5 | **Public status page incidents.** A payments or fintech company with repeated incidents in 30 days | Reliability pain points to QA, SRE and test automation (CAP-06). Public and free. | Status pages (Atlassian Statuspage and similar) | M |
| B6 | **Integrator ads that name the client.** A systems integrator or agency advertises roles "for a banking client in London" using the client's stack | Shows delivery is staffing up at the integrator, which is a route in (SRC-04) | Job boards, filtered by integrator employer | M, low precision |
| B7 | **Earnings and investor-day statements.** Technology investment amounts, cost-to-income targets, nearshoring, "modernisation" capex | Budget is stated, which no job ad does. Feeds COM-08. | Listed companies' reports and calls | M–L |
| B8 | **Supplier-trouble news at the supplier level** for *all* suppliers we know, not only on demand | Today this is read when a seller presses the button. A weekly pass over known suppliers would raise it without being asked. | The `account_vendor_intel` suppliers, a capped search each | M (cost-bound) |

### C. Needs a new event type first (negative signals)

The tracker has several "stop or wait" rules with no detector, because the extractor has no event type for them. Each needs a migration (the `events_event_type_check` constraint), an extraction vocabulary entry, a rule and fixtures:

| Rule | Event | Effect |
|---|---|---|
| NEG-02 | Engineering layoffs or hiring freeze in the last 90 days | Downgrade augmentation plays by 2 |
| NEG-04 | Own nearshore or engineering hub opened (Poland, CEE) | Downgrade augmentation; angle becomes niche skills |
| NEG-08 | Financial distress: down round, insolvency news, missed payments | Finance check before any proposal |
| NEG-09 | Sanctions exposure, misconduct enforcement | Stop until Compliance clears |

These matter because the loudest cost in outbound is writing to a company that is about to freeze spend.

### D. Longer shots

- **Mobile app signals:** rating drops, crash-heavy reviews, release cadence changes for a bank's app. Points to a digital channel rebuild (PRG-03). Public data, noisy.
- **Open-source footprint:** a fintech's public repositories and languages over time (a new language is a stack move). Only some targets publish.
- **Regulatory forcing functions:** the regulatory calendar already drives REG-01. Candidate regulations to research and add by a person (the file says never to add a date from memory): DORA third-party register and testing duties, instant-payments verification of payee, PSD3 and PSR, FIDA, the AI Act's high-risk obligations, UK APP-fraud reimbursement, and the UK's Basel 3.1 start.
- **Conference and award speaker lists** naming programmes (Money20/20, Finovate and the like): a MAN-04 source.

## 4. What not to track

- Funding with no stated use (COM-01 already rates it strength 1).
- "Hiring" with no team, system or date. CAP-01 requires a cluster naming a product, platform or launch.
- A logo on a vendor's customer page with nothing said about what the vendor does for them. The vendor research drops these unless the page says what was done.
- Anything that needs a login, a social network or a site that blocks us. The platform never works around a block.
- Intent data bought as a score. Without a named source and a date, a seller cannot check it, which is the platform's first rule.

## 5. Judging whether a signal is worth keeping

A signal earns its place if it passes all five:

1. **Dated.** There is a date, or a window that ends. Signals without one pile up.
2. **Scoped.** It names a system, a team or a programme, so a seller knows what to say.
3. **Separable.** The work could be ours, not only the incumbent's.
4. **Observable.** We can see it from public data we are allowed to read, at a cost we can state.
5. **Checkable.** A seller can open the page. The "Was this useful?" answer then tells us whether the rule is worth its slot (Library, Signals shows it per rule).

Re-rank this list every quarter from that feedback, not from opinion.
