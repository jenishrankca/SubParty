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
} from "./api";

type JobStatus = "idle" | "queued" | "running" | "paused" | "done" | "error" | "cancelled";

interface ModelInfo {
  id: string;
  name: string;
  kind: string;
  installed: boolean;
  engine?: string;
  optional?: boolean;
  size_hint?: string;
  notes?: string;
  path?: string;
}

function formatTime(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

/** Seconds of cue coverage ahead of playhead before Play is unlocked. */
const BUFFER_AHEAD_TARGET_S = 12;
const BUFFER_MIN_CUES = 3;

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

function computeBufferStatus(cues: Cue[], t: number, jobDone: boolean): BufferStatus {
  const windowEnd = t + BUFFER_AHEAD_TARGET_S;
  const inWindow = cues.filter(
    (c) => cueHasText(c) && c.end > t - 0.25 && c.start < windowEnd + 0.5
  );
  let coveredAhead = 0;
  if (inWindow.length) {
    const farthest = inWindow.reduce((m, c) => Math.max(m, c.end), t);
    coveredAhead = Math.max(0, farthest - t);
  }
  const hasActiveOrNear = cues.some(
    (c) => cueHasText(c) && c.start <= t + 2.5 && c.end >= t - 0.15
  );
  const cueCountInWindow = inWindow.length;
  const ready =
    jobDone && cues.some(cueHasText)
      ? true
      : (coveredAhead >= BUFFER_AHEAD_TARGET_S * 0.7 && cueCountInWindow >= 2) ||
        (cueCountInWindow >= BUFFER_MIN_CUES && coveredAhead >= 6) ||
        (hasActiveOrNear && cueCountInWindow >= BUFFER_MIN_CUES && coveredAhead >= 4);
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
  const [glossaryCount, setGlossaryCount] = useState<number | null>(null);
  const [reviewOpen, setReviewOpen] = useState(true);
  const [engines, setEngines] = useState<EngineInfo[]>([]);
  const [engineId, setEngineId] = useState("argos");
  const [glossary, setGlossary] = useState<GlossaryData | null>(null);
  const [gZh, setGZh] = useState("");
  const [gEn, setGEn] = useState("");
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [editCue, setEditCue] = useState<Cue | null>(null);
  const [panel, setPanel] = useState<"job" | "glossary" | "timeline" | "models">("job");
  const [batchNote, setBatchNote] = useState<string | null>(null);
  const [recovery, setRecovery] = useState<Array<Record<string, unknown>>>([]);
  const [audioVerify, setAudioVerify] = useState(false);
  const [audioReady, setAudioReady] = useState<AudioVerifyStatus | null>(null);
  const [audioSummary, setAudioSummary] = useState<AudioSummary | null>(null);
  const [subtitleGate, setSubtitleGate] = useState<SubtitleGate>("idle");
  const [bufferOrigin, setBufferOrigin] = useState(0);
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
    () => computeBufferStatus(cues, bufferClock, status === "done"),
    [cues, bufferClock, status]
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

  const uncertainCues = useMemo(
    () => cues.filter((c) => c.uncertain || (typeof c.confidence === "number" && c.confidence < 0.78)),
    [cues]
  );

  const audioDisagreements = useMemo(
    () => cues.filter((c) => c.audio_disagreement),
    [cues]
  );

  async function refreshMeta() {
    try {
      const eng = await apiGet<{ engines: EngineInfo[] }>("/engines");
      setEngines(eng.engines || []);
      const mods = await apiGet<{ models: ModelInfo[] }>("/models");
      setModels(mods.models || []);
      const rec = await apiGet<{ jobs: Array<Record<string, unknown>> }>("/recovery");
      setRecovery(rec.jobs || []);
      const health = await apiGet<{ audio_verify?: AudioVerifyStatus }>("/health");
      if (health.audio_verify) setAudioReady(health.audio_verify);
    } catch {
      /* auth may not be ready yet */
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
    const url = await window.subparty.pathToFileUrl(picked);
    setVideoUrl(url);
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
      setBatchNote(`Batch ${res.batch_id.slice(0, 8)} · ${res.jobs.length} jobs`);
      const first = res.jobs.find((j) => j.id);
      if (first?.id) {
        setVideoPath(paths[0]);
        const url = await window.subparty.pathToFileUrl(paths[0]);
        setVideoUrl(url);
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
      await apiSendOptional("/engines/select", "POST", { engine_id: engineId });
      const job = await apiSend<{
        id: string;
        srt_path: string;
        status: JobStatus;
        duration: number;
      }>("/jobs", "POST", {
        video_path: videoPath,
        fps: 1.0,
        band_ratio: 0.2,
        lookahead: 12,
        playhead: startAt,
        engine_id: engineId,
        context_window_s: 120,
        high_accuracy: true,
        audio_verify: audioVerify,
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
          const region = data.region as { y?: number; h?: number } | undefined;
          if (region && typeof region.y === "number" && typeof region.h === "number") {
            setRegionLabel(`y ${(region.y * 100).toFixed(0)}–${((region.y + region.h) * 100).toFixed(0)}%`);
          }
        } else if (type === "progress") {
          setProgress(Number(data.progress ?? 0));
          if (typeof data.duration === "number") setDuration(data.duration);
          if (typeof data.avg_slot_ms === "number") setAvgSlotMs(data.avg_slot_ms);
          if (typeof data.live_lag === "number") setLiveLag(data.live_lag);
          if (typeof data.buffer_ahead === "number") setBufferAhead(data.buffer_ahead);
          if (typeof data.sample_interval === "number") setSampleInterval(data.sample_interval);
          if (typeof data.series_id === "string") setSeriesId(data.series_id);
          setStatus("running");
        } else if (type === "audio") {
          if (data.audio && typeof data.audio === "object") {
            setAudioSummary(data.audio as AudioSummary);
          }
        } else if (type === "status" || type === "done" || type === "error") {
          if (typeof data.status === "string") setStatus(data.status as JobStatus);
          if (typeof data.progress === "number") setProgress(data.progress);
          if (typeof data.cue_count === "number") setCueCount(data.cue_count);
          if (typeof data.error === "string") setError(data.error);
          if (typeof data.srt_path === "string") setSrtPath(data.srt_path);
          if (typeof data.series_id === "string") setSeriesId(data.series_id);
          if (data.audio && typeof data.audio === "object") {
            setAudioSummary(data.audio as AudioSummary);
          }
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
      const url = await window.subparty.pathToFileUrl(job.video_path);
      setVideoUrl(url);
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

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          SubParty
          <span>ZH hardsub → EN softsub</span>
        </div>
        <div className="actions">
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
          <button
            type="button"
            className="secondary"
            onClick={() => void retryEngine()}
            disabled={busy}
            title="Restart Python engine sidecar"
          >
            Retry engine
          </button>
          <button type="button" className="secondary" onClick={() => void pickVideo()}>
            Pick video
          </button>
          <button type="button" className="secondary" onClick={() => void pickBatch()} disabled={busy}>
            Batch
          </button>
          <label className="audio-toggle" title="EOF Chinese ASR vs OCR — never overwrites OCR">
            <input
              type="checkbox"
              checked={audioVerify}
              onChange={(e) => setAudioVerify(e.target.checked)}
              disabled={busy || status === "running" || status === "paused" || status === "queued"}
            />
            Audio verify
            {audioReady?.ready === false ? " (install ASR)" : ""}
          </label>
          <button
            type="button"
            onClick={() => void startProcess()}
            disabled={!videoPath || busy || status === "running" || engineOk === false}
          >
            {status === "running" ? "Live OCR…" : "Process"}
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
              title="Stop processing and remove this job (also deletes recovery snapshot)"
            >
              {status === "running" || status === "paused" || status === "queued"
                ? "Cancel job"
                : "Remove job"}
            </button>
          ) : null}
          <button type="button" className="secondary" onClick={() => void exportAll()} disabled={!jobId}>
            Export
          </button>
          <button type="button" className="secondary" onClick={() => void openFolder()} disabled={!videoPath}>
            Open folder
          </button>
          <button type="button" className="secondary" onClick={() => setBilingual((v) => !v)}>
            {bilingual ? "ZH+EN" : "EN only"}
          </button>
        </div>
      </header>

      <div className="main">
        <section className="player-wrap">
          {videoUrl ? (
            <>
              <video
                ref={videoRef}
                src={videoUrl}
                onTimeUpdate={(e) => setPlayTime(e.currentTarget.currentTime)}
                onPlay={(e) => {
                  if (subtitleGateRef.current === "preparing") {
                    e.currentTarget.pause();
                    setPlaying(false);
                    return;
                  }
                  setPlaying(true);
                }}
                onPause={() => setPlaying(false)}
                onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
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
                        {bufferStatus.coveredAhead.toFixed(1)}s / {BUFFER_AHEAD_TARGET_S}s ahead
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
                <span className="clock">
                  {formatTime(playTime)}
                  {duration > 0 ? ` / ${formatTime(duration)}` : ""}
                </span>
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

        <aside className="side">
          <div className="tabs">
            {(["job", "glossary", "timeline", "models"] as const).map((p) => (
              <button
                key={p}
                type="button"
                className={panel === p ? "tab active" : "tab"}
                onClick={() => setPanel(p)}
              >
                {p}
              </button>
            ))}
          </div>

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

              {audioDisagreements.length ? (
                <>
                  <h2>
                    Audio disagreements
                    <span className="linkish" style={{ pointerEvents: "none" }}>
                      {audioDisagreements.length}
                    </span>
                  </h2>
                  <ul className="cue-list review">
                    {audioDisagreements.slice(0, 40).map((c) => (
                      <li key={`a-${c.cue_id || c.start}-${c.index}`} className="audio-disagree-item">
                        <div className="t">
                          #{c.index} · {formatTime(c.start)}–{formatTime(c.end)}
                          {typeof c.audio_evidence?.char_similarity === "number"
                            ? ` · sim ${Math.round(c.audio_evidence.char_similarity * 100)}%`
                            : ""}
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
                        <div className="zh">OCR: {c.original_ocr || c.text_zh}</div>
                        <div className="en">ASR: {c.audio_transcript || "—"}</div>
                        {c.reconciled_zh && c.reconciled_zh !== (c.original_ocr || c.text_zh) ? (
                          <div className="t">Reconciled: {c.reconciled_zh}</div>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}

              {recovery.length ? (
                <>
                  <h2>
                    Unfinished jobs
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
                </>
              ) : null}

              <h2>
                Uncertain
                <button type="button" className="linkish" onClick={() => setReviewOpen((v) => !v)}>
                  {uncertainCues.length} · {reviewOpen ? "hide" : "show"}
                </button>
              </h2>
              {reviewOpen ? (
                uncertainCues.length === 0 ? (
                  <div className="empty" style={{ padding: "0.35rem 0" }}>
                    No low-confidence cues.
                  </div>
                ) : (
                  <ul className="cue-list review">
                    {uncertainCues.slice(0, 30).map((c) => (
                      <li key={`u-${c.start}-${c.index}`} className="uncertain-item">
                        <div className="t">
                          #{c.index} · {formatTime(c.start)}–{formatTime(c.end)} ·{" "}
                          {typeof c.confidence === "number" ? `${Math.round(c.confidence * 100)}%` : "?"}
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
                        <div className="en">{c.text_en}</div>
                        <div className="zh">{c.text_zh}</div>
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
              </div>
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

          {panel === "timeline" ? (
            <>
              <h2>Timeline editor</h2>
              {editCue ? (
                <div className="editor">
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
                    ZH
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
                  <div className="form-row">
                    <button type="button" onClick={() => void saveEditCue()}>
                      Save cue
                    </button>
                    <button type="button" className="secondary" onClick={() => setEditCue(null)}>
                      Cancel
                    </button>
                  </div>
                </div>
              ) : null}
              <ul className="cue-list">
                {cues.map((c) => (
                  <li key={`t-${c.start}-${c.index}`} className={c.uncertain ? "uncertain-item" : undefined}>
                    <div className="t">
                      #{c.index} · {formatTime(c.start)}–{formatTime(c.end)}
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
              <h2>Models</h2>
              <p className="hint" style={{ opacity: 0.75, fontSize: "0.85rem", marginBottom: "0.75rem" }}>
                Weights live under the SubParty <code>data/models</code> folder (not beside videos). After
                install, pick the matching engine in the top bar. Only one of NLLB/Qwen stays loaded in RAM.
                Disk: NLLB ~2.4&nbsp;GB · Qwen2.5-1.5B ~3.0&nbsp;GB · faster-whisper small ~500&nbsp;MB.
                Audio ASR is optional evidence (never overwrites OCR). Install:{" "}
                <code>pip install -r requirements-audio.txt</code>, then Ensure below, then enable{" "}
                <strong>Audio verify</strong> on Process. SRT still saves next to the video.
              </p>
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
                      {m.size_hint ? ` · ${m.size_hint}` : ""}
                    </div>
                    <div className="t">
                      {m.kind}
                      {m.optional ? " · optional" : ""}
                      {m.engine && m.engine !== "ocr" ? ` · engine id: ${m.engine}` : ""}
                    </div>
                    {m.notes ? <div className="zh">{m.notes}</div> : null}
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
