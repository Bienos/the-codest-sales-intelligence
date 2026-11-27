import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, fmtDate, put } from "../api";
import { tr } from "../locale";
import { Badge, Err, PageHeader, Skeleton } from "../components/ui";
import "../styles/forecast.css";

// Revenue forecast (backend: ri/forecast). Every number on the page comes from GET /api/forecast; managers change the assumptions here.

const I = (d: JSX.Element) => () => <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">{d}</svg>;
export const IconForecast = I(<><path d="M3 20h18" /><path d="M5 16l4-5 4 3 6-8" /><path d="M15 6h4v4" /></>);

const money = (v: number | null | undefined) => {
  if (v == null || !isFinite(v)) return "–";
  const a = Math.abs(v);
  return a >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : a >= 1e3 ? `${Math.round(v / 1e3)}k` : String(Math.round(v));
};
const pct = (v: number | null | undefined, d = 0) => (v == null ? "–" : `${v.toFixed(d)}%`);
const signed = (x: number) => { const r = Math.round(x * 100); return `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r)}%`; };

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

function niceStep(v: number) { const e = Math.pow(10, Math.floor(Math.log10(v))); const f = v / e; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * e; }

function QuarterChart({ d }: { d: any }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const b = d.result.buckets as any[]; const labels = d.buckets.map((x: any) => x.label);
  const showBefore = b[0].mean > 0.5;
  const idx = showBefore ? [0, 1, 2, 3, 4] : [1, 2, 3, 4];
  const h = 300, ml = 52, mr = 12, mt = 20, mb = 34, iw = Math.max(200, w) - ml - mr, ih = h - mt - mb;
  const pace = d.target > 0 ? d.target / 4 : 0;
  const top = Math.max(1, pace * 1.05, ...idx.map((i) => Math.max(b[i].p90, b[i].mean)));
  const step = niceStep(top / 4), max = Math.ceil(top / step) * step;
  const y = (v: number) => mt + ih - (v / max) * ih;
  const band = iw / idx.length, cw = 24;
  const short = band < 80;                       // phone width: "Q1" instead of "Q1 2027"
  const xLabel = (i: number) => (short ? (i === 0 ? tr("Before") : `Q${i}`) : labels[i]);
  const ticks: number[] = []; for (let v = 0; v <= max + 1e-6; v += step) ticks.push(v);
  const tr4 = (x: number, y0: number, wd: number, hh: number, r = 4) => { r = Math.min(r, hh); return `M${x},${y0 + hh}V${y0 + r}Q${x},${y0} ${x + r},${y0}H${x + wd - r}Q${x + wd},${y0} ${x + wd},${y0 + r}V${y0 + hh}Z`; };
  return (
    <div className="fc-chart" ref={ref}>
      <svg width={Math.max(200, w)} height={h} role="img" aria-label={tr("Closed-won revenue by quarter, with the range 8 in 10 simulated years fall in")}>
        {ticks.map((v) => <g key={v}><line className={v === 0 ? "fc-axis" : "fc-grid"} x1={ml} x2={ml + iw} y1={y(v)} y2={y(v)} /><text className="fc-t" x={ml - 8} y={y(v) + 4} textAnchor="end">{money(v)}</text></g>)}
        {pace > 0 && <line className="fc-pace" x1={ml} x2={ml + iw} y1={y(pace)} y2={y(pace)} />}
        {idx.map((i, k) => {
          const q = b[i], cx = ml + band * k + band / 2, x = cx - cw / 2, hO = (q.open / max) * ih, hN = (q.new / max) * ih, yO = mt + ih - hO, gap = hN > 3 && hO > 3 ? 2 : 0, wx = cx + 22;
          const text = `${labels[i]}${i === 0 ? ` (${tr("not in the year total")})` : ""}: ${tr("average")} ${money(q.mean)} ${d.currency}. ${tr("Open pipeline")} ${money(q.open)}, ${tr("new deals")} ${money(q.new)}. P10 ${money(q.p10)}, ${tr("median")} ${money(q.p50)}, P90 ${money(q.p90)}.`;
          return (
            <g key={i}>
              {hN > 0.5 && <path className="fc-new" d={tr4(x, yO - hN, cw, hN - gap)} />}
              {hO > 0.5 && <path className="fc-open" d={hN > 0.5 ? `M${x},${mt + ih}V${yO}H${x + cw}V${mt + ih}Z` : tr4(x, yO, cw, hO)} />}
              <path className="fc-whisk" d={`M${wx},${y(q.p10)}V${y(q.p90)}M${wx - 4},${y(q.p10)}H${wx + 4}M${wx - 4},${y(q.p90)}H${wx + 4}`} />
              <text className="fc-tv" x={cx - 6} y={y(q.mean) - 7} textAnchor="middle">{money(q.mean)}</text>
              <text className="fc-t" x={cx} y={h - 10} textAnchor="middle">{xLabel(i)}</text>
              <rect className="fc-hit" tabIndex={0} aria-label={text} x={ml + band * k} y={mt} width={band} height={ih}
                onPointerMove={(e) => setTip({ x: e.clientX, y: e.clientY, text })} onPointerLeave={() => setTip(null)}
                onFocus={(e) => { const r = (e.target as SVGRectElement).getBoundingClientRect(); setTip({ x: r.left + r.width / 2, y: r.top, text }); }} onBlur={() => setTip(null)} />
            </g>
          );
        })}
      </svg>
      {tip && <div className="fc-tip" role="tooltip" style={{ left: Math.min(tip.x + 12, window.innerWidth - 280), top: Math.max(8, tip.y - 70) }}>{tip.text}</div>}
    </div>
  );
}

function Tiles({ d }: { d: any }) {
  const p = d.pipeline;
  const tiles: [string, string, string][] = [
    [tr("Expected deals won"), d.result.wins.toFixed(1), tr("Average of the simulated years")],
    [tr("Open pipeline"), `${money(p.open_value)} ${d.currency}`, `${p.deals} ${tr("open deals")}${p.without_value ? ` · ${tr("{} valued at the average", p.without_value)}` : ""}${p.coverage != null ? ` · ${p.coverage.toFixed(1)}× ${tr("target")}` : ""}`],
    [tr("Weighted open pipeline"), `${money(p.weighted)} ${d.currency}`, p.weighted_share_of_target != null ? tr("{} of target; the rest must come from new deals", pct(p.weighted_share_of_target * 100)) : tr("Each deal times its chance of winning")],
    [tr("Sales cycle"), `${Math.round(d.flow.cycle_days)} ${tr("days")}`, tr("Sum of stage medians, first stage to won")],
    [tr("New deals per month"), (d.flow.outbound_deals_per_month + d.flow.inbound_deals_per_month).toFixed(1), tr("{} from outbound, {} inbound", d.flow.outbound_deals_per_month.toFixed(1), d.flow.inbound_deals_per_month.toFixed(1))],
    [tr("Won so far in {}", d.year), `${money(d.won_so_far.value)} ${d.currency}`, `${d.won_so_far.n} ${tr("deals marked won in Pipedrive")}`],
  ];
  return <div className="fc-kpis">{tiles.map(([l, v, n]) => <div className="fc-tile" key={l}><div className="fc-label">{l}</div><div className="fc-val">{v}</div><div className="fc-note">{n}</div></div>)}</div>;
}

function Funnel({ d }: { d: any }) {
  const f = d.flow.funnel as any[]; const top = Math.max(1e-9, ...f.map((x) => x.entering));
  const st = d.inputs.stages as any[];
  const wonN = d.flow.won_per_year;
  return (
    <div className="fc-funnel">
      {f.map((x, i) => {
        const s = st[i], m = s.measured;
        return (
          <div className="fc-frow" key={x.stage_id}>
            <div className="fc-fname">{x.name}{x.new_here > 0 && <span className="small muted">{tr("{} new a year start here", x.new_here.toFixed(0))}</span>}</div>
            <div className="fc-fbar"><i style={{ width: `${(x.entering / top) * 100}%` }} /></div>
            <div className="fc-fnum">{x.entering.toFixed(1)}</div>
            <div className="fc-fconv"><b>{pct(s.used.conv)}</b> {tr("move on")}<div className="small muted">{m.conv == null ? tr("no decided deals yet · prior {}", pct(s.prior.conv)) : tr("measured {} of {} · prior {}", pct(m.conv), m.decided, pct(s.prior.conv))}</div></div>
          </div>
        );
      })}
      <div className="fc-frow"><div className="fc-fname"><b>{tr("Won")}</b></div><div className="fc-fbar"><i style={{ width: `${(wonN / top) * 100}%` }} /></div><div className="fc-fnum">{wonN.toFixed(1)}</div><div /></div>
    </div>
  );
}

function Sensitivity({ d }: { d: any }) {
  const rows = d.sensitivity as any[]; const mx = Math.max(0.01, ...rows.map((r) => Math.max(Math.abs(r.lower), Math.abs(r.higher))));
  if (!rows.length) return <p className="small muted">{tr("Nothing to compare yet: the simulated year is empty.")}</p>;
  return (
    <div>
      {rows.map((r) => {
        const neg = Math.min(r.lower, r.higher), pos = Math.max(r.lower, r.higher);
        const label = tr("{}: 20% lower gives {}, 20% higher gives {}", tr(r.label), signed(r.lower), signed(r.higher));
        return (
          <div className="fc-trow" key={r.key}>
            <div className="fc-tl">{tr(r.label)}</div>
            <div className="fc-ttrack" tabIndex={0} aria-label={label} title={label}>
              <div className="fc-tneg"><b>{signed(neg)}</b><i style={{ width: `${(Math.abs(neg) / mx) * 62}%` }} /></div>
              <div className="fc-taxis" />
              <div className="fc-tpos"><i style={{ width: `${(Math.abs(pos) / mx) * 62}%` }} /><b>{signed(pos)}</b></div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Outbound({ d }: { d: any }) {
  const o = d.outbound; const i = d.inputs;
  const rate = (n: number, of: number) => (of ? pct((n / of) * 100, 1) : "–");
  return (
    <>
      <p className="small muted mt-0">{tr("From lemlist activity in the last {} days. A lead counts once, from the first email they were sent. Opens are left out because privacy proxies inflate them.", o.window_days)}</p>
      <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Outbound by campaign")}>
        <table className="fc-table"><thead><tr><th>{tr("Campaign")}</th><th>{tr("Leads")}</th><th>{tr("Emails")}</th><th>{tr("Replied")}</th><th>{tr("Interested")}</th><th>{tr("Bounced")}</th></tr></thead>
          <tbody>{o.campaigns.map((c: any) => <tr key={c.name}><td>{c.name}</td><td>{c.leads}</td><td>{c.sends}</td><td>{c.replied} <span className="muted">({rate(c.replied, c.leads)})</span></td><td>{c.interested}</td>
            <td>{c.bounced}{c.sends > 0 && c.bounced / c.sends >= 0.08 && <> <Badge kind="warn">{tr("Check list quality")}</Badge></>}</td></tr>)}
            {o.campaigns.length === 0 && <tr><td colSpan={6} className="muted">{tr("No lemlist activity mirrored yet.")}</td></tr>}</tbody>
          {o.campaigns.length > 0 && <tfoot><tr><td>{tr("All campaigns")}</td><td>{o.contacted}</td><td>{o.sends}</td><td>{o.replied} <span className="muted">({rate(o.replied, o.contacted)})</span></td><td>{o.interested}</td><td>{o.bounced}</td></tr></tfoot>}
        </table>
      </div>
      <dl className="fc-dl">
        <div><dt>{tr("Leads per month")}</dt><dd>{Math.round(i.leads_per_month.used)}{i.leads_per_month.setting == null ? ` (${tr("measured")})` : ` (${tr("set by hand")})`}</dd></div>
        <div><dt>{tr("Reply rate used")}</dt><dd>{pct(i.reply_rate.used, 1)} <span className="muted">{i.reply_rate.measured == null ? tr("prior only") : tr("measured {} of {} leads", pct(i.reply_rate.measured, 1), i.reply_rate.n)}</span></dd></div>
        <div><dt>{tr("Positive share used")}</dt><dd>{pct(i.positive_share.used)} <span className="muted">{i.positive_share.measured == null ? tr("prior only") : tr("measured {} of {} replies", pct(i.positive_share.measured), i.positive_share.n)}</span></dd></div>
        <div><dt>{tr("Outbound deals per month")}</dt><dd>{d.flow.outbound_deals_per_month.toFixed(1)}</dd></div>
        <div><dt>{tr("Inbound deals per month")}</dt><dd>{d.flow.inbound_deals_per_month.toFixed(1)}{i.inbound_per_month.setting == null && i.inbound_per_month.measured != null ? ` (${tr("measured")})` : ""}</dd></div>
      </dl>
    </>
  );
}

type Draft = Record<string, any>;
function draftFrom(d: any): Draft {
  const s = d.settings, st = d.inputs.stages as any[];
  return {
    target: s.target || "", year: s.year ?? "", currency: s.currency ?? "", deal_value: s.deal_value, leads_per_month: s.leads_per_month ?? "", inbound_per_month: s.inbound_per_month ?? "",
    reply_rate: s.reply_rate, positive_share: s.positive_share, deal_from_positive: s.deal_from_positive, prior_strength: s.prior_strength,
    outbound_entry_stage_id: String(d.inputs.outbound_entry.stage_id), inbound_entry_stage_id: String(d.inputs.inbound_entry.stage_id),
    parking: (s.parking_stage_names || []).join(", "), won: (s.won_stage_names || []).join(", "),
    fx: Object.entries(d.inputs.fx_to_eur || {}).filter(([k]) => k !== "EUR").map(([k, v]) => `${k}=${v}`).join(", "),
    conv: Object.fromEntries(st.map((x) => [String(x.id), x.prior.conv])), days: Object.fromEntries(st.map((x) => [String(x.id), x.prior.days])),
  };
}

function Assumptions({ d, onSaved }: { d: any; onSaved: (x: any) => void }) {
  const [f, setF] = useState<Draft>(() => draftFrom(d));
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<unknown>(null); const [saved, setSaved] = useState(false);
  useEffect(() => { setF(draftFrom(d)); }, [d]);
  const set = (k: string, v: any) => { setSaved(false); setF((o) => ({ ...o, [k]: v })); };
  const num = (v: any) => (v === "" || v == null ? null : Number(v));
  const st = d.inputs.stages as any[];
  const save = async () => {
    setBusy(true); setErr(null);
    const clear: string[] = [];
    const body: Record<string, any> = {
      target: num(f.target) ?? 0, deal_value: num(f.deal_value), reply_rate: num(f.reply_rate), positive_share: num(f.positive_share), deal_from_positive: num(f.deal_from_positive),
      prior_strength: num(f.prior_strength), prior_conv: Object.fromEntries(Object.entries(f.conv).map(([k, v]) => [k, Number(v)])),
      prior_days: Object.fromEntries(Object.entries(f.days).map(([k, v]) => [k, Number(v)])),
      parking_stage_names: String(f.parking).split(",").map((x) => x.trim()).filter(Boolean), won_stage_names: String(f.won).split(",").map((x) => x.trim()).filter(Boolean),
      fx_to_eur: { EUR: 1, ...Object.fromEntries(String(f.fx).split(",").map((x) => x.split("=").map((y) => y.trim())).filter(([k, v]) => /^[A-Za-z]{3}$/.test(k ?? "") && Number(v) > 0).map(([k, v]) => [k.toUpperCase(), Number(v)])) },
    };
    for (const k of ["leads_per_month", "inbound_per_month", "year"]) { const v = num(f[k]); if (v == null) clear.push(k); else body[k] = v; }
    if (String(f.currency).trim()) body.currency = String(f.currency).trim().toUpperCase(); else clear.push("currency");
    if (d.stages_source === "pipedrive") { body.outbound_entry_stage_id = Number(f.outbound_entry_stage_id); body.inbound_entry_stage_id = Number(f.inbound_entry_stage_id); }
    try { onSaved(await put("/api/forecast/settings", { ...body, clear })); setSaved(true); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const field = (k: string, label: string, hint?: string, step = "any") => (
    <label className="fc-field"><span>{label}</span><input id={`fc-${k}`} type="number" min="0" step={step} value={f[k]} onChange={(e) => set(k, e.target.value)} />{hint && <small className="muted">{hint}</small>}</label>);
  return (
    <form onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <div className="fc-fields">
        {field("target", tr("Target for the year"), d.currency)}
        {field("deal_value", tr("Average deal value (prior)"), d.inputs.deal_value.n ? tr("measured {} from {} won deals", money(d.inputs.deal_value.measured), d.inputs.deal_value.n) : tr("no won deals measured yet"))}
        {field("leads_per_month", tr("Leads contacted per month"), tr("Empty: measured from lemlist"))}
        {field("reply_rate", tr("Reply rate, % (prior)"))}
        {field("positive_share", tr("Positive share of replies, % (prior)"))}
        {field("deal_from_positive", tr("Positive reply becomes a deal, % (prior)"))}
        {field("inbound_per_month", tr("Inbound deals per month"), tr("Empty: measured from Pipedrive"))}
        {field("prior_strength", tr("Weight of the priors, in deals"), tr("How many deals of evidence a prior counts as"), "1")}
        <label className="fc-field"><span>{tr("Year")}</span><input id="fc-year" type="number" min="2024" max="2100" step="1" placeholder={String(d.year)} value={f.year} onChange={(e) => set("year", e.target.value)} /></label>
        <label className="fc-field"><span>{tr("Currency")}</span><input id="fc-currency" type="text" maxLength={5} placeholder={d.currency} value={f.currency} onChange={(e) => set("currency", e.target.value)} /></label>
        {d.stages_source === "pipedrive" && <>
          <label className="fc-field"><span>{tr("Outbound deals start in")}</span><select id="fc-out-entry" value={f.outbound_entry_stage_id} onChange={(e) => set("outbound_entry_stage_id", e.target.value)}>{st.map((x) => <option key={x.id} value={String(x.id)}>{x.name}</option>)}</select></label>
          <label className="fc-field"><span>{tr("Inbound deals start in")}</span><select id="fc-in-entry" value={f.inbound_entry_stage_id} onChange={(e) => set("inbound_entry_stage_id", e.target.value)}>{st.map((x) => <option key={x.id} value={String(x.id)}>{x.name}</option>)}</select></label>
        </>}
        <label className="fc-field"><span>{tr("Parking stages (not part of the funnel)")}</span><input id="fc-parking" type="text" value={f.parking} onChange={(e) => set("parking", e.target.value)} /></label>
        <label className="fc-field"><span>{tr("Stages that mean won")}</span><input id="fc-won" type="text" value={f.won} onChange={(e) => set("won", e.target.value)} /></label>
        <label className="fc-field"><span>{tr("Exchange rates: 1 unit in EUR")}</span><input id="fc-fx" type="text" placeholder="PLN=0.235, GBP=1.17" value={f.fx} onChange={(e) => set("fx", e.target.value)} /><small className="muted">{tr("Deal values in other currencies are converted with these rates")}</small></label>
      </div>
      <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Stage assumptions")}>
        <table className="fc-table fc-stages"><thead><tr><th>{tr("Stage")}</th><th>{tr("Prior: move on, %")}</th><th>{tr("Prior: median days")}</th><th>{tr("Measured")}</th><th>{tr("Used")}</th><th>{tr("Open now")}</th><th>{tr("Win chance from here")}</th></tr></thead>
          <tbody>{st.map((x) => { const k = String(x.id), m = x.measured; return (
            <tr key={k}><td>{x.name}</td>
              <td><input id={`fc-conv-${k}`} aria-label={tr("{}: prior chance of moving on, %", x.name)} type="number" min="0" max="100" step="1" value={f.conv[k]} onChange={(e) => set("conv", { ...f.conv, [k]: e.target.value })} /></td>
              <td><input id={`fc-days-${k}`} aria-label={tr("{}: prior median days", x.name)} type="number" min="1" max="1000" step="1" value={f.days[k]} onChange={(e) => set("days", { ...f.days, [k]: e.target.value })} /></td>
              <td className="small">{m.conv == null ? tr("none yet") : `${pct(m.conv)} · ${m.decided} ${tr("decided")}`}<div className="muted">{m.days == null ? "" : `${Math.round(m.days)} ${tr("days")} · ${m.days_n} ${tr("moves")}`}</div></td>
              <td className="small">{pct(x.used.conv)}<div className="muted">{Math.round(x.used.days)} {tr("days")}</div></td>
              <td className="small">{x.open.n} · {money(x.open.value)}{x.open.avg_days != null && <div className="muted">{tr("avg {} days in stage", Math.round(x.open.avg_days))}</div>}</td>
              <td className="small">{pct(x.win_chance * 100, 1)}</td></tr>); })}</tbody></table>
      </div>
      <div className="row mt-2"><button type="submit" className="primary" disabled={busy}>{busy ? tr("Saving") : tr("Save and recalculate")}</button>
        {saved && <span className="small" role="status">{tr("Saved. The forecast below uses the new assumptions.")}</span>}</div>
      {!!err && <Err e={err} />}
    </form>
  );
}

export default function Forecast({ canManage }: { canManage: boolean }) {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [retry, setRetry] = useState(0);
  useEffect(() => {
    const req = new AbortController(); setErr(null);
    api("/api/forecast", { signal: req.signal }).then(setD).catch((e) => { if (!req.signal.aborted) setErr(e); });
    return () => req.abort();
  }, [retry]);
  if (err) return <><PageHeader eyebrow="Revenue" title="Forecast" /><Err e={err} onRetry={() => setRetry((n) => n + 1)} /></>;
  if (!d) return <><PageHeader eyebrow="Revenue" title="Forecast" /><Skeleton rows={6} /></>;
  const y = d.result.year, chance = d.result.chance_of_target, h = d.health;
  return (
    <>
      <PageHeader eyebrow="Revenue" title="Forecast" sub={tr("Closed-won revenue for {}, simulated {} times from the open Pipedrive pipeline and the deals outbound and inbound are expected to create.", d.year, d.runs.toLocaleString())} />
      {h.warnings.length > 0 && <div className="callout fc-warn">{h.warnings.map((w: string) => <p key={w} className="m-0">{tr(w)}</p>)}</div>}
      <div className="fc-top">
        <section className="card fc-hero" aria-labelledby="fc-hero-l">
          <div className="fc-label" id="fc-hero-l">{tr("Likely closed-won revenue in {} (median)", d.year)}</div>
          <div className="fc-big">{money(y.p50)}<small>{d.currency}</small></div>
          <p className="small m-0">{tr("8 in 10 simulated years land between {} and {}. Average {}.", money(y.p10), money(y.p90), money(y.mean))}</p>
          {chance != null ? <>
            <div className="fc-meter" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(chance * 100)} aria-label={tr("Chance of reaching the target")}><i style={{ width: `${chance * 100}%` }} /></div>
            <p className="small m-0">{tr("Chance of reaching the {} target: {}", money(d.target), pct(chance * 100))}</p>
          </> : <p className="small muted">{canManage ? tr("Set a target under Assumptions to see the chance of reaching it.") : tr("No target set yet.")}</p>}
          {d.stages_source === "placeholder" && <p className="small mt-2 mb-0"><Badge kind="warn">{tr("Placeholder stages")}</Badge></p>}
        </section>
        <Tiles d={d} />
      </div>

      <section className="card" aria-labelledby="fc-q">
        <div className="row spread"><h2 id="fc-q" className="h-sub m-0">{tr("Closed-won revenue by quarter")}</h2><span className="small muted">{tr("As of {}", fmtDate(d.as_of))}</span></div>
        <div className="fc-legend">
          <span><i className="fc-sw fc-open-sw" />{tr("Open pipeline today")}</span><span><i className="fc-sw fc-new-sw" />{tr("Deals not yet created")}</span>
          <span><svg width="12" height="14" aria-hidden="true"><path className="fc-whisk" d="M6,1V13M2,1H10M2,13H10" /></svg>{tr("8 in 10 years fall in this range")}</span>
          {d.target > 0 && <span><svg width="18" height="10" aria-hidden="true"><line className="fc-pace" x1="0" x2="18" y1="5" y2="5" /></svg>{tr("Even pace to target ({} a quarter)", money(d.target / 4))}</span>}
        </div>
        <QuarterChart d={d} />
        <details className="mt-2"><summary className="small">{tr("Table view")}</summary>
          <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Revenue by quarter")}>
            <table className="fc-table"><thead><tr><th>{tr("Period")}</th><th>{tr("Average")}</th><th>{tr("Open pipeline")}</th><th>{tr("New deals")}</th><th>P10</th><th>{tr("Median")}</th><th>P90</th></tr></thead>
              <tbody>{d.result.buckets.map((q: any, i: number) => <tr key={i}><td>{d.buckets[i].label}{i === 0 ? ` (${tr("not in the year total")})` : ""}</td><td>{money(q.mean)}</td><td>{money(q.open)}</td><td>{money(q.new)}</td><td>{money(q.p10)}</td><td>{money(q.p50)}</td><td>{money(q.p90)}</td></tr>)}</tbody>
              <tfoot><tr><td>{d.year}</td><td>{money(y.mean)}</td><td /><td /><td>{money(y.p10)}</td><td>{money(y.p50)}</td><td>{money(y.p90)}</td></tr></tfoot></table>
          </div>
          <p className="small muted">{tr("Quarter ranges do not add up to the year's range: a bad quarter and a good one rarely fall in the same simulated year. Currency: {}.", d.currency)}</p>
        </details>
      </section>

      <div className="fc-two">
        <section className="card" aria-labelledby="fc-f">
          <h2 id="fc-f" className="h-sub mt-0">{tr("Where deals drop out")}</h2>
          <p className="small muted mt-0">{tr("Twelve months of new deals at the rates in use. The rate in use blends what Pipedrive history measured with the prior, weighted by how many deals were decided.")}</p>
          <Funnel d={d} />
        </section>
        <section className="card" aria-labelledby="fc-s">
          <h2 id="fc-s" className="h-sub mt-0">{tr("What moves the number")}</h2>
          <p className="small muted mt-0">{tr("Change in average revenue for the year when one input is 20% lower or higher.")} <i className="fc-sw fc-up-sw" /> {tr("raises it")} <i className="fc-sw fc-down-sw" /> {tr("lowers it")}</p>
          <Sensitivity d={d} />
        </section>
      </div>

      <div className="fc-two">
        <section className="card" aria-labelledby="fc-o"><h2 id="fc-o" className="h-sub mt-0">{tr("Outbound")}</h2><Outbound d={d} /></section>
        <section className="card" aria-labelledby="fc-a">
          <h2 id="fc-a" className="h-sub mt-0">{tr("Deals taking longer than usual")}</h2>
          {d.pipeline.aging.length === 0 ? <p className="small muted">{tr("No open deal has been in its stage for more than 1.5 times the usual time.")}</p> :
            <ul className="fc-aging">{d.pipeline.aging.map((a: any) => <li key={a.crm_id}><b>{a.title}</b><span className="small muted">{a.stage} · {tr("{} days", a.days)} · {money(a.value)}</span></li>)}</ul>}
          {d.pipeline.parked.n > 0 && <p className="small">{tr("{} deals worth {} are parked and not counted.", d.pipeline.parked.n, money(d.pipeline.parked.value))}</p>}
          <h3 className="fc-h3">{tr("Earlier forecasts for {}", d.year)}</h3>
          {d.log.length === 0 ? <p className="small muted">{tr("The first forecast is saved tonight; from then on each day's forecast is kept so it can be checked against what closed.")}</p> :
            <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Earlier forecasts")}><table className="fc-table"><thead><tr><th>{tr("Made on")}</th><th>P10</th><th>{tr("Median")}</th><th>P90</th></tr></thead>
              <tbody>{d.log.slice(-8).reverse().map((l: any, i: number) => <tr key={i}><td>{fmtDate(l.made_on)}{l.trigger === "settings_saved" ? ` · ${tr("after a change")}` : ""}</td><td>{money(l.p10)}</td><td>{money(l.p50)}</td><td>{money(l.p90)}</td></tr>)}</tbody></table></div>}
        </section>
      </div>

      <section className="card" aria-labelledby="fc-set">
        <h2 id="fc-set" className="h-sub mt-0">{tr("Assumptions")}</h2>
        <p className="small muted mt-0">{tr("Deals in the data: {}, with history: {}, history read from Pipedrive: {}.", h.deals, h.deals_with_history, h.deals_backfilled)} {h.first_event_at && tr("History since {}.", fmtDate(h.first_event_at))}</p>
        {canManage ? <Assumptions d={d} onSaved={setD} /> : <p className="small">{tr("Sales managers and administrators can change these assumptions.")}</p>}
      </section>
    </>
  );
}
