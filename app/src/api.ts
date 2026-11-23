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
  cue_id?: string;
  revision_count?: number;
  original_ocr?: string;
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

/** Turn FastAPI / fetch failures into short human text (never raw JSON blobs). */
export function formatApiError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return formatApiErrorText(err.message);
  return formatApiErrorText(String(err));
}

export function formatApiErrorText(raw: string): string {
  const text = (raw || "").trim();
  if (!text) return "Request failed.";
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") {
      const d = parsed.detail;
      if (d === "Not Found") {
        return "Engine route not found — the sidecar looks outdated. Quit SubParty fully and run npm run dev again so engine 2.0 starts.";
      }
      if (d === "Unauthorized") {
        return "Engine rejected the request (auth). Restart the app so it reloads the engine token.";
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

async function raiseForStatus(res: Response): Promise<void> {
  if (res.ok) return;
  const raw = await res.text();
  throw new ApiError(res.status, raw, formatApiErrorText(raw || `HTTP ${res.status}`));
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${_base}${path}`, { headers: headers(false) });
  await raiseForStatus(res);
  return (await res.json()) as T;
}

export async function apiSend<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(`${_base}${path}`, {
    method,
    headers: headers(true),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  await raiseForStatus(res);
  return (await res.json()) as T;
}

/** Best-effort call — ignores 404 (old engines) so Process can continue. */
export async function apiSendOptional(path: string, method: string, body?: unknown): Promise<boolean> {
  try {
    await apiSend(path, method, body);
    return true;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return false;
    throw err;
  }
}

export function eventsUrl(jobId: string): string {
  const q = _token ? `?token=${encodeURIComponent(_token)}` : "";
  return `${_base}/jobs/${jobId}/events${q}`;
}
