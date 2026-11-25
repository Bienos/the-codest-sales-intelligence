import { getLanguage, number, tr } from "../locale";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, fmtDateTime } from "../api";
import { Badge, Err, humanize, Skeleton, reloadPage, Tabs } from "../components/ui";
import { Fold } from "./PipelineStatus";
import "../styles/admin.css";

/* Usage & cost: where the month's money goes, at a glance.
   - Two "month rulers" (AI reading, web search): spent so far, where the month ends at the recent pace, the even-pace mark and the limit.
   - A daily chart for the month: past days as stacked columns (AI / web search), days still to come as hatched columns at the recent pace.
   - Spend by purpose, with what each purpose produced and the cost of one useful result.
   - The per-call log and the older tables are folded away. */

const usd = (v: number | null | undefined, d = 2) => (v === null || v === undefined ? tr("Not known") : `$${Number(v).toFixed(d)}`);
const usdShort = (v: number) => (Number.isInteger(v) || v >= 100 ? `$${Math.round(v)}` : v >= 10 ? `$${v.toFixed(1)}` : `$${v.toFixed(2)}`);
const num = (v: any) => number(Number(v ?? 0));
const compact = (v: number) => (v >= 1e6 ? `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M` : v >= 1e3 ? `${Math.round(v / 1e3)}K` : String(Math.round(v)));
const locale = () => (getLanguage() === "pl" ? "pl-PL" : "en-GB");
const dayLabel = (iso: string, opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short" }) => new Date(`${iso}T12:00:00Z`).toLocaleDateString(locale(), opts);
const monthName = (period: string) => new Date(`${period}-15T12:00:00Z`).toLocaleDateString(locale(), { month: "long" });

/** Width of an element, kept up to date, so charts draw at their real size (text never stretches). */
function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null); const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current; if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    ro.observe(el); setW(Math.round(el.getBoundingClientRect().width));
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

/* ------------------------------------------------------------------ the month ruler */

type G = { provider: string; unit: string; cap: number; used: number; spent_usd: number; rate_per_day: number; rate_usd_per_day: number; forecast: number; forecast_usd: number;
  will_exceed: boolean; runs_out_on: string | null; pct: number | null; cap_usd_estimate?: number | null };

function Ruler({ g, title, what, day, days }: { g: G; title: string; what: string; day: number; days: number }) {
  const isUsd = g.unit === "usd";
  const fmt = (v: number) => (isUsd ? usd(v) : `${compact(v)} ${tr("tokens")}`);
  if (!(g.cap > 0)) {
    return <section className="ruler off" aria-label={tr(title)}><div className="ruler-head"><h3>{tr(title)}</h3></div>
      <p className="ruler-say">{tr("No monthly limit is set, so these calls are blocked.")}</p><p className="small muted m-0">{tr(what)}</p></section>;
  }
  const scale = Math.max(g.cap, g.forecast) * 1.02;
  const p = (v: number) => `${Math.min(v / scale, 1) * 100}%`;
  const pace = (g.cap * day) / days;
  const over = g.used >= g.cap;
  const kind = over ? "bad" : g.will_exceed ? "warn" : "ok";
  const money = isUsd ? g.used : g.spent_usd;
  const forecastMoney = isUsd ? g.forecast : g.forecast_usd;
  const capMoney = isUsd ? g.cap : g.cap_usd_estimate ?? null;
  const ahead = g.used > pace * 1.1 ? tr("ahead of an even pace") : g.used < pace * 0.9 ? tr("behind an even pace") : tr("on an even pace");
  return (
    <section className={`ruler ${kind}`} aria-label={tr(title)}>
      <div className="ruler-head">
        <h3>{tr(title)}</h3>
        <span className="ruler-pct">{g.pct !== null ? tr("{}% used", Math.round(g.pct)) : ""}</span>
      </div>
      <p className="ruler-fig"><b>{usd(money)}</b> <span>{capMoney !== null ? (isUsd ? tr("of {} this month", usd(capMoney, 0)) : tr("of about {} this month", usd(capMoney, 0))) : tr("this month")}</span></p>
      {!isUsd && <p className="small muted m-0">{tr("{} of {} tokens. A token is a small piece of text the AI reads or writes.", compact(g.used), compact(g.cap))}</p>}
      <div className="ruler-track" role="img" aria-label={tr("{} used of {}; at this pace the month ends at {}", fmt(g.used), fmt(g.cap), fmt(g.forecast))}>
        <i className="ruler-used" style={{ width: p(Math.min(g.used, g.cap)) }} />
        {g.forecast > g.used && <i className="ruler-ahead" style={{ left: p(Math.min(g.used, g.cap)), width: `calc(${p(Math.min(g.forecast, g.cap))} - ${p(Math.min(g.used, g.cap))})` }} />}
        {g.forecast > g.cap && <i className="ruler-over" style={{ left: p(g.cap), width: `calc(${p(g.forecast)} - ${p(g.cap)})` }} />}
        <i className="ruler-pace" style={{ left: p(pace) }} title={tr("Even pace for today")} />
        <i className="ruler-cap" style={{ left: p(g.cap) }} />
      </div>
      <div className="ruler-scale" aria-hidden="true"><span>0</span><span style={{ left: p(g.cap) }}>{tr("limit {}", isUsd ? usdShort(g.cap) : compact(g.cap))}</span></div>
      <ul className="ruler-key" aria-hidden="true">
        <li><i className="k-used" />{tr("spent")}</li><li><i className="k-ahead" />{tr("rest of the month at this pace")}</li><li><i className="k-pace" />{tr("even pace for today")}</li>
      </ul>
      <p className="ruler-say">
        {over ? tr("The limit is reached: these calls are blocked until next month or a higher limit.")
          : g.will_exceed && g.runs_out_on ? tr("At this pace the limit runs out on {}. Raise the limit or expect work to pause.", dayLabel(g.runs_out_on))
          : g.rate_per_day > 0 ? tr("At this pace the month ends at about {}, within the limit.", isUsd ? usd(forecastMoney) : `${usd(forecastMoney)} (${compact(g.forecast)} ${tr("tokens")})`)
          : tr("Nothing spent in the last 7 days.")}
      </p>
      <p className="small muted m-0">{g.rate_per_day > 0 ? tr("About {} a day over the last 7 days, {}.", isUsd ? usd(g.rate_usd_per_day) : `${usd(g.rate_usd_per_day)} (${compact(g.rate_per_day)} ${tr("tokens")})`, ahead) : tr(what)}</p>
    </section>
  );
}

/* ------------------------------------------------------------------ daily spend for the month */

type Day = { day: string; ai_usd: number; exa_usd: number; ai_tokens: number; searches: number; calls: number };

function DailyChart({ d }: { d: any }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const past: Day[] = d.daily;
  const rate = Number(d.ai.rate_usd_per_day) + Number(d.exa.rate_usd_per_day);
  const lastDay = past.length ? past[past.length - 1].day : `${d.period}-01`;
  const future = Array.from({ length: Math.max(d.days_in_month - past.length, 0) }, (_, i) => {
    const t = new Date(`${lastDay}T12:00:00Z`); t.setUTCDate(t.getUTCDate() + i + 1); return t.toISOString().slice(0, 10);
  });
  const total = (x: Day) => Number(x.ai_usd) + Number(x.exa_usd);
  const maxV = Math.max(...past.map(total), rate, 0.01);
  const step = [0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 25, 50].find((s) => maxV / s <= 4) ?? 100;
  const top = Math.ceil(maxV / step) * step;
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
  const H = 190, padL = 44, padB = 24, padT = 10;
  const n = d.days_in_month, plotW = Math.max(w - padL - 4, 50), slot = plotW / n, bw = Math.min(Math.max(slot - 2, 2), 24);
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / top);
  const x = (i: number) => padL + i * slot + (slot - bw) / 2;
  const showLabel = (i: number) => i === 0 || (i + 1) % 7 === 1 || i === n - 1;
  const tipFor = (i: number) => {
    if (i < past.length) { const p = past[i]; return { title: dayLabel(p.day, { weekday: "short", day: "numeric", month: "short" }), rows: [[tr("Total"), usd(total(p))], [tr("AI reading"), `${usd(p.ai_usd)} · ${compact(p.ai_tokens)} ${tr("tokens")}`], [tr("Web search"), `${usd(p.exa_usd)} · ${tr("{} searches", num(p.searches))}`]], future: false }; }
    return { title: dayLabel(future[i - past.length], { weekday: "short", day: "numeric", month: "short" }), rows: [[tr("Expected"), usd(rate)]], future: true };
  };
  const tip = hover !== null ? tipFor(hover) : null;
  return (
    <section className="daily" aria-labelledby="daily-h">
      <div className="daily-head">
        <h3 id="daily-h">{tr("Spend per day in {}", monthName(d.period))}</h3>
        <ul className="legend"><li><i className="sw ai" />{tr("AI reading")}</li><li><i className="sw exa" />{tr("Web search")}</li><li><i className="sw fut" />{tr("Expected at the last 7 days' pace")}</li></ul>
      </div>
      <div className="daily-plot" ref={ref} onPointerLeave={() => setHover(null)}>
        {w > 0 && <svg width={w} height={H} role="img" aria-label={tr("Spend per day this month; the table below lists every day.")}>
          {ticks.map((t) => <g key={t}><line x1={padL} x2={w} y1={y(t)} y2={y(t)} className="grid" /><text x={padL - 8} y={y(t) + 4} className="ax" textAnchor="end">{usdShort(t)}</text></g>)}
          {past.map((p, i) => {
            const a = Number(p.ai_usd), e = Number(p.exa_usd);
            const ya = y(a), ye = y(a + e);
            return <g key={p.day} className={hover === i ? "col on" : "col"}>
              {a > 0 && <rect x={x(i)} y={ya} width={bw} height={Math.max(y(0) - ya, 1)} className="m-ai" />}
              {e > 0 && <rect x={x(i)} y={ye} width={bw} height={Math.max(ya - ye - (a > 0 ? 2 : 0), 1)} className="m-exa" />}
            </g>;
          })}
          {future.map((f, j) => { const i = past.length + j; return rate > 0 ? <rect key={f} x={x(i)} y={y(rate)} width={bw} height={y(0) - y(rate)} className={`m-fut${hover === i ? " on" : ""}`} /> : null; })}
          {past.length > 0 && <line x1={x(past.length - 1) + bw + (slot - bw) / 2} x2={x(past.length - 1) + bw + (slot - bw) / 2} y1={padT} y2={y(0)} className="today" />}
          {Array.from({ length: n }, (_, i) => showLabel(i) && <text key={i} x={x(i) + bw / 2} y={H - 6} className="ax" textAnchor="middle">{i + 1}</text>)}
          <line x1={padL} x2={w} y1={y(0)} y2={y(0)} className="base" />
          {Array.from({ length: n }, (_, i) => <rect key={`h${i}`} x={padL + i * slot} y={padT} width={slot} height={H - padT - padB} className="hit" tabIndex={0}
            aria-label={`${tipFor(i).title}: ${tipFor(i).rows.map((r) => r.join(" ")).join(", ")}`} onPointerEnter={() => setHover(i)} onFocus={() => setHover(i)} onBlur={() => setHover(null)} />)}
        </svg>}
        {tip && hover !== null && <div className={`chart-tip${tip.future ? " fut" : ""}`} style={{ left: Math.min(Math.max(x(hover) + bw / 2, 90), Math.max(w - 90, 90)) }} role="status">
          <b>{tip.title}</b>{tip.rows.map(([k, v]) => <span key={k}><em>{v}</em>{k}</span>)}</div>}
      </div>
      <details className="daily-table"><summary>{tr("Show the days as a table")}</summary>
        <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Spend per day")}><table><thead><tr><th>{tr("Day")}</th><th>{tr("AI reading")}</th><th>{tr("Web search")}</th><th>{tr("Total")}</th><th>{tr("Calls")}</th></tr></thead>
          <tbody>{[...past].reverse().map((p) => <tr key={p.day}><td>{dayLabel(p.day, { weekday: "short", day: "numeric", month: "short" })}</td><td>{usd(p.ai_usd)} <span className="small muted">{compact(p.ai_tokens)}</span></td><td>{usd(p.exa_usd)} <span className="small muted">{tr("{} searches", num(p.searches))}</span></td><td><b>{usd(total(p))}</b></td><td>{num(p.calls)}</td></tr>)}</tbody></table></div>
      </details>
    </section>
  );
}

/* ------------------------------------------------------------------ spend by purpose */

const PURPOSE_NOTE: Record<string, string> = {
  reading: "The AI reads new pages from the sources and turns them into findings.", web_search: "Saved web searches that look for new pages to check.",
  tenders: "Plain-English and Polish summaries of open tenders.", company: "Company research and briefs a seller asked for.", person: "Profiles of one person, asked for by a seller.",
  find_people: "Searches for decision makers at a company.", fit: "Why a finding fits The Codest, written for findings that had no reason yet.",
  emails: "Draft email sequences.", matching: "Matching company names from your workbooks to accounts.", other: "Calls that do not belong to one of the purposes above.",
};
/** What one useful result is called: "for a finding", "1 finding", "24 findings". */
const USEFUL: Record<string, [string, string, string]> = {
  findings: ["a finding", "{} finding", "{} findings"], "pages found": ["a page found", "{} page found", "{} pages found"], summaries: ["a summary", "{} summary", "{} summaries"],
  profiles: ["a profile", "{} profile", "{} profiles"], "people found": ["a person found", "{} person found", "{} people found"],
  "findings explained": ["a finding explained", "{} finding explained", "{} findings explained"], drafts: ["a draft", "{} draft", "{} drafts"],
  "companies matched": ["a company matched", "{} company matched", "{} companies matched"],
};

function Purposes({ items, total }: { items: any[]; total: number }) {
  const max = Math.max(...items.map((p) => Number(p.usd)), 0.0001);
  if (!items.length) return <p className="quiet">{tr("Nothing was spent this month yet.")}</p>;
  return (
    <section className="purposes" aria-labelledby="purposes-h">
      <div className="daily-head"><h3 id="purposes-h">{tr("What the money was for")}</h3>
        <ul className="legend"><li><i className="sw ai" />{tr("AI reading")}</li><li><i className="sw exa" />{tr("Web search")}</li></ul></div>
      <ol className="pp-list">
        {items.map((p) => {
          const u = USEFUL[p.useful_label] as [string, string, string] | undefined;
          const share = total > 0 ? Math.round((Number(p.usd) / total) * 100) : 0;
          return (
            <li key={p.key} className="pp">
              <div className="pp-name"><b>{tr(p.label)}</b><span className="small muted">{tr(PURPOSE_NOTE[p.key] ?? PURPOSE_NOTE.other)}</span></div>
              <div className="pp-bar" role="img" aria-label={tr("{}: AI {}, web search {}", tr(p.label), usd(p.ai_usd), usd(p.exa_usd))}>
                <span className="pp-track" style={{ width: `${(Number(p.usd) / max) * 100}%` }}>
                  {Number(p.ai_usd) > 0 && <i className="m-ai" style={{ flexGrow: Number(p.ai_usd) }} />}
                  {Number(p.exa_usd) > 0 && <i className="m-exa" style={{ flexGrow: Number(p.exa_usd) }} />}
                </span>
                <span className="pp-val"><b>{usd(p.usd)}</b> <span className="muted">{share > 0 ? `${share}%` : ""}</span></span>
              </div>
              <div className="pp-per">
                {p.cost_per_useful !== null && u ? <><b>{usd(p.cost_per_useful, p.cost_per_useful < 0.1 ? 3 : 2)}</b><span className="small muted">{" " + tr("for {}", tr(u[0]))}</span><span className="small muted pp-n">{tr(p.useful === 1 ? u[1] : u[2], num(p.useful))}</span></>
                  : u && p.useful === 0 ? <span className="small muted">{tr("Nothing produced yet this month")}</span>
                  : <span className="small muted">{tr("{} calls", num(p.calls))}{p.failed ? ` · ${tr("{} failed", num(p.failed))}` : ""}</span>}
              </div>
            </li>);
        })}
      </ol>
    </section>
  );
}

/* ------------------------------------------------------------------ folded detail: every call, and the older tables */

function Ledger() {
  const [f, setF] = useState({ provider: "", status: "" });
  const [d, setD] = useState<any>(null); const [off, setOff] = useState(0); const [err, setErr] = useState<unknown>(null);
  useEffect(() => { api(`/api/admin/usage/ledger?provider=${f.provider}&status=${f.status}&limit=50&offset=${off}`).then(setD).catch(setErr); }, [f, off]);
  return (
    <>
      <div className="row mb-3">
        <select aria-label={tr("Service")} value={f.provider} onChange={(e) => { setOff(0); setF({ ...f, provider: e.target.value }); }}><option value="">{tr("All services")}</option><option value="anthropic">{tr("AI reading")}</option><option value="exa">{tr("Web search")}</option></select>
        <select aria-label={tr("Status")} value={f.status} onChange={(e) => { setOff(0); setF({ ...f, status: e.target.value }); }}><option value="">{tr("Any status")}</option><option value="ok">{tr("Worked")}</option></select>
        {d && <span className="small muted">{num(d.total)}{" " + tr("calls")}</span>}
      </div>
      <Err e={err} />
      {d && d.items.length === 0 && <p className="quiet">{tr("No paid calls match.")}</p>}
      {d && d.items.length > 0 && <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Every paid call")}><table><thead><tr><th>{tr("When")}</th><th>{tr("Call")}</th><th>{tr("Used")}</th><th>{tr("Cost")}</th><th>{tr("What it was for")}</th></tr></thead><tbody>
        {d.items.map((u: any) => <tr key={u.id}><td>{fmtDateTime(u.at)}</td>
          <td><b>{u.provider === "exa" ? tr("Web search") : tr("AI reading")}</b> {humanize(u.operation)}{u.status !== "ok" && <> <Badge kind="bad">{u.status}</Badge></>}</td>
          <td>{num(u.units)} {tr(u.unit)}{u.tokens_in ? <div className="small muted">{num(u.tokens_in)}{" " + tr("in ·") + " "}{num(u.tokens_out)}{" " + tr("out")}</div> : null}</td>
          <td>{u.cost_usd === null ? <span className="muted">{tr("unknown")}</span> : usd(u.cost_usd, 4)}</td>
          <td className="small">{u.query ?? u.document_title ?? u.source_id ?? ""}{u.organization && <div className="muted">{u.organization}</div>}{u.document_url && <a href={u.document_url} target="_blank" rel="noreferrer noopener" className="small">{tr("source ↗")}</a>}</td></tr>)}
      </tbody></table></div>}
      {d && d.total > 50 && <div className="row mt-3"><button disabled={off === 0} onClick={() => setOff(Math.max(0, off - 50))}>{tr("Newer")}</button><button disabled={!d || off + 50 >= d.total} onClick={() => setOff(off + 50)}>{tr("Older")}</button></div>}
    </>
  );
}

function Tables() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [tab, setTab] = useState("month");
  useEffect(() => { api("/api/admin/usage").then(setD).catch(setErr); }, []);
  if (err) return <Err e={err} onRetry={reloadPage} />;
  if (!d) return <Skeleton rows={2} />;
  return (
    <Tabs items={[["month", "By month"], ["source", "By source"], ["plan", "By saved search"]].map(([k, l]) => ({ key: k, label: l }))} value={tab} onChange={setTab} label="Usage views">
      {tab === "month" && (d.months.length === 0 ? <p className="quiet">{tr("No paid calls yet.")}</p> : <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Paid calls by month")}><table><thead><tr><th>{tr("Month")}</th><th>{tr("Service")}</th><th>{tr("Calls")}</th><th>{tr("Used")}</th><th>{tr("Cost")}</th><th>{tr("Failed")}</th></tr></thead><tbody>
        {d.months.map((x: any, i: number) => <tr key={i}><td>{x.period}</td><td>{x.provider === "exa" ? tr("Web search") : tr("AI reading")}</td><td>{num(x.calls)}</td><td>{x.provider === "anthropic" ? tr("{} tokens ({} read, {} written)", num(x.units), num(x.tokens_in), num(x.tokens_out)) : tr("{} searches", num(x.units))}</td><td>{x.cost_usd === null ? <span className="muted">{tr("unknown")}</span> : usd(x.cost_usd)}</td><td>{x.errors}</td></tr>)}
      </tbody></table></div>)}
      {tab === "source" && (d.by_source.length === 0 ? <p className="quiet">{tr("Nothing recorded this month.")}</p> : <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Paid calls by source")}><table><thead><tr><th>{tr("Source")}</th><th>{tr("Service")}</th><th>{tr("Calls")}</th><th>{tr("Used")}</th><th>{tr("Cost")}</th></tr></thead><tbody>
        {d.by_source.map((x: any, i: number) => <tr key={i}><td>{x.display_name ?? (x.source_id === "(unattributed)" ? tr("Not tied to a source") : x.source_id)}</td><td>{x.provider === "exa" ? tr("Web search") : tr("AI reading")}</td><td>{num(x.calls)}</td><td>{num(x.units)}</td><td>{x.cost_usd === null ? tr("unknown") : usd(x.cost_usd)}</td></tr>)}
      </tbody></table></div>)}
      {tab === "plan" && (d.by_plan.length === 0 ? <p className="quiet">{tr("No web searches yet.")}</p> : <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Web searches by saved search")}><table><thead><tr><th>{tr("Saved search")}</th><th>{tr("Searches")}</th><th>{tr("Results")}</th><th>{tr("Cost")}</th></tr></thead><tbody>
        {d.by_plan.map((x: any) => <tr key={x.plan_id}><td><b>{humanize(x.theme)}</b> · {x.geography}<div className="small muted">{x.query}</div></td><td>{x.searches}</td><td>{x.results}</td><td>{usd(x.cost_usd)}</td></tr>)}
      </tbody></table></div>)}
    </Tabs>
  );
}

/* ------------------------------------------------------------------ the page */

export default function Usage() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  useEffect(() => { api("/api/admin/usage/insights").then(setD).catch(setErr); }, []);
  if (err) return <Err e={err} onRetry={reloadPage} />;
  if (!d) return <Skeleton />;
  const prev = d.previous?.usd > 0 ? tr("{} last month", usd(d.previous.usd)) : null;
  const warn = [d.ai.will_exceed && tr("AI reading"), d.exa.will_exceed && tr("Web search")].filter(Boolean);
  return (
    <div className="usage">
      <header className="usage-hero">
        <div>
          <p className="usage-k">{tr("Spent in {} so far", monthName(d.period))}</p>
          <p className="usage-big">{usd(d.total_usd)}</p>
        </div>
        <dl className="usage-facts">
          <div><dt>{tr("Expected for the whole month")}</dt><dd>{usd(d.forecast_usd)}</dd></div>
          <div><dt>{tr("Days left")}</dt><dd>{tr("{} of {}", Math.max(Math.ceil(d.days_left), 0), d.days_in_month)}</dd></div>
          {prev && <div><dt>{tr("For comparison")}</dt><dd>{prev}</dd></div>}
        </dl>
      </header>
      {warn.length > 0 && <div className="callout mb-4" role="status">{tr("On pace to go over the monthly limit: {}.", warn.join(", "))}</div>}
      <div className="rulers">
        <Ruler g={d.ai} title="AI reading" what="Claude reads pages, writes summaries and checks fit. Costs are tokens at list price." day={d.day} days={d.days_in_month} />
        <Ruler g={d.exa} title="Web search" what="Exa finds pages and people. Costs are what Exa reports on each answer." day={d.day} days={d.days_in_month} />
      </div>
      <DailyChart d={d} />
      <Purposes items={d.purposes} total={Number(d.total_usd)} />
      <p className="small muted">{d.notes.map((n: string) => tr(n)).join(" ")}</p>
      <div className="folds">
        <Fold title="Every paid call" desc="One line per call: what it was for, what it used and what it cost."><Ledger /></Fold>
        <Fold title="By month, source and saved search" desc="The same spend added up in other ways."><Tables /></Fold>
      </div>
    </div>
  );
}
