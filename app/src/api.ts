export interface Cue {
  index: number;
  start: number;
  end: number;
  text_zh: string;
  text_en: string;
  confidence?: number;
  ocr_confidence?: number;
  timing_confidence?: number;
  uncertain?: boolean;
  provisional?: boolean;
  approved?: boolean;
  maturity?: "provisional" | "context-refined" | "context-complete" | "final";
  cue_id?: string;
  revision_count?: number;
  original_ocr?: string;
  audio_transcript?: string;
  reconciled_zh?: string;
  audio_disagreement?: boolean;
  audio_evidence?: {
    asr_confidence?: number;
    char_similarity?: number;
    notes?: string;
    disagreement?: boolean;
  };
}

export interface AudioVerifyStatus {
  phase?: string;
  enabled?: boolean;
  ready?: boolean;
  package?: string | null;
  model_size_default?: string;
  model_id?: string;
  note?: string;
  never_overwrite_ocr?: boolean;
}

export interface AudioSummary {
  ok?: boolean;
  error?: string;
  disagreements?: number;
  segments?: number;
  cues?: number;
  engine?: string;
  model_size?: string;
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

export class ApiError extends Error {
  status: number;
  raw: string;

  constructor(status: number, raw: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.raw = raw;
  }
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

export function formatApiError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return formatApiErrorText(err.message);
  return formatApiErrorText(String(err));
}

export function formatApiErrorText(raw: string): string {
  const text = (raw || "").trim();
  if (!text) return "Request failed.";
  if (/failed to fetch/i.test(text) || /networkerror/i.test(text) || text === "timeout") {
    return "Engine offline — click Retry engine, or restart with npm run dev.";
  }
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") {
      const d = parsed.detail;
      if (d === "Not Found") {
        return "Engine route missing — outdated sidecar. Retry engine / restart app.";
      }
      if (d === "Unauthorized") {
        return "Engine auth mismatch — Retry engine to refresh the token.";
      }
      if (d.toLowerCase().includes("video not found")) {
        return "Video file not found on disk. Pick the file again.";
      }
      return d;
    }
    if (Array.isArray(parsed.detail)) {
      return parsed.detail
        .map((item) => {
          if (item && typeof item === "object" && "msg" in item) {
            return String((item as { msg: string }).msg);
          }
          return JSON.stringify(item);
        })
        .join("; ");
    }
  } catch {
    /* not JSON */
  }
  if (text.length > 180) return `${text.slice(0, 180)}…`;
  return text;
}

async function viaIpc(
  pathName: string,
  method: string,
  body?: unknown
): Promise<{ ok: boolean; status: number; raw: string; error?: string }> {
  if (!window.subparty?.engineFetch) {
    throw new Error("no-ipc");
  }
  return window.subparty.engineFetch(pathName, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function raiseFromResult(res: { ok: boolean; status: number; raw: string; error?: string }) {
  if (res.ok) return;
  if (res.status === 0) {
    throw new ApiError(0, res.error || "", formatApiErrorText(res.error || "Failed to fetch"));
  }
  throw new ApiError(res.status, res.raw, formatApiErrorText(res.raw || `HTTP ${res.status}`));
}

export async function apiGet<T>(path: string): Promise<T> {
  try {
    const res = await viaIpc(path, "GET");
    await raiseFromResult(res);
    return JSON.parse(res.raw) as T;
  } catch (err) {
    if (!(err instanceof Error) || err.message !== "no-ipc") throw err;
  }
  const res = await fetch(`${_base}${path}`, { headers: headers(false) });
  if (!res.ok) {
    const raw = await res.text();
    throw new ApiError(res.status, raw, formatApiErrorText(raw || `HTTP ${res.status}`));
  }
  return (await res.json()) as T;
}

export async function apiSend<T>(path: string, method: string, body?: unknown): Promise<T> {
  try {
    const res = await viaIpc(path, method, body);
    await raiseFromResult(res);
    return JSON.parse(res.raw || "null") as T;
  } catch (err) {
    if (!(err instanceof Error) || err.message !== "no-ipc") throw err;
  }
  const res = await fetch(`${_base}${path}`, {
    method,
    headers: headers(true),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const raw = await res.text();
    throw new ApiError(res.status, raw, formatApiErrorText(raw || `HTTP ${res.status}`));
  }
  return (await res.json()) as T;
}

export async function apiSendOptional(path: string, method: string, body?: unknown): Promise<boolean> {
  try {
    await apiSend(path, method, body);
    return true;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return false;
    throw err;
  }
}

export async function pingHealth(): Promise<{
  ok: boolean;
  version: string | null;
  error?: string;
}> {
  if (window.subparty?.engineHealth) {
    const h = await window.subparty.engineHealth();
    return { ok: !!h.ok, version: h.version || null, error: h.error };
  }
  try {
    const res = await fetch(`${_base}/health`);
    if (!res.ok) return { ok: false, version: null, error: `HTTP ${res.status}` };
    const body = (await res.json()) as { version?: string };
    return { ok: true, version: body.version || null };
  } catch (err) {
    return {
      ok: false,
      version: null,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

export function eventsUrl(jobId: string): string {
  const q = _token ? `?token=${encodeURIComponent(_token)}` : "";
  return `${_base}/jobs/${jobId}/events${q}`;
}
