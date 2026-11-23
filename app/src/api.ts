export interface Cue {
  index: number;
  start: number;
  end: number;
  text_zh: string;
  text_en: string;
  confidence?: number;
  uncertain?: boolean;
}

export interface EngineInfo {
  id: string;
  name: string;
  available: boolean;
  local: boolean;
  notes: string;
}

export interface GlossaryData {
  series_id: string;
  terms: Record<string, string>;
  path: string;
}

let _token = "";
let _base = "http://127.0.0.1:18765";

export function setApiBase(url: string) {
  _base = url.replace(/\/$/, "");
}

export function setApiToken(token: string) {
  _token = token || "";
}

export function apiToken() {
  return _token;
}

export function apiBase() {
  return _base;
}

function headers(json = true): HeadersInit {
  const h: Record<string, string> = {};
  if (json) h["Content-Type"] = "application/json";
  if (_token) h.Authorization = `Bearer ${_token}`;
  return h;
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${_base}${path}`, { headers: headers(false) });
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as T;
}

export async function apiSend<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(`${_base}${path}`, {
    method,
    headers: headers(true),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as T;
}

export function eventsUrl(jobId: string): string {
  const q = _token ? `?token=${encodeURIComponent(_token)}` : "";
  return `${_base}/jobs/${jobId}/events${q}`;
}
