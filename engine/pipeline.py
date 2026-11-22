"""Playhead-aware OCR pipeline: prioritize near playback, fill SRT in background."""

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


@dataclass
class JobState:
    id: str
    video_path: str
    srt_path: str
    status: str = "queued"  # queued | running | done | error | cancelled
    progress: float = 0.0
    cue_count: int = 0
    duration: float = 0.0
    current_time: float = 0.0  # last processed sample
    playhead: float = 0.0  # viewer position (UI)
    error: str | None = None
    cues: list[dict[str, Any]] = field(default_factory=list)
    live_lag: float = 0.0  # playhead - latest_live_cue_end (approx)
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
        lookahead: float = 12.0,
    ) -> JobState:
        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")
        if path.suffix.lower() not in {".mp4", ".mkv", ".ts", ".webm", ".avi", ".mov"}:
            raise ValueError(f"Unsupported format: {path.suffix}")

        srt_path = str(path.parent / f"{path.stem}.en.srt")
        job = JobState(id=str(uuid.uuid4()), video_path=str(path), srt_path=srt_path)
        with self._lock:
            self._jobs[job.id] = job
            self._listeners[job.id] = []

        thread = threading.Thread(
            target=self._run,
            args=(job.id, fps, band_ratio, lookahead),
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
            # Seek jump → wake worker to retarget
            if abs(job.playhead - prev) >= 2.0:
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

    def _run(self, job_id: str, fps: float, band_ratio: float, lookahead: float) -> None:
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

            interval = 1.0 / max(0.5, fps)  # default ~1.0s live slots
            n_slots = max(1, int(duration / interval) + 1)
            done: set[int] = set()

            # Per-slot OCR text; merge contiguous same-text into cues for SRT/UI
            slot_text: dict[int, str] = {}
            emitted_runs: set[tuple[int, int, str]] = set()
            cue_serial = 0
            timings: list[float] = []

            with tempfile.TemporaryDirectory(prefix="subparty_") as tmp:
                tmp_dir = Path(tmp)

                while not job._cancel.is_set():
                    if len(done) >= n_slots:
                        break

                    job._seek_event.clear()
                    with job._playhead_lock:
                        playhead = job.playhead

                    slot = _pick_next_slot(
                        done=done,
                        n_slots=n_slots,
                        interval=interval,
                        playhead=playhead,
                        lookahead=lookahead,
                    )
                    if slot is None:
                        break

                    t = min(duration, slot * interval)
                    frame_path = tmp_dir / f"frame_{slot:06d}.png"
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
                    if len(timings) > 40:
                        timings = timings[-40:]

                    done.add(slot)
                    slot_text[slot] = zh
                    job.current_time = t
                    job.progress = len(done) / float(n_slots)

                    cue_serial = self._emit_merged_runs(
                        job, writer, slot_text, done, interval, duration, emitted_runs, cue_serial
                    )

                    # Live lag: playhead vs highest contiguous processed slot from playhead
                    ph_slot = max(0, int(playhead / interval))
                    frontier = ph_slot - 1
                    s = ph_slot
                    while s < n_slots and s in done:
                        frontier = s
                        s += 1
                    job.live_lag = playhead - (frontier * interval) if frontier >= 0 else playhead

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
                            "live_lag": job.live_lag,
                            "slot_ms": round(elapsed * 1000),
                            "avg_slot_ms": round(avg_ms),
                            "slots_done": len(done),
                            "slots_total": n_slots,
                        },
                    )

                    if job._seek_event.is_set():
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
        """Merge contiguous same-text slots into cues; emit newly completed or extended runs."""
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
            # Only emit when the run cannot grow forward yet (next slot done & different/blank,
            # or next slot not done but we still emit for live — emit whenever run has ≥1 slot)
            key = (s0, s1, zh)
            # Re-emit if run extended
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


def _pick_next_slot(
    *,
    done: set[int],
    n_slots: int,
    interval: float,
    playhead: float,
    lookahead: float,
) -> int | None:
    """Prefer unprocessed slots in [playhead, playhead+lookahead], else next ahead, else gaps behind."""
    if len(done) >= n_slots:
        return None

    ph = max(0, int(playhead / interval))
    ahead_end = min(n_slots - 1, int((playhead + lookahead) / interval))

    # 1) Live window forward from playhead
    for s in range(ph, ahead_end + 1):
        if s not in done:
            return s

    # 2) Further ahead beyond lookahead (background catch-up forward)
    for s in range(ahead_end + 1, n_slots):
        if s not in done:
            return s

    # 3) Fill gaps behind playhead (background SRT completeness)
    for s in range(0, ph):
        if s not in done:
            return s

    return None


jobs = JobManager()
