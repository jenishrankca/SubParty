import { useEffect, useMemo, useRef, useState } from "react";

interface Cue {
  index: number;
  start: number;
  end: number;
  text_zh: string;
  text_en: string;
}

type JobStatus = "idle" | "queued" | "running" | "done" | "error" | "cancelled";

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
      }
    })();
    return () => {
      esRef.current?.close();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const ping = async () => {
      try {
        const res = await fetch(`${engineUrl}/health`);
        if (!cancelled) setEngineOk(res.ok);
      } catch {
        if (!cancelled) setEngineOk(false);
      }
    };
    void ping();
    const id = window.setInterval(ping, 4000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [engineUrl]);

  // Push playhead so OCR prioritizes near current time / seeks
  useEffect(() => {
    if (!jobId || status !== "running") return;
    const id = window.setInterval(() => {
      const jid = jobIdRef.current;
      const v = videoRef.current;
      if (!jid || !v) return;
      void fetch(`${engineUrlRef.current}/jobs/${jid}/playhead`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ t: v.currentTime }),
      }).catch(() => undefined);
    }, 500);
    return () => window.clearInterval(id);
  }, [jobId, status]);

  const activeCue = useMemo(() => {
    // Prefer the cue covering playhead; if none, show nearest recent cue within 0.4s
    const hit = cues.find((c) => playTime >= c.start && playTime <= c.end);
    if (hit) return hit;
    let best: Cue | null = null;
    for (const c of cues) {
      if (c.start <= playTime + 0.15 && c.end >= playTime - 0.4) {
        best = c;
      }
    }
    return best;
  }, [cues, playTime]);

  async function pickVideo() {
    setError(null);
    const picked = await window.subparty.pickVideo();
    if (!picked) return;
    setVideoPath(picked);
    const url = await window.subparty.pathToFileUrl(picked);
    setVideoUrl(url);
    setSrtPath(null);
    setCues([]);
    setCueCount(0);
    setProgress(0);
    setStatus("idle");
    setJobId(null);
    setPlayTime(0);
    setAvgSlotMs(null);
    setLiveLag(null);
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
      const res = await fetch(`${engineUrl}/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          video_path: videoPath,
          fps: 1.0,
          band_ratio: 0.2,
          lookahead: 12,
          playhead: startAt,
        }),
      });
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(detail || `HTTP ${res.status}`);
      }
      const job = (await res.json()) as {
        id: string;
        srt_path: string;
        status: JobStatus;
        duration: number;
      };
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
          /* autoplay may need gesture */
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
    const es = new EventSource(`${engineUrl}/jobs/${id}/events`);
    esRef.current = es;
    es.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data) as Record<string, unknown>;
        const type = String(data.type || "");
        if (type === "cue" && data.cue) {
          const cue = data.cue as Cue;
          setCues((prev) => {
            const next = prev.filter(
              (c) => !(Math.abs(c.start - cue.start) < 0.05 && c.text_en === cue.text_en)
            );
            next.push(cue);
            next.sort((a, b) => a.start - b.start);
            return next;
          });
          setCueCount(Number(data.cue_count ?? 0));
          if (typeof data.srt_path === "string") setSrtPath(data.srt_path);
        } else if (type === "progress") {
          setProgress(Number(data.progress ?? 0));
          if (typeof data.duration === "number") setDuration(data.duration);
          if (typeof data.avg_slot_ms === "number") setAvgSlotMs(data.avg_slot_ms);
          if (typeof data.live_lag === "number") setLiveLag(data.live_lag);
          setStatus("running");
        } else if (type === "status" || type === "done" || type === "error") {
          if (typeof data.status === "string") setStatus(data.status as JobStatus);
          if (typeof data.progress === "number") setProgress(data.progress);
          if (typeof data.cue_count === "number") setCueCount(data.cue_count);
          if (typeof data.error === "string") setError(data.error);
          if (typeof data.srt_path === "string") setSrtPath(data.srt_path);
        } else if (type === "end") {
          es.close();
        }
      } catch {
        /* ignore malformed */
      }
    };
    es.onerror = () => {
      /* EventSource retries */
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
    if (v.paused) {
      void v.play().then(() => setPlaying(true)).catch(() => undefined);
    } else {
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
    if (jid) {
      void fetch(`${engineUrlRef.current}/jobs/${jid}/playhead`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ t: next }),
      }).catch(() => undefined);
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
          <button type="button" className="secondary" onClick={() => void pickVideo()}>
            Pick video
          </button>
          <button
            type="button"
            onClick={() => void startProcess()}
            disabled={!videoPath || busy || status === "running" || engineOk === false}
          >
            {status === "running" ? "Live OCR…" : "Process"}
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => void openFolder()}
            disabled={!videoPath}
          >
            Open folder
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
              <div className={`cue-overlay${activeCue ? " visible" : ""}`}>
                {activeCue?.text_en || ""}
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
          <h2>Job</h2>
          <div className="meta">
            <div>
              Engine:{" "}
              <strong>
                {engineOk === null ? "…" : engineOk ? "ready" : "offline"}
              </strong>
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
            {liveLag != null && status === "running" ? (
              <div>
                Live lag: <strong>{liveLag.toFixed(1)}s</strong>
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
                ? "Playhead-priority OCR — English appears near current time; SRT fills in background."
                : status === "done"
                  ? "Done. .en.srt is beside the video for VLC / YouTube."
                  : "Free local stack: ffmpeg → RapidOCR (zh) → Argos ZH→EN."}
          </div>

          <h2>Cues</h2>
          {cues.length === 0 ? (
            <div className="empty" style={{ padding: "0.5rem 0" }}>
              No cues yet.
            </div>
          ) : (
            <ul className="cue-list">
              {[...cues].reverse().slice(0, 40).map((c) => (
                <li key={`${c.start}-${c.index}`}>
                  <div className="t">
                    #{c.index} · {formatTime(c.start)}–{formatTime(c.end)}
                  </div>
                  <div className="en">{c.text_en}</div>
                  <div className="zh">{c.text_zh}</div>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>
    </div>
  );
}
