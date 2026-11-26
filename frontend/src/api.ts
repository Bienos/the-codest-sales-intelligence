import { getLanguage, tr } from "./locale";

// Thin API client. In production an authenticating reverse proxy (e.g. oauth2-proxy) injects the OIDC bearer token;
// in development a signed dev token can be pasted once.
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

const KEY = "ri_token";
/** Sent when the server says the sign-in is no longer valid, so the app can show the sign-in screen instead of a page full of errors. */
export const AUTH_EXPIRED = "ri-auth-expired";

/** The sign-in is kept in localStorage: every tab shares it and it survives closing the browser (or the phone discarding a tab).
 *  It still ends on its own: 12 hours without use, and never later than 7 days after the last Google sign-in (the server decides). */
export function getToken(): string | null {
  try { const t = localStorage.getItem(KEY); if (t) return t; } catch { /* storage unavailable: look at this tab's own copy */ }
  try {
    const old = sessionStorage.getItem(KEY);           // signed in before this change, or localStorage is blocked: carry it over once
    if (old) { try { localStorage.setItem(KEY, old); sessionStorage.removeItem(KEY); } catch { /* keep the tab copy */ } return old; }
  } catch { /* storage unavailable */ }
  return null;
}
export function clearToken() {
  try { localStorage.removeItem(KEY); } catch { /* storage unavailable */ }
  try { sessionStorage.removeItem(KEY); } catch { /* storage unavailable */ }
}
export function setToken(t: string) {
  try { localStorage.setItem(KEY, t); try { sessionStorage.removeItem(KEY); } catch { /* storage unavailable */ } return; } catch { /* private window: this tab only */ }
  try { sessionStorage.setItem(KEY, t); } catch { /* storage unavailable */ }
}
const token = getToken;

function expiresIn(t: string): number | null {        // seconds left on one of our own sessions; null for any other token
  try { const c = JSON.parse(atob(t.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))); return c.typ === "session" ? c.exp - Date.now() / 1000 : null; } catch { return null; }
}
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
/** Swap a Google token for our 12-hour session (so people are not signed out every hour). A server that is still waking up is tried again;
 *  a refusal is final. Returns whether a session was started. */
export async function startSession(): Promise<boolean> {
  const t = token(); if (!t) return false;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const r = await fetch("/api/auth/session", { method: "POST", headers: { Authorization: `Bearer ${t}`, "Content-Type": "application/json" }, body: "{}" });
      if (r.ok) { const j = await r.json(); setToken(j.token); return true; }
      if (r.status < 500) return false;
    } catch { /* offline or the server is starting: try again */ }
    await pause(1500 * (attempt + 1));
  }
  return false;
}

const KEEP_HOURS = 6;               // while the app is open, keep at least this much of the session in hand
const RENEW_EVERY_MS = 5 * 60 * 1000;
let renewing = false;
let lastRenew = 0;
/** Renew when less than KEEP_HOURS are left (or when a plain Google token was never swapped for a session). At most once every few minutes. */
export async function renewSession(): Promise<void> {
  const t = token(); if (!t || renewing) return;
  const left = expiresIn(t);
  if (left !== null && left > KEEP_HOURS * 3600) return;
  if (Date.now() - lastRenew < RENEW_EVERY_MS) return;
  renewing = true; lastRenew = Date.now();
  try { await startSession(); } finally { renewing = false; }
}
/** Keeps an open app signed in: checks every 15 minutes and whenever the tab comes back into view or the connection returns. Returns a stop function. */
export function keepSessionAlive(): () => void {
  const check = () => { if (document.visibilityState !== "hidden") void renewSession(); };
  const timer = window.setInterval(check, 15 * 60 * 1000);
  document.addEventListener("visibilitychange", check);
  window.addEventListener("online", check);
  void renewSession();
  return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", check); window.removeEventListener("online", check); };
}
/** A 401 for a request that carried a sign-in: tell the app, once. (A visitor with no sign-in at all just sees the sign-in screen.) */
function signInEnded(path: string, sent: string | null) {
  if (sent && path !== "/api/me" && !path.startsWith("/api/auth/")) window.dispatchEvent(new Event(AUTH_EXPIRED));
}

export async function api<T = any>(path: string, init: RequestInit = {}, retried = false): Promise<T> {
  const t = token();
  void renewSession();
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}), ...(init.headers || {}) },
  });
  if (res.status === 401 && t && !retried && path !== "/api/me" && !path.startsWith("/api/auth/")) {
    const now = token();
    if (now && now !== t) return api<T>(path, init, true);                             // another tab renewed the sign-in a moment ago
    if (expiresIn(t) === null && await startSession()) return api<T>(path, init, true); // still on a plain Google token: swap it and try once more
  }
  if (res.status === 401) signInEnded(path, t);
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* not json */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}
export const post = <T = any>(p: string, body?: unknown) => api<T>(p, { method: "POST", body: JSON.stringify(body ?? {}) });
export const patch = <T = any>(p: string, body?: unknown) => api<T>(p, { method: "PATCH", body: JSON.stringify(body ?? {}) });
export const put = <T = any>(p: string, body?: unknown) => api<T>(p, { method: "PUT", body: JSON.stringify(body ?? {}) });

export function fmtDate(v?: string | null): string {
  if (!v) return tr("Unknown");
  const d = new Date(v);
  return isNaN(d.getTime()) ? tr("Unknown") : d.toLocaleDateString(getLanguage() === "pl" ? "pl-PL" : "en-GB", { year: "numeric", month: "short", day: "numeric" });
}
export function fmtDateTime(v?: string | null): string {
  if (!v) return tr("Unknown");
  const d = new Date(v);
  return isNaN(d.getTime()) ? tr("Unknown") : d.toLocaleString(getLanguage() === "pl" ? "pl-PL" : "en-GB", { dateStyle: "medium", timeStyle: "short" });
}

export async function upload<T = any>(path: string, file: File): Promise<T> {
  const t = token();
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(path, { method: "POST", body: fd, headers: t ? { Authorization: `Bearer ${t}` } : {} });
  if (res.status === 401) signInEnded(path, t);
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* not json */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

/** Fire-and-forget product instrumentation: never blocks or breaks the click. */
export function trackSourceOpened(body: { organization_id?: string; hypothesis_id?: string; where?: "evidence" | "prospect" }) {
  void post("/api/telemetry/source-opened", body).catch(() => undefined);
}
