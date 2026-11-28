import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { tr } from "../locale";
import { Badge, Empty, Err, PageHeader, Skeleton } from "../components/ui";
import "../styles/forecast.css";
import "../styles/clients.css";

// Client book (backend: ri/clients.py, GET /api/clients). Every client we deliver for: the work and its monthly value come from the revenue
// forecast, so the two pages agree; contact, hiring and signals are added per client. Managers only: it shows money per client.

const I = (d: JSX.Element) => () => <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">{d}</svg>;
export const IconClients = I(<><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /><path d="M3 13h18" /></>);

const money = (v: number | null | undefined) => {
  if (v == null || !isFinite(v)) return "–";
  const a = Math.abs(v);
  return a >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : a >= 1e3 ? `${Math.round(v / 1e3)}k` : String(Math.round(v));
};
const pct = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v * 100)}%`);

const FLAG: Record<string, { label: string; kind: string }> = {
  ending_soon: { label: "Ends within 90 days", kind: "warn" }, on_hold: { label: "On hold", kind: "warn" }, no_contact: { label: "No contact for 30 days", kind: "warn" },
  no_value: { label: "Deal without a value", kind: "" }, end_assumed: { label: "End date assumed", kind: "" }, not_linked: { label: "Not linked to an account", kind: "" },
};

function Ends({ c }: { c: any }) {
  if (!c.ends_on) return <span className="muted">{tr("Not recorded")}</span>;
  const soon = c.days_left != null && c.days_left <= 90;
  return <span className={soon ? "cl-soon" : undefined}>{fmtDate(c.ends_on)}<br /><small className="muted">{c.days_left < 0 ? tr("{} days ago", -c.days_left) : tr("in {} days", c.days_left)}</small></span>;
}

export default function Clients() {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [open, setOpen] = useState<string>("");
  const [flag, setFlag] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => { setErr(null); api("/api/clients").then(setD).catch(setErr); }, [retry]);
  const head = <PageHeader eyebrow="Delivery" title="Client book" sub="Every client we deliver for: what is running, what it is worth a month, when it ends, who spoke to them last, and what suggests they need more people or may leave." />;
  if (err && !d) return <>{head}<Err e={err} onRetry={() => setRetry((n) => n + 1)} /></>;
  if (!d) return <>{head}<Skeleton rows={6} /></>;
  const list = (d.clients as any[]).filter((c) => !flag || c.flags.includes(flag));
  const noEnd = (d.clients as any[]).filter((c) => c.flags.includes("end_assumed")).length;
  const key = (c: any) => String(c.crm_org_id ?? c.name);
  return (
    <>
      {head}
      {!d.delivery_pipeline?.found && <div className="callout fc-warn"><p className="m-0">{tr("No Delivery Clients pipeline found in Pipedrive, so only won sales deals are listed. The pipeline name is set on Forecast, Assumptions.")}</p></div>}
      <div className="cl-tiles">
        <div className="fc-tile"><div className="fc-label">{tr("Clients")}</div><div className="fc-val">{d.clients.length}</div><div className="fc-note">{tr("with running work or a deal won in 3 years")}</div></div>
        <div className="fc-tile"><div className="fc-label">{tr("Invoiced a month now")}</div><div className="fc-val">{money(d.monthly_total)} <small>{d.currency}</small></div><div className="fc-note">{tr("from running work, as on Forecast")}</div></div>
        <div className="fc-tile"><div className="fc-label">{tr("People placed")}</div><div className="fc-val">{d.fte_total || "–"}</div><div className="fc-note">{tr("from deal products or the client's People at client (FTE) field")}</div></div>
        <div className="fc-tile"><div className="fc-label">{tr("Ending within {} days", d.ending_days)}</div><div className="fc-val">{d.flags.ending_soon}</div><div className="fc-note">{tr("renewal is assumed at {}%", Math.round(d.renewal_rate))}</div></div>
        {d.activities_synced && <div className="fc-tile"><div className="fc-label">{tr("No contact for {} days", d.no_contact_days)}</div><div className="fc-val">{d.flags.no_contact}</div><div className="fc-note">{tr("nothing logged in Pipedrive or Lemlist")}</div></div>}
      </div>
      {noEnd > 0 && <p className="small">{tr("{} of {} clients have no contract end in Pipedrive, so their end date is assumed. Set Contract end and People at client (FTE) on the organisation in Pipedrive; this page and the forecast read them every hour.", noEnd, d.clients.length)}</p>}
      {!d.activities_synced && <p className="small muted">{tr("Last contact and next steps appear after the first hourly read of Pipedrive activities.")}</p>}
      <div className="cl-filter scroll-x" role="group" aria-label={tr("Show clients by flag")}>
        <button type="button" aria-pressed={!flag} onClick={() => setFlag("")}>{tr("All")} <b>{d.clients.length}</b></button>
        {Object.keys(FLAG).filter((k) => d.flags[k] > 0).map((k) => <button key={k} type="button" aria-pressed={flag === k} onClick={() => setFlag(flag === k ? "" : k)}>{tr(FLAG[k].label)} <b>{d.flags[k]}</b></button>)}
      </div>
      {list.length === 0 ? <Empty title="No clients to show">{d.clients.length ? tr("No client has this flag.") : tr("No running work in the Delivery Clients pipeline and no sales deal won in the last three years.")}</Empty> :
        <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Client book")}>
          <table className="fc-table cl-table">
            <thead><tr><th>{tr("Client")}</th><th>{tr("A month")}</th><th>{tr("People")}</th><th>{tr("Ends")}</th><th>{tr("Last contact")}</th><th>{tr("Next step")}</th><th>{tr("Hiring and signals")}</th><th>{tr("Needs attention")}</th></tr></thead>
            <tbody>{list.map((c) => {
              const k = key(c), isOpen = open === k;
              return <Fragment key={k}>
                <tr>
                  <td><button type="button" className="cl-name" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? "" : k)}>{c.name}</button>
                    <br /><small className="muted">{c.owner || tr("No owner")}{c.account_id && <> · <Link to={`/accounts/${c.account_id}`}>{tr("Account")}</Link></>}</small></td>
                  <td className="num">{money(c.monthly)}<br /><small className="muted">{pct(c.share)}</small></td>
                  <td className="num">{c.fte || "–"}</td>
                  <td><Ends c={c} /></td>
                  <td>{c.last_contact ? <>{fmtDate(c.last_contact)}<br /><small className="muted">{tr("{} days ago", c.days_since_contact)}</small></> : <span className="muted">–</span>}</td>
                  <td>{c.next_step ? fmtDate(c.next_step) : <span className="muted">–</span>}</td>
                  <td>{c.hiring && <div>{tr("{} open job ads", c.hiring.n)}</div>}{c.signals && <div className="small">{tr("{} buying signals", c.signals.n)}</div>}{!c.hiring && !c.signals && <span className="muted">–</span>}</td>
                  <td><span className="rv-chips">{c.flags.map((f: string) => <Badge key={f} kind={FLAG[f]?.kind}>{FLAG[f]?.label ?? f}</Badge>)}</span></td>
                </tr>
                {isOpen && <tr className="cl-detail"><td colSpan={8}>
                  {c.work.length > 0 && <><b>{tr("Running work")}</b><ul>{c.work.map((w: any) => <li key={w.crm_id}>{w.title}: {money(w.monthly)} {d.currency} {tr("a month")}, {fmtDate(w.start)} – {fmtDate(w.end)} <span className="muted">({tr(w.end_source)})</span>{w.fte ? ` · ${tr("{} people", w.fte)}` : ""}</li>)}</ul></>}
                  {c.open_roles.length > 0 && <><b>{tr("Open roles")}</b><ul>{c.open_roles.map((r: any) => <li key={r.crm_id}>{r.title}: {money(r.monthly)} {d.currency} {tr("a month")}{r.estimated ? ` (${tr("typical value")})` : ""}, {tr("from {}", fmtDate(r.start))}</li>)}</ul></>}
                  {c.on_hold.length > 0 && <><b>{tr("On hold")}</b><ul>{c.on_hold.map((r: any) => <li key={r.crm_id}>{r.title}{r.monthly ? `: ${money(r.monthly)} ${d.currency} ${tr("a month")}` : ""}</li>)}</ul></>}
                  {c.no_value.length > 0 && <><b>{tr("Deals without a value")}</b><ul>{c.no_value.map((r: any) => <li key={r.crm_id}>{r.title}{r.stage ? ` · ${r.stage}` : ""}</li>)}</ul></>}
                  {c.hiring && <><b>{tr("Hiring now")}</b><ul>{c.hiring.titles.map((t: string) => <li key={t}>{t}</li>)}</ul></>}
                  {c.signals && <p className="small">{tr("Latest buying signal {}", fmtDate(c.signals.latest))}{c.account_id && <> · <Link to={`/accounts/${c.account_id}`}>{tr("See them on the account")}</Link></>}</p>}
                </td></tr>}
              </Fragment>;
            })}</tbody>
          </table>
        </div>}
      <p className="small muted">{tr("As of {}.", fmtDate(d.as_of))}</p>
    </>
  );
}
