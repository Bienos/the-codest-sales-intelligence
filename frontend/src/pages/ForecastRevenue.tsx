import { useLayoutEffect, useRef, useState } from "react";
import { fmtDate, post } from "../api";
import { tr } from "../locale";
import { Err } from "../components/ui";

/* Invoiced revenue by month, forecast categories and data quality (backend: ri/forecast/revenue.py and quality.py).
   Today's clients are drawn in the navy family, what sales still has to win in the purple family. */

const money = (v: number | null | undefined) => {
  if (v == null || !isFinite(v)) return "–";
  const a = Math.abs(v);
  return a >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : a >= 1e3 ? `${Math.round(v / 1e3)}k` : String(Math.round(v));
};
const pct = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v * 100)}%`);
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const mlabel = (iso: string) => { const [y, m] = iso.split("-").map(Number); return `${tr(MON[m - 1])}${m === 1 ? ` ${String(y).slice(2)}` : ""}`; };

export const LAYERS: { key: string; label: string; cls: string }[] = [
  { key: "backlog", label: "Current contracts", cls: "rv-l1" },
  { key: "renewals", label: "Renewals after a contract ends", cls: "rv-l2" },
  { key: "expansions", label: "Open roles at clients", cls: "rv-l3" },
  { key: "open_pipeline", label: "Open sales deals", cls: "rv-l4" },
  { key: "new_deals", label: "Deals not created yet", cls: "rv-l5" },
];

function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null); const [w, setW] = useState(640);
  useLayoutEffect(() => {
    const el = ref.current; if (!el) return;
    setW(el.clientWidth || 640);
    if (!("ResizeObserver" in window)) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth || 640)); ro.observe(el); return () => ro.disconnect();
  }, []);
  return [ref, w];
}
function niceStep(v: number) { const e = Math.pow(10, Math.floor(Math.log10(Math.max(v, 1)))); const f = v / e; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * e; }

function MonthChart({ r }: { r: any }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<{ x: number; y: number; i: number } | null>(null);
  const n = r.months.length as number;
  const h = 280, ml = 48, mr = 8, mt = 14, mb = 30, iw = Math.max(240, w) - ml - mr, ih = h - mt - mb;
  const top = Math.max(1, ...r.p90, ...r.total);
  const step = niceStep(top / 4), ymax = Math.ceil(top / step) * step;
  const y = (v: number) => mt + ih - (v / ymax) * ih;
  const band = iw / n, bw = Math.max(6, Math.min(34, band * 0.66));
  const ticks = Array.from({ length: Math.round(ymax / step) + 1 }, (_, k) => k * step);
  const every = band < 26 ? 2 : 1;
  const text = (i: number) => `${mlabel(r.months[i])}: ${money(r.total[i])} (${money(r.p10[i])}–${money(r.p90[i])}). ` +
    LAYERS.map((l) => `${tr(l.label)} ${money(r.layers[l.key][i])}`).join(", ");
  return (
    <div className="fc-chart" ref={ref}>
      <svg width={Math.max(240, w)} height={h} role="img" aria-label={tr("Invoiced revenue by month, by where it comes from")}>
        {ticks.map((t) => <g key={t}><line className="fc-grid" x1={ml} x2={ml + iw} y1={y(t)} y2={y(t)} /><text className="fc-t" x={ml - 6} y={y(t) + 4} textAnchor="end">{money(t)}</text></g>)}
        {r.months.map((m: string, i: number) => {
          const cx = ml + band * i + band / 2; let acc = 0;
          return <g key={m}>
            {LAYERS.map((l) => { const v = r.layers[l.key][i] || 0; const y0 = y(acc), y1 = y(acc + v); acc += v;
              return v > 0 ? <rect key={l.key} className={l.cls} x={cx - bw / 2} y={y1} width={bw} height={Math.max(0, y0 - y1 - (y0 - y1 > 2 ? 1 : 0))} /> : null; })}
            {r.p90[i] - r.p10[i] > 1 && <path className="fc-whisk" d={`M${cx},${y(r.p10[i])}V${y(r.p90[i])}M${cx - 4},${y(r.p10[i])}H${cx + 4}M${cx - 4},${y(r.p90[i])}H${cx + 4}`} />}
            {i % every === 0 && <text className="fc-t" x={cx} y={h - 10} textAnchor="middle">{mlabel(m)}</text>}
            <rect className="fc-hit" tabIndex={0} aria-label={text(i)} x={ml + band * i} y={mt} width={band} height={ih}
              onMouseMove={(e) => setTip({ x: e.clientX, y: e.clientY, i })} onMouseLeave={() => setTip(null)}
              onFocus={(e) => { const b = (e.target as SVGRectElement).getBoundingClientRect(); setTip({ x: b.left + b.width / 2, y: b.top + 30, i }); }} onBlur={() => setTip(null)} />
          </g>;
        })}
        <line className="fc-axis" x1={ml} x2={ml + iw} y1={y(0)} y2={y(0)} />
      </svg>
      {tip && <div className="fc-tip rv-tip" style={{ left: Math.min(tip.x + 12, window.innerWidth - 270), top: tip.y + 12 }}>
        <b>{mlabel(r.months[tip.i])} · {money(r.total[tip.i])}</b>
        {LAYERS.map((l) => <div key={l.key}><i className={`fc-sw ${l.cls}`} /> {tr(l.label)}: {money(r.layers[l.key][tip.i])}</div>)}
        <div className="muted-inv">{tr("8 in 10: {} to {}", money(r.p10[tip.i]), money(r.p90[tip.i]))}</div>
      </div>}
    </div>
  );
}

export function SetupFields({ canManage, onDone }: { canManage: boolean; onDone: () => void }) {
  const [busy, setBusy] = useState(false); const [out, setOut] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  if (!canManage) return null;
  const go = async () => { setBusy(true); setErr(null); try { setOut(await post("/api/forecast/pipedrive-fields")); onDone(); } catch (e) { setErr(e); } finally { setBusy(false); } };
  return <div className="rv-setup">
    <button type="button" onClick={() => void go()} disabled={busy}>{busy ? tr("Setting up") : tr("Set up the fields in Pipedrive")}</button>
    {out && <span className="small" role="status">{tr("{} created, {} already there.", out.created.length, out.found.length)}</span>}
    {!!err && <Err e={err} />}
  </div>;
}

export function Revenue({ d, onEdit }: { d: any; onEdit?: () => void }) {
  const r = d.revenue;
  if (!r) return null;
  if (r.error) return <p className="small muted">{tr("The revenue view could not be built: {}", r.error)}</p>;
  const yt = r.year_total, bl = yt.by_layer;
  const clients = bl.backlog + bl.renewals + bl.expansions;
  const a = r.assumptions;
  const top = r.concentration[0];
  return (
    <>
      <div className="fc-kv rv-kv">
        <span><b>{money(yt.mean)}</b> {tr("invoiced in {} (8 in 10: {}–{})", r.year, money(yt.p10), money(yt.p90))}</span>
        <span><b>{pct(yt.mean ? clients / yt.mean : null)}</b> {tr("from today's clients")}</span>
        <span><b>{money(r.this_month.total)}</b> {tr("this month")}</span>
        {top && <span><b>{pct(top.share)}</b> {tr("of client revenue is {}", top.org)}</span>}
      </div>
      {!r.delivery_pipeline.found && <div className="callout fc-warn"><p className="m-0">{tr("No pipeline named {} was found, so running client work is not in this view. Set its name under Assumptions.", (r.delivery_pipeline.names || []).join(", ") || "–")}</p></div>}
      <div className="fc-legend">{LAYERS.map((l) => <span key={l.key}><i className={`fc-sw ${l.cls}`} />{tr(l.label)} · {money(bl[l.key])}</span>)}
        <span><svg width="12" height="14" aria-hidden="true"><path className="fc-whisk" d="M6,1V13M2,1H10M2,13H10" /></svg>{tr("8 in 10 outcomes")}</span></div>
      <MonthChart r={r} />
      <p className="small muted">{tr("Revenue is counted in the months it is invoiced: a won contract starts {} days after signing and runs {} months unless the deal says otherwise. {}% of contracts are assumed to continue after their end. Open roles are filled {}% of the time.",
        Math.round(a.start_lag_days), Math.round(a.contract_months), Math.round(a.renewal_rate), Math.round(a.open_role_win_rate.used))} {onEdit && <button type="button" className="linklike" onClick={onEdit}>{tr("Change")}</button>}</p>

      <div className="fc-two mt-2">
        <div><h3 className="fc-h3 mt-0">{tr("Contracts ending in the next 90 days")}</h3>
          {r.renewals_due.length === 0 && r.orgs_ending.length === 0 ? <p className="small muted">{tr("None on record. Add Contract end to each client in Pipedrive to see renewals coming.")}</p> :
            <ul className="fc-aging">{r.renewals_due.map((e: any) => <li key={e.crm_id}><b>{e.org || e.title}</b><span className="small muted">{e.title} · {money(e.monthly)}/{tr("month")} · {tr("ends")} {fmtDate(e.end)}{e.end_source === "assumed" ? ` · ${tr("end date not in Pipedrive")}` : ""}</span></li>)}
              {r.orgs_ending.map((o: any) => <li key={o.org}><b>{o.org}</b><span className="small muted">{tr("Contract end")} {fmtDate(o.contract_end)}</span></li>)}</ul>}</div>
        <div><h3 className="fc-h3 mt-0">{tr("Open roles at clients")}</h3>
          {r.open_roles.length === 0 ? <p className="small muted">{tr("No open roles.")}</p> :
            <ul className="fc-aging">{r.open_roles.map((x: any) => <li key={x.crm_id}><b>{x.title}</b><span className="small muted">{x.org} · {money(x.monthly)}/{tr("month")}{x.estimated ? ` (${tr("typical, no value in Pipedrive")})` : ""} · {tr("from")} {fmtDate(x.start)} · {pct(x.chance)}</span></li>)}</ul>}</div>
      </div>

      <h3 className="fc-h3">{tr("What is invoiced now")}</h3>
      <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Running contracts")}>
        <table className="fc-table rv-table"><thead><tr><th>{tr("Contract")}</th><th>{tr("Per month")}</th><th>{tr("From")}</th><th>{tr("Until")}</th><th>{tr("People")}</th><th>{tr("Based on")}</th></tr></thead>
          <tbody>{r.engagements.slice(0, 25).map((e: any) => <tr key={e.crm_id}><td>{e.org || e.title}{e.org && <span className="small muted fc-fname"><span className="small">{e.title}</span></span>}</td><td>{money(e.monthly)}</td><td>{fmtDate(e.start)}</td>
            <td>{fmtDate(e.end)}{e.end_source === "assumed" && <span className="small muted"> · {tr("assumed")}</span>}</td><td>{e.fte ?? "–"}</td><td className="small">{tr(e.how)}</td></tr>)}</tbody></table>
      </div>
      {(r.missing_value.length > 0 || r.paused.n > 0 || r.counted_in_delivery.length > 0) && <div className="callout fc-warn mt-2">
        {r.missing_value.length > 0 && <p className="m-0">{tr("Running deals with no value, adding nothing ({}): {}.", r.missing_value.length, r.missing_value.slice(0, 8).map((m: any) => m.title).join(", "))}</p>}
        {r.paused.n > 0 && <p className="m-0">{tr("On hold and not counted ({}): {} a month.", r.paused.n, money(r.paused.monthly))}</p>}
        {r.counted_in_delivery.length > 0 && <p className="m-0">{tr("Signed sales deals of clients run from {1}, counted there only ({0}).", r.counted_in_delivery.length, r.delivery_pipeline.name || "Delivery Clients")}</p>}
      </div>}
    </>
  );
}

const FLAG: Record<string, string> = {
  commit_model_doubts: "Committed, but deals like it are usually lost from here",
  late_not_committed: "Late stage and likely, but not committed: ask for a call",
};

const cat = (c: string) => (c === "Not set" ? tr("Not set") : c);       // Pipedrive's own option labels, as sellers see them

export function Categories({ d, canManage, reload }: { d: any; canManage: boolean; reload: () => void }) {
  const c = d.categories;
  if (!c) return null;
  if (c.error) return <p className="small muted">{c.error}</p>;
  return (
    <>
      <p className="small muted mt-0">{tr("The seller's call (Commit, Best case, Pipeline) next to the model's chance from deal history. Where they disagree is what the weekly forecast call is for.")}</p>
      {!c.field_set_up && <div className="callout fc-warn"><p className="m-0">{tr("The Forecast category field is not in Pipedrive yet. It is created with the next hourly sync, or now:")}</p><SetupFields canManage={canManage} onDone={reload} /></div>}
      <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Forecast categories")}>
        <table className="fc-table"><thead><tr><th>{tr("Category")}</th><th>{tr("Deals")}</th><th>{tr("Value")}</th><th>{tr("Model: wins expected")}</th><th>{tr("Model: revenue this year")}</th></tr></thead>
          <tbody>{c.groups.map((g: any) => <tr key={g.category}><td>{cat(g.category)}</td><td>{g.n}</td><td>{money(g.value)}</td><td>{g.model_wins.toFixed(1)}</td><td>{money(g.model_expected)}</td></tr>)}</tbody></table>
      </div>
      <p className="small muted">{tr("{} of open deals have a category.", pct(c.set_share))}</p>
      <h3 className="fc-h3">{tr("Where the seller and the model disagree")}</h3>
      {c.flagged.length === 0 ? <p className="small muted">{tr("No disagreements right now.")}</p> :
        <ul className="fc-aging">{c.flagged.map((x: any) => <li key={x.crm_id}><b>{x.title}{x.org ? ` · ${x.org}` : ""}</b>
          <span className="small muted">{x.stage} · {cat(x.category)} · {tr("model {}", pct(x.win_chance))} · {money(x.value)}{x.estimated_value ? ` (${tr("average")})` : ""}</span><span className="small">{tr(FLAG[x.flag] || x.flag)}</span></li>)}</ul>}
    </>
  );
}

const CHECK: Record<string, string> = { value: "Value", lead_source: "Lead source", deal_type: "Deal type", close_date: "Expected close date", category: "Forecast category",
  start: "Planned start", qual_early: "Pain and champion", qual_late: "Decision maker and process" };
const HYG: Record<string, string> = { close_past: "Close date passed", no_next_step: "No next step", step_overdue: "Next step overdue", no_contact: "No contact logged" };

export function Quality({ d, canManage, reload }: { d: any; canManage: boolean; reload: () => void }) {
  const q = d.quality;
  const [owner, setOwner] = useState("");
  if (!q) return null;
  if (q.error) return <p className="small muted">{q.error}</p>;
  const sr = q.stage_rules;
  return (
    <>
      <p className="small muted mt-0">{tr("What each open deal still needs for the forecast to be trusted. From {}: close date and category; from {}: planned start, pain and champion; from {}: decision maker and process.", sr.sql, sr.proposal, sr.commercials)}</p>
      {q.fields_set_up.length === 0 && <div className="callout fc-warn"><p className="m-0">{tr("The forecast's fields are not in Pipedrive yet. They are created with the next hourly sync, or now:")}</p><SetupFields canManage={canManage} onDone={reload} /></div>}
      <div className="rv-checks">{q.checks.map((c: any) => <div key={c.key} className="rv-check"><span>{tr(CHECK[c.key] || c.label)}</span>
        <span className="fc-src-bar" aria-hidden="true"><i style={{ width: `${(c.share || 0) * 100}%` }} /></span><b>{pct(c.share)}</b><small className="muted">{c.filled}/{c.of}</small></div>)}</div>
      {q.hygiene && <>
        <h3 className="fc-h3">{tr("Pipeline hygiene")}</h3>
        <div className="rv-checks">{q.hygiene.counts.map((c: any) => <div key={c.key} className="rv-check"><span>{tr(HYG[c.key] || c.label)}</span>
          <span className="fc-src-bar" aria-hidden="true"><i style={{ width: `${q.hygiene.open_deals ? (c.n / q.hygiene.open_deals) * 100 : 0}%` }} /></span><b>{c.n}</b><small className="muted">{tr("of {}", q.hygiene.open_deals)}</small></div>)}</div>
        <p className="small muted">{q.hygiene.activities_synced
          ? tr("Next step: an open activity on the deal in Pipedrive. No contact: nothing marked done on the deal for {} days.", q.hygiene.no_contact_days)
          : tr("Next steps and last contact appear after the first hourly read of Pipedrive activities.")}</p>
      </>}
      {q.lost_90d.n > 0 && <p className="small">{tr("Lost in the last 90 days: {}, of them {} without a reason.", q.lost_90d.n, q.lost_90d.no_reason)}</p>}
      <h3 className="fc-h3">{tr("By owner")}</h3>
      <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Data quality by owner")}>
        <table className="fc-table"><thead><tr><th>{tr("Owner")}</th><th>{tr("Filled in")}</th><th>{tr("Open deals")}</th><th>{tr("Stuck")}</th><th>{tr("Close date passed")}</th>
          {q.hygiene?.activities_synced && <><th>{tr("No next step")}</th><th>{tr("No contact logged")}</th></>}<th>{tr("Lost without reason (90 days)")}</th></tr></thead>
          <tbody>{q.owners.map((o: any) => <tr key={String(o.owner_id)}><td>{o.name}</td><td>{pct(o.score)}</td><td>{o.deals}</td><td>{o.stale}</td><td>{o.close_past ?? 0}</td>
            {q.hygiene?.activities_synced && <><td>{o.no_next_step ?? 0}</td><td>{o.no_contact ?? 0}</td></>}<td>{o.lost_no_reason}</td></tr>)}</tbody></table>
      </div>
      <h3 className="fc-h3">{tr("Deals to fix")}</h3>
      {q.owners.length > 1 && <p className="small"><label htmlFor="fc-hyg-owner">{tr("Owner")} </label>
        <select id="fc-hyg-owner" value={owner} onChange={(e) => setOwner(e.target.value)}><option value="">{tr("Everyone")}</option>
          {q.owners.map((o: any) => <option key={String(o.owner_id)} value={String(o.owner_id)}>{o.name}</option>)}</select></p>}
      {(() => {
        const list = q.deals.filter((x: any) => !owner || String(x.owner_id) === owner);
        if (list.length === 0) return <p className="small muted">{tr("Every open deal has what it needs.")}</p>;
        return <ul className="fc-aging">{list.slice(0, 40).map((x: any) => <li key={x.crm_id}>
          <b>{q.pipedrive_url ? <a href={`${q.pipedrive_url}/deal/${x.crm_id}`} target="_blank" rel="noreferrer">{x.title}</a> : x.title}{x.org ? ` · ${x.org}` : ""}</b>
          <span className="small muted">{x.owner} · {x.stage}{x.stale_days ? ` · ${tr("{} days in stage", x.stale_days)}` : ""}{x.days_since_contact != null ? ` · ${tr("last contact {} days ago", x.days_since_contact)}` : ""}</span>
          <span className="rv-chips">{(x.hygiene || []).map((m: string) => <span key={m} className="badge warn">{m === "close_past" && x.close_date ? tr("Close date {} passed", x.close_date) : tr(HYG[m] || m)}</span>)}
            {x.missing.map((m: string) => <span key={m} className="badge">{tr(CHECK[m] || m)}</span>)}</span></li>)}</ul>;
      })()}
      <details className="mt-2"><summary>{tr("How to make Pipedrive ask for these")}</summary>
        <ol className="small">
          <li>{tr("Settings, Data fields, Deals: open each field and mark it required from the stage named above (Professional plan and up).")}</li>
          <li>{tr("Settings, Company, Lost reasons: switch off free text and add: Rate too high; Chose another vendor; No candidate in time; Candidate rejected at interview; Project paused or no budget; Doing it in-house; No response; Other.")}</li>
          <li>{tr("Price roles as products: one product per role, quantity = people, price = monthly rate, billed monthly for the contract months, from the planned start. The forecast reads them.")}</li>
          <li>{tr("Mark a deal Won when the contract is signed, and keep running work in Delivery Clients.")}</li>
        </ol></details>
    </>
  );
}
