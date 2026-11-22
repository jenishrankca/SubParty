"""Playhead-driven adaptive OCR: keep up with live watch, fill SRT in background."""

from __future__ import annotations

import logging
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ffmpeg_util import extract_subtitle_band, probe_duration, probe_size
from ocr import ocr_lines, warm_ocr
from srt_writer import Cue, SrtWriter
from text_filters import filter_ocr_lines
from translate import translate_zh_to_en, warm_translate

logger = logging.getLogger("subparty.pipeline")

EventCallback = Callable[[dict[str, Any]], None]

# Adaptive sampling bounds (seconds between samples)
_MIN_INTERVAL = 0.7
_MAX_INTERVAL = 2.5
_DEFAULT_LOOKAHEAD = 10.0


@dataclass
class JobState:
    id: str
    video_path: str
    srt_path: str
    status: str = "queued"
    progress: float = 0.0
    cue_count: int = 0
    duration: float = 0.0
    current_time: float = 0.0
    playhead: float = 0.0
    error: str | None = None
    cues: list[dict[str, Any]] = field(default_factory=list)
    live_lag: float = 0.0
    sample_interval: float = 1.0
    buffer_ahead: float = 0.0
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _playhead_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _seek_event: threading.Event = field(default_factory=threading.Event, repr=False)


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, JobState] = {}
        self._listeners: dict[str, list[EventCallback]] = {}
        self._lock = threading.Lock()

    def create(
        self,
        video_path: str,
        fps: float = 1.0,
        band_ratio: float = 0.2,
        lookahead: float = _DEFAULT_LOOKAHEAD,
        playhead: float = 0.0,
    ) -> JobState:
        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")
        if path.suffix.lower() not in {".mp4", ".mkv", ".ts", ".webm", ".avi", ".mov"}:
            raise ValueError(f"Unsupported format: {path.suffix}")

        srt_path = str(path.parent / f"{path.stem}.en.srt")
        job = JobState(id=str(uuid.uuid4()), video_path=str(path), srt_path=srt_path)
        job.sample_interval = 1.0 / max(0.5, fps)
        job.playhead = max(0.0, float(playhead))
        with self._lock:
            self._jobs[job.id] = job
            self._listeners[job.id] = []

        thread = threading.Thread(
            target=self._run,
            args=(job.id, band_ratio, lookahead),
            daemon=True,
            name=f"subparty-job-{job.id[:8]}",
        )
        thread.start()
        return job

    def get(self, job_id: str) -> JobState | None:
        return self._jobs.get(job_id)

    def set_playhead(self, job_id: str, t: float) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        with job._playhead_lock:
            prev = job.playhead
            job.playhead = max(0.0, float(t))
            if abs(job.playhead - prev) >= 1.5:
                job._seek_event.set()
        return True

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        job._cancel.set()
        job._seek_event.set()
        return True

    def subscribe(self, job_id: str, cb: EventCallback) -> None:
        with self._lock:
            self._listeners.setdefault(job_id, []).append(cb)

    def unsubscribe(self, job_id: str, cb: EventCallback) -> None:
        with self._lock:
            listeners = self._listeners.get(job_id)
            if listeners and cb in listeners:
                listeners.remove(cb)

    def _emit(self, job: JobState, event: dict[str, Any]) -> None:
        with self._lock:
            listeners = list(self._listeners.get(job.id, []))
        for cb in listeners:
            try:
                cb(event)
            except Exception:
                logger.exception("listener error")

    def _run(self, job_id: str, band_ratio: float, base_lookahead: float) -> None:
        job = self._jobs[job_id]
        job.status = "running"
        self._emit(job, {"type": "status", "status": "running", "progress": 0, "cue_count": 0})

        try:
            warm_ocr()
            warm_translate()

            video = Path(job.video_path)
            duration = probe_duration(video)
            frame_w, frame_h = probe_size(video)
            job.duration = duration
            writer = SrtWriter(Path(job.srt_path))

            # Quantized sample grid uses a *base* tick; adaptive stride skips ticks when lagging
            base_tick = 0.5  # fine grid; we jump by adaptive stride
            n_ticks = max(1, int(duration / base_tick) + 1)
            done: set[int] = set()
            slot_text: dict[int, str] = {}
            emitted_runs: set[tuple[int, int, str]] = set()
            cue_serial = 0
            timings: list[float] = []
            interval = max(_MIN_INTERVAL, min(_MAX_INTERVAL, job.sample_interval))
            lookahead = base_lookahead
            last_zh = ""
            blank_streak = 0

            with tempfile.TemporaryDirectory(prefix="subparty_") as tmp:
                tmp_dir = Path(tmp)

                while not job._cancel.is_set():
                    if len(done) >= n_ticks:
                        break

                    job._seek_event.clear()
                    with job._playhead_lock:
                        playhead = job.playhead

                    # Adaptive interval from recent OCR cost + lag
                    avg_s = (sum(timings) / len(timings)) if timings else interval
                    lag = job.live_lag
                    if lag > 4.0:
                        # Falling behind: coarsen + shrink lookahead; skip backlog
                        interval = min(_MAX_INTERVAL, max(avg_s * 1.25, 1.4))
                        lookahead = max(4.0, base_lookahead * 0.5)
                    elif lag > 1.5:
                        interval = min(_MAX_INTERVAL, max(avg_s * 1.1, 1.0))
                        lookahead = max(6.0, base_lookahead * 0.75)
                    elif lag < -1.0:
                        # Buffer ahead: can densify a bit
                        interval = max(_MIN_INTERVAL, min(avg_s * 0.85, 1.0))
                        lookahead = min(18.0, base_lookahead * 1.3)
                    else:
                        interval = max(_MIN_INTERVAL, min(_MAX_INTERVAL, max(avg_s * 1.05, 0.85)))
                        lookahead = base_lookahead

                    # Blank streak → jump farther (no dialogue to catch)
                    stride_ticks = max(1, int(round(interval / base_tick)))
                    if blank_streak >= 3:
                        stride_ticks = max(stride_ticks, int(round(1.6 / base_tick)))

                    job.sample_interval = stride_ticks * base_tick

                    tick = _pick_next_tick(
                        done=done,
                        n_ticks=n_ticks,
                        base_tick=base_tick,
                        playhead=playhead,
                        lookahead=lookahead,
                        stride_ticks=stride_ticks,
                        lagging=lag > 1.5,
                    )
                    if tick is None:
                        break

                    t = min(duration, tick * base_tick)
                    frame_path = tmp_dir / f"frame_{tick:07d}.png"
                    t0 = time.perf_counter()
                    try:
                        extract_subtitle_band(
                            video,
                            t,
                            frame_path,
                            band_ratio=band_ratio,
                            height=frame_h,
                            width=frame_w,
                            side_inset_ratio=0.08,
                            scale_width=640,
                        )
                        zh = filter_ocr_lines(ocr_lines(str(frame_path)))
                    except Exception as exc:
                        logger.warning("frame @%.2fs failed: %s", t, exc)
                        zh = ""
                    elapsed = time.perf_counter() - t0
                    timings.append(elapsed)
                    if len(timings) > 30:
                        timings = timings[-30:]

                    # Mark this tick done; when lagging, also mark skipped ticks behind playhead
                    # in the live window so we don't crawl through backlog later
                    done.add(tick)
                    slot_text[tick] = zh
                    if lag > 2.0:
                        ph_tick = max(0, int(playhead / base_tick))
                        for s in range(max(0, ph_tick - int(2.0 / base_tick)), ph_tick):
                            if s not in done:
                                done.add(s)
                                slot_text.setdefault(s, "")

                    if zh:
                        blank_streak = 0
                    else:
                        blank_streak += 1
                    if zh and zh == last_zh:
                        # Same dialogue — treat nearby unfinished ticks as same text cheaply
                        for s in range(tick + 1, min(n_ticks, tick + stride_ticks)):
                            if s not in done:
                                done.add(s)
                                slot_text[s] = zh
                    last_zh = zh

                    job.current_time = t
                    covered = max((s for s in done), default=0) * base_tick
                    job.progress = min(1.0, covered / duration) if duration > 0 else 1.0

                    cue_serial = self._emit_merged_runs(
                        job,
                        writer,
                        slot_text,
                        done,
                        base_tick,
                        duration,
                        emitted_runs,
                        cue_serial,
                    )

                    # Buffer ahead / lag relative to playhead
                    ph_tick = max(0, int(playhead / base_tick))
                    frontier = ph_tick - 1
                    s = ph_tick
                    while s < n_ticks and s in done:
                        frontier = s
                        s += 1
                    frontier_t = frontier * base_tick if frontier >= 0 else playhead - 30
                    job.live_lag = playhead - frontier_t
                    job.buffer_ahead = max(0.0, frontier_t - playhead)

                    avg_ms = (sum(timings) / len(timings)) * 1000 if timings else 0
                    self._emit(
                        job,
                        {
                            "type": "progress",
                            "progress": job.progress,
                            "current_time": t,
                            "duration": duration,
                            "cue_count": job.cue_count,
                            "playhead": playhead,
                            "live_lag": round(job.live_lag, 2),
                            "buffer_ahead": round(job.buffer_ahead, 2),
                            "sample_interval": round(job.sample_interval, 2),
                            "slot_ms": round(elapsed * 1000),
                            "avg_slot_ms": round(avg_ms),
                            "ticks_done": len(done),
                            "ticks_total": n_ticks,
                        },
                    )

                    if job._seek_event.is_set():
                        # On seek: abandon dense catch-up; next loop retargets to new playhead
                        continue

            if job._cancel.is_set():
                job.status = "cancelled"
                self._emit(job, {"type": "status", "status": "cancelled", "progress": job.progress})
                return

            job.status = "done"
            job.progress = 1.0
            self._emit(
                job,
                {
                    "type": "done",
                    "status": "done",
                    "progress": 1.0,
                    "cue_count": job.cue_count,
                    "srt_path": job.srt_path,
                },
            )
        except Exception as exc:
            logger.exception("job failed")
            job.status = "error"
            job.error = str(exc)
            self._emit(job, {"type": "error", "status": "error", "error": str(exc)})

    def _emit_merged_runs(
        self,
        job: JobState,
        writer: SrtWriter,
        slot_text: dict[int, str],
        done: set[int],
        interval: float,
        duration: float,
        emitted_runs: set[tuple[int, int, str]],
        serial: int,
    ) -> int:
        if not done:
            return serial
        slots = sorted(done)
        i = 0
        while i < len(slots):
            s0 = slots[i]
            zh = slot_text.get(s0, "")
            if not zh:
                i += 1
                continue
            s1 = s0
            j = i + 1
            while j < len(slots) and slots[j] == s1 + 1 and slot_text.get(slots[j], "") == zh:
                s1 = slots[j]
                j += 1
            key = (s0, s1, zh)
            older = [k for k in emitted_runs if k[0] == s0 and k[2] == zh and k[1] < s1]
            for k in older:
                emitted_runs.discard(k)
            if key not in emitted_runs:
                start = s0 * interval
                end = min(duration, (s1 + 1) * interval)
                serial = self._flush_cue(job, writer, serial, start, end, zh)
                emitted_runs.add(key)
            i = j
        return serial

    def _flush_cue(
        self,
        job: JobState,
        writer: SrtWriter,
        serial: int,
        start: float,
        end: float,
        zh: str,
    ) -> int:
        serial += 1
        try:
            en = translate_zh_to_en(zh) or zh
        except Exception as exc:
            logger.warning("translate failed, keeping ZH: %s", exc)
            en = zh

        cue = Cue(index=serial, start=start, end=end, text_zh=zh, text_en=en)
        writer.upsert_and_rewrite(cue)
        payload = {
            "index": cue.index,
            "start": cue.start,
            "end": cue.end,
            "text_zh": cue.text_zh,
            "text_en": cue.text_en,
        }
        job.cues = sorted(
            [
                c
                for c in job.cues
                if not (abs(float(c["start"]) - start) < 0.05 and c["text_zh"] == zh)
            ]
            + [payload],
            key=lambda c: float(c["start"]),
        )
        job.cue_count = len(job.cues)
        self._emit(
            job,
            {"type": "cue", "cue": payload, "cue_count": job.cue_count, "srt_path": job.srt_path},
        )
        return serial


def _pick_next_tick(
    *,
    done: set[int],
    n_ticks: int,
    base_tick: float,
    playhead: float,
    lookahead: float,
    stride_ticks: int,
    lagging: bool,
) -> int | None:
    """
    Playhead-first picker.
    When lagging: jump to playhead (skip backlog). When keeping up: walk forward by stride.
    Background fill only when live window is already covered.
    """
    if len(done) >= n_ticks:
        return None

    ph = max(0, int(playhead / base_tick))
    ahead_end = min(n_ticks - 1, int((playhead + lookahead) / base_tick))
    stride = max(1, stride_ticks)

    if lagging:
        # Snap to playhead — do not crawl missed ticks behind
        for s in range(ph, min(n_ticks, ph + stride * 4), stride):
            if s not in done:
                return s
        if ph not in done and ph < n_ticks:
            return ph

    # Live window: forward from playhead by stride
    s = ph
    # Align to stride grid near playhead
    s = ph - (ph % stride) if ph % stride else ph
    if s < ph:
        s += stride
    while s <= ahead_end:
        if s not in done:
            return s
        s += stride

    # Any remaining hole in live window (dense fill if we have spare capacity)
    for s in range(ph, ahead_end + 1):
        if s not in done:
            return s

    # Further ahead (pre-buffer) — coarse stride
    s = ahead_end + 1
    while s < n_ticks:
        if s not in done:
            return s
        s += max(stride, int(1.5 / base_tick))

    # Behind playhead only when live buffer is healthy (caller sets lagging=False)
    if not lagging:
        for s in range(0, ph, max(stride, int(2.0 / base_tick))):
            if s not in done:
                return s
        for s in range(0, ph):
            if s not in done:
                return s

    return None


jobs = JobManager()
