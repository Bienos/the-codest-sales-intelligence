import { useEffect, useRef, useState } from "react";
import { setToken } from "../api";
import { Brand } from "../components/ui";

export type Notice = { kind: "denied" | "expired"; email?: string } | null;
declare global { interface Window { google?: any } }

function AuthCard({ onDone, notice }: { onDone: () => void; notice: Notice }) {
  const [cfg, setCfg] = useState<any>(null);
  const [t, setT] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => { fetch("/api/config").then((r) => r.json()).then(setCfg).catch(() => setErr("Cannot reach the server.")); }, []);
  useEffect(() => {
    if (!cfg?.google_client_id) return;
    const s = document.createElement("script");
    s.src = "https://accounts.google.com/gsi/client"; s.async = true;
    s.onload = () => {
      window.google.accounts.id.initialize({ client_id: cfg.google_client_id, callback: (r: any) => { setToken(r.credential); onDone(); } });
      window.google.accounts.id.renderButton(document.getElementById("g-btn"), { theme: "outline", size: "large", text: "signin_with", shape: "rectangular", width: 300 });
    };
    s.onerror = () => setErr("Google Sign-In could not be loaded.");
    document.head.appendChild(s);
    return () => { s.remove(); };
  }, [cfg]);
  return (
    <div className="auth-card" id="signin">
      <div className="eyebrow">Sign in</div>
      <h2>Welcome back</h2>
      {notice?.kind === "denied" && <div className="err" role="alert"><b>{notice.email ?? "This Google account"} has not been given access.</b><br />Ask an administrator to add you, then sign in again. If you have more than one Google account, choose your work account.</div>}
      {notice?.kind === "expired" && <div className="okmsg" role="status">Your session ended. Sign in again to continue.</div>}
      {cfg?.google_client_id ? <div id="g-btn" style={{ minHeight: 44 }} /> :
        cfg && cfg.env !== "production" ? <><p className="sub" style={{ margin: "0 0 12px" }}>Development environment. Paste a signed development token.</p>
          <input aria-label="Development token" type="password" value={t} onChange={(e) => setT(e.target.value)} placeholder="Development token" style={{ width: "100%", marginBottom: 14 }} />
          <button className="primary" onClick={() => { setToken(t); onDone(); }}>Continue</button></> :
        cfg ? <div className="err">Sign-in is not configured. An administrator must set RI_GOOGLE_CLIENT_ID.</div> : <span className="muted">Loading</span>}
      {err && <div className="err" role="alert">{err}</div>}
      <p className="small muted" style={{ margin: "16px 0 0" }}>Access is limited to people an administrator has added.</p>
    </div>
  );
}

/** Signal field: sources drift along the brand wave lines and converge on one pink square, the single next step. */
function SignalField() {
  const ref = useRef<SVGSVGElement>(null);
  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce && ref.current) ref.current.pauseAnimations();
  }, []);
  const paths = ["M0 90 C90 40 200 150 290 120 S350 160 372 167", "M0 170 C100 120 200 230 290 190 S350 170 372 167", "M0 250 C110 200 220 300 300 230 S355 180 372 167", "M0 320 C120 270 230 360 310 270 S360 190 372 167"];
  return (
    <svg ref={ref} className="field" viewBox="0 0 600 330" preserveAspectRatio="xMidYMid slice" role="img" aria-label="Signals from many sources converge on one next step" fill="none">
      {paths.map((d, i) => <path key={i} id={`w${i}`} d={d} stroke="#B87DFF" strokeWidth="0.9" opacity={0.6 - i * 0.07} />)}
      {paths.map((_, i) => [0, 1, 2].map((k) => (
        <circle key={`${i}-${k}`} r={k === 0 ? 3.2 : 2.4} fill={k === 0 ? "#fff" : "#B87DFF"}>
          <animateMotion dur={`${8 + i * 1.4 + k * 2.1}s`} begin={`${-k * 2.9 - i}s`} repeatCount="indefinite"><mpath href={`#w${i}`} /></animateMotion>
        </circle>)))}
      <g transform="translate(372 150)"><rect x="5" y="5" width="34" height="34" fill="none" stroke="#F2096E" strokeWidth="1.6" transform="scale(.6)" /><rect width="20" height="20" fill="#F2096E" transform="translate(0 0)" /></g>
    </svg>
  );
}

const STEPS = [
  { t: "Watch", d: "Tenders, company newsrooms, regulators, trade media and your own account lists are checked on a schedule." },
  { t: "Verify", d: "Every claim is tied to a word-for-word quote on the original page. No quote, no signal." },
  { t: "Group", d: "Coverage of the same announcement counts once. Related events join into one programme." },
  { t: "Test", d: "Seven gates ask what is proven and what is still unknown, including whether demand is external." },
  { t: "Recommend", d: "One next step with an objective, an owner, a trigger and a stop condition." },
  { t: "Approve", d: "Drafts and CRM changes wait for a second person. Nothing is sent from the system." },
];

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
  return (
    <div className="land" ref={root}>
      <header className="land-nav"><Brand /><a className="land-link" href="#signin">Sign in</a></header>

      <section className="land-hero">
        <SignalField />
        <div className="land-hero-in">
          <div className="land-copy">
            <div className="eyebrow">Revenue intelligence</div>
            <h1>See which engineering work is real, and who owns it.</h1>
            <p>Tenders, programme announcements and hiring patterns land in one place. Each one shows its evidence, what is still unknown, and one specific next step.</p>
            <ul className="land-points"><li>Every claim quotes its original source</li><li>Unknown stays unknown</li><li>Nothing is sent or changed without approval</li></ul>
          </div>
          <AuthCard onDone={onDone} notice={notice} />
        </div>
      </section>

      <section className="land-sec" aria-labelledby="how">
        <div className="land-wrap">
          <div className="eyebrow">How it works</div>
          <h2 id="how">From noise to one next step</h2>
          <ol className="land-steps">
            {STEPS.map((s, i) => (
              <li key={s.t} data-reveal style={{ transitionDelay: `${i * 70}ms` }}>
                <span className="land-n">{String(i + 1).padStart(2, "0")}</span>
                <h3>{s.t}</h3>
                <p>{s.d}</p>
              </li>))}
          </ol>
        </div>
      </section>

      <section className="land-sec land-tint" aria-labelledby="card">
        <div className="land-wrap land-two">
          <div data-reveal>
            <div className="eyebrow">What you get</div>
            <h2 id="card">A card you can act on</h2>
            <p>The verdict comes first, in plain words. The gates show what is proven, what is unknown and what is blocked. The evidence is one click away, quoted from the source.</p>
            <p className="small muted">The card on the right uses made-up data to show the layout.</p>
          </div>
          <article className="land-card" data-reveal aria-label="Example card with made-up data">
            <div className="land-card-top"><span className="badge info">Example, not real data</span></div>
            <div className="land-card-title">Example Bank AG</div>
            <div className="small muted">Core platform replacement · implementation</div>
            <div className="verdict warn"><span className="k">Our read</span>A real programme, but the direct route is blocked. Look for a partner route.</div>
            <div className="mini-gates" aria-label="Gate states">
              {[["G1", "pass"], ["G2", "pass"], ["G3", "pass"], ["G4", "unknown"], ["G5", "fail"], ["G6", "unknown"], ["G7", "unknown"]].map(([g, s]) => <span key={g} className={`mg ${s}`}>{g}</span>)}
            </div>
            <blockquote className="land-quote">“The bank has selected a vendor to replace its core banking platform.” <cite>Original press release, quoted</cite></blockquote>
            <div className="land-next"><span className="k">Next action</span><b>Qualify external demand</b><span className="small">Ask whether the separable workstream is sourced externally. A buyer conversation, not more web research.</span></div>
          </article>
        </div>
      </section>

      <section className="land-sec" aria-labelledby="honest">
        <div className="land-wrap">
          <div className="eyebrow">How it stays honest</div>
          <h2 id="honest">What it will not do</h2>
          <div className="land-cols">
            {[["It will not guess", "A source that fails to load shows as degraded coverage. A quiet screen never means nothing happened."],
              ["It will not send", "Drafts only. A second person approves any CRM change, and the exact change is shown first."],
              ["It will not hide the limits", "Scores are provisional weights, not win probabilities. Missing facts are listed, not filled in."]].map(([t, d], i) => (
              <div key={t} className="land-col" data-reveal style={{ transitionDelay: `${i * 90}ms` }}><h3>{t}</h3><p>{d}</p></div>))}
          </div>
        </div>
      </section>

      <footer className="land-foot">
        <div className="land-wrap row spread"><Brand sub={false} /><a className="land-link" href="#signin">Sign in</a></div>
        <div className="land-wrap small">Internal tool of The Codest. Access is limited to people an administrator has added.</div>
      </footer>
    </div>
  );
}
