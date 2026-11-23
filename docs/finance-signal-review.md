# Banking and financial services signal review

A seller should be able to answer three questions from the first screen. Which legal entity could buy? What did that entity actually do? What evidence justifies the next action?

The local implementation defaults Today, Accounts and Market Feed to banking and financial services. Other industries, unclassified accounts and public procurement remain accessible through separate choices. Classification starts from the account segment. Mentions of banking in a vendor's article do not classify the vendor as a bank. An unlinked market finding may use the reported buyer role as context and is labelled unmatched.

| Step | Before | Local implementation |
| --- | --- | --- |
| 1. Today | The programme and route were visible, but the seller had to open the source or Opportunity for the actual signal. | Sector focus narrows the queue first. A named signal, demand status and source sit on the opportunity card. Public tenders have their own view. |
| 2. Accounts | A score and signal group count helped triage an account but did not show the event or proof. | The leading current signal, date and source sit in the account row. Account Overview shows the exact excerpt and a qualification question when available. |
| 3. Market Feed | The headline and suggested service were easier to find than the rule that fired. | The card shows the named signal or the evidence gap before suggested fit. Discovery and confirmed account links remain distinct. |
| 4. Opportunity | The score and seven checks were available, but the signal underlying the programme was implicit. Some cards could pick up a tender from a different programme at the same account. | The programme's own signal and excerpt appear above the qualification checks. Tender deadlines require a notice tied to that programme and buyer. |

The worklist ranks current signals by their evidence of outside demand, then by recency and strength. It keeps proof separate from the recommendation. A funded core replacement or vendor appointment can be strong evidence of a programme while the bank's willingness to buy external engineering stays unknown. An approved vendor list, a tender that explicitly asks for software engineering, or a buyer confirming a need supports a different next step. Workbook links remain leads to verify. A source checked for one legal entity cannot verify a second entity merely because both workbook rows cite it.

## Source checks on 23 November 2026

The repository lists 90 source entries, including one generic per-account company newsroom/careers entry. That count includes candidates and internal sources and does not measure live coverage. I probed 17 configured endpoints. Ten returned HTTP 200, and the actual feed connector could parse all three successful RSS feeds that I sampled. These are checks from the review workspace, not production worker validation.

| Intent | Result and recommendation |
| --- | --- |
| Official procurement | TED and UK Find a Tender returned official records. Use the named buyer, exact notice, engineering scope, open status and deadline together. Keep public procurement separate and do not translate routine renewals into software delivery leads. |
| Legal entity identity | GLEIF returned LEI records. Use identifiers, jurisdiction and supported aliases to resolve bank groups and subsidiaries. Registry entries establish identity, not purchasing intent. |
| First-party programme evidence | Baseline coverage is a generic per-account template. Build a named list of official bank and insurer newsrooms, investor releases, supplier portals and careers feeds for priority accounts. A funded change or contractor request is more useful than broad news coverage. |
| Vendor and partner announcements | Mambu, Thought Machine, Finastra and Form3 pages returned HTML. Temenos returned 403. These sources can show a named customer, selected platform and incumbent route. Treat a vendor win as a reason to qualify the route, not a claim that subcontracting is available. |
| Industry discovery | The Paypers, IT Finanzmagazin and Bankier returned entries through the app's connector. The sample included an APAC payments pilot, an explicitly sponsored article, and broad market-news stories. Use these to find leads, then find the buyer's underlying evidence. Exclude ads and duplicate press releases from seller priorities. |
| Blocked or failing endpoints | FCA, EBA, Temenos, Finextra and FinTech Futures returned 403. Finanz-Szene returned 500 and Fintek.pl returned 404. Recheck through the production worker and confirm a supported endpoint before counting any as coverage. Regulatory notices support timing only for named or demonstrably in-scope buyers. |

Measure source usefulness by fresh findings that resolve to the correct legal entity and generate a seller-accepted action. Track unmatched buyers, stale links, duplicate releases and false buying signals separately. A useful next audit would label a sample of 30 recent production findings by buyer identity, intent, freshness and next action, starting with the priority banking accounts.

## Verification and release

The screen captures use the app's development seed and labelled fixtures. Production requires an authorized seller sign-in, so this review did not measure the accuracy or freshness of actual production leads. Browser checks exercised the finance default, different sector and public views, and the exact source link from account to opportunity at 1440 and 390 px.

The implementation is local. Migration 0030 changes the uniqueness rule for workbook verification requests. Deploy the compatible worker code before the web service applies that migration so existing discovery jobs can keep writing during release.

The final backend suite passed 646 tests. Two pre-existing knowledge-base tests still require private HTML files that are excluded from the repository. The TypeScript and production frontend build passed.
