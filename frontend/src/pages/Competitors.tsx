import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, NavLink, useNavigate, useParams } from "react-router-dom";
import { api, patch, post, put } from "../api";
import { tr } from "../locale";
import { Err, Skeleton } from "../components/ui";
import "../styles/competitors.css";

/* Competitor tracker (docs/competitor-tracker.md). Everything here is read from /api/competitors: tiers, overlap scores and pattern counts are
   worked out by the server from the rows, so the page never shows a number the data does not support. Every value shows its source, its date
   and its grade (official, reported or our estimate). */

type Fact = { value?: any; n?: number | null; source?: string; asOf?: string; grade?: string; trend?: string } | null | undefined;
type Move = { id: string; date: string; type: string; headline: string; detail?: string; sowhat?: string; source: string; grade: string; impact: number; status?: string };
type Comp = any;

const ORIGIN: Record<string, string> = { PL: "Poland", UA: "Ukraine", EU: "Rest of Europe" };
const TIER: Record<number, string> = { 0: "Us", 1: "Tier 1 · direct", 2: "Tier 2 · adjacent", 3: "Tier 3 · reference" };
const MARKETS = ["UK", "DACH", "Nordics", "PL", "US"];
const GRADE: Record<string, string> = { official: "Official", reported: "Reported", estimate: "Estimate" };
const OFFERS: Record<string, string> = { staff: "Staff augmentation", team: "Team extension / dedicated teams", product: "Product development with delivery responsibility", ai: "AI services" };
const ROLE: Record<string, string> = { incumbent: "Incumbent supplier", shortlisted: "Shortlisted with us", won_against: "We won against them", lost_to: "We lost to them", unknown: "Met, outcome unknown" };
const VIEWS: [string, string][] = [["briefing", "Briefing"], ["landscape", "Landscape"], ["compare", "Compare"], ["profiles", "Profiles"], ["moves", "Moves"],
  ["patterns", "Patterns"], ["head-to-head", "Head-to-head"], ["method", "Method"]];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function monthsOld(d: string | undefined, asOf: string): number | null {
  const m = String(d || "").match(/^(\d{4})(?:-(\d{2}))?/); const a = String(asOf || "").match(/^(\d{4})-(\d{2})/);
  if (!m || !a) return null;
  return (+a[1] - +m[1]) * 12 + (+a[2] - (m[2] ? +m[2] : 1));
}
function fmtD(d?: string | null): string {
  const m = String(d || "").match(/^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?/);
  if (!m) return d || "";
  return (m[3] ? +m[3] + " " : "") + (m[2] ? tr(MON[+m[2] - 1]) + " " : "") + m[1];
}
function host(u?: string) { try { return new URL(u || "").hostname.replace(/^www\./, ""); } catch { return ""; } }

const Grade = ({ g }: { g?: string }) => (g ? <i className={`ct-gr ${g}`} title={tr(GRADE[g] || g)} aria-label={tr(GRADE[g] || g)} /> : null);
function Src({ f, asOf }: { f: any; asOf: string }) {
  if (!f) return null;
  const old = f.asOf ? (monthsOld(f.asOf, asOf) ?? 0) > 18 : false;
  return <span className="ct-src"><Grade g={f.grade} />{f.source && <a href={f.source} target="_blank" rel="noopener noreferrer">{host(f.source)}</a>}
    {f.asOf && <span className={`ct-age${old ? " stale" : ""}`} title={old ? tr("Older than 18 months: check before use") : tr("Date of the source")}>{fmtD(f.asOf)}</span>}</span>;
}
const Val = ({ f }: { f: Fact }) => (f && f.value != null && f.value !== "" ? <>{String(f.value)}</> : <span className="muted">{tr("Not found")}</span>);
const Dot = ({ c }: { c: Comp }) => <i className={`ct-dot o-${c.origin}`} title={tr(ORIGIN[c.origin] || c.origin)} />;
const Meter = ({ n, max = 3 }: { n: number; max?: number }) => <span className="ct-meter" role="img" aria-label={tr("{} of {}", n, max)}>{Array.from({ length: max }, (_, i) => <i key={i} className={i < n ? "on" : ""} />)}</span>;
function Imp({ n }: { n: number }) { const t = n === 3 ? "High" : n === 2 ? "Medium" : "Low"; return <span className={`ct-imp i${n}`} title={tr("Impact on The Codest: {}", tr(t))}>{n === 3 ? "▲▲" : n === 2 ? "▲" : "·"} {tr(t)}</span>; }
function Markets({ c }: { c: Comp }) { const m = new Set(c.markets || []); return <span className="ct-mk">{MARKETS.map((k) => <span key={k} className={m.has(k) ? "on" : ""}>{k}</span>)}</span>; }
const TierChip = ({ c }: { c: Comp }) => <span className={`ct-chip t${c.tier}`}>{tr(TIER[c.tier] || "")}</span>;

export default function Competitors() {
  const { view = "briefing", key } = useParams();
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [n, setN] = useState(0);
  const reload = () => setN((x) => x + 1);
  useEffect(() => { setErr(null); api("/api/competitors").then(setD).catch(setErr); }, [n]);
  if (err) return <Err e={err} onRetry={reload} />;
  if (!d) return <Skeleton rows={6} />;
  if (d.empty) return <div className="empty"><b>{tr("No competitors loaded yet")}</b>{tr("The research seed loads when the server starts (ri seed). Ask an administrator to run it.")}</div>;
  const all: Comp[] = [d.us, ...d.competitors];
  const byKey: Record<string, Comp> = Object.fromEntries(all.map((c) => [c.key, c]));
  const current = view === "profile" ? "profiles" : view;
  const tabs = [...VIEWS, ...(d.can?.manage ? [["review", d.waiting ? tr("Review ({})", d.waiting) : "Review"] as [string, string]] : [])];
  const ctx = { d, all, byKey, reload, asOf: d.asOf as string };
  return (
    <div className="ct">
      <div className="page-head">
        <div className="eyebrow">{tr("Competitor tracker")}</div>
        <h1>{tr("Who we compete with, and what they are doing")}</h1>
        <p className="sub">{d.lede}</p>
        <div className="ct-meta">
          <span><b>{tr("Data as of {}", fmtD(d.asOf))}</b> · {tr("{} companies", d.competitors.length)} · {tr("{} dated moves", d.competitors.reduce((s: number, c: Comp) => s + c.moves.length, 0))}</span>
          <span className="ct-legend">
            <span><i className="ct-gr official" />{tr("Official: their own site or filing")}</span>
            <span><i className="ct-gr reported" />{tr("Reported: press, rankings, review sites")}</span>
            <span><i className="ct-gr estimate" />{tr("Estimate: our judgement")}</span>
            <span className="ct-age stale">{tr("older than 18 months")}</span>
          </span>
        </div>
      </div>
      <nav className="ct-tabs" aria-label={tr("Views")}>
        {tabs.map(([k, l]) => <NavLink key={k} to={`/competitors/${k}`} className={() => (k === current ? "active" : "")} aria-current={k === current ? "page" : undefined}>{tr(l)}</NavLink>)}
      </nav>
      {current === "briefing" && <Briefing {...ctx} />}
      {current === "landscape" && <Landscape {...ctx} />}
      {current === "compare" && <Compare {...ctx} />}
      {current === "profiles" && <Profiles {...ctx} selected={key} />}
      {current === "moves" && <Moves {...ctx} />}
      {current === "patterns" && <Patterns {...ctx} />}
      {current === "head-to-head" && <HeadToHead {...ctx} />}
      {current === "method" && <Method {...ctx} />}
      {current === "review" && d.can?.manage && <Review {...ctx} />}
      <p className="ct-foot small muted">{tr("Research seed from public sources ({}): company sites, registry filings, ITwiz BEST100, Clutch, press and buyer studies. Scores, tiers and map placements are judgements and are labelled as such.", fmtD(d.seedAsOf))}</p>
    </div>
  );
}

type Ctx = { d: any; all: Comp[]; byKey: Record<string, Comp>; reload: () => void; asOf: string };
const ProfileLink = ({ c, children }: { c: Comp; children?: ReactNode }) => <Link to={`/competitors/profile/${c.key}`}>{children ?? c.name}</Link>;

/* ------------------------------------------------------------------ Briefing */
function Briefing({ d, asOf }: Ctx) {
  const recent = d.competitors.flatMap((c: Comp) => c.moves.map((m: Move) => ({ ...m, c }))).filter((m: any) => (monthsOld(m.date, asOf) ?? 99) <= 12)
    .sort((a: any, b: any) => b.impact - a.impact || String(b.date).localeCompare(String(a.date))).slice(0, 6);
  const t1 = d.competitors.filter((c: Comp) => c.tier === 1).sort((a: Comp, b: Comp) => b.overlap.total - a.overlap.total);
  const TREND: Record<string, string> = { up: "▲ Growing", down: "▼ Shrinking", flat: "■ Flat" };
  return (
    <section className="ct-view">
      <div className="ct-brief">
        <div className="card"><h3 className="ct-h">{tr("What the evidence says")}</h3>
          {d.briefing.map((b: any, i: number) => <div key={i} className="ct-insight"><span className="n">{i + 1}</span><h4>{b.title}</h4><p className="small">{b.text}</p><p className="ct-act"><b>{tr("For us:")}</b> {b.action}</p></div>)}
        </div>
        <div className="ct-stack">
          <div className="card"><h3 className="ct-h">{tr("Where we stand")}</h3>
            <p className="xs muted">{tr("The Codest against the median of the {} direct competitors (tier 1). Only things we can measure from public data.", t1.length)}</p>
            <div className="ct-scroll"><table className="ct-stand"><thead><tr><th>{tr("Measure")}</th><th>{tr("Us")}</th><th>{tr("Tier 1")}</th><th /></tr></thead><tbody>
              {d.standing.map((s: any) => <tr key={s.what}><td><b>{s.what}</b><div className="xs muted">{s.basis}</div></td><td className="num">{s.us}</td><td className="num">{s.them}</td>
                <td><span className={`ct-verdict ${s.verdict}`}>{tr(s.label || ({ ahead: "Ahead", behind: "Behind", level: "Level" } as any)[s.verdict])}</span></td></tr>)}
            </tbody></table></div></div>
          <div className="card"><h3 className="ct-h">{tr("Tier 1 at a glance")}</h3>
            <p className="xs muted">{tr("The seven firms most likely to meet us in a deal, by overlap score.")}</p>
            <div className="ct-scroll"><table className="ct-stand"><thead><tr><th>{tr("Firm")}</th><th>{tr("Overlap")}</th><th>{tr("Revenue")}</th><th>{tr("Latest move")}</th></tr></thead><tbody>
              {t1.map((c: Comp) => { const last = c.moves[0]; return <tr key={c.key}><td><ProfileLink c={c}><Dot c={c} /> {c.name}</ProfileLink><div className="xs muted">{c.archetype}</div></td>
                <td className="num">{c.overlap.total}</td><td className="xs nowrap">{TREND[c.revenue?.trend] ? tr(TREND[c.revenue.trend]) : <span className="muted">{tr("Not known")}</span>}</td>
                <td className="xs">{last ? <><span className="ct-age">{fmtD(last.date)}</span> {last.headline}</> : <span className="muted">{tr("No dated move")}</span>}</td></tr>; })}
            </tbody></table></div></div>
        </div>
      </div>
      <div className="card"><h3 className="ct-h">{tr("Biggest moves in the last 12 months")}</h3>
        {recent.map((m: any) => <MoveRow key={m.id} m={m} c={m.c} asOf={asOf} />)}
        <p className="small"><Link to="/competitors/moves">{tr("All moves")}</Link></p>
      </div>
    </section>
  );
}

function MoveRow({ m, c, asOf, types }: { m: Move; c?: Comp; asOf: string; types?: Record<string, any> }) {
  return <div className="ct-mv"><span className="when">{fmtD(m.date)}</span><div>
    <div className="ct-row">{c && <><Dot c={c} /><ProfileLink c={c}><b>{c.name}</b></ProfileLink></>}<Imp n={m.impact} />{types && <span className="ct-chip">{tr(types[m.type]?.label || m.type)}</span>}</div>
    <div className="what">{m.headline}</div>
    {m.detail && <p className="small">{m.detail}</p>}
    {m.sowhat && <p className="ct-so"><b>{tr("So what:")}</b> {m.sowhat}</p>}
    <Src f={{ source: m.source, grade: m.grade }} asOf={asOf} />
  </div></div>;
}

/* ------------------------------------------------------------------ Landscape */
function Landscape({ d, all, asOf }: Ctx) {
  const nav = useNavigate();
  const [tier, setTier] = useState("all"); const [origin, setOrigin] = useState("all");
  const [tip, setTip] = useState<{ c: Comp; x: number; y: number } | null>(null);
  const W = 760, H = 520, L = 58, R = 24, T = 30, B = 58, pw = W - L - R, ph = H - T - B;
  const X = (v: number) => L + (v / 10) * pw, Y = (v: number) => T + ph - (v / 10) * ph;
  const vis = (c: Comp) => c.tier === 0 || ((tier === "all" || String(c.tier) === tier) && (origin === "all" || c.origin === origin));
  const rad = (c: Comp) => { const n = c.headcount?.n || 150; return n < 250 ? 5 : n < 1000 ? 7 : n < 5000 ? 9.5 : 12; };
  const pts = useMemo(() => {
    const sorted = all.slice().sort((a, b) => (a.tier === 0 ? -1 : b.tier === 0 ? 1 : (b.headcount?.n || 0) - (a.headcount?.n || 0)));
    const placed: { x: number; y: number; r: number }[] = [];
    const steps = [[0, 0], [1, 0], [-1, 0], [0, -1], [0, 1], [1, -1], [-1, 1], [1, 1], [-1, -1], [2, 0], [-2, 0]];
    return sorted.map((c) => {
      const r = c.tier === 0 ? rad(c) + 5 : rad(c);
      const x0 = X(c.pos?.x ?? 5), y0 = Y(c.pos?.y ?? 5);
      let at = { x: x0, y: y0 };
      for (const [dx, dy] of steps) { const x = x0 + dx * (r + 6), y = y0 + dy * (r + 6); if (placed.every((p) => Math.hypot(p.x - x, p.y - y) >= p.r + r + 3)) { at = { x, y }; break; } }
      placed.push({ ...at, r });
      return { c, ...at, r: rad(c) };
    });
  }, [all]);   // eslint-disable-line react-hooks/exhaustive-deps
  const ov = d.competitors.slice().sort((a: Comp, b: Comp) => b.overlap.total - a.overlap.total).slice(0, 12);
  const Seg = ({ v, set, opts }: { v: string; set: (s: string) => void; opts: [string, string][] }) =>
    <span className="ct-seg">{opts.map(([k, l]) => <button key={k} type="button" aria-pressed={v === k} onClick={() => set(k)}>{tr(l)}</button>)}</span>;
  return (
    <section className="ct-view">
      <p className="sub">{tr("Where each firm sits on the two choices that matter most in our deals: how specialised it is in financial services, and whether it sells people by the month or takes responsibility for an outcome. Hover or focus a mark for the reason.")}</p>
      <div className="ct-filters"><span className="ct-label">{tr("Show")}</span>
        <Seg v={tier} set={setTier} opts={[["all", "All"], ["1", "Tier 1"], ["2", "Tier 2"], ["3", "Tier 3"]]} />
        <Seg v={origin} set={setOrigin} opts={[["all", "All origins"], ["PL", "Poland"], ["UA", "Ukraine"], ["EU", "Rest of Europe"]]} /></div>
      <div className="card">
        <h3 className="ct-h">{tr("Positioning map")}</h3>
        <div className="ct-keys"><span><i className="ct-dot o-PL" />{tr("Poland")}</span><span><i className="ct-dot o-UA" />{tr("Ukraine")}</span><span><i className="ct-dot o-EU" />{tr("Rest of Europe")}</span>
          <span><i className="ct-us" />The Codest</span><span>{tr("Mark size: headcount (under 250, 250 to 999, 1,000 to 4,999, 5,000+)")}</span></div>
        <p className="xs muted ct-maphint">{tr("Swipe sideways to see the whole map, or open it as a table below.")}</p>
        <div className="ct-chart"><div className="inner">
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={tr("Positioning map of tracked competitors")}>
            {[0, 2.5, 5, 7.5, 10].map((i) => <g key={i}><line className="grid" x1={X(i)} x2={X(i)} y1={T} y2={T + ph} /><line className="grid" x1={L} x2={L + pw} y1={Y(i)} y2={Y(i)} /></g>)}
            <rect className="zone" x={X(6.5)} y={Y(10)} width={X(10) - X(6.5)} height={Y(5) - Y(10)} /><text className="zl" x={X(6.6)} y={Y(10) + 14}>{tr("Specialist and owns delivery")}</text>
            <line className="axis" x1={L} x2={L + pw} y1={T + ph} y2={T + ph} /><line className="axis" x1={L} x2={L} y1={T} y2={T + ph} />
            <text x={L} y={T + ph + 20}>{tr("Generalist")}</text><text x={L + pw} y={T + ph + 20} textAnchor="end">{tr("Financial-services specialist")}</text>
            <text x={L + pw / 2} y={T + ph + 44} textAnchor="middle" className="ql">{tr("Who they sell to →")}</text>
            <text transform={`translate(16 ${T + ph / 2}) rotate(-90)`} textAnchor="middle" className="ql">{tr("What they sell →")}</text>
            <text x={L - 8} y={T + ph} textAnchor="end">{tr("People")}</text><text x={L - 8} y={T + 10} textAnchor="end">{tr("Outcomes")}</text>
            {pts.map(({ c, x, y, r }) => { const f = vis(c) ? "" : " fade"; const fill = `var(--ct-${String(c.origin).toLowerCase()})`;
              return <g key={c.key} className={f}>
                {c.tier === 0 ? <><circle className="ring" cx={x} cy={y} r={r + 5} /><circle className="pt" cx={x} cy={y} r={r} fill="var(--pink)" /></>
                  : c.origin === "PL" ? <rect className="pt" x={x - r} y={y - r} width={2 * r} height={2 * r} fill={fill} />
                  : c.origin === "UA" ? <rect className="pt" x={x - r} y={y - r} width={2 * r} height={2 * r} fill={fill} transform={`rotate(45 ${x} ${y})`} />
                  : <circle className="pt" cx={x} cy={y} r={r} fill={fill} />}
                <circle className="hit" cx={x} cy={y} r={Math.max(13, r + 4)} tabIndex={0} role="img" aria-label={c.name} onMouseEnter={() => setTip({ c, x, y })} onFocus={() => setTip({ c, x, y })}
                  onMouseLeave={() => setTip(null)} onBlur={() => setTip(null)} onClick={() => c.tier !== 0 && nav(`/competitors/profile/${c.key}`)} onKeyDown={(e) => { if (e.key === "Enter" && c.tier !== 0) nav(`/competitors/profile/${c.key}`); }} />
              </g>; })}
            {pts.filter(({ c }) => (c.tier === 1 && vis(c)) || c.tier === 0).map(({ c, x, y, r }) => { const right = x > W - R - 150; const dx = (c.tier === 0 ? r + 9 : r + 5);
              return <text key={c.key} className={`nm${c.tier === 0 ? " us" : ""}`} x={right ? x - dx : x + dx} y={y + 4} textAnchor={right ? "end" : "start"}>{c.name}</text>; })}
          </svg>
          {tip && <div className="ct-tip" style={{ left: `${(tip.x / W) * 100}%`, top: `${(tip.y / H) * 100}%` }}><b>{tip.c.name}</b>{tr(ORIGIN[tip.c.origin])} · {tr(TIER[tip.c.tier])}<br />
            {tr("Financial services {}/10 · Outcomes {}/10", tip.c.pos?.x, tip.c.pos?.y)}<br />{tip.c.headcount?.value || tr("Headcount not found")}<br /><span className="muted">{tip.c.pos?.why}</span></div>}
        </div></div>
        <details className="ct-tbl"><summary>{tr("Show as table")}</summary><div className="ct-scroll"><table className="ct-data"><thead><tr><th>{tr("Company")}</th><th>{tr("Origin")}</th><th>{tr("Tier")}</th><th>{tr("FS focus (0–10)")}</th><th>{tr("Outcome (0–10)")}</th><th>{tr("Headcount")}</th><th>{tr("Why placed here")}</th></tr></thead><tbody>
          {all.map((c) => <tr key={c.key} className={c.tier === 0 ? "us" : ""}><td>{c.tier === 0 ? c.name : <ProfileLink c={c} />}</td><td>{tr(ORIGIN[c.origin])}</td><td>{c.tier === 0 ? tr("Us") : c.tier}</td><td className="num">{c.pos?.x}</td><td className="num">{c.pos?.y}</td><td>{c.headcount?.value}</td><td className="small">{c.pos?.why}</td></tr>)}
        </tbody></table></div></details>
      </div>
      <div className="ct-g2">
        <div className="card"><h3 className="ct-h">{tr("Who overlaps with us most")}</h3>
          <p className="xs muted">{tr("Score out of 100: same buyers (30), same way of selling (30), same markets (20), similar size (20). It says who is likely to meet us in a deal, not who is better.")}</p>
          <div className="ct-keys"><span><i className="ct-dot k1" />{tr("Same buyers")}</span><span><i className="ct-dot k2" />{tr("Same way of selling")}</span><span><i className="ct-dot k3" />{tr("Same markets")}</span><span><i className="ct-dot k4" />{tr("Similar size")}</span></div>
          {ov.map((c: Comp) => { const o = c.overlap; return <div key={c.key} className="ct-ovr"><ProfileLink c={c}><Dot c={c} /> {c.name}</ProfileLink>
            <span className="bar" role="img" aria-label={tr("Same buyers {} of 30, same way of selling {} of 30, same markets {} of 20, similar size {} of 20", o.buyers, o.selling, o.markets, o.size)}>
              <i className="k1" style={{ width: `${o.buyers}%` }} /><i className="k2" style={{ width: `${o.selling}%` }} /><i className="k3" style={{ width: `${o.markets}%` }} /><i className="k4" style={{ width: `${o.size}%` }} /></span>
            <b className="num">{o.total}</b></div>; })}
        </div>
        <div className="card"><h3 className="ct-h">{tr("Reading the map")}</h3>{d.landscapeNotes.map((t: string, i: number) => <p key={i} className="small">{t}</p>)}</div>
      </div>
      <div className="card"><h3 className="ct-h">{tr("What the buyer can do instead of hiring any of us")}</h3>
        <div className="ct-g4">{d.alternatives.map((a: any) => <div key={a.name} className="ct-alt"><b>{a.name}</b><p className="small">{a.evidence}</p><Src f={{ source: a.source }} asOf={asOf} /></div>)}</div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ Compare */
function Compare({ d, byKey, asOf }: Ctx) {
  const [pick, setPick] = useState<string[]>(d.defaultCompare || []);
  const cols: Comp[] = [d.us, ...pick.map((k) => byKey[k]).filter(Boolean)];
  const cell = (f: Fact) => <div className="ct-cell"><span className="v"><Val f={f} /></span><Src f={f} asOf={asOf} /></div>;
  const list = (a?: string[]) => (a && a.length ? <span className="small">{a.join(", ")}</span> : <span className="muted">{tr("Not found")}</span>);
  const rows: [string, (c: Comp) => ReactNode][] = [
    ["Origin and HQ", (c) => <><Dot c={c} /> {c.hq}</>], ["Founded", (c) => c.founded], ["Ownership", (c) => cell(c.ownership)], ["Headcount", (c) => cell(c.headcount)],
    ["Revenue", (c) => cell(c.revenue)], ["Homepage headline", (c) => <div className="ct-cell"><span className="q">{c.hero?.value ? `“${c.hero.value}”` : tr("Not found")}</span><Src f={c.hero} asOf={asOf} /></div>],
    ["In one line", (c) => <span className="small">{c.oneLiner}</span>], ["Archetype", (c) => c.archetype],
    ["What they sell", (c) => <span className="small">{(c.offers || []).map((o: string) => tr(OFFERS[o])).join("; ") || tr("Not found")}</span>], ["Engagement models", (c) => list(c.models)],
    ["AI in the offer", (c) => <div className="ct-cell"><Meter n={c.ai?.score ?? 0} /><span className="small">{c.ai?.value}</span><Src f={c.ai} asOf={asOf} /></div>],
    ["Financial services", (c) => <div className="ct-cell"><Meter n={c.fs?.score ?? 0} /><span className="small">{c.fs?.note}</span><Src f={c.fs} asOf={asOf} /></div>],
    ["Markets", (c) => <Markets c={c} />],
    ["Clutch", (c) => (c.clutch ? <div className="ct-cell"><span className="v">{tr("{} from {} reviews", c.clutch.rating ?? "–", c.clutch.reviews ?? "–")}</span><span className="xs muted">{c.clutch.rate}{c.clutch.min ? ` · min ${c.clutch.min}` : ""}{c.clutch.latest ? ` · ${tr("last review")} ${fmtD(c.clutch.latest)}` : ""}</span><Src f={c.clutch} asOf={asOf} /></div> : <span className="muted">{tr("Not found")}</span>)],
    ["Certifications and partners", (c) => list(c.certs)], ["Named clients", (c) => list(c.clients)],
    ["Open roles on justjoin.it", (c) => (c.jobs ? cell(c.jobs) : <span className="muted">{tr("Not counted")}</span>)],
    ["Where we meet them", (c) => (c.tier === 0 ? "–" : <span className="small">{tr("{} accounts · {} public awards", c.presence.accounts, c.presence.awards)}</span>)],
    ["Latest moves", (c) => (c.moves.length ? c.moves.slice(0, 3).map((m: Move) => <div key={m.id} className="small"><span className="ct-age">{fmtD(m.date)}</span> {m.headline}</div>) : <span className="muted">{tr("None recorded")}</span>)],
  ];
  const groups: Record<string, string> = { "Origin and HQ": "Scale", "Homepage headline": "Positioning", "What they sell": "Offer", "Financial services": "Focus", Clutch: "Proof and price", "Open roles on justjoin.it": "Momentum" };
  return (
    <section className="ct-view">
      <p className="sub">{tr("The Codest against up to four competitors, field by field. Each value shows where it came from and how old it is. Pick the firms you meet in a deal.")}</p>
      <div className="card"><div className="ct-label">{tr("Compare with ({} of 4)", pick.length)}</div><div className="ct-picker">
        {d.competitors.slice().sort((a: Comp, b: Comp) => a.tier - b.tier || a.name.localeCompare(b.name)).map((c: Comp) => { const on = pick.includes(c.key);
          return <button key={c.key} type="button" aria-pressed={on} disabled={!on && pick.length >= 4} onClick={() => setPick(on ? pick.filter((k) => k !== c.key) : [...pick, c.key])}><Dot c={c} />{c.name}</button>; })}
      </div></div>
      <div className="card flush"><div className="ct-scroll"><table className="ct-cmp"><thead><tr><th className="rowh" />{cols.map((c) => <th key={c.key} className={c.tier === 0 ? "us" : ""}>{c.name}<div className="xs muted">{tr(TIER[c.tier])}</div></th>)}</tr></thead><tbody>
        {rows.map(([label, fn]) => <FragmentRows key={label} group={groups[label]} n={cols.length + 1}><tr><th className="rowh">{tr(label)}</th>{cols.map((c) => <td key={c.key} className={c.tier === 0 ? "us" : ""}>{fn(c)}</td>)}</tr></FragmentRows>)}
      </tbody></table></div></div>
    </section>
  );
}
const FragmentRows = ({ group, n, children }: { group?: string; n: number; children: ReactNode }) => <>{group && <tr className="grp"><th colSpan={n}>{tr(group)}</th></tr>}{children}</>;

/* ------------------------------------------------------------------ Profiles */
function Profiles({ d, byKey, reload, asOf, selected }: Ctx & { selected?: string }) {
  const nav = useNavigate();
  const [q, setQ] = useState(""); const [tier, setTier] = useState("all");
  const c = (selected && byKey[selected]) || d.competitors.slice().sort((a: Comp, b: Comp) => b.overlap.total - a.overlap.total)[0];
  const list = d.competitors.filter((x: Comp) => (tier === "all" || String(x.tier) === tier) && (!q || x.name.toLowerCase().includes(q.toLowerCase())))
    .sort((a: Comp, b: Comp) => a.tier - b.tier || a.name.localeCompare(b.name));
  return (
    <section className="ct-view">
      <div className="ct-prof">
        <div className="ct-stack">
          <div className="ct-filters"><input type="search" aria-label={tr("Find a company")} placeholder={tr("Find a company")} value={q} onChange={(e) => setQ(e.target.value)} />
            <span className="ct-seg">{["all", "1", "2", "3"].map((t) => <button key={t} type="button" aria-pressed={tier === t} onClick={() => setTier(t)}>{t === "all" ? tr("All") : "T" + t}</button>)}</span></div>
          <div className="ct-plist">{[d.us, ...list].map((x: Comp) => <button key={x.key} type="button" aria-current={x.key === c.key} onClick={() => nav(`/competitors/profile/${x.key}`)}>
            <Dot c={x} /><span className="pn">{x.name}</span><span className="xs muted">{x.tier === 0 ? tr("Us") : "T" + x.tier}</span><span className="pm">{x.archetype}</span></button>)}</div>
          {d.can?.manage && <AddFirm reload={reload} />}
        </div>
        <Profile key={c.key} c={c} d={d} asOf={asOf} reload={reload} />
      </div>
    </section>
  );
}

function Profile({ c, d, asOf, reload }: { c: Comp; d: any; asOf: string; reload: () => void }) {
  const [pres, setPres] = useState<any>(null);
  useEffect(() => { if (c.tier !== 0) api(`/api/competitors/${c.key}/presence`).then(setPres).catch(() => setPres({ error: true })); }, [c.key, c.tier]);
  const fact = (label: string, f: Fact) => <div className="ct-fact"><span className="ct-label">{tr(label)}</span><span className="fv"><Val f={f} /></span><Src f={f} asOf={asOf} /></div>;
  const ul = (a?: string[]) => (a && a.length ? <ul className="ct-ul small">{a.map((x, i) => <li key={i}>{x}</li>)}</ul> : <p className="small muted">{tr("Not enough evidence yet.")}</p>);
  const hasCard = c.tier === 1 || [c.strengths, c.weaknesses, c.howWeWin, c.questions].some((a) => a && a.length);
  const h = c.h2h;
  return (
    <div className="ct-stack">
      <div className="card">
        <div className="ct-phead"><div>
          <div className="ct-row">{c.tier === 0 ? <span className="ct-chip">{tr("Us")}</span> : <TierChip c={c} />}<span className="ct-chip dim"><Dot c={c} /> {tr(ORIGIN[c.origin])}</span>{c.archetype && <span className="ct-chip dim">{c.archetype}</span>}</div>
          <h2>{c.name}</h2>
          <p className="small"><a href={c.url} target="_blank" rel="noopener noreferrer">{host(c.url)}</a> · {c.hq}{c.founded ? ` · ${tr("founded")} ${c.founded}` : ""}</p>
        </div>{c.overlap && <div className="ct-score"><span className="ct-label">{tr("Overlap with us")}</span><b>{c.overlap.total}</b><span className="xs muted">{tr("out of 100")}</span></div>}</div>
        <p className="ct-one">{c.oneLiner}</p>
        {c.relevance && <p className="small muted">{c.relevance}</p>}
        {c.tierWhy && <p className="small"><b>{tr("Why tier 1:")}</b> {c.tierWhy}</p>}
        {c.edited_by && <p className="xs muted">{tr("Last changed by {}", c.edited_by)}</p>}
        {c.hero?.value && <div className="mt-2"><div className="ct-label">{tr("Homepage headline, word for word")}</div><blockquote className="ct-hero">“{c.hero.value}”</blockquote><Src f={c.hero} asOf={asOf} /></div>}
      </div>
      <div className="ct-facts">{fact("Ownership", c.ownership)}{fact("Headcount", c.headcount)}{fact("Revenue", c.revenue)}
        {fact("Clutch", c.clutch ? { value: `${c.clutch.rating ?? "–"} · ${tr("{} reviews", c.clutch.reviews ?? "–")} · ${c.clutch.rate || ""}`, source: c.clutch.source, asOf: c.clutch.asOf, grade: c.clutch.grade } : null)}
        {fact("Open roles on justjoin.it", c.jobs || { value: tr("Not counted") })}</div>
      {d.can?.manage && c.tier !== 0 && <EditFacts c={c} reload={reload} />}
      <div className="ct-g2">
        <div className="card"><h3 className="ct-h">{tr("Offer")}</h3>
          <div className="ct-label">{tr("Services")}</div><p className="small">{(c.services || []).join(", ") || tr("Not found")}</p>
          <div className="ct-label">{tr("Engagement models")}</div><p className="small">{(c.models || []).join(", ") || tr("Not found")}</p>
          {c.entryOffer && <><div className="ct-label">{tr("First step they sell")}</div><p className="small">{c.entryOffer}</p></>}
          <div className="ct-label">{tr("AI")} <Meter n={c.ai?.score ?? 0} /></div><p className="small">{c.ai?.value || tr("Not found")}</p><Src f={c.ai} asOf={asOf} /></div>
        <div className="card"><h3 className="ct-h">{tr("Focus")}</h3>
          <div className="ct-label">{tr("Financial services")} <Meter n={c.fs?.score ?? 0} /></div><p className="small">{c.fs?.note || tr("Not found")}</p><Src f={c.fs} asOf={asOf} />
          <div className="ct-label mt-2">{tr("Markets")}</div><Markets c={c} />{c.offices && <p className="small">{c.offices}</p>}
          <div className="ct-label">{tr("Named clients")}</div><p className="small">{(c.clients || []).join(", ") || tr("Not found")}</p>
          <div className="ct-label">{tr("Certifications and partners")}</div><p className="small">{(c.certs || []).join(", ") || tr("Not found")}</p></div>
      </div>
      {c.tier !== 0 && <div className="card"><h3 className="ct-h">{tr("Where we meet them")}</h3>
        {!pres ? <Skeleton rows={2} /> : pres.error ? <p className="small muted">{tr("Could not load this list.")}</p> : <>
          <p className="xs muted">{tr("Accounts you can see where account research names them as a supplier, and public IT contracts they won (TED award notices).")}</p>
          {pres.accounts.length === 0 && pres.awards.length === 0 && <p className="small muted">{tr("Not seen at any of your accounts or in public contract awards yet. This fills in as account research runs.")}</p>}
          {pres.accounts.length > 0 && <table className="ct-data"><thead><tr><th>{tr("Account")}</th><th>{tr("Relationship")}</th><th>{tr("Evidence")}</th></tr></thead><tbody>
            {pres.accounts.map((a: any) => <tr key={a.organization_id}><td><Link to={`/accounts/${a.organization_id}`}>{a.name}</Link></td><td className="small">{a.relationship}{a.role ? ` · ${a.role}` : ""}</td>
              <td className="small">{a.quote ? <q>{a.quote}</q> : null} {a.source && <a href={a.source} target="_blank" rel="noopener noreferrer">{host(a.source)}</a>} {a.date && <span className="ct-age">{fmtD(a.date)}</span>}</td></tr>)}</tbody></table>}
          {pres.awards.length > 0 && <table className="ct-data mt-2"><thead><tr><th>{tr("Public award")}</th><th>{tr("Awarded")}</th><th>{tr("Expected end")}</th></tr></thead><tbody>
            {pres.awards.slice(0, 20).map((a: any) => <tr key={a.notice_id}><td className="small">{a.url ? <a href={a.url} target="_blank" rel="noopener noreferrer">{a.buyer}</a> : a.buyer} ({a.country})</td><td className="small">{fmtD(a.awarded_on)}</td>
              <td className="small">{fmtD(a.expected_end)}{a.end_basis === "assumed" ? ` (${tr("estimate")})` : ""}</td></tr>)}</tbody></table>}
        </>}
      </div>}
      {c.tier !== 0 && hasCard && <div className="card"><h3 className="ct-h">{tr("Battlecard")}</h3>
        {c.quickDismiss && <><div className="ct-label">{tr("If the buyer mentions them")}</div><p className="ct-so">{c.quickDismiss}</p></>}
        <div className="ct-bc">
          <div><div className="ct-label">{tr("Why buyers pick them")}</div>{ul(c.strengths)}</div><div><div className="ct-label">{tr("Where they are weaker")}</div>{ul(c.weaknesses)}</div>
          <div className="win"><div className="ct-label">{tr("How we compete")}</div>{ul(c.howWeWin)}</div><div><div className="ct-label">{tr("Questions to ask the buyer")}</div>{ul(c.questions)}</div>
        </div>
        <div className="ct-label mt-2">{tr("Track record against them")}</div>
        <p className="small">{!h ? tr("No Pipedrive deal names them yet. Record it on the Head-to-head tab.") : h.win_rate == null
          ? tr("Met in {} deals: {} won, {} lost. A win rate shows after {} decided deals.", h.met, h.won, h.lost, d.headToHead.min_decided)
          : tr("Met in {} deals: {} won, {} lost, win rate {}%.", h.met, h.won, h.lost, Math.round(h.win_rate * 100))}</p>
        <p className="ct-note">{tr("Internal draft for sellers. Before any of this reaches a prospect, check it against the brand claims register: only cleared claims, no client names, no unapproved numbers.")}</p>
      </div>}
      <div className="card"><h3 className="ct-h">{tr("Moves")}</h3>
        {c.moves.length ? c.moves.map((m: Move) => <MoveRow key={m.id} m={m} asOf={asOf} types={d.moveTypes} />) : <p className="small muted">{tr("No dated moves found in the last 18 months.")}</p>}
        {c.tier !== 0 && <SuggestMove c={c} d={d} reload={reload} />}
      </div>
    </div>
  );
}

function Form({ title, children, onSubmit, busy, msg }: { title: string; children: ReactNode; onSubmit: () => void; busy: boolean; msg: string }) {
  return <details className="ct-form"><summary>{tr(title)}</summary>
    <form onSubmit={(e) => { e.preventDefault(); onSubmit(); }}>{children}<div className="ct-row"><button type="submit" disabled={busy}>{tr("Save")}</button>{msg && <span className="small" role="status">{msg}</span>}</div></form>
  </details>;
}
const errText = (e: any) => String(e?.message || e);

function SuggestMove({ c, d, reload }: { c: Comp; d: any; reload: () => void }) {
  const [f, setF] = useState<any>({ date: "", type: "offer", headline: "", source: "", grade: "official", impact: 2, sowhat: "", confirm: false });
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState("");
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const save = () => { setBusy(true); setMsg(""); post(`/api/competitors/${c.key}/moves`, { ...f, impact: Number(f.impact), sowhat: f.sowhat || null })
    .then((r: any) => { setMsg(r.status === "confirmed" ? tr("Saved and shown.") : tr("Saved. It shows once a reviewer confirms it.")); reload(); }).catch((e) => setMsg(errText(e))).finally(() => setBusy(false)); };
  return <Form title={d.can?.manage ? "Add a move" : "Suggest a move"} onSubmit={save} busy={busy} msg={msg}>
    <div className="ct-fgrid">
      <label>{tr("Date (YYYY, YYYY-MM or YYYY-MM-DD)")}<input required pattern="\d{4}(-\d{2}(-\d{2})?)?" value={f.date} onChange={set("date")} /></label>
      <label>{tr("Type")}<select value={f.type} onChange={set("type")}>{Object.entries(d.moveTypes).map(([k, v]: any) => <option key={k} value={k}>{tr(v.label)}</option>)}</select></label>
      <label className="wide">{tr("What happened")}<input required minLength={5} maxLength={300} value={f.headline} onChange={set("headline")} /></label>
      <label className="wide">{tr("Source link")}<input required type="url" pattern="https://.*" value={f.source} onChange={set("source")} /></label>
      <label>{tr("Grade")}<select value={f.grade} onChange={set("grade")}>{Object.keys(GRADE).map((g) => <option key={g} value={g}>{tr(GRADE[g])}</option>)}</select></label>
      <label>{tr("Impact on us")}<select value={f.impact} onChange={set("impact")}><option value={3}>{tr("High")}</option><option value={2}>{tr("Medium")}</option><option value={1}>{tr("Low")}</option></select></label>
      <label className="wide">{tr("So what for us (needed for medium and high)")}<input maxLength={1000} value={f.sowhat} onChange={set("sowhat")} /></label>
      {d.can?.manage && <label className="check"><input type="checkbox" checked={f.confirm} onChange={set("confirm")} /> {tr("Confirm now (skip review)")}</label>}
    </div>
  </Form>;
}

function EditFacts({ c, reload }: { c: Comp; reload: () => void }) {
  const fields: [string, string][] = [["headcount", "Headcount"], ["revenue", "Revenue"], ["hero", "Homepage headline"], ["jobs", "Open roles"], ["ownership", "Ownership"]];
  const [field, setField] = useState("headcount");
  const cur = c[field] || {};
  const [f, setF] = useState<any>({});
  useEffect(() => { setF({ value: cur.value ?? "", n: cur.n ?? "", source: cur.source ?? "", asOf: "", grade: cur.grade || "official", trend: cur.trend ?? "" }); }, [field, c.key]);   // eslint-disable-line react-hooks/exhaustive-deps
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState("");
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const save = () => {
    const body: any = { value: f.value, source: f.source, asOf: f.asOf, grade: f.grade };
    if (field === "headcount" || field === "jobs") body.n = f.n === "" ? null : Number(f.n);
    if (field === "revenue" && f.trend) body.trend = f.trend;
    setBusy(true); setMsg("");
    patch(`/api/competitors/${c.key}`, { [field]: body }).then(() => { setMsg(tr("Saved.")); reload(); }).catch((e) => setMsg(errText(e))).finally(() => setBusy(false));
  };
  return <Form title="Update a fact" onSubmit={save} busy={busy} msg={msg}>
    <div className="ct-fgrid">
      <label>{tr("Fact")}<select value={field} onChange={(e) => setField(e.target.value)}>{fields.map(([k, l]) => <option key={k} value={k}>{tr(l)}</option>)}</select></label>
      <label className="wide">{tr("Value, as the source says it")}<input required value={f.value ?? ""} onChange={set("value")} /></label>
      {(field === "headcount" || field === "jobs") && <label>{tr("Number")}<input type="number" min={0} value={f.n ?? ""} onChange={set("n")} /></label>}
      {field === "revenue" && <label>{tr("Trend")}<select value={f.trend ?? ""} onChange={set("trend")}><option value="">–</option><option value="up">{tr("Growing")}</option><option value="flat">{tr("Flat")}</option><option value="down">{tr("Shrinking")}</option></select></label>}
      <label className="wide">{tr("Source link")}<input required type="url" pattern="https://.*" value={f.source ?? ""} onChange={set("source")} /></label>
      <label>{tr("Date of the source")}<input required placeholder="2026-12" pattern="\d{4}(-\d{2}(-\d{2})?)?" value={f.asOf ?? ""} onChange={set("asOf")} /></label>
      <label>{tr("Grade")}<select value={f.grade} onChange={set("grade")}>{Object.keys(GRADE).map((g) => <option key={g} value={g}>{tr(GRADE[g])}</option>)}</select></label>
    </div>
  </Form>;
}

function AddFirm({ reload }: { reload: () => void }) {
  const nav = useNavigate();
  const [f, setF] = useState({ name: "", origin: "PL", url: "" }); const [busy, setBusy] = useState(false); const [msg, setMsg] = useState("");
  const save = () => { setBusy(true); setMsg(""); post("/api/competitors", f).then((r: any) => { reload(); nav(`/competitors/profile/${r.key}`); }).catch((e) => setMsg(errText(e))).finally(() => setBusy(false)); };
  return <Form title="Track a new firm" onSubmit={save} busy={busy} msg={msg}>
    <div className="ct-fgrid">
      <label className="wide">{tr("Name")}<input required minLength={2} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      <label>{tr("Origin")}<select value={f.origin} onChange={(e) => setF({ ...f, origin: e.target.value })}>{Object.entries(ORIGIN).map(([k, v]) => <option key={k} value={k}>{tr(v)}</option>)}</select></label>
      <label className="wide">{tr("Website")}<input required type="url" pattern="https://.*" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} /></label>
    </div>
  </Form>;
}

/* ------------------------------------------------------------------ Moves */
function Moves({ d, asOf }: Ctx) {
  const [imp, setImp] = useState(0); const [tier, setTier] = useState("all"); const [type, setType] = useState("all");
  const allMoves = d.competitors.flatMap((c: Comp) => c.moves.map((m: Move) => ({ ...m, c })));
  const types = [...new Set(allMoves.map((m: any) => m.type))] as string[];
  const shown = allMoves.filter((m: any) => m.impact >= imp && (tier === "all" || String(m.c.tier) === tier) && (type === "all" || m.type === type))
    .sort((a: any, b: any) => String(b.date).localeCompare(String(a.date)));
  return (
    <section className="ct-view">
      <p className="sub">{tr("Dated things competitors did, each with its source. Impact is about The Codest: high means it changes how we compete for banks, insurers and fintechs in our four markets. Anyone can suggest a move from a profile; a reviewer confirms it.")}</p>
      <div className="ct-filters">
        <span className="ct-label">{tr("Impact")}</span><span className="ct-seg">{[[0, "Any"], [2, "Medium and high"], [3, "High only"]].map(([v, l]) => <button key={v} type="button" aria-pressed={imp === v} onClick={() => setImp(v as number)}>{tr(l as string)}</button>)}</span>
        <span className="ct-label">{tr("Tier")}</span><span className="ct-seg">{["all", "1", "2", "3"].map((t) => <button key={t} type="button" aria-pressed={tier === t} onClick={() => setTier(t)}>{t === "all" ? tr("All") : t}</button>)}</span>
        <select aria-label={tr("Type of move")} value={type} onChange={(e) => setType(e.target.value)}><option value="all">{tr("Every type of move")}</option>{types.map((t) => <option key={t} value={t}>{tr(d.moveTypes[t]?.label || t)}</option>)}</select>
        <span className="small muted">{tr("{} moves", shown.length)}</span>
      </div>
      <div className="card">{shown.length ? shown.map((m: any) => <MoveRow key={m.id} m={m} c={m.c} asOf={asOf} types={d.moveTypes} />) : <p className="muted">{tr("No moves match these filters.")}</p>}</div>
    </section>
  );
}

/* ------------------------------------------------------------------ Patterns */
function Patterns({ d, asOf }: Ctx) {
  const bw = d.bellwethers as any[];
  const max = Math.max(1, ...bw.map((b) => Math.abs(b.growth)));
  const W = 640, rowH = 34, L = 170, R = 70, mid = L + (W - L - R) / 2, half = (W - L - R) / 2, H = bw.length * rowH + 30;
  const BANDS = ["$25–49/hr", "$50–99/hr", "$100–149/hr", "$150–199/hr"];
  const bandOf = (c: Comp) => { const m = String(c.clutch?.rate || "").match(/\$(\d+)/); if (!m) return "Not shown"; const n = +m[1]; return n < 50 ? BANDS[0] : n < 100 ? BANDS[1] : n < 150 ? BANDS[2] : BANDS[3]; };
  const bands = [...BANDS, "Not shown"].map((b) => ({ b, names: d.competitors.filter((c: Comp) => bandOf(c) === b).map((c: Comp) => c.name) }));
  const bmax = Math.max(1, ...bands.map((x) => x.names.length));
  return (
    <section className="ct-view">
      <p className="sub">{tr("What the market is doing, written as hypotheses we can check. Each indicator is counted live across the {} tracked firms.", d.competitors.length)}</p>
      <div className="card"><h3 className="ct-h">{tr("Market temperature: listed bellwethers")}</h3>
        <p className="xs muted">{tr("Year-on-year revenue growth in the latest reported period. Large listed firms report first, so they show demand before private Polish firms do.")}</p>
        <div className="ct-scroll"><svg className="ct-bw" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={tr("Revenue growth of listed bellwethers")}>
          <line className="axis" x1={mid} x2={mid} y1={6} y2={H - 22} />
          {bw.map((b, i) => { const y = 10 + i * rowH, w = (Math.abs(b.growth) / max) * half, x = b.growth >= 0 ? mid : mid - w;
            return <g key={b.name}><text x={L - 10} y={y + 14} textAnchor="end" className="nm">{b.name}</text><rect x={x} y={y + 3} width={Math.max(1, w)} height={16} className={b.growth >= 0 ? "pos" : "neg"}><title>{`${b.name}: ${b.growth}% (${b.period})`}</title></rect>
              <text x={b.growth >= 0 ? x + w + 6 : x - 6} y={y + 15} textAnchor={b.growth >= 0 ? "start" : "end"}>{b.growth > 0 ? "+" : ""}{b.growth}%</text></g>; })}
          <text x={mid} y={H - 6} textAnchor="middle">0%</text>
        </svg></div>
        <details className="ct-tbl"><summary>{tr("Show as table")}</summary><table className="ct-data"><thead><tr><th>{tr("Company")}</th><th>{tr("Period")}</th><th>{tr("Growth")}</th><th>{tr("Note and source")}</th></tr></thead><tbody>
          {bw.map((b) => <tr key={b.name}><td>{b.name}</td><td>{b.period}</td><td className="num">{b.growth > 0 ? "+" : ""}{b.growth}%</td><td className="small">{b.note} <a href={b.source} target="_blank" rel="noopener noreferrer">{host(b.source)}</a></td></tr>)}</tbody></table></details>
      </div>
      <div className="ct-g2">{d.patterns.map((p: any) => { const ind = p.indicator || {}; const pct = ind.of ? Math.round((ind.count / ind.of) * 100) : 0;
        return <div key={p.title} className="card ct-pat"><div className="ct-row spread"><h3 className="ct-h plain">{p.title}</h3><span className={`ct-strength ${p.strength}`}>{tr(({ strong: "Strong evidence", emerging: "Emerging", weak: "Weak signal" } as any)[p.strength] || p.strength)}</span></div>
          <p className="stmt">{p.statement}</p>
          {ind.of ? <div><div className="ct-row spread"><span className="small">{ind.label}</span><b className="small">{tr("{} of {}", ind.count, ind.of)}</b></div><div className="ct-bar" role="img" aria-label={tr("{} of {}", ind.count, ind.of)}><i style={{ width: `${pct}%` }} /></div>
            {ind.names && <p className="xs muted">{ind.names.join(", ")}</p>}</div> : null}
          <ul className="ct-ul small">{p.evidence.map((e: any, i: number) => <li key={i}>{e.text} {e.source && <a className="xs" href={e.source} target="_blank" rel="noopener noreferrer">{host(e.source)}</a>}</li>)}</ul>
          <p className="ct-so"><b>{tr("For us:")}</b> {p.meaning}</p><p className="xs muted"><b>{tr("Watch:")}</b> {p.watch}</p></div>; })}</div>
      <div className="ct-g3">
        <div className="card"><h3 className="ct-h">{tr("What buyers say")}</h3><ul className="ct-ul small">{d.buyers.map((b: any, i: number) => <li key={i}>{b.text} <a className="xs" href={b.source} target="_blank" rel="noopener noreferrer">{host(b.source)}</a></li>)}</ul></div>
        <div className="card"><h3 className="ct-h">{tr("Price")}</h3><p className="xs muted">{tr("Hourly band each tracked firm lists on Clutch (hover a row for the names), then public rate benchmarks.")}</p>
          {bands.map((x) => <div key={x.b} className="ct-band" title={x.names.join(", ")}><span className="small">{tr(x.b)}{bandOf(d.us) === x.b ? ` · ${tr("us")}` : ""}</span><span className="track"><i style={{ width: `${(x.names.length / bmax) * 100}%` }} className={x.b === "Not shown" ? "none" : ""} /></span><b className="small num">{x.names.length}</b></div>)}
          <ul className="ct-ul small mt-2">{d.rates.map((r: any, i: number) => <li key={i}>{r.text} <a className="xs" href={r.source} target="_blank" rel="noopener noreferrer">{host(r.source)}</a></li>)}</ul></div>
        <div className="card"><h3 className="ct-h">{tr("Deals in the market")}</h3>{d.deals.slice().sort((a: any, b: any) => b.date.localeCompare(a.date)).map((x: any, i: number) =>
          <div key={i} className="ct-mv"><span className="when">{fmtD(x.date)}</span><div className="small">{x.text} <Src f={{ source: x.source }} asOf={asOf} /></div></div>)}</div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ Head-to-head */
function HeadToHead({ d, byKey, reload }: Ctx) {
  const h = d.headToHead;
  const rows = Object.entries(h.by_competitor as Record<string, any>).filter(([k]) => byKey[k]).sort((a, b) => b[1].met - a[1].met);
  return (
    <section className="ct-view">
      <p className="sub">{tr("Where we actually meet each competitor, and how often we win. Won and lost follow the deal's status in Pipedrive. A win rate shows once {} deals against a firm are decided.", h.min_decided)}</p>
      {rows.length === 0 ? <div className="empty"><b>{tr("No deal names a competitor yet")}</b>{tr("Record competitors on your deals below, or ask an administrator to connect the Pipedrive deal field (RI_PIPEDRIVE_COMPETITOR_FIELD).")}</div> :
        <div className="card flush"><div className="ct-scroll"><table className="ct-data"><thead><tr><th>{tr("Competitor")}</th><th>{tr("Deals met")}</th><th>{tr("Share of our deals")}</th><th>{tr("Won")}</th><th>{tr("Lost")}</th><th>{tr("Win rate")}</th><th>{tr("Threat index")}</th><th>{tr("Top lost reason")}</th><th>{tr("At your accounts")}</th><th>{tr("Public awards")}</th></tr></thead><tbody>
          {rows.map(([k, s]) => { const c = byKey[k]; return <tr key={k}><td><ProfileLink c={c} /></td><td className="num">{s.met}</td><td className="num">{Math.round(s.share * 100)}%</td><td className="num">{s.won}</td><td className="num">{s.lost}</td>
            <td className="num">{s.win_rate == null ? <span className="muted">{tr("Too few")}</span> : `${Math.round(s.win_rate * 100)}%`}</td><td className="num">{s.threat ?? "–"}</td><td>{s.top_lost_reason || "–"}</td><td className="num">{c.presence.accounts}</td><td className="num">{c.presence.awards}</td></tr>; })}
        </tbody></table></div></div>}
      <p className="xs muted">{tr("Threat index is a firm's share of our competitive deals divided by our win rate against it: a firm we meet often and beat rarely scores high. Once there is enough data, tier 1 should follow this index.")}</p>
      {d.can?.record && <RecordDeals d={d} reload={reload} />}
    </section>
  );
}

function RecordDeals({ d, reload }: { d: any; reload: () => void }) {
  const [q, setQ] = useState(""); const [items, setItems] = useState<any[] | null>(null); const [err, setErr] = useState<unknown>(null); const [n, setN] = useState(0);
  const [pick, setPick] = useState<Record<number, { competitor: string; role: string; reason: string }>>({});
  useEffect(() => { const t = setTimeout(() => api(`/api/competitors/deals${q ? `?q=${encodeURIComponent(q)}` : ""}`).then((r) => setItems(r.items)).catch(setErr), q ? 300 : 0); return () => clearTimeout(t); }, [q, n]);
  const opts = d.competitors.slice().sort((a: Comp, b: Comp) => a.name.localeCompare(b.name));
  const save = (id: number) => { const p = pick[id]; if (!p?.competitor) return; put(`/api/competitors/deals/${id}`, { competitor: p.competitor, role: p.role || "unknown", reason: p.reason || null }).then(() => { setN((x) => x + 1); reload(); }).catch(setErr); };
  const remove = (id: number, k: string) => api(`/api/competitors/deals/${id}/${k}`, { method: "DELETE" }).then(() => { setN((x) => x + 1); reload(); }).catch(setErr);
  return (
    <div className="card"><h3 className="ct-h">{tr("Record who we met on a deal")}</h3>
      <p className="xs muted">{tr("Your deals from Pipedrive. Name every competitor the buyer mentioned; mark who won when you know.")}</p>
      <div className="ct-filters"><input type="search" aria-label={tr("Find a deal")} placeholder={tr("Find a deal")} value={q} onChange={(e) => setQ(e.target.value)} /></div>
      {err ? <Err e={err} /> : !items ? <Skeleton rows={2} /> : items.length === 0 ? <p className="small muted">{tr("No deals found in your accounts.")}</p> :
        <div className="ct-scroll"><table className="ct-data"><thead><tr><th>{tr("Deal")}</th><th>{tr("Named so far")}</th><th>{tr("Add a competitor")}</th></tr></thead><tbody>
          {items.map((x) => { const p = pick[x.id] || { competitor: "", role: "unknown", reason: "" }; const setP = (k: string, v: string) => setPick({ ...pick, [x.id]: { ...p, [k]: v } });
            return <tr key={x.id}><td><b>{x.title}</b><div className="xs muted">{x.stage || ""}{x.status ? ` · ${x.status}` : ""}</div></td>
              <td className="small">{x.competitors.length ? x.competitors.map((k: string) => <span key={k} className="ct-chip">{d.competitors.find((c: Comp) => c.key === k)?.name || k} <button type="button" className="ct-x" aria-label={tr("Remove")} onClick={() => remove(x.id, k)}>×</button></span>) : <span className="muted">–</span>}</td>
              <td><div className="ct-row">
                <select aria-label={tr("Competitor")} value={p.competitor} onChange={(e) => setP("competitor", e.target.value)}><option value="">{tr("Choose a firm")}</option>{opts.map((c: Comp) => <option key={c.key} value={c.key}>{c.name}</option>)}</select>
                <select aria-label={tr("Role")} value={p.role} onChange={(e) => setP("role", e.target.value)}>{Object.entries(ROLE).map(([k, v]) => <option key={k} value={k}>{tr(v)}</option>)}</select>
                {p.role === "lost_to" && <input aria-label={tr("Why we lost")} placeholder={tr("Why we lost")} value={p.reason} onChange={(e) => setP("reason", e.target.value)} maxLength={300} />}
                <button type="button" disabled={!p.competitor} onClick={() => save(x.id)}>{tr("Add")}</button></div></td></tr>; })}
        </tbody></table></div>}
    </div>
  );
}

/* ------------------------------------------------------------------ Review (competitors.manage) */
function Review({ d, reload }: Ctx) {
  const [items, setItems] = useState<any[] | null>(null); const [err, setErr] = useState<unknown>(null); const [n, setN] = useState(0);
  const [sw, setSw] = useState<Record<string, string>>({}); const [msg, setMsg] = useState<Record<string, string>>({});
  useEffect(() => { api("/api/competitors/moves/waiting").then((r) => setItems(r.items)).catch(setErr); }, [n]);
  const act = (m: any, status: string) => post(`/api/competitors/moves/${m.id}/review`, { status, sowhat: sw[m.id] ?? m.sowhat ?? null })
    .then(() => { setN((x) => x + 1); reload(); }).catch((e) => setMsg({ ...msg, [m.id]: errText(e) }));
  return (
    <section className="ct-view">
      <p className="sub">{tr("Moves suggested by sellers or found by collection wait here. Confirm what is true and matters, with a “So what” for medium and high impact; dismiss the rest.")}</p>
      {err ? <Err e={err} /> : !items ? <Skeleton /> : items.length === 0 ? <div className="empty"><b>{tr("Nothing waiting")}</b>{tr("New suggestions appear here.")}</div> :
        <div className="card">{items.map((m) => <div key={m.id} className="ct-mv"><span className="when">{fmtD(m.date)}</span><div>
          <div className="ct-row"><b>{m.name}</b><Imp n={m.impact} /><span className="ct-chip">{tr(d.moveTypes[m.type]?.label || m.type)}</span><span className="xs muted">{tr("from {}", m.added_by)}</span></div>
          <div className="what">{m.headline}</div><Src f={{ source: m.source, grade: m.grade }} asOf={d.asOf} />
          <label className="small ct-block">{tr("So what for us")}<input value={sw[m.id] ?? m.sowhat ?? ""} onChange={(e) => setSw({ ...sw, [m.id]: e.target.value })} maxLength={1000} /></label>
          <div className="ct-row"><button type="button" onClick={() => act(m, "confirmed")}>{tr("Confirm")}</button><button type="button" className="secondary" onClick={() => act(m, "dismissed")}>{tr("Dismiss")}</button>{msg[m.id] && <span className="small" role="alert">{msg[m.id]}</span>}</div>
        </div></div>)}</div>}
    </section>
  );
}

/* ------------------------------------------------------------------ Method */
function Method({ d }: Ctx) {
  const M = d.method || {};
  const rub = (rows?: string[][]) => <table className="ct-rub"><tbody>{(rows || []).map((r, i) => <tr key={i}><td>{r[0]}</td><td>{r[1]}</td></tr>)}</tbody></table>;
  const lst = (rows?: string[]) => <ul className="ct-ul small">{(rows || []).map((r, i) => <li key={i}>{r}</li>)}</ul>;
  return (
    <section className="ct-view">
      <p className="sub">{tr("How companies are chosen, scored and kept current, so anyone can check a number or challenge a placement.")}</p>
      <div className="card"><h3 className="ct-h">{tr("Questions this tracker answers")}</h3>{rub(M.questions)}</div>
      <div className="ct-g2">
        <div className="card"><h3 className="ct-h">{tr("Tiers")}</h3>{rub(M.tiers)}</div>
        <div className="card"><h3 className="ct-h">{tr("Evidence grades and freshness")}</h3>{lst(M.grades)}</div>
        <div className="card"><h3 className="ct-h">{tr("Financial-services focus (0–3)")}</h3>{rub(M.fs)}</div>
        <div className="card"><h3 className="ct-h">{tr("What they sell: people or outcomes")}</h3>{rub(M.model)}</div>
        <div className="card"><h3 className="ct-h">{tr("AI in the offer (0–3)")}</h3>{rub(M.ai)}</div>
        <div className="card"><h3 className="ct-h">{tr("Move impact")}</h3>{rub(M.impact)}</div>
        <div className="card"><h3 className="ct-h">{tr("Overlap with us (0–100)")}</h3>{lst(M.overlap)}</div>
        <div className="card"><h3 className="ct-h">{tr("Cadence and owners")}</h3>{rub(M.cadence)}</div>
        <div className="card"><h3 className="ct-h">{tr("How the data is collected")}</h3>{rub(M.collection)}</div>
        <div className="card"><h3 className="ct-h">{tr("Rules we keep")}</h3>{lst(M.ethics)}</div>
      </div>
      <div className="card"><h3 className="ct-h">{tr("Sources used in the seed")}</h3><p className="small">{M.sourcesNote}</p></div>
    </section>
  );
}
