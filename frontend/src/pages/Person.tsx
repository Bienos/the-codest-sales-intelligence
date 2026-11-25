import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post } from "../api";
import { tr } from "../locale";
import { Err, Skeleton, reloadPage, safeHref } from "../components/ui";
import { Cite, ResearchPanel, ResearchSetup, Sec, fmtDay, initials, usePoll } from "./AccountDetail";
import "../styles/account.css";
import "../styles/person.css";

const KIND: Record<string, string> = { talk: "Talk", post: "Post", article: "Article", interview: "Interview", podcast: "Podcast", news: "News", appointment: "Appointment", award: "Award", other: "Other" };
const ACTIVITY: Record<string, string> = { emailsSent: "Email sent", emailsOpened: "Opened an email", emailsClicked: "Clicked a link", emailsReplied: "Replied", emailsBounced: "Email bounced",
  emailsUnsubscribed: "Unsubscribed", emailsInterested: "Marked interested", emailsNotInterested: "Marked not interested", interested: "Marked interested", notInterested: "Marked not interested" };
const EMAIL: Record<string, string> = { verified: "Verified email", unverified: "Email not verified", none: "No email" };

/** One person, everything we hold about them: role, research with Exa, what the lead list says, and every outreach touch. */
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
  const others = d.employment.slice(1);
  const ll = d.lead_list ?? {};
  const r = d.research;
  const c = r?.profile?.content;
  const start = async () => { setRErr(null); setBusy(true); try { await post(`/api/people/${id}/research`); await load(); } catch (e) { setRErr(e); } finally { setBusy(false); } };
  const out = d.outreach;
  const touches = [...out.enrolments.map((e: any) => ({ when: e.last_event_at ?? e.created_at, what: tr("{}: {}", e.campaign, tr(e.status === "pushed" ? "added to the campaign" : e.status.replace(/_/g, " "))) })),
    ...out.activity.map((a: any) => ({ when: a.happened_at, what: `${tr(ACTIVITY[a.type] ?? a.type)}${a.campaign_name ? ` (${a.campaign_name})` : ""}` }))]
    .sort((x, y) => String(y.when ?? "").localeCompare(String(x.when ?? "")));
  return <>
    <header className="ac-mast">
      <p className="ac-crumb"><Link to="/people">{tr("People")}</Link>{job && <> / <Link to={`/accounts/${job.company.id}?tab=people`}>{job.company.name}</Link></>}</p>
      <div className="ac-id">
        <span className="ac-mono" aria-hidden="true">{initials(p.name)}</span>
        <div className="ac-name">
          <h1>{p.name}</h1>
          <p className="ps-role">{job ? <>{c?.current_role?.title ?? job.title ?? tr("Role not recorded")} {tr("at")} <Link to={`/accounts/${job.company.id}`}>{job.company.name}</Link></> : tr("Not linked to a company")}</p>
          <div className="ac-links">
            {p.linkedin_url && safeHref(p.linkedin_url) ? <a href={safeHref(p.linkedin_url)} target="_blank" rel="noreferrer noopener">{tr("LinkedIn profile")} ↗</a>
              : <a href={`https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(`${p.name} ${job?.company.name ?? ""}`)}`} target="_blank" rel="noreferrer noopener">{tr("Find on LinkedIn")} ↗</a>}
            {job?.evidence_url && safeHref(job.evidence_url) && job.evidence_url !== p.linkedin_url && <a href={safeHref(job.evidence_url)} target="_blank" rel="noreferrer noopener">{tr("Where the role was found")} ↗</a>}
          </div>
          {p.suppressed && <p className="ps-warn">{tr("Do not contact.")} {p.suppression_reason ?? ""}</p>}
        </div>
        {d.can_research && <div className="ac-actions">
          <button type="button" className="primary" disabled={busy || r.running || !r.setup?.ready} title={r.setup?.ready ? undefined : tr("Research with Exa is not set up in this environment.")} onClick={start}>
            {r.running ? tr("Researching…") : c ? tr("Research again") : tr("Research with Exa")}</button>
        </div>}
      </div>
    </header>
    <dl className="ac-facts">
      <div><dt>{tr("Email")}</dt><dd>{tr(EMAIL[p.email])}<div className="small">{tr("The address itself is never shown.")}</div></dd></div>
      <div><dt>{tr("Role")}</dt><dd>{job?.role_confirmed ? tr("Confirmed") : tr("Not confirmed")}</dd></div>
      <div><dt>{tr("Outreach")}</dt><dd>{tr(out.engagement_label)}</dd></div>
      <div><dt>{tr("Buying committee")}</dt><dd>{ll.committee_role ? tr(ll.committee_role) : tr("Not known")}</dd></div>
      <div><dt>{tr("Seniority")}</dt><dd>{ll.seniority ? tr(ll.seniority) : tr("Not known")}</dd></div>
      <div><dt>{tr("Lead list priority")}</dt><dd>{ll.priority ? <span className={`ac-tag ${ll.priority.toLowerCase()}`}>{ll.priority}</span> : tr("Not on the list")}</dd></div>
    </dl>
    <div className="ac-grid mt-4">
      <div className="ac-sheet">
        {c ? <>
          {c.identity && !c.identity.confident && <section className="ac-sec"><h2>{tr("Check first")}</h2><div><p className="ac-setup m-0">{tr("The pages may be about someone else with the same name.")} {c.identity.note ?? ""}</p></div></section>}
          {c.talking_points?.length > 0 && <Sec title="Open with" hint="Suggested, check before you use them" id="ps-open">
            <ul className="ps-open">{c.talking_points.map((t: any, i: number) => <li key={i}><b>{t.point}</b>{t.why && <span>{t.why}</span>}{t.sources.map((s: any, k: number) => <Cite key={k} c={s} />)}</li>)}</ul>
          </Sec>}
          <Sec title="Role" id="ps-role">
            {c.current_role ? <>
              <p className="ac-lead">{c.current_role.title}{c.current_role.since ? <span className="small"> {tr("since {}", c.current_role.since)}</span> : null}</p>
              {c.current_role.remit && <p>{c.current_role.remit} <Cite c={c.current_role} /></p>}
            </> : <p className="small">{tr("The pages did not describe the current role. On record: {}.", job?.title ?? tr("no title"))}</p>}
            {c.previous_roles?.length > 0 && <><p className="small mt-3 mb-1"><b>{tr("Before that")}</b></p>
              <ul className="ps-roles">{c.previous_roles.map((x: any, i: number) => <li key={i}><span><b>{x.title}</b>{x.company ? `, ${x.company}` : ""} <Cite c={x} /></span><span className="when">{x.period}</span></li>)}</ul></>}
          </Sec>
          <Sec title="Recent public activity" hint="Talks, articles, posts and news" id="ps-act">
            {c.recent_activity?.length ? <ul className="ac-tl">{c.recent_activity.map((x: any, i: number) => <li key={i}><time dateTime={x.date ?? undefined}>{fmtDay(x.date)}</time>
              <div className="ac-tl-body"><b><span className="ps-kind">{tr(KIND[x.kind] ?? "Other")}</span>{x.title}</b>{x.detail && <p>{x.detail}</p>}<Cite c={x} /></div></li>)}</ul>
              : <p className="small">{tr("Exa found no public talks, articles or news about this person in the last two years.")}</p>}
          </Sec>
          {c.focus_areas?.length > 0 && <Sec title="Focus areas" id="ps-focus"><div className="ac-tags">{c.focus_areas.map((f: any, i: number) => <span key={i} className="ac-tag">{f.label}</span>)}</div></Sec>}
          {c.open_questions?.length > 0 && <Sec title="Still to find out" id="ps-q"><ul className="tight">{c.open_questions.map((q: string, i: number) => <li key={i}>{q}</li>)}</ul></Sec>}
          {c.empty && <section className="ac-sec"><h2>{tr("Research")}</h2><div><p>{tr(c.empty)}</p></div></section>}
        </> : <section className="ac-sec"><h2>{tr("Research")}</h2><div>
          {r.running ? <div className="ac-research"><div className="busy" role="status"><span className="ac-pulse" />{tr("Researching with Exa. This page fills in when it is done, in about a minute.")}</div></div> : <>
            <p>{tr("No research yet. Research with Exa reads this person's LinkedIn profile, their public talks, articles and interviews, and news that names them, then writes their role, recent activity and what to open with. Every item links to its page.")}</p>
            <p className="small">{tr("Professional information only. Three searches, a few US cents from the monthly Exa budget.")}</p>
            <ResearchSetup setup={r.setup} />
            {d.can_research && r.setup?.ready && <button type="button" className="primary mt-2" disabled={busy} onClick={start}>{tr("Research with Exa")}</button>}
            <Err e={rErr} /></>}
        </div></section>}
      </div>
      <aside className="ac-rail" aria-label={tr("Lead list, outreach and sources")}>
        <section className="ac-panel accent" aria-labelledby="ps-ll"><h2 id="ps-ll">{tr("What your lead list says")}</h2>
          {Object.keys(ll).length === 0 ? <p className="small m-0">{tr("This person is not on your lead list.")}</p> : <>
            {ll.angle && <p className="ps-angle">{ll.angle}</p>}
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
        <ResearchPanel r={r} canRun={false} start={start} busy={busy} err={rErr} />
        <section className="ac-panel" aria-labelledby="ps-src"><h2 id="ps-src">{tr("Where we know them from")}</h2>
          <ul className="ps-log">{d.sources.map((s: any, i: number) => <li key={i}><span>{tr(s.label)}</span><span className="when">{fmtDate(s.first_seen_at)}</span></li>)}
            {d.sources.length === 0 && <li><span className="small">{tr("No source recorded.")}</span></li>}</ul>
          {others.length > 0 && <><p className="small mt-3 mb-1"><b>{tr("Other roles we hold")}</b></p>
            <ul className="tight small mb-0">{others.map((j: any, i: number) => <li key={i}>{j.title ?? tr("Role not recorded")}, <Link to={`/accounts/${j.company.id}`}>{j.company.name}</Link>{j.current ? "" : ` (${tr("past")})`}</li>)}</ul></>}
          <p className="ac-note mt-2 mb-0">{tr("Added {}. Every look at this page is logged.", fmtDateTime(p.created_at))}</p>
        </section>
      </aside>
    </div>
  </>;
}
