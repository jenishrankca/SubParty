import { useEffect, useMemo, useRef, useState } from "react";
import {
  apiBase,
  apiGet,
  apiSend,
  apiToken,
  eventsUrl,
  setApiBase,
  setApiToken,
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
}

function formatTime(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
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
  const videoRef = useRef<HTMLVideoElement>(null);
  const esRef = useRef<EventSource | null>(null);
  const jobIdRef = useRef<string | null>(null);
  const engineUrlRef = useRef(engineUrl);

  useEffect(() => {
    jobIdRef.current = jobId;
  }, [jobId]);
  useEffect(() => {
    engineUrlRef.current = engineUrl;
  }, [engineUrl]);

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
      try {
        const res = await fetch(`${engineUrl}/health`);
        if (!cancelled) setEngineOk(res.ok);
        if (res.ok && apiToken()) {
          void refreshMeta();
        }
      } catch {
        if (!cancelled) setEngineOk(false);
      }
    };
    void ping();
    const id = window.setInterval(ping, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [engineUrl]);

  useEffect(() => {
    if (!jobId || (status !== "running" && status !== "paused")) return;
    const id = window.setInterval(() => {
      const jid = jobIdRef.current;
      const v = videoRef.current;
      if (!jid || !v || status === "paused") return;
      void apiSend(`/jobs/${jid}/playhead`, "POST", { t: v.currentTime }).catch(() => undefined);
    }, 500);
    return () => window.clearInterval(id);
  }, [jobId, status]);

  const activeCue = useMemo(() => {
    const hit = cues.find((c) => playTime >= c.start && playTime <= c.end);
    if (hit) return hit;
    let best: Cue | null = null;
    for (const c of cues) {
      if (c.start <= playTime + 0.15 && c.end >= playTime - 0.4) best = c;
    }
    return best;
  }, [cues, playTime]);

  const uncertainCues = useMemo(
    () => cues.filter((c) => c.uncertain || (typeof c.confidence === "number" && c.confidence < 0.78)),
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
      setError(err instanceof Error ? err.message : String(err));
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
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function resetJobState() {
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
  }

  async function startProcess() {
    if (!videoPath) return;
    setBusy(true);
    setError(null);
    setCues([]);
    setCueCount(0);
    setProgress(0);
    esRef.current?.close();
    const startAt = videoRef.current?.currentTime ?? 0;
    try {
      await apiSend("/engines/select", "POST", { engine_id: engineId });
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
      });
      setJobId(job.id);
      setSrtPath(job.srt_path);
      setStatus(job.status);
      setDuration(job.duration || 0);
      subscribe(job.id);
      const v = videoRef.current;
      if (v) {
        try {
          await v.play();
          setPlaying(true);
        } catch {
          /* gesture */
        }
      }
    } catch (err) {
      setStatus("error");
      setError(err instanceof Error ? err.message : String(err));
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
          setCues((prev) => {
            const next = prev.filter((c) => {
              const sameStart = Math.abs(c.start - cue.start) < 0.12;
              const overlaps = Math.min(c.end, cue.end) - Math.max(c.start, cue.start) > 0.05;
              const sameZhNear = c.text_zh === cue.text_zh && Math.abs(c.start - cue.start) < 0.6;
              return !(sameStart || overlaps || sameZhNear);
            });
            next.push(cue);
            next.sort((a, b) => a.start - b.start);
            return next;
          });
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
        } else if (type === "status" || type === "done" || type === "error") {
          if (typeof data.status === "string") setStatus(data.status as JobStatus);
          if (typeof data.progress === "number") setProgress(data.progress);
          if (typeof data.cue_count === "number") setCueCount(data.cue_count);
          if (typeof data.error === "string") setError(data.error);
          if (typeof data.srt_path === "string") setSrtPath(data.srt_path);
          if (typeof data.series_id === "string") setSeriesId(data.series_id);
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
    if (v.paused) void v.play().then(() => setPlaying(true)).catch(() => undefined);
    else {
      v.pause();
      setPlaying(false);
    }
  }

  function seekBy(delta: number) {
    const v = videoRef.current;
    if (!v) return;
    const next = Math.max(0, Math.min(v.duration || Number.MAX_SAFE_INTEGER, v.currentTime + delta));
    v.currentTime = next;
    setPlayTime(next);
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
      setError(err instanceof Error ? err.message : String(err));
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
      setError(err instanceof Error ? err.message : String(err));
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
      subscribe(job.id);
      await loadGlossary(job.video_path);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
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
          <button type="button" className="secondary" onClick={() => void pickVideo()}>
            Pick video
          </button>
          <button type="button" className="secondary" onClick={() => void pickBatch()} disabled={busy}>
            Batch
          </button>
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
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
              />
              <div
                className={`cue-overlay${activeCue ? " visible" : ""}${bilingual ? " bilingual" : ""}${
                  activeCue?.uncertain ? " uncertain" : ""
                }`}
              >
                {bilingual && activeCue?.text_zh ? <div className="cue-zh">{activeCue.text_zh}</div> : null}
                <div className="cue-en">{activeCue?.text_en || ""}</div>
              </div>
              <div className="transport">
                <button type="button" className="secondary" onClick={() => seekBy(-10)}>
                  −10s
                </button>
                <button type="button" onClick={togglePlay}>
                  {playing ? "Pause" : "Play"}
                </button>
                <button type="button" className="secondary" onClick={() => seekBy(10)}>
                  +10s
                </button>
                <span className="clock">
                  {formatTime(playTime)}
                  {duration > 0 ? ` / ${formatTime(duration)}` : ""}
                </span>
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
              <h2>Job</h2>
              <div className="meta">
                <div>
                  Engine: <strong>{engineOk === null ? "…" : engineOk ? "ready" : "offline"}</strong>
                  {apiToken() ? " · secured" : ""}
                </div>
                <div>
                  Translator: <strong>{engineId}</strong>
                </div>
                <div>
                  Video: <strong>{videoPath || "—"}</strong>
                </div>
                <div>
                  SRT: <strong>{srtPath || "—"}</strong>
                </div>
                {jobId ? (
                  <div>
                    Job: <strong>{jobId.slice(0, 8)}</strong>
                  </div>
                ) : null}
                {avgSlotMs != null ? (
                  <div>
                    Slot: <strong>{avgSlotMs} ms avg</strong>
                  </div>
                ) : null}
                {sampleInterval != null && status === "running" ? (
                  <div>
                    Sample: <strong>every {sampleInterval.toFixed(1)}s</strong>
                  </div>
                ) : null}
                {bufferAhead != null && status === "running" ? (
                  <div>
                    Buffer ahead: <strong>{bufferAhead.toFixed(1)}s</strong>
                  </div>
                ) : null}
                {liveLag != null && status === "running" ? (
                  <div>
                    Live lag: <strong>{liveLag.toFixed(1)}s</strong>
                  </div>
                ) : null}
                {seriesId ? (
                  <div>
                    Series: <strong>{seriesId}</strong>
                    {glossaryCount != null ? ` · ${glossaryCount} terms` : ""}
                  </div>
                ) : null}
                {regionLabel ? (
                  <div>
                    Sub region: <strong>{regionLabel}</strong>
                  </div>
                ) : null}
                {batchNote ? (
                  <div>
                    Note: <strong>{batchNote}</strong>
                  </div>
                ) : null}
              </div>

              <div className="progress-row">
                <span>
                  {status === "idle" ? "Idle" : status} · {cueCount} cues
                </span>
                <span>
                  {pct}%
                  {duration > 0 ? ` · ${formatTime(duration)}` : ""}
                </span>
              </div>
              <div className="bar">
                <i style={{ width: `${pct}%` }} />
              </div>

              <div className={`status${error ? " error" : ""}`}>
                {error
                  ? error
                  : status === "running"
                    ? "Adaptive live OCR with context, glossary, and selectable engines."
                    : status === "paused"
                      ? "Paused — resume to continue from the playhead."
                      : status === "done"
                        ? "Done. Export bilingual SRT / VTT / ASS from the toolbar."
                        : `Base ${apiBase()}`}
              </div>

              {recovery.length ? (
                <>
                  <h2>Recover</h2>
                  <ul className="cue-list">
                    {recovery.slice(0, 8).map((r) => (
                      <li key={String(r.id)}>
                        <div className="t">
                          {String(r.status)} · {String(r.id).slice(0, 8)}
                        </div>
                        <div className="en">{String(r.video_path)}</div>
                        <button type="button" className="linkish" onClick={() => void resumeRecovery(r)}>
                          Resume job
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
                      {typeof c.confidence === "number" ? ` · ${Math.round(c.confidence * 100)}%` : ""}
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
                  </li>
                ))}
              </ul>
            </>
          ) : null}

          {panel === "models" ? (
            <>
              <h2>Models</h2>
              <ul className="cue-list">
                {models.map((m) => (
                  <li key={m.id}>
                    <div className="en">
                      {m.name} {m.installed ? "· installed" : "· missing"}
                    </div>
                    <div className="t">
                      {m.kind}
                      {m.optional ? " · optional" : ""}
                    </div>
                    {!m.installed ? (
                      <button type="button" className="linkish" onClick={() => void ensureModel(m.id)}>
                        Download / ensure
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
