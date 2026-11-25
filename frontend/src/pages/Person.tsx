import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post } from "../api";
import { tr } from "../locale";
import { Err, Skeleton, reloadPage, safeHref } from "../components/ui";
import { SHORT } from "../components/Signals";
import { Avatar, COMMITTEE, COMMITTEE_ICON, Cite, DateTile, EmailMark, Ic, Monogram, ResearchPanel, ResearchState, Sec, initials, usePoll } from "./AccountDetail";
import { countryName, staffCount } from "./targetMarket";
import "../styles/account.css";
import "../styles/person.css";

const KIND: Record<string, [string, typeof Ic.mail]> = { talk: ["Talk", Ic.mic], post: ["Post", Ic.chat], article: ["Article", Ic.doc], interview: ["Interview", Ic.chat],
  podcast: ["Podcast", Ic.headphones], news: ["News", Ic.news], appointment: ["Appointment", Ic.badge], award: ["Award", Ic.star], other: ["Other", Ic.spark] };
const ACTIVITY: Record<string, string> = { emailsSent: "Email sent", emailsOpened: "Opened an email", emailsClicked: "Clicked a link", emailsReplied: "Replied", emailsBounced: "Email bounced",
  emailsUnsubscribed: "Unsubscribed", emailsInterested: "Marked interested", emailsNotInterested: "Marked not interested", interested: "Marked interested", notInterested: "Marked not interested" };
const REL: Record<string, string> = { none: "No relationship", prospect: "Prospect", in_talks: "In talks", past_client: "Past client", client: "Client" };

type Role = { title: string; company?: string | null; from?: number | null; to?: number | null; current?: boolean; cite?: any };
const yearOf = (v: any): number | null => { const m = /\b(19|20)\d{2}\b/.exec(String(v ?? "")); return m ? Number(m[0]) : null; };

/** The career as bars on a shared year axis (a real sequence, so a timeline). Roles without years are listed underneath. */
function CareerPath({ roles }: { roles: Role[] }) {
  const now = new Date().getFullYear();
  const dated = roles.filter((r) => r.from);
  const undated = roles.filter((r) => !r.from);
  const min = dated.length ? Math.min(...dated.map((r) => r.from!)) : now;
  const max = now + 1, span = Math.max(1, max - min);
  const step = span > 16 ? 5 : span > 8 ? 2 : 1;
  const ticks: number[] = [];
  for (let y = Math.ceil(min / step) * step; y <= now; y += step) ticks.push(y);
  const pos = (y: number) => `${((y - min) / span) * 100}%`;
  const years = (r: Role) => (r.from ? `${r.from}–${r.current ? tr("now") : r.to ?? "?"}` : "");
  return <div className="career">
    {dated.length > 0 && <div className="cp" role="list" aria-label={tr("Career path")}>
      {dated.map((r, i) => { const end = r.current ? max : (r.to ?? r.from! + 1); return <div key={i} className={`cp-row${r.current ? " now" : ""}`} role="listitem">
        <div className="cp-label"><Monogram name={r.company || r.title} size="sm" /><div><b>{r.title}</b><span>{[r.company, years(r)].filter(Boolean).join(", ")}</span>{r.cite && <Cite c={r.cite} />}</div></div>
        <div className="cp-track" aria-hidden="true"><span className={`cp-bar${r.to || r.current ? "" : " open"}`} style={{ left: pos(r.from!), width: `max(6px, calc(${pos(Math.max(end, r.from! + 0.5))} - ${pos(r.from!)}))` }} title={`${r.title}${r.company ? `, ${r.company}` : ""}: ${years(r)}`} /></div>
      </div>; })}
      <div className="cp-axis" aria-hidden="true"><div className="cp-ticks">{ticks.map((y) => <span key={y} style={{ left: pos(y) }}>{y}</span>)}</div></div>
    </div>}
    {undated.length > 0 && <ul className="cp-undated">{undated.map((r, i) => <li key={i}><Monogram name={r.company || r.title} size="xs" /><span><b>{r.title}</b>{r.company ? `, ${r.company}` : ""}{r.current ? ` (${tr("now")})` : ""}</span>{r.cite && <Cite c={r.cite} />}</li>)}</ul>}
  </div>;
}

/** One person, everything we hold about them: the Exa profile, what the lead list says, outreach, and their company and colleagues. */
export default function Person() {
  const { id } = useParams();
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [rErr, setRErr] = useState<unknown>(null);
  const load = () => api(`/api/people/${id}/profile`).then(setD).catch(setErr);
  useEffect(() => { setD(null); setErr(null); load(); }, [id]);
  usePoll(Boolean(d?.research?.running), load, 5000);
  if (!d) return <>
    <p className="small mt-0"><Link to="/people">{tr("People")}</Link></p><h1 className="title-sm">{tr("Person")}</h1>
    {err ? <Err e={err} onRetry={reloadPage} /> : <Skeleton rows={4} label={tr("Loading the person")} />}
  </>;
  const p = d.person;
  const job = d.employment[0];
  const co = job?.company;
  const others = d.employment.slice(1);
  const ll = d.lead_list ?? {};
  const r = d.research;
  const c = r?.profile?.content;
  const g = COMMITTEE[p.committee] ? p.committee : "influencer";
  const GIcon = COMMITTEE_ICON[g];
  const start = async () => { setRErr(null); setBusy(true); try { await post(`/api/people/${id}/research`); await load(); } catch (e) { setRErr(e); } finally { setBusy(false); } };
  const out = d.outreach;
  const touches = [...out.enrolments.map((e: any) => ({ when: e.last_event_at ?? e.created_at, what: tr("{}: {}", e.campaign, tr(e.status === "pushed" ? "added to the campaign" : e.status.replace(/_/g, " "))) })),
    ...out.activity.map((a: any) => ({ when: a.happened_at, what: `${tr(ACTIVITY[a.type] ?? a.type)}${a.campaign_name ? ` (${a.campaign_name})` : ""}` }))]
    .sort((x, y) => String(y.when ?? "").localeCompare(String(x.when ?? "")));
  const title = c?.current_role?.title ?? job?.title;
  const roles: Role[] = c ? [
    ...(c.current_role ? [{ title: c.current_role.title, company: co?.name, from: c.current_role.since_year ?? yearOf(c.current_role.since), current: true, cite: c.current_role }] : []),
    ...(c.previous_roles ?? []).map((x: any) => ({ title: x.title, company: x.company, from: x.from ?? yearOf(x.period), to: x.to ?? yearOf(String(x.period ?? "").split(/[-–]/)[1]), cite: x })),
  ] : [];
  const openers = c?.talking_points ?? [];
  const colleagues = d.colleagues ?? [];
  const company = d.company;
  return <>
    <header className="ac-hero ps-hero" data-mark={initials(p.name)}>
      <nav className="ac-crumb" aria-label={tr("Breadcrumb")}>
        {co ? <><Link to="/accounts">{tr("Accounts & leads")}</Link><span aria-hidden="true">/</span><Link to={`/accounts/${co.id}`}>{co.name}</Link><span aria-hidden="true">/</span>
          <Link to={`/accounts/${co.id}?tab=people`}>{tr("People")}</Link></> : <Link to="/people">{tr("People")}</Link>}
        <span aria-hidden="true">/</span><span aria-current="page">{p.name}</span>
      </nav>
      <div className="ac-id">
        <span className="ps-avatar"><Avatar name={p.name} committee={g} size="lg" /></span>
        <div className="ac-name">
          <h1>{p.name}</h1>
          <p className="ps-role">{title ?? tr("Role not recorded")}{co && <> {tr("at")} <Link className="ps-co" to={`/accounts/${co.id}`}><Monogram name={co.name} size="xs" />{co.name}</Link></>}</p>
          {c?.summary?.text && <p className="ps-summary">{c.summary.text}</p>}
          <div className="ac-links">
            <span className="grp-badge"><GIcon />{tr(COMMITTEE[g].one)}</span>
            <EmailMark has={Boolean(p.has_email)} onNavy />
            {p.linkedin_url && safeHref(p.linkedin_url) ? <a href={safeHref(p.linkedin_url)} target="_blank" rel="noreferrer noopener"><Ic.linkedin />{tr("LinkedIn profile")}</a>
              : <a href={`https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(`${p.name} ${co?.name ?? ""}`)}`} target="_blank" rel="noreferrer noopener"><Ic.linkedin />{tr("Find on LinkedIn")}</a>}
          </div>
          {p.suppressed && <p className="ps-warn">{tr("Do not contact.")} {p.suppression_reason ?? ""}</p>}
        </div>
        {d.can_research && <div className="ac-actions">
          <button type="button" className="primary" disabled={busy || r.running || !r.setup?.ready} title={r.setup?.ready ? undefined : tr("Research with Exa is not set up in this environment.")} onClick={start}>
            {r.running ? tr("Researching…") : c ? tr("Research again") : tr("Research with Exa")}</button>
        </div>}
      </div>
      <dl className="ps-facts">
        <div><dt>{tr("Seniority")}</dt><dd>{ll.seniority ? tr(ll.seniority) : tr("Not known")}</dd></div>
        <div><dt>{tr("Buying committee")}</dt><dd>{ll.committee_role ? tr(ll.committee_role) : tr(COMMITTEE[g].one)}</dd></div>
        <div><dt>{tr("Lead list priority")}</dt><dd>{ll.priority ? <span className={`ac-tag ${ll.priority.toLowerCase()}`}>{ll.priority}</span> : tr("Not on the list")}</dd></div>
        <div><dt>{tr("Outreach")}</dt><dd>{tr(out.engagement_label)}</dd></div>
      </dl>
    </header>
    <div className="ac-grid mt-4">
      <div className="ps-main">
        <ResearchState r={r} kind="person" name={p.name} canRun={d.can_research} start={start} busy={busy} err={rErr} />
        {c?.identity && !c.identity.confident && !c.empty && <p className="ps-check"><Ic.alert />{tr("Check first: the pages may be about someone else with the same name.")} {c.identity.note ?? ""}</p>}
        {(openers.length > 0 || ll.angle) && <section className="opener" aria-labelledby="ps-open">
          <h2 id="ps-open"><Ic.quote />{tr("How to open the conversation")}</h2>
          <ul>{openers.map((t: any, i: number) => <li key={i}><b>{t.point}</b>{t.why && <span>{t.why}</span>}<div>{t.sources.map((s: any, k: number) => <Cite key={k} c={s} />)}</div></li>)}
            {ll.angle && <li className="ll"><b>{ll.angle}</b><span>{tr("The angle on your lead list.")}</span></li>}</ul>
          {openers.length > 0 && <p className="opener-note">{tr("Suggested from public pages. Check them before you use them.")}</p>}
        </section>}
        <div className="ac-sheet">
          <Sec title="Role & remit" id="ps-role">
            {c?.current_role ? <>
              <p className="ac-lead">{c.current_role.title}{c.current_role.since ? <span className="small"> {tr("since {}", c.current_role.since)}</span> : null}</p>
              {c.current_role.remit && <p>{c.current_role.remit} <Cite c={c.current_role} /></p>}
            </> : <p className="m-0">{job ? tr("On record: {} at {}.", job.title ?? tr("no title"), co.name) : tr("Not linked to a company.")}{c ? " " + tr("The pages did not describe the current role.") : ""}</p>}
          </Sec>
          {roles.length > 0 && <Sec title="Career path" hint="From the pages Exa read" id="ps-career"><CareerPath roles={roles} /></Sec>}
          {c?.focus_areas?.length > 0 && <Sec title="Focus areas" id="ps-focus"><ul className="ac-tags">{c.focus_areas.map((f: any, i: number) => <li key={i} className="ac-tag focus" title={f.source_title ?? undefined}>{f.label}</li>)}</ul></Sec>}
          {c && <Sec title="Recent public activity" hint="Talks, articles, posts and news" id="ps-act">
            {c.recent_activity?.length ? <ul className="act-grid">{c.recent_activity.map((x: any, i: number) => { const [label, KIcon] = KIND[x.kind] ?? KIND.other; return <li key={i} className="act-card">
              <DateTile d={x.date} />
              <div><span className="act-kind"><KIcon />{tr(label)}</span><b>{x.title}</b>{x.detail && <p>{x.detail}</p>}<Cite c={x} /></div></li>; })}</ul>
              : <p className="small m-0">{tr("Exa found no public talks, articles or news about this person in the last two years.")}</p>}
          </Sec>}
          {c?.open_questions?.length > 0 && <Sec title="Still to find out" id="ps-q"><ul className="tight">{c.open_questions.map((q: string, i: number) => <li key={i}>{q}</li>)}</ul></Sec>}
          {c?.empty && <section className="ac-sec"><h2>{tr("Research")}</h2><div><p className="m-0">{tr(c.empty)}</p></div></section>}
        </div>
      </div>
      <aside className="ac-rail" aria-label={tr("Company, lead list and outreach")}>
        {co && <section className="ac-panel co-card" aria-label={co.name}>
          <Link className="co-head" to={`/accounts/${co.id}`}><Monogram name={co.name} size="md" /><span><b>{co.name}</b><small>{[co.segment, countryName(co.country)].filter(Boolean).join(", ")}</small></span></Link>
          {company?.about && <p className="small">{company.about}</p>}
          <dl className="ac-kv">
            {company?.employees != null && <><dt>{tr("Size")}</dt><dd>{staffCount(company.employees, company.employees_uncertain)}</dd></>}
            <dt>{tr("Relationship")}</dt><dd>{company?.relationship ? tr(REL[company.relationship]) : tr("Not recorded")}</dd>
            {company?.list_priority && <><dt>{tr("Lead list")}</dt><dd><span className={`ac-tag ${String(company.list_priority).toLowerCase()}`}>{company.list_priority}</span></dd></>}
          </dl>
          {company?.signal && <div className="co-sig"><Ic.target /><div><b>{company.signal.rule_id && SHORT[company.signal.rule_id] ? tr(SHORT[company.signal.rule_id]) : company.signal.label}</b>
            <span>{company.signal.summary}</span>{company.signal.date && <span className="muted">{fmtDate(company.signal.date)}</span>}</div></div>}
          <p className="co-links"><Link to={`/accounts/${co.id}`}>{tr("Company page")}</Link><Link to={`/accounts/${co.id}?tab=people`}>{tr("People at {}", co.name)}</Link></p>
          {others.length > 0 && <><p className="small mt-2 mb-1"><b>{tr("Other roles we hold")}</b></p>
            <ul className="tight small mb-0">{others.map((j: any, i: number) => <li key={i}>{j.title ?? tr("Role not recorded")}, <Link to={`/accounts/${j.company.id}`}>{j.company.name}</Link>{j.current ? "" : ` (${tr("past")})`}</li>)}</ul></>}
        </section>}
        <section className="ac-panel" aria-labelledby="ps-ll"><h2 id="ps-ll">{tr("What your lead list says")}</h2>
          {Object.keys(ll).length === 0 ? <p className="small m-0">{tr("This person is not on your lead list.")}</p> : <>
            <dl className="ac-kv">
              {ll.committee_role && <><dt>{tr("Buying committee")}</dt><dd>{tr(ll.committee_role)}</dd></>}
              {ll.persona && <><dt>{tr("Persona")}</dt><dd>{tr(ll.persona)}</dd></>}
              {ll.seniority && <><dt>{tr("Seniority")}</dt><dd>{tr(ll.seniority)}</dd></>}
              {ll.priority && <><dt>{tr("Priority")}</dt><dd>{ll.priority}</dd></>}
              {ll.eligibility && <><dt>{tr("Eligibility")}</dt><dd>{ll.eligibility}</dd></>}
            </dl>
            {ll.signal && <p className="small mt-2 mb-0"><b>{tr("Signal it relies on")}:</b> {ll.signal}</p>}
          </>}
        </section>
        <section className="ac-panel" aria-labelledby="ps-out"><h2 id="ps-out">{tr("Outreach")}</h2>
          {touches.length === 0 ? <p className="small m-0">{tr("Never contacted from this platform.")}</p>
            : <ul className="ps-log">{touches.slice(0, 15).map((t, i) => <li key={i}><span>{t.what}</span><span className="when">{t.when ? fmtDate(t.when) : ""}</span></li>)}</ul>}
          {out.campaigns.length > 0 && <><p className="small mt-3 mb-1"><b>{tr("Signal campaigns")}</b></p>
            <ul className="tight small mb-0">{out.campaigns.map((m: any) => <li key={m.id}><Link to={`/campaigns/${m.id}`}>{m.name}</Link>{!m.included && m.exclusion ? `: ${tr("left out")}, ${m.exclusion}` : ""}</li>)}</ul></>}
        </section>
        {co && colleagues.length > 0 && <section className="ac-panel kp" aria-labelledby="ps-co"><h2 id="ps-co">{tr("Other people at {}", co.name)}</h2>
          <ul className="kp-list">{colleagues.slice(0, 6).map((x: any) => <li key={x.id}>
            <Avatar name={x.name} committee={x.committee} size="sm" />
            <div className="kp-who"><Link to={`/people/${x.id}`}>{x.name}</Link><span>{x.title || tr("Role not recorded")}</span></div>
            {x.has_email && <span className="kp-mail" title={tr("Email ✓")}><Ic.mail /><span className="ac-sr">{tr("Email ✓")}</span></span>}
          </li>)}</ul>
          <Link className="small" to={`/accounts/${co.id}?tab=people`}>{tr("Everyone at {}, by buying committee", co.name)}</Link>
        </section>}
        <ResearchPanel r={r} canRun={false} start={start} busy={busy} err={rErr} />
        <p className="ac-note m-0">{tr("Added {}. Every look at this page is logged.", fmtDateTime(p.created_at))}</p>
      </aside>
    </div>
  </>;
}
