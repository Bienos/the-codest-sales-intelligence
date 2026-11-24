import { getLanguage, tr } from "./locale";

// Thin API client. In production an authenticating reverse proxy (e.g. oauth2-proxy) injects the OIDC bearer token;
// in development a signed dev token can be pasted once and is kept in sessionStorage only.
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

function token(): string | null {
  try { return sessionStorage.getItem("ri_token"); } catch { return null; }
}
export function clearToken() { try { sessionStorage.removeItem("ri_token"); } catch { /* storage unavailable */ } }
export function setToken(t: string) { try { sessionStorage.setItem("ri_token", t); } catch { /* storage unavailable */ } }

function expiresIn(t: string): number | null {        // seconds left on one of our own sessions; null for any other token
  try { const c = JSON.parse(atob(t.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))); return c.typ === "session" ? c.exp - Date.now() / 1000 : null; } catch { return null; }
}
/** Swap a Google token for our 12-hour session (so people are not signed out every hour). Quietly keeps the old token if sessions are not configured. */
export async function startSession(): Promise<void> {
  const t = token(); if (!t) return;
  try {
    const r = await fetch("/api/auth/session", { method: "POST", headers: { Authorization: `Bearer ${t}`, "Content-Type": "application/json" }, body: "{}" });
    if (r.ok) { const j = await r.json(); setToken(j.token); }
  } catch { /* offline: keep what we have */ }
}
let renewing = false;
async function renewSoon() {
  const t = token(); const left = t ? expiresIn(t) : null;
  if (left !== null && left < 3 * 3600 && !renewing) { renewing = true; try { await startSession(); } finally { renewing = false; } }
}

export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const t = token();
  void renewSoon();
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}), ...(init.headers || {}) },
  });
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
  return isNaN(d.getTime()) ? tr("Unknown") : d.toLocaleDateString(getLanguage(), { year: "numeric", month: "short", day: "numeric" });
}
export function fmtDateTime(v?: string | null): string {
  if (!v) return tr("Unknown");
  const d = new Date(v);
  return isNaN(d.getTime()) ? tr("Unknown") : d.toLocaleString(getLanguage(), { dateStyle: "medium", timeStyle: "short" });
}

export async function upload<T = any>(path: string, file: File): Promise<T> {
  const t = token();
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(path, { method: "POST", body: fd, headers: t ? { Authorization: `Bearer ${t}` } : {} });
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
