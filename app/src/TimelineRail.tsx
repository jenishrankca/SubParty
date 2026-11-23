import { useEffect, useMemo, useRef, useState } from "react";
import type { Cue } from "./workspaceTypes";

export interface TimelineRailProps {
  cues: Cue[];
  duration: number;
  playTime: number;
  selectedCueId?: string | null;
  selectedStart?: number | null;
  onSeek: (t: number) => void;
  onSelectCue: (cue: Cue) => void;
  videoUrl?: string | null;
}

function formatTime(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

/**
 * Cue timeline + optional real waveform peaks (Web Audio).
 * Never invents peaks — if decode fails, cue rail only.
 */
export function TimelineRail({
  cues,
  duration,
  playTime,
  selectedCueId,
  selectedStart,
  onSeek,
  onSelectCue,
  videoUrl,
}: TimelineRailProps) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const [peaks, setPeaks] = useState<Float32Array | null>(null);
  const dur = Math.max(duration || 0, playTime, 0.001);

  useEffect(() => {
    let cancelled = false;
    setPeaks(null);
    if (!videoUrl) return;
    void (async () => {
      try {
        const res = await fetch(videoUrl);
        if (!res.ok) return;
        const buf = await res.arrayBuffer();
        const ctx = new AudioContext();
        const audio = await ctx.decodeAudioData(buf.slice(0));
        const ch = audio.getChannelData(0);
        const buckets = 240;
        const out = new Float32Array(buckets);
        const step = Math.floor(ch.length / buckets) || 1;
        for (let i = 0; i < buckets; i++) {
          let peak = 0;
          const start = i * step;
          const end = Math.min(ch.length, start + step);
          for (let j = start; j < end; j++) {
            const v = Math.abs(ch[j]);
            if (v > peak) peak = v;
          }
          out[i] = peak;
        }
        await ctx.close();
        if (!cancelled) setPeaks(out);
      } catch {
        /* no fake waveform — leave null */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [videoUrl]);

  const windowDur = dur / zoom;
  const windowStart = Math.max(0, Math.min(dur - windowDur, playTime - windowDur * 0.35));

  const visibleCues = useMemo(
    () => cues.filter((c) => c.end >= windowStart && c.start <= windowStart + windowDur),
    [cues, windowStart, windowDur]
  );

  function clientToTime(clientX: number): number {
    const el = trackRef.current;
    if (!el) return playTime;
    const rect = el.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    return windowStart + x * windowDur;
  }

  return (
    <div className="timeline-rail">
      <div className="timeline-rail-head">
        <strong>Timeline</strong>
        <span>
          {formatTime(playTime)} / {formatTime(dur)} · {cues.length} cues
        </span>
        <label className="zoom">
          Zoom
          <input
            type="range"
            min={1}
            max={8}
            step={0.5}
            value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))}
          />
        </label>
      </div>
      <div
        className="timeline-track"
        ref={trackRef}
        onClick={(e) => onSeek(clientToTime(e.clientX))}
        role="presentation"
      >
        {peaks ? (
          <div className="waveform" aria-hidden>
            {Array.from(peaks).map((p, i) => {
              const t0 = (i / peaks.length) * dur;
              const t1 = ((i + 1) / peaks.length) * dur;
              if (t1 < windowStart || t0 > windowStart + windowDur) return null;
              const left = ((t0 - windowStart) / windowDur) * 100;
              const width = Math.max(0.15, ((t1 - t0) / windowDur) * 100);
              return (
                <i
                  key={i}
                  style={{
                    left: `${left}%`,
                    width: `${width}%`,
                    height: `${Math.max(4, p * 100)}%`,
                  }}
                />
              );
            })}
          </div>
        ) : (
          <div className="waveform waveform-empty" title="Waveform unavailable — showing cue rail only" />
        )}
        {visibleCues.map((c) => {
          const left = ((c.start - windowStart) / windowDur) * 100;
          const width = Math.max(0.4, ((c.end - c.start) / windowDur) * 100);
          const selected =
            (selectedCueId && c.cue_id === selectedCueId) ||
            (selectedStart != null && Math.abs(c.start - selectedStart) < 0.05);
          return (
            <button
              key={c.cue_id || `${c.start}-${c.index}`}
              type="button"
              className={`cue-block${selected ? " selected" : ""}${c.uncertain ? " uncertain" : ""}${
                c.audio_disagreement ? " disagree" : ""
              }${c.provisional ? " provisional" : ""}`}
              style={{ left: `${left}%`, width: `${width}%` }}
              title={`#${c.index} ${c.text_en || c.text_zh}`}
              onClick={(e) => {
                e.stopPropagation();
                onSelectCue(c);
              }}
            />
          );
        })}
        <div
          className="playhead"
          style={{ left: `${((playTime - windowStart) / windowDur) * 100}%` }}
        />
      </div>
    </div>
  );
}
