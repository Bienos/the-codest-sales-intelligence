import { useEffect, useRef, useState } from "react";
import { setToken, startSession } from "../api";
import { LanguageSwitcher, tr, useLanguage } from "../locale";
import { Brand } from "../components/ui";

export type Notice = { kind: "denied" | "expired"; email?: string } | null;
declare global { interface Window { google?: any } }

function AuthCard({ onDone, notice }: { onDone: () => void; notice: Notice }) {
  const locale = useLanguage();
  const initializedClient = useRef<string>();
  const [cfg, setCfg] = useState<any>(null);
  const [t, setT] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => { fetch("/api/config").then((r) => r.json()).then(setCfg).catch(() => setErr(tr("Cannot reach the server."))); }, []);
  useEffect(() => {
    if (!cfg?.google_client_id) return;
    let active = true;
    const googleButton = document.getElementById("g-btn");
    const renderButton = () => {
      if (!active || !googleButton) return;
      if (initializedClient.current !== cfg.google_client_id) {
        window.google.accounts.id.initialize({ client_id: cfg.google_client_id, callback: async (r: any) => { setToken(r.credential); await startSession(); onDone(); } });
        initializedClient.current = cfg.google_client_id;
      }
      googleButton.replaceChildren();
      window.google.accounts.id.renderButton(googleButton, { theme: "outline", size: "large", text: "signin_with", shape: "rectangular", width: 300, locale });
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
      <h2>{tr("Welcome back")}</h2>
      {notice?.kind === "denied" && <div className="err" role="alert"><b>{notice.email ?? tr("This Google account")}{" " + tr("has not been given access.")}</b><br />{tr("Ask an administrator to add you, then sign in again. If you have more than one Google account, choose your work account.")}</div>}
      {notice?.kind === "expired" && <div className="okmsg" role="status">{tr("Your session ended. Sign in again to continue.")}</div>}
      {cfg?.google_client_id ? <div id="g-btn" style={{ minHeight: 44 }} /> :
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

/** Signal field: sources drift along the brand wave lines and converge on one pink square, the single next step. */
function SignalField({ uid = "a", target = true }: { uid?: string; target?: boolean }) {
  const ref = useRef<SVGSVGElement>(null);
  useEffect(() => { if (reducedMotion() && ref.current) ref.current.pauseAnimations(); }, []);
  const paths = ["M0 90 C90 40 200 150 290 120 S350 160 372 167", "M0 170 C100 120 200 230 290 190 S350 170 372 167", "M0 250 C110 200 220 300 300 230 S355 180 372 167", "M0 320 C120 270 230 360 310 270 S360 190 372 167"];
  return (
    <svg ref={ref} className="field" viewBox="0 0 600 330" preserveAspectRatio="xMidYMid slice" role="img" aria-label={tr("Signals from many sources converge on one next step")} fill="none">
      {paths.map((d, i) => <path key={i} id={`${uid}w${i}`} d={d} stroke="#B87DFF" strokeWidth="0.9" opacity={0.6 - i * 0.07} />)}
      {paths.map((_, i) => [0, 1, 2].map((k) => (
        <circle key={`${i}-${k}`} r={k === 0 ? 3.2 : 2.4} fill={k === 0 ? "#fff" : "#B87DFF"}>
          <animateMotion dur={`${8 + i * 1.4 + k * 2.1}s`} begin={`${-k * 2.9 - i}s`} repeatCount="indefinite"><mpath href={`#${uid}w${i}`} /></animateMotion>
        </circle>)))}
      {target && <g transform="translate(372 150)"><rect x="5" y="5" width="34" height="34" fill="none" stroke="#F2096E" strokeWidth="1.6" transform="scale(.6)" /><rect width="20" height="20" fill="#F2096E" transform="translate(0 0)" /></g>}
    </svg>
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

function CountUp({ to, label }: { to: number; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [n, setN] = useState(reducedMotion() ? to : 0);
  useEffect(() => {
    const el = ref.current; if (!el || n === to) return;
    const io = new IntersectionObserver((es) => {
      if (!es[0].isIntersecting) return; io.disconnect();
      const t0 = performance.now(); const step = (t: number) => { const p = Math.min(1, (t - t0) / 1100); setN(Math.round(to * (1 - Math.pow(1 - p, 3)))); if (p < 1) requestAnimationFrame(step); };
      requestAnimationFrame(step);
    }, { threshold: 0.6 });
    io.observe(el); return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [to]);
  return <div ref={ref} className="fact-c"><b>{n}</b><span>{label}</span></div>;
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

function useReveal() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current; if (!el) return;
    const items = el.querySelectorAll<HTMLElement>("[data-reveal]");
    if (!("IntersectionObserver" in window)) { items.forEach((i) => i.classList.add("in")); return; }
    const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }), { threshold: 0.2 });
    items.forEach((i) => io.observe(i));
    return () => io.disconnect();
  }, []);
  return ref;
}

export default function Landing({ onDone, notice }: { onDone: () => void; notice: Notice }) {
  const root = useReveal();
  const hero = useRef<HTMLElement>(null);
  const onMove = (e: React.PointerEvent) => {
    const el = hero.current; if (!el || e.pointerType === "touch") return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", `${e.clientX - r.left}px`); el.style.setProperty("--my", `${e.clientY - r.top}px`);
  };
  return (
    <div className="land" ref={root}>
      <div className="land-top"><header className="land-nav"><Brand /><div className="land-nav-actions"><LanguageSwitcher /><a className="land-jump" href="#signin">{tr("Sign in")}</a></div></header>

      <section className="land-hero" ref={hero} onPointerMove={onMove}>
        <div className="dots" aria-hidden="true" /><div className="dots lit" aria-hidden="true" />
        <SignalField />
        <div className="land-hero-in">
          <div className="land-copy">
            <div className="eyebrow">{tr("Revenue intelligence")}</div>
            <h1>{tr("Find the next engineering opportunity")}</h1>
            <p>{tr("See open RFPs and buying signals from banks and financial services firms. Check the original source, then decide what to do next.")}</p>
            <div className="land-copy-actions"><a href="#demo">{tr("See how it works")} ↓</a></div>
            <Watching />
          </div>
          <AuthCard onDone={onDone} notice={notice} />
        </div>
      </section></div>

      <Ticker />

      <section className="land-sec land-tint" id="demo" aria-labelledby="demo-h">
        <div className="land-wrap">
          <div className="eyebrow" data-reveal>{tr("Watch it work")}</div>
          <h2 id="demo-h" data-reveal>{tr("A signal gets checked, tested and turned into one next step")}</h2>
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
              <div key={t} className="land-col" data-reveal style={{ transitionDelay: `${i * 90}ms` }}><span className="no" aria-hidden="true">{String(i + 1).padStart(2, "0")}</span><h3>{t}</h3><p>{d}</p></div>))}
          </div>
        </div>
      </section>

      <section className="land-cta" aria-labelledby="cta">
        <SignalField uid="b" target={false} />
        <div className="land-wrap">
          <h2 id="cta" data-reveal>{tr("Ready for your next step?")}</h2>
          <a className="btn-pink" href="#signin" data-reveal>{tr("Sign in")}</a>
        </div>
      </section>

      <footer className="land-foot">
        <div className="land-wrap land-foot-in"><Brand sub={false} /><span className="small">{tr("Internal tool of The Codest. Access is limited to people an administrator has added.")}</span></div>
      </footer>
    </div>
  );
}
