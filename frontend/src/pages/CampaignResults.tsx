import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { number, tr } from "../locale";
import { Badge, Empty, Err, Skeleton } from "../components/ui";

/* Every Lemlist campaign side by side, whoever made it (backend: ri/campaign_results.py, GET /api/campaign-results).
   People, not events: a lead is someone sent at least one email; a reply is a person who replied. Opens are left out: privacy proxies
   open emails by themselves. The split by sender is shown to managers only. */

const rate = (v: number | null | undefined) => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);
const BOUNCE: Record<string, string> = { warn: "Bounces over 2%", bad: "Bounces over 5%: check the list" };

function Bounce({ r }: { r: any }) {
  return <>{r.bounced} <span className="muted">({rate(r.bounce_rate)})</span>{r.bounce && r.bounce !== "ok" && <> <Badge kind={r.bounce === "bad" ? "bad" : "warn"}>{BOUNCE[r.bounce]}</Badge></>}</>;
}

function Row({ r, first }: { r: any; first: React.ReactNode }) {
  return <tr><td>{first}</td><td className="num">{number(r.leads)}</td><td className="num">{number(r.sends)}</td>
    <td className="num">{r.replied} <span className="muted">({rate(r.reply_rate)})</span></td><td className="num">{r.interested}</td>
    <td><Bounce r={r} /></td><td className="num">{r.unsubscribed}</td></tr>;
}

const HEAD = ["Leads", "Emails sent", "Replied", "Interested", "Bounced", "Opted out"];

export function CampaignResults() {
  const [days, setDays] = useState(90);
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { setErr(null); api(`/api/campaign-results?days=${days}`).then(setD).catch(setErr); }, [days]);
  if (err && !d) return <Err e={err} />;
  if (!d) return <Skeleton rows={4} />;
  const t = d.total;
  return <>
    <div className="row spread end cr-head">
      <p className="small muted m-0">{tr("Every Lemlist campaign, made here or in Lemlist. People are counted once: a lead is someone sent an email, a reply is a person who replied. Opens are left out because privacy tools open emails by themselves.")}</p>
      <label className="small">{tr("Period")} <select id="cr-days" value={days} onChange={(e) => setDays(Number(e.target.value))}>
        {[30, 90, 180, 365].map((n) => <option key={n} value={n}>{tr("Last {} days", n)}</option>)}</select></label>
    </div>
    {d.campaigns.length === 0 ? <Empty title="No Lemlist activity yet">{tr("Results appear here within the hour after the first emails go out.")}</Empty> : <>
      <div className="cr-tiles">
        <div className="fc-tile"><div className="fc-label">{tr("Leads")}</div><div className="fc-val">{number(t.leads)}</div><div className="fc-note">{tr("in {} campaigns", d.campaigns.length)}</div></div>
        <div className="fc-tile"><div className="fc-label">{tr("Replied")}</div><div className="fc-val">{rate(t.reply_rate)}</div><div className="fc-note">{tr("{} people", t.replied)}</div></div>
        <div className="fc-tile"><div className="fc-label">{tr("Bounced")}</div><div className="fc-val">{rate(t.bounce_rate)}</div><div className="fc-note">{t.bounce === "ok" ? tr("under 2%: fine") : tr("over 2% hurts delivery for every campaign on the same mailboxes")}</div></div>
        <div className="fc-tile"><div className="fc-label">{tr("Deals that followed")}</div><div className="fc-val">{d.deals_total}</div><div className="fc-note">{tr("created in Pipedrive at a contacted company after its first email")}</div></div>
      </div>
      <h3 className="fc-h3">{tr("By campaign")}</h3>
      <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Results by campaign")}>
        <table className="fc-table cr-table"><thead><tr><th>{tr("Campaign")}</th>{HEAD.map((h) => <th key={h}>{tr(h)}</th>)}<th>{tr("Deals that followed")}</th></tr></thead>
          <tbody>{d.campaigns.map((c: any) => <tr key={c.campaign_id ?? c.name}>
            <td><b>{c.name}</b>{c.draft && <> <Badge>{"Draft in Lemlist"}</Badge></>}
              <br /><small className="muted">{c.first_sent ? tr("since {}", fmtDate(c.first_sent)) : ""}{c.signal_campaign && <> · <Link to={`/campaigns/${c.signal_campaign.id}`}>{tr("from a signal campaign")}</Link></>}</small></td>
            <td className="num">{number(c.leads)}</td><td className="num">{number(c.sends)}</td>
            <td className="num">{c.replied} <span className="muted">({rate(c.reply_rate)})</span></td><td className="num">{c.interested}</td>
            <td><Bounce r={c} /></td><td className="num">{c.unsubscribed}</td>
            <td className="num">{c.deals}{c.won > 0 && <> <span className="muted">({tr("{} won", c.won)})</span></>}<br /><small className="muted">{tr("{} companies", c.companies)}</small></td>
          </tr>)}</tbody>
          <tfoot><Row r={t} first={tr("All campaigns")} /></tfoot>
        </table>
      </div>
      {d.steps.length > 0 && <>
        <h3 className="fc-h3">{tr("By email in the sequence")}</h3>
        {d.steps.every((s: any) => s.step == null || s.step === 1) && <p className="small muted">{tr("Only the first email has gone out so far; follow-ups show here as they are sent.")}</p>}
        <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Results by email in the sequence")}>
          <table className="fc-table cr-table"><thead><tr><th>{tr("Email")}</th>{HEAD.map((h) => <th key={h}>{tr(h)}</th>)}</tr></thead>
            <tbody>{d.steps.map((s: any) => <Row key={String(s.step)} r={s} first={s.step == null ? <span className="muted">{tr("Step not recorded")}</span> : tr("Email {}", s.step)} />)}</tbody></table>
        </div>
      </>}
      {d.senders && <>
        <h3 className="fc-h3">{tr("By sender")}</h3>
        <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Results by sender")}>
          <table className="fc-table cr-table"><thead><tr><th>{tr("Sender")}</th>{HEAD.map((h) => <th key={h}>{tr(h)}</th>)}</tr></thead>
            <tbody>{d.senders.map((s: any) => <Row key={s.email ?? "none"} r={s} first={s.email ? (s.name || s.email) : <span className="muted">{tr("Sender not recorded")}</span>} />)}</tbody></table>
        </div>
        <p className="small muted">{tr("Shown to managers only. Lemlist does not name the sender on every event, so some activity has no sender.")}</p>
      </>}
    </>}
    <p className="small muted">{tr("As of {}. Lemlist activity is read every hour.", fmtDate(d.as_of))}</p>
  </>;
}
