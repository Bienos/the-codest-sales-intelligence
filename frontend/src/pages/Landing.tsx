import { ReactNode, useEffect, useRef, useState } from "react";
import { setToken, startSession } from "../api";
import { LanguageSwitcher, tr, useLanguage } from "../locale";
import { Brand } from "../components/ui";
import "../styles/landing.css";

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
    <div className="lp-card" id="signin">
      <h2 id="signin-h" tabIndex={-1}>{tr("Sign in")}</h2>
      <p className="lp-card-sub">{cfg?.allowed_domain ? tr("Use your {} Google account.", "@" + cfg.allowed_domain) : tr("Use your work Google account.")}</p>
      {notice?.kind === "denied" && <div className="err" role="alert"><b>{notice.email ?? tr("This Google account")}{" " + tr("has not been given access.")}</b><br />{tr("Ask an administrator to add you, then sign in again. If you have more than one Google account, choose your work account.")}</div>}
      {notice?.kind === "expired" && <div className="okmsg" role="status">{tr("Your session ended. Sign in again to continue.")}</div>}
      {cfg?.google_client_id ? <>
          <div id="g-btn" className={`lp-g${ready ? " ready" : ""}`} aria-busy={!ready} />
          {!ready && !err && <div className="lp-g-wait small muted" role="status">{tr("Loading Google sign-in")}</div>}
          {signing && <div className="okmsg" role="status">{tr("Signing you in")}</div>}
          {!ready && slow && !err && <p className="small muted mt-2 mb-0">{tr("If the Google button does not appear, allow pop-ups and third-party sign-in for this site, or try another browser.")}</p>}
        </> :
        cfg && cfg.env !== "production" ? <><p className="lp-card-sub">{tr("Development environment. Paste a signed development token.")}</p>
          <input aria-label={tr("Development token")} type="password" value={t} onChange={(e) => setT(e.target.value)} placeholder={tr("Development token")} className="lp-token" />
          <button className="primary lp-continue" onClick={() => { setToken(t); onDone(); }}>{tr("Continue")}</button></> :
        cfg ? <div className="err">{tr("Sign-in is not configured. An administrator must set RI_GOOGLE_CLIENT_ID.")}</div> : <span className="muted">{tr("Loading")}</span>}
      {err && <div className="err" role="alert">{tr(err)}</div>}
    </div>
  );
}


/* ---------- the hero animation: dots from five kinds of source travel to ONE point, a buying signal ---------- */
/* One canvas, drawn from a pure function of time, so the still frame for reduced motion is the same drawing.
   About 9 seconds: (1) dots rest in five groups, each with its label; (2) they leave along curved paths with fading trails and
   all arrive at one pink point; (3) a ring pulses and "Buying signal" appears; (4) two lines reach "Who to contact" and "What to say"
   (an invented example); (5) everything fades and a fresh scatter starts. Seeded, so every load looks the same. No allocation per frame. */
const GROUPS: { long: string; short: string; size: number; color: string }[] = [
  { long: "Public tenders", short: "Tenders", size: 3.6, color: "#B87DFF" },
  { long: "News and trade press", short: "Press", size: 2.6, color: "#D6D8F2" },
  { long: "Job adverts", short: "Jobs", size: 2.6, color: "#B87DFF" },
  { long: "Company websites", short: "Websites", size: 3.4, color: "#D6D8F2" },
  { long: "Your lead list", short: "Your list", size: 3.8, color: "#FFFFFF" },
];
const NG = GROUPS.length, MAXN = 125, TAU = Math.PI * 2;
const CYCLE = 9, T_LAB = 0.3, T_GO = 2.0, T_SIG = 4.6, T_CHIP = 6.0, T_OUT = 8.4;   // seconds into the loop
const PINK = "#F2096E", PURPLE = "#B87DFF", WHITE = "#FFFFFF";
/** Where things sit, as fractions of the stage: the point, the left edge of the example chips, and the five groups in order. */
const LAYOUT = {
  wide: { p: [0.45, 0.5], chip: 0.68, g: [[0.08, 0.27], [0.27, 0.16], [0.14, 0.77], [0.37, 0.8], [0.62, 0.2]] },
  narrow: { p: [0.41, 0.5], chip: 0.62, g: [[0.12, 0.26], [0.36, 0.15], [0.14, 0.73], [0.4, 0.78], [0.66, 0.2]] },
};

type Scene = {
  w: number; h: number; narrow: boolean; n: number; per: number; px: number; py: number; ring: number; ax: number; ay1: number; ay2: number;
  gx: Float32Array; gy: Float32Array; gr: number;
  sx: Float32Array; sy: Float32Array; cx: Float32Array; cy: Float32Array; t0: Float32Array; du: Float32Array; ph: Float32Array; ap: Float32Array; fr: Float32Array;
};
const makeScene = (): Scene => ({ w: 0, h: 0, narrow: false, n: 0, per: 0, px: 0, py: 0, ring: 0, ax: 0, ay1: 0, ay2: 0, gx: new Float32Array(NG), gy: new Float32Array(NG), gr: 0,
  sx: new Float32Array(MAXN), sy: new Float32Array(MAXN), cx: new Float32Array(MAXN), cy: new Float32Array(MAXN), t0: new Float32Array(MAXN), du: new Float32Array(MAXN),
  ph: new Float32Array(MAXN), ap: new Float32Array(MAXN), fr: new Float32Array(MAXN) });

function rng(seed: number) {            // mulberry32
  let a = seed >>> 0;
  return () => { a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

/** A fresh scatter for each round of the loop: the dots rest in their groups and each gets a curved path to the point. All curves bend the same way, so the flow swirls in. */
function build(s: Scene, cycle: number) {
  const r = rng(0x9e3779b9 ^ (cycle * 7919 + 17));
  for (let g = 0; g < NG; g++) for (let k = 0; k < s.per; k++) {
    const i = g * s.per + k, a = r() * TAU, d = Math.sqrt(r()) * s.gr;
    const x = s.gx[g] + Math.cos(a) * d, y = s.gy[g] + Math.sin(a) * d * 0.85;
    const dx = s.px - x, dy = s.py - y, len = Math.hypot(dx, dy) || 1, bend = len * (0.16 + r() * 0.2);
    s.sx[i] = x; s.sy[i] = y;
    s.cx[i] = (x + s.px) / 2 - (dy / len) * bend; s.cy[i] = (y + s.py) / 2 + (dx / len) * bend;
    s.t0[i] = T_GO + g * 0.12 + r() * 0.5; s.du[i] = 1.5 + r() * 0.7;
    s.ph[i] = r() * TAU; s.ap[i] = r() * 0.9; s.fr[i] = 0.45 + r() * 0.5;
  }
}

let BX = 0, BY = 0;                       // the last point from bez(): module-level, so nothing is allocated per frame
function bez(s: Scene, i: number, e: number) {
  const m = 1 - e;
  BX = m * m * s.sx[i] + 2 * m * e * s.cx[i] + e * e * s.px;
  BY = m * m * s.sy[i] + 2 * m * e * s.cy[i] + e * e * s.py;
}
const ramp = (t: number, a: number, b: number) => (t <= a ? 0 : t >= b ? 1 : (t - a) / (b - a));
const smooth = (u: number) => u * u * (3 - 2 * u);

function draw(c: CanvasRenderingContext2D, s: Scene, t: number, still: boolean, glow: HTMLCanvasElement) {
  const { w, h, px, py, n, per } = s;
  c.clearRect(0, 0, w, h);
  const G = still || t < T_OUT ? 1 : Math.max(0, 1 - (t - T_OUT) / 0.55);          // everything fades out at the end of the loop
  if (G <= 0) return;
  const prog = (i: number) => (still ? s.fr[i] : (t - s.t0[i]) / s.du[i]);
  c.lineWidth = 1;
  // thin rays from each group to the point: where the dots will go
  const rayA = still ? 0.5 : ramp(t, 1.4, 2.2) * (1 - ramp(t, 4.8, 5.6));
  if (rayA > 0) {
    c.strokeStyle = WHITE; c.globalAlpha = 0.16 * rayA * G; c.beginPath();
    for (let g = 0; g < NG; g++) {
      const gx = s.gx[g], gy = s.gy[g], dx = px - gx, dy = py - gy, len = Math.hypot(dx, dy) || 1, bend = len * 0.26;
      c.moveTo(gx, gy); c.quadraticCurveTo((gx + px) / 2 - (dy / len) * bend, (gy + py) / 2 + (dx / len) * bend, px, py);
    }
    c.stroke();
  }
  // trails: a long faint one and a short brighter one behind each dot that is on its way
  let arrived = 0;
  c.strokeStyle = PURPLE;
  for (let pass = 0; pass < 2; pass++) {
    const span = pass ? 0.11 : 0.3;
    c.globalAlpha = (pass ? 0.6 : 0.22) * G; c.beginPath();
    for (let i = 0; i < n; i++) {
      const u = prog(i);
      if (u >= 1) { if (!pass) arrived++; continue; }
      if (u <= 0) continue;
      const e = smooth(u), e0 = Math.max(0, e - span);
      bez(s, i, e0); c.moveTo(BX, BY);
      for (let k = 1; k <= 4; k++) { bez(s, i, e0 + ((e - e0) * k) / 4); c.lineTo(BX, BY); }
    }
    c.stroke();
  }
  // the dots: square, like the brand's mark; one size and tint per group
  for (let g = 0; g < NG; g++) {
    c.fillStyle = GROUPS[g].color;
    const size = GROUPS[g].size * (s.narrow ? 1.1 : 1);
    for (let k = 0; k < per; k++) {
      const i = g * per + k, u = prog(i);
      if (u >= 1) continue;
      let x: number, y: number, a: number, z = size;
      if (u <= 0) {                    // resting: a slow drift, fading in; the drift settles just before the dot leaves
        const d = still ? 0 : Math.min(1, Math.max(0, (s.t0[i] - t) / 0.4)) * 2.2;
        x = s.sx[i] + Math.sin(t * 0.8 + s.ph[i]) * d; y = s.sy[i] + Math.cos(t * 0.65 + s.ph[i] * 1.3) * d;
        a = (still ? 1 : ramp(t, s.ap[i], s.ap[i] + 0.7)) * (0.78 + 0.22 * Math.sin(t * 1.3 + s.ph[i]));
      } else {
        const e = smooth(u); bez(s, i, e); x = BX; y = BY; a = 1; z = size * (1 - 0.4 * e);
      }
      c.globalAlpha = a * G; c.fillRect(x - z / 2, y - z / 2, z, z);
    }
  }
  // the point: dim at first, brighter with every dot that arrives
  const I = still ? 1 : n ? arrived / n : 0, breathe = still || t < T_SIG ? 1 : 1 + 0.06 * Math.sin((t - T_SIG) * 3);
  const gs = (60 + 80 * I) * breathe;
  c.globalAlpha = (0.2 + 0.6 * I) * G; c.drawImage(glow, px - gs / 2, py - gs / 2, gs, gs);
  // a soft ring pulses when the last dots land, and once more, lighter, while the lines draw
  c.strokeStyle = PINK; c.lineWidth = 1.5;
  for (let r = 0; r < 2; r++) {
    const k = still ? (r ? -1 : 0.4) : (t - (r ? 6.1 : 4.5)) / 1.7;
    if (k <= 0 || k >= 1) continue;
    c.globalAlpha = (1 - k) * (r ? 0.45 : 0.9) * G; c.beginPath(); c.arc(px, py, 8 + s.ring * (1 - (1 - k) * (1 - k)), 0, TAU); c.stroke();
  }
  c.globalAlpha = G; c.fillStyle = PINK; const ps = 5 + 6 * I; c.fillRect(px - ps / 2, py - ps / 2, ps, ps);
  // two clean lines from the point to the example chips
  c.strokeStyle = WHITE; c.lineWidth = 1;
  for (let l = 0; l < 2; l++) {
    const p = still ? 1 : smooth(ramp(t, 5.5 + l * 0.12, 6.2 + l * 0.12));
    if (p <= 0) continue;
    const ex = s.ax, ey = l ? s.ay2 : s.ay1, dx = ex - px, dy = ey - py, len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len;
    c.globalAlpha = 0.85 * G; c.beginPath(); c.moveTo(px + ux * 9, py + uy * 9); c.lineTo(px + dx * p, py + dy * p); c.stroke();
    if (p >= 1) { c.fillStyle = WHITE; c.fillRect(ex - 2, ey - 2, 4, 4); }
  }
  c.globalAlpha = 1;
}

const reducedMotion = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function Converge() {
  const locale = useLanguage();
  const [still, setStill] = useState(reducedMotion);
  const box = useRef<HTMLDivElement>(null), canvas = useRef<HTMLCanvasElement>(null), sig = useRef<HTMLSpanElement>(null);
  const chips = useRef<HTMLDivElement>(null), c1 = useRef<HTMLDivElement>(null), c2 = useRef<HTMLDivElement>(null), labs = useRef<(HTMLSpanElement | null)[]>([]);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)"), on = () => setStill(mq.matches);
    mq.addEventListener?.("change", on); return () => mq.removeEventListener?.("change", on);
  }, []);
  useEffect(() => {
    const el = box.current, cv = canvas.current, ctx = cv?.getContext("2d");
    if (!el || !cv || !ctx) return;
    const glow = document.createElement("canvas"); glow.width = glow.height = 96;     // a soft pink glow, drawn once and reused
    const gc = glow.getContext("2d");
    if (gc) { const gr = gc.createRadialGradient(48, 48, 0, 48, 48, 48); gr.addColorStop(0, "rgba(242,9,110,.9)"); gr.addColorStop(0.35, "rgba(242,9,110,.32)"); gr.addColorStop(1, "rgba(242,9,110,0)"); gc.fillStyle = gr; gc.fillRect(0, 0, 96, 96); }
    const s = makeScene();
    let raf = 0, last = 0, time = 0, cycle = 0, stage = -1, seen = true;
    const setStage = (v: number) => { if (v !== stage) { stage = v; el.dataset.stage = String(v); } };
    const place = () => {
      const w = el.clientWidth, h = el.clientHeight; if (!w || !h) return;
      const narrow = w < 520, L = narrow ? LAYOUT.narrow : LAYOUT.wide, dpr = Math.min(window.devicePixelRatio || 1, 2);
      el.dataset.narrow = narrow ? "true" : "false";
      cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      s.w = w; s.h = h; s.narrow = narrow; s.px = L.p[0] * w; s.py = L.p[1] * h;
      s.gr = narrow ? Math.min(0.056 * w, 0.09 * h) : Math.min(0.06 * w, 0.16 * h);
      s.ring = narrow ? 30 : 46; s.per = narrow ? 13 : 24; s.n = s.per * NG;
      for (let g = 0; g < NG; g++) {
        s.gx[g] = L.g[g][0] * w; s.gy[g] = L.g[g][1] * h;
        const lab = labs.current[g]; if (!lab) continue;
        const half = lab.offsetWidth / 2;
        lab.style.left = `${Math.min(Math.max(s.gx[g], half + 2), w - half - 2)}px`; lab.style.top = `${s.gy[g] + s.gr + 8}px`;
      }
      if (sig.current) { const edge = s.px - s.ring - (narrow ? 12 : 18); sig.current.style.right = `${w - edge}px`; sig.current.style.maxWidth = `${Math.max(60, edge - 2)}px`; sig.current.style.top = `${s.py}px`; }   // right-aligned, and wraps to two lines when the words are long (Polish)
      const ch = chips.current, a = c1.current, b = c2.current;
      if (ch && a && b) {
        const left = L.chip * w; ch.style.left = `${left}px`; ch.style.width = `${Math.min(w - left, 300)}px`; ch.style.top = "0px";
        const y1 = a.offsetTop + a.offsetHeight / 2, y2 = b.offsetTop + b.offsetHeight / 2;
        const top = Math.min(Math.max(0, s.py - (y1 + y2) / 2), Math.max(0, h - ch.offsetHeight));
        ch.style.top = `${top}px`; s.ax = left; s.ay1 = top + y1; s.ay2 = top + y2;
      }
      build(s, cycle);
      if (still) draw(ctx, s, 0, true, glow);
    };
    place();
    const ro = "ResizeObserver" in window ? new ResizeObserver(place) : null; ro?.observe(el);
    if (still) return () => ro?.disconnect();
    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      if (now - last < 31) return;                                  // about 30 frames a second is plenty for this
      const dt = last ? Math.min(0.1, (now - last) / 1000) : 0; last = now; time += dt;
      if (time >= CYCLE) { time -= CYCLE; cycle++; build(s, cycle); }
      setStage(time < T_LAB ? 0 : time < T_GO ? 1 : time < T_SIG ? 2 : time < T_CHIP ? 3 : time < T_OUT ? 4 : 0);
      draw(ctx, s, time, false, glow);
    };
    const sync = () => { const go = seen && !document.hidden; if (go && !raf) { last = 0; raf = requestAnimationFrame(frame); } else if (!go && raf) { cancelAnimationFrame(raf); raf = 0; } };
    const io = "IntersectionObserver" in window ? new IntersectionObserver((es) => { seen = es[0].isIntersecting; sync(); }) : null; io?.observe(el);
    document.addEventListener("visibilitychange", sync); sync();
    return () => { cancelAnimationFrame(raf); raf = 0; ro?.disconnect(); io?.disconnect(); document.removeEventListener("visibilitychange", sync); };
  }, [locale, still]);
  return (
    <div className="lp-anim">
    <div className="lp-stage" ref={box} data-stage="0" data-narrow="false" data-still={still ? "true" : undefined}>
      <canvas ref={canvas} aria-hidden="true" />
      {GROUPS.map((g, i) => <span key={g.long} className="lp-lab" aria-hidden="true" ref={(n) => { labs.current[i] = n; }}><span className="l-long">{tr(g.long)}</span><span className="l-short">{tr(g.short)}</span></span>)}
      <span className="lp-sig" aria-hidden="true" ref={sig}>{tr("Buying signal")}</span>
      <div className="lp-chips" aria-hidden="true" ref={chips}>
        <span className="lp-chips-tag">{tr("Example")}</span>
        <div className="lp-chip" ref={c1}><small>{tr("Who to contact")}</small><b>{tr("Sam Taylor, CTO")}</b></div>
        <div className="lp-chip" ref={c2}><small>{tr("What to say")}</small><b>{tr("“Saw you are hiring 12 engineers”")}</b></div>
      </div>
      <p className="lp-vh">{tr("Animation: dots from public tenders, news, job adverts, company websites and your lead list travel to one point, a buying signal. From it, lines lead to who to contact and what to say, shown here with an invented example.")}</p>
    </div>
    <ul className="lp-legend" aria-hidden="true">{GROUPS.map((g) => <li key={g.long}>{tr(g.long)}</li>)}</ul>
    </div>
  );
}

/* ---------- small line icons, the same style as the rest of the app ---------- */
const ico = (d: ReactNode) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true" focusable="false">{d}</svg>
);
const ICONS = {
  companies: ico(<><path d="M4 5h16M4 12h16M4 19h10" /><path d="M18 17l2 2 3-4" /></>),
  tender: ico(<><path d="M6 3h8l4 4v14H6z" /><path d="M14 3v4h4M9 12h6M9 16h6" /></>),
  person: ico(<><circle cx="12" cy="8" r="3.5" /><path d="M5 20c0-4 3-6 7-6s7 2 7 6" /></>),
  news: ico(<><rect x="4" y="4" width="16" height="16" /><path d="M8 9h8M8 13h8M8 17h5" /></>),
  jobs: ico(<><rect x="3" y="7" width="18" height="13" /><path d="M9 7V4h6v3M3 12h18" /></>),
  web: ico(<><rect x="3" y="4" width="18" height="16" /><path d="M3 9h18M6.5 6.5h.01M9.5 6.5h.01" /></>),
  list: ico(<><path d="M5 3h14v18H5z" /><path d="M9 8h6M9 12h6M9 16h3" /></>),
};

/* ---------- (b) what you get ---------- */
const GETS: { icon: keyof typeof ICONS; title: string; text: string }[] = [
  { icon: "companies", title: "A short list of companies to call", text: "Banks, insurers, payment companies and fintechs where something has just happened that usually means they need engineers." },
  { icon: "tender", title: "Open tenders, found for you", text: "Public requests for software work in the EU, the UK and Poland, with the deadline and whether it is work we do." },
  { icon: "person", title: "Who to contact and what to say", text: "The right person at each company, and a first line that opens on that company's own news." },
];

/* ---------- (c) one example card: the same parts as a card in the app, with an invented company ---------- */
function ExampleCard() {
  return (
    <article className="lp-ec" aria-label={tr("Example card with an invented company")}>
      <div className="lp-ec-bar"><span className="lp-ec-tag">{tr("Example")}</span><span className="lp-ec-note">{tr("Invented company and details")}</span></div>
      <header className="lp-ec-head">
        <span className="lp-mono" aria-hidden="true">EM</span>
        <div className="lp-ec-co"><b>Example Mutual plc</b><span>{tr("London, UK")} · {tr("Insurer")}</span></div>
        <span className="lp-ec-str"><span className="lp-bars" aria-hidden="true"><i /><i /><i /></span>{tr("Strong")}</span>
      </header>
      <p className="lp-ec-kind"><span className="lp-sw" aria-hidden="true" /><b>{tr("Hiring")}</b><span>{tr("{} days ago", 3)}</span></p>
      <dl className="lp-ec-rows">
        <div><dt>{tr("What happened")}</dt><dd>{tr("Opened 12 engineering roles for a new claims platform.")}</dd></div>
        <div><dt>{tr("Why it matters to us")}</dt><dd>{tr("A build this size usually needs more engineers than an insurer can hire quickly.")}<span className="lp-fit">{tr("Best fit")}: <b>{tr("Team extension")}</b></span></dd></div>
        <div><dt>{tr("Who to contact")}</dt><dd><b>Sam Taylor</b>, {tr("Chief Technology Officer")} <span className="lp-mail">{tr("Email ✓")}</span></dd></div>
        <div><dt>{tr("What to say")}</dt><dd><q>{tr("I saw you are hiring 12 engineers for your claims platform. We help insurers add experienced engineers to a build like this. Would a short call be useful?")}</q></dd></div>
        <div className="lp-ec-next"><dt>{tr("Next step")}</dt><dd>{tr("Write to Sam this week.")}</dd></div>
      </dl>
      <p className="lp-ec-src">{tr("Source")}: {tr("the careers page of Example Mutual")}</p>
    </article>
  );
}

/* ---------- (d) where the information comes from ---------- */
const SOURCES: { icon: keyof typeof ICONS; title: string; text: string; tags?: string[] }[] = [
  { icon: "tender", title: "Public tenders", text: "Official requests for suppliers, each with a deadline. Only software and engineering work.",
    tags: ["TED (EU)", "Find a Tender (UK)", "Contracts Finder (UK)", "e-Zamówienia (Poland)"] },
  { icon: "news", title: "News and trade press", text: "Company announcements, funding news and trade media about banks, insurers and fintechs." },
  { icon: "jobs", title: "Job adverts", text: "Engineering roles that show a company is building something new." },
  { icon: "web", title: "Company websites", text: "The news and careers pages of the companies you follow, read on a schedule." },
  { icon: "list", title: "Your own lead list", text: "The companies you already track. Everything the app finds is matched to a company on this list." },
];

/* ---------- (e) how it works ---------- */
const STEPS: [string, string][] = [
  ["It reads", "Public tenders, news, job adverts and company websites are checked on a schedule, so you do not have to."],
  ["It finds a buying signal", "Each story is matched to a company on your list. The app checks that the words are really on the page, and that the company and the work suit The Codest."],
  ["You get who to contact and what to say", "A short list of companies, each with the reason, the right person and a first line. You decide what to send. The app never sends anything by itself."],
];

export default function Landing({ onDone, notice }: { onDone: () => void; notice: Notice }) {
  const [cfg, setCfg] = useState<Cfg>(null);
  const [cfgErr, setCfgErr] = useState("");
  useEffect(() => { fetch("/api/config").then((r) => r.json()).then(setCfg).catch(() => setCfgErr(tr("Cannot reach the server."))); }, []);
  /** Take the person to the sign-in card and put the keyboard there: the token box when there is one, otherwise the card's heading. */
  const goSignIn = (e: React.MouseEvent) => {
    e.preventDefault();
    const card = document.getElementById("signin"); if (!card) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    card.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "center" });
    window.setTimeout(() => ((card.querySelector("input, button") ?? document.getElementById("signin-h")) as HTMLElement | null)?.focus({ preventScroll: true }), still ? 0 : 350);
  };
  return (
    <div className="lp">
      <div className="lp-top">
        <div className="lp-bar">
          <div className="lp-wrap lp-nav">
            <Brand sub={false} />
            <div className="lp-nav-actions"><LanguageSwitcher />{cfg?.google_client_id ? <div id="g-btn-top" className="lp-g-top" />
              : <a className="lp-signin" href="#signin" onClick={goSignIn}>{tr("Sign in")}</a>}</div>
          </div>
        </div>
        <section className="lp-wrap lp-hero" aria-labelledby="lp-h1">
          <div className="lp-copy">
            <p className="lp-eyebrow">{tr("For The Codest sales team")}</p>
            <h1 id="lp-h1">{tr("Know which banks and fintechs to call, and why")}</h1>
            <p className="lp-hero-p">{tr("It reads public tenders, news, job adverts and company websites. You get a short list of banks, insurers and fintechs to contact, each with a reason and a person to write to.")}</p>
          </div>
          <Converge />
          <AuthCard onDone={onDone} notice={notice} cfg={cfg} cfgErr={cfgErr} />
        </section>
      </div>

      <section className="lp-sec" aria-labelledby="lp-gets">
        <div className="lp-wrap">
          <div className="lp-head"><h2 id="lp-gets">{tr("What it gives you")}</h2><p>{tr("Three things, so you start the day knowing who to contact.")}</p></div>
          <ul className="lp-gets">
            {GETS.map((g) => <li key={g.title} className="lp-get"><span className="lp-ico">{ICONS[g.icon]}</span><h3>{tr(g.title)}</h3><p>{tr(g.text)}</p></li>)}
          </ul>
        </div>
      </section>

      <section className="lp-sec lp-tint" aria-labelledby="lp-ex">
        <div className="lp-wrap lp-split">
          <div className="lp-head"><h2 id="lp-ex">{tr("One example")}</h2><p>{tr("Every company in the app is shown like this. This one is made up.")}</p></div>
          <ExampleCard />
        </div>
      </section>

      <section className="lp-sec" aria-labelledby="lp-src">
        <div className="lp-wrap lp-split">
          <div className="lp-head"><h2 id="lp-src">{tr("Where the information comes from")}</h2><p>{tr("Public pages only. Every finding links back to the page it came from.")}</p></div>
          <div>
            <ul className="lp-srcs">
              {SOURCES.map((s) => <li key={s.title}>
                <span className="lp-ico lp-ico-sm">{ICONS[s.icon]}</span>
                <h3>{tr(s.title)}</h3>
                <div className="lp-src-d"><p>{tr(s.text)}</p>
                  {s.tags && <ul className="lp-tags" aria-label={tr("Tender sites")}>{s.tags.map((x) => <li key={x}>{tr(x)}</li>)}</ul>}</div>
              </li>)}
            </ul>
            <p className="lp-fine">{tr("The app never logs in anywhere and never gets round a block.")}</p>
          </div>
        </div>
      </section>

      <section className="lp-sec lp-tint" aria-labelledby="lp-how">
        <div className="lp-wrap">
          <div className="lp-head"><h2 id="lp-how">{tr("How it works in three steps")}</h2><p>{tr("It runs by itself. You decide what to do with the result.")}</p></div>
          <ol className="lp-steps">
            {STEPS.map(([title, text], i) => <li key={title} className="lp-step"><span className="lp-n" aria-hidden="true">{i + 1}</span><h3>{tr(title)}</h3><p>{tr(text)}</p></li>)}
          </ol>
        </div>
      </section>

      <section className="lp-cta" aria-labelledby="lp-cta-h">
        <div className="lp-wrap lp-cta-in">
          <div><h2 id="lp-cta-h">{tr("Ready to see who to call?")}</h2>
            <p>{cfg?.allowed_domain ? tr("Use your {} Google account.", "@" + cfg.allowed_domain) : tr("Use your work Google account.")}</p></div>
          <a className="lp-cta-btn" href="#signin" onClick={goSignIn}>{tr("Sign in")}</a>
        </div>
      </section>

      <footer className="lp-foot">
        <div className="lp-wrap lp-foot-in"><Brand sub={false} /><p>{tr("Internal tool of The Codest. Access is limited to people an administrator has added.")}</p></div>
      </footer>
    </div>
  );
}
