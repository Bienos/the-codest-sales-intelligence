import { useEffect, useState } from "react";
import { api, fmtDate, put } from "../api";
import { tr } from "../locale";
import { Badge, Err, Skeleton } from "../components/ui";

/* Two Forecast tabs for the weekly sales meeting (backend: ri/forecast/movement.py).
   Leading indicators: what shows up weeks before revenue, week by week, against goals managers set.
   Pipeline movement: how the open funnel changed between two dates, as a waterfall, with the deals behind each step. */

const money = (v: number | null | undefined) => {
  if (v == null || !isFinite(v)) return "–";
  const a = Math.abs(v);
  return a >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : a >= 1e3 ? `${Math.round(v / 1e3)}k` : String(Math.round(v));
};

/* ------------------------------------------------------------------ leading indicators */
function Spark({ weeks, starts, label }: { weeks: number[]; starts: string[]; label: string }) {
  const W = 132, H = 30, n = weeks.length, gap = 2, bw = (W - gap * (n - 1)) / n, mx = Math.max(1, ...weeks);
  return <svg className="fw-spark" viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={tr("{}: last {} weeks", label, n)}>
    <line x1="0" x2={W} y1={H - 0.5} y2={H - 0.5} className="fw-axis" />
    {weeks.map((v, i) => { const h = v ? Math.max(2, (v / mx) * (H - 2)) : 0;
      return <rect key={i} x={i * (bw + gap)} y={H - 1 - h} width={bw} height={h} className={i === n - 1 ? "fw-now" : "fw-bar"}>
        <title>{tr("Week of {}: {}", fmtDate(starts[i]), v)}</title></rect>; })}
  </svg>;
}

export function Indicators({ canManage }: { canManage: boolean }) {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [goals, setGoals] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const load = () => api("/api/forecast/indicators").then((x: any) => { setD(x); setGoals(Object.fromEntries(x.rows.map((r: any) => [r.key, r.goal == null ? "" : String(r.goal)]))); }).catch(setErr);
  useEffect(() => { void load(); }, []);
  if (err && !d) return <Err e={err} />;
  if (!d) return <Skeleton rows={5} />;
  const changed = d.rows.some((r: any) => (goals[r.key] ?? "") !== (r.goal == null ? "" : String(r.goal)));
  const save = async () => {
    setBusy(true); setNote("");
    try {
      const weekly_goals = Object.fromEntries(Object.entries(goals).map(([k, v]) => [k, v.trim() === "" ? 0 : Number(v)]).filter(([, v]) => isFinite(v as number)));
      await put("/api/forecast/settings", { weekly_goals }); await load(); setNote(tr("Goals saved."));
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return <>
    <p className="small muted mt-0">{tr("What shows up weeks before revenue does. Each person or deal counts once, in the week it first happened. This week so far: {} of 7 days; pace is this week scaled to a full week.", d.days_into_week)}</p>
    <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Leading indicators by week")}>
      <table className="fc-table fw-table"><thead><tr><th>{tr("Indicator")}</th><th>{tr("Last {} weeks", d.week_starts.length)}</th><th>{tr("This week")}</th><th>{tr("Pace")}</th>
        <th>{tr("Last week")}</th><th>{tr("4-week average")}</th><th>{tr("Weekly goal")}</th></tr></thead>
        <tbody>{d.rows.map((r: any) => <tr key={r.key}>
          <td><b>{tr(r.label)}</b>{r.key === "sql" && d.stages.sql && <br />}{r.key === "sql" && d.stages.sql && <small className="muted">{tr("first time in {}", d.stages.sql)}</small>}
            {r.key === "proposal" && d.stages.proposal && <><br /><small className="muted">{tr("first time in {}", d.stages.proposal)}</small></>}</td>
          <td><Spark weeks={r.weeks} starts={d.week_starts} label={tr(r.label)} /></td>
          <td className="num">{r.this_week}</td><td className="num">{r.pace}</td><td className="num">{r.last_week ?? "–"}</td><td className="num">{r.avg_4w ?? "–"}</td>
          <td>{canManage ? <input id={`fw-goal-${r.key}`} className="fw-goal" type="number" min="0" step="1" inputMode="numeric" aria-label={tr("Weekly goal: {}", tr(r.label))}
              value={goals[r.key] ?? ""} onChange={(e) => setGoals({ ...goals, [r.key]: e.target.value })} /> : (r.goal ?? "–")}
            {r.status && <> <Badge kind={r.status === "on_pace" ? "ok" : "warn"}>{r.status === "on_pace" ? "On pace" : "Behind pace"}</Badge></>}</td>
        </tr>)}</tbody></table>
    </div>
    {canManage && <p className="row fc-actions"><button type="button" onClick={() => void save()} disabled={busy || !changed}>{busy ? tr("Saving") : tr("Save goals")}</button>
      {note && <span className="small" role="status">{note}</span>}</p>}
    {!d.sources.pipedrive && <p className="small muted">{tr("Meetings and calls appear after the first hourly read of Pipedrive activities.")}</p>}
    <p className="small muted">{tr("Meetings and calls count what is marked done in Pipedrive, plus meetings from connected calendars. Work not logged there does not show.")}</p>
  </>;
}

/* ------------------------------------------------------------------ pipeline movement */
type Step = { key: string; label: string; sign: 1 | -1 | 0; kind: "total" | "up" | "down" };
const STEPS: Step[] = [
  { key: "start", label: "Open at the start", sign: 0, kind: "total" }, { key: "added", label: "New deals", sign: 1, kind: "up" },
  { key: "revived", label: "Back from parking", sign: 1, kind: "up" }, { key: "won", label: "Won", sign: -1, kind: "down" },
  { key: "lost", label: "Lost", sign: -1, kind: "down" }, { key: "parked", label: "Parked or moved out", sign: -1, kind: "down" },
  { key: "value_up", label: "Value raised", sign: 1, kind: "up" }, { key: "value_down", label: "Value lowered", sign: -1, kind: "down" },
  { key: "end", label: "Open now", sign: 0, kind: "total" }];

function Waterfall({ m, by, cur }: { m: any; by: "n" | "value"; cur: string }) {
  const steps = STEPS.filter((s) => by === "value" || !s.key.startsWith("value_"));
  let run = 0;
  const bars = steps.map((s) => {
    const v = m.steps[s.key][by] as number;
    if (s.kind === "total") { run = v; return { s, v, lo: 0, hi: v }; }
    const lo = s.sign > 0 ? run : run - v, hi = s.sign > 0 ? run + v : run;
    run = s.sign > 0 ? run + v : run - v;
    return { s, v, lo, hi };
  });
  const mx = Math.max(1, ...bars.map((b) => b.hi));
  const fmt = (v: number) => (by === "value" ? `${money(v)} ${cur}` : String(v));
  const rowH = 30, labelW = 150, valueW = 96, W = 640, plotW = W - labelW - valueW, H = bars.length * rowH;
  const x = (v: number) => labelW + (v / mx) * plotW;
  return <svg className="fw-water" viewBox={`0 0 ${W} ${H + 4}`} role="img" aria-label={tr("Pipeline movement, {}", by === "value" ? tr("by value") : tr("by number of deals"))}>
    {bars.map((b, i) => {
      const y = i * rowH + 6, sign = !b.v ? "" : b.s.kind === "up" ? "+" : b.s.kind === "down" ? "−" : "";
      const w = Math.max(b.v ? 2 : 0, x(b.hi) - x(b.lo));
      return <g key={b.s.key}>
        <text x={labelW - 8} y={y + 13} textAnchor="end" className="fw-lbl">{tr(b.s.label)}</text>
        <rect x={x(b.lo)} y={y} width={w} height={18} className={`fw-${b.s.kind}`}><title>{`${tr(b.s.label)}: ${sign}${fmt(b.v)} (${m.steps[b.s.key].n} ${tr("deals")})`}</title></rect>
        {i < bars.length - 1 && <line x1={x(b.s.kind === "down" ? b.lo : b.hi)} x2={x(b.s.kind === "down" ? b.lo : b.hi)} y1={y + 18} y2={y + rowH} className="fw-link" />}
        <text x={x(b.hi) + 6} y={y + 13} className={b.v || b.s.kind === "total" ? "fw-val" : "fw-val fw-zero"}>{sign}{fmt(b.v)}</text>
      </g>;
    })}
  </svg>;
}

/** The same steps as a table: shown instead of the chart on narrow screens. */
function WaterfallTable({ m, by, cur }: { m: any; by: "n" | "value"; cur: string }) {
  let run = 0;
  const fmt = (v: number) => (by === "value" ? `${money(v)} ${cur}` : String(v));
  return <table className="fc-table fw-water-table"><caption className="small muted">{tr("Pipeline movement")}</caption>
    <thead><tr><th>{tr("Step")}</th><th>{tr("Change")}</th><th>{tr("Open funnel")}</th></tr></thead>
    <tbody>{STEPS.filter((s) => by === "value" || !s.key.startsWith("value_")).map((s) => {
      const v = m.steps[s.key][by] as number;
      run = s.kind === "total" ? v : run + s.sign * v;
      return <tr key={s.key} className={s.kind === "total" ? "fw-row-total" : undefined}><td>{tr(s.label)}</td>
        <td className="num">{s.kind === "total" ? "" : v ? `${s.sign > 0 ? "+" : "−"}${fmt(v)}` : "0"}</td><td className="num">{fmt(run)}</td></tr>;
    })}</tbody></table>;
}

export function Movement({ pipedriveUrl }: { pipedriveUrl?: string | null }) {
  const [days, setDays] = useState(30);
  const [by, setBy] = useState<"n" | "value">("n");
  const [m, setM] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { setErr(null); setM(null); api(`/api/forecast/movement?days=${days}`).then(setM).catch(setErr); }, [days]);
  if (err && !m) return <Err e={err} />;
  const sm = m?.stage_moves, cd = m?.close_dates;
  return <>
    <div className="row spread end fw-head">
      <p className="small muted m-0">{tr("How the open funnel changed: what came in, what closed or was parked, and what moved. Replayed from each deal's stage history, so any period works.")}</p>
      <div className="row fw-controls">
        <label className="small">{tr("Period")} <select id="fw-days" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 14, 30, 90, 180].map((n) => <option key={n} value={n}>{tr("Last {} days", n)}</option>)}</select></label>
        <div className="cg-filter fw-by" role="group" aria-label={tr("Measure")}>
          <button type="button" aria-pressed={by === "n"} onClick={() => setBy("n")}>{tr("Deals")}</button>
          <button type="button" aria-pressed={by === "value"} onClick={() => setBy("value")}>{tr("Value")}</button>
        </div>
      </div>
    </div>
    {!m ? <Skeleton rows={6} /> : <>
      <div className="fw-water-wrap"><Waterfall m={m} by={by} cur={m.currency} /></div>
      <WaterfallTable m={m} by={by} cur={m.currency} />
      <p className="small fw-legend"><span className="fw-key fw-total" aria-hidden="true" /> {tr("open funnel")} <span className="fw-key fw-up" aria-hidden="true" /> {tr("came in")} <span className="fw-key fw-down" aria-hidden="true" /> {tr("went out")}</p>
      {by === "value" && m.end_without_value > 0 && <p className="small">{tr("{} of {} deals open now have no value in Pipedrive and count as 0 here.", m.end_without_value, m.steps.end.n)}</p>}
      <dl className="fc-dl">
        <div><dt>{tr("Open at both dates")}</dt><dd>{tr("{} moved forward, {} moved back, {} stayed in the same stage", sm.forward, sm.back, sm.same)}</dd></div>
        <div><dt>{tr("Close dates")}</dt><dd>{cd.measured ? tr("{} moved later, {} moved earlier, {} unchanged", cd.pushed, cd.pulled, cd.same)
          : tr("Shown once daily snapshots cover the start of the period (kept since {}).", m.snapshots_since ? fmtDate(m.snapshots_since) : tr("today"))}</dd></div>
      </dl>
      {STEPS.filter((s) => s.kind !== "total" && m.steps[s.key].n > 0).map((s) => <details key={s.key} className="fw-deals">
        <summary>{tr(s.label)}: {m.steps[s.key].n}{m.steps[s.key].value ? ` · ${money(m.steps[s.key].value)} ${m.currency}` : ""}</summary>
        <ul>{m.steps[s.key].deals.map((x: any) => <li key={x.crm_id}>{pipedriveUrl ? <a href={`${pipedriveUrl}/deal/${x.crm_id}`} target="_blank" rel="noreferrer">{x.title}</a> : x.title}
          {x.org ? ` · ${x.org}` : ""}{x.value ? ` · ${money(x.value)} ${m.currency}` : ""}{x.stage_before ? ` · ${tr("was in {}", x.stage_before)}` : ""}</li>)}</ul>
      </details>)}
      <p className="small muted">{tr("From {} to {}.", fmtDate(m.start), fmtDate(m.end))}</p>
    </>}
  </>;
}
