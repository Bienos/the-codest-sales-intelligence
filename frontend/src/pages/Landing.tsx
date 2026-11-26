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
/* The same square dots as the map at the bottom of the page. Signal cells light up around the copy and the card, and each
   sends a pulse that steps along the grid, cell by cell, into one pink cell beside the sign-in card: many signals, one place,
   sign in here. Cells and routes follow the real layout and never cross the text or the card. */
const GRID = 24;                  // px between dots: must match .dots in landing.css
const CYCLE = 8, DWELL = 0.4, STEP = 0.11, ARRIVE = 4;   // seconds: one round, a signal's flash before its pulse leaves, one cell of travel, when the first pulse lands
type Box = { l: number; t: number; r: number; b: number };
type Pt = [number, number];
type Hit = { x: number; y: number; next?: boolean };
type Cell = Hit & { route?: Pt[] };   // route: the corners from this cell to the pink one

/** Where signals appear: in the open space around the copy and the card, never under the text. */
function hitCells(w: number, h: number, card: Box, text: Box, link: Box): Hit[] {
  if (w <= 980) {
    const ny = card.t - 20;
    const out: Hit[] = [{ x: card.r - 24, y: ny, next: true }, { x: w * 0.06, y: ny }, { x: Math.max(link.r + 48, w * 0.72), y: (link.t + link.b) / 2 },
      { x: w * 0.86, y: text.t / 2 }, { x: w * 0.2, y: (card.b + h) / 2 }, { x: w * 0.64, y: (card.b + h) / 2 }];
    if (w - card.r > 120) out.push({ x: (card.r + w) / 2, y: card.t + (card.b - card.t) * 0.3 }, { x: card.r + (w - card.r) * 0.7, y: card.t + (card.b - card.t) * 0.78 });
    return out;
  }
  const gl = text.r + 36, gr = card.l - 36, gx = (f: number) => gl + (gr - gl) * f, cw = card.r - card.l, mid = (card.t + card.b) / 2;
  const out: Hit[] = [{ x: card.l - 36, y: mid, next: true }, { x: card.l + cw * 0.2, y: card.t * 0.5 }, { x: card.l + cw * 0.76, y: card.t * 0.28 },
    { x: card.l + cw * 0.58, y: (card.b + h) / 2 }, { x: text.l / 2, y: h * 0.88 }];
  if (gr - gl > 60) out.push({ x: gx(0.25), y: h * 0.15 }, { x: gx(0.7), y: h * 0.33 }, { x: gx(0.2), y: h * 0.7 }, { x: gx(0.65), y: h * 0.9 });
  if (gr - gl > 150) out.push({ x: card.l - 108, y: mid - 72 }, { x: card.l - 156, y: mid + 48 }, { x: card.l - 60, y: mid + 120 });   // a few more near the next step
  if (w - card.r > 60) out.push({ x: (card.r + w) / 2, y: card.t + (card.b - card.t) * 0.62 });
  return out;
}

/** A grid route with one turn that keeps clear of the text and the card; none if both ways are blocked.
    `across` first runs along the row and enters the pink cell from above or below; otherwise it enters along the pink cell's row. */
function routeTo(s: Hit, p: Hit, walls: Box[], across: boolean): Pt[] | undefined {
  const clear = (a: Pt, b: Pt) => !walls.some((w) => Math.min(a[0], b[0]) < w.r + 8 && Math.max(a[0], b[0]) > w.l - 8 && Math.min(a[1], b[1]) < w.b + 8 && Math.max(a[1], b[1]) > w.t - 8);
  const turns: Pt[] = [[p.x, s.y], [s.x, p.y]];
  for (const turn of across ? turns : turns.reverse()) {
    if (clear([s.x, s.y], turn) && clear(turn, [p.x, p.y])) return ([[s.x, s.y], turn, [p.x, p.y]] as Pt[]).filter((q, i, a) => i === 0 || q[0] !== a[i - 1][0] || q[1] !== a[i - 1][1]);
  }
}

/** Every grid cell along a route, start and end included. */
function routeCells(r: Pt[]): Pt[] {
  const out: Pt[] = [r[0]];
  for (let i = 1; i < r.length; i++) {
    const [x0, y0] = r[i - 1], [x1, y1] = r[i], n = Math.round((Math.abs(x1 - x0) + Math.abs(y1 - y0)) / GRID);
    for (let k = 1; k <= n; k++) out.push([x0 + ((x1 - x0) * k) / n, y0 + ((y1 - y0) * k) / n]);
  }
  return out;
}

function WatchGrid() {
  const ref = useRef<HTMLDivElement>(null);
  const anims = useRef<Animation[]>([]);
  const still = reducedMotion();
  const [cells, setCells] = useState<Cell[]>([]);
  const [live, setLive] = useState(true);
  useEffect(() => {
    const hero = ref.current?.parentElement; if (!hero) return;
    const box = (sel: string): Box => {          // offsets, not client rects: the card slides in, and that must not move the cells
      const n = hero.querySelector<HTMLElement>(sel); let l = 0, t = 0;
      for (let e: HTMLElement | null = n; e && e !== hero; e = e.offsetParent as HTMLElement | null) { l += e.offsetLeft; t += e.offsetTop; }
      return n ? { l, t, r: l + n.offsetWidth, b: t + n.offsetHeight } : { l: -99, t: -99, r: -99, b: -99 };
    };
    const snap = (v: number) => Math.round((v - GRID / 2) / GRID) * GRID + GRID / 2;
    const place = () => {
      const w = hero.clientWidth, h = hero.clientHeight; if (!w) return;
      const h1 = box(".land-copy h1"), p = box(".land-copy p"), eyebrow = box(".land-copy .eyebrow"), card = box("#signin"), link = box(".land-copy-actions a"), watch = box(".watching");
      const text = { l: Math.min(h1.l, p.l), t: h1.t, r: Math.max(h1.r, p.r), b: p.b };
      const placed: Cell[] = [];
      hitCells(w, h, card, text, link).forEach((c) => {
        const x = snap(c.x), y = snap(c.y);
        if (x < 0 || y < 0 || x > w || y > h || placed.some((o) => Math.abs(o.x - x) < GRID * 2 && Math.abs(o.y - y) < GRID * 2)) return;   // keep cells apart
        placed.push({ ...c, x, y });
      });
      const next = placed.find((c) => c.next);
      const walls = [card, text, eyebrow, link, { ...watch, r: watch.l + Math.min(watch.r - watch.l, 300) }];   // the watching line is at most ~300px wide; its box is wider
      if (next) placed.forEach((c, i) => { if (!c.next) c.route = routeTo(c, next, walls, i % 2 === 1); });   // alternate, so pulses arrive from several sides
      setCells(placed);
    };
    place();
    const ro = "ResizeObserver" in window ? new ResizeObserver(place) : null;
    [hero, hero.querySelector(".land-copy"), hero.querySelector("#signin")].forEach((n) => n && ro?.observe(n));
    const io = "IntersectionObserver" in window ? new IntersectionObserver((es) => setLive(es[0].isIntersecting)) : null;
    io?.observe(hero);
    return () => { ro?.disconnect(); io?.disconnect(); };
  }, []);
  /* The choreography, on the compositor (Web Animations, transform and opacity only). Rebuilt when the layout changes. */
  useEffect(() => {
    const root = ref.current; if (!root || still || !("animate" in root)) return;
    const routed = cells.filter((c) => c.route), out: Animation[] = [];
    const run = (el: Element | null, frames: Keyframe[], delay: number, fill: FillMode = "backwards") => {
      if (el) out.push(el.animate(frames, { duration: CYCLE * 1000, delay: delay * 1000, iterations: Infinity, fill }));
    };
    routed.forEach((c, i) => {
      const r = c.route!, lens = r.slice(1).map((q, j) => Math.round((Math.abs(q[0] - r[j][0]) + Math.abs(q[1] - r[j][1])) / GRID));
      const cellsToGo = lens.reduce((a, b) => a + b, 0), step = Math.min(STEP, (0.78 * CYCLE - DWELL) / cellsToGo);   // long routes hop a little faster
      const t0 = ARRIVE + i * 0.3 - DWELL - cellsToGo * step;     // leave so that all pulses land within a few beats of each other
      const tr = (q: Pt) => `translate(${q[0] - r[0][0]}px, ${q[1] - r[0][1]}px)`;
      let at = DWELL / CYCLE + 0.001;
      const keys: [number, Pt, string?][] = [];
      lens.forEach((n, j) => { keys.push([at, r[j], `steps(${n}, end)`]); at += (n * step) / CYCLE; });   // hop cell by cell
      const end = r[r.length - 1];
      root.querySelectorAll(`[data-pulse="${i}"]`).forEach((el, k) => {     // the head, then a trail one, two and three cells behind
        const o = [1, 0.85, 0.55, 0.3][k];
        run(el, [{ offset: 0, opacity: 0, transform: tr(r[0]) }, { offset: DWELL / CYCLE, opacity: 0, transform: tr(r[0]) },
          ...keys.map(([offset, q, easing]) => ({ offset, opacity: o, transform: tr(q), easing })),
          { offset: at, opacity: o, transform: tr(end) }, { offset: at + 0.002, opacity: 0, transform: tr(end) }, { offset: 1, opacity: 0, transform: tr(end) }], t0 + k * step);
      });
      run(root.querySelector(`[data-sig="${i}"]`), [{ offset: 0, opacity: 1, transform: "scale(1.8)" }, { offset: 0.03, opacity: 1, transform: "none" },
        { offset: 0.6, opacity: 0.9, transform: "none" }, { offset: 1, opacity: 0.45, transform: "none" }], t0, "none");
      run(root.querySelector(`[data-sig="${i}"] span`), [{ offset: 0, opacity: 0.9, transform: "none", easing: "cubic-bezier(.2,.7,.2,1)" }, { offset: 0.12, opacity: 0, transform: "scale(4.5)" },
        { offset: 1, opacity: 0, transform: "scale(4.5)" }], t0);
      run(root.querySelector(`[data-arrive="${i}"]`), [{ offset: 0, opacity: 0, transform: "none" }, { offset: at, opacity: 0, transform: "none" },
        { offset: at + 0.002, opacity: 1, transform: "scale(2)", easing: "ease-out" }, { offset: at + 0.06, opacity: 0, transform: "none" }, { offset: 1, opacity: 0, transform: "none" }], t0);
      run(root.querySelector(`[data-arrive="${i}"] + .wgrid-ring`), [{ offset: 0, opacity: 0, transform: "none" }, { offset: at, opacity: 0, transform: "none" },
        { offset: at + 0.002, opacity: 0.9, transform: "none", easing: "cubic-bezier(.2,.7,.2,1)" }, { offset: Math.min(at + 0.15, 1), opacity: 0, transform: "scale(5)" }, { offset: 1, opacity: 0, transform: "scale(5)" }], t0);
    });
    const glow = root.querySelector(".wgrid-glow");     // the grid around the pink cell brightens as the pulses land, then settles
    if (glow && routed.length) {
      const base = parseFloat(getComputedStyle(glow).opacity) || 0.5, peak = Math.min(1, base * 1.8), last = Math.min(0.97, (ARRIVE + routed.length * 0.3) / CYCLE);
      run(glow, [{ offset: 0, opacity: base * 1.3 }, { offset: 0.2, opacity: base }, { offset: ARRIVE / CYCLE, opacity: base }, { offset: last, opacity: peak }, { offset: 1, opacity: base * 1.3 }], 0);
    }
    anims.current = out;
    return () => { out.forEach((a) => a.cancel()); anims.current = []; };
  }, [cells, still]);
  useEffect(() => { anims.current.forEach((a) => (live ? a.play() : a.pause())); }, [live, cells]);
  const next = cells.find((c) => c.next), routed = cells.filter((c) => c.route);
  const trace = routed.flatMap((c) => routeCells(c.route!).slice(1, -1)).map(([x, y]) => `M${x - 2} ${y - 2}h4v4h-4z`).join("");
  return (
    <div className="wgrid" ref={ref} aria-hidden="true" data-live={live} style={{ ["--nx" as any]: `${next?.x ?? 0}px`, ["--ny" as any]: `${next?.y ?? 0}px` }}>
      {next && <div className="wgrid-glow" />}
      <svg className="wgrid-trace"><path d={trace} /></svg>
      {cells.filter((c) => !c.route).map((c, i) => <i key={`s${i}`} className={`wgrid-hit${c.next ? " wgrid-next" : ""}`} style={{ left: c.x, top: c.y }}>{c.next && <span className="wgrid-ring" />}</i>)}
      {routed.map((c, i) => {
        const path = routeCells(c.route!), at = Math.round(path.length * 0.62);      // with reduced motion each pulse is shown part-way, still heading in
        return [
          <i key={`h${i}`} data-sig={i} className="wgrid-hit" style={{ left: c.x, top: c.y }}><span className="wgrid-ring" /></i>,
          ...[0, 1, 2, 3].map((k) => {
            const q = path[Math.max(0, at - k)];
            return <i key={`p${i}-${k}`} data-pulse={i} className={`wgrid-pulse wgrid-pulse-${k}`} style={{ left: c.x, top: c.y, transform: still ? `translate(${q[0] - c.x}px, ${q[1] - c.y}px)` : undefined }} />;
          }),
          next && <i key={`a${i}`} data-arrive={i} className="wgrid-flash" style={{ left: next.x, top: next.y }} />,
          next && <i key={`r${i}`} className="wgrid-ring wgrid-ring-next" style={{ left: next.x, top: next.y }} />,
        ];
      })}
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

const WATCHING = ["public tenders", "trade news", "company newsrooms", "job ads", "web search", "vendor news", "regulators", "your account lists"];

/** One word at a time: what the platform is watching. Static when motion is reduced. */
function Watching() {
  const [i, setI] = useState(0);
  useEffect(() => { if (reducedMotion()) return; const t = setInterval(() => setI((x) => (x + 1) % WATCHING.length), 2300); return () => clearInterval(t); }, []);
  return <div className="watching" aria-label={tr("Watching {}", WATCHING.map((name) => tr(name)).join(", "))}><span className="watching-k">{tr("Watching")}</span><span key={i} className="watching-w" aria-hidden="true">{tr(WATCHING[i])}</span></div>;
}

const SOURCES = ["Public tenders", "Trade and fintech news", "Company newsrooms", "Job ads", "Web search", "Vendor announcements", "Regulators", "Your account lists"];

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
  { t: "Watch", d: "Public tenders, trade news, company websites, job ads, web search and your own account lists are read regularly." },
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
