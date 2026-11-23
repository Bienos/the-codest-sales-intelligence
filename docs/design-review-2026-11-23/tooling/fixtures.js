// FIXTURE DATA for the design review only. Every name is invented and prefixed "FIXTURE" or "SYNTHETIC".
// Served through Playwright route interception for screens the dev seed leaves empty. Never sent to any real service.
const now = Date.now();
const iso = (d) => new Date(now + d * 864e5).toISOString();
const day = (d) => iso(d).slice(0, 10);
const ORG = process.env.ORG_ID;

const lead = (i, o) => ({ id: i === 0 ? ORG : `00000000-0000-4000-8000-0000000000${String(i).padStart(2, "0")}`, name: `FIXTURE ${o.n}`, country_code: o.c, segment: o.seg, employees: o.e, employees_uncertain: !!o.unc,
  in_master_list: o.origin === "list", canonical_domain: o.dom, entity_type: "company", created_at: iso(-40), score: o.s ?? null, band: o.b ?? "none", groups: o.g ?? [], signal_count: o.g ? o.g.length + 1 : null,
  stopped: !!o.stop, last_signal_at: o.s ? iso(-o.age) : null, top: null, candidate_status: o.cand ?? null, shortlist: !!o.sl, watched: !!o.w, region: o.r, icp: o.icp, boundary_uncertain: false, origin: o.origin });
const LEADS = [
  { n: "Northbridge Bank", c: "GB", seg: "Banking", e: 42000, r: "uk", icp: "strategic_5000_plus", origin: "list", s: 82, b: "hot", g: ["PRG", "CAP", "SRC"], age: 3, sl: true, w: true, dom: "northbridge.example" },
  { n: "Alpenkasse AG", c: "DE", seg: "Banking", e: 8600, r: "dach", icp: "strategic_5000_plus", origin: "list", s: 71, b: "hot", g: ["MAN", "PRG"], age: 6, dom: "alpenkasse.example" },
  { n: "Fjordpay AS", c: "NO", seg: "Payments", e: 1200, unc: true, r: "nordics", icp: "enterprise_1000_plus", origin: "discovered", s: 58, b: "warm", g: ["CAP"], age: 9, cand: "pending_qualification", dom: "fjordpay.example" },
  { n: "Wisła Leasing SA", c: "PL", seg: "Leasing", e: 640, r: "poland", icp: "scaleup_500_999", origin: "added", s: 44, b: "warm", g: ["REG"], age: 21, dom: "wislaleasing.example" },
  { n: "Thames Mutual", c: "GB", seg: "Insurance", e: 3100, r: "uk", icp: "enterprise_1000_plus", origin: "list", s: 37, b: "watch", g: ["COM"], age: 34, w: true, dom: "thamesmutual.example" },
  { n: "Helvetia Clearing", c: "CH", seg: "Market infrastructure", e: 2200, r: "dach", icp: "enterprise_1000_plus", origin: "list", s: 0, b: "none", stop: true, dom: "hclearing.example" },
  { n: "Baltic Card Services", c: "SE", seg: "Payments", e: 900, unc: true, r: "nordics", icp: "scaleup_500_999", origin: "discovered", s: 29, b: "watch", g: ["REL"], age: 48, cand: "qualified", dom: "balticcard.example" },
  { n: "Mercia Building Society", c: "GB", seg: "Banking", e: 1500, r: "uk", icp: "enterprise_1000_plus", origin: "list", dom: "mercia.example" },
  { n: "Rheinland Versicherung", c: "DE", seg: "Insurance", e: 12000, r: "dach", icp: "strategic_5000_plus", origin: "list", s: 52, b: "warm", g: ["CAP", "MAN"], age: 12, dom: "rheinland.example" },
  { n: "Kraków Digital Bank", c: "PL", seg: "Banking", e: 450, r: "poland", icp: "below_threshold", origin: "added", dom: "kdb.example" },
].map((o, i) => lead(i, o));

module.exports.fixtures = {
  "/api/leads": { items: LEADS, total: 1284, page: 1, size: 50,
    facets: { region: { uk: 512, dach: 388, nordics: 201, poland: 143, europe: 30, us: 10 }, icp: { strategic_5000_plus: 210, enterprise_1000_plus: 480, scaleup_500_999: 390, below_threshold: 150, unknown: 54 }, segment: { Banking: 600, Payments: 300, Insurance: 250, Leasing: 80, "Market infrastructure": 54 }, band: { hot: 18, warm: 64, watch: 140, none: 1062 }, origin: { list: 1100, added: 34, discovered: 150 } },
    regions: [{ key: "uk", label: "UK" }, { key: "dach", label: "DACH" }, { key: "nordics", label: "Nordics" }, { key: "poland", label: "Poland" }, { key: "europe", label: "Rest of Europe" }, { key: "us", label: "US" }],
    origins: { list: "Your lists", added: "Added by a person", discovered: "Found by the platform" } },
  "/api/renewals": { months: 12, contracts_known: 18342, records_since: "2021-01-01", items: [
    { notice_id: "FX-1", months_left: 2, buyer_country: "GB", on_lists: true, end_basis: "stated", buyer_org_id: ORG, buyer_name: "FIXTURE Northbridge Borough Council", title: "United Kingdom – IT services – Case management platform support and development", winners: ["FIXTURE Integrator Ltd"], winner_size: "large", awarded_on: day(-1035), expected_end: day(60), official_url: "https://example.invalid/ted/1" },
    { notice_id: "FX-2", months_left: 5, buyer_country: "DE", on_lists: false, end_basis: "assumed", buyer_org_id: null, buyer_name: "FIXTURE Landesamt für Digitales", title: "Germany – Software development – Citizen portal backend", winners: ["FIXTURE Softwarehaus GmbH", "FIXTURE Consulting AG"], winner_size: "sme", awarded_on: day(-930), expected_end: day(150), official_url: "https://example.invalid/ted/2" },
    { notice_id: "FX-3", months_left: 9, buyer_country: "SE", on_lists: false, end_basis: "stated", buyer_org_id: null, buyer_name: null, title: "Sweden – IT services – Payments gateway maintenance", winners: [], winner_size: null, awarded_on: day(-820), expected_end: day(270), official_url: "https://example.invalid/ted/3" }] },
  "/api/renewals/incumbents": { items: [
    { supplier: "FIXTURE Integrator Ltd", size: "large", awards: 41, countries: ["GB", "DE"], latest: day(-20) },
    { supplier: "FIXTURE Softwarehaus GmbH", size: "sme", awards: 17, countries: ["DE", "AT"], latest: day(-75) },
    { supplier: "FIXTURE Nordic Dev AB", size: null, awards: 9, countries: ["SE", "NO", "DK"], latest: day(-140) }] },
  "/api/prospects": { total: 46, note: "From your imported sheets.", items: [
    { organization: { id: ORG, name: "FIXTURE Alpenkasse AG", country: "DE", segment: "Banking", employees: 8600 }, rank: 1, priority: null, score: 86, confidence: "High (two sources)", market: { fit: "core", label: "Core market" }, vendor_flag: false,
      signal: "Announced a two-year programme to replace its card issuing platform", signal_type: "Programme", signal_date: day(-12), age_days: 12, why: "A platform replacement this size usually needs extra engineering capacity for integration and testing.", caveat: "The announcement names a vendor but not an integrator.", buyer_role: "Head of Cards Technology", buyer_name: null, service_fit: "team_extension", source_url: "https://example.invalid/press/1", verification: null },
    { organization: { id: ORG, name: "FIXTURE Fjordpay AS", country: "NO", segment: "Payments", employees: 1200 }, rank: 2, priority: null, score: 74, confidence: "Medium", market: { fit: "backup", label: "Backup market" }, vendor_flag: true,
      signal: "Hiring 12 backend engineers for instant payments", signal_type: "Hiring", signal_date: day(-40), age_days: 40, why: "Sustained hiring suggests a capacity gap.", caveat: null, buyer_role: "CTO", buyer_name: "FIXTURE Person", service_fit: "staff_augmentation", source_url: "https://example.invalid/jobs/2", verification: { status: "verified", events: 2 } },
    { organization: { id: ORG, name: "FIXTURE Thames Mutual", country: "GB", segment: "Insurance", employees: 3100 }, rank: 3, priority: null, score: null, confidence: null, market: { fit: "core" }, vendor_flag: false,
      signal: "New Chief Digital Officer appointed", signal_type: null, signal_date: null, age_days: null, why: null, caveat: null, buyer_role: null, service_fit: null, source_url: null, verification: null }] },
  "/api/deadlines": { items: [
    { id: "d1", title: "FIXTURE DORA register of information due", regime: "DORA", applies_to: "EU banks and payment institutions", deadline_date: day(38), days_left: 38, source_url: "https://example.invalid/reg/1", source_quote: "Entities shall submit the register by…", status: "approved", proposed_by: "analyst@demo.invalid" },
    { id: "d2", title: "FIXTURE PSD3 transition ends", regime: "PSD3", applies_to: null, deadline_date: day(190), days_left: 190, source_url: "https://example.invalid/reg/2", source_quote: "The transitional period shall end on…", status: "proposed", proposed_by: "analyst@demo.invalid" }] },
  "/api/approvals": { items: [
    { id: "a1", kind: "crm_activity", operation: "create_activity", status: "pending", target_system: "pipedrive", target_id: "9001", requested_by: "bdm@demo.invalid", expires_at: iso(2), payload: { subject: "Ask vendor about partner programme", type: "call", org_id: 9001 }, justification: "Vendor route unverified; this call establishes it." },
    { id: "a2", kind: "contact_reveal", operation: "reveal_email", status: "approved", target_system: "apollo", target_id: "p-77", requested_by: "bdm@demo.invalid", expires_at: iso(1), payload: { person: "FIXTURE Person", credits: 1 }, justification: "CTO named in the tender." }] },
  "/api/admin/usage": { period: "2026-11", anthropic_cap_usd_estimate: 40, anthropic_usd_per_million_tokens_blended: 0.16,
    budgets: { anthropic: { cap: 250000000, spent: 118000000, reserved: 4000000 }, exa: { cap: 50, spent: 41.2, reserved: 1.1 } },
    months: [{ period: "2026-11", provider: "anthropic", calls: 4210, units: 122000000, tokens_in: 110000000, tokens_out: 12000000, cost_usd: 17.0, errors: 12 }, { period: "2026-11", provider: "exa", calls: 820, units: 820, cost_usd: 41.2, errors: 3 }],
    daily: Array.from({ length: 22 }, (_, i) => [{ day: `2026-11-${String(i + 1).padStart(2, "0")}`, provider: "anthropic", units: 3e6 + (i % 5) * 1.4e6, calls: 190 }, { day: `2026-11-${String(i + 1).padStart(2, "0")}`, provider: "exa", units: 1 + (i % 4), calls: 38 }]).flat(),
    by_source: [{ source_id: "procurement-ted", display_name: "TED (EU tenders)", provider: "anthropic", calls: 900, units: 3e7, cost_usd: 4.1 }],
    by_plan: [{ plan_id: "p1", theme: "core_banking", geography: "DACH", query: "core banking replacement announced", searches: 120, results: 960, cost_usd: 6.0 }],
    forecast: { anthropic: { projected_month_end: 170000000, will_exceed: false }, exa: { projected_month_end: 58.3, will_exceed: true } },
    verification_outcomes: [{ status: "verified", n: 212 }, { status: "no_event", n: 380 }, { status: "failed", n: 21 }, { status: "waiting_budget", n: 7 }],
    cost_per_verified_event_usd: 0.274, prices: { anthropic_in_per_mtok: 0.1, anthropic_out_per_mtok: 0.5 }, notes: ["Exa cost is the amount Exa reports on each response."] },
  "/api/verifications": { counts: { verified: 212, no_event: 380, failed: 21, waiting_budget: 7, queued: 4 }, items: [
    { id: "v1", created_at: iso(-0.1), organization: "FIXTURE Alpenkasse AG", claimed_text: "Alpenkasse selects new card platform", url: "https://example.invalid/press/1", origin: "sheet_signal", status: "verified", events_created: 2, result: { signals: 1 } },
    { id: "v2", created_at: iso(-0.3), organization: null, claimed_text: "Nordic bank tender for API work", url: "https://example.invalid/news/9", origin: "exa_discovery", status: "no_event", events_created: 0, result: null },
    { id: "v3", created_at: iso(-0.5), organization: "FIXTURE Fjordpay AS", claimed_text: "Fjordpay hiring", url: "https://example.invalid/jobs/2", origin: "seller_miss", status: "failed", events_created: 0, result: { reason: "HTTP 503 from the site" } },
    { id: "v4", created_at: iso(-0.6), organization: "FIXTURE Thames Mutual", claimed_text: "New CDO", url: "https://example.invalid/news/4", origin: "sheet_signal", status: "waiting_budget", events_created: 0, result: null }] },
  "/api/admin/discovery": { configured: true, key_present_on_api: false, cap_usd: 50, interval_days: 7, max_searches_per_run: 16, max_new_requests_per_run: 30, budget: { spent: 41.2, reserved: 1.1 }, note: "Hits are queued for verification, never shown as findings.",
    plans: [{ plan_id: "p1", theme: "core_banking", geography: "DACH", query: "core banking replacement announced", signal_rule_id: "PRG-01", last_run_at: iso(-2), runs: 6, results_total: 960, requests_created: 41, cost_total_usd: 6.0, enabled: true, last_error: null },
      { plan_id: "p2", theme: "instant_payments", geography: "Nordics", query: "instant payments programme bank", signal_rule_id: "PRG-04", last_run_at: null, runs: 0, results_total: 0, requests_created: 0, cost_total_usd: 0, enabled: false, last_error: "rate_limited" }] },
  "/api/admin/identity-reviews": { items: [{ id: "i1", raw_company_name: "Alpenkasse", reason: "ambiguous_match", sheet_title: "Accounts DACH", row_number: 118, candidates: [{ id: ORG, display_name: "FIXTURE Alpenkasse AG", country_code: "DE" }, { id: "x2", display_name: "FIXTURE Alpenkasse Holding", country_code: "AT" }] }] },
  "/api/admin/jobs": { by_status: [{ status: "done", n: 9120 }, { status: "queued", n: 4 }, { status: "permanent_failed", n: 2 }], dead_letters: [{ id: "dl1", kind: "verify_url", error_class: "HTTPStatusError", error: "503 Service Unavailable for https://example.invalid/jobs/2", replayed_at: null }] },
  "/api/admin/costs": { budgets: [{ provider: "exa", kind: "usd", period: "2026-11", cap: 50, reserved: 1.1, spent: 41.2 }], recent_ledger: [], paid_enrichment_enabled: false, note: "Default caps are zero: no paid provider call runs unless a cap AND (for contact data) an approval exist." },
};

// Account-scoped fixtures (path suffix match)
module.exports.accountFixtures = {
  briefs: { items: [{ id: "b1", status: "done", finished_at: iso(-1), model: "claude-haiku-5-5", dropped: 1, content: { source_count: 7, note: null,
    summary: [{ statement: "A UK retail bank with about 42,000 staff.", quote: "Northbridge employs around 42,000 colleagues", source_url: "https://example.invalid/about", source_title: "About us", source_kind: "company_site" }],
    signals: [{ statement: "Replacing its core banking platform under Project Northstar.", quote: "selects Synthetic Platform Vendor to replace its core banking platform", source_url: "https://example.invalid/news/northbridge-core", source_title: "Press release", source_kind: "newsroom" }],
    hiring: [], icp_fit: { read: "Strong fit: large regulated bank in a core market with an active platform programme.", reasons: [] }, open_questions: ["Who owns integration testing?", "Is the integrator allowed to subcontract?"] } }] },
  campaigns: { ownership_checked: true, note: "", items: [{ id: "c1", status: "draft", rounds: 2, model: "claude-haiku-5-5", finished_at: iso(-1), decided_by: null, review: { breaks: [] }, facts: { F1: "Selected Synthetic Platform Vendor (press release)" },
    steps: [{ day: 0, subject: "Project Northstar and integration capacity", body: "Hi,\n\nSaw that Northbridge picked a new core platform…\n\nBest,\nFIXTURE Seller", claims_used: ["C2"], fact_refs: ["F1"] },
      { day: 4, subject: "Re: Project Northstar", body: "A short follow-up…", claims_used: [], fact_refs: ["F1"] }] }] },
  "contact-candidates": { apollo_configured: false, paid_enrichment_enabled: false, reveal_price: null, items: [{ id: "cc1", name: "FIXTURE Person", title: "Head of Payments Technology", status: "found", linkedin_url: null }] },
  endpoints: { items: [{ kind: "careers_page", provider: null, url: "https://northbridge.example/careers", last_scanned_at: iso(-2), last_status: "ok", last_detail: null, docs_seen: 14 }, { kind: "rss", provider: "newsroom", url: "https://northbridge.example/news.rss", last_scanned_at: iso(-2), last_status: "error", last_detail: "Feed returned 404", docs_seen: 0 }] },
};

// Empty-state variants
module.exports.empties = {
  "/api/today": (real) => ({ ...real, count: 0, sections: Object.fromEntries(Object.keys(real.sections).map((k) => [k, []])), hot_accounts: [], empty_state: "No live source has produced a verified change yet." }),
  "/api/leads": () => ({ items: [], total: 0, page: 1, size: 50, facets: { origin: {} }, regions: module.exports.fixtures["/api/leads"].regions, origins: module.exports.fixtures["/api/leads"].origins }),
  "/api/feed": (real) => ({ ...real, count: 0, items: [], leads: [], totals: { unverified: 14 } }),
  "/api/renewals": () => ({ items: [], months: 12, contracts_known: 0, records_since: null }),
  "/api/prospects": () => ({ items: [], total: 0 }),
  "/api/explorer": (real) => ({ ...real, count: 0, items: [], coverage_note: "Coverage is limited to configured sources." }),
  "/api/search": () => ({ query: "zzqx", total: 0, groups: [] }),
};
