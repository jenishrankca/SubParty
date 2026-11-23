import { useEffect, useMemo, useRef, useState } from "react";
import {
  apiBase,
  apiGet,
  apiSend,
  apiSendOptional,
  apiToken,
  eventsUrl,
  formatApiError,
  pingHealth,
  setApiBase,
  setApiToken,
  type AudioSummary,
  type AudioVerifyStatus,
  type Cue,
  type EngineInfo,
  type GlossaryData,
  type SetupChecklist,
} from "./api";
import { CueInspector } from "./CueInspector";
import { TimelineRail } from "./TimelineRail";
import { buildReviewQueue, computeQualitySummary, processingStages } from "./workspaceStats";

type JobStatus = "idle" | "queued" | "running" | "paused" | "done" | "error" | "cancelled";

interface ModelInfo {
  id: string;
  name: string;
  kind: string;
  installed: boolean;
  engine?: string;
  optional?: boolean;
  required?: boolean;
  size_hint?: string;
  notes?: string;
  first_run?: string;
  path?: string;
}

interface RegionConfirmable {
  region: { x: number; y: number; w: number; h: number };
  source?: string;
  confidence?: number;
  confirmed?: boolean;
  evidence?: Record<string, unknown>;
}

function formatTime(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

/** Live Ready: small playhead-ahead buffer (wall ≤30s). Quality uses a deeper gate. */
const LIVE_BUFFER_AHEAD_TARGET_S = 5;
const LIVE_BUFFER_MIN_CUES = 1;
const QUALITY_BUFFER_AHEAD_TARGET_S = 12;
const QUALITY_BUFFER_MIN_CUES = 3;

type WatchMode = "live" | "quality";
type SubtitleGate = "idle" | "preparing" | "ready";

interface BufferStatus {
  ready: boolean;
  coveredAhead: number;
  cueCountInWindow: number;
  hasActiveOrNear: boolean;
}

function cueHasText(c: Cue): boolean {
  return Boolean((c.text_en || "").trim() || (c.text_zh || "").trim());
}

function computeBufferStatus(
  cues: Cue[],
  t: number,
  jobDone: boolean,
  watchMode: WatchMode = "live",
  engineBufferAhead: number | null = null
): BufferStatus {
  const target = watchMode === "live" ? LIVE_BUFFER_AHEAD_TARGET_S : QUALITY_BUFFER_AHEAD_TARGET_S;
  const minCues = watchMode === "live" ? LIVE_BUFFER_MIN_CUES : QUALITY_BUFFER_MIN_CUES;
  const windowEnd = t + target;
  const inWindow = cues.filter(
    (c) => cueHasText(c) && c.end > t - 0.25 && c.start < windowEnd + 0.5
  );
  let coveredAhead = 0;
  if (inWindow.length) {
    const farthest = inWindow.reduce((m, c) => Math.max(m, c.end), t);
    coveredAhead = Math.max(0, farthest - t);
  }
  // Prefer engine frontier when it reports further ahead than cue ends alone
  if (engineBufferAhead != null && engineBufferAhead > coveredAhead) {
    coveredAhead = engineBufferAhead;
  }
  const hasActiveOrNear = cues.some(
    (c) => cueHasText(c) && c.start <= t + 2.5 && c.end >= t - 0.15
  );
  const cueCountInWindow = inWindow.length;
  const ready =
    jobDone && cues.some(cueHasText)
      ? true
      : watchMode === "live"
        ? // Live: unlock once a small playhead-ahead scan exists (silence OK) or first cue
          (engineBufferAhead ?? 0) >= 2.5 ||
          (cueCountInWindow >= minCues && coveredAhead >= 2) ||
          (hasActiveOrNear && cueCountInWindow >= 1) ||
          (cues.some(cueHasText) && (engineBufferAhead ?? 0) >= 1.5)
        : (coveredAhead >= target * 0.7 && cueCountInWindow >= 2) ||
          (cueCountInWindow >= minCues && coveredAhead >= 6) ||
          (hasActiveOrNear && cueCountInWindow >= minCues && coveredAhead >= 4);
  return { ready, coveredAhead, cueCountInWindow, hasActiveOrNear };
}

function mergeIncomingCue(prev: Cue[], cue: Cue): Cue[] {
  const next = [...prev];
  let idx = -1;
  if (cue.cue_id) {
    idx = next.findIndex((c) => c.cue_id && c.cue_id === cue.cue_id);
  }
  if (idx < 0) {
    idx = next.findIndex(
      (c) =>
        Math.abs(c.start - cue.start) < 0.2 &&
        (c.text_zh || "") === (cue.text_zh || "")
    );
  }
  if (idx < 0) {
    idx = next.findIndex((c) => Math.abs(c.start - cue.start) < 0.08);
  }
  if (idx >= 0) {
    next[idx] = { ...next[idx], ...cue };
  } else {
    next.push(cue);
  }
  next.sort((a, b) => a.start - b.start);
  return next;
}

function findActiveCue(cues: Cue[], t: number): Cue | null {
  // Prefer an in-range cue that has English (or any text)
  let best: Cue | null = null;
  let bestScore = -1;
  for (const c of cues) {
    const inRange = t >= c.start - 0.05 && t <= c.end + 0.12;
    const near = c.start <= t + 0.35 && c.end >= t - 0.35;
    if (!inRange && !near) continue;
    const en = (c.text_en || "").trim();
    const zh = (c.text_zh || "").trim();
    if (!en && !zh) continue;
    let score = inRange ? 10 : 3;
    if (en) score += 5;
    if (c.approved) score += 1;
    if (!c.provisional) score += 1;
    // Prefer the cue whose midpoint is closest to t
    const mid = (c.start + c.end) / 2;
    score -= Math.min(2, Math.abs(mid - t) * 0.15);
    if (score > bestScore) {
      bestScore = score;
      best = c;
    }
  }
  return best;
}

export function App() {
  const [engineUrl, setEngineUrl] = useState("http://127.0.0.1:18765");
  const [videoPath, setVideoPath] = useState<string | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [srtPath, setSrtPath] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<JobStatus>("idle");
  const [progress, setProgress] = useState(0);
  const [duration, setDuration] = useState(0);
  const [cueCount, setCueCount] = useState(0);
  const [cues, setCues] = useState<Cue[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [playTime, setPlayTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [engineOk, setEngineOk] = useState<boolean | null>(null);
  const [engineVersion, setEngineVersion] = useState<string | null>(null);
  const [avgSlotMs, setAvgSlotMs] = useState<number | null>(null);
  const [liveLag, setLiveLag] = useState<number | null>(null);
  const [bufferAhead, setBufferAhead] = useState<number | null>(null);
  const [sampleInterval, setSampleInterval] = useState<number | null>(null);
  const [bilingual, setBilingual] = useState(true);
  const [seriesId, setSeriesId] = useState<string | null>(null);
  const [regionLabel, setRegionLabel] = useState<string | null>(null);
  const [regionConfirmable, setRegionConfirmable] = useState<RegionConfirmable | null>(null);
  const [regionBusy, setRegionBusy] = useState(false);
  const [glossaryCount, setGlossaryCount] = useState<number | null>(null);
  const [reviewOpen, setReviewOpen] = useState(true);
  const [setup, setSetup] = useState<SetupChecklist | null>(null);
  const [setupDismissed, setSetupDismissed] = useState(() => {
    try {
      return sessionStorage.getItem("subparty.setup.dismissed") === "1";
    } catch {
      return false;
    }
  });
  const [engines, setEngines] = useState<EngineInfo[]>([]);
  const [engineId, setEngineId] = useState("argos");
  const [watchMode, setWatchMode] = useState<WatchMode>("live");
  const [glossary, setGlossary] = useState<GlossaryData | null>(null);
  const [gZh, setGZh] = useState("");
  const [gEn, setGEn] = useState("");
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [editCue, setEditCue] = useState<Cue | null>(null);
  const [panel, setPanel] = useState<"inspect" | "job" | "glossary" | "timeline" | "qa" | "batch" | "models">(
    "inspect"
  );
  const [batchNote, setBatchNote] = useState<string | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [batchStatus, setBatchStatus] = useState<{
    total: number;
    progress: number;
    complete?: boolean;
    by_status?: Record<string, number>;
    jobs?: Array<Record<string, unknown>>;
  } | null>(null);
  const [qaReport, setQaReport] = useState<Record<string, unknown> | null>(null);
  const [seriesList, setSeriesList] = useState<Array<{ series_id: string; term_count: number; path: string }>>(
    []
  );
  const [nameSuggestions, setNameSuggestions] = useState<Array<{ zh: string; en: string; count: string }>>(
    []
  );
  const [goldPath, setGoldPath] = useState<string | null>(null);
  const [qaReportPath, setQaReportPath] = useState<string | null>(null);
  const [characters, setCharacters] = useState<
    Array<{ zh: string; en: string; role?: string; notes?: string; aliases?: string[] }>
  >([]);
  const [mergeStart, setMergeStart] = useState<number | null>(null);
  const [recovery, setRecovery] = useState<Array<Record<string, unknown>>>([]);
  const [recoveryOpen, setRecoveryOpen] = useState(false);
  const [processNote, setProcessNote] = useState<string | null>(null);
  const [audioVerify, setAudioVerify] = useState(false);
  const [audioReady, setAudioReady] = useState<AudioVerifyStatus | null>(null);
  const [audioSummary, setAudioSummary] = useState<AudioSummary | null>(null);
  const [subtitleGate, setSubtitleGate] = useState<SubtitleGate>("idle");
  const [bufferOrigin, setBufferOrigin] = useState(0);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [audioInfo, setAudioInfo] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const esRef = useRef<EventSource | null>(null);
  const jobIdRef = useRef<string | null>(null);
  const engineUrlRef = useRef(engineUrl);
  const subtitleGateRef = useRef<SubtitleGate>("idle");
  const bufferOriginRef = useRef(0);

  useEffect(() => {
    jobIdRef.current = jobId;
  }, [jobId]);
  useEffect(() => {
    engineUrlRef.current = engineUrl;
  }, [engineUrl]);
  useEffect(() => {
    subtitleGateRef.current = subtitleGate;
  }, [subtitleGate]);
  useEffect(() => {
    bufferOriginRef.current = bufferOrigin;
  }, [bufferOrigin]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    v.volume = Math.max(0, Math.min(1, volume));
    v.muted = muted;
  }, [volume, muted, videoUrl]);

  async function loadPlayableVideo(filePath: string): Promise<void> {
    setAudioInfo(null);
    if (window.subparty.prepareVideo) {
      setBatchNote("Preparing player audio…");
      const prep = await window.subparty.prepareVideo(filePath);
      setVideoUrl(prep.url);
      const a0 = prep.audio && prep.audio[0];
      const codec = a0?.codec_name || "?";
      const ch = a0?.channels ? `${a0.channels}ch` : "";
      const sr = a0?.sample_rate ? `${a0.sample_rate}Hz` : "";
      const bits = [codec, ch, sr].filter(Boolean).join(" · ");
      if (prep.remuxed) {
        setAudioInfo(
          `${bits}${prep.cached ? " · cached fix" : " · remuxed for Chromium"}`
        );
        setBatchNote(
          prep.reason === "mp4-aac-edit-list"
            ? "Fixed silent AAC (MP4 edit list) for player — OCR still uses the original file."
            : `Player media remuxed (${prep.reason || "compat"}) — OCR still uses the original file.`
        );
      } else {
        setAudioInfo(bits || null);
        setBatchNote(null);
      }
      if (prep.error) setError(`Player prepare: ${prep.error}`);
      return;
    }
    const url = await window.subparty.pathToFileUrl(filePath);
    setVideoUrl(url);
  }

  useEffect(() => {
    void (async () => {
      if (window.subparty?.engineUrl) {
        const url = await window.subparty.engineUrl();
        setEngineUrl(url);
        setApiBase(url);
      }
      if (window.subparty?.engineToken) {
        const tok = await window.subparty.engineToken();
        setApiToken(tok);
      }
    })();
    return () => esRef.current?.close();
  }, []);

  useEffect(() => {
    setApiBase(engineUrl);
  }, [engineUrl]);

  useEffect(() => {
    let cancelled = false;
    const ping = async () => {
      const h = await pingHealth();
      if (cancelled) return;
      setEngineOk(h.ok);
      setEngineVersion(h.version);
      if (h.ok) {
        if (h.version && h.version.startsWith("0.")) {
          setError(
            `Engine ${h.version} is outdated (need 2.0+). Click Retry engine or restart npm run dev.`
          );
        } else {
          setError((prev) =>
            prev && /engine offline|failed to fetch|outdated sidecar|retry engine/i.test(prev)
              ? null
              : prev
          );
        }
        if (apiToken()) void refreshMeta();
      }
    };
    void ping();
    const id = window.setInterval(ping, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [engineUrl]);

  async function retryEngine() {
    setBusy(true);
    setError(null);
    try {
      if (window.subparty?.engineRestart) {
        const r = await window.subparty.engineRestart();
        if (!r.ok) throw new Error(r.error || "Engine restart failed");
      }
      if (window.subparty?.engineToken) {
        setApiToken(await window.subparty.engineToken());
      }
      const h = await pingHealth();
      setEngineOk(h.ok);
      setEngineVersion(h.version);
      if (!h.ok) {
        setError(formatApiError(new Error(h.error || "Failed to fetch")));
      } else {
        void refreshMeta();
      }
    } catch (err) {
      setEngineOk(false);
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!jobId || (status !== "running" && status !== "paused")) return;
    const id = window.setInterval(() => {
      const jid = jobIdRef.current;
      const v = videoRef.current;
      if (!jid || !v || status === "paused") return;
      // While preparing, keep engine playhead at the user anchor (not advancing video)
      const t =
        subtitleGateRef.current === "preparing" ? bufferOriginRef.current : v.currentTime;
      void apiSend(`/jobs/${jid}/playhead`, "POST", { t }).catch(() => undefined);
    }, 500);
    return () => window.clearInterval(id);
  }, [jobId, status]);

  // Smooth playhead → overlay sync (timeupdate alone is too coarse / skips when paused)
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      if (v) setPlayTime(v.currentTime);
      raf = window.requestAnimationFrame(tick);
    };
    raf = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(raf);
  }, [videoUrl]);

  const bufferClock = subtitleGate === "preparing" ? bufferOrigin : playTime;
  const bufferStatus = useMemo(
    () =>
      computeBufferStatus(
        cues,
        bufferClock,
        status === "done",
        watchMode,
        bufferAhead
      ),
    [cues, bufferClock, status, watchMode, bufferAhead]
  );

  useEffect(() => {
    if (subtitleGate !== "preparing" && subtitleGate !== "ready") return;
    if (bufferStatus.ready) {
      setSubtitleGate("ready");
    } else if (status === "running" || status === "paused" || status === "queued") {
      setSubtitleGate("preparing");
    }
  }, [bufferStatus.ready, status, subtitleGate]);

  // Hold video until buffer ready
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    if (subtitleGate === "preparing" && !v.paused) {
      v.pause();
      setPlaying(false);
    }
  }, [subtitleGate]);

  const activeCue = useMemo(() => findActiveCue(cues, playTime), [cues, playTime]);

  const overlayZh = (activeCue?.text_zh || "").trim();
  const overlayEn = (activeCue?.text_en || "").trim();
  const showOverlay = Boolean(activeCue && (overlayEn || overlayZh));

  const reviewQueue = useMemo(() => buildReviewQueue(cues), [cues]);

  async function refreshMeta() {
    try {
      const eng = await apiGet<{ engines: EngineInfo[] }>("/engines");
      setEngines(eng.engines || []);
      const mods = await apiGet<{ models: ModelInfo[]; setup?: SetupChecklist }>("/models");
      setModels(mods.models || []);
      if (mods.setup) setSetup(mods.setup);
      const rec = await apiGet<{ jobs: Array<Record<string, unknown>> }>("/recovery");
      setRecovery(rec.jobs || []);
      const health = await apiGet<{ audio_verify?: AudioVerifyStatus; setup?: SetupChecklist }>("/health");
      if (health.audio_verify) setAudioReady(health.audio_verify);
      if (health.setup) setSetup(health.setup);
      const series = await apiGet<{ series: Array<{ series_id: string; term_count: number; path: string }> }>(
        "/glossary/series"
      );
      setSeriesList(series.series || []);
    } catch {
      /* auth may not be ready yet — try open /setup */
      try {
        const s = await apiGet<SetupChecklist>("/setup");
        setSetup(s);
      } catch {
        /* ignore */
      }
    }
  }

  async function confirmRegionCrop() {
    if (!videoPath || !regionConfirmable?.region) return;
    setRegionBusy(true);
    try {
      const r = regionConfirmable.region;
      await apiSend("/region/confirm", "POST", {
        video_path: videoPath,
        x: r.x,
        y: r.y,
        w: r.w,
        h: r.h,
      });
      setRegionConfirmable({ ...regionConfirmable, confirmed: true, source: "user" });
      setRegionLabel(
        `confirmed y ${(r.y * 100).toFixed(0)}–${((r.y + r.h) * 100).toFixed(0)}%`
      );
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setRegionBusy(false);
    }
  }

  async function clearRegionCrop() {
    if (!videoPath) return;
    setRegionBusy(true);
    try {
      await apiSend("/region/clear", "POST", { video_path: videoPath });
      setRegionConfirmable(null);
      setRegionLabel(null);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setRegionBusy(false);
    }
  }

  async function refreshBatchStatus(id?: string | null) {
    const bid = id ?? batchId;
    if (!bid) return;
    try {
      const res = await apiGet<{
        total: number;
        progress: number;
        complete?: boolean;
        by_status?: Record<string, number>;
        jobs?: Array<Record<string, unknown>>;
      }>(`/jobs/batch/${bid}`);
      setBatchStatus(res);
    } catch (err) {
      setError(formatApiError(err));
    }
  }

  async function refreshQa(opts?: { exportHtml?: boolean; gold?: string | null }) {
    if (!jobId) return;
    setBusy(true);
    try {
      const gold = opts?.gold ?? goldPath;
      const exportHtml = opts?.exportHtml ?? false;
      let res: Record<string, unknown>;
      if (gold || exportHtml) {
        res = await apiSend<Record<string, unknown>>(`/jobs/${jobId}/qa`, "POST", {
          gold_path: gold || undefined,
          export_html: exportHtml,
        });
      } else {
        res = await apiGet<Record<string, unknown>>(`/jobs/${jobId}/qa`);
      }
      setQaReport(res);
      if (typeof res.report_html === "string") setQaReportPath(res.report_html);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  async function pickGoldJson() {
    if (!window.subparty.pickJson) {
      setError("Update the desktop shell to pick gold JSON files.");
      return;
    }
    const picked = await window.subparty.pickJson();
    if (!picked) return;
    setGoldPath(picked);
    void refreshQa({ gold: picked });
  }

  async function loadCharacters() {
    const sid = glossary?.series_id || seriesId;
    if (!sid) return;
    try {
      const res = await apiGet<{ characters: Array<{ zh: string; en: string; role?: string; notes?: string }> }>(
        `/characters/${encodeURIComponent(sid)}`
      );
      setCharacters(res.characters || []);
    } catch {
      /* optional */
    }
  }

  async function saveCharacterFromSuggestion(zh: string, en: string) {
    const sid = glossary?.series_id || seriesId;
    if (!sid || !zh) return;
    setBusy(true);
    try {
      const res = await apiSend<{ characters: Array<{ zh: string; en: string }> }>("/characters", "POST", {
        series_id: sid,
        zh,
        en: en || gEn || zh,
        role: "suggested",
        sync_glossary: true,
        video_path: videoPath,
      });
      setCharacters(res.characters || []);
      if (videoPath) void loadGlossary(videoPath);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  async function splitEditCue() {
    if (!jobId || !editCue) return;
    setBusy(true);
    try {
      const res = await apiSend<{ cues: Cue[] }>(`/jobs/${jobId}/cues/split`, "POST", {
        cue_id: editCue.cue_id,
        start: editCue.start,
      });
      setCues(res.cues || []);
      setCueCount((res.cues || []).length);
      setEditCue(null);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  async function mergeWithEditCue(otherStart: number) {
    if (!jobId || !editCue) return;
    setBusy(true);
    try {
      const res = await apiSend<{ cues: Cue[] }>(`/jobs/${jobId}/cues/merge`, "POST", {
        cue_id_a: editCue.cue_id,
        start_a: editCue.start,
        start_b: otherStart,
      });
      setCues(res.cues || []);
      setCueCount((res.cues || []).length);
      setEditCue(null);
      setMergeStart(null);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  async function retryFailedBatch() {
    if (!batchId) return;
    setBusy(true);
    try {
      await apiSend(`/jobs/batch/${batchId}/retry`, "POST", { engine_id: engineId });
      await refreshBatchStatus(batchId);
      setBatchNote(`Batch ${batchId.slice(0, 8)} · retry queued`);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  async function exportBatchAll() {
    if (!batchId) return;
    setBusy(true);
    try {
      const res = await apiSend<{ exported: Array<{ paths?: Record<string, string>; error?: string }> }>(
        `/jobs/batch/${batchId}/export`,
        "POST",
        { bilingual_srt: true, vtt: true, ass: true }
      );
      setBatchNote(`Batch export · ${res.exported?.length || 0} jobs`);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  async function refreshNameSuggestions() {
    if (!jobId && !videoPath) return;
    try {
      const q = jobId
        ? `/glossary/suggest?job_id=${encodeURIComponent(jobId)}`
        : `/glossary/suggest?video_path=${encodeURIComponent(videoPath || "")}`;
      const res = await apiGet<{ suggestions: Array<{ zh: string; en: string; count: string }> }>(q);
      setNameSuggestions(res.suggestions || []);
    } catch {
      /* optional */
    }
  }

  async function loadGlossary(path: string) {
    try {
      const g = await apiGet<GlossaryData>(`/glossary?video_path=${encodeURIComponent(path)}`);
      setGlossary(g);
      setGlossaryCount(Object.keys(g.terms || {}).length);
      setSeriesId(g.series_id);
    } catch (err) {
      setError(formatApiError(err));
    }
  }

  async function pickVideo() {
    setError(null);
    setBatchNote(null);
    const picked = await window.subparty.pickVideo();
    if (!picked) return;
    setVideoPath(picked);
    await loadPlayableVideo(picked);
    resetJobState();
    await loadGlossary(picked);
  }

  async function pickBatch() {
    setError(null);
    const paths = await window.subparty.pickVideos();
    if (!paths.length) return;
    setBusy(true);
    try {
      const res = await apiSend<{ batch_id: string; jobs: Array<{ id?: string; error?: string }> }>(
        "/jobs/batch",
        "POST",
        { video_paths: paths, engine_id: engineId, lookahead: 12, fps: 1 }
      );
      setBatchId(res.batch_id);
      setBatchNote(`Batch ${res.batch_id.slice(0, 8)} · ${res.jobs.length} jobs`);
      setPanel("batch");
      void refreshBatchStatus(res.batch_id);
      const first = res.jobs.find((j) => j.id);
      if (first?.id) {
        setVideoPath(paths[0]);
        await loadPlayableVideo(paths[0]);
        setJobId(first.id);
        setStatus("running");
        subscribe(first.id);
        await loadGlossary(paths[0]);
      }
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  function resetJobState() {
    esRef.current?.close();
    esRef.current = null;
    setSrtPath(null);
    setCues([]);
    setCueCount(0);
    setProgress(0);
    setStatus("idle");
    setJobId(null);
    setPlayTime(0);
    setAvgSlotMs(null);
    setLiveLag(null);
    setBufferAhead(null);
    setSampleInterval(null);
    setSeriesId(null);
    setRegionLabel(null);
    setGlossaryCount(null);
    setEditCue(null);
    setBatchNote(null);
    setError(null);
    setSubtitleGate("idle");
    setBufferOrigin(0);
  }

  async function refreshRecovery() {
    try {
      const rec = await apiGet<{ jobs: Array<Record<string, unknown>> }>("/recovery");
      setRecovery(rec.jobs || []);
    } catch {
      /* offline */
    }
  }

  async function cancelOrRemoveJob() {
    if (!jobId) {
      resetJobState();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await apiSend(`/jobs/${jobId}/remove`, "POST");
    } catch (err) {
      // Fallback for older sidecars
      try {
        await apiSend(`/jobs/${jobId}/cancel`, "POST", { discard: true });
      } catch (err2) {
        setError(formatApiError(err2 instanceof Error ? err2 : err));
        setBusy(false);
        return;
      }
    }
    resetJobState();
    await refreshRecovery();
    setBusy(false);
  }

  async function discardRecovery(snap: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      const res = await apiSend<{ jobs?: Array<Record<string, unknown>> }>("/recovery/discard", "POST", {
        job_id: snap.id,
        snapshot_path: snap._snapshot_path,
        video_path: snap.video_path,
      });
      if (jobId && String(snap.id) === jobId) {
        resetJobState();
      }
      setRecovery(res.jobs || []);
      if (!res.jobs) await refreshRecovery();
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  async function discardAllRecovery() {
    if (!recovery.length) return;
    setBusy(true);
    setError(null);
    try {
      const res = await apiSend<{ jobs?: Array<Record<string, unknown>> }>("/recovery/discard-all", "POST");
      const activeRecovering = jobId && recovery.some((r) => String(r.id) === jobId);
      if (activeRecovering || status === "running" || status === "paused") {
        resetJobState();
      }
      setRecovery(res.jobs || []);
      if (!res.jobs) await refreshRecovery();
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  async function startProcess() {
    if (!videoPath) return;
    setBusy(true);
    setError(null);
    setCues([]);
    setCueCount(0);
    setProgress(0);
    setStatus("queued");
    setAudioSummary(null);
    setSubtitleGate("preparing");
    esRef.current?.close();
    const v = videoRef.current;
    const startAt = v?.currentTime ?? 0;
    setBufferOrigin(startAt);
    setPlayTime(startAt);
    // Never auto-start — hold until subtitle buffer is ready
    if (v && !v.paused) {
      v.pause();
      setPlaying(false);
    }
    try {
      // Optional on older sidecars — never block Process on /engines/select 404
      const live = watchMode === "live";
      const engineForJob = live ? "argos" : engineId;
      if (live && engineId !== "argos") setEngineId("argos");
      await apiSendOptional("/engines/select", "POST", { engine_id: engineForJob });
      const job = await apiSend<{
        id: string;
        srt_path: string;
        status: JobStatus;
        duration: number;
      }>("/jobs", "POST", {
        video_path: videoPath,
        fps: live ? 1.0 : 1.0,
        band_ratio: 0.2,
        lookahead: live ? 8 : 12,
        playhead: startAt,
        engine_id: engineForJob,
        context_window_s: live ? 30 : 120,
        live_mode: live,
        high_accuracy: live ? false : true,
        audio_verify: live ? false : audioVerify,
      });
      setJobId(job.id);
      setSrtPath(job.srt_path);
      setStatus(job.status);
      setDuration(job.duration || 0);
      subscribe(job.id);
    } catch (err) {
      setStatus("error");
      setSubtitleGate("idle");
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  function subscribe(id: string) {
    const es = new EventSource(eventsUrl(id));
    esRef.current = es;
    es.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data) as Record<string, unknown>;
        const type = String(data.type || "");
        if (type === "cue" && data.cue) {
          const cue = data.cue as Cue;
          setCues((prev) => mergeIncomingCue(prev, cue));
          setCueCount(Number(data.cue_count ?? 0));
          if (typeof data.srt_path === "string") setSrtPath(data.srt_path);
        } else if (type === "region") {
          if (typeof data.series_id === "string") setSeriesId(data.series_id);
          if (typeof data.glossary_terms === "number") setGlossaryCount(data.glossary_terms);
          const region = data.region as { x?: number; y?: number; w?: number; h?: number } | undefined;
          const confirmable = data.region_confirmable as RegionConfirmable | undefined;
          if (confirmable?.region) {
            setRegionConfirmable(confirmable);
            const r = confirmable.region;
            setRegionLabel(
              `${confirmable.confirmed ? "confirmed " : "auto "}y ${(r.y * 100).toFixed(0)}–${(
                (r.y + r.h) *
                100
              ).toFixed(0)}%`
            );
          } else if (region && typeof region.y === "number" && typeof region.h === "number") {
            setRegionLabel(`y ${(region.y * 100).toFixed(0)}–${((region.y + region.h) * 100).toFixed(0)}%`);
            if (
              typeof region.x === "number" &&
              typeof region.w === "number"
            ) {
              setRegionConfirmable({
                region: { x: region.x, y: region.y, w: region.w, h: region.h },
                source: "auto",
                confirmed: false,
              });
            }
          }
        } else if (type === "progress") {
          setProgress(Number(data.progress ?? 0));
          if (typeof data.duration === "number") setDuration(data.duration);
          if (typeof data.avg_slot_ms === "number") setAvgSlotMs(data.avg_slot_ms);
          if (typeof data.live_lag === "number") setLiveLag(data.live_lag);
          if (typeof data.buffer_ahead === "number") setBufferAhead(data.buffer_ahead);
          if (typeof data.sample_interval === "number") setSampleInterval(data.sample_interval);
          if (typeof data.series_id === "string") setSeriesId(data.series_id);
          if (typeof data.note === "string") setProcessNote(data.note);
          setStatus("running");
        } else if (type === "audio") {
          if (data.audio && typeof data.audio === "object") {
            setAudioSummary(data.audio as AudioSummary);
          }
          setProcessNote("audio-verify");
        } else if (type === "status" || type === "done" || type === "error") {
          if (typeof data.status === "string") setStatus(data.status as JobStatus);
          if (typeof data.progress === "number") setProgress(data.progress);
          if (typeof data.cue_count === "number") setCueCount(data.cue_count);
          if (typeof data.error === "string") setError(data.error);
          if (typeof data.srt_path === "string") setSrtPath(data.srt_path);
          if (typeof data.series_id === "string") setSeriesId(data.series_id);
          if (typeof data.note === "string") setProcessNote(data.note);
          if (data.audio && typeof data.audio === "object") {
            setAudioSummary(data.audio as AudioSummary);
          }
          if (type === "done") setProcessNote(null);
        } else if (type === "end") {
          es.close();
        }
      } catch {
        /* ignore */
      }
    };
  }

  async function openFolder() {
    const target = srtPath || videoPath;
    if (!target) return;
    await window.subparty.openFolder(target);
  }

  function togglePlay() {
    const v = videoRef.current;
    if (!v) return;
    if (!v.paused) {
      v.pause();
      setPlaying(false);
      return;
    }
    if (subtitleGate === "preparing") {
      setError(null);
      setBatchNote("Still preparing subtitles — wait for Ready, then Play.");
      return;
    }
    void v.play().then(() => setPlaying(true)).catch(() => undefined);
  }

  function seekBy(delta: number) {
    const v = videoRef.current;
    if (!v) return;
    const next = Math.max(0, Math.min(v.duration || Number.MAX_SAFE_INTEGER, v.currentTime + delta));
    v.currentTime = next;
    setPlayTime(next);
    if (subtitleGate === "preparing" || subtitleGate === "ready") {
      setBufferOrigin(next);
      // Seeking resets readiness until cues cover the new window
      setSubtitleGate("preparing");
    }
    const jid = jobIdRef.current;
    if (jid) void apiSend(`/jobs/${jid}/playhead`, "POST", { t: next }).catch(() => undefined);
  }

  async function pauseJob() {
    if (!jobId) return;
    await apiSend(`/jobs/${jobId}/pause`, "POST");
    setStatus("paused");
  }

  async function resumeJob() {
    if (!jobId) return;
    await apiSend(`/jobs/${jobId}/resume`, "POST");
    setStatus("running");
  }

  async function exportAll() {
    if (!jobId) return;
    setBusy(true);
    try {
      const res = await apiSend<{ paths: Record<string, string> }>(`/jobs/${jobId}/export`, "POST", {
        bilingual_srt: true,
        vtt: true,
        ass: true,
      });
      setBatchNote(`Exported: ${Object.keys(res.paths).join(", ")}`);
      if (res.paths.en_srt) setSrtPath(res.paths.en_srt);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  async function saveGlossaryTerm() {
    if (!videoPath || !gZh.trim() || !gEn.trim()) return;
    const g = await apiSend<GlossaryData>("/glossary/term", "POST", {
      video_path: videoPath,
      zh: gZh.trim(),
      en: gEn.trim(),
    });
    setGlossary(g);
    setGlossaryCount(Object.keys(g.terms).length);
    setGZh("");
    setGEn("");
  }

  async function deleteGlossaryTerm(zh: string) {
    if (!videoPath) return;
    const g = await apiSend<GlossaryData>("/glossary/term", "DELETE", {
      video_path: videoPath,
      zh,
    });
    setGlossary(g);
    setGlossaryCount(Object.keys(g.terms).length);
  }

  async function saveEditCue() {
    if (!jobId || !editCue) return;
    const res = await apiSend<{ cue: Cue }>(`/jobs/${jobId}/cues`, "PATCH", editCue);
    const cue = res.cue;
    setCues((prev) => {
      const next = prev.filter((c) => Math.abs(c.start - cue.start) > 0.05);
      next.push(cue);
      next.sort((a, b) => a.start - b.start);
      return next;
    });
    setEditCue(null);
  }

  async function ensureModel(id: string) {
    setBusy(true);
    try {
      await apiSend("/models/ensure", "POST", { model_id: id });
      await refreshMeta();
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  async function resumeRecovery(snap: Record<string, unknown>) {
    setBusy(true);
    try {
      const job = await apiSend<{
        id: string;
        video_path: string;
        srt_path: string;
        status: JobStatus;
        duration: number;
        playhead?: number;
      }>("/recovery/resume", "POST", {
        snapshot_path: snap._snapshot_path,
        engine_id: engineId,
      });
      setVideoPath(job.video_path);
      await loadPlayableVideo(job.video_path);
      setJobId(job.id);
      setSrtPath(job.srt_path);
      setStatus(job.status);
      setDuration(job.duration || 0);
      const anchor = Number(snap.playhead ?? job.playhead ?? 0) || 0;
      setBufferOrigin(anchor);
      setPlayTime(anchor);
      setSubtitleGate("preparing");
      const prior = Array.isArray(snap.cues) ? (snap.cues as Cue[]) : [];
      if (prior.length) {
        setCues(prior);
        setCueCount(prior.length);
      }
      const v = videoRef.current;
      if (v) {
        v.pause();
        v.currentTime = anchor;
        setPlaying(false);
      }
      subscribe(job.id);
      await loadGlossary(job.video_path);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  const pct = Math.round(progress * 100);
  const quality = useMemo(() => computeQualitySummary(cues), [cues]);
  const stages = useMemo(
    () =>
      processingStages(status, {
        note: processNote,
        audioVerify,
        audioDone: Boolean(audioSummary && audioSummary.ok !== undefined),
        progress,
      }),
    [status, processNote, audioVerify, audioSummary, progress]
  );

  return (
    <div className="app workspace">
      <header className="topbar">
        <div className="brand">
          SubParty
          <span>subtitle studio</span>
        </div>
        <div className="toolbar-groups">
          <div className="tool-group" data-label="Project">
            <button type="button" className="secondary" onClick={() => void pickVideo()}>
              Open
            </button>
            <button type="button" className="secondary" onClick={() => void pickBatch()} disabled={busy}>
              Batch
            </button>
            <button type="button" className="secondary" onClick={() => void openFolder()} disabled={!videoPath}>
              Folder
            </button>
          </div>
          <div className="tool-group" data-label="Language">
            <select
              value={engineId}
              onChange={(e) => setEngineId(e.target.value)}
              title="Translation engine"
            >
              {(engines.length ? engines : [{ id: "argos", name: "Argos", available: true }]).map((e) => (
                <option key={e.id} value={e.id} disabled={e.available === false}>
                  {e.name}
                  {e.available === false ? " (unavailable)" : ""}
                </option>
              ))}
            </select>
            <button type="button" className="secondary" onClick={() => setBilingual((v) => !v)}>
              {bilingual ? "ZH+EN" : "EN"}
            </button>
          </div>
          <div className="tool-group" data-label="Mode">
            <select
              value={watchMode}
              onChange={(e) => {
                const next = e.target.value as WatchMode;
                setWatchMode(next);
                if (next === "live") setAudioVerify(false);
              }}
              title="Live = keep-up while watching. Quality = offline heavy pass + optional ASR verify."
              disabled={busy || status === "running" || status === "paused" || status === "queued"}
            >
              <option value="live">Live (watch)</option>
              <option value="quality">Quality (offline)</option>
            </select>
            <label
              className="audio-toggle"
              title="EOF Chinese ASR vs OCR — Quality only; never on Live path"
            >
              <input
                type="checkbox"
                checked={audioVerify}
                onChange={(e) => setAudioVerify(e.target.checked)}
                disabled={
                  watchMode === "live" ||
                  busy ||
                  status === "running" ||
                  status === "paused" ||
                  status === "queued"
                }
              />
              Verify
              {audioReady?.ready === false ? " (install)" : ""}
            </label>
          </div>
          <div className="tool-group" data-label="Run">
            <button
              type="button"
              onClick={() => void startProcess()}
              disabled={!videoPath || busy || status === "running" || engineOk === false}
            >
              {status === "running" ? (watchMode === "live" ? "Live…" : "Quality…") : "Process"}
            </button>
            {status === "running" ? (
              <button type="button" className="secondary" onClick={() => void pauseJob()}>
                Pause
              </button>
            ) : null}
            {status === "paused" ? (
              <button type="button" onClick={() => void resumeJob()}>
                Resume
              </button>
            ) : null}
            {jobId || status === "running" || status === "paused" || status === "queued" ? (
              <button
                type="button"
                className="danger"
                onClick={() => void cancelOrRemoveJob()}
                disabled={busy}
              >
                {status === "running" || status === "paused" || status === "queued" ? "Cancel" : "Remove"}
              </button>
            ) : null}
          </div>
          <div className="tool-group" data-label="Export">
            <button type="button" className="secondary" onClick={() => void exportAll()} disabled={!jobId}>
              Export
            </button>
          </div>
          <div className="tool-group" data-label="Settings">
            <button
              type="button"
              className="secondary"
              onClick={() => void retryEngine()}
              disabled={busy}
              title="Restart Python engine sidecar"
            >
              Engine
            </button>
            <button type="button" className="secondary" onClick={() => setPanel("models")}>
              Models
            </button>
          </div>
        </div>
      </header>

      {setup && setup.first_run && !setupDismissed ? (
        <div className="setup-banner" role="status">
          <div>
            <strong>First-run setup</strong>
            <span>{setup.next_action || "Ensure required models before Process."}</span>
            <ul>
              {(setup.steps || [])
                .filter((s) => s.required && !s.ok)
                .slice(0, 4)
                .map((s) => (
                  <li key={s.id}>{s.label}</li>
                ))}
            </ul>
          </div>
          <div className="setup-actions">
            <button type="button" onClick={() => setPanel("models")}>
              Open Models
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => {
                setSetupDismissed(true);
                try {
                  sessionStorage.setItem("subparty.setup.dismissed", "1");
                } catch {
                  /* ignore */
                }
              }}
            >
              Dismiss
            </button>
          </div>
        </div>
      ) : null}

      <div className="mode-banner" aria-live="polite">
        <strong>{watchMode === "live" ? "Live" : "Quality"}</strong>
        <span>
          {watchMode === "live"
            ? setup?.live_vs_quality?.live?.summary ||
              "Keep-up watch: Argos, provisional cues, playhead-ahead OCR. Audio verify stays off."
            : setup?.live_vs_quality?.quality?.summary ||
              "Offline heavy pass: fine timing, optional ASR verify, deeper Ready buffer."}
        </span>
        {watchMode === "quality" && audioVerify ? (
          <em>ASR verify on · EOF pass after OCR</em>
        ) : null}
      </div>

      <div className="stage-strip" aria-label="Processing stages">
        {stages.map((s) => (
          <div key={s.id} className={`stage${s.active ? " active" : ""}${s.done ? " done" : ""}`}>
            {s.label}
          </div>
        ))}
        <div className="stage-progress">
          {status === "idle" ? "Idle" : `${status}${engineOk ? "" : " · engine offline"}`}
          {status === "running" || status === "paused" ? ` · ${pct}%` : ""}
          {processNote ? ` · ${processNote}` : ""}
        </div>
      </div>

      <div className="quality-strip" aria-label="Quality summary from cue fields">
        <span>{quality.cueCount} cues</span>
        <span>{quality.uncertain} uncertain</span>
        <span>{quality.missingEn} missing EN</span>
        <span>{quality.overlaps} overlaps</span>
        <span>{quality.audioDisagreements} audio disagree</span>
        <span>{reviewQueue.length} review</span>
        <span>{quality.provisional} provisional</span>
        {Object.entries(quality.maturity).map(([k, v]) => (
          <span key={k}>
            {k}:{v}
          </span>
        ))}
      </div>

      <div className="main workspace-main">
        <div className="workspace-center">
        <section className="player-wrap">
          {videoUrl ? (
            <>
              <video
                ref={videoRef}
                src={videoUrl}
                onTimeUpdate={(e) => setPlayTime(e.currentTarget.currentTime)}
                onVolumeChange={(e) => {
                  setVolume(e.currentTarget.volume);
                  setMuted(e.currentTarget.muted);
                }}
                onPlay={(e) => {
                  if (subtitleGateRef.current === "preparing") {
                    e.currentTarget.pause();
                    setPlaying(false);
                    return;
                  }
                  setPlaying(true);
                }}
                onPause={() => setPlaying(false)}
                onLoadedMetadata={(e) => {
                  setDuration(e.currentTarget.duration || 0);
                  e.currentTarget.volume = volume;
                  e.currentTarget.muted = muted;
                }}
              />
              {subtitleGate === "preparing" || (subtitleGate === "ready" && !playing) ? (
                <div
                  className={`subtitle-gate ${subtitleGate === "ready" ? "ready" : "preparing"}`}
                  aria-live="polite"
                >
                  {subtitleGate === "preparing" ? (
                    <>
                      <strong>Preparing subtitles…</strong>
                      <span>
                        Buffering near playhead · {bufferStatus.cueCountInWindow} cues ·{" "}
                        {bufferStatus.coveredAhead.toFixed(1)}s /{" "}
                        {watchMode === "live"
                          ? LIVE_BUFFER_AHEAD_TARGET_S
                          : QUALITY_BUFFER_AHEAD_TARGET_S}
                        s ahead
                      </span>
                    </>
                  ) : (
                    <>
                      <strong>Ready — Play</strong>
                      <span>Subtitles buffered · press Play when you want to watch</span>
                    </>
                  )}
                </div>
              ) : null}
              <div
                className={`cue-overlay${showOverlay ? " visible" : ""}${
                  activeCue?.uncertain ? " uncertain" : ""
                }${activeCue?.provisional ? " provisional" : ""}`}
              >
                {activeCue?.provisional ? <div className="cue-flag">provisional</div> : null}
                {bilingual && overlayZh ? <div className="cue-zh">{overlayZh}</div> : null}
                {overlayEn ? (
                  <div className="cue-en">{overlayEn}</div>
                ) : bilingual ? null : overlayZh ? (
                  <div className="cue-en">{overlayZh}</div>
                ) : null}
              </div>
              <div className="transport">
                <button type="button" className="secondary" onClick={() => seekBy(-10)}>
                  −10s
                </button>
                <button
                  type="button"
                  onClick={togglePlay}
                  disabled={subtitleGate === "preparing" && !playing}
                  title={
                    subtitleGate === "preparing"
                      ? "Wait until subtitles are buffered"
                      : undefined
                  }
                >
                  {playing ? "Pause" : subtitleGate === "ready" ? "Play" : "Play"}
                </button>
                <button type="button" className="secondary" onClick={() => seekBy(10)}>
                  +10s
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setMuted((m) => !m)}
                  title={muted ? "Unmute" : "Mute"}
                  aria-pressed={muted}
                >
                  {muted || volume === 0 ? "Unmute" : "Mute"}
                </button>
                <label className="volume-control" title="Volume">
                  <span className="volume-pct">{Math.round((muted ? 0 : volume) * 100)}%</span>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={Math.round((muted ? 0 : volume) * 100)}
                    onChange={(e) => {
                      const next = Number(e.target.value) / 100;
                      setVolume(next);
                      if (next > 0 && muted) setMuted(false);
                      if (next === 0) setMuted(true);
                    }}
                  />
                </label>
                <span className="clock">
                  {formatTime(playTime)}
                  {duration > 0 ? ` / ${formatTime(duration)}` : ""}
                </span>
                {audioInfo ? <span className="gate-hint" title={audioInfo}>{audioInfo}</span> : null}
                {subtitleGate === "preparing" ? (
                  <span className="gate-hint">Preparing subtitles…</span>
                ) : subtitleGate === "ready" && !playing ? (
                  <span className="gate-hint">Ready — Play</span>
                ) : null}
              </div>
            </>
          ) : (
            <div className="empty">Pick a local MP4 / MKV / TS with burned-in Simplified Chinese subs.</div>
          )}
        </section>

        <TimelineRail
          cues={cues}
          duration={duration}
          playTime={playTime}
          selectedCueId={editCue?.cue_id}
          selectedStart={editCue?.start}
          videoUrl={videoUrl}
          onSeek={(t) => {
            const v = videoRef.current;
            if (v) {
              v.currentTime = t;
              setPlayTime(t);
            }
          }}
          onSelectCue={(c) => {
            setEditCue({ ...c });
            setPanel("inspect");
          }}
        />
        </div>

        <aside className="side">
          <div className="tabs">
            {(["inspect", "job", "qa", "glossary", "batch", "models"] as const).map((p) => (
              <button
                key={p}
                type="button"
                className={panel === p ? "tab active" : "tab"}
                onClick={() => {
                  setPanel(p);
                  if (p === "qa") void refreshQa();
                  if (p === "glossary") {
                    void refreshNameSuggestions();
                    void loadCharacters();
                  }
                  if (p === "batch") void refreshBatchStatus();
                }}
              >
                {p}
              </button>
            ))}
          </div>

          {panel === "inspect" ? (
            <CueInspector
              cue={editCue}
              bilingual={bilingual}
              busy={busy}
              onChange={setEditCue}
              onSave={() => void saveEditCue()}
              onCancel={() => setEditCue(null)}
              onSplit={() => void splitEditCue()}
              onMarkMerge={() => editCue && setMergeStart(editCue.start)}
              onJump={() => {
                if (!editCue || !videoRef.current) return;
                videoRef.current.currentTime = editCue.start;
                setPlayTime(editCue.start);
              }}
            />
          ) : null}

          {panel === "job" ? (
            <>
              <div
                className={`status-banner${
                  error || engineOk === false ? " error" : status === "running" ? " live" : ""
                }`}
              >
                <strong>
                  {engineOk === false
                    ? "Engine offline"
                    : status === "idle"
                      ? "Idle"
                      : status}
                  {engineOk && cueCount ? ` · ${cueCount} cues` : ""}
                  {engineOk && (status === "running" || status === "paused") ? ` · ${pct}%` : ""}
                </strong>
                <span>
                  {error
                    ? error
                    : engineOk === false
                      ? "Sidecar not reachable. Click Retry engine (or restart npm run dev)."
                      : subtitleGate === "preparing"
                        ? `Preparing subtitles… ${bufferStatus.cueCountInWindow} cues, ${bufferStatus.coveredAhead.toFixed(1)}s ahead of playhead.`
                        : subtitleGate === "ready" && !playing
                          ? "Ready — Play when you want. OCR keeps refining in the background."
                          : status === "running"
                            ? "Live OCR — provisional cues refine as context fills."
                            : status === "paused"
                              ? "Paused at playhead."
                              : status === "done"
                                ? "Done. Use Export for bilingual SRT / VTT / ASS — or Remove job to clear."
                                : status === "cancelled"
                                  ? "Job cancelled. Process again when ready."
                                  : status === "error"
                                    ? "Fix the issue above, then Process again — or Remove job to clear."
                                    : "Pick a video, then Process. Playback waits until subtitles are buffered."}
                </span>
              </div>

              <div className="bar">
                <i style={{ width: `${pct}%` }} />
              </div>

              <div className="meta compact">
                <div>
                  <span>Engine</span>
                  <strong>
                    {engineOk === null ? "…" : engineOk ? "ready" : "offline"}
                    {engineVersion ? ` ${engineVersion}` : ""}
                    {apiToken() ? " · secured" : ""}
                  </strong>
                </div>
                <div>
                  <span>Translator</span>
                  <strong>{engineId}</strong>
                </div>
                <div className="path">
                  <span>Video</span>
                  <strong title={videoPath || undefined}>
                    {videoPath ? videoPath.split(/[/\\]/).pop() : "—"}
                  </strong>
                </div>
                <div className="path">
                  <span>SRT</span>
                  <strong title={srtPath || undefined}>
                    {srtPath ? srtPath.split(/[/\\]/).pop() : "—"}
                  </strong>
                </div>
                {(status === "running" || status === "paused") && (
                  <div className="live-stats">
                    {liveLag != null ? <span>lag {liveLag.toFixed(1)}s</span> : null}
                    {bufferAhead != null ? <span>buf {bufferAhead.toFixed(1)}s</span> : null}
                    {sampleInterval != null ? <span>every {sampleInterval.toFixed(1)}s</span> : null}
                    {avgSlotMs != null ? <span>{avgSlotMs} ms</span> : null}
                  </div>
                )}
                {seriesId ? (
                  <div>
                    <span>Series</span>
                    <strong>
                      {seriesId}
                      {glossaryCount != null ? ` · ${glossaryCount} terms` : ""}
                      {regionLabel ? ` · ${regionLabel}` : ""}
                    </strong>
                  </div>
                ) : null}
                {batchNote ? (
                  <div>
                    <span>Note</span>
                    <strong>{batchNote}</strong>
                  </div>
                ) : null}
                {jobId ? (
                  <div>
                    <span>Job</span>
                    <strong>{jobId.slice(0, 8)}</strong>
                  </div>
                ) : null}
                <div>
                  <span>Mode</span>
                  <strong>{watchMode === "live" ? "Live watch" : "Quality offline"}</strong>
                </div>
                <div>
                  <span>Crop</span>
                  <strong>
                    {regionLabel || "—"}
                    {regionConfirmable && !regionConfirmable.confirmed && videoPath ? (
                      <>
                        {" "}
                        <button
                          type="button"
                          className="linkish"
                          disabled={regionBusy}
                          onClick={() => void confirmRegionCrop()}
                        >
                          Confirm
                        </button>
                      </>
                    ) : null}
                    {regionConfirmable?.confirmed ? (
                      <>
                        {" "}
                        <button
                          type="button"
                          className="linkish"
                          disabled={regionBusy}
                          onClick={() => void clearRegionCrop()}
                        >
                          Clear
                        </button>
                      </>
                    ) : null}
                  </strong>
                </div>
                <div>
                  <span>Audio</span>
                  <strong>
                    {audioVerify
                      ? audioSummary
                        ? audioSummary.ok === false
                          ? `error${audioSummary.error ? `: ${audioSummary.error}` : ""}`
                          : `${audioSummary.disagreements ?? 0} disagree · ${audioSummary.segments ?? "?"} segs`
                        : status === "running" || status === "paused" || status === "queued"
                          ? "queued (EOF pass)"
                          : "on"
                      : audioReady?.ready
                        ? "off (ASR ready)"
                        : "off"}
                  </strong>
                </div>
              </div>

              {recovery.length ? (
                <>
                  <h2>
                    Unfinished jobs
                    <button type="button" className="linkish" onClick={() => setRecoveryOpen((v) => !v)}>
                      {recovery.length} · {recoveryOpen ? "hide" : "show"}
                    </button>
                    <button
                      type="button"
                      className="linkish"
                      onClick={() => void discardAllRecovery()}
                      disabled={busy}
                      title="Discard all recovery snapshots"
                    >
                      Discard all
                    </button>
                  </h2>
                  {recoveryOpen ? (
                    <ul className="cue-list">
                      {recovery.slice(0, 8).map((r) => (
                        <li key={String(r.id)}>
                          <div className="t">
                            {String(r.status)} · {String(r.id).slice(0, 8)}
                          </div>
                          <div className="en">{String(r.video_path)}</div>
                          <button type="button" className="linkish" onClick={() => void resumeRecovery(r)}>
                            Resume
                          </button>
                          <button
                            type="button"
                            className="linkish"
                            onClick={() => void discardRecovery(r)}
                            disabled={busy}
                            style={{ marginLeft: "0.75rem", color: "var(--danger)" }}
                          >
                            Discard
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </>
              ) : null}

              <h2>
                Review queue
                <button type="button" className="linkish" onClick={() => setReviewOpen((v) => !v)}>
                  {reviewQueue.length} · {reviewOpen ? "hide" : "show"}
                </button>
              </h2>
              {reviewOpen ? (
                reviewQueue.length === 0 ? (
                  <div className="empty" style={{ padding: "0.35rem 0" }}>
                    No uncertain, audio-disagree, inferred, or missing-EN cues.
                  </div>
                ) : (
                  <ul className="cue-list review">
                    {reviewQueue.slice(0, 40).map(({ cue: c, reasons }) => (
                      <li
                        key={`rq-${c.cue_id || c.start}-${c.index}`}
                        className={
                          reasons.includes("audio")
                            ? "audio-disagree-item"
                            : reasons.includes("uncertain")
                              ? "uncertain-item"
                              : undefined
                        }
                      >
                        <div className="t">
                          #{c.index} · {formatTime(c.start)}–{formatTime(c.end)} ·{" "}
                          {reasons.join(" · ")}
                          {typeof c.confidence === "number"
                            ? ` · ${Math.round(c.confidence * 100)}%`
                            : ""}
                          <button
                            type="button"
                            className="linkish"
                            onClick={() => {
                              const v = videoRef.current;
                              if (!v) return;
                              v.currentTime = Math.max(0, c.start);
                              setPlayTime(c.start);
                              setEditCue({ ...c });
                              setPanel("inspect");
                            }}
                          >
                            Inspect
                          </button>
                          <button
                            type="button"
                            className="linkish"
                            onClick={() => {
                              const v = videoRef.current;
                              if (!v) return;
                              v.currentTime = Math.max(0, c.start);
                              setPlayTime(c.start);
                            }}
                          >
                            Jump
                          </button>
                        </div>
                        <div className="en">{c.text_en || "—"}</div>
                        <div className="zh">OCR: {c.original_ocr || c.text_zh}</div>
                        {c.audio_transcript ? (
                          <div className="en">ASR: {c.audio_transcript}</div>
                        ) : null}
                        {c.reconciled_zh && c.reconciled_zh !== (c.original_ocr || c.text_zh) ? (
                          <div className="t">Reconciled: {c.reconciled_zh}</div>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )
              ) : null}
            </>
          ) : null}

          {panel === "glossary" ? (
            <>
              <h2>Series glossary</h2>
              <div className="meta">
                <div>
                  Series: <strong>{glossary?.series_id || seriesId || "—"}</strong>
                </div>
                <div>
                  File: <strong>{glossary?.path || "—"}</strong>
                  <span style={{ opacity: 0.7 }}> · app data (not beside video)</span>
                </div>
              </div>
              <div className="form-row">
                <input value={gZh} onChange={(e) => setGZh(e.target.value)} placeholder="ZH term / name" />
                <input value={gEn} onChange={(e) => setGEn(e.target.value)} placeholder="EN rendering" />
                <button type="button" onClick={() => void saveGlossaryTerm()} disabled={!videoPath}>
                  Add / edit
                </button>
                <button type="button" className="secondary" onClick={() => void refreshNameSuggestions()}>
                  Suggest names
                </button>
              </div>
              {nameSuggestions.length ? (
                <>
                  <h2>Character suggestions</h2>
                  <ul className="cue-list review">
                    {nameSuggestions.slice(0, 16).map((s) => (
                      <li key={s.zh}>
                        <div className="en">
                          {s.zh} · seen {s.count}×
                        </div>
                        <button
                          type="button"
                          className="linkish"
                          onClick={() => {
                            setGZh(s.zh);
                            setGEn(s.en || "");
                          }}
                        >
                          Use
                        </button>
                        <button
                          type="button"
                          className="linkish"
                          onClick={() => void saveCharacterFromSuggestion(s.zh, s.en || "")}
                        >
                          Sheet
                        </button>
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
              {characters.length ? (
                <>
                  <h2>Character sheet</h2>
                  <ul className="cue-list">
                    {characters.map((c) => (
                      <li key={c.zh}>
                        <div className="en">
                          {c.zh} → {c.en || "—"}
                          {c.role ? ` · ${c.role}` : ""}
                        </div>
                        {c.notes ? <div className="zh">{c.notes}</div> : null}
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
              {seriesList.length ? (
                <>
                  <h2>All series memory</h2>
                  <ul className="cue-list">
                    {seriesList.slice(0, 20).map((s) => (
                      <li key={s.series_id}>
                        <div className="en">
                          {s.series_id} · {s.term_count} terms
                        </div>
                        <div className="t">{s.path}</div>
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
              <ul className="cue-list">
                {Object.entries(glossary?.terms || {}).map(([zh, en]) => (
                  <li key={zh}>
                    <div className="en">
                      {zh} → {en}
                    </div>
                    <button type="button" className="linkish" onClick={() => void deleteGlossaryTerm(zh)}>
                      Delete
                    </button>
                  </li>
                ))}
              </ul>
            </>
          ) : null}

          {panel === "qa" ? (
            <>
              <h2>
                Quality report
                <button type="button" className="linkish" onClick={() => void refreshQa()} disabled={!jobId || busy}>
                  Refresh
                </button>
                <button type="button" className="linkish" onClick={() => void pickGoldJson()} disabled={!jobId || busy}>
                  Gold JSON
                </button>
                <button
                  type="button"
                  className="linkish"
                  onClick={() => void refreshQa({ exportHtml: true })}
                  disabled={!jobId || busy}
                >
                  Export HTML
                </button>
              </h2>
              {goldPath ? (
                <div className="t" style={{ marginBottom: "0.5rem", opacity: 0.85 }}>
                  Gold: {goldPath.split(/[/\\]/).pop()}
                </div>
              ) : null}
              {qaReportPath ? (
                <div className="t" style={{ marginBottom: "0.5rem" }}>
                  Report:{" "}
                  <button type="button" className="linkish" onClick={() => void window.subparty.openFolder(qaReportPath)}>
                    {qaReportPath.split(/[/\\]/).pop()}
                  </button>
                </div>
              ) : null}
              {!jobId ? (
                <div className="empty" style={{ padding: "0.5rem 0" }}>
                  Process a job to score CER / timing / multimodal QA.
                </div>
              ) : qaReport ? (
                <>
                  <div className="meta compact">
                    <div>
                      <span>Cues</span>
                      <strong>{String(qaReport.cue_count ?? "—")}</strong>
                    </div>
                    <div>
                      <span>Uncertain</span>
                      <strong>{String(qaReport.uncertain_count ?? "—")}</strong>
                    </div>
                    <div>
                      <span>OCR CER</span>
                      <strong>
                        {(() => {
                          const m = qaReport.metrics as { ocr_cer?: { mean_cer?: number } } | undefined;
                          const v = m?.ocr_cer?.mean_cer;
                          return typeof v === "number" ? v.toFixed(3) : "—";
                        })()}
                      </strong>
                    </div>
                    <div>
                      <span>Start err</span>
                      <strong>
                        {(() => {
                          const m = qaReport.metrics as { timing?: { mean_start_err_ms?: number } } | undefined;
                          const v = m?.timing?.mean_start_err_ms;
                          return typeof v === "number" ? `${Math.round(v)} ms` : "—";
                        })()}
                      </strong>
                    </div>
                    <div>
                      <span>RTF</span>
                      <strong>
                        {(() => {
                          const m = qaReport.metrics as { rtf?: number } | undefined;
                          return typeof m?.rtf === "number" ? m.rtf.toFixed(3) : "—";
                        })()}
                      </strong>
                    </div>
                    <div>
                      <span>Audio disagree</span>
                      <strong>
                        {(() => {
                          const mm = qaReport.multimodal as { disagreements?: number } | undefined;
                          return String(mm?.disagreements ?? "—");
                        })()}
                      </strong>
                    </div>
                  </div>
                  <h2>Review queue</h2>
                  <ul className="cue-list review">
                    {((qaReport.review_queue as Array<Record<string, unknown>>) || []).map((c, i) => (
                      <li
                        key={`q-${String(c.start)}-${i}`}
                        className={c.audio_disagreement ? "audio-disagree-item" : "uncertain-item"}
                      >
                        <div className="t">
                          #{String(c.index ?? i)} · {formatTime(Number(c.start || 0))}–{formatTime(Number(c.end || 0))}
                          {c.maturity ? ` · ${String(c.maturity)}` : ""}
                          <button
                            type="button"
                            className="linkish"
                            onClick={() => {
                              const v = videoRef.current;
                              if (!v) return;
                              v.currentTime = Number(c.start || 0);
                              setPlayTime(Number(c.start || 0));
                              setPanel("timeline");
                              const match = cues.find(
                                (x) => Math.abs(x.start - Number(c.start || 0)) < 0.08
                              );
                              if (match) setEditCue({ ...match });
                            }}
                          >
                            Edit
                          </button>
                        </div>
                        <div className="en">{String(c.text_en || "")}</div>
                        <div className="zh">{String(c.text_zh || "")}</div>
                        {c.audio_transcript ? <div className="t">ASR: {String(c.audio_transcript)}</div> : null}
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <div className="empty" style={{ padding: "0.5rem 0" }}>
                  Click Refresh to build the QA report.
                </div>
              )}
            </>
          ) : null}

          {panel === "batch" ? (
            <>
              <h2>
                Batch production
                <button
                  type="button"
                  className="linkish"
                  onClick={() => void refreshBatchStatus()}
                  disabled={!batchId || busy}
                >
                  Refresh
                </button>
                <button type="button" className="linkish" onClick={() => void retryFailedBatch()} disabled={!batchId || busy}>
                  Retry failed
                </button>
                <button type="button" className="linkish" onClick={() => void exportBatchAll()} disabled={!batchId || busy}>
                  Export all
                </button>
              </h2>
              {!batchId ? (
                <div className="empty" style={{ padding: "0.5rem 0" }}>
                  Use Batch in the top bar to queue multiple videos.
                </div>
              ) : (
                <>
                  <div className="meta compact">
                    <div>
                      <span>Batch</span>
                      <strong>{batchId.slice(0, 8)}</strong>
                    </div>
                    <div>
                      <span>Jobs</span>
                      <strong>{batchStatus?.total ?? "…"}</strong>
                    </div>
                    <div>
                      <span>Progress</span>
                      <strong>
                        {batchStatus ? `${Math.round((batchStatus.progress || 0) * 100)}%` : "…"}
                        {batchStatus?.complete ? " · complete" : ""}
                      </strong>
                    </div>
                  </div>
                  {batchStatus?.by_status ? (
                    <div className="t" style={{ marginBottom: "0.5rem", opacity: 0.85 }}>
                      {Object.entries(batchStatus.by_status)
                        .map(([k, v]) => `${k}: ${v}`)
                        .join(" · ")}
                    </div>
                  ) : null}
                  <ul className="cue-list">
                    {(batchStatus?.jobs || []).map((j) => (
                      <li key={String(j.id)}>
                        <div className="t">
                          {String(j.status)} · {String(j.id).slice(0, 8)}
                          {typeof j.cue_count === "number" ? ` · ${j.cue_count} cues` : ""}
                        </div>
                        <div className="en">{String(j.video_path || "")}</div>
                        {j.error ? <div className="zh">{String(j.error)}</div> : null}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          ) : null}

          {panel === "timeline" ? (
            <>
              <h2>Production editor</h2>
              {editCue ? (
                <div className="editor">
                  <div className="t" style={{ marginBottom: "0.35rem" }}>
                    Cue #{editCue.index}
                    {editCue.maturity ? ` · ${editCue.maturity}` : ""}
                    {editCue.audio_disagreement ? " · audio disagree" : ""}
                    <button
                      type="button"
                      className="linkish"
                      onClick={() => {
                        const idx = cues.findIndex(
                          (c) =>
                            (editCue.cue_id && c.cue_id === editCue.cue_id) ||
                            Math.abs(c.start - editCue.start) < 0.05
                        );
                        if (idx > 0) setEditCue({ ...cues[idx - 1] });
                      }}
                    >
                      Prev
                    </button>
                    <button
                      type="button"
                      className="linkish"
                      onClick={() => {
                        const idx = cues.findIndex(
                          (c) =>
                            (editCue.cue_id && c.cue_id === editCue.cue_id) ||
                            Math.abs(c.start - editCue.start) < 0.05
                        );
                        if (idx >= 0 && idx < cues.length - 1) setEditCue({ ...cues[idx + 1] });
                      }}
                    >
                      Next
                    </button>
                  </div>
                  <label>
                    Start
                    <input
                      type="number"
                      step="0.05"
                      value={editCue.start}
                      onChange={(e) => setEditCue({ ...editCue, start: Number(e.target.value) })}
                    />
                  </label>
                  <label>
                    End
                    <input
                      type="number"
                      step="0.05"
                      value={editCue.end}
                      onChange={(e) => setEditCue({ ...editCue, end: Number(e.target.value) })}
                    />
                  </label>
                  <label>
                    ZH (display)
                    <input
                      value={editCue.text_zh}
                      onChange={(e) => setEditCue({ ...editCue, text_zh: e.target.value })}
                    />
                  </label>
                  <label>
                    EN
                    <input
                      value={editCue.text_en}
                      onChange={(e) => setEditCue({ ...editCue, text_en: e.target.value })}
                    />
                  </label>
                  {editCue.original_ocr ? (
                    <div className="zh">OCR: {editCue.original_ocr}</div>
                  ) : null}
                  {editCue.audio_transcript ? (
                    <div className="t">ASR: {editCue.audio_transcript}</div>
                  ) : null}
                  {editCue.reconciled_zh ? (
                    <div className="t">Reconciled: {editCue.reconciled_zh}</div>
                  ) : null}
                  <div className="form-row">
                    <button type="button" onClick={() => void saveEditCue()}>
                      Save cue
                    </button>
                    <button type="button" className="secondary" onClick={() => void splitEditCue()} disabled={busy}>
                      Split
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => setMergeStart(editCue.start)}
                      disabled={busy}
                      title="Then click Merge on another cue"
                    >
                      Mark merge
                    </button>
                    <button type="button" className="secondary" onClick={() => setEditCue(null)}>
                      Cancel
                    </button>
                  </div>
                  {mergeStart != null ? (
                    <div className="t" style={{ marginTop: "0.35rem" }}>
                      Merge marked @ {formatTime(mergeStart)} — pick another cue’s Merge.
                    </div>
                  ) : null}
                </div>
              ) : null}
              <ul className="cue-list">
                {cues.map((c) => (
                  <li
                    key={`t-${c.start}-${c.index}`}
                    className={
                      c.audio_disagreement
                        ? "audio-disagree-item"
                        : c.uncertain
                          ? "uncertain-item"
                          : undefined
                    }
                  >
                    <div className="t">
                      #{c.index} · {formatTime(c.start)}–{formatTime(c.end)}
                      {c.maturity ? ` · ${c.maturity}` : ""}
                      {typeof c.ocr_confidence === "number"
                        ? ` · OCR ${Math.round(c.ocr_confidence * 100)}%`
                        : typeof c.confidence === "number"
                          ? ` · ${Math.round(c.confidence * 100)}%`
                          : ""}
                      {typeof c.timing_confidence === "number"
                        ? ` · T ${Math.round(c.timing_confidence * 100)}%`
                        : ""}
                      {c.provisional ? " · provisional" : ""}
                      {c.revision_count && c.revision_count > 1 ? ` · r${c.revision_count}` : ""}
                      <button type="button" className="linkish" onClick={() => setEditCue({ ...c })}>
                        Edit
                      </button>
                      <button
                        type="button"
                        className="linkish"
                        onClick={() => {
                          const v = videoRef.current;
                          if (!v) return;
                          v.currentTime = c.start;
                          setPlayTime(c.start);
                        }}
                      >
                        Jump
                      </button>
                      {mergeStart != null && Math.abs(mergeStart - c.start) > 0.05 ? (
                        <button
                          type="button"
                          className="linkish"
                          onClick={() => void mergeWithEditCue(c.start)}
                        >
                          Merge
                        </button>
                      ) : null}
                    </div>
                    <div className="en">{c.text_en}</div>
                    <div className="zh">{c.text_zh}</div>
                    {c.audio_transcript ? (
                      <div className="t">
                        ASR: {c.audio_transcript}
                        {c.audio_disagreement ? " · disagreement" : ""}
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            </>
          ) : null}

          {panel === "models" ? (
            <>
              <h2>Models · first-run</h2>
              <p className="hint" style={{ opacity: 0.75, fontSize: "0.85rem", marginBottom: "0.75rem" }}>
                Weights live under <code>data/models</code> (not beside videos).{" "}
                <strong>Required</strong> for Live: Argos + OCR. Optional: NLLB / Qwen (Quality MT),
                faster-whisper (Quality Verify). Only one of NLLB/Qwen stays loaded. SRT still saves next
                to the video.
              </p>
              {setup ? (
                <div className="setup-checklist">
                  <div className="en">
                    {setup.ready ? "Setup ready" : "Setup incomplete"} · {setup.next_action}
                  </div>
                  <ul className="cue-list review">
                    {(setup.steps || []).map((s) => (
                      <li key={s.id}>
                        <div className="t">
                          {s.ok ? "✓" : "○"} {s.label}
                          {s.required ? " · required" : " · optional"}
                        </div>
                        {s.hint ? <div className="zh">{s.hint}</div> : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {audioReady ? (
                <p className="hint" style={{ opacity: 0.8, fontSize: "0.82rem", marginBottom: "0.75rem" }}>
                  Audio verify: {audioReady.ready ? "package ready" : "not installed"}
                  {audioReady.note ? ` — ${audioReady.note}` : ""}
                </p>
              ) : null}
              <ul className="cue-list">
                {models.map((m) => (
                  <li key={m.id}>
                    <div className="en">
                      {m.name} {m.installed ? "· installed" : "· missing"}
                      {m.required || !m.optional ? " · required" : " · optional"}
                      {m.size_hint ? ` · ${m.size_hint}` : ""}
                    </div>
                    <div className="t">
                      {m.kind}
                      {m.engine && m.engine !== "ocr" ? ` · engine id: ${m.engine}` : ""}
                    </div>
                    {m.first_run || m.notes ? <div className="zh">{m.first_run || m.notes}</div> : null}
                    <button
                      type="button"
                      className="linkish"
                      onClick={() => void ensureModel(m.id)}
                      disabled={busy}
                    >
                      {m.installed ? "Re-check / warm" : "Download / ensure"}
                    </button>
                    {m.installed && m.engine && m.engine !== "ocr" && m.engine !== "audio" ? (
                      <button
                        type="button"
                        className="linkish"
                        onClick={() => setEngineId(m.engine || "argos")}
                        style={{ marginLeft: "0.75rem" }}
                      >
                        Use engine
                      </button>
                    ) : null}
                    {m.kind === "asr" && m.installed ? (
                      <button
                        type="button"
                        className="linkish"
                        onClick={() => setAudioVerify(true)}
                        style={{ marginLeft: "0.75rem" }}
                      >
                        Enable Audio verify
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </aside>
      </div>
    </div>
  );
}

