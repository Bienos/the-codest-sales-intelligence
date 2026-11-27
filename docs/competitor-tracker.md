# Competitor tracker design

Status: proposal, 2026-11-27. Nothing here is built into the app or deployed. A working prototype of the page is in `docs/prototypes/competitor-tracker.html`, reading a research seed of 36 firms in `docs/prototypes/competitor-tracker-data.json` (open it through a local web server, for example `python3 -m http.server` in `docs/prototypes`). The seed was collected from public sources on 27 Nov 2026.

How to read the numbers: **official** means the firm's own site or filing; **reported** means press, rankings, review sites or job boards; **estimate** means our judgement. Scores, tiers and map placements are judgements and are labelled as such. Every value in the seed carries its source link and date. Where a fact could not be found the seed says "Not found" rather than guessing.

## 1. Summary

1. **What it is.** One page in this app, plus a weekly digest, that answers for everyone from the CEO to a seller: who we compete with, how they position, what they changed, where we stand, and what to do about it. It follows the platform's own evidence rules (ADR-0004): a value without a source and a date is not shown.
2. **How professionals do it.** Competitive-intelligence teams track few rivals deeply and many lightly (tier 1 of five to seven firms), write every update as fact, impact and action, keep battlecards short with talk tracks and proof, show the date on everything, share weekly, and treat a CRM competitor field and win/loss interviews as their most valuable inputs. They avoid feature checklists and stand-alone SWOTs. Section 2 has the evidence.
3. **What the first research found** (section 8): financial services is now the crowded middle; hourly staff augmentation is being repackaged as fixed steps and outcomes; every homepage says AI but almost nobody shows numbers; regulated-buyer proof (ISO 27001, SOC 2, DORA) is common among finance specialists and absent from our site; our Clutch proof is ageing; and several rivals are in ownership or leadership transition.
4. **Where we stand.** Against the median of the seven tier 1 firms we are level on finance focus and AI, ahead on market coverage (all four of the UK, DACH, the Nordics and Poland), and behind on public proof (27 Clutch reviews against a median of 51, the last in April 2025).
5. **Build first** the two inputs nothing else can replace: a competitor field on every Pipedrive deal and a required lost reason. Then add 14 missing firms to the supplier lexicon so vendor intelligence tags them at accounts, load the seed into tables, and switch on weekly collection that reuses Exa, TED and the existing page checks (about 85 searches a month, under $2 at the repo's own Exa estimate). Section 15 has the order.

## 2. What professionals do, and what we take from it

| Practice | Evidence | What we do |
| --- | --- | --- |
| Tier rivals by how often they appear in deals; resource each tier differently | Crayon's tiering guidance; half of CI teams track 1–10 rivals daily, 8 in 10 track 30 or fewer (Crayon 2025, 2026) | Three tiers, tier 1 capped at seven. Until deals record competitors, tier 1 is set by an overlap score (section 7). |
| Track alternatives, not only competitors | April Dunford: ask what a buyer would do without you; enterprise deals lose 20–30% to "no decision" | Four alternatives on the page: hiring in-house, offshore integrators, contractors, doing nothing. |
| Start from key intelligence questions | Herring's Key Intelligence Topics; Klue's KITs and KIQs | Section 3 lists the questions by audience; a move that answers none is not shown. |
| Classify moves; score likelihood, impact, verifiability | Crayon: only 6.1% of detected website changes were worth sending (2017) | Fifteen move types, an impact rule about The Codest, and a person confirms every automated candidate. |
| Write fact, impact, act; bottom line first | Klue's FIA format; US intelligence standard ICD 203 (main message up front, facts separate from judgements) | Every move carries a "So what"; the briefing leads with six findings, each with an action. |
| Show source quality and freshness | NATO/Admiralty grading (source A–F, information 1–6); ICD 203 counts age as a quality factor; staleness is the top CI pain point (58% in 2024, 49% in 2025) | Three grades shown by shape, the source date on every value, a flag after 18 months. We do not use the six-by-six Admiralty grid in the page: it is too fine for sellers. |
| Short battlecards with talk tracks and proof | Klue's audit of 150+ battlecards: only 43% had talk tracks and 19% proof; Gartner: long, abstract cards go unused | Six parts at most: if the buyer mentions them, why buyers pick them, where they are weaker, how we compete, questions to ask, track record. |
| No sales-facing feature checklists; SWOT only with evidence | Klue: feature battles are a "no-go zone"; Hill and Westbrook (1997) found SWOT outputs went unused | Compare shows evidence per field, not ticks. No SWOT. |
| Share weekly | Teams that share weekly or faster report revenue impact 79% of the time against 41% for monthly (Crayon 2026) | Weekly digest. |
| Put competitors in the CRM and run win/loss | 44% of teams have no CRM view of competitors in deals (Crayon 2025); leaders who run win/loss with CI are about three times as likely to report impact (Klue 2025) | Pipedrive competitor field and required lost reason are step 1 of the build. |
| Measure the programme | 55% of teams had no KPIs (Crayon 2025); KPIs, a platform and a sales-executive sponsor together go with revenue impact 90% of the time against 25% (Crayon 2026) | Staged metrics in section 13 and a named sales sponsor. |

These figures come from vendor surveys of mostly SaaS teams. They show correlation, not cause. They are the best public evidence available, not proof for an IT services firm.

How analyst firms compare IT services providers, and what we can copy from public data: ISG Provider Lens (portfolio attractiveness against competitive strength), Everest PEAK Matrix (market impact against vision and capability), Gartner Magic Quadrant (ability to execute against completeness of vision) and Whitelane (client satisfaction only). We can track scope of services, vertical strategy, delivery footprint, partnerships, deals, AI offers and hiring from public data with fair confidence. Pricing and client satisfaction are low-confidence unless they come from our own deals, so the page labels them.

## 3. Questions the tracker answers

| Audience | Questions | Decision it supports |
| --- | --- | --- |
| Leaders | Where are rivals moving in financial services, and is our position still distinct? Who is growing, shrinking, buying or for sale? How are prices and commercial models changing? | Positioning, offers, pricing model, partnerships, which markets to push |
| Sales | Who are we up against in this deal, and how do we win? Which rivals' clients are in transition and open to a second supplier? What will the buyer ask that a rival has already answered? | Deal strategy, target lists, talk tracks |
| Marketing | How do rivals describe themselves, and which words have stopped working? Which proof (references, certificates, reviews, research) do they publish that we do not? | Website copy, proof programme, content |

## 4. Who we track

The seed holds 36 firms: 24 Polish, 4 Ukrainian-born and 8 from elsewhere in Europe. Tiers today:

| Tier | Rule | Firms (seed) |
| --- | --- | --- |
| 1 · direct | The seven highest overlap scores; ties go to the higher finance score, then to the firm closest to our size | Miquido 78, Altkom Software 78, STX Next 75, Software Mind 75, The Software House 73, Pragmatic Coders 70, Neontri 70 |
| 2 · adjacent | Same buyers or same model but not both, or a very different size | Code & Pepper, Avenga, Future Processing, Sollers, Scott Logic, Brainhub, Netguru, Spyrosoft, Ailleron, N-iX, Mindera, 10Clouds, Sii, ELEKS, Britenet, Monterail, Scalefocus, Infinum, Intellias, Ciklum, Boldare, Billennium |
| 3 · reference | Rarely met; learn from them: bellwethers and specialists | Endava, Software Mansion, Tooploox, Polcode, Sparkbit, HTEC, Pentalog |

Rules for the list:
- **Add** a firm when it appears in two Pipedrive deals, is named as a supplier at three target accounts by vendor intelligence, or wins a public contract in our markets.
- **Retire** a firm after twelve months with no deal, no account appearance and no move.
- **Re-tier** every quarter. Once five or more deals against a firm are decided, tier 1 becomes the seven firms highest on the threat index (section 7).
- Sellers should check tier 1 now: a five-minute question to each seller ("which of these did you meet in the last year?") is the fastest correction before deal data exists.

The seed was built from the `engineering_partner` list in `backend/rules/vendor_lexicon.yaml`. **Fourteen tracked firms are missing from that lexicon**, two of them tier 1, so vendor intelligence cannot tag them correctly at accounts: Software Mind, Neontri, Pragmatic Coders, Code & Pepper, Britenet, Billennium, Monterail, Polcode, Ailleron, Scalefocus, Infinum, Mindera, HTEC (engineering partners) and Sollers Consulting (systems integrator).

## 5. What we record about each competitor

| Lens | Field | How it is measured | Source | Refresh |
| --- | --- | --- | --- | --- |
| Identity and scale | HQ, founded, ownership | Ownership type and owner (founder, private equity, listed, group) | Own site, KRS, Companies House, press | Yearly, or on a move |
| | Headcount | Latest public figure, else a band | Own site, filings, LinkedIn (looked at, never scraped) | Monthly |
| | Revenue and trend | Latest year and prior year, currency and year stated | KRS statements, ITwiz BEST100, listed reports, Companies House | Yearly |
| Positioning | Homepage headline | Copied word for word, with the date | Own site | Weekly change check |
| | One-line summary and archetype | Our summary; archetype from a fixed list | Estimate | Quarterly |
| Offer | Services, engagement models, first step | Named offers; fixed-length or priced first step | Own site | Monthly |
| | AI in the offer | Rubric 0–3 with a quoted line | Own site | Monthly |
| Focus | Financial services | Rubric 0–3 with the evidence | Own site, Clutch mix, case studies | Quarterly |
| | Markets | Which of UK, DACH, Nordics, Poland, US it targets; offices | Own site, press | Quarterly |
| Proof | Clients, certificates, partner tiers | Named only where public | Own site, partner directories, IAF CertSearch | Quarterly |
| Reputation and price | Clutch rating, reviews, rate band, minimum, last review | As listed | Clutch (by hand) | Monthly |
| Momentum | Open roles | Count of offers | justjoin.it sitemap, nofluffjobs | Weekly (automated) |
| | Moves | Section 6 | All | Weekly |
| Battlecard (tier 1) | Six parts | Written by marketing with sales | Our judgement plus the facts above | Every 90 days |

The map placement (finance focus 0–10, people to outcomes 0–10) carries a one-line reason on every firm so it can be challenged.

## 6. Moves

A move is a dated thing a competitor did, with its source and a "So what" for us.

**Types:** positioning, new offer, pricing or model, vertical move, market entry, acquisition made, acquired or merged, ownership or capital, leadership, hiring or cuts, partnership or certificate, client win, content or event, results, award or ranking.

**Impact** is about The Codest, not about the competitor:
- **High:** changes how we compete for banks, insurers or fintechs in the UK, DACH, the Nordics or Poland: a tier 1 firm buys a specialist, opens in one of our markets, changes its price model, or launches the same offer as ours.
- **Medium:** a real change in offer, positioning, scale, ownership or leadership at a tier 1 or 2 firm, outside our core.
- **Low:** awards, content, events, normal hiring, results in line with the market.

**Workflow:** automated collection creates a candidate with the page, the quote and a suggested type. A person confirms it, sets the impact and writes the "So what" (required for High and Medium), or dismisses it. High moves are posted the same day; Medium and Low wait for the digest. This mirrors how vendor intelligence already works: the model may only cite a returned page, and its quote must be on that page.

The seed holds 60 moves. Examples of High: Miquido's move from time and materials to outcome-based billing with the UK and DACH as priorities (Nov 2026); Software Mind's Nordic fintech push (Oct 2026); Endava's audit investigation and restatement (Oct 2026); 10Clouds' AI partnership with PZU (Mar 2026); Avenga's core-banking migration for a Swedish bank on Mambu (Jun 2025); N-iX's Warsaw office taking its Polish team past 400 (Jul 2025); Scalefocus hiring a DACH sales lead (Sep 2025).

## 7. Scores and the map

All scores are judgements from public pages, shown with their reason. There is deliberately no single "competitor strength" score: it would hide the reasoning, and the analyst firms above use two axes with published criteria for the same reason.

**Financial-services focus (0–3):** 0 not mentioned, or one logo among many; 1 one industry among several, one or two case studies; 2 one of the two or three industries it leads with, or 30%+ of reviewed work, with several named financial clients; 3 financial services is the identity (all of the positioning or most of the revenue, own products, or core-platform partnerships).

**What they sell, people to outcomes (0–10):** 0–2 mostly people by the month; 3–5 team extension and dedicated teams lead, some agreed-scope projects; 6–8 product development with delivery responsibility leads; 9–10 sells outcomes (consulting-led programmes, managed services, own products).

**AI in the offer (0–3):** 0 none; 1 a skill or blog topic; 2 a named AI service line; 3 the headline, or AI-native delivery sold as the model.

**Overlap with us (0–100)** says who is likely to meet us in a deal, not who is better: same buyers (finance score, 30 points), same way of selling (closeness to us on the people-to-outcomes axis, 30 points when level, none at six steps), same markets (5 points for each of the UK, DACH, the Nordics and Poland), similar size (20 under 500 people, 12 under 2,000, 5 under 6,000, none above, 10 when unknown). Each part is rounded and the total is their sum.

**Threat index (once deal data exists):** a competitor's share of our competitive deals divided by our win rate against it (Crayon's "competitor coefficient"). A firm we meet often and beat rarely scores high. Win rate is shown only after five decided deals against that firm.

## 8. What the first research found

### 8.1 Where we stand

| Measure | Us | Tier 1 median | Reading |
| --- | --- | --- | --- |
| Clutch reviews | 27, last April 2025 | 51 | Behind |
| Financial-services focus (0–3) | 2 | 2 | Level |
| Our four markets covered | 4 | 2 | Ahead |
| AI in the offer (0–3) | 2 | 2 | Level |
| Named, fixed-length first step | No | 1 of 7 | Level |
| Security or DORA proof on the site | Not shown | 3 of 7 | Level |
| Headcount | about 150 | about 260 | Similar |

Our own profile (from our site, Clutch and LinkedIn, 27 Nov 2026): headline "We take responsibility for the software that drives your revenue. As if it were our own."; Clutch 4.8 from 27 reviews, $50–99 an hour, $10,000 minimum, 50% financial services, 40% staff augmentation; London HQ with Kraków and Warsaw hubs; three AI services. Our revenue and exact headcount are not public; add them from our own accounts.

### 8.2 Patterns, with the indicator measured across the 36 firms

| Pattern | Indicator now | What it means for us |
| --- | --- | --- |
| Everyone says AI now | 20 of 36 homepage headlines mention AI or agents; the promise has converged on "AI speed, human control" | Saying AI no longer sets anyone apart. Measured results do. |
| Selling something other than hours | 16 of 36 publicly offer fixed scope, sprints, pods, outcomes or managed service | Keep staff augmentation; add a priced, agreed-scope option next to it. |
| A small, safe first purchase | 5 of 36 sell a named, fixed-length first step (a sprint, workshop or assessment) | Offer one, for example a two-week assessment of a live system or an AI prototype on one workflow. |
| Financial services is crowded | 22 of 36 lead with it | "We work with banks" is not a position. Name problems, systems and countries where we have proof. |
| The regulated-buyer file | 13 of 36 show ISO 27001, SOC 2 or DORA readiness | Show what we hold, after clearance; if nothing, ISO 27001 is the cheapest gap to close for bank deals. |
| AI vendor badges | 10 of 36 show an Anthropic or OpenAI partner tier | Decide whether a model-vendor partnership is worth it for AI Services. |
| Generalists shrink, specialists grow | 12 of 25 with data shrank in their latest year | The market rewards a sharp specialism, said the same way everywhere. |
| Consolidation | 8 of 36 bought, sold or merged in two years; Software Mind's sale failed | Rivals' clients get new owners and account teams: an opening. |
| Polish delivery is no longer a difference | 6 of 12 non-Polish firms run delivery in Poland | Our argument is Polish-managed, one jurisdiction and people who stay, not location. |
| The Nordics are less crowded | 7 of 36 target the Nordics | A Nordic bank reference is worth more than another Polish one. |
| Buyers punish passive suppliers | 43% of UK buyers say suppliers do not challenge them | Every engagement starts with what we would change, in writing. |
| AI moves budget from builds to pilots | 51% of German buyers say AI reduces the need to outsource | Our AI prototyping offer is where the budget is; each pilot should lead into engineering work. |

### 8.3 Market temperature

Latest reported quarter, year on year: Grid Dynamics +9.7% (Q3 2026), Spyrosoft +9.6% (nine months 2026, about +5% in Q3), EPAM +2.3% (financial services +13.7%), Nagarro −0.2%, Globant −1.0%, Ailleron −2.0% (Q1 2026), Endava −6.4% in constant currency (Jan–Mar 2026, later figures being restated), Ework (Nordic contractors) −8.0%. Growth has gone to firms with a large AI book; generalist digital-engineering firms are flat or shrinking. ISG's EMEA index for Q3 2026 shows banking, financial services and insurance managed-services awards down by double digits, DACH up 80% and the Nordics down.

### 8.4 Buyers

Whitelane 2026: in the UK and Ireland financial services is the most cautious sector (21% will increase outsourcing, 25% reduce) yet 40% of financial firms plan more offshore; top supplier complaints are "does not challenge" (43%), poor value (27%), stuck in time and materials (24%) and reliance on staff augmentation (23%). In the Nordics nearshore intent (+30%) beats offshore (+27%). In Germany 43% will increase nearshore, 51% say AI reduces the need to outsource, and 31% plan to cut external IT spend. DORA has applied since 17 January 2025: banks must keep a register of every ICT supplier, subcontractors included, with due diligence, contract clauses and exit plans. We found no published evidence yet that DORA pushes banks away from small suppliers; treat that as a hypothesis to watch.

### 8.5 Recommendations

These are the briefing's actions, in priority order. Any claim that reaches a prospect must pass the brand claims register (`backend/rules/brand_claims.yaml`) first.

1. Narrow the position: lead with financial-services engineering everywhere, name the problems and countries where we have proof, and win references in the UK, DACH and the Nordics, where the Polish specialists have none.
2. Add one fixed-length, fixed-price first step and an agreed-scope delivery option next to staff augmentation.
3. Measure our own AI-assisted delivery on one real engagement and publish it once cleared.
4. Assemble a regulated-buyer file (security certificates, DORA third-party answers) and show it once cleared; if we hold no ISO 27001, start it.
5. Ask three financial-services clients for Clutch reviews this quarter; keep one a month.
6. List target accounts where Endava, Avenga, N-iX or Software Mind are the named supplier (vendor intelligence already finds this) and open second-supplier conversations there.

## 9. The page

| View | For | Shows |
| --- | --- | --- |
| Briefing | Everyone, leaders first | Six findings with an action each; where we stand; tier 1 at a glance; the biggest moves of the last 12 months |
| Landscape | Leaders, marketing | Positioning map (finance focus against people-to-outcomes, mark size by headcount, shape by origin), overlap ranking, the buyer's alternatives |
| Compare | Sales, marketing | Us against up to four firms, field by field, each value with source, grade and date |
| Profiles | Sales | One page per firm: facts, headline word for word, offer, focus, battlecard, moves |
| Moves | Marketing, sales | Every dated move with impact, type and "So what", filterable |
| Patterns | Leaders | Bellwether growth, twelve patterns with indicators, buyer evidence, price bands, deals |
| Head-to-head | Sales leadership | Deals met, win rate and threat index per firm; empty until Pipedrive records competitors |
| Method | Everyone | Questions, tiers, rubrics, collection, rules |

Design rules applied: summary before detail; source grade shown by shape (filled, half, outline) so it never depends on colour; source date on every value; a "So what" on every move; battlecards in six parts or fewer; no feature checklist; a table behind every chart; a colour-blind-safe palette checked with a validator in light and dark; works at phone width. The prototype uses the app's own tokens, fonts and square edges, so it can be ported into `frontend/src/pages` as one page.

Where it appears in the rest of the app: a "Competitors" entry under "Look up"; a competitor chip on account pages when vendor intelligence names a tracked firm as a supplier; a competitor field on opportunities; tracked winners highlighted on the contract awards list.

**Weekly digest** (Monday, one screen): the three highest-impact moves with their "So what", any tier change, one pattern update, one action with an owner.

## 10. Data model and how it plugs into this repo

Sketch only; follow the repo's migration and append-only conventions (`forbid_mutation`, as on `opportunity_transitions`).

```sql
CREATE TABLE competitors (                       -- one row per tracked firm, linked to the shared company list
  organization_id uuid PRIMARY KEY REFERENCES organizations(id),
  tier smallint NOT NULL CHECK (tier IN (1,2,3)),
  kind text NOT NULL,                            -- engineering_partner | system_integrator | staffing_msp (lexicon categories)
  origin text NOT NULL, owner text, added_at timestamptz NOT NULL DEFAULT now(), retired_at timestamptz,
  map_x numeric, map_y numeric, map_reason text  -- judgement, shown with its reason
);
CREATE TABLE competitor_facts (                  -- append-only; newest per field wins
  id bigserial PRIMARY KEY, organization_id uuid NOT NULL REFERENCES competitors(organization_id),
  field text NOT NULL, value jsonb NOT NULL, as_of date, source_url text NOT NULL, quote text,
  grade text NOT NULL CHECK (grade IN ('official','reported','estimate')),
  collected_by text NOT NULL, collected_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE competitor_page_snapshots (         -- homepage, services, finance, AI, careers pages: change detection
  id bigserial PRIMARY KEY, organization_id uuid NOT NULL, url text NOT NULL, kind text NOT NULL,
  fetched_at timestamptz NOT NULL, content_hash text NOT NULL, headline text, UNIQUE (url, content_hash)
);
CREATE TABLE competitor_moves (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL,
  move_type text NOT NULL, happened_on date, headline text NOT NULL, detail text,
  source_url text NOT NULL, quote text NOT NULL, grade text NOT NULL,
  impact smallint CHECK (impact BETWEEN 1 AND 3), so_what text, owner text,
  status text NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate','confirmed','acted','dismissed')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE deal_competitors (                  -- from the Pipedrive field, via crm_webhook_inbox
  crm_deal_id bigint NOT NULL, organization_id uuid NOT NULL,
  role text NOT NULL CHECK (role IN ('incumbent','shortlisted','won_against','lost_to','unknown')),
  noted_at timestamptz NOT NULL DEFAULT now(), reason text, PRIMARY KEY (crm_deal_id, organization_id)
);
```

| Existing piece | Use |
| --- | --- |
| `backend/rules/vendor_lexicon.yaml` | The name list and aliases; add the 14 missing firms (section 4) |
| `backend/src/ri/research/vendor_intel.py`, `supplier_edges`, `account_vendor_intel` | "Competitor at a target account" with the quoted page; feeds Head-to-head and second-supplier lists |
| `awarded_contracts` (TED) | Public contract wins by tracked firms, with expected end dates |
| `backend/src/ri/crm/pipedrive.py`, `crm_webhook_inbox` | Deal competitor field and lost reason into `deal_competitors` |
| `backend/src/ri/research/exa_search.py`, `budget.py` | Weekly news and page searches under the existing monthly Exa cap |
| Page checks and findings pipeline | A confirmed move about an account (for example a supplier exit) can still become a finding through the normal route |
| `docs/prototypes/competitor-tracker-data.json` | Seed for the tables; the counts in it were computed when it was built |

## 11. Collection: sources, cadence and cost

| Source | Reveals | Access | Verdict |
| --- | --- | --- | --- |
| KRS API and change bulletin | Owners, board, capital changes, dates of filed accounts | Free REST, no key; lookup by KRS number | Core |
| KRS financial statements (RDF) | Revenue, profit, average employment | Free web, no official API, anti-bot | Core, yearly by hand |
| ITwiz BEST100 | Revenue of Polish IT firms, with prior year | Public tables each July | Core, yearly |
| Companies House, SEC, Warsaw Stock Exchange | UK subsidiaries' accounts; listed firms' results | Free APIs | Core |
| Competitor sitemaps and blog feeds | New case studies, offers, AI pages, logos | Public; sitemaps worked for 12 of 16 domains tested | Core, automated |
| justjoin.it and nofluffjobs | Open roles, stacks, salary ranges | Public sitemaps; their APIs are disallowed | Core, automated at a low rate |
| TED, Poland's BZP, UK Find a Tender and Contracts Finder | Contract awards and winners | Free, anonymous | Core; TED already in the repo |
| Polish and trade press feeds | Deals, wins, rankings | RSS (ITwiz, rp.pl) and Exa | Core |
| Clutch | Rating, reviews, rate band, minimum | Terms ban scraping | Monthly by hand |
| LinkedIn | Headcount | Terms ban scraping | Look monthly, note by hand |
| Visualping or Distill | Pricing and careers page changes | Free tiers | Nice to have |
| Ahrefs, Semrush, Similarweb | Traffic and keywords | From about $129 a month | One at most, later |
| Crunchbase, PitchBook, G2, Glassdoor, GoWork | Deals, reviews | Paid, blocked or banned | Skip |

Cadence: weekly automated (sitemaps, news, awards, KRS bulletin, job boards); monthly by hand (Clutch, LinkedIn); quarterly (partner directories, ad libraries, event sponsor lists); yearly (financial statements, ITwiz BEST100, FT1000).

Cost: tier 1 twice a week (14 searches a week), tier 2 once a month (22) and tier 3 once a quarter come to about 85 Exa searches a month. At the repo's own estimate of about $0.005 a search plus $0.001 a page, that is under $2 a month before the model call that summarises a change. The bigger cost is the 30 to 60 minutes a week a person spends confirming moves and writing the "So what".

Rate benchmarks found: Clutch's pricing guide lists Poland at $50–99 an hour and Ukraine and India at $25–49; Accelerance puts the Central and Eastern European senior developer median at $65 and an agentic-AI engineer at $80, with rates holding in 2026 after a 4.4% fall in Europe the year before. Most tracked firms list $50–99 on Clutch, as we do; Infinum lists $150–199, Software Mansion and Sparkbit $100–149.

## 12. Operating rhythm and ownership

| When | What | Owner |
| --- | --- | --- |
| Every deal | Competitor field on the deal; lost reason and winner when lost | Seller |
| Weekly | Confirm moves, write the "So what", send the digest on Monday | Marketing (CI owner) |
| Monthly | Clutch, headcount and roles; digest to sales; review two tier 1 battlecards | Sales operations |
| Within 30 days of a decided deal over an agreed size | Win/loss interview with the buyer, with consent | Sales lead |
| Quarterly | Patterns, map and tiers with leadership; battlecard refresh; win/loss summary | CEO with marketing |

A sales executive should sponsor the programme; in the surveys above, a sponsor is one of the three things that go with reported revenue impact.

## 13. How we know it works

1. **Adoption** (first quarter): page views per seller, digest opens, moves submitted by sellers.
2. **Confidence** (second quarter): a two-question seller survey before and after battlecards go live ("How confident are you against X?").
3. **Outcomes** (from the third quarter, once deals exist): win rate and share of competitive deals against each tier 1 firm, the threat index trend, and lost reasons by competitor.
4. **Speed**: days from a move happening to it appearing in the digest.

## 14. Rules we keep

1. Public sources, official registers and licensed data only; we comply with the law where we collect.
2. No pretexting: we never pose as a buyer, a candidate or anyone else, and give our name and company in any conversation.
3. We follow site terms and robots.txt, and never get round logins, CAPTCHAs, bot protection or rate limits. No automated collection from LinkedIn, Clutch or G2.
4. We never ask rivals' staff, former staff or shared clients for confidential information, and new hires do not bring a former employer's documents.
5. We track companies, not people. Names appear only in business roles (a new CEO), under a documented legitimate interest (GDPR Art. 6(1)(f)), kept no longer than needed.
6. Every value keeps its source link and date, and any attribution the source requires.
7. Claims about rivals used with buyers must be checked and fair. No disparaging rivals.
8. Win/loss interviews need the buyer's consent and a clear statement of purpose.

## 15. Build order

1. **Pipedrive** (this week, no code): add a Competitor field (multi-select from the tracked list plus "Other") and make Lost reason required. The sales metrics design (`docs/sales-metrics-and-forecast.md`) asks for the same field. History cannot be recovered later.
2. **Lexicon**: add the 14 missing firms to `vendor_lexicon.yaml` so vendor intelligence tags them at accounts.
3. **Tables and seed**: the migration in section 10; load `competitor-tracker-data.json`.
4. **Weekly collection**: sitemap and headline checks, Exa news per tier, TED winners matched to tracked firms, the KRS change bulletin; then BZP and Find a Tender next to TED. Candidates go to a review queue.
5. **Page and digest**: port the prototype into the React app and add the Monday digest.
6. **Head-to-head and win/loss**: capture `deal_competitors` from the deal webhook; start interviews; switch tier 1 to the threat index after five decided deals per firm.

## 16. Open questions

1. Who owns the tracker week to week (marketing is the usual owner) and which sales executive sponsors it?
2. Do we hold ISO 27001 or a DORA third-party answer pack? This decides whether recommendation 4 is "show it" or "start it".
3. Which client names and numbers may we use in proposals? The claims register blocks MUFG and Allegro, which appear on our homepage.
4. Which tracked firms have sellers actually met in the last year? This corrects tier 1 before deal data exists.
5. Our exact headcount and revenue for the "us" column.
6. Is one SEO tool worth about $129 a month for content and keyword tracking, or not yet?
7. The brand rules block the US market in drafts. The tracker records rivals' US presence as context only; confirm that is right.

## Sources

Per-fact sources, with dates, are in `docs/prototypes/competitor-tracker-data.json`. Method and market sources used in this document:

- Competitive-intelligence practice: [Crayon State of CI 2025](https://www.crayon.co/hubfs/Crayon%27s%202025%20State%20of%20CI%20Report.pdf), [Crayon State of CI 2024](https://crayon.co/hubfs/Algert/2024%20State%20of%20Competitive%20Intelligence/Competitive%20Intelligence%20Report%202024_PDF.pdf), [Crayon 2026](https://www.crayon.co/blog/state-of-competitive-intelligence-2026), [Crayon competitor tiers](https://crayon.co/blog/competitor-tiers), [Crayon competitor coefficient](https://www.crayon.co/blog/competitor-coefficient), [Crayon on market intelligence noise](https://www.crayon.co/blog/market-intelligence), [Klue battlecards 101](https://klue.com/blog/competitive-battlecards-101/), [Klue battlecard audit](https://klue.com/blog/sales-battlecard-mistakes-according-to-the-data), [Klue KITs](https://klue.com/blog/get-your-kits-together), [Klue win-loss statistics](https://klue.com/blog/win-loss-analysis-statistics), [Forrester on CI programmes](https://www.forrester.com/blogs/five-findings-about-todays-market-and-competitive-intelligence-programs/), [Gartner on battlecards](https://www.gartner.com/en/documents/5220863), [April Dunford on competition](https://aprildunford.substack.com/p/positioning-and-competition), [ICD 203](https://www.dni.gov/files/documents/ICD/ICD-203.pdf), [UK JDP 2-00](https://assets.publishing.service.gov.uk/media/653a4b0780884d0013f71bb0/JDP_2_00_Ed_4_web.pdf), [SCIP code of ethics](https://scip.world/ethics)
- Analyst frameworks: [ISG Provider Lens](https://isg-one.com/research/isg-provider-lens/isg-provider-lens-quadrant), [Everest PEAK Matrix](https://www.everestgrp.com/wp-content/uploads/2025/07/Everest-Group-PEAK-Matrix-Assessment-and-RFI-Overview.pdf), [Whitelane Europe 2025/26](https://whitelane.com/europe-2025-2026/)
- Buyers: [Whitelane UK and Ireland 2026](https://whitelane.com/uki-2026/), [Whitelane Nordics 2026](https://whitelane.com/nordics-2026/), [Whitelane Germany 2026](https://whitelane.com/germany-2026/), [ISG EMEA index Q3 2026](https://via.tt.se/pressmeddelande/4590434/demand-for-tech-services-in-europe-continues-to-surge-led-by-ai-driven-infrastructure-spending-in-q3-isg-indextm?lang=en), [Bain buyer survey](https://www.bain.com/insights/tech-services-buyer-survey-betting-big-on-ai-and-resilience/), [Forrester on AI and services pricing](https://www.forrester.com/blogs/for-service-providers-ai-giveth-and-ai-taketh-away/)
- Bellwethers: [EPAM Q3 2026](https://www.sec.gov/Archives/edgar/data/0001352010/000135201026000048/exhibit99_q3x2026.htm), [Endava Q3 FY26](https://www.sec.gov/Archives/edgar/data/1656081/000165608126000015/endavapressreleaseq3fy26.htm), [Endava investigation update](https://www.financialcontent.com/article/bizwire-2026-10-28-endava-provides-update-on-audit-committee-investigation), [Globant Q3 2026](https://www.sec.gov/Archives/edgar/data/0001557860/000110465926130143/tm2630201d1_ex99-1.htm), [Grid Dynamics Q3 2026](https://stockstory.org/us/stocks/nasdaq/gdyn/news/earnings-call/gdyn-q3-deep-dive-ai-driven-delivery-boosts-margins-and-productivity-gains), [Nagarro Q3 2026](https://www.cantechletter.com/newswires/nagarro-releases-unaudited-q3-2026-results-posts-2-7-constant-currency-growth-for-9-months-ended-september-2026/), [Spyrosoft Q3 2026](https://www.bankier.pl/wiadomosc/SPYROSOFT-S-A-Wyniki-finansowe-QSr-3-2026-9236599.html), [Ailleron Q3 2026](https://ailleron.com/press-releases/grupa-ailleron-record-third-quarter-2026/), [Ework Q3 2026](https://www.placera.se/pressmeddelanden/ework-group-ework-group-publishes-interim-report-january-september-2026-stable-profitability-and-increased-activity-across-several-markets-20261022)
- Polish market and deals: [ITwiz BEST100 2026](https://itwiz.pl/najwieksze-firmy-it-w-polsce-w-roku-2025-ranking-itwiz-best100/), [Tailwind and Software Mind](https://www.biznesradar.pl/a/145558,tailwind-rezygnuje-z-przejecia-software-mind), [Miquido transformation](https://xyz.pl/polacy-zbudowali-aplikacje-dla-ponad-100-mln-osob-teraz-przechodzi-gleboka-transformacje/), [ABSL 2026](https://absl.pl/en/news/p/business-services-sector-sets-development-path-polish-economy)
- Data sources and rates: [KRS API notes](https://outreachpilot.pl/blog/api-krs-odpis-aktualny-json), [Companies House API](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/reference), [Clutch terms](https://clutch.co/terms), [LinkedIn user agreement](https://www.linkedin.com/legal/user-agreement), [justjoin.it sitemap](https://justjoin.it/sitemaps/active-jobs/part0.xml), [Clutch pricing guide](https://clutch.co/developers/pricing), [Accelerance rates 2026](https://www.accelerance.com/blog/software-engineering-rates-2026), [DORA overview](https://en.wikipedia.org/wiki/Digital_Operational_Resilience_Act)
