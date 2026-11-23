// Design-review capture: screenshots + automated checks for every screen. Local dev servers only (vite :5173 -> api :8000).
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const { fixtures, accountFixtures, empties } = require("./fixtures");
const AXE = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const BASE = "http://127.0.0.1:5173";
const TOKEN = fs.readFileSync("/tmp/claude-0/tok_admin", "utf8").trim();
const ORG = process.env.ORG_ID, OPP = process.env.OPP_ID;
const OUT = process.env.OUT || "/tmp/claude-0/shots";
fs.mkdirSync(OUT, { recursive: true });

const tab = (name) => async (p) => { await p.getByRole("tab", { name, exact: true }).first().click(); await p.waitForTimeout(700); };
const S = [
  ["today", "/today", null, "/api/today"],
  ["guide", "/guide"],
  ["feed", "/feed", null, "/api/feed"],
  ["feed-market", "/feed", tab(/Main markets/)],
  ["renewals", "/renewals", null, "/api/renewals"],
  ["renewals-winners", "/renewals", tab("Who wins this work")],
  ["search-start", "/search"],
  ["search-results", "/search?q=bank", null, "/api/search"],
  ["library-signals", "/library", null, "/api/library/signals"],
  ["library-signal-open", "/library", async (p) => { await p.locator("details.sig summary").first().click(); await p.waitForTimeout(300); }],
  ["library-sources", "/library?tab=sources"],
  ["library-urls", "/library?tab=urls"],
  ["knowledge", "/knowledge"],
  ["leads", "/accounts", null, "/api/leads"],
  ["leads-add", "/accounts", async (p) => { await p.getByRole("button", { name: "Add a company" }).click(); await p.waitForTimeout(300); }],
  ["account-overview", `/accounts/${ORG}`, null, `/api/accounts/${ORG}`],
  ...["Research brief", "Outreach drafts", "Initiatives", "Signals & evidence", "Suppliers", "Buyers", "Access & CRM", "Research & coverage"].map((t) => [`account-${t.toLowerCase().replace(/[^a-z]+/g, "-")}`, `/accounts/${ORG}`, tab(t)]),
  ["opportunity", `/opportunities/${OPP}`, null, `/api/hypotheses/${OPP}`],
  ["opportunity-trace-open", `/opportunities/${OPP}`, async (p) => { await p.locator("details.card summary").first().click(); await p.waitForTimeout(300); }],
  ["explorer", "/explorer", null, "/api/explorer"],
  ["explorer-companies", "/explorer", async (p) => { await p.getByLabel("Entity").selectOption("companies"); await p.waitForTimeout(900); }],
  ["admin-sources", "/admin"],
  ...["pipeline", "connections", "integrations", "imports", "users", "knowledge", "identity", "approvals", "jobs", "verification", "discovery", "usage", "rules", "precision", "coverage", "deadlines", "quality", "audit", "costs"].map((k) => [`admin-${k}`, `/admin/${k}`, null, k === "pipeline" ? "/api/admin/pipeline" : k === "usage" ? "/api/admin/usage" : undefined]),
  ...["By source", "By search plan", "Every call"].map((t) => [`admin-usage-${t.toLowerCase().replace(/[^a-z]+/g, "-")}`, "/admin/usage", tab(t)]),
];
const ONLY = process.env.ONLY ? new RegExp(process.env.ONLY) : null;

async function setupRoutes(page, state, mainApi) {
  await page.route("**/api/**", async (route) => {
    const req = route.request(); const u = new URL(req.url()); const p = u.pathname;
    if (req.method() !== "GET") return route.continue();
    if (mainApi && p === mainApi && state === "loading") return; // never answer: the screen stays in its loading state
    if (mainApi && p === mainApi && state === "error") return route.fulfill({ status: 503, contentType: "text/plain", body: "Service Unavailable" });
    const acct = p.match(/^\/api\/accounts\/[^/]+\/(briefs|campaigns|contact-candidates|endpoints)$/);
    if (state === "empty" && mainApi && p === mainApi && empties[p]) {
      const real = await (await route.fetch()).json().catch(() => ({}));
      return route.fulfill({ json: empties[p](real) });
    }
    if (acct) return route.fulfill({ json: accountFixtures[acct[1]] });
    if (p === "/api/today") {
      const r = await route.fetch(); const j = await r.json();
      j.hot_accounts = [{ id: ORG, display_name: "SYNTHETIC Northbridge Bank", country_code: "GB", segment: "Banking", score: 82, band: "hot", signal_count: 4, groups: ["PRG", "CAP", "SRC"], last_signal_at: new Date().toISOString() }];
      return route.fulfill({ json: j });
    }
    if (p === "/api/deadlines" && u.searchParams.get("all") !== "true") return route.fulfill({ json: { items: fixtures["/api/deadlines"].items.filter((x) => x.status === "approved") } });
    if (fixtures[p]) return route.fulfill({ json: fixtures[p] });
    return route.continue();
  });
}

async function checks(page, width) {
  await page.addScriptTag({ content: AXE });
  return page.evaluate(async (width) => {
    const r = await window.axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "best-practice"], resultTypes: ["violations"] });
    const vis = (el) => { const b = el.getBoundingClientRect(); const s = getComputedStyle(el); return b.width > 0 && b.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
    const small = width < 500 ? [...document.querySelectorAll("button, select, input:not([type=hidden]), [role=tab], a.pill, .chip, nav a")].filter(vis)
      .map((el) => { const b = el.getBoundingClientRect(); return { t: (el.innerText || el.getAttribute("aria-label") || el.tagName).trim().slice(0, 40), h: Math.round(b.height), w: Math.round(b.width) }; })
      .filter((x) => x.h < 44 || x.w < 44) : [];
    const text = document.querySelector("main")?.innerText ?? document.body.innerText;
    const snake = [...new Set((text.match(/\b[a-z]+(?:_[a-z0-9]+)+\b/g) || []))].slice(0, 25);
    const codes = [...new Set((text.match(/\b(?:[A-Z]{3}-\d{2}|G[1-7])\b/g) || []))].slice(0, 25);
    const overflow = document.documentElement.scrollWidth - window.innerWidth;
    const cls = (window.__cls || 0);
    return { overflow, small: small.slice(0, 30), smallCount: small.length, snake, codes, cls: Math.round(cls * 1000) / 1000,
      axe: r.violations.map((v) => ({ id: v.id, impact: v.impact, n: v.nodes.length, help: v.help, sample: v.nodes.slice(0, 3).map((n) => n.target.join(" ") + (n.any?.[0]?.message ? " :: " + n.any[0].message.slice(0, 140) : "")) })) };
  }, width);
}

(async () => {
  const browser = await chromium.launch();
  const results = {};
  const widths = (process.env.WIDTHS || "1440,390").split(",").map(Number);
  const schemes = (process.env.SCHEMES || "light,dark").split(",");
  const states = (process.env.STATES || "full").split(",");
  for (const [name, url, act, mainApi] of S) {
    if (ONLY && !ONLY.test(name)) continue;
    for (const state of states) {
      if (state !== "full" && !mainApi) continue;
      for (const w of widths) for (const scheme of schemes) {
        if (state !== "full" && scheme === "dark") continue;
        const ctx = await browser.newContext({ viewport: { width: w, height: w < 500 ? 844 : 900 }, deviceScaleFactor: Number(process.env.DSF || 1), colorScheme: scheme, isMobile: w < 500, hasTouch: w < 500 });
        await ctx.addInitScript((t) => { try { sessionStorage.setItem("ri_token", t); } catch {} window.__cls = 0;
          try { new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; }).observe({ type: "layout-shift", buffered: true }); } catch {} }, TOKEN);
        const page = await ctx.newPage();
        const errs = []; page.on("pageerror", (e) => errs.push(String(e)));
        await setupRoutes(page, state, mainApi);
        await page.goto(BASE + url, { waitUntil: "domcontentloaded" });
        await page.waitForTimeout(state === "loading" ? 1500 : 2200);
        if (act && state === "full") { try { await act(page); } catch (e) { errs.push("action failed: " + e.message); } }
        const key = `${name}__${w}__${scheme}${state === "full" ? "" : "__" + state}`;
        await page.screenshot({ path: path.join(OUT, key + ".png"), fullPage: true });
        if (scheme === "light") { try { results[key] = { ...(await checks(page, w)), errs }; } catch (e) { results[key] = { checkError: String(e), errs }; } }
        await ctx.close();
        process.stdout.write(key + "\n");
      }
    }
  }
  fs.writeFileSync(path.join(OUT, `results-${process.env.TAG || "run"}.json`), JSON.stringify(results, null, 1));
  await browser.close();
})();
