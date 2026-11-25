import { useEffect, useRef, useState } from "react";
import { setToken, startSession } from "../api";
import { LanguageSwitcher, tr, useLanguage } from "../locale";
import { Brand } from "../components/ui";

export type Notice = { kind: "denied" | "expired"; email?: string } | null;
declare global { interface Window { google?: any } }

type Cfg = { google_client_id?: string | null; env?: string; allowed_domain?: string | null } | null;

function AuthCard({ onDone, notice, cfg, cfgErr }: { onDone: () => void; notice: Notice; cfg: Cfg; cfgErr: string }) {
  const locale = useLanguage();
  const initializedClient = useRef<string>();
  const [t, setT] = useState("");
  const [err, setErr] = useState(cfgErr);
  useEffect(() => setErr(cfgErr), [cfgErr]);
  const [ready, setReady] = useState(false);
  const [signing, setSigning] = useState(false);
  const [slow, setSlow] = useState(false);
  useEffect(() => { const timer = setTimeout(() => setSlow(true), 6000); return () => clearTimeout(timer); }, []);
  useEffect(() => {
    if (!cfg?.google_client_id) return;
    let active = true;
    const googleButton = document.getElementById("g-btn");
    const renderButton = () => {
      if (!active || !googleButton) return;
      if (initializedClient.current !== cfg.google_client_id) {
        window.google.accounts.id.initialize({ client_id: cfg.google_client_id, ...(cfg.allowed_domain ? { hd: cfg.allowed_domain } : {}), ux_mode: "popup", itp_support: true,
          callback: async (r: any) => { setSigning(true); setToken(r.credential); await startSession(); onDone(); } });
        initializedClient.current = cfg.google_client_id ?? undefined;
      }
      googleButton.replaceChildren();
      const width = Math.max(200, Math.min(380, Math.floor(googleButton.getBoundingClientRect().width || 300)));      // fill the card, within Google's limits
      window.google.accounts.id.renderButton(googleButton, { theme: "filled_blue", size: "large", text: "continue_with", shape: "rectangular", logo_alignment: "left", width, locale });
      const top = document.getElementById("g-btn-top");             // the same sign-in at the top of the page: one click, no scrolling
      if (top) {
        top.replaceChildren();
        window.google.accounts.id.renderButton(top, { theme: "outline", size: "large", text: "signin", shape: "rectangular", logo_alignment: "left", locale });
      }
      setReady(true);
    };
    if (window.google?.accounts?.id) {
      renderButton();
      return () => { active = false; };
    }
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.onload = renderButton;
    script.onerror = () => { if (active) setErr(tr("Google Sign-In could not be loaded.")); };
    document.head.appendChild(script);
    return () => { active = false; script.remove(); };
  }, [cfg, locale]);
  return (
    <div className="auth-card" id="signin">
      <div className="eyebrow">{tr("Sign in")}</div>
      <h2>{tr("Sign in to your workspace")}</h2>
      <p className="sub mt-0 mb-3">{cfg?.allowed_domain ? tr("Use your {} Google account.", "@" + cfg.allowed_domain) : tr("Use your work Google account.")}</p>
      {notice?.kind === "denied" && <div className="err" role="alert"><b>{notice.email ?? tr("This Google account")}{" " + tr("has not been given access.")}</b><br />{tr("Ask an administrator to add you, then sign in again. If you have more than one Google account, choose your work account.")}</div>}
      {notice?.kind === "expired" && <div className="okmsg" role="status">{tr("Your session ended. Sign in again to continue.")}</div>}
      {cfg?.google_client_id ? <>
          <div id="g-btn" className={`g-btn${ready ? " ready" : ""}`} aria-busy={!ready} />
          {!ready && !err && <div className="g-btn-wait small muted" role="status">{tr("Loading Google sign-in")}</div>}
          {signing && <div className="okmsg" role="status">{tr("Signing you in")}</div>}
          {!ready && slow && !err && <p className="small muted mt-2 mb-0">{tr("If the Google button does not appear, allow pop-ups and third-party sign-in for this site, or try another browser.")}</p>}
        </> :
        cfg && cfg.env !== "production" ? <><p className="sub mt-0 mb-3">{tr("Development environment. Paste a signed development token.")}</p>
          <input aria-label={tr("Development token")} type="password" value={t} onChange={(e) => setT(e.target.value)} placeholder={tr("Development token")} style={{ width: "100%", marginBottom: 14 }} />
          <button className="primary" onClick={() => { setToken(t); onDone(); }}>{tr("Continue")}</button></> :
        cfg ? <div className="err">{tr("Sign-in is not configured. An administrator must set RI_GOOGLE_CLIENT_ID.")}</div> : <span className="muted">{tr("Loading")}</span>}
      {err && <div className="err" role="alert">{tr(err)}</div>}
      <p className="small muted mt-4 mb-0">{tr("Access is limited to people an administrator has added.")}</p>
    </div>
  );
}

const reducedMotion = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------- hero background: the watch grid ---------- */
/* The same square dots as the map at the bottom of the page. A reading head sweeps across the grid; the cells it reads as
   signals light up and fade, and one cell beside the sign-in card turns pink: the next step. Cells follow the real layout. */
const GRID = 24, SWEEP = 12;     // px between dots and seconds per sweep: must match .dots and .wgrid-sweep in landing.css
type Box = { l: number; t: number; r: number; b: number };
type Hit = { x: number; y: number; next?: boolean };

/** Where the head finds signals: in the open space around the copy and the card, never under the text. */
function hitCells(w: number, h: number, card: Box, text: Box, link: Box): Hit[] {
  if (w <= 980) {
    const out: Hit[] = [{ x: card.r - 24, y: card.t - 20, next: true }, { x: w * 0.86, y: text.t / 2 }, { x: Math.max(link.r + 48, w * 0.72), y: (link.t + link.b) / 2 },
      { x: w * 0.2, y: (card.b + h) / 2 }, { x: w * 0.64, y: (card.b + h) / 2 }];
    if (w - card.r > 120) out.push({ x: (card.r + w) / 2, y: card.t + (card.b - card.t) * 0.3 }, { x: card.r + (w - card.r) * 0.7, y: card.t + (card.b - card.t) * 0.78 });
    return out;
  }
  const gl = text.r + 36, gr = card.l - 36, gx = (f: number) => gl + (gr - gl) * f, cw = card.r - card.l;
  const out: Hit[] = [{ x: card.l + cw * 0.2, y: card.t * 0.5 }, { x: card.l + cw * 0.76, y: card.t * 0.28 },
    { x: card.l + cw * 0.58, y: (card.b + h) / 2 }, { x: text.l / 2, y: h * 0.88 }];
  out.unshift({ x: card.l - 36, y: (card.t + card.b) / 2, next: true });
  if (gr - gl > 60) out.push({ x: gx(0.25), y: h * 0.15 }, { x: gx(0.7), y: h * 0.33 }, { x: gx(0.2), y: h * 0.7 }, { x: gx(0.65), y: h * 0.9 });
  if (gr - gl > 150) { const mid = (card.t + card.b) / 2; out.push({ x: card.l - 108, y: mid - 72 }, { x: card.l - 156, y: mid + 48 }, { x: card.l - 60, y: mid + 120 }); }   // a few more near the next step
  if (w - card.r > 60) out.push({ x: (card.r + w) / 2, y: card.t + (card.b - card.t) * 0.62 });
  return out;
}

function WatchGrid() {
  const ref = useRef<HTMLDivElement>(null);
  const [cells, setCells] = useState<(Hit & { d: number })[]>([]);
  const [live, setLive] = useState(true);
  useEffect(() => {
    const hero = ref.current?.parentElement; if (!hero) return;
    const box = (sel: string): Box => {          // offsets, not client rects: the card slides in, and that must not move the cells
      const n = hero.querySelector<HTMLElement>(sel); let l = 0, t = 0;
      for (let e: HTMLElement | null = n; e && e !== hero; e = e.offsetParent as HTMLElement | null) { l += e.offsetLeft; t += e.offsetTop; }
      return n ? { l, t, r: l + n.offsetWidth, b: t + n.offsetHeight } : { l: 0, t: 0, r: 0, b: 0 };
    };
    const snap = (v: number) => Math.round((v - GRID / 2) / GRID) * GRID + GRID / 2;
    const place = () => {
      const w = hero.clientWidth, h = hero.clientHeight; if (!w) return;
      const h1 = box(".land-copy h1"), p = box(".land-copy p");
      const text = { l: Math.min(h1.l, p.l), t: h1.t, r: Math.max(h1.r, p.r), b: p.b };
      const placed: (Hit & { d: number })[] = [];
      hitCells(w, h, box("#signin"), text, box(".land-copy-actions a")).forEach((c) => {
        const x = snap(c.x), y = snap(c.y);
        if (x < 0 || y < 0 || x > w || y > h || placed.some((o) => Math.abs(o.x - x) < GRID * 2 && Math.abs(o.y - y) < GRID * 2)) return;   // keep cells apart
        placed.push({ ...c, x, y, d: ((x + 0.0132 * w) / (1.22 * w) - 1) * SWEEP });   // d: when the head passes x
      });
      setCells(placed);
    };
    place();
    const ro = "ResizeObserver" in window ? new ResizeObserver(place) : null;
    [hero, hero.querySelector(".land-copy"), hero.querySelector("#signin")].forEach((n) => n && ro?.observe(n));
    const io = "IntersectionObserver" in window ? new IntersectionObserver((es) => setLive(es[0].isIntersecting)) : null;
    io?.observe(hero);
    return () => { ro?.disconnect(); io?.disconnect(); };
  }, []);
  const next = cells.find((c) => c.next);
  return (
    <div className="wgrid" ref={ref} aria-hidden="true" data-live={live}
      style={{ ["--at" as any]: `${-(SWEEP + (next?.d ?? -SWEEP / 2)).toFixed(2)}s`, ["--nx" as any]: `${next?.x ?? 0}px`, ["--ny" as any]: `${next?.y ?? 0}px` }}>
      {next && <div className="wgrid-glow" />}
      <div className="wgrid-sweep" />
      {cells.map((c, i) => <i key={i} className={`wgrid-hit${c.next ? " wgrid-next" : ""}`} style={{ left: c.x, top: c.y, ["--d" as any]: `${c.d.toFixed(2)}s` }} />)}
    </div>
  );
}

/* ---------- closing map: example signals light up the markets we sell into, and one becomes the next step ---------- */
/* Square-dot Europe, one dot per cell, from Natural Earth (public domain), run-length encoded row by row.
   _ sea, o other land, u UK, d DACH, n Nordics, p Poland. */
const MAP_COLS = 64, MAP_ROWS = 66;
const MAP_RLE = "39_11n14o/40_12n12o/38_14n11o/36_n_14n4o/38_14n6o/37_16n6o/37_17n5o3_2o/36_18n6o2_2o/36_11n_6n6o/36_10n3_6n9o/35_10n5_5n9o/36_9n4_7n8o/34_12n2_8n8o/33_12n3_10n6o/33_12n2_11n6o/31_n2_9n3_12n6o/31_12n3_11n7o/29_13n4_11n7o/27_15n4_10n8o/27_15n5_9n8o/27_14n6_8n9o/27_15n5_6n6_5o/26_16n6_4n4_8o/26_17n2_o_n8_8o/26_18n7_13o/26_18n6_14o/9_u2_3u11_18n5_o_13o/8_u3_2u13_5n2_8n7_o_13o/8_2u_2u14_4n3_8n10_12o/9_u_5u12_2n4_8n10_12o/10_6u19_7n2_n4_2o_12o/10_5u17_n2_7n2_n3_16o/10_4u16_3n3_6n6_16o/6_o4_4u16_2n3_5n7_16o/5_2o2u2_4u15_4n2_5n8_15o/3_o_4u_5u15_3n2_n_2n10_15o/2_5o2u3_3u15_3n_2n_2n10_15o/2_6o4_4u14_2n_n13_17o/2_6o5_4u14_d2_n8_2p2_6p11o/2_6o4_5u13_3d3_2d3_12p11o/_6o3_7u14_3d_4d15p10o/5o_o3_7u9_o_d_9d15p10o/9_10u6_3o11d14p11o/8_11u5_4o11d15p10o/10_9u4_5o12d14p10o/9_9u5_4o13d14p10o/8_10u4_4o14d15p9o/9_u9_7o14d15p9o/18_8o13d2o14p9o/18_8o11d6o11p10o/12_o3_10o10d11o7p10o/7_o_o2_14o10d11opop3op10o/7_19o11d27o/7_22o9d3od22o/9_19o16d20o/10_18o17d19o/11_17o15d21o/11_16o17d20o/12_14o17d21o/12_13o8d4o4d23o/12_14o5d33o/12_52o/11_25o2_26o/o10_25o3_25o/4o7_25o5_23o/8o2_19o_6o6_22o";
let mapPaths: Record<string, string> | null = null;
function mapDots() {
  if (mapPaths) return mapPaths;
  const parts: Record<string, string[]> = {};
  MAP_RLE.split("/").forEach((row, y) => {
    const re = /(\d*)(\D)/g; let m: RegExpExecArray | null; let x = 0;
    while ((m = re.exec(row))) {
      const n = Number(m[1] || 1), k = m[2];
      if (k !== "_") for (let i = 0; i < n; i++) (parts[k] = parts[k] || []).push(`M${x + i + 0.27} ${y + 0.27}h.46v.46h-.46z`);
      x += n;
    }
  });
  mapPaths = Object.fromEntries(Object.entries(parts).map(([k, v]) => [k, v.join("")]));
  return mapPaths;
}
/** Made-up signals, one per market; positions are map cells, `at` is where the card sits (`sm` on phones). The last one becomes the next step. */
const PINGS: { m: string; x: number; y: number; at: string; sm?: string; kind: string; who: string }[] = [
  { m: "u", x: 15.9, y: 45.5, at: "b", kind: "New technology leader", who: "Example Insurance plc, London" },
  { m: "n", x: 26.6, y: 23.5, at: "l", kind: "Funding round", who: "Example Pay AS, Bergen" },
  { m: "p", x: 49.8, y: 43.9, at: "r", sm: "t", kind: "Hiring several engineers", who: "Example Fintech S.A., Warsaw" },
  { m: "d", x: 43.2, y: 55.1, at: "r", sm: "b", kind: "Core banking replacement", who: "Example Bank AG, Vienna" },
];
const MAP_DONE = PINGS.length + 1;                          // every signal in, the last one resolved
const MAP_WAIT = [700, 1200, 1200, 1200, 1500, 6500, 700];  // per step; the last step fades out before the loop restarts

function SignalMap() {
  const ref = useRef<HTMLDivElement>(null);
  const still = reducedMotion();
  const [step, setStep] = useState(still ? MAP_DONE : 0);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const el = ref.current; if (!el || !("IntersectionObserver" in window)) { setVisible(true); return; }
    const io = new IntersectionObserver((es) => setVisible(es[0].isIntersecting), { threshold: 0.3 });
    io.observe(el); return () => io.disconnect();
  }, []);
  useEffect(() => {
    if (still || !visible) return;
    const t = setTimeout(() => setStep((s) => (s + 1) % MAP_WAIT.length), MAP_WAIT[step]);
    return () => clearTimeout(t);
  }, [step, visible, still]);
  const dots = mapDots(), out = step > MAP_DONE;
  return (
    <div className={`smap${out ? " smap-out" : ""}`} ref={ref} data-live={visible && !still} role="img"
      aria-label={tr("Map of Europe with example signals in the UK, DACH, the Nordics and Poland. One of them, a core banking replacement, becomes the next step.")}>
      <svg viewBox={`0 0 ${MAP_COLS} ${MAP_ROWS}`} aria-hidden="true">
        <path className="smap-land" d={dots.o} />
        {PINGS.map((p, i) => <path key={p.m} className={`smap-mk${step > i && !out ? " smap-lit" : ""}`} d={dots[p.m]} />)}
      </svg>
      <span className="smap-tag" aria-hidden="true">{tr("Example data, not real")}</span>
      {PINGS.map((p, i) => {
        const last = i === PINGS.length - 1;
        return (
          <div key={p.m} aria-hidden="true" style={{ left: `${(p.x / MAP_COLS) * 100}%`, top: `${(p.y / MAP_ROWS) * 100}%` }}
            className={`smap-ping smap-${p.at}${p.sm ? ` smap-sm-${p.sm}` : ""}${last ? " smap-last" : ""}${step > i ? " smap-on" : ""}${step >= MAP_DONE ? (last ? " smap-pick" : " smap-dim") : ""}`}>
            <i className="smap-pm" />
            <div className="smap-card"><b>{tr(p.kind)}</b><span>{tr(p.who)}</span>
              {last && <div className="smap-next"><small>{tr("Next step")}</small>{tr("Ask who will staff the migration")}</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

const WATCHING = ["tenders", "regulators", "company newsrooms", "job boards", "trade media", "your account lists"];

/** One word at a time: what the platform is watching. Static when motion is reduced. */
function Watching() {
  const [i, setI] = useState(0);
  useEffect(() => { if (reducedMotion()) return; const t = setInterval(() => setI((x) => (x + 1) % WATCHING.length), 2300); return () => clearInterval(t); }, []);
  return <div className="watching" aria-label={tr("Watching {}", WATCHING.map((name) => tr(name)).join(", "))}><span className="watching-k">{tr("Watching")}</span><span key={i} className="watching-w" aria-hidden="true">{tr(WATCHING[i])}</span></div>;
}

const SOURCES = ["Tenders", "Regulators", "Company newsrooms", "Job boards", "Trade media", "Your account lists", "Search discovery", "Programme announcements"];

function Ticker() {
  return (
    <div className="ticker" aria-label={tr("Sources the platform reads")}>
      <div className="ticker-track" aria-hidden="true">
        {[0, 1].map((k) => <div className="ticker-set" key={k}>{SOURCES.map((x) => <span key={x + k}>{tr(x)}</span>)}</div>)}
      </div>
    </div>
  );
}

/** The real number is always in the page: nothing here waits for a scroll event to appear. */
function CountUp({ to, label }: { to: number; label: string }) {
  return <div className="fact-c"><b>{to}</b><span>{label}</span></div>;
}

/* ---------- the live demo: made-up signals get checked, tested and turned into one next step ---------- */
const PHASES = [
  { k: "Arrives", d: "Three raw signals land. They are leads, not findings." },
  { k: "Checked", d: "Each one is read against the original page. The platform looks for a word-for-word quote." },
  { k: "Verdict", d: "No quote, no signal. The first one drops out. The other two keep their quote as evidence." },
  { k: "Seven gates", d: "What is proven, what is unknown and what is blocked, one gate at a time." },
  { k: "One next step", d: "A single action with an owner, a trigger and a stop condition. A second person approves it." },
];
const GATES: [string, string][] = [["G1", "pass"], ["G2", "pass"], ["G3", "pass"], ["G4", "unknown"], ["G5", "fail"], ["G6", "unknown"], ["G7", "unknown"]];
const DURATIONS = [2300, 2400, 2600, 3300, 4600];

function Demo() {
  const ref = useRef<HTMLDivElement>(null);
  const [phase, setPhase] = useState(reducedMotion() ? 4 : 0);
  const [auto, setAuto] = useState(!reducedMotion());
  const [visible, setVisible] = useState(false);
  const [gap, setGap] = useState(false);
  useEffect(() => {
    const el = ref.current; if (!el || !("IntersectionObserver" in window)) { setVisible(true); return; }
    const io = new IntersectionObserver((es) => setVisible(es[0].isIntersecting), { threshold: 0.35 });
    io.observe(el); return () => io.disconnect();
  }, []);
  useEffect(() => {
    if (!auto || !visible) return;
    const t = setTimeout(() => { if (phase < 4) setPhase(phase + 1); else { setGap(true); setTimeout(() => { setGap(false); setPhase(0); }, 900); } }, DURATIONS[phase]);
    return () => clearTimeout(t);
  }, [phase, auto, visible]);
  useEffect(() => {      // on a phone the steps are a sideways strip: keep the active one in view (without moving the page)
    const ol = ref.current?.querySelector<HTMLElement>(".demo-steps"); const on = ol?.querySelector<HTMLElement>("button.on")?.parentElement;
    if (ol && on && ol.scrollWidth > ol.clientWidth) ol.scrollTo({ left: on.offsetLeft - 16, behavior: "smooth" });
  }, [phase]);
  const cls = gap ? "ph-gap" : Array.from({ length: phase + 1 }, (_, i) => `ph${i}`).join(" ");
  const pick = (i: number) => { setAuto(false); setGap(false); setPhase(i); };
  return (
    <div className="demo" ref={ref} data-auto={auto && visible}>
      <ol className="demo-steps" aria-label={tr("Steps")}>
        {PHASES.map((p, i) => (
          <li key={p.k}><button className={i === phase && !gap ? "on" : ""} style={{ ["--dur" as any]: `${DURATIONS[i]}ms` }} aria-current={i === phase ? "step" : undefined} onClick={() => pick(i)}>
            <span className="demo-n">{String(i + 1).padStart(2, "0")}</span><span><b>{tr(p.k)}</b><small>{tr(p.d)}</small></span></button></li>))}
        <li className="demo-ctl"><button className="ghost" onClick={() => { setAuto(true); setGap(false); setPhase(0); }}>{auto ? tr("Restart") : tr("Play again")}</button></li>
      </ol>
      <div className={`stage ${cls}`} role="img" aria-label={tr("Animated example with made-up data: three signals are checked against their original pages, one is dropped for having no quote, the others are tested against seven gates and become one next step.")}>
        <div className="stage-top"><span className="badge info">{tr("Example data, not real")}</span><span className="stage-phase">{tr(PHASES[gap ? 0 : phase].k)}</span></div>
        <div className="scan" aria-hidden="true" />
        <div className="sigs">
          <div className="sig bad"><div className="sig-t">{tr("Forum post says a bank is “thinking about” a new core")}</div><div className="sig-s">{tr("Forum post, no original page")}</div>
            <div className="sig-q"><span className="stamp">{tr("No quote, no signal")}</span></div></div>
          <div className="sig good"><div className="sig-t">{tr("Example Bank AG selects a vendor to replace its core platform")}</div><div className="sig-s">{tr("Press release on the bank's own site")}</div>
            <div className="sig-q"><mark><span>{tr("“The bank has selected a vendor to replace its core banking platform.”")}</span></mark><span className="tick">{tr("Quote found")}</span></div></div>
          <div className="sig good second"><div className="sig-t">{tr("Example Fintech Ltd opens 14 payments engineering roles")}</div><div className="sig-s">{tr("Careers page")}</div>
            <div className="sig-q"><mark><span>{tr("“We are hiring 14 engineers for our payments platform.”")}</span></mark><span className="tick">{tr("Quote found")}</span></div></div>
        </div>
        <div className="gates" aria-hidden="true">{GATES.map(([g, st], i) => <span key={g} className={`gt ${st}`} style={{ ["--i" as any]: i }}>{g}</span>)}</div>
        <div className="next"><span className="k">{tr("Next action")}</span><b>{tr("Qualify external demand")}</b><span className="small">{tr("Ask whether the separable workstream is sourced externally. A buyer conversation, not more web research.")}</span><span className="next-btn">{tr("Needs a second person to approve")}</span></div>
      </div>
      <p className="demo-d" aria-live="polite">{tr(PHASES[gap ? 0 : phase].d)}</p>
    </div>
  );
}

/* ---------- scroll-drawn timeline ---------- */
const STEPS = [
  { t: "Watch", d: "Tenders, company newsrooms, regulators, trade media and your own account lists are checked on a schedule." },
  { t: "Verify", d: "Every claim is tied to a word-for-word quote on the original page. No quote, no signal." },
  { t: "Group", d: "Coverage of the same announcement counts once. Related events join into one programme." },
  { t: "Test", d: "Seven gates ask what is proven and what is still unknown, including whether demand is external." },
  { t: "Recommend", d: "One next step with an objective, an owner, a trigger and a stop condition." },
  { t: "Approve", d: "Drafts and CRM changes wait for a second person. Nothing is sent from the system." },
];

function Timeline() {
  const ref = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const el = ref.current; if (!el) return; let raf = 0;
    const upd = () => {
      const r = el.getBoundingClientRect(), vh = window.innerHeight;
      el.style.setProperty("--p", String(Math.min(1, Math.max(0, (vh * 0.62 - r.top) / r.height))));
      Array.from(el.children).forEach((c) => c.classList.toggle("lit", c.getBoundingClientRect().top < vh * 0.64));
    };
    const on = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(upd); };
    upd(); window.addEventListener("scroll", on, { passive: true }); window.addEventListener("resize", on);
    return () => { cancelAnimationFrame(raf); window.removeEventListener("scroll", on); window.removeEventListener("resize", on); };
  }, []);
  return (
    <ol className="tl" ref={ref}>
      {STEPS.map((s, i) => <li key={s.t}><span className="tl-n">{String(i + 1).padStart(2, "0")}</span><div><h3>{tr(s.t)}</h3><p>{tr(s.d)}</p></div></li>)}
    </ol>
  );
}

export default function Landing({ onDone, notice }: { onDone: () => void; notice: Notice }) {
  const [cfg, setCfg] = useState<Cfg>(null);
  const [cfgErr, setCfgErr] = useState("");
  useEffect(() => { fetch("/api/config").then((r) => r.json()).then(setCfg).catch(() => setCfgErr(tr("Cannot reach the server."))); }, []);
  const hero = useRef<HTMLElement>(null);
  const onMove = (e: React.PointerEvent) => {
    const el = hero.current; if (!el || e.pointerType === "touch") return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", `${e.clientX - r.left}px`); el.style.setProperty("--my", `${e.clientY - r.top}px`);
  };
  return (
    <div className="land">
      <div className="land-top"><header className="land-nav"><Brand /><div className="land-nav-actions"><LanguageSwitcher />{cfg?.google_client_id ? <div id="g-btn-top" className="g-btn-top" />
            : <a className="land-signin" href="#signin" onClick={() => setTimeout(() => (document.querySelector("#signin input, #signin button") as HTMLElement | null)?.focus(), 300)}>{tr("Sign in")}</a>}</div></header>

      <section className="land-hero" ref={hero} onPointerMove={onMove}>
        <div className="dots" aria-hidden="true" /><div className="dots lit" aria-hidden="true" />
        <WatchGrid />
        <div className="land-hero-in">
          <div className="land-copy">
            <div className="eyebrow">{tr("Revenue intelligence")}</div>
            <h1>{tr("Find the next engineering opportunity")}</h1>
            <p>{tr("See open RFPs and buying signals from banks and financial services firms. Check the original source, then decide what to do next.")}</p>
            <div className="land-copy-actions"><a href="#demo">{tr("See how it works")} ↓</a></div>
            <Watching />
          </div>
          <AuthCard onDone={onDone} notice={notice} cfg={cfg} cfgErr={cfgErr} />
        </div>
      </section></div>

      <Ticker />

      <section className="land-sec land-tint" id="demo" aria-labelledby="demo-h">
        <div className="land-wrap">
          <div className="eyebrow">{tr("Watch it work")}</div>
          <h2 id="demo-h">{tr("A signal gets checked, tested and turned into one next step")}</h2>
          <Demo />
        </div>
      </section>

      <section className="land-sec land-how" aria-labelledby="how">
        <div className="land-wrap land-split">
          <div className="land-stick">
            <div className="eyebrow">{tr("How it works")}</div>
            <h2 id="how">{tr("From noise to one next step")}</h2>
            <div className="facts-c">
              <CountUp to={64} label={tr("signal rules")} />
              <CountUp to={7} label={tr("gates every card passes")} />
              <CountUp to={1} label={tr("next step, never a list")} />
            </div>
          </div>
          <Timeline />
        </div>
      </section>

      <section className="land-sec land-tint" aria-labelledby="honest">
        <div className="land-wrap">
          <div className="eyebrow">{tr("How it stays honest")}</div>
          <h2 id="honest">{tr("What it will not do")}</h2>
          <div className="land-cols">
            {[[tr("It will not guess"), tr("A source that fails to load shows as degraded coverage. A quiet screen does not mean nothing happened.")],
              [tr("It will not send"), tr("Drafts only. A second person approves any CRM change, and the exact change is shown first.")],
              [tr("It will not hide the limits"), tr("Scores are provisional weights, not win probabilities. Missing facts are listed, not filled in.")]].map(([t, d], i) => (
              <div key={t} className="land-col"><span className="no" aria-hidden="true">{String(i + 1).padStart(2, "0")}</span><h3>{t}</h3><p>{d}</p></div>))}
          </div>
        </div>
      </section>

      <section className="land-cta" aria-labelledby="cta">
        <div className="land-wrap land-cta-in">
          <div className="land-cta-copy">
            <h2 id="cta">{tr("Ready for your next step?")}</h2>
            <p>{tr("Buying signals from banks, insurers and payment companies in the UK, DACH, the Nordics and Poland. Each one is checked against its original page.")}</p>
            <a className="btn-pink" href="#signin">{tr("Sign in")}</a>
          </div>
          <SignalMap />
        </div>
      </section>

      <footer className="land-foot">
        <div className="land-wrap land-foot-in"><Brand sub={false} /><span className="small">{tr("Internal tool of The Codest. Access is limited to people an administrator has added.")}</span></div>
      </footer>
    </div>
  );
}
