import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { tr } from "../locale";

/* Comparison visuals for the competitor tracker. Every number comes from the server's scorecard (ri/competitors.py: scorecard, MEASURES),
   so the ranges, the duel and the grid always agree with each other. Missing values are shown as missing, never as zero. */

type Comp = any;
type Measure = { key: string; label: string; max: number | null; t1: { n: number; min: number | null; max: number | null; median: number | null } };

/** Measures where more is not better or worse, just different: worded "above/below" instead of "ahead/behind". */
const NEUTRAL = new Set(["headcount", "roles", "moves"]);
const SHORT: Record<string, string> = { headcount: "Headcount", reviews: "Clutch reviews", rating: "Clutch rating", fs: "Finance focus", ai: "AI offer",
  markets: "Our markets", proof: "Proof points", roles: "Open roles", moves: "Moves, 12 months" };
const HINT: Record<string, string> = { headcount: "People, latest public figure", reviews: "Reviews on Clutch", rating: "Average on Clutch, out of 5",
  fs: "0 to 3, from our rubric", ai: "0 to 3, from our rubric", markets: "UK, DACH, Nordics, Poland", proof: "Security, first step, outcomes, AI badge",
  roles: "Live on justjoin.it", moves: "Confirmed in the tracker" };

export function fmtV(key: string, v: number | null | undefined): string {
  if (v == null) return "–";
  if (key === "headcount") return v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : String(Math.round(v));
  if (key === "rating") return v.toFixed(1);
  if (key === "fs" || key === "ai") return `${v}/3`;
  if (key === "markets" || key === "proof") return `${v}/4`;
  return String(Math.round(v * 10) / 10);
}

/** Where a scale starts: zero, except Clutch ratings, which all sit between 4 and 5. */
const FLOOR: Record<string, number> = { rating: 4 };
/** Differences this small count as level: a tenth of a Clutch star is noise. */
const TOL: Record<string, number> = { rating: 0.1 };

function scaleMax(m: Measure, vals: (number | null | undefined)[]): number {
  if (m.max) return m.max;
  const top = Math.max(1, ...vals.filter((v): v is number => typeof v === "number"));
  return top * 1.08;
}
/** 0..1 position of a value on the measure's scale. */
const frac = (key: string, v: number, mx: number) => { const lo = FLOOR[key] ?? 0; return Math.max(0, Math.min(1, (v - lo) / Math.max(1e-9, mx - lo))); };

function verdict(key: string, us: number | null | undefined, med: number | null | undefined, span = 0): { cls: string; text: string } {
  if (us == null || med == null) return { cls: "na", text: tr("No data") };
  const neutral = NEUTRAL.has(key);
  if (Math.abs(us - med) <= Math.max(TOL[key] ?? 0, span * 0.03)) return { cls: "level", text: tr("Level") };       // a hair either side is not a lead
  if (us > med) return { cls: neutral ? "above" : "ahead", text: neutral ? tr("Above") : tr("Ahead") };
  if (us < med) return { cls: neutral ? "below" : "behind", text: neutral ? tr("Below") : tr("Behind") };
  return { cls: "level", text: tr("Level") };
}

/* ------------------------------------------------------------------ where we stand: our value inside the tier 1 range */
const standRows = (d: any): Measure[] => (d.measures || []).filter((m: Measure) => m.key !== "moves");
/** Ahead, level and behind the tier 1 median, counting only measures where more is better. */
const spanOf = (d: any, m: Measure) => scaleMax(m, [...d.competitors.filter((c: Comp) => c.tier === 1).map((c: Comp) => c.score?.[m.key]), d.us.score?.[m.key]]) - (FLOOR[m.key] ?? 0);
export function standTally(d: any): { ahead: number; level: number; behind: number; of: number } {
  const t = { ahead: 0, level: 0, behind: 0, of: 0 };
  standRows(d).filter((m) => !NEUTRAL.has(m.key)).forEach((m) => { const v = verdict(m.key, d.us.score?.[m.key], m.t1.median, spanOf(d, m)).cls as "ahead" | "level" | "behind" | "na"; if (v !== "na") { t[v] += 1; t.of += 1; } });
  return t;
}

export function StandRanges({ d }: { d: any }) {
  const t1: Comp[] = d.competitors.filter((c: Comp) => c.tier === 1);
  const rows = standRows(d);
  const tally = standTally(d);
  return (
    <div className="cv-stand">
      <div className="cv-keys" aria-hidden="true">
        <span><i className="cv-k us" />{tr("The Codest")}</span><span><i className="cv-k firm" />{tr("Each tier 1 firm")}</span>
        <span><i className="cv-k band" />{tr("Tier 1 range")}</span><span><i className="cv-k med" />{tr("Tier 1 median")}</span>
      </div>
      {rows.map((m) => {
        const usV = d.us.score?.[m.key];
        const vals = t1.map((c) => c.score?.[m.key]);
        const mx = scaleMax(m, [...vals, usV]);
        const x = (v: number) => `${frac(m.key, v, mx) * 100}%`;
        const v = verdict(m.key, usV, m.t1.median, mx - (FLOOR[m.key] ?? 0));
        const label = tr("{}: The Codest {}, tier 1 from {} to {}, median {}", tr(m.label), fmtV(m.key, usV), fmtV(m.key, m.t1.min), fmtV(m.key, m.t1.max), fmtV(m.key, m.t1.median));
        return (
          <div className="cv-srow" key={m.key}>
            <div className="cv-sl"><b>{tr(SHORT[m.key] || m.label)}</b><span>{tr(HINT[m.key] || "")}</span></div>
            <div className="cv-track" role="img" aria-label={label} title={label}>
              {m.t1.min != null && m.t1.max != null && <i className="band" style={{ left: x(m.t1.min), width: `calc(${x(m.t1.max)} - ${x(m.t1.min)})` }} />}
              {t1.map((c) => (typeof c.score?.[m.key] === "number" ? <i key={c.key} className="firm" style={{ left: x(c.score[m.key]) }} title={`${c.name}: ${fmtV(m.key, c.score[m.key])}`} /> : null))}
              {m.t1.median != null && <i className="med" style={{ left: x(m.t1.median) }} />}
              {typeof usV === "number" && <><i className="us" style={{ left: x(usV) }} /><b className={`usv${frac(m.key, usV, mx) > 0.85 ? " edge" : ""}`} style={{ left: x(usV) }}>{fmtV(m.key, usV)}</b></>}
            </div>
            <div className="cv-sv"><span className={`cv-verdict ${v.cls}`}>{v.text}</span><span className="xs muted">{tr("median {}", fmtV(m.key, m.t1.median))}</span></div>
          </div>
        );
      })}
      <p className="xs muted cv-foot">{tr("Against the median of tier 1: ahead on {}, level on {}, behind on {}. Headcount and open roles say how big and how busy a firm is, not how good.", tally.ahead, tally.level, tally.behind)}</p>
    </div>
  );
}

/* ------------------------------------------------------------------ head-to-head duel: us against one rival, measure by measure */
export function Duel({ d, rivalKey, setRival }: { d: any; rivalKey: string; setRival: (k: string) => void }) {
  const rivals: Comp[] = d.competitors.slice().sort((a: Comp, b: Comp) => a.tier - b.tier || b.overlap.total - a.overlap.total);
  const them: Comp = rivals.find((c) => c.key === rivalKey) || rivals[0];
  const us = d.us;
  let usWins = 0, themWins = 0;
  const rows = (d.measures as Measure[]).map((m) => {
    const a = us.score?.[m.key], b = them.score?.[m.key];
    const mx = m.max || Math.max(1, a ?? 0, b ?? 0) * 1.15;
    const neutral = NEUTRAL.has(m.key);
    const lead = a == null || b == null || Math.abs(a - b) <= Math.max(TOL[m.key] ?? 0, (mx - (FLOOR[m.key] ?? 0)) * 0.03) ? "" : a > b ? "us" : "them";
    if (!neutral && lead === "us") usWins += 1;
    if (!neutral && lead === "them") themWins += 1;
    return { m, a, b, mx, lead, neutral };
  });
  const flags = Object.entries(d.proofLabels || {}) as [string, string][];
  return (
    <div className="cv-duel">
      <div className="cv-dhead">
        <div className="cv-side us"><span className="ct-label">{tr("Us")}</span><b>{us.name}</b><span className="cv-tally">{usWins}</span></div>
        <div className="cv-vs"><span>vs</span>
          <select aria-label={tr("Rival to compare with")} value={them.key} onChange={(e) => setRival(e.target.value)}>
            {rivals.map((c) => <option key={c.key} value={c.key}>{c.name} · T{c.tier}</option>)}
          </select></div>
        <div className="cv-side them"><span className="ct-label">{tr("Tier {}", them.tier)} · {tr("overlap {}", them.overlap?.total)}</span><b><Link to={`/competitors/profile/${them.key}`}>{them.name}</Link></b><span className="cv-tally">{themWins}</span></div>
      </div>
      <p className="xs muted cv-dnote">{tr("The big number is how many measures each side leads on. Size, open roles and moves are shown but not counted: bigger is not better.")}</p>
      <div className="cv-drows">
        {rows.map(({ m, a, b, mx, lead, neutral }) => (
          <div className={`cv-drow${neutral ? " neutral" : ""}`} key={m.key}>
            <div className={`cv-dv us${lead === "us" ? " lead" : ""}`}>{fmtV(m.key, a)}</div>
            <div className="cv-dbar us"><i style={{ width: a == null ? 0 : `${Math.max(2, frac(m.key, a, mx) * 100)}%` }} className={a == null ? "none" : ""} /></div>
            <div className="cv-dl">{tr(SHORT[m.key] || m.label)}</div>
            <div className="cv-dbar them"><i style={{ width: b == null ? 0 : `${Math.max(2, frac(m.key, b, mx) * 100)}%` }} className={b == null ? "none" : ""} /></div>
            <div className={`cv-dv them${lead === "them" ? " lead" : ""}`}>{fmtV(m.key, b)}</div>
          </div>
        ))}
      </div>
      <div className="cv-flags">
        {flags.map(([k, label]) => (
          <div className="cv-flag" key={k}>
            <span className={`cv-tick ${us.score?.flags?.[k] ? "on" : ""}`} aria-label={us.score?.flags?.[k] ? tr("Yes") : tr("No")}>{us.score?.flags?.[k] ? "✓" : "–"}</span>
            <span className="cv-fl">{tr(label)}</span>
            <span className={`cv-tick them ${them.score?.flags?.[k] ? "on" : ""}`} aria-label={them.score?.flags?.[k] ? tr("Yes") : tr("No")}>{them.score?.flags?.[k] ? "✓" : "–"}</span>
          </div>
        ))}
      </div>
      {them.quickDismiss && <div className="cv-say"><span className="ct-label">{tr("If the buyer mentions them, say")}</span><p>{them.quickDismiss}</p></div>}
    </div>
  );
}

/* ------------------------------------------------------------------ scorecard grid: every tier 1 firm and us, shaded by value */
export function ScoreGrid({ d, extra = [] }: { d: any; extra?: string[] }) {
  const [sort, setSort] = useState<string>("overlap");
  const ms: Measure[] = d.measures || [];
  const pool: Comp[] = d.competitors.filter((c: Comp) => c.tier === 1 || extra.includes(c.key));
  const all: Comp[] = [d.us, ...d.competitors];
  const mxs = Object.fromEntries(ms.map((m) => [m.key, scaleMax(m, all.map((c) => c.score?.[m.key]))]));
  const rows = useMemo(() => {
    const val = (c: Comp) => (sort === "overlap" ? c.overlap?.total ?? 101 : c.score?.[sort]);
    return [d.us, ...pool.slice().sort((a, b) => (val(b) ?? -1) - (val(a) ?? -1))];
  }, [sort, pool, d.us]);   // eslint-disable-line react-hooks/exhaustive-deps
  const shade = (k: string, v: number | null | undefined) => {
    if (v == null) return undefined;
    const t = frac(k, v, mxs[k]);
    return { background: `rgba(13, 18, 76, ${0.06 + t * 0.72})`, color: t > 0.5 ? "var(--white)" : "var(--navy)" };
  };
  return (
    <div className="cv-grid-wrap" tabIndex={0} role="region" aria-label={tr("Scorecard grid")}>
      <table className="cv-grid">
        <thead><tr>
          <th className="nm"><button type="button" className={sort === "overlap" ? "on" : ""} onClick={() => setSort("overlap")}>{tr("Firm · overlap")}</button></th>
          {ms.map((m) => <th key={m.key}><button type="button" className={sort === m.key ? "on" : ""} onClick={() => setSort(m.key)} title={tr(HINT[m.key] || "")}>{tr(SHORT[m.key] || m.label)}</button></th>)}
        </tr></thead>
        <tbody>{rows.map((c) => (
          <tr key={c.key} className={c.tier === 0 ? "us" : ""}>
            <th className="nm">{c.tier === 0 ? <b>{c.name}</b> : <Link to={`/competitors/profile/${c.key}`}>{c.name}</Link>}<span className="xs muted">{c.tier === 0 ? tr("Us") : `T${c.tier} · ${c.overlap?.total}`}</span></th>
            {ms.map((m) => { const v = c.score?.[m.key]; return <td key={m.key} style={shade(m.key, v)} className={v == null ? "na" : ""}>{fmtV(m.key, v)}</td>; })}
          </tr>
        ))}</tbody>
      </table>
      <p className="xs muted">{tr("Darker means more. Click a column to sort. A dash means we did not find it, not zero.")}</p>
    </div>
  );
}

/* ------------------------------------------------------------------ momentum: who moved, when, and what kind of move */
const KIND: Record<string, string> = { ma_buy: "grow", funding: "grow", market: "grow", client: "grow", partnership: "grow", hiring: "grow",
  offer: "offer", positioning: "offer", pricing: "offer", content: "offer", award: "offer", leadership: "people", layoffs: "people", ma_sold: "people", other: "people" };
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function Momentum({ d, limit = 12 }: { d: any; limit?: number }) {
  const [tip, setTip] = useState<string>("");
  const a = String(d.asOf || "").match(/^(\d{4})-(\d{2})/);
  if (!a) return null;
  const end = +a[1] * 12 + (+a[2] - 1);
  const months = Array.from({ length: 12 }, (_, i) => end - 11 + i);
  const idx = (date: string) => { const m = String(date).match(/^(\d{4})-(\d{2})/); return m ? +m[1] * 12 + (+m[2] - 1) : null; };
  const firms = d.competitors.map((c: Comp) => ({ c, mv: c.moves.filter((m: any) => { const i = idx(m.date); return i != null && i >= months[0] && i <= end; }) }))
    .filter((x: any) => x.mv.length).sort((x: any, y: any) => (y.mv.reduce((s: number, m: any) => s + m.impact, 0) - x.mv.reduce((s: number, m: any) => s + m.impact, 0)) || x.c.tier - y.c.tier).slice(0, limit);
  if (!firms.length) return <p className="small muted">{tr("No dated moves in the last 12 months.")}</p>;
  return (
    <div className="cv-mom">
      <div className="cv-keys" aria-hidden="true"><span><i className="cv-k k-grow" />{tr("Growth: markets, clients, deals, money")}</span><span><i className="cv-k k-offer" />{tr("Offer and message")}</span>
        <span><i className="cv-k k-people" />{tr("People and ownership")}</span><span>{tr("Bigger mark: higher impact on us")}</span></div>
      <div className="cv-mgrid" style={{ gridTemplateColumns: `minmax(120px, 180px) repeat(12, minmax(22px, 1fr))` }}>
        <span />
        {months.map((m) => <span key={m} className="cv-mh">{tr(MON[m % 12])}{m % 12 === 0 ? ` ${Math.floor(m / 12)}` : ""}</span>)}
        {firms.map(({ c, mv }: any) => <Row key={c.key} c={c} mv={mv} months={months} idx={idx} setTip={setTip} />)}
      </div>
      <p className="small cv-mtip" aria-live="polite">{tip || <span className="muted">{tr("Point at a mark to read the move.")}</span>}</p>
    </div>
  );
}

function Row({ c, mv, months, idx, setTip }: { c: Comp; mv: any[]; months: number[]; idx: (s: string) => number | null; setTip: (s: string) => void }) {
  return <>
    <Link className="cv-mn" to={`/competitors/profile/${c.key}`}>{c.name}<span className="xs muted"> T{c.tier}</span></Link>
    {months.map((m) => {
      const here = mv.filter((x) => idx(x.date) === m);
      return <span key={m} className="cv-mc">{here.map((x) => {
        const text = `${c.name}, ${x.date}: ${x.headline}`;
        return <button key={x.id} type="button" className={`cv-mk k-${KIND[x.type] || "people"} i${x.impact}`} aria-label={text} title={text}
          onMouseEnter={() => setTip(text)} onFocus={() => setTip(text)} onClick={() => setTip(text)} />;
      })}</span>;
    })}
  </>;
}
