import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, api, post } from "../api";
import { tr } from "../locale";
import { Brand, Skeleton, lab } from "../components/ui";
import "../styles/assistant.css";

/* The consent page Claude or ChatGPT sends a person to when they connect the app (/oauth/authorize hands over here).
   It says which app asks, where the approval is sent, and as whom; nothing is issued until the person allows it. */
type Info = { client_name: string; redirect: { host: string; known_as: string | null; loopback: boolean }; email: string; role: string; already_connected: boolean; server: string };

export default function ConnectAuthorize() {
  const [sp] = useSearchParams();
  const [info, setInfo] = useState<Info | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const p = Object.fromEntries(sp.entries());

  useEffect(() => {
    if (!p.client_id || !p.redirect_uri || !p.code_challenge) { setErr(tr("This connection link is incomplete. Start again from Claude or ChatGPT.")); return; }
    api<Info>(`/api/oauth/request?${new URLSearchParams({ client_id: p.client_id, redirect_uri: p.redirect_uri })}`)
      .then(setInfo).catch((e) => setErr(e instanceof ApiError ? e.message : tr("The server did not respond. Try again in a minute.")));
  }, []);

  const decide = async (approve: boolean) => {
    setBusy(true);
    try {
      const r = await post<{ redirect: string }>("/api/oauth/approve", {
        client_id: p.client_id, redirect_uri: p.redirect_uri, code_challenge: p.code_challenge, code_challenge_method: p.code_challenge_method || "S256",
        state: p.state, scope: p.scope, resource: p.resource, approve });
      window.location.assign(r.redirect);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("The server did not respond. Try again in a minute."));
      setBusy(false);
    }
  };

  const name = info ? (info.redirect.known_as ?? info.client_name) : "";
  return <div className="ca">
    <div className="ca-card">
      <div className="ca-brand"><Brand /></div>
      {err && <><h1>{tr("This connection cannot go ahead")}</h1><p className="err" role="alert">{err}</p></>}
      {!err && !info && <Skeleton rows={3} label={tr("Checking the connection request")} />}
      {!err && info && <>
        <h1>{tr("Connect {} to The Codest Revenue Intelligence?", name)}</h1>
        <ul className="ca-facts">
          <li><b>{tr("It will read as you:")}</b> {info.email} ({lab("role", info.role)}). {tr("It sees exactly what you can see in the app: accounts, signals, people, tenders, the forecast, competitors.")}</li>
          <li><b>{tr("It can only read.")}</b> {tr("It cannot change, send, approve or export anything.")}</li>
          <li><b>{tr("You stay in control.")}</b> {tr("Disconnect it any time under My connections; access ends at once.")}</li>
        </ul>
        <p className={`ca-dest${info.redirect.known_as ? "" : " warn"}`}>
          {tr("After you choose, you go back to")} <b>{info.redirect.host}</b>
          {info.redirect.known_as && <> ({info.redirect.known_as})</>}
          {info.redirect.loopback && <>. {tr("That is an app on this computer, for example Claude Code. Allow it only if you started this connection yourself just now.")}</>}
          {info.client_name !== name && <><br /><span className="ca-small">{tr("The app calls itself \"{}\".", info.client_name)}</span></>}
        </p>
        {info.already_connected && <p className="ca-small">{tr("You connected this app before. Allowing again starts a fresh connection.")}</p>}
        <div className="ca-actions">
          <button type="button" className="primary" disabled={busy} onClick={() => void decide(true)} autoFocus>{tr("Allow read access")}</button>
          <button type="button" disabled={busy} onClick={() => void decide(false)}>{tr("Cancel")}</button>
        </div>
      </>}
    </div>
  </div>;
}
