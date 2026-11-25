import { number, tr } from "../locale";
import { Fragment, ReactNode, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post, put } from "../api";
import { AccountFindings } from "../components/Findings";
import { SignalSummary } from "../components/BuyingSignal";
import { SHORT } from "../components/Signals";
import { countryName, staffCount } from "./targetMarket";
import { Badge, Err, Evidence, groupName, humanize, lab, priorityKind, Skeleton, reloadPage, safeHref, whyShort, Tabs } from "../components/ui";
import "../styles/account.css";

/* ------------------------------------------------------------------ shared helpers (also used by the person page) */
export const host = (u?: string | null) => { try { return new URL(String(u)).hostname.replace(/^www\./, ""); } catch { return ""; } };
export const initials = (name: string) => {
  const words = name.split(/[\s-]+/).filter((w) => /^\p{L}/u.test(w));
  return (words.length === 1 ? words[0].slice(0, 2) : words.slice(0, 2).map((w) => w[0]).join("")).toUpperCase() || "?";
};
/** A research date: a full day, a month (YYYY-MM) or a year. */
export function fmtDay(d?: string | null): string {
  if (!d) return tr("Date unknown");
  if (/^\d{4}$/.test(d)) return d;
  if (/^\d{4}-\d{2}$/.test(d)) { const x = new Date(`${d}-01T00:00:00Z`); return isNaN(x.getTime()) ? d : x.toLocaleDateString(undefined, { year: "numeric", month: "short", timeZone: "UTC" }); }
  return fmtDate(d);
}
/** Where a statement came from: the page's site, or our own records. */
export function Cite({ c }: { c: any }) {
  if (!c) return null;
  if (c.source === "R" || !c.source_url) return <span className="ac-cite rec" title={tr("From our own records")}>{tr("Our records")}</span>;
  const href = safeHref(c.source_url);
  return href ? <a className="ac-cite" href={href} target="_blank" rel="noreferrer noopener" title={c.source_title ?? undefined}>{host(c.source_url)} ↗</a> : null;
}
export function usePoll(active: boolean, fn: () => void, ms = 5000) {
  useEffect(() => { if (!active) return; const t = setInterval(() => { if (!document.hidden) fn(); }, ms); return () => clearInterval(t); }, [active]);
}
/** Research costs money, so the button explains itself when it cannot run. */
export function ResearchSetup({ setup }: { setup?: any }) {
  if (!setup || setup.ready) return null;
  return <p className="ac-setup m-0">{tr("Research with Exa is not set up in this environment. An administrator adds {} to the API and the worker.", (setup.missing ?? []).join(", "))}</p>;
}

const REL_LABEL: Record<string, string> = { none: "No relationship", prospect: "Prospect", in_talks: "In talks", past_client: "Past client", client: "Client" };
const FIT_WORD: Record<string, string> = { good: "In our favour", neutral: "Neutral", bad: "Against us" };
const UKDACH = ["GB", "UK", "IE", "DE", "AT", "CH", "SE", "NO", "DK", "FI", "IS", "PL"];
const EUROPE = ["FR", "ES", "IT", "PT", "NL", "BE", "LU", "CZ", "SK", "HU", "RO", "BG", "GR", "HR", "SI", "EE", "LV", "LT", "CY", "MT", "LI", "MC"];
const FIN = /bank|insur|payment|fintech|lend|wealth|financ|credit|asset|invest|pension|brok|card|capital|leasing/i;

/** Fit points we can judge from our own records, shown until (and alongside) Exa research. */
function recordFit(a: any, lp: Record<string, string>): any[] {
  const out: any[] = [];
  const sector = a.segment || lp["Industry"];
  if (sector) out.push({ label: "Sector", value: sector, fit: FIN.test(sector) ? "good" : "neutral", source: "R" });
  const cc = String(a.country_code ?? "").toUpperCase();
  if (cc) out.push({ label: "Region", value: countryName(cc), fit: UKDACH.includes(cc) ? "good" : EUROPE.includes(cc) ? "neutral" : "bad", source: "R" });
  if (a.employees_estimate != null) {
    const min = UKDACH.includes(cc) || EUROPE.includes(cc) ? 500 : 2000;
    out.push({ label: "Size", value: staffCount(a.employees_estimate, a.employees_uncertain), fit: a.employees_estimate > min ? "good" : a.employees_estimate < 150 ? "bad" : "neutral", source: "R" });
  }
  const own = a.company_ownership;
  if (own && own !== "unknown") out.push({ label: "Ownership", value: tr(OWNERSHIP[own] ?? own), fit: own === "state_owned" ? "bad" : own === "private" ? "good" : "neutral", source: "R" });
  if (a.list_eligibility) out.push({ label: "Lead list", value: a.list_eligibility, fit: a.list_eligibility === "Eligible" ? "good" : a.list_eligibility === "Review" ? "neutral" : "bad", source: "R" });
  return out;
}

function FitLedger({ points, fromRecords }: { points: any[]; fromRecords: boolean }) {
  const n = (k: string) => points.filter((p) => p.fit === k).length;
  const order = ["good", "neutral", "bad"];
  const sorted = [...points].sort((x, y) => order.indexOf(x.fit) - order.indexOf(y.fit));
  return <>
    <div className="ac-tally">
      <span className="ac-tally-bar" aria-hidden="true">{sorted.map((p, i) => <i key={i} className={`ac-mark ${p.fit}`} />)}</span>
      <span className="ac-tally-words">{tr("{} in our favour, {} neutral, {} against", n("good"), n("neutral"), n("bad"))}</span>
    </div>
    <ul className="ac-ledger">{sorted.map((p, i) => <li key={i}>
      <i className={`ac-mark ${p.fit}`} aria-hidden="true" />
      <b>{tr(p.label)}<span className="ac-verdict">{tr(FIT_WORD[p.fit] ?? "Neutral")}</span></b>
      <span>{p.value} <Cite c={p} /></span>
    </li>)}</ul>
    {fromRecords && <p className="ac-note mt-2">{tr("Judged from our own records. Research with Exa adds the tech stack, the engineering team and current change programmes.")}</p>}
  </>;
}

const OWNERSHIP: Record<string, string> = { private: "Privately held", listed: "Publicly listed", state_owned: "State-owned", unknown: "Ownership unconfirmed" };

function CompanyProfile({ account, onSave }: { account: any; onSave: (profile: any) => void }) {
  const [form, setForm] = useState({
    ownership: account.company_ownership ?? "unknown", source_url: account.company_profile_source_url ?? "",
    country_code: account.country_code ?? "", segment: account.segment ?? "", employees: account.employees_estimate ?? "",
    employees_uncertain: account.employees_uncertain ?? false,
  });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true); setSaved(false); setErr(null);
    try {
      onSave(await put(`/api/accounts/${account.id}/company-profile`, { ...form, country_code: form.country_code || null, employees: form.employees ? Number(form.employees) : null }));
      setSaved(true);
    } catch (error) { setErr(error); } finally { setBusy(false); }
  };
  return <form className="card company-profile mt-3" onSubmit={save}>
    <h2>{tr("Company details")}</h2>
    <p className="small muted">{tr("Use the company register, annual report or company website to confirm ownership. For a subsidiary, check who owns the parent company.")}</p>
    <div className="directory-filters">
      <label>{tr("Ownership")}<select value={form.ownership} onChange={(e) => setForm({ ...form, ownership: e.target.value })}>
        {Object.entries(OWNERSHIP).map(([key, title]) => <option key={key} value={key}>{tr(title)}</option>)}
      </select></label>
      <label>{tr("Ownership source")}<input type="url" maxLength={1000} value={form.source_url} required={form.ownership !== "unknown"} placeholder="https://…" onChange={(e) => setForm({ ...form, source_url: e.target.value })} /></label>
      <label>{tr("Headquarters country code")}<input maxLength={2} pattern="[A-Z]{2}" value={form.country_code} placeholder={tr("e.g. GB")} onChange={(e) => setForm({ ...form, country_code: e.target.value.toUpperCase() })} /></label>
      <label>{tr("Sector")}<input maxLength={80} value={form.segment} placeholder={tr("e.g. Banking or Payments")} onChange={(e) => setForm({ ...form, segment: e.target.value })} /></label>
      <label>{tr("Employees")}<input type="number" min={1} max={2147483647} value={form.employees} onChange={(e) => setForm({ ...form, employees: e.target.value })} /></label>
      <label className="profile-estimate"><input type="checkbox" checked={form.employees_uncertain} onChange={(e) => setForm({ ...form, employees_uncertain: e.target.checked })} />{" " + tr("Headcount is an estimate")}</label>
    </div>
    <button className="primary" disabled={busy}>{busy ? tr("Saving…") : tr("Save company details")}</button>
    {saved && <span className="small ml-2" role="status">{tr("Saved.")}</span>}
    <Err e={err} />
  </form>;
}

/* ------------------------------------------------------------------ owner and relationship */
function OwnerControl({ rel, orgId, onChange }: { rel: any; orgId: string; onChange: (owner: any) => void }) {
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (!rel) return <span className="small">{tr("Loading")}</span>;
  const owner = rel.owner;
  const set = async (userId: string | null) => {
    setErr(null); setBusy(true);
    try { const r = await put(`/api/accounts/${orgId}/owner`, { user_id: userId }); onChange(r.owner); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  let control: ReactNode = null;
  if (rel.can_assign_others) {
    control = <select aria-label={tr("Owner")} value={owner?.user_id ?? ""} disabled={busy} onChange={(e) => set(e.target.value || null)}>
      <option value="">{tr("Unassigned")}</option>
      {rel.team.map((u: any) => <option key={u.id} value={u.id}>{u.display_name}</option>)}
    </select>;
  } else if (rel.can_edit && !owner) {
    control = <button type="button" className="linkish" disabled={busy} onClick={() => set(rel.me)}>{tr("Assign to me")}</button>;
  } else if (rel.can_edit && owner?.user_id === rel.me) {
    control = <button type="button" className="linkish" disabled={busy} onClick={() => set(null)}>{tr("Release")}</button>;
  }
  return <>
    {rel.can_assign_others ? null : <>{owner ? owner.name : tr("Unassigned")} </>}
    {control}
    {owner?.assigned_at && <div className="small">{tr("since {}", fmtDate(owner.assigned_at))}</div>}
    <Err e={err} />
  </>;
}

function RelationshipPanel({ rel, orgId, onSaved }: { rel: any; orgId: string; onSaved: () => void }) {
  const rec = rel?.recorded;
  const [status, setStatus] = useState<string | null>(rec?.status ?? null);
  const [note, setNote] = useState<string>(rec?.note ?? "");
  const [editing, setEditing] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { setStatus(rec?.status ?? null); setNote(rec?.note ?? ""); }, [rec?.status, rec?.note]);
  if (!rel) return <section className="ac-panel accent"><h2>{tr("Relationship with us")}</h2><Skeleton rows={2} /></section>;
  const save = async (s: string | null = status) => {
    setErr(null);
    try { await put(`/api/accounts/${orgId}/relationship`, { status: s, note: note.trim() || null }); setEditing(false); onSaved(); } catch (e) { setErr(e); }
  };
  const o = rel.outreach;
  const ll = rel.lead_list;
  return <section className="ac-panel accent ac-rel" aria-labelledby="rel-h">
    <h2 id="rel-h">{tr("Relationship with us")}</h2>
    {rec?.status ? <p><b>{tr(REL_LABEL[rec.status])}</b>{rec.note && <><br /><span className="small">{rec.note}</span></>}
      <br /><span className="ac-note">{tr("Recorded by {} on {}", rec.set_by, fmtDate(rec.set_at))}</span></p>
      : <p><b>{tr("Not recorded yet.")}</b> <span className="small">{tr("Our data suggests: {}.", tr(REL_LABEL[rel.suggested]))} {tr(rel.suggested_why)}</span></p>}
    {rel.can_edit && !editing && <div className="row gap-2">
      {!rec?.status && <button type="button" onClick={() => save(rel.suggested)}>{tr("Record as {}", tr(REL_LABEL[rel.suggested]).toLowerCase())}</button>}
      <button type="button" className="ghost" onClick={() => setEditing(true)}>{rec?.status ? tr("Change") : tr("Choose another")}</button>
    </div>}
    {editing && <div className="mt-2">
      <div className="ac-rel-options" role="group" aria-label={tr("Relationship")}>
        {Object.keys(REL_LABEL).map((k) => <button key={k} type="button" className="chip" aria-pressed={status === k} onClick={() => setStatus(k)}>{tr(REL_LABEL[k])}</button>)}
      </div>
      <label className="small">{tr("Note (what we did with them, who knows them)")}<textarea maxLength={600} value={note} onChange={(e) => setNote(e.target.value)} /></label>
      <div className="row gap-2 mt-2"><button type="button" className="primary" onClick={() => save()}>{tr("Save relationship")}</button><button type="button" className="ghost" onClick={() => setEditing(false)}>{tr("Cancel")}</button></div>
    </div>}
    <Err e={err} />
    <ul className="ac-evidence">
      <li>{ll.on_list ? tr("On your lead list ({}{}{}).", ll.account_id ?? "", ll.eligibility ? `, ${ll.eligibility}` : "", ll.priority ? `, ${ll.priority}` : "") : tr("Not on your lead list.")}</li>
      <li>{tr("{} people known, {} with an email ({} verified).", number(rel.people.n), number(rel.people.with_email), number(rel.people.verified))}</li>
      <li>{o.contacted ? tr("Outreach: {}. Last activity {}.", tr(o.engagement_label), fmtDate(o.last_at)) : tr("Never contacted from this platform.")}
        {o.bounces > 0 && <> {tr("{} bounced.", o.bounces)}</>}{o.unsubscribes > 0 && <> {tr("{} unsubscribed.", o.unsubscribes)}</>}</li>
      {rel.campaigns.length > 0 && <li>{tr("In signal campaigns:")} {rel.campaigns.map((c: any, i: number) => <span key={c.id}>{i > 0 && ", "}<Link to={`/campaigns/${c.id}`}>{c.name}</Link></span>)}</li>}
      {rel.drafts.n > 0 && <li>{tr("{} outreach drafts written, {} approved.", rel.drafts.n, rel.drafts.approved)}</li>}
      {rel.deals.linked && <li>{tr("Deals: {} open, {} won, {} lost.", rel.deals.open, rel.deals.won, rel.deals.lost)}</li>}
    </ul>
  </section>;
}

/* ------------------------------------------------------------------ research */
export function ResearchPanel({ r, canRun, start, busy, err }: { r: any; canRun: boolean; start: () => void; busy: boolean; err: unknown }) {
  if (!r) return null;
  const p = r.profile;
  const last = r.latest;
  if (!p && !r.running && last?.status !== "failed") return null;          // nothing to report: the empty overview already explains research
  return <section className="ac-panel" aria-labelledby="research-h">
    <h2 id="research-h">{tr("Research with Exa")}</h2>
    <div className="ac-research">
      {r.running ? <div className="busy" role="status"><span className="ac-pulse" />{tr("Researching. This takes about a minute.")}</div>
        : p ? <p className="small m-0">{tr("Last researched {}. {} searches, {} pages read.", fmtDateTime(p.finished_at), p.searches, (p.sources ?? []).length)}
          {p.dropped > 0 && <> {tr("Left out because their page did not support them: {}.", p.dropped)}</>}</p>
          : <p className="small m-0">{tr("Not researched yet.")}</p>}
      {last?.status === "failed" && !r.running && <div className="err m-0" role="alert">{tr("The last attempt failed: {}", last.error ?? tr("unknown error"))}</div>}
      {p?.content?.partial && <p className="small m-0">{(p.content.notes ?? []).join(" ")}</p>}
      <ResearchSetup setup={r.setup} />
      {canRun && <div><button type="button" disabled={busy || r.running || !r.setup?.ready} onClick={start}>{p ? tr("Research again") : tr("Research with Exa")}</button></div>}
      <Err e={err} />
      {p?.sources?.length > 0 && <details><summary className="small">{tr("Pages read ({})", p.sources.length)}</summary>
        <ul className="tight small mt-2">{p.sources.map((s: any) => <li key={s.id}><a href={safeHref(s.url)} target="_blank" rel="noreferrer noopener">{s.title || host(s.url)}</a> <span className="muted">{host(s.url)}{s.published ? `, ${fmtDay(s.published)}` : ""}</span></li>)}</ul></details>}
    </div>
  </section>;
}

export function Sec({ title, hint, children, id }: { title: string; hint?: string; children: ReactNode; id?: string }) {
  return <section className="ac-sec" aria-labelledby={id}>
    <h2 id={id}>{tr(title)}{hint && <small>{tr(hint)}</small>}</h2>
    <div>{children}</div>
  </section>;
}

/** What the lead list says about the account: not checked by the platform. */
function ListProfile({ p }: { p: Record<string, string> }) {
  const text: [string, string | undefined][] = [["Strongest signal", p["Strongest Buying Signal"]], ["Possible work for us", p["Potential Opportunity"]], ["How The Codest fits", p["The Codest Service Fit"]],
    ["Next step", p["Next Action"]], ["Still missing", p["Coverage Gap"]]];
  const facts: [string, string | undefined][] = [["Segment", p["ICP Segment"]], ["Buying stage", p["Buying Stage"]], ["Signal strength", p["Signal Strength"]], ["Last researched", p["Last Researched"]]];
  const route = p["Procurement Route"];
  return <>
    {text.filter(([, v]) => v).map(([k, v]) => <p key={k} className={k === "Still missing" ? "small" : ""}><b>{tr(k)}:</b> {v}</p>)}
    {(facts.some(([, v]) => v) || route) && <dl className="ac-kv mt-2">{facts.filter(([, v]) => v).map(([k, v]) => <Fragment key={k}><dt>{tr(k)}</dt><dd>{v}</dd></Fragment>)}
      {route && <><dt>{tr("How they buy")}</dt><dd>{safeHref(route) ? <a href={safeHref(route)} target="_blank" rel="noreferrer noopener">{host(route)} ↗</a> : route}</dd></>}</dl>}
    {p["Primary Source URL"] && safeHref(p["Primary Source URL"]) && <p className="mt-2"><a className="ac-cite" href={safeHref(p["Primary Source URL"])} target="_blank" rel="noreferrer noopener">{host(p["Primary Source URL"])} ↗</a></p>}
  </>;
}

function Overview({ d, research, rel, orgId, me, canRun, startResearch, researchBusy, researchErr, reloadRel, goto }: any) {
  const a = d.account;
  const lp: Record<string, string> = d.list_profile ?? {};
  const c = research?.profile?.content;
  const fit = c?.icp_points?.length ? c.icp_points : recordFit(a, lp);
  const suppliers = d.suppliers ?? [];
  return <div className="ac-grid">
    <div className="ac-sheet">
      {c ? <>
        {(c.about || c.products?.length > 0) && <Sec title="What they do" id="sec-about">
          {c.about && <p className="ac-lead">{c.about.text} {c.about.sources.map((s: any, i: number) => <Cite key={i} c={s} />)}</p>}
          {c.products?.length > 0 && <div className="ac-tags mt-2" aria-label={tr("Products and services")}>{c.products.map((p: any, i: number) => <span key={i} className="ac-tag" title={p.detail ?? undefined}>{p.name}</span>)}</div>}
          {c.footprint?.markets && <p className="small mt-2"><b>{tr("Markets")}:</b> {c.footprint.markets.value} <Cite c={c.footprint.markets} /></p>}
        </Sec>}
      </> : <section className="ac-sec"><h2>{tr("What they do")}</h2><div>
        {research?.running ? <div className="ac-research"><div className="busy" role="status"><span className="ac-pulse" />{tr("Researching with Exa. The overview fills in when it is done, in about a minute.")}</div></div> : <>
          <p>{tr("No research yet. Research with Exa reads the company's website, its news from the last 12 months, partner and vendor announcements and hiring pages, then writes this overview with a link to the page behind every statement.")}</p>
          <p className="small">{tr("About four searches, a few US cents, taken from the monthly Exa budget.")}</p>
          <ResearchSetup setup={research?.setup} />
          {canRun && research?.setup?.ready && <button type="button" className="primary mt-2" disabled={researchBusy} onClick={startResearch}>{tr("Research with Exa")}</button>}
          <Err e={researchErr} /></>}
      </div></section>}
      <Sec title="Why they fit us" hint="Points of our ideal customer profile" id="sec-fit">
        {fit.length ? <FitLedger points={fit} fromRecords={!c?.icp_points?.length} /> : <p className="small">{tr("We hold too little about this company to judge its fit. Edit the company details or research it with Exa.")}</p>}
      </Sec>
      {c && <Sec title="What they're doing now" hint="Last 12 months, from the sources" id="sec-now">
        {c.recent?.length ? <ul className="ac-tl">{c.recent.map((x: any, i: number) => <li key={i}>
          <time dateTime={x.date ?? undefined}>{fmtDay(x.date)}</time>
          <div className="ac-tl-body"><b>{x.headline}</b>{x.detail && <p>{x.detail}</p>}<Cite c={x} /></div>
        </li>)}</ul> : <p className="small">{tr("Exa found no dated news about this company in the last 12 months.")}</p>}
      </Sec>}
      {(c?.vendors?.length > 0 || suppliers.length > 0 || c) && <Sec title="Vendors & partners" hint="Who they buy technology from" id="sec-vendors">
        {(c?.vendors?.length > 0 || suppliers.length > 0) ? <div className="ac-vendors">
          {(c?.vendors ?? []).map((v: any, i: number) => <div key={`v${i}`} className="ac-vendor"><b>{v.name}</b>{v.role && <span>{v.role}</span>}<Cite c={v} /></div>)}
          {suppliers.map((x: any, i: number) => <div key={`s${i}`} className="ac-vendor"><b>{x.supplier_name}</b><span>{[humanize(x.supplier_role), x.scope_text].filter(Boolean).join(": ")}</span><span className="ac-cite rec">{tr("Our records")}</span></div>)}
        </div> : <p className="small">{tr("No vendors or integrators are named in the pages Exa found. Ask about current suppliers in the first call.")}</p>}
        {(c?.vendors?.length > 0 || suppliers.length > 0) && <p className="ac-note mt-2">{tr("A named vendor does not mean an exclusive contract or that they cover everything.")}</p>}
      </Sec>}
      {c?.how_we_help?.length > 0 && <Sec title="How we could help" hint="Suggested angles, check before you use them" id="sec-help">
        <ul className="ac-angles">{c.how_we_help.map((h: any, i: number) => <li key={i}><b>{h.angle}</b>{h.why && <span className="small">{h.why} </span>}{h.sources.map((s: any, k: number) => <Cite key={k} c={s} />)}</li>)}</ul>
      </Sec>}
      {Object.keys(lp).length > 0 && <Sec title="From your lead list" hint="Not checked by the platform" id="sec-list"><ListProfile p={lp} /></Sec>}
      {d.opportunities.length > 0 && <Sec title="Opportunities" id="sec-opps">
        {d.opportunities.map((o: any) => <div key={o.hypothesis_id} className="mb-3"><div className="row spread"><span><Badge kind={priorityKind(o.priority)}>{lab("priority", o.priority)}</Badge> <b>{o.initiative.title}</b></span><Link to={`/opportunities/${o.hypothesis_id}`}>{tr("Open")}</Link></div>
          <p className="small">{o.plain_reason ?? whyShort(o.why_it_matters)}</p>{o.next_action && <p className="small"><b>{tr("Next:")}</b> {o.next_action.objective}</p>}</div>)}
      </Sec>}
      {c?.open_questions?.length > 0 && <Sec title="Still to find out" id="sec-q"><ul className="tight">{c.open_questions.map((q: string, i: number) => <li key={i}>{q}</li>)}</ul></Sec>}
    </div>
    <aside className="ac-rail" aria-label={tr("Relationship and research")}>
      <RelationshipPanel rel={rel} orgId={orgId} onSaved={reloadRel} />
      <section className="ac-panel" aria-labelledby="sig-h"><h2 id="sig-h">{tr("Strongest buying signal")}</h2>
        <SignalSummary signal={d.buying_signal} detailed />
        {d.buying_signal?.rule_id && me.permissions.includes("draft.create") && <p className="small mt-2 mb-0"><Link to={`/campaigns/new?rules=${d.buying_signal.rule_id}`}>{tr("Start a campaign to every company with this signal")}</Link></p>}
        {d.score && !d.score.stopped && <p className="small mt-2 mb-0">{tr("Signal score {} ({}) from {} signals: {}.", d.score.score, tr(BAND[d.score.band]?.[1] ?? d.score.band), d.score.signal_count, d.score.groups.map(groupName).join(", "))}</p>}
        {d.score?.stopped && <p className="small mt-2 mb-0">{tr("On hold: something we found says not to pursue this account now.")}</p>}
        <p className="small mt-2 mb-0"><button type="button" className="linkish" onClick={() => goto("activity", "signals")}>{tr("All signals, findings and tenders")}</button></p>
      </section>
      <ResearchPanel r={research} canRun={canRun} start={startResearch} busy={researchBusy} err={researchErr} />
    </aside>
  </div>;
}

/* ------------------------------------------------------------------ people */
const DECIDER = /executive|c-level|chief|\bc[tiod]o\b|vp|vice president|director|head/i;

function FindPeople({ orgId, onAdded }: { orgId: string; onAdded: () => void }) {
  const [s, setS] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState("");
  const [open, setOpen] = useState(false);
  const load = () => api(`/api/accounts/${orgId}/people-search`).then(setS).catch(setErr);
  useEffect(() => { load(); }, [orgId]);
  usePoll(Boolean(s?.running), load, 4000);
  if (!s) return null;
  const run = async () => { setErr(null); setBusy("run"); try { await post(`/api/accounts/${orgId}/people-search`); setOpen(true); await load(); } catch (e) { setErr(e); } finally { setBusy(""); } };
  const act = async (id: string, what: "add" | "dismiss") => {
    setErr(null); setBusy(id);
    try { await post(`/api/people-candidates/${id}/${what}`); await load(); if (what === "add") onAdded(); } catch (e) { setErr(e); } finally { setBusy(""); }
  };
  const found = s.items.filter((c: any) => c.status === "found");
  const known = s.items.filter((c: any) => c.status !== "found");
  const last = s.search;
  return <section className="ac-find" aria-labelledby="find-h">
    <div className="row spread gap-2">
      <div className="measure-sm">
        <h2 id="find-h">{tr("Find decision makers with Exa")}</h2>
        <p className="small m-0">{tr("Searches LinkedIn profiles for technology, engineering, delivery, data, payments and procurement leaders at this company. Names, job titles and LinkedIn links only, no email addresses. You choose who to add.")}</p>
      </div>
      {s.can_run && <button type="button" className={found.length ? "" : "primary"} disabled={Boolean(busy) || s.running || !s.setup.ready} onClick={run}>{last ? tr("Search again") : tr("Find decision makers")}</button>}
    </div>
    {!s.setup.ready && <p className="ac-setup mt-2 mb-0">{tr("Finding people with Exa is not set up in this environment. An administrator adds EXA_API_KEY and RI_EXA_MONTHLY_CAP_USD to the API and the worker.")}</p>}
    {s.running && <div className="ac-research mt-2"><div className="busy" role="status"><span className="ac-pulse" />{tr("Searching five parts of the buying group. About half a minute.")}</div></div>}
    {last?.status === "failed" && !s.running && <div className="err" role="alert">{tr("The last search failed: {}", last.error ?? "")}</div>}
    {last?.status === "done" && <p className="small mt-2 mb-0">{tr("Last search {}: {} found, {} new, {} already in your people list, {} left out because the job title is not a decision role.", fmtDateTime(last.finished_at), last.found, last.new_candidates, last.already_known, last.skipped)}{last.error && <> {last.error}</>}</p>}
    <Err e={err} />
    {found.length > 0 && <ul className="ac-cands" aria-label={tr("People found")}>{found.map((c: any) => <li key={c.id}>
      <div className="who"><b>{c.name}</b> {c.linkedin_url && <a className="small" href={safeHref(c.linkedin_url)} target="_blank" rel="noreferrer noopener">LinkedIn ↗</a>}
        <span className="small">{c.title}{c.persona ? `, ${tr(c.persona)}` : ""}</span>
        {c.company_match === "profile" && <span className="small muted">{tr("The company appears in the profile, not the headline: check they still work there.")}</span>}</div>
      <div className="acts"><button type="button" className="primary" disabled={Boolean(busy)} onClick={() => act(c.id, "add")}>{tr("Add to people")}</button>
        <button type="button" className="ghost" disabled={Boolean(busy)} onClick={() => act(c.id, "dismiss")}>{tr("Dismiss")}</button></div>
    </li>)}</ul>}
    {known.length > 0 && <details className="mt-2" open={open && !found.length}><summary className="small">{tr("{} already in your people list", known.length)}</summary>
      <ul className="tight small mt-2">{known.map((c: any) => <li key={c.id}>{c.person_id ? <Link to={`/people/${c.person_id}`}>{c.name}</Link> : c.name}, {c.title}</li>)}</ul></details>}
  </section>;
}

function PersonCard({ p }: { p: any }) {
  const ll = p.lead_list ?? {};
  const prio = ll.priority;
  const useful = (v?: string | null) => (v && !/^other\s*\/\s*review$/i.test(v) ? v : null);
  const seniority = useful(p.seniority), persona = useful(p.persona);
  return <li className={`ac-pcard${prio === "P1" ? " p1" : ""}${p.suppressed ? " off" : ""}`}>
    <div className="ac-pcard-head">
      <span className="ac-mono sm dark" aria-hidden="true">{initials(p.name)}</span>
      <div><Link className="name" to={`/people/${p.id}`}>{p.name}</Link><span className="title">{p.title || tr("Role not recorded")}</span></div>
    </div>
    {(prio || seniority || persona || ll.committee_role) && <div className="ac-tags">
      {prio && <span className={`ac-tag ${prio.toLowerCase()}`} title={tr("Contact priority on your lead list")}>{prio}</span>}
      {seniority && <span className="ac-tag">{tr(seniority)}</span>}
      {ll.committee_role && <span className="ac-tag">{tr(ll.committee_role)}</span>}
      {!ll.committee_role && persona && <span className="ac-tag">{tr(persona)}</span>}
    </div>}
    {ll.angle && <p className="ac-angle">{ll.angle}</p>}
    <div className="ac-status">
      <span><i className={`ac-dot ${p.email === "verified" ? "ok" : p.email === "unverified" ? "warn" : ""}`} />{p.email === "verified" ? tr("Verified email") : p.email === "unverified" ? tr("Email not verified") : tr("No email")}</span>
      <span><i className={`ac-dot ${p.role_confirmed ? "ok" : ""}`} />{p.role_confirmed ? tr("Role confirmed") : tr("Role not confirmed")}</span>
      {p.linkedin_url && safeHref(p.linkedin_url) ? <a href={safeHref(p.linkedin_url)} target="_blank" rel="noreferrer noopener">LinkedIn ↗</a> : <span><i className="ac-dot" />{tr("No LinkedIn")}</span>}
      {p.engagement && <span><i className={`ac-dot ${["replied", "interested"].includes(p.engagement) ? "ok" : ["bounced", "unsubscribed"].includes(p.engagement) ? "bad" : "info"}`} />{tr(p.engagement_label)}</span>}
      {p.researched_at && <span><i className="ac-dot info" />{tr("Researched")}</span>}
      {p.suppressed && <span><i className="ac-dot bad" />{tr("Do not contact")}</span>}
    </div>
  </li>;
}

function PeopleTab({ orgId, me }: { orgId: string; me: { permissions: string[] } }) {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [filter, setFilter] = useState("all");
  const load = () => api(`/api/accounts/${orgId}/people`).then(setD).catch(setErr);
  useEffect(() => { load(); }, [orgId]);
  if (!me.permissions.includes("contacts.read")) return <div className="empty">{tr("Your role cannot see contact details.")}</div>;
  if (err) return <Err e={err} onRetry={load} />;
  if (!d) return <Skeleton rows={4} label={tr("Loading people")} />;
  const tests: Record<string, (p: any) => boolean> = {
    all: () => true, deciders: (p) => DECIDER.test(p.seniority ?? "") || DECIDER.test(p.title ?? ""), verified: (p) => p.email === "verified",
    linkedin: (p) => Boolean(p.linkedin_url), not_contacted: (p) => !p.engagement && !p.suppressed,
  };
  const labels: Record<string, string> = { all: "Everyone", deciders: "Decision makers", verified: "Verified email", linkedin: "On LinkedIn", not_contacted: "Not contacted" };
  const shown = d.items.filter(tests[filter]);
  return <>
    <FindPeople orgId={orgId} onAdded={load} />
    <div className="ac-toolbar">
      <div className="chips" role="group" aria-label={tr("Filter people")}>{Object.keys(tests).map((k) => <button key={k} type="button" className="chip" aria-pressed={filter === k} onClick={() => setFilter(k)}>{tr(labels[k])}<b>{d.items.filter(tests[k]).length}</b></button>)}</div>
      <span className="small muted">{tr("Email addresses are never shown. Every look at personal data is logged.")}</span>
    </div>
    {d.items.length === 0 ? <div className="empty"><b>{tr("No people recorded yet")}</b>{tr("Use “Find decision makers” above, or import the lead list.")}</div>
      : shown.length === 0 ? <p className="muted">{tr("Nobody matches this filter.")}</p>
      : <ul className="ac-people">{shown.map((p: any) => <PersonCard key={p.id} p={p} />)}</ul>}
  </>;
}

/* ------------------------------------------------------------------ outreach (drafts and the Lemlist panel) */
function SendPanel({ draft, orgId, can }: { draft: any; orgId: string; can: boolean }) {
  const [plan, setPlan] = useState<any>(null); const [sel, setSel] = useState<string[]>([]); const [en, setEn] = useState<any>({ items: [] });
  const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null);
  const load = () => { api(`/api/accounts/${orgId}/contacts-plan`).then(setPlan).catch(() => setPlan(null)); api(`/api/campaigns/${draft.id}/enrolments`).then(setEn).catch(() => undefined); };
  useEffect(load, [draft.id]);
  if (!plan) return null;
  const ready = en.outbound_enabled === true && en.lemlist_configured === true;
  const unverified = plan.eligible.filter((c: any) => !c.verified).length;
  const unavailable = [
    [plan.manual_review, tr("need manual review")], [plan.no_email, tr("without an email")], [plan.suppressed, tr("suppressed or opted out")],
    [plan.already_enrolled ?? 0, tr("already enrolled")],
  ].filter(([count]) => Number(count) > 0).map(([count, reason]) => `${count} ${reason}`);
  const toggle = (id: string) => setSel((x) => x.includes(id) ? x.filter((i) => i !== id) : [...x, id]);
  const request = async () => { setErr(null); setMsg(""); try { const r = await post(`/api/campaigns/${draft.id}/enrol`, { person_ids: sel }); setMsg(tr("Approval requested for {} {}. A colleague approves it under Administration, Approvals.", r.contacts, r.contacts === 1 ? tr("contact") : tr("contacts"))); setSel([]); load(); } catch (e) { setErr(e); } };
  return (
    <div className="card mt-3">
      <div className="row spread"><b>{tr("Prepare a Lemlist campaign")}</b>{typeof en.lemlist_configured === "boolean" && <Badge kind={ready ? "ok" : "warn"}>{ready ? tr("Ready for approval") : tr("Lemlist setup required")}</Badge>}</div>
      <p className="small muted mt-1 mb-2">{tr("Everyone with an email address can be added, verified addresses first. We build the campaign in Lemlist but do not launch it: a person presses Launch there.") + " "}
        {unverified > 0 && tr("Addresses that are not verified may bounce, and a bounce suppresses that person everywhere.") + " "}
        {en.outbound_enabled === false && tr("Sending is switched off for now.")}{en.lemlist_configured === false && tr("Lemlist is not connected yet.")}</p>
      {plan.eligible.length === 0 && <p className="small">{tr("No contacts are eligible for this campaign.")}</p>}
      {plan.eligible.map((c: any) => <label key={c.id} className="row small gap-2 mb-1"><input type="checkbox" checked={sel.includes(c.id)} onChange={() => toggle(c.id)} disabled={!can || !ready} /> {c.name}{c.title ? `, ${c.title}` : ""}
        <Badge kind={c.verified ? "ok" : "warn"}>{c.verified ? tr("Verified") : tr("Not verified")}</Badge></label>)}
      {unavailable.length > 0 && <p className="small muted">{tr("Unavailable for outreach:") + " "}{unavailable.join(", ")}.</p>}
      {can && <button className="primary" disabled={!ready || sel.length === 0} onClick={request}>{tr("Ask for approval to add") + " "}{sel.length || ""}</button>}
      {msg && <div className="okmsg mt-2" role="status">{msg}</div>}<Err e={err} />
      {en.items.length > 0 && <div className="table-wrap mt-3" tabIndex={0} role="region" aria-label={tr("Contact, status and last activity")}><table><thead><tr><th>{tr("Contact")}</th><th>{tr("Status")}</th><th>{tr("Last activity")}</th></tr></thead><tbody>{en.items.map((x: any) => <tr key={x.id}><td>{x.full_name ?? tr("Contact")}</td><td><Badge kind={x.status === "interested" ? "ok" : x.status === "unsubscribed" || x.status === "bounced" ? "bad" : "info"}>{humanize(x.status)}</Badge></td><td>{x.last_event_at ? fmtDateTime(x.last_event_at) : tr("No reply yet")}</td></tr>)}</tbody></table></div>}
    </div>
  );
}

function CampaignTab({ camps, canDraft, orgId, make, decide }: { camps: any; canDraft: boolean; orgId: string; make: (role: string) => void; decide: (id: string, d: string) => void }) {
  const [role, setRole] = useState("");
  const [copied, setCopied] = useState("");
  const b = camps.items[0];
  const busy = b && (b.status === "queued" || b.status === "running");
  const copy = async (k: string, t: string) => { try { await navigator.clipboard.writeText(t); setCopied(k); setTimeout(() => setCopied(""), 1500); } catch { /* clipboard unavailable */ } };
  return (
    <div className="card">
      <p className="small muted mt-0">{tr("Three emails written from facts we have checked about this company, and tested against The Codest's brand rules. These are drafts: nothing is sent from here.")}</p>
      {canDraft && <div className="row mb-3">
        <label className="small">{tr("Who is it for?")}<select id="camp-role" value={role} onChange={(e) => setRole(e.target.value)} className="ml-2"><option value="">{tr("Technology or delivery leader")}</option><option value="CTO">CTO</option><option value="Head of Engineering">{tr("Head of Engineering")}</option><option value="Head of Payments">{tr("Head of Payments")}</option><option value="Head of Delivery or PMO">{tr("Head of Delivery or PMO")}</option><option value="Procurement lead">{tr("Procurement lead")}</option></select></label>
        <button className="primary" disabled={busy} onClick={() => make(role)}>{tr("Write outreach sequence")}</button></div>}
      {!b && <p className="muted">{tr("No draft yet. Research the company with Exa first for the best result: the emails are built from the facts we hold.")}</p>}
      {busy && <p>{tr("Writing and checking the draft. This takes about a minute.")}</p>}
      {b?.status === "failed" && <Err e={new Error(b.error || "The draft could not be written.")} />}
      {b && (b.status === "draft" || b.status === "needs_work" || b.status === "approved" || b.status === "rejected") && <>
        <div className="row mt-2 mb-3">
          <Badge kind={b.status === "approved" ? "ok" : b.status === "draft" ? "info" : b.status === "rejected" ? "" : "warn"}>{b.status === "draft" ? tr("Passed the brand review") : b.status === "needs_work" ? tr("Needs work") : humanize(b.status)}</Badge>
          <span className="small muted">{b.rounds > 1 ? tr("Rewritten {} times to fix rule breaks.", b.rounds - 1) + " " : ""}{tr("Written") + " "}{fmtDateTime(b.finished_at)}{b.decided_by ? `, ${humanize(b.status).toLowerCase()} ${tr("by")} ${b.decided_by}` : ""}</span></div>
        {b.review?.breaks?.length > 0 && <div className="err" role="alert"><b>{tr("Fix these before approving:")}</b><ul className="tight">{b.review.breaks.map((x: any, i: number) => <li key={i}>{x.step ? tr("Email {}: ", x.step) : ""}{x.detail}</li>)}</ul></div>}
        {(b.steps ?? []).map((st: any, i: number) => (
          <article key={i} className="fcard">
            <div className="row spread"><b>{tr("Email") + " "}{i + 1}</b><span className="row small muted">{tr("Day {}", st.day)}<button className="chip" onClick={() => copy(`${b.id}-${i}`, `Subject: ${st.subject}\n\n${st.body}`)}>{copied === `${b.id}-${i}` ? tr("Copied") : tr("Copy")}</button></span></div>
            <p className="mt-2 mb-1"><b>{tr("Subject:")}</b> {st.subject}</p>
            <div className="pre-wrap">{st.body}</div>
          </article>))}
        {canDraft && b.status === "draft" && <div className="row"><button className="primary" onClick={() => decide(b.id, "approve")}>{tr("Approve draft")}</button><button onClick={() => decide(b.id, "reject")}>{tr("Reject")}</button></div>}
        {b.status === "approved" && <SendPanel draft={b} orgId={orgId} can={canDraft} />}
        {b.facts && Object.keys(b.facts).length > 0 && <details className="mt-3"><summary>{tr("Facts these emails are built on")}</summary><ul className="tight small">{Object.entries(b.facts).map(([k, v]: any) => <li key={k}><b>{k}</b> {v}</li>)}</ul></details>}
      </>}
    </div>
  );
}

/* ------------------------------------------------------------------ activity */
const BAND: Record<string, ["ok" | "warn" | "info" | "bad" | "", string]> = { hot: ["bad", "Hot"], warm: ["warn", "Warm"], watch: ["info", "Watch"], none: ["", "No signal"] };
const DEMAND_TIER: Record<string, string> = { D1: "They asked for outside help", D2: "Likely to need outside help", D3: "Checked programme, outside demand unknown", D4: "Attention, outside demand unknown" };
const SUBS = ["timeline", "signals", "tenders", "list", "leads", "sources"] as const;
const SUB_LABEL: Record<string, string> = { timeline: "Findings timeline", signals: "Signals", tenders: "Tenders & RFPs", list: "Lead list signals", leads: "Assigned links", sources: "Sources we watch" };

function Tenders({ orgId, route }: { orgId: string; route?: string }) {
  const [t, setT] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { api(`/api/accounts/${orgId}/tenders`).then(setT).catch(setErr); }, [orgId]);
  if (err) return <Err e={err} />;
  if (!t) return <Skeleton rows={2} />;
  if (!t.notices.length && !t.awards.length) return <div className="card"><p className="m-0">{tr("No public tenders or contract awards name this company as the buyer.")}</p>
    <p className="small mt-2 mb-0">{tr("Most private banks, insurers and payment companies buy without a public tender.")}{route && safeHref(route) && <> {tr("Their supplier route from your lead list:")} <a href={safeHref(route)} target="_blank" rel="noreferrer noopener">{host(route)} ↗</a></>}</p></div>;
  return <>
    {t.notices.length > 0 && <div className="card"><h3 className="mt-0">{tr("Tenders where they are the buyer")}</h3>
      {t.notices.map((n: any) => <div key={n.id} className="ac-tender"><div><b>{n.title || tr("Untitled notice")}</b>
        <div className="small">{[humanize(n.status), n.country, n.published_at ? tr("published {}", fmtDate(n.published_at)) : null].filter(Boolean).join(", ")}</div></div>
        <div className="small">{n.deadline_at && <div><b>{tr("Deadline")}</b> {fmtDate(n.deadline_at)}</div>}{n.official_url && safeHref(n.official_url) && <a href={safeHref(n.official_url)} target="_blank" rel="noreferrer noopener">{tr("Official notice")} ↗</a>}</div></div>)}</div>}
    {t.awards.length > 0 && <div className="card"><h3 className="mt-0">{tr("Contracts they awarded")}</h3>
      {t.awards.map((a: any) => <div key={a.notice_id} className="ac-tender"><div><b>{a.title || tr("Untitled award")}</b><div className="small">{tr("Won by {}", (a.winners ?? []).join(", ") || tr("not stated"))}</div></div>
        <div className="small">{a.awarded_on && <div>{tr("Awarded {}", fmtDate(a.awarded_on))}</div>}{a.expected_end && <div>{tr("Ends {}", fmtDate(a.expected_end))}{a.end_basis === "assumed" ? ` (${tr("estimate")})` : ""}</div>}</div></div>)}</div>}
  </>;
}

function Activity({ d, eps, sub, setSub, params, sigNames }: any) {
  const [signalStatus, setSignalStatus] = useState("all");
  const [signalTier, setSignalTier] = useState("all");
  const [signalOrder, setSignalOrder] = useState("newest");
  const a = d.account;
  const groups = new Map<string, any[]>();
  for (const s of d.signals) {
    if ((signalStatus !== "all" && s.status !== signalStatus) || (signalTier !== "all" && s.demand_tier !== signalTier)) continue;
    const key = s.story_key ?? s.event_id;
    groups.set(key, [...(groups.get(key) ?? []), s]);
  }
  const stories = [...groups.values()].sort(([x], [y]) => {
    const dx = x.event_at ?? x.pub_at, dy = y.event_at ?? y.pub_at;
    if (!dx) return dy ? 1 : 0; if (!dy) return -1;
    const o = Date.parse(dx) - Date.parse(dy); return signalOrder === "oldest" ? o : -o;
  });
  const counts: Record<string, number | undefined> = { signals: new Set(d.signals.map((s: any) => s.story_key ?? s.event_id)).size, list: (d.list_signals ?? []).length };
  return <>
    <div className="ac-subnav" role="group" aria-label={tr("Activity sections")}>
      {SUBS.map((k) => <button key={k} type="button" className="chip" aria-pressed={sub === k} onClick={() => setSub(k)}>{tr(SUB_LABEL[k])}{counts[k] != null && <b>{counts[k]}</b>}</button>)}
    </div>
    {sub === "timeline" && <AccountFindings key={"t" + (params.get("finding") ?? "")} accountId={a.id} selected={params.get("finding")} leadsOnly={false} />}
    {sub === "leads" && <AccountFindings key="leads" accountId={a.id} selected={null} leadsOnly />}
    {sub === "tenders" && <Tenders orgId={a.id} route={d.list_profile?.["Procurement Route"]} />}
    {sub === "list" && <div className="card">
      <p className="small muted mt-0">{tr("Buying signals recorded in your lead list, with the source the list gives. Not checked by the platform.")}</p>
      {(d.list_signals ?? []).length === 0 ? <p className="m-0">{tr("Your lead list records no signals for this company.")}</p>
        : <ul className="ac-tl">{d.list_signals.map((x: any, i: number) => <li key={i}><time>{fmtDay(x.date)}</time><div className="ac-tl-body">
          <b>{x.event}</b><p>{[x.category, x.confidence ? tr("{} confidence", x.confidence) : null].filter(Boolean).join(", ")}</p>
          {x.implication && <p>{x.implication}</p>}{x.source_url && <Cite c={{ source_url: x.source_url, source: "list" }} />}</div></li>)}</ul>}
    </div>}
    {sub === "signals" && <>
      {d.initiatives.length > 0 && <><h3>{tr("Programmes")}</h3>{d.initiatives.map((i: any) => <div key={i.id} className="card"><b>{i.title}</b> {!i.title_is_official && <Badge kind="warn">{tr("Our working title")}</Badge>} <Badge>{humanize(i.stage)}</Badge> {!i.budget_verified && <Badge kind="warn">{tr("Budget not confirmed")}</Badge>}
        <p className="small">{tr("Technology:") + " "}{i.technology_tags.join(", ") || tr("Not known")}. {tr("Last big news {}, next milestone {}.", fmtDate(i.last_material_event_at), fmtDate(i.next_milestone_at))}</p>
        {i.research_gaps.length > 0 && <><span className="small"><b>{tr("Still to find out")}</b></span><ul className="tight small">{i.research_gaps.map((g: string, k: number) => <li key={k}>{g}</li>)}</ul></>}</div>)}</>}
      <div className="row gap-2 mb-2">
        <label>{tr("Status") + " "}<select value={signalStatus} onChange={(e) => setSignalStatus(e.target.value)}><option value="all">{tr("All signals")}</option><option value="active">{tr("Current")}</option><option value="historical">{tr("Historical")}</option></select></label>
        <label>{tr("Order") + " "}<select value={signalOrder} onChange={(e) => setSignalOrder(e.target.value)}><option value="newest">{tr("Newest first")}</option><option value="oldest">{tr("Oldest first")}</option></select></label>
        <label>{tr("Evidence of demand") + " "}<select value={signalTier} onChange={(e) => setSignalTier(e.target.value)}><option value="all">{tr("All levels")}</option><option value="D1">{tr("Explicit")}</option><option value="D2">{tr("Inferred")}</option><option value="D3">{tr("Programme")}</option><option value="D4">{tr("Context")}</option></select></label>
      </div>
      {stories.map((group) => {
        const first = group[0];
        const rules = new Map(group.map((x: any) => [`${x.rule_id}:${x.status}:${x.demand_tier}`, x]));
        const evidence = new Map(group.flatMap((x: any) => x.evidence).map((e: any) => [e.claim_id, e]));
        return <div className="card" key={first.story_key ?? first.event_id}>
          {[...new Set(group.map((x: any) => x.event_id))].map((eventId: any) => <span id={`event-${eventId}`} key={eventId} />)}
          <b>{first.summary}</b><p className="small muted">{first.event_at || first.pub_at ? fmtDate(first.event_at ?? first.pub_at) : tr("Date unknown")}</p>
          {[...rules.entries()].map(([key, x]: any) => <p className="small" key={key}><Badge kind={x.status === "active" ? "ok" : ""}>{x.status === "active" ? tr("Current") : tr("Historical")}</Badge> {SHORT[x.rule_id] ? tr(SHORT[x.rule_id]) : sigNames[x.rule_id] ?? x.rule_id}. {tr(DEMAND_TIER[x.demand_tier] ?? humanize(x.demand_tier))}</p>)}
          <Evidence items={[...evidence.values()]} />
        </div>;
      })}
      {stories.length === 0 && <p className="muted">{d.signals.length ? tr("No signals match these filters.") : tr("No verified signals yet. Press “Check for news” to read the company's own pages and search the web.")}</p>}
    </>}
    {sub === "sources" && <>
      <div className="card table-wrap" tabIndex={0} role="region" aria-label={tr("Coverage table")}><p className="small mt-0">{tr(d.source_coverage_note)}</p><table><thead><tr><th>{tr("Kind of source")}</th><th>{tr("State")}</th><th>{tr("Last successful check")}</th></tr></thead><tbody>{d.coverage.map((c: any, i: number) => <tr key={i}><td>{humanize(c.source_family)}</td><td>{humanize(c.coverage_state)}</td><td>{fmtDate(c.last_success_at)}</td></tr>)}
        {d.coverage.length === 0 && <tr><td colSpan={3} className="muted">{tr("We have not checked this account's sources yet. That means we cannot see, not that nothing is happening.")}</td></tr>}</tbody></table></div>
      <div className="card table-wrap" tabIndex={0} role="region" aria-label={tr("Where this company publishes")}><h3 className="mt-0">{tr("Where this company publishes")}</h3>
        {eps.length === 0 ? <p className="small muted">{tr("Nothing found yet. Press “Check for news” to look for its news feed, press page, careers page and job board.")}</p>
          : <table><thead><tr><th>{tr("Kind")}</th><th>{tr("Address")}</th><th>{tr("Last check")}</th><th>{tr("Items read")}</th></tr></thead><tbody>{eps.map((e: any, i: number) => <tr key={i}><td>{humanize(e.kind)}{e.provider ? ` (${e.provider})` : ""}</td><td className="small break-all"><a href={safeHref(e.url)} target="_blank" rel="noreferrer noopener">{e.url} ↗</a></td><td className="small">{e.last_scanned_at ? <>{fmtDateTime(e.last_scanned_at)} <Badge kind={e.last_status === "ok" ? "ok" : "bad"}>{e.last_status === "ok" ? tr("Worked") : tr("Problem")}</Badge>{e.last_detail && <div className="muted">{e.last_detail}</div>}</> : tr("Not yet")}</td><td>{e.docs_seen}</td></tr>)}</tbody></table>}</div>
      {d.source_rows?.length > 0 && <div className="card"><h3 className="mt-0">{tr("Where it appears in your workbooks")}</h3><ul className="tight small mb-0">{d.source_rows.map((r: any, i: number) => <li key={i}>{tr("{}, row {}", r.sheet_title, r.row_number)}</li>)}</ul></div>}
      {d.opportunities.length > 0 && <div className="card"><h3 className="mt-0">{tr("Sources for its opportunities")}</h3>{d.opportunities.slice(0, 3).map((o: any) => <Evidence key={o.hypothesis_id} items={o.evidence} />)}</div>}
    </>}
  </>;
}

/* ------------------------------------------------------------------ the page */
const TABS = ["overview", "people", "activity", "outreach"] as const;
type Tab = typeof TABS[number];

export function Account({ me }: { me: { permissions: string[] } }) {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const [editing, setEditing] = useState(params.get("profile") === "1");
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const linked: [Tab, string] | null = params.has("finding") || params.has("timeline") ? ["activity", "timeline"] : params.has("event") ? ["activity", "signals"] : null;
  const asked = params.get("tab") as Tab | null;
  const [tab, setTab] = useState<Tab>(linked?.[0] ?? (asked && TABS.includes(asked) ? asked : "overview"));
  const [sub, setSub] = useState<string>(linked?.[1] ?? "timeline");
  useEffect(() => { if (linked) { setTab(linked[0]); setSub(linked[1]); } }, [params]);
  const [sigNames, setSigNames] = useState<Record<string, string>>({});
  useEffect(() => { api("/api/library/signals").then((r) => setSigNames(Object.fromEntries(r.items.map((x: any) => [x.rule_id, x.signal])))).catch(() => undefined); }, []);
  const [eps, setEps] = useState<any[]>([]);
  const [context, setContext] = useState<any[]>([]);
  const [scan, setScan] = useState<{ busy: boolean; msg: string; err: unknown }>({ busy: false, msg: "", err: null });
  useEffect(() => {
    const request = new AbortController();
    setD(null); setErr(null); setEps([]);
    api(`/api/accounts/${id}?target=true`, { signal: request.signal }).then((data) => { if (!request.signal.aborted) setD(data); }).catch((error) => { if (!request.signal.aborted) setErr(error); });
    api(`/api/accounts/${id}/endpoints`, { signal: request.signal }).then((data) => { if (!request.signal.aborted) setEps(data.items); }).catch(() => undefined);
    api(`/api/accounts/${id}/context`, { signal: request.signal }).then((data) => { if (!request.signal.aborted) setContext(data.items); }).catch(() => undefined);
    return () => request.abort();
  }, [id]);
  // every hook runs on every render, before any early return (React rule of hooks)
  const [research, setResearch] = useState<any>(null);
  const [researchBusy, setResearchBusy] = useState(false);
  const [researchErr, setResearchErr] = useState<unknown>(null);
  const loadResearch = () => api(`/api/accounts/${id}/research`).then(setResearch).catch(() => undefined);
  useEffect(() => { loadResearch(); }, [id]);
  usePoll(Boolean(research?.running), loadResearch, 5000);
  const [rel, setRel] = useState<any>(null);
  const loadRel = () => api(`/api/accounts/${id}/relationship`).then(setRel).catch(() => undefined);
  useEffect(() => { loadRel(); }, [id]);
  const [camps, setCamps] = useState<any>({ items: [] });
  const loadCamps = () => api(`/api/accounts/${id}/campaigns`).then(setCamps).catch(() => setCamps({ items: [] }));
  useEffect(() => { loadCamps(); }, [id]);
  usePoll(camps.items.some((b: any) => b.status === "queued" || b.status === "running"), loadCamps, 6000);
  useEffect(() => {
    const event = params.get("event");
    if (d && tab === "activity" && sub === "signals" && event) document.getElementById(`event-${event}`)?.scrollIntoView({ block: "center" });
  }, [d, tab, sub, params]);
  if (!d || d.account.id !== id) return <>
    <p className="small mt-0"><Link to="/accounts">{tr("Accounts")}</Link></p><h1 className="title-sm">{tr("Account details")}</h1>
    {err ? <Err e={err} onRetry={reloadPage} /> : <Skeleton rows={4} label={tr("Loading the account")} />}
  </>;
  const a = d.account;
  const lp: Record<string, string> = d.list_profile ?? {};
  const canScan = me.permissions.includes("research.request");
  const c = research?.profile?.content;
  const startResearch = async () => {
    setResearchErr(null); setResearchBusy(true);
    try { await post(`/api/accounts/${id}/research`); await loadResearch(); } catch (e) { setResearchErr(e); } finally { setResearchBusy(false); }
  };
  const makeCampaign = async (role: string) => { try { await post(`/api/accounts/${id}/campaign`, { role: role || null }); await loadCamps(); } catch (e) { setScan({ busy: false, msg: "", err: e }); } };
  const decideCampaign = async (did: string, decision: string) => { try { await post(`/api/campaigns/${did}/decision`, { decision }); await loadCamps(); } catch (e) { setScan({ busy: false, msg: "", err: e }); } };
  const runScan = async () => {
    setScan({ busy: true, msg: "", err: null });
    try { const r = await post(`/api/accounts/${id}/scan`); setScan({ busy: false, msg: r.website ? tr("Reading {} and searching the web. Verified findings appear under Activity.", r.website) : tr("Started. This account has no website on record, so we will only run the web search."), err: null }); }
    catch (e) { setScan({ busy: false, msg: "", err: e }); }
  };
  const goto = (t: Tab, s?: string) => {
    setTab(t); if (s) setSub(s);
    if (linked || params.has("tab")) { const q = new URLSearchParams(params); ["finding", "timeline", "event", "tab"].forEach((k) => q.delete(k)); setParams(q, { replace: true }); }
  };
  const companyLinkedIn = [...context.filter((x) => x.kind === "link").map((x) => x.value), ...((research?.profile?.sources ?? []).map((s: any) => s.url))]
    .find((u: string) => /linkedin\.com\/company\//i.test(u));
  const hq = c?.footprint?.hq?.value ?? countryName(a.country_code);
  const peopleCount = d.buyers?.length ?? 0;
  const relLabel = rel?.recorded?.status ? tr(REL_LABEL[rel.recorded.status]) : null;
  return (
    <>
      <header className="ac-mast">
        <p className="ac-crumb"><Link to="/accounts">{tr("Accounts & leads")}</Link></p>
        <div className="ac-id">
          <span className="ac-mono" aria-hidden="true">{initials(a.display_name)}</span>
          <div className="ac-name">
            <h1>{a.display_name}</h1>
            <div className="ac-links">
              {a.canonical_domain && <a href={`https://${a.canonical_domain}`} target="_blank" rel="noreferrer noopener">{a.canonical_domain} ↗</a>}
              {companyLinkedIn ? <a href={safeHref(companyLinkedIn)} target="_blank" rel="noreferrer noopener">LinkedIn ↗</a>
                : <a href={`https://www.linkedin.com/search/results/companies/?keywords=${encodeURIComponent(a.display_name)}`} target="_blank" rel="noreferrer noopener">{tr("Find on LinkedIn")} ↗</a>}
              {a.in_master_list && <span className="muted-on-navy">{tr("On your lead list")}</span>}
            </div>
          </div>
          <div className="ac-actions">
            {canScan && research && <button type="button" className="primary" disabled={researchBusy || research.running || !research.setup?.ready}
              title={research.setup?.ready ? undefined : tr("Research with Exa is not set up in this environment.")} onClick={startResearch}>{research.running ? tr("Researching…") : c ? tr("Research again") : tr("Research with Exa")}</button>}
            {canScan && <button type="button" disabled={scan.busy} onClick={runScan}>{tr("Check for news")}</button>}
            {canScan && a.entity_type !== "public_body" && <button type="button" aria-expanded={editing} onClick={() => setEditing(!editing)}>{editing ? tr("Close details") : tr("Edit details")}</button>}
          </div>
        </div>
      </header>
      <dl className="ac-facts">
        <div><dt>{tr("Headquarters")}</dt><dd>{hq || tr("Not known")}</dd></div>
        <div><dt>{tr("Size")}</dt><dd>{c?.footprint?.size?.value ?? staffCount(a.employees_estimate, a.employees_uncertain)}</dd></div>
        <div><dt>{tr("Industry")}</dt><dd>{a.segment || lp["Industry"] || tr("Not known")}{a.company_ownership && a.company_ownership !== "unknown" && <div className="small">{tr(OWNERSHIP[a.company_ownership])}</div>}</dd></div>
        <div><dt>{tr("Lead list")}</dt><dd>{a.list_priority || a.list_score != null ? <>{a.list_priority && <span className={`ac-tag ${String(a.list_priority).toLowerCase()}`}>{a.list_priority}</span>} {a.list_score != null && tr("score {}", a.list_score)}<div className="small">{a.list_eligibility}</div></> : tr("Not on the list")}</dd></div>
        <div><dt>{tr("Owner")}</dt><dd><OwnerControl rel={rel} orgId={a.id} onChange={(owner) => setRel({ ...rel, owner })} /></dd></div>
        <div><dt>{tr("Relationship")}</dt><dd>{relLabel ?? <span className="small">{tr("Not recorded")}{rel && <>{", " + tr("looks like {}", tr(REL_LABEL[rel.suggested]).toLowerCase())}</>}</span>}</dd></div>
      </dl>
      {scan.msg && <div className="okmsg mt-2 mb-2" role="status">{scan.msg}</div>}<Err e={scan.err} />
      {editing && canScan && a.entity_type !== "public_body" && <CompanyProfile account={a} onSave={(profile) => setD({ ...d, account: { ...a, ...profile } })} />}
      <Tabs items={[{ key: "overview", label: tr("Overview") }, { key: "people", label: <>{tr("People")}{me.permissions.includes("contacts.read") && <span className="tabn">{number(peopleCount)}</span>}</> },
        { key: "activity", label: tr("Activity") }, { key: "outreach", label: tr("Outreach") }]} value={tab} onChange={(k) => goto(k as Tab)} label={tr("Account sections")}>
        {tab === "overview" && <Overview d={d} research={research} rel={rel} orgId={a.id} me={me} canRun={canScan} startResearch={startResearch} researchBusy={researchBusy}
          researchErr={researchErr} reloadRel={loadRel} goto={goto} />}
        {tab === "people" && <PeopleTab orgId={a.id} me={me} />}
        {tab === "activity" && <Activity d={d} eps={eps} sub={sub} setSub={setSub} params={params} sigNames={sigNames} />}
        {tab === "outreach" && <CampaignTab camps={camps} canDraft={me.permissions.includes("draft.create")} orgId={a.id} make={makeCampaign} decide={decideCampaign} />}
      </Tabs>
    </>
  );
}
