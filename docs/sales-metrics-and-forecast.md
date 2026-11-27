# Sales metrics and forecast design

Status: proposal, 2026-11-27. Nothing here is built or deployed. Numbers marked **observed** come from a live lemlist read today or from this repo. Numbers marked **benchmark** are third-party and unverified. Anything marked **assumption** is a placeholder until your Pipedrive history replaces it.

## 1. Summary

1. **You cannot forecast from what is stored today.** `crm_deals` keeps only the *current* stage, status and value (`backend/migrations/0004_opportunity_intelligence.sql`). It has no created, won or lost time, no lost reason, no expected close date and no stage history. Conversion rates and stage durations cannot be computed from that. Capturing stage transitions is the first job, and the sooner it starts the sooner you have data.
2. **Lemlist is too young to calibrate anything.** The six running campaigns were created on 24 Nov. As of today they have sent 226 first-step emails, got 8 replies, 0 marked interested, 0 meetings booked. No follow-up steps have sent yet.
3. **Public benchmarks do not cover your market.** I found no usable sales-cycle or win-rate benchmark for B2B IT services or software outsourcing. The generic B2B and SaaS funnel numbers differ by 2 to 3x between sources because each defines MQL and SQL differently. Use them as priors for the first quarter only.
4. **Method.** Forecast in two layers: (a) open deals in Pipedrive, weighted by stage conversion and time-to-close you measured yourselves; (b) deals not yet created, driven by lemlist volume and reply rates. Report ranges, not one number, and backtest every quarter.
5. **A working prototype** of the model, with editable assumptions and a Monte Carlo range per 2027 quarter, is published as an artifact (link in the chat reply). It runs on placeholder inputs.

## 2. What exists today

| Area | Finding | Source |
| --- | --- | --- |
| Pipedrive stages | `Webform MQL`, `Marketing Nurturing`, `DISCOVERY`, `SQL`, `Proposal/Shortlist`, `Commercials/NDA`, `Active Delivery` | `backend/src/ri/deal/progress.py` |
| Stage order | In that file Discovery is listed before SQL. You described MQL, SQL, Discovery. **Confirm the real order in Pipedrive**, because "SQL to Discovery" and "Discovery to SQL" are different conversion definitions. | needs your answer |
| Pipedrive sync | Reads organizations, persons and deals incrementally, plus a durable webhook inbox (`crm_webhook_inbox`) that already receives `deal` events | `backend/src/ri/crm/pipedrive.py` |
| Pipedrive access | No Pipedrive connector in this session. Everything about Pipedrive is from the repo, not a live read. | |
| Lemlist | 20 of 31 campaigns listed; 6 running (Procurement and Engineering Leadership for Poland, Nordics, DACH), rest paused drafts. 584 leads launched. | lemlist API, 2026-11-27 |

Lemlist, all running campaigns, 27 Nov 2026 (**observed**, tiny samples):

| Campaign | Leads | Sent | Replied | Bounced |
| --- | --- | --- | --- | --- |
| DACH Procurement | 148 | 118 | 2 (1.7%) | 4 |
| Nordics Procurement | 68 | 68 | 4 (5.9%) | 3 |
| Poland Procurement | 40 | 40 | 2 (5.0%) | 4 (10%) |
| Engineering Leadership (DACH, Nordics, Poland) | 328 | 0 | 0 | 0 |
| **Total** | **584** | **226** | **8 (3.5%)** | **11 (4.9%)** |

Eight replies is far too few to tell DACH from Nordics. Poland's 10% bounce rate on 40 sends is worth checking list quality for now.

## 3. The funnel, defined so it can be measured

Every stage needs an entry rule that is true or false, an owner, and one timestamp. Use Pipedrive's stage as the system of record. Lemlist feeds the stages before a deal exists.

| # | Stage | Entry rule (proposed) | Timestamp source |
| --- | --- | --- | --- |
| 0 | Contacted | Lemlist step 1 sent | lemlist lead event |
| 1 | Replied | Any reply | lemlist reply event |
| 2 | Positive reply | Reply classified interested or meeting asked | lemlist `interested` status or manual tag |
| 3 | MQL (`Webform MQL`) | Deal created. For outbound, a positive reply. For inbound, a form fill. | Pipedrive `add_time` |
| 4 | Discovery | First discovery call held (not scheduled) | stage transition |
| 5 | SQL | Need, budget owner and timing confirmed by the buyer | stage transition |
| 6 | Proposal/Shortlist | Written proposal sent or vendor shortlist confirmed | stage transition |
| 7 | Commercials/NDA | Contract terms or NDA in negotiation | stage transition |
| 8 | Won / Lost | Pipedrive won or lost | `won_time`, `lost_time`, `lost_reason` |

Rules that keep the numbers honest:
- **`Marketing Nurturing` is a parking stage.** Count it as "recycled", exclude it from stage duration, and track re-entry to MQL separately. Otherwise it drags every velocity figure.
- **Add a `Source` field** on every deal: outbound lemlist, inbound webform, signal, referral, tender. Conversion differs sharply by source and the forecast should not blend them.
- **Lost reason is required** to move a deal to Lost. It is the cheapest way to learn why you lose.
- The repo already says "a meeting alone changes nothing" for the evidence-gated state (`opportunity/states.py`). Apply the same discipline here: a stage means something the buyer did.

## 4. What to capture

### Per deal (Pipedrive fields)
`add_time`, `stage_id`, `stage_change_time`, `status`, `won_time`, `lost_time`, `lost_reason`, `value`, `currency`, `expected_close_date`, `owner_id`, `source`, `country`, `service line` (the lemlist campaign themes: Engineering, Data/AI, Product, CTO/CIO, Risk, Procurement), `competitor`, `tender yes/no`. I believe the first nine exist on Pipedrive's deal object, but I could not confirm that from documentation in this session. Check them against the API reference.

### Per stage transition (new append-only table, the important one)
`deal_id`, `from_stage`, `to_stage`, `at`, `actor`, `value_at_time`. Everything else (conversion, duration, aging, cohorts) is computed from this. Include backward moves.

### Per lemlist lead
`campaign`, `step`, `sent_at`, `delivered`, `replied_at`, `reply_class` (interested, not now, wrong person, out of office, unsubscribe), `meeting_booked_at`, `bounced`. Skip open and click rates for decisions: privacy proxies inflate opens (the 12 to 48% opens above are not trustworthy) and the benchmark sources say to rely on replies.

### Daily snapshot (cheap insurance)
Once a day, store each open deal's stage, value, expected close date. It lets you reconstruct pipeline *as it was* on any date, which is what a forecast backtest needs, and it catches edits that rewrite history.

## 5. Metrics

Group them so each dashboard panel answers one question.

| Question | Metric | Formula |
| --- | --- | --- |
| Is outbound healthy? | Delivery, reply, positive-reply rate, meetings per 1,000 sends | positive replies / delivered; meetings / sends x 1000 |
| Where do we leak? | Stage-to-stage conversion, by source and cohort month | deals entering stage n+1 / deals entering stage n, per creation cohort |
| How fast? | Median days per stage and total cycle (won deals only) | from transition table |
| Is the pipeline real? | Stage aging, stale share, coverage, weighted coverage | stale = no activity or stage change in 2x median stage time; coverage = open value / remaining target |
| What is it worth? | Sales velocity | deals x win rate x average value / cycle days |
| Can we trust the forecast? | Forecast error per quarter | (forecast P50 - actual) / actual, and whether actual fell inside P10 to P90 |

Coverage rule of thumb: required coverage is about 1 / win rate (a 25% win rate needs 4x). Several RevOps sources agree that stage-weighted coverage is the more honest figure, and that stale deals inflate the headline number (their example: 4x with 30% stale is effectively 2.8x). Mid-market B2B targets are usually quoted at 2.5 to 4x.

## 6. Benchmarks (use as priors, then replace)

| Stage | Range across sources | Note |
| --- | --- | --- |
| Lead to MQL | 10 to 25% (SaaS sources up to 39%) | definitions differ |
| MQL to SQL | 12 to 18% (Belkins, MarketJoy); 20 to 30% (SaleHive); 38 to 39% (SaaS-specific) | widest spread |
| SQL to opportunity | 10 to 12% (Belkins); 30 to 42% (others) | |
| Closed won | 6 to 9% (Belkins) | |
| Cold email reply rate | 0.45% (Belkins, 2025, strict method); 1 to 4% (Mailshake); 3.0 to 3.4% (Reply.io, Instantly); 3 to 6% (Apollo) | same metric varies 3x with method |
| Interested share of replies | about 23% (Reply.io) | single source |
| Meetings per 100 emails | 1 to 2 (SaleHive) | single source, unconfirmed |

Your first-send reply rate of 3.5% sits inside the generic 3 to 6% band but on 226 sends, and with steps 2 to 5 still unsent. Treat it as encouraging, not proven.

## 7. Forecast method

**Layer A: open deals.** For each open deal at stage k: probability of winning = product of the remaining stage conversions; close date = today + sampled time in the current stage + sampled durations of the later stages. Use *your* measured conversions per stage, split by source once there are enough deals.

**Layer B: deals not yet created.** Monthly lemlist volume x delivery x reply x positive x deal-creation rate gives new MQLs per month, each then runs through the same stage path. This is what makes the 2027 quarters meaningful, because most of next year's closes do not exist as deals yet.

**Combine** with a Monte Carlo run (a few thousand trials, durations lognormal around the median, win/lose per stage as a coin flip). Output P10, P50, P90 per quarter and the chance of hitting target. Weighted-pipeline is the usual default for mid-market B2B with clean CRM data, cohort conversion suits steady funnels, and velocity-style timing is best used as an adjustment. I found no published backtest comparing them, so **run your own**: store each quarter's forecast and score it against actuals.

**Cold-start rule (my judgment, not sourced):** with fewer than roughly 20 to 30 deals passing a stage, that stage's measured conversion is noise. Blend it with the benchmark prior and widen the range rather than show a precise number.

## 8. Getting Pipedrive history

Findings from Pipedrive developer-community threads (secondhand, verify against the official API reference):
- `GET /v1/deals/{id}/flow` returns a deal's change history including `stage_id` changes. There is **no bulk endpoint**, one deal per call. One poster with 30,000 deals had to loop.
- Two posts report gaps: some stage moves missing from the flow, and backward moves not tracked.
- A `stay_in_pipeline_stages` field on the deal gives seconds spent in each stage.

So: **backfill** won, lost and open deals through the flow endpoint once, **then capture going forward** from the `deal` webhook that `crm_webhook_inbox` already receives, writing a row to the transition table whenever `stage_id` changes. Reconcile nightly against the daily snapshot. Check how many deals you have; if it is a few hundred, the backfill is a short script.

## 9. Proposed schema (sketch only)

```sql
CREATE TABLE deal_stage_transitions (
  id bigserial PRIMARY KEY, crm_deal_id bigint NOT NULL,
  from_stage_id bigint, to_stage_id bigint NOT NULL, to_stage_name text NOT NULL,
  at timestamptz NOT NULL, value numeric, currency text, source text NOT NULL DEFAULT 'webhook',
  UNIQUE (crm_deal_id, to_stage_id, at)
);
CREATE TABLE deal_snapshots_daily (
  day date NOT NULL, crm_deal_id bigint NOT NULL, stage_id bigint, status text,
  value numeric, expected_close date, owner_id bigint, PRIMARY KEY (day, crm_deal_id)
);
ALTER TABLE crm_deals ADD COLUMN add_time timestamptz, ADD COLUMN won_time timestamptz,
  ADD COLUMN lost_time timestamptz, ADD COLUMN lost_reason text, ADD COLUMN expected_close_date date,
  ADD COLUMN deal_source text;
CREATE TABLE lemlist_lead_events (
  id bigserial PRIMARY KEY, campaign_id text NOT NULL, lead_email_hash text NOT NULL,
  step int, event text NOT NULL, reply_class text, at timestamptz NOT NULL
);
```
Follow the repo's existing migration and append-only conventions (`forbid_mutation` trigger, as on `opportunity_transitions`). Hash lead emails in the events table; the dashboard does not need them.

## 10. Dashboard layout

1. **Headline:** forecast P10 / P50 / P90 for the year and each quarter vs target, probability of target, forecast error history.
2. **Funnel:** contacted to won, counts and conversion at each step, filter by source, country, service line, cohort month.
3. **Pipeline health:** open value by stage, weighted value, coverage and weighted coverage, aging bars, stale deals list.
4. **Velocity:** median days per stage, cycle trend, sales velocity.
5. **Outbound:** per campaign sends, delivery, reply, positive, meetings; bounce alerts.
6. **Why we lose:** lost reasons by stage and competitor.
7. **Assumptions panel:** the conversions and durations in use, showing measured value, sample size and benchmark prior side by side.

## 11. Build order

1. Answer the open questions below (15 minutes).
2. Add the deal fields and the transition table; start capturing from the webhook. **Do this first, history cannot be recovered later.**
3. Backfill history from the flow endpoint.
4. Mirror lemlist lead events (the repo already has activity read-back and a webhook).
5. Replace the placeholder assumptions in the prototype with measured values; turn it into a page in the frontend.
6. Start the quarterly forecast backtest log.

## 12. Open questions

1. Real stage order: is Discovery before or after SQL?
2. How many won, lost and open deals does Pipedrive hold, and since when? (Decides whether the backfill is trivial and whether measured conversions are usable.)
3. Deal value: one currency? Is it annual contract value, total contract value or first-year revenue? Services revenue usually recognises over time, so decide whether "profit" means bookings, revenue or margin.
4. Do you have a 2027 revenue target to plot against?
5. What share of deals is inbound webform vs outbound vs tenders? Tenders in particular have their own cycle and win rate.
6. Who classifies a lemlist reply as interested, and where is that recorded?

## Sources

- Funnel benchmarks: [Belkins pipeline statistics](https://belkins.io/blog/sales-pipeline-statistics), [RevenueGrid](https://revenuegrid.com/uncategorized/sales-funnel-satistics/), [Powered by Search](https://www.poweredbysearch.com/learn/b2b-saas-funnel-conversion-benchmarks/), [SaleHive](https://saleshive.com/blog/understanding-the-b2b-sales-funnel)
- Cold email: [Belkins](https://belkins.io/blog/cold-email-response-rates), [Instantly](https://instantly.ai/blog/cold-email-reply-rate-benchmarks), [Apollo](https://www.apollo.io/insights/what-is-a-good-benchmark-for-reply-rates-in-cold-outreach), [Reply.io](https://reply.io/blog/how-to-improve-your-email-response-rate/), [Cleverly](https://www.cleverly.co/blog/b2b-outbound-benchmarks)
- Forecasting: [Salesforce methods](https://www.salesforce.com/sales/analytics/sales-forecasting-guide/methods/), [Clari](https://www.clari.com/blog/sales-forecast-methods), [Rework](https://resources.rework.com/libraries/pipeline-management/sales-forecasting-methods)
- Coverage: [Fullcast](https://fullcast.com/content/pipeline-coverage-ratios), [Outreach](https://outreach.io/resources/blog/sales-pipeline-coverage-ratio)
- Pipedrive history: [dev community, all-deals stage history](https://devcommunity.pipedrive.com/t/get-all-deals-stages-history/4686), [deals flow endpoint](https://devcommunity.pipedrive.com/t/deals-get-deals-id-flow-endpoint-history/2818)
