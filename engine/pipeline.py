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

from context_translate import DialogueContext
from dialogue_buffer import DialogueBuffer
from ffmpeg_util import extract_region, probe_duration, probe_size
from glossary import glossary_for_video
from ocr import ocr_lines, warm_ocr
from audio_verify import audio_verify_enabled, verify_cue_records, whisper_available
from metrics import realtime_factor
from refine import finalize_cues_at_eof, refine_batch
from region import (
    ConfirmableRegion,
    SubtitleRegion,
    detect_subtitle_region_confirmable,
    get_confirmed_region,
)
from revision import CueRecord
from scheduler import PerformanceScheduler
from srt_writer import Cue, SrtWriter, merge_cue_payloads
from text_filters import UNCERTAIN_CONFIDENCE, filter_ocr_result
from timing_engine import DEFAULT_FINE_STEP_S, refine_boundaries, transition_ticks
from translate import warm_translate

logger = logging.getLogger("subparty.pipeline")

EventCallback = Callable[[dict[str, Any]], None]

# Adaptive sampling bounds (seconds between samples)
_MIN_INTERVAL = 0.5
_MAX_INTERVAL = 2.8
_MIN_INTERVAL_ACCURACY = 0.35
_DEFAULT_LOOKAHEAD = 10.0
# Live: keep a deeper playhead-ahead window so 1× watch does not drain the buffer
_DEFAULT_LOOKAHEAD_LIVE = 12.0
_LIVE_BUFFER_TARGET_S = 5.0  # maintain ≥ this many seconds ahead while watching
_LIVE_BUFFER_REFILL_S = 3.0  # densify when buffer dips below this
_SEEK_COOLDOWN_S = 2.0  # after seek: snap to playhead, drop backlog
_DEFAULT_CONTEXT_WINDOW_S = 120.0
_LIVE_CONTEXT_WINDOW_S = 30.0
_LIVE_SCALE_WIDTH = 320
_QUALITY_SCALE_WIDTH = 640


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
    series_id: str = ""
    region: dict[str, float] = field(default_factory=dict)
    engine_id: str = "argos"
    paused: bool = False
    batch_id: str | None = None
    context_window_s: float = _DEFAULT_CONTEXT_WINDOW_S
    live_mode: bool = True
    high_accuracy: bool = False
    audio_verify: bool = False
    audio_summary: dict[str, Any] = field(default_factory=dict)
    cue_records: list[CueRecord] = field(default_factory=list, repr=False)
    discard_on_cancel: bool = False
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _pause: threading.Event = field(default_factory=threading.Event, repr=False)
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
        lookahead: float | None = None,
        playhead: float = 0.0,
        engine_id: str = "argos",
        batch_id: str | None = None,
        resume_job_id: str | None = None,
        context_window_s: float | None = None,
        live_mode: bool = True,
        high_accuracy: bool | None = None,
        audio_verify: bool | None = None,
    ) -> JobState:
        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")
        if path.suffix.lower() not in {".mp4", ".mkv", ".ts", ".webm", ".avi", ".mov"}:
            raise ValueError(f"Unsupported format: {path.suffix}")

        srt_path = str(path.parent / f"{path.stem}.en.srt")
        job_id = resume_job_id or str(uuid.uuid4())
        job = JobState(id=job_id, video_path=str(path), srt_path=srt_path)
        job.live_mode = bool(live_mode)
        # Live = speed path (Argos + provisional); Quality = heavy pipeline
        if job.live_mode:
            job.high_accuracy = False if high_accuracy is None else bool(high_accuracy)
            job.audio_verify = False  # never on live path
            job.context_window_s = max(
                15.0,
                float(context_window_s if context_window_s is not None else _LIVE_CONTEXT_WINDOW_S),
            )
            job.context_window_s = min(job.context_window_s, 45.0)
            la = float(lookahead if lookahead is not None else _DEFAULT_LOOKAHEAD_LIVE)
        else:
            job.high_accuracy = True if high_accuracy is None else bool(high_accuracy)
            job.audio_verify = audio_verify_enabled(audio_verify)
            job.context_window_s = max(
                15.0,
                float(context_window_s if context_window_s is not None else _DEFAULT_CONTEXT_WINDOW_S),
            )
            la = float(lookahead if lookahead is not None else _DEFAULT_LOOKAHEAD)
        job.sample_interval = 1.0 / max(0.5, fps)
        job.playhead = max(0.0, float(playhead))
        # Live defaults to Argos; quality may use heavier engines
        eng = (engine_id or "argos").strip().lower()
        if job.live_mode and eng in {"nllb", "qwen", "qwen2"}:
            logger.info("live mode: forcing Argos (requested %s)", eng)
            eng = "argos"
        job.engine_id = eng
        job.batch_id = batch_id
        with self._lock:
            self._jobs[job.id] = job
            self._listeners[job.id] = []

        thread = threading.Thread(
            target=self._run,
            args=(job.id, band_ratio, la),
            daemon=True,
            name=f"subparty-job-{job.id[:8]}",
        )
        thread.start()
        return job

    def pause(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status not in {"running", "paused"}:
            return False
        job.paused = True
        job.status = "paused"
        job._pause.set()
        self._persist(job)
        self._emit(job, {"type": "status", "status": "paused", "progress": job.progress})
        return True

    def resume(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.paused = False
        job.status = "running"
        job._pause.set()  # wake the pause wait loop
        self._persist(job)
        self._emit(job, {"type": "status", "status": "running", "progress": job.progress})
        return True

    def _rewrite_srt(self, job: JobState) -> None:
        from srt_writer import Cue, SrtWriter

        writer = SrtWriter(Path(job.srt_path))
        writer.rewrite_all(
            [
                Cue(
                    index=int(c.get("index") or i),
                    start=float(c["start"]),
                    end=float(c["end"]),
                    text_zh=str(c.get("text_zh") or ""),
                    text_en=str(c.get("text_en") or ""),
                    confidence=float(c.get("confidence") or 1.0),
                    uncertain=bool(c.get("uncertain") or False),
                )
                for i, c in enumerate(job.cues, start=1)
            ]
        )

    def replace_cues(self, job_id: str, cues: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        job = self._jobs.get(job_id)
        if not job:
            return None
        job.cues = list(cues)
        job.cue_count = len(job.cues)
        self._rewrite_srt(job)
        self._persist(job)
        self._emit(
            job,
            {"type": "cues_replaced", "cue_count": job.cue_count, "srt_path": job.srt_path},
        )
        return job.cues

    def update_cue(self, job_id: str, cue: dict[str, Any]) -> dict[str, Any] | None:
        job = self._jobs.get(job_id)
        if not job:
            return None
        from srt_writer import merge_cue_payloads

        payload = {
            "index": int(cue.get("index") or 0),
            "start": float(cue["start"]),
            "end": float(cue["end"]),
            "text_zh": str(cue.get("text_zh") or ""),
            "text_en": str(cue.get("text_en") or ""),
            "confidence": float(cue.get("confidence") or 1.0),
            "uncertain": bool(cue.get("uncertain") or False),
        }
        # Preserve evidence fields when editing existing cue
        for existing in job.cues:
            if abs(float(existing.get("start") or 0) - payload["start"]) < 0.12 or (
                cue.get("cue_id") and existing.get("cue_id") == cue.get("cue_id")
            ):
                for k in (
                    "cue_id",
                    "original_ocr",
                    "audio_transcript",
                    "reconciled_zh",
                    "audio_disagreement",
                    "maturity",
                ):
                    if k in existing and k not in cue:
                        payload[k] = existing[k]
                break
        if cue.get("cue_id"):
            payload["cue_id"] = cue["cue_id"]
        job.cues = merge_cue_payloads(job.cues, payload)
        job.cue_count = len(job.cues)
        self._rewrite_srt(job)
        self._persist(job)
        matched = next(
            (c for c in job.cues if abs(float(c["start"]) - payload["start"]) < 1e-6),
            payload,
        )
        self._emit(job, {"type": "cue", "cue": matched, "cue_count": job.cue_count, "srt_path": job.srt_path})
        return matched

    def split_cue(self, job_id: str, body: dict[str, Any]) -> list[dict[str, Any]] | None:
        from cue_ops import split_cue

        job = self._jobs.get(job_id)
        if not job:
            return None
        next_cues = split_cue(
            list(job.cues),
            cue_id=body.get("cue_id"),
            start=body.get("start"),
            at=body.get("at"),
            left_zh=body.get("left_zh"),
            right_zh=body.get("right_zh"),
            left_en=body.get("left_en"),
            right_en=body.get("right_en"),
        )
        return self.replace_cues(job_id, next_cues)

    def merge_cues(self, job_id: str, body: dict[str, Any]) -> list[dict[str, Any]] | None:
        from cue_ops import merge_cues

        job = self._jobs.get(job_id)
        if not job:
            return None
        next_cues = merge_cues(
            list(job.cues),
            cue_id_a=body.get("cue_id_a"),
            cue_id_b=body.get("cue_id_b"),
            start_a=body.get("start_a"),
            start_b=body.get("start_b"),
        )
        return self.replace_cues(job_id, next_cues)

    def list_jobs(self) -> list[JobState]:
        with self._lock:
            return list(self._jobs.values())

    def _persist(self, job: JobState) -> None:
        if job.discard_on_cancel:
            return
        try:
            from job_store import save_job_snapshot

            save_job_snapshot(job)
        except Exception:
            logger.exception("persist failed")
        # Keep durable batch manifest in sync
        bid = getattr(job, "batch_id", None)
        if bid:
            try:
                from batch_store import update_batch_job

                update_batch_job(
                    str(bid),
                    job.id,
                    status=job.status,
                    progress=job.progress,
                    cue_count=job.cue_count,
                    srt_path=job.srt_path,
                    error=job.error,
                    video_path=job.video_path,
                    audio_disagreements=(getattr(job, "audio_summary", None) or {}).get(
                        "disagreements"
                    ),
                )
            except Exception:
                logger.exception("batch persist failed")

    def _discard_snapshot(self, job: JobState) -> None:
        try:
            from job_store import delete_snapshot

            delete_snapshot(job.video_path, job.id)
        except Exception:
            logger.exception("discard snapshot failed")

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
                # Stale frontier must not look "healthy" after a jump
                job.buffer_ahead = 0.0
                job.live_lag = max(0.0, abs(job.playhead - prev))
        return True

    def cancel(self, job_id: str, *, discard: bool = True) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        if discard:
            job.discard_on_cancel = True
            self._discard_snapshot(job)
        terminal = job.status in {"done", "error", "cancelled"}
        # Unstick pause loop so cancel is noticed immediately
        job.paused = False
        job._cancel.set()
        job._seek_event.set()
        if terminal:
            # Worker already finished — finalize here
            job.status = "cancelled"
            if not discard:
                self._persist(job)
            self._emit(job, {"type": "status", "status": "cancelled", "progress": job.progress})
        return True

    def remove(self, job_id: str) -> bool:
        """Cancel if active and always discard recovery snapshot."""
        return self.cancel(job_id, discard=True)

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
        wall_t0 = time.perf_counter()

        try:
            warm_ocr()
            # Live: defer Argos/NLLB warm until first dialogue — saves cold-start wall
            if not job.live_mode:
                warm_translate(job.engine_id)

            video = Path(job.video_path)
            duration = probe_duration(video)
            frame_w, frame_h = probe_size(video)
            job.duration = duration
            writer = SrtWriter(Path(job.srt_path))

            gloss = glossary_for_video(str(video))
            job.series_id = gloss.series_id
            dial_ctx = DialogueContext(window=6, glossary=gloss, engine_id=job.engine_id)
            buffer = DialogueBuffer(window_s=job.context_window_s)
            perf = PerformanceScheduler(live_mode=job.live_mode)
            job.cue_records = []
            self._persist(job)

            # Prefer user-confirmed crop; Live skips auto-detect; Quality uses confirmable auto
            confirmed = get_confirmed_region(video)
            region_meta: ConfirmableRegion | None = None
            if confirmed and confirmed.confirmed:
                region = confirmed.region
                region_meta = confirmed
            elif job.live_mode:
                region = SubtitleRegion(
                    x=0.06, y=1.0 - band_ratio, w=0.88, h=band_ratio
                ).clamp()
                region_meta = ConfirmableRegion(
                    region=region,
                    source="default",
                    confidence=0.35,
                    confirmed=False,
                    evidence={"live_mode": True, "needs_confirm": True},
                )
            else:
                try:
                    with job._playhead_lock:
                        ph0 = job.playhead
                    sample_t = [
                        max(0.5, ph0),
                        max(0.5, ph0 + 4),
                        max(0.5, ph0 + 12),
                        max(0.5, min(duration - 1, ph0 + 30) if duration > 2 else ph0 + 2),
                    ]
                    region_meta = detect_subtitle_region_confirmable(
                        video, sample_times=sample_t, duration=duration
                    )
                    region = region_meta.region
                except Exception as exc:
                    logger.warning("region detect failed: %s", exc)
                    region = SubtitleRegion(
                        x=0.06, y=1.0 - band_ratio, w=0.88, h=band_ratio
                    ).clamp()
                    region_meta = ConfirmableRegion(
                        region=region,
                        source="default",
                        confidence=0.3,
                        confirmed=False,
                        evidence={"error": str(exc)},
                    )
            job.region = {
                "x": region.x,
                "y": region.y,
                "w": region.w,
                "h": region.h,
                "_fw": frame_w,
                "_fh": frame_h,
            }
            self._emit(
                job,
                {
                    "type": "region",
                    "region": job.region,
                    "region_confirmable": region_meta.as_dict() if region_meta else None,
                    "series_id": job.series_id,
                    "glossary_terms": len(gloss.terms),
                    "live_mode": job.live_mode,
                },
            )

            # Live: 1.0s grid (fewer OCR); quality: 0.5s fine grid
            base_tick = 1.0 if job.live_mode else 0.5
            scale_w = _LIVE_SCALE_WIDTH if job.live_mode else _QUALITY_SCALE_WIDTH
            n_ticks = max(1, int(duration / base_tick) + 1)
            done: set[int] = set()
            slot_text: dict[int, str] = {}
            slot_conf: dict[int, float] = {}
            emitted_runs: set[tuple[int, int, str]] = set()
            cue_serial = 0
            timings: list[float] = []
            interval = max(_MIN_INTERVAL, min(_MAX_INTERVAL, job.sample_interval))
            lookahead = base_lookahead
            last_zh = ""
            blank_streak = 0
            dialogue_streak = 0
            lag_ema = 0.0
            seek_until = 0.0
            last_playhead = job.playhead
            last_playhead_wall = time.perf_counter()
            translate_warmed = not job.live_mode
            # Live cold-start: dense ticks until a small buffer exists (≤30s Ready)
            live_priming = job.live_mode

            with tempfile.TemporaryDirectory(prefix="subparty_") as tmp:
                tmp_dir = Path(tmp)

                while not job._cancel.is_set():
                    if len(done) >= n_ticks:
                        break

                    # Pause support — park until resumed or cancelled
                    while job.paused and not job._cancel.is_set():
                        job.status = "paused"
                        self._persist(job)
                        job._pause.wait(timeout=0.5)
                        job._pause.clear()
                    if job._cancel.is_set():
                        break
                    if job.status == "paused":
                        job.status = "running"
                        self._emit(job, {"type": "status", "status": "running", "progress": job.progress})

                    seeked = job._seek_event.is_set()
                    job._seek_event.clear()
                    if seeked:
                        seek_until = time.perf_counter() + _SEEK_COOLDOWN_S
                        # Drop ALL unfinished backlog behind new playhead — retarget now
                        with job._playhead_lock:
                            ph_now = job.playhead
                        ph_tick0 = max(0, int(ph_now / base_tick))
                        for s in range(0, ph_tick0):
                            if s not in done:
                                done.add(s)
                                slot_text.setdefault(s, "")
                                slot_conf.setdefault(s, 0.0)
                        lag_ema = 0.0  # reset lag estimate after seek
                        if job.live_mode:
                            live_priming = True  # dense re-buffer around new playhead

                    with job._playhead_lock:
                        playhead = job.playhead

                    wall = time.perf_counter()
                    dt_wall = max(1e-3, wall - last_playhead_wall)
                    playhead_vel = (playhead - last_playhead) / dt_wall  # ~1.0 when watching 1×
                    last_playhead = playhead
                    last_playhead_wall = wall
                    scrubbing = abs(playhead_vel) > 2.5

                    # Adaptive interval from recent OCR cost + lag + dialogue density
                    avg_s = (sum(timings) / len(timings)) if timings else interval
                    lag = job.live_lag
                    lag_ema = 0.65 * lag_ema + 0.35 * lag if timings else lag
                    in_seek = wall < seek_until
                    perf.live_lag = lag_ema
                    perf.buffer_ahead = job.buffer_ahead
                    perf.avg_slot_s = avg_s
                    perf.live_mode = job.live_mode
                    min_iv, max_iv = perf.ocr_interval_bounds()
                    if job.high_accuracy and not job.live_mode and perf.mode() != "catch_up":
                        min_iv = min(min_iv, _MIN_INTERVAL_ACCURACY)

                    # Live refill: keep sampling densely while buffer_ahead is thin at 1×
                    live_refill = (
                        job.live_mode
                        and not in_seek
                        and not scrubbing
                        and job.buffer_ahead < _LIVE_BUFFER_REFILL_S
                    )
                    if (
                        (live_priming and job.buffer_ahead < _LIVE_BUFFER_TARGET_S)
                        or live_refill
                    ) and not in_seek and not scrubbing:
                        # Dense contiguous samples until Ready / refill target
                        interval = base_tick
                        lookahead = max(base_lookahead, _DEFAULT_LOOKAHEAD_LIVE)
                        stride_ticks = 1
                    elif in_seek or lag_ema > 3.5 or scrubbing:
                        interval = min(max_iv, max(avg_s * 1.15, 1.5 if job.live_mode else 1.2))
                        lookahead = 3.0 if scrubbing else (6.0 if job.live_mode else 5.0)
                        stride_ticks = max(1, int(round(interval / base_tick)))
                    elif lag_ema > 1.0:
                        interval = min(max_iv, max(avg_s * 1.05, 1.0 if job.live_mode else 0.95))
                        # Live: still look ahead while catching up — do not shrink window
                        lookahead = (
                            max(_LIVE_BUFFER_TARGET_S, base_lookahead)
                            if job.live_mode
                            else max(4.0, base_lookahead * 0.65)
                        )
                        stride_ticks = max(1, int(round(interval / base_tick)))
                    elif lag_ema < -1.5 and dialogue_streak > 0 and not job.live_mode:
                        interval = max(min_iv, min(avg_s * 0.8, 0.7))
                        lookahead = min(20.0, base_lookahead * 1.4)
                        stride_ticks = max(1, int(round(interval / base_tick)))
                    elif lag_ema < -1.0 and not job.live_mode:
                        interval = max(min_iv, min(avg_s * 0.9, 0.9))
                        lookahead = min(16.0, base_lookahead * 1.2)
                        stride_ticks = max(1, int(round(interval / base_tick)))
                    else:
                        if job.live_mode:
                            # Prefer staying ahead of 1× playhead even when OCR is slower than realtime
                            target = max(min(avg_s * 0.95, 1.0), 0.85)
                            lookahead = max(base_lookahead, _DEFAULT_LOOKAHEAD_LIVE)
                        else:
                            target = max(avg_s * 1.02, 0.5 if avg_s < 0.55 else 0.7)
                            lookahead = base_lookahead
                        interval = max(min_iv, min(max_iv, target))
                        stride_ticks = max(1, int(round(interval / base_tick)))

                    if not live_priming and not live_refill:
                        if blank_streak >= 2:
                            stride_ticks = max(
                                stride_ticks,
                                int(round((1.5 if job.live_mode else 1.4) / base_tick)),
                            )
                        if blank_streak >= 5 and not job.live_mode:
                            stride_ticks = max(
                                stride_ticks,
                                int(round(2.2 / base_tick)),
                            )
                        # Live: never stride >2s while buffer is only moderately healthy
                        if job.live_mode and job.buffer_ahead < _LIVE_BUFFER_TARGET_S:
                            stride_ticks = min(stride_ticks, max(1, int(round(1.2 / base_tick))))
                    if (
                        dialogue_streak >= 2
                        and lag_ema < 0.8
                        and not in_seek
                        and not job.live_mode
                    ):
                        stride_ticks = max(1, min(stride_ticks, int(round(0.55 / base_tick))))

                    job.sample_interval = stride_ticks * base_tick

                    # Transition-aware densify only in quality mode
                    tick = None
                    if perf.should_dense_transitions() and job.high_accuracy and done:
                        last_done = max(done)
                        for cand in transition_ticks(slot_text, done, around=last_done, radius=3):
                            ph = max(0, int(playhead / base_tick))
                            if abs(cand - ph) <= int(lookahead / base_tick) + 2:
                                tick = cand
                                break

                    # Only "lagging" when actually behind / seeking — NOT always after priming.
                    # Always-lagging after Ready was snapping to playhead and never building ahead.
                    actually_lagging = (
                        lag_ema > 0.8
                        or in_seek
                        or scrubbing
                        or (job.live_mode and job.buffer_ahead <= 0.05 and lag_ema > 0.2)
                    )
                    if tick is None:
                        tick = _pick_next_tick(
                            done=done,
                            n_ticks=n_ticks,
                            base_tick=base_tick,
                            playhead=playhead,
                            lookahead=lookahead,
                            stride_ticks=stride_ticks,
                            lagging=actually_lagging,
                            near_window_s=(
                                3.0
                                if (live_priming or live_refill)
                                else (
                                    2.0
                                    if job.live_mode
                                    else (1.4 if (dialogue_streak or lag_ema < 0.5) else 0.7)
                                )
                            ),
                        )
                    if tick is None:
                        break

                    # If a seek arrived while we were picking, drop this tick and retarget
                    if job._seek_event.is_set():
                        continue

                    t = min(duration, tick * base_tick)
                    frame_path = tmp_dir / f"frame_{tick:07d}.png"
                    t0 = time.perf_counter()
                    try:
                        extract_region(
                            video,
                            t,
                            frame_path,
                            x_ratio=region.x,
                            y_ratio=region.y,
                            w_ratio=region.w,
                            h_ratio=region.h,
                            height=frame_h,
                            width=frame_w,
                            scale_width=scale_w,
                        )
                        # Seek mid-extract: discard result and retarget
                        if job._seek_event.is_set():
                            continue
                        filtered = filter_ocr_result(ocr_lines(str(frame_path)))
                        zh = filtered.text
                        conf = filtered.confidence
                    except Exception as exc:
                        logger.warning("frame @%.2fs failed: %s", t, exc)
                        zh = ""
                        conf = 0.0
                    elapsed = time.perf_counter() - t0
                    timings.append(elapsed)
                    if len(timings) > 30:
                        timings = timings[-30:]

                    # Seek during OCR: do not commit this tick (wrong region)
                    if job._seek_event.is_set():
                        continue

                    done.add(tick)
                    slot_text[tick] = zh
                    slot_conf[tick] = conf if zh else 0.0
                    # Live stride: fill intentional holes so buffer_ahead advances contiguously
                    if job.live_mode and stride_ticks > 1:
                        for s in range(max(0, tick - stride_ticks + 1), tick):
                            if s not in done:
                                done.add(s)
                                slot_text.setdefault(s, zh if zh else "")
                                slot_conf.setdefault(s, conf if zh else 0.0)
                    # When lagging / seeking / live catch-up: skip unfinished ticks behind playhead
                    if lag_ema > 1.2 or in_seek or (job.live_mode and lag_ema > 0.5):
                        ph_tick = max(0, int(playhead / base_tick))
                        skip_back = ph_tick if in_seek else int((4.0 if job.live_mode else 2.0) / base_tick)
                        for s in range(max(0, ph_tick - skip_back), ph_tick):
                            if s not in done:
                                done.add(s)
                                slot_text.setdefault(s, "")
                                slot_conf.setdefault(s, 0.0)

                    if zh:
                        blank_streak = 0
                        dialogue_streak = min(8, dialogue_streak + 1)
                        if not translate_warmed:
                            try:
                                warm_translate(job.engine_id)
                            except Exception as exc:
                                logger.warning("deferred translate warm failed: %s", exc)
                            translate_warmed = True
                    else:
                        blank_streak += 1
                        dialogue_streak = max(0, dialogue_streak - 1)
                    if zh and zh == last_zh:
                        # Same dialogue — fill nearby unfinished ticks cheaply
                        fill_to = stride_ticks + (1 if dialogue_streak >= 2 else 0)
                        for s in range(tick + 1, min(n_ticks, tick + fill_to)):
                            if s not in done:
                                done.add(s)
                                slot_text[s] = zh
                                slot_conf[s] = conf
                    last_zh = zh

                    job.current_time = t
                    covered = max((s for s in done), default=0) * base_tick
                    job.progress = min(1.0, covered / duration) if duration > 0 else 1.0

                    cue_serial = self._emit_merged_runs(
                        job,
                        writer,
                        slot_text,
                        slot_conf,
                        done,
                        base_tick,
                        duration,
                        emitted_runs,
                        cue_serial,
                        dial_ctx,
                        buffer,
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
                    if live_priming and job.buffer_ahead >= _LIVE_BUFFER_TARGET_S:
                        live_priming = False
                    observed_until = max(playhead, frontier_t, t)

                    # Retroactive refine when scheduler allows (deferred on live path)
                    if perf.should_run_refine() and job.cue_records:
                        changed = refine_batch(
                            job.cue_records,
                            dial_ctx=dial_ctx,
                            buffer=buffer,
                            limit=1 if job.live_mode else (4 if perf.mode() == "refine" else 2),
                            observed_until=observed_until,
                        )
                        for rec in changed:
                            self._publish_record(job, writer, rec)

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
                            "series_id": job.series_id,
                            "region": job.region,
                            "seek_boost": in_seek,
                            "scheduler_mode": perf.mode(),
                            "context_window_s": job.context_window_s,
                            "live_mode": job.live_mode,
                            "context_buffer": buffer.as_dict(),
                        },
                    )

                    if job._seek_event.is_set():
                        continue

            if job._cancel.is_set():
                job.status = "cancelled"
                if job.discard_on_cancel:
                    self._discard_snapshot(job)
                else:
                    self._persist(job)
                self._emit(job, {"type": "status", "status": "cancelled", "progress": job.progress})
                return

            # EOF: full-buffer refine (quality always; live light pass only)
            if job.cue_records and (not job.live_mode or job.high_accuracy):
                for rec in finalize_cues_at_eof(
                    job.cue_records,
                    dial_ctx=dial_ctx,
                    buffer=buffer,
                    observed_until=duration,
                ):
                    self._publish_record(job, writer, rec)

            # Phase B: audio verification only off live path (Quality mode)
            if job.audio_verify and not job.live_mode and job.cue_records:
                if not whisper_available():
                    job.audio_summary = {
                        "ok": False,
                        "error": "faster-whisper not installed",
                        "disagreements": 0,
                    }
                    self._emit(job, {"type": "audio", "audio": job.audio_summary})
                else:
                    self._emit(
                        job,
                        {
                            "type": "status",
                            "status": "running",
                            "progress": job.progress,
                            "note": "audio-verify",
                        },
                    )
                    try:

                        def _tr(zh: str) -> str:
                            return dial_ctx.translate(zh, compare_candidates=True) or zh

                        summary = verify_cue_records(
                            job.cue_records,
                            video_path=job.video_path,
                            translate_fn=_tr,
                        )
                        job.audio_summary = summary
                        for rec in job.cue_records:
                            self._publish_record(job, writer, rec)
                        speech = summary.get("speech_align") or {}
                        self._emit(
                            job,
                            {
                                "type": "audio",
                                "audio": {
                                    "ok": summary.get("ok"),
                                    "disagreements": summary.get("disagreements"),
                                    "segments": summary.get("segments"),
                                    "model_size": summary.get("model_size"),
                                    "engine": summary.get("engine"),
                                    "speech_aligned": speech.get("aligned", 0),
                                },
                            },
                        )
                    except Exception as exc:
                        logger.exception("audio verify failed")
                        job.audio_summary = {"ok": False, "error": str(exc), "disagreements": 0}
                        self._emit(job, {"type": "audio", "audio": job.audio_summary})

            processing_s = time.perf_counter() - wall_t0
            rtf = realtime_factor(processing_s, duration)
            job.status = "done"
            job.progress = 1.0
            self._persist(job)
            self._emit(
                job,
                {
                    "type": "done",
                    "status": "done",
                    "progress": 1.0,
                    "cue_count": job.cue_count,
                    "srt_path": job.srt_path,
                    "processing_s": round(processing_s, 3),
                    "rtf": round(rtf, 4) if rtf is not None else None,
                    "live_latency_s": round(job.live_lag, 3),
                    "context_window_s": job.context_window_s,
                    "live_mode": job.live_mode,
                    "audio_verify": job.audio_verify,
                    "audio": job.audio_summary or None,
                },
            )
        except Exception as exc:
            logger.exception("job failed")
            job.status = "error"
            job.error = str(exc)
            self._persist(job)
            self._emit(job, {"type": "error", "status": "error", "error": str(exc)})

    def _emit_merged_runs(
        self,
        job: JobState,
        writer: SrtWriter,
        slot_text: dict[int, str],
        slot_conf: dict[int, float],
        done: set[int],
        interval: float,
        duration: float,
        emitted_runs: set[tuple[int, int, str]],
        serial: int,
        dial_ctx: DialogueContext,
        buffer: DialogueBuffer,
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
                rough_start = s0 * interval
                rough_end = min(duration, (s1 + 1) * interval)
                confs = [slot_conf.get(s, 1.0) for s in range(s0, s1 + 1) if slot_text.get(s) == zh]
                ocr_conf = min(confs) if confs else 1.0

                fine_cache: dict[float, tuple[str, float]] = {}

                def _probe(t: float) -> tuple[str, float]:
                    key_t = round(t, 4)
                    if key_t in fine_cache:
                        return fine_cache[key_t]
                    # Prefer already-known coarse slots
                    s_near = int(round(t / interval))
                    if s_near in done and abs(s_near * interval - t) < 1e-6:
                        fine_cache[key_t] = (slot_text.get(s_near) or "", float(slot_conf.get(s_near) or 0.0))
                        return fine_cache[key_t]
                    try:
                        frame_path = Path(tempfile.gettempdir()) / f"sp_fine_{job.id[:8]}_{key_t:.3f}.png"
                        extract_region(
                            Path(job.video_path),
                            t,
                            frame_path,
                            x_ratio=float((job.region or {}).get("x", 0.05)),
                            y_ratio=float((job.region or {}).get("y", 0.78)),
                            w_ratio=float((job.region or {}).get("w", 0.9)),
                            h_ratio=float((job.region or {}).get("h", 0.18)),
                            height=int((job.region or {}).get("_fh", 720) or 720),
                            width=int((job.region or {}).get("_fw", 1280) or 1280),
                            scale_width=640,
                        )
                        filtered = filter_ocr_result(ocr_lines(str(frame_path)))
                        fine_cache[key_t] = (filtered.text, float(filtered.confidence or 0.0))
                        try:
                            frame_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                    except Exception as exc:
                        logger.debug("fine probe @%.3f failed: %s", t, exc)
                        fine_cache[key_t] = ("", 0.0)
                    return fine_cache[key_t]

                do_fine = bool(job.high_accuracy and not job.live_mode)
                timing = refine_boundaries(
                    zh=zh,
                    rough_start=rough_start,
                    rough_end=rough_end,
                    slot_text=slot_text,
                    slot_conf=slot_conf,
                    base_tick=interval,
                    duration=duration,
                    high_accuracy=do_fine,
                    fine_step_s=DEFAULT_FINE_STEP_S,
                    probe_zh=_probe if do_fine else None,
                )
                # Extend existing record for same ZH near start, else provisional new
                existing = next(
                    (
                        r
                        for r in job.cue_records
                        if r.text_zh == zh and abs(r.start - timing.start) < 0.6
                    ),
                    None,
                )
                if existing:
                    # Timing extend must NOT reset maturity / permanently skip refine
                    existing.revise(
                        start=timing.start,
                        end=timing.end,
                        ocr_confidence=ocr_conf,
                        timing_confidence=timing.confidence,
                        timing_evidence=timing.evidence,
                        reason="timing-extend",
                        maturity=existing.maturity if existing.maturity != "final" else "context-refined",
                    )
                    buffer.add(
                        existing.start,
                        existing.text_zh,
                        en=existing.text_en,
                        cue_id=existing.cue_id,
                        ocr_confidence=ocr_conf,
                    )
                    self._publish_record(job, writer, existing)
                else:
                    prior, upcoming = buffer.context_zh(timing.start)
                    try:
                        # Live: single-candidate translate (faster); Quality: compare
                        en = (
                            dial_ctx.translate(
                                zh,
                                upcoming=(
                                    upcoming[:6]
                                    if job.live_mode
                                    else (upcoming[:12] or _upcoming_zh(slot_text, done, s1, limit=4))
                                ),
                                prior_override=prior[-12:] if job.live_mode else (prior[-24:] or None),
                                compare_candidates=not job.live_mode,
                            )
                            or zh
                        )
                    except Exception as exc:
                        logger.warning("translate failed, keeping ZH: %s", exc)
                        en = zh
                    record = CueRecord.new(
                        text_zh=zh,
                        start=timing.start,
                        end=timing.end,
                        text_en=en,
                        ocr_confidence=ocr_conf,
                        timing_confidence=timing.confidence,
                        reason="provisional",
                        timing_evidence=timing.evidence,
                        provisional=True,
                        future_horizon_s=job.context_window_s,
                    )
                    job.cue_records.append(record)
                    buffer.add(
                        record.start,
                        record.text_zh,
                        en=record.text_en,
                        cue_id=record.cue_id,
                        ocr_confidence=ocr_conf,
                    )
                    self._publish_record(job, writer, record)
                emitted_runs.add(key)
                serial += 1
            i = j
        return serial

    def _publish_record(self, job: JobState, writer: SrtWriter, record: CueRecord) -> None:
        """Push latest cue state to SRT (approved EN preferred) + SSE."""
        # Final SRT uses latest approved EN when available; live may still be provisional
        en_for_srt = record.latest_approved_en() if record.approved else record.text_en
        conf = min(record.ocr_confidence, record.timing_confidence)
        cue = Cue(
            index=0,
            start=record.start,
            end=record.end,
            text_zh=record.text_zh,
            text_en=en_for_srt,
            confidence=conf,
            uncertain=conf < UNCERTAIN_CONFIDENCE or record.provisional,
        )
        stored = writer.upsert_and_rewrite(cue)
        payload = record.as_payload(index=stored.index)
        payload["text_en"] = record.text_en  # live shows current (may be provisional)
        payload["srt_en"] = en_for_srt
        job.cues = merge_cue_payloads(job.cues, payload)
        job.cue_count = len(job.cues)
        for c in job.cues:
            if c.get("cue_id") == record.cue_id or (
                abs(float(c["start"]) - record.start) < 1e-6 and c.get("text_zh") == record.text_zh
            ):
                payload = dict(c)
                break
        self._emit(
            job,
            {
                "type": "cue",
                "cue": payload,
                "cue_count": job.cue_count,
                "srt_path": job.srt_path,
                "provisional": record.provisional,
            },
        )


def _pick_next_tick(
    *,
    done: set[int],
    n_ticks: int,
    base_tick: float,
    playhead: float,
    lookahead: float,
    stride_ticks: int,
    lagging: bool,
    near_window_s: float = 0.8,
) -> int | None:
    """
    Playhead-first picker.
    When lagging: jump to playhead (skip backlog). When keeping up: densify near-now,
    then walk forward by stride. Background fill only when live window is covered.
    """
    if len(done) >= n_ticks:
        return None

    ph = max(0, int(playhead / base_tick))
    ahead_end = min(n_ticks - 1, int((playhead + lookahead) / base_tick))
    stride = max(1, stride_ticks)
    near_end = min(ahead_end, ph + max(1, int(near_window_s / base_tick)))

    if lagging:
        # Snap to playhead — do not crawl missed ticks behind
        if ph < n_ticks and ph not in done:
            return ph
        for s in range(ph, min(n_ticks, ph + stride * 4), stride):
            if s not in done:
                return s

    # Dense holes in the immediate near-now window (dialogue cuts)
    for s in range(ph, near_end + 1):
        if s not in done:
            return s

    # Live window: forward from playhead by stride
    s = ph - (ph % stride) if ph % stride else ph
    if s < ph:
        s += stride
    while s <= ahead_end:
        if s not in done:
            return s
        s += stride

    # Any remaining hole in live window
    for s in range(near_end + 1, ahead_end + 1):
        if s not in done:
            return s

    # Further ahead (pre-buffer) — coarse stride
    s = ahead_end + 1
    while s < n_ticks:
        if s not in done:
            return s
        s += max(stride, int(1.5 / base_tick))

    # Behind playhead only when live buffer is healthy
    if not lagging:
        for s in range(0, ph, max(stride, int(2.0 / base_tick))):
            if s not in done:
                return s
        for s in range(0, ph):
            if s not in done:
                return s

    return None


def _upcoming_zh(
    slot_text: dict[int, str],
    done: set[int],
    after_slot: int,
    *,
    limit: int = 2,
) -> list[str]:
    """Next distinct non-empty ZH lines already sampled after the current run."""
    out: list[str] = []
    seen: set[str] = set()
    for s in sorted(done):
        if s <= after_slot:
            continue
        zh = (slot_text.get(s) or "").strip()
        if not zh or zh in seen:
            continue
        seen.add(zh)
        out.append(zh)
        if len(out) >= limit:
            break
    return out


jobs = JobManager()
