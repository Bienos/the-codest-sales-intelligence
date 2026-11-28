import { tr } from "../locale";

/* Pipeline flow and funnel predictions (backend: ri/forecast/outlook.py). Everything is from d.outlook: the stage rates and times in use,
   2,000 simulated runs of the next 90 days, and what actually entered each stage month by month. */

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const r0 = (v: number) => (v >= 10 ? Math.round(v) : Math.round(v * 10) / 10);
const pc = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v * 100)}%`);
const range = (a: any) => (Math.round(a.p10) === Math.round(a.p90) ? `${Math.round(a.p50)}` : `${Math.round(a.p10)}–${Math.round(a.p90)}`);

type O = any;
const names = (o: O): string[] => [...o.stages.map((s: any) => s.name), tr("Won")];
const arrivals = (o: O, j: number) => (j < o.stages.length ? o.stages[j].arrivals : o.won.arrivals);

/* ------------------------------------------------------------------ flow: every stage, its rate onwards, what is there now, what is coming */
export function FlowStrip({ o, pair, setPair }: { o: O; pair: [number, number]; setPair: (p: [number, number]) => void }) {
  const st = o.stages as any[];
  const cols = [...st.map((s, i) => ({ i, name: s.name, open: s.open, a: s.arrivals.total, days: s.days })), { i: st.length, name: tr("Won"), open: null, a: o.won.arrivals.total, days: null }];
  const inPair = (i: number) => i >= pair[0] && i <= pair[1];
  const parts: JSX.Element[] = [];
  cols.forEach((c, k) => {
    const s = st[c.i];
    const trend = s && s.recent != null && s.measured != null && s.recent_decided >= 5 ? s.recent - s.measured : 0;
    parts.push(
      <button key={`s${c.i}`} type="button" className={`ff-stage${inPair(c.i) ? " in" : ""}${c.i === pair[0] || c.i === pair[1] ? " end" : ""}${c.i === st.length ? " won" : ""}`}
        onClick={() => setPair(c.i <= pair[0] ? [c.i, Math.max(pair[1], c.i + 1)] : [pair[0], c.i])}
        aria-label={tr("{}: {} open now, {} expected in the next 90 days. Pick as the start or end of the prediction.", c.name, c.open ?? 0, range(c.a))}>
        <span className="ff-name">{c.name.replace(/\//g, "/\u200b")}</span>
        <span className="ff-now"><b>{c.open == null ? "–" : c.open}</b><small>{c.open == null ? tr("closed so far") : tr("open now")}</small></span>
        <span className="ff-next"><span className="ct-lab">{tr("Next 90 days")}</span><b>{range(c.a)}</b></span>
        {c.days != null && <span className="ff-days">{tr("~{} days here", Math.round(c.days))}</span>}
      </button>);
    if (k < cols.length - 1 && s) {
      const why = s.interval ? tr("Measured {} of {} decided deals; 8 in 10 chance the true rate is {} to {}. In use: {} (blended with the prior).", pc(s.measured), s.decided, pc(s.interval[0]), pc(s.interval[1]), pc(s.conv))
        : tr("No decided deals yet: the prior {} is in use.", pc(s.prior));
      parts.push(
        <div key={`a${c.i}`} className="ff-arrow" title={why} aria-label={tr("{} move on from {}. {}", pc(s.conv), c.name, why)} role="img">
          <b>{pc(s.conv)}</b>
          <svg viewBox="0 0 60 12" aria-hidden="true"><path d="M1 6H52" /><path d="M50 1l8 5-8 5" /></svg>
          <span className="ff-n">{s.decided ? tr("{} decided", s.decided) : tr("prior")}</span>
          {Math.abs(trend) >= 0.05 && <span className={`ff-trend ${trend > 0 ? "up" : "down"}`}>{trend > 0 ? "▲" : "▼"} {tr("{} lately", pc(s.recent))}</span>}
        </div>);
    }
  });
  return <div className="ff-strip" style={{ gridTemplateColumns: `repeat(${cols.length - 1}, minmax(112px, 1fr) 70px) minmax(112px, 1fr)` }}>{parts}</div>;
}

/* ------------------------------------------------------------------ the predictor: from one stage to another */
export function Predictor({ o, pair, setPair }: { o: O; pair: [number, number]; setPair: (p: [number, number]) => void }) {
  const nm = names(o);
  const [a, b] = pair;
  const r = o.reach[a]?.[b];
  const from = o.stages[a];
  if (!r || !from) return null;
  const inTen = r.p * 10;
  const fromOpen = from.open * r.p;
  const arr = arrivals(o, b);
  const lo = (o.stages[a].interval && a + 1 === b) ? o.stages[a].interval : null;
  return (
    <div className="ff-pred">
      <div className="ff-pick">
        <label><span className="ct-lab">{tr("From")}</span><select value={a} onChange={(e) => { const x = +e.target.value; setPair([x, Math.max(b, x + 1)]); }}>
          {o.stages.map((s: any, i: number) => <option key={s.id} value={i}>{s.name}</option>)}</select></label>
        <span className="ff-to">→</span>
        <label><span className="ct-lab">{tr("To")}</span><select value={b} onChange={(e) => setPair([a, +e.target.value])}>
          {nm.map((n, i) => (i > a ? <option key={i} value={i}>{n}</option> : null))}</select></label>
      </div>
      <div className="ff-big">
        <div className="ff-ten" role="img" aria-label={tr("{} in 10", r0(inTen))}>
          {Array.from({ length: 10 }, (_, i) => <i key={i}><b style={{ width: `${Math.max(0, Math.min(1, inTen - i)) * 100}%` }} /></i>)}
        </div>
        <p className="ff-say"><b>{r0(inTen)} {tr("in 10")}</b> {tr("deals that reach {} go on to reach {}, usually within about {} days.", nm[a], nm[b], Math.round(r.days))}
          {lo && <span className="muted"> {tr("Measured rate {}; 8 in 10 chance the true rate is {} to {}.", pc(o.stages[a].measured), pc(lo[0]), pc(lo[1]))}</span>}</p>
      </div>
      <div className="ff-facts">
        <div><span className="ct-lab">{tr("From today's deals")}</span><b>{r0(fromOpen)}</b><small>{tr("of the {} open in {} should reach {}", from.open, nm[a], nm[b])}</small></div>
        <div><span className="ct-lab">{tr("Arriving in {}", nm[b])}</span><b>{range(arr.total)}</b><small>{tr("in the next 90 days, most likely {}", Math.round(arr.total.p50))}</small></div>
      </div>
      <div className="ff-wins" aria-label={tr("Arrivals by 30-day window")}>
        {arr.by_window.map((w: any, i: number) => {
          const top = Math.max(1, ...arr.by_window.map((x: any) => x.p90));
          return <div key={i} className="ff-win"><span className="ct-lab">{i === 0 ? tr("Next 30 days") : tr("Days {}–{}", i * 30 + 1, (i + 1) * 30)}</span>
            <span className="ff-wbar"><i className="rng" style={{ left: `${(w.p10 / top) * 100}%`, width: `${Math.max(1, ((w.p90 - w.p10) / top) * 100)}%` }} /><i className="mid" style={{ left: `${(w.p50 / top) * 100}%` }} /></span>
            <b>{range(w)}</b></div>;
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ months: what entered a stage, and what the model expects next */
export function ArrivalsChart({ o, stage, setStage }: { o: O; stage: number; setStage: (i: number) => void }) {
  const nm = names(o);
  const act = o.actuals;
  const counts: number[] = act.counts[stage] || [];
  const proj = arrivals(o, stage).by_window as any[];
  const n = counts.length, slots = n + proj.length;
  const top = Math.max(1, ...counts, ...proj.map((p) => p.p90));
  const H = 180, W = 100 / slots;
  const label = (iso: string) => { const [y, m] = iso.split("-").map(Number); return `${tr(MON[m - 1])}${m === 1 ? ` ${String(y).slice(2)}` : ""}`; };
  const avg = counts.length > 1 ? counts.slice(0, -1).reduce((s, x) => s + x, 0) / (counts.length - 1) : 0;
  return (
    <div className="ff-months">
      <div className="ff-mhead">
        <div className="ff-tabs" role="tablist" aria-label={tr("Stage")}>
          {nm.map((x, i) => <button key={i} type="button" role="tab" aria-selected={i === stage} className={i === stage ? "on" : ""} onClick={() => setStage(i)}>{x}</button>)}
        </div>
        <span className="small muted">{tr("Average {} a month over the last 11 full months", r0(avg))}</span>
      </div>
      <div className="ff-chart" style={{ height: H + 34 }}>
        {[0.25, 0.5, 0.75, 1].map((f) => <span key={f} className="ff-grid" style={{ bottom: 24 + f * H }}><em>{Math.round(top * f)}</em></span>)}
        {counts.map((c, i) => {
          const cur = i === n - 1;
          return <div key={i} className={`ff-col${cur ? " cur" : ""}`} style={{ left: `${i * W}%`, width: `${W}%` }} title={cur ? tr("{} so far this month", c) : String(c)}>
            <i style={{ height: (c / top) * H }}><b>{c}</b></i><span>{label(act.months[i])}</span></div>;
        })}
        {proj.map((p, k) => (
          <div key={`p${k}`} className="ff-col proj" style={{ left: `${(n + k) * W}%`, width: `${W}%` }} title={tr("Expected {} (8 in 10 runs: {} to {})", Math.round(p.p50), Math.round(p.p10), Math.round(p.p90))}>
            <i style={{ height: (p.p50 / top) * H }}><b style={{ top: -17 - ((p.p90 - p.p50) / top) * H }}>{Math.round(p.p50)}</b></i>
            <s style={{ bottom: 24 + (p.p10 / top) * H, height: Math.max(2, ((p.p90 - p.p10) / top) * H) }} />
            <span>{k === 0 ? tr("+30 d") : k === 1 ? tr("+60 d") : tr("+90 d")}</span></div>
        ))}
        <span className="ff-now-line" style={{ left: `${n * W}%` }}><em>{tr("today")}</em></span>
      </div>
      <div className="ff-mkeys"><span><i className="a" />{tr("Entered {} (first time)", nm[stage])}</span><span><i className="c" />{tr("This month so far")}</span><span><i className="p" />{tr("Expected per 30 days, with the range 8 in 10 runs fall in")}</span></div>
      <p className="xs muted">{tr("If the bars to the right of today keep landing well above or below the recent months, the rates or the new-deal inputs under Assumptions need a look.")}</p>
    </div>
  );
}
