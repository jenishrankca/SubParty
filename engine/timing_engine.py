"""Independent timing engine — coarse grid + frame-level edge inspection (SubParty 2.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from revision import TimingEvidence

# Default fine step for boundary inspection around transitions (sub-0.5s)
DEFAULT_FINE_STEP_S = 0.1

ProbeFn = Callable[[float], tuple[str, float]]  # t -> (zh_text, ocr_conf)


@dataclass
class TimingResult:
    start: float
    end: float
    confidence: float
    evidence: TimingEvidence


def _text_hit(txt: str, zh: str) -> bool:
    txt = (txt or "").strip()
    zh = (zh or "").strip()
    if not txt or not zh:
        return False
    return txt == zh or zh in txt or txt in zh


def refine_boundaries(
    *,
    zh: str,
    rough_start: float,
    rough_end: float,
    slot_text: dict[int, str],
    slot_conf: dict[int, float],
    base_tick: float,
    duration: float,
    high_accuracy: bool = True,
    fine_step_s: float = DEFAULT_FINE_STEP_S,
    probe_zh: ProbeFn | None = None,
) -> TimingResult:
    """
    Snap start/end using sample evidence, then (when high_accuracy) inspect
    edges at fine_step_s via optional OCR probe for sub-base_tick timing.
    """
    zh = (zh or "").strip()
    if not zh:
        return TimingResult(
            start=rough_start,
            end=max(rough_start + 0.04, rough_end),
            confidence=0.0,
            evidence=TimingEvidence(method="empty"),
        )

    present: list[int] = []
    times: list[float] = []
    flags: list[bool] = []
    s0 = max(0, int(rough_start / base_tick) - (4 if high_accuracy else 2))
    s1 = int(rough_end / base_tick) + (4 if high_accuracy else 2)
    for s in range(s0, s1 + 1):
        t = s * base_tick
        if t > duration:
            break
        txt = (slot_text.get(s) or "").strip()
        hit = _text_hit(txt, zh)
        times.append(t)
        flags.append(hit)
        if hit:
            present.append(s)

    if not present:
        ev = TimingEvidence(
            sample_times=times,
            present_flags=flags,
            boundary_refined=False,
            method="fallback-rough",
        )
        return TimingResult(
            start=rough_start,
            end=max(rough_start + 0.04, rough_end),
            confidence=0.35,
            evidence=ev,
        )

    start = present[0] * base_tick
    end = min(duration, (present[-1] + 1) * base_tick)
    method = "high-accuracy" if high_accuracy else "standard"
    fine_used = False

    # Frame-level edges require a live OCR probe — slot interpolation alone
    # would mis-map fractional times back onto the coarse grid.
    if high_accuracy and probe_zh is not None and fine_step_s > 0 and fine_step_s < base_tick:
        start, end, fine_times, fine_flags = _frame_level_edges(
            zh=zh,
            coarse_start=start,
            coarse_end=end,
            base_tick=base_tick,
            fine_step_s=fine_step_s,
            duration=duration,
            slot_text=slot_text,
            probe_zh=probe_zh,
        )
        if fine_times:
            times.extend(fine_times)
            flags.extend(fine_flags)
            fine_used = True
            method = "frame-level"

    # Confidence: denser consistent hits → higher; gaps inside run → lower
    span_ticks = max(1, present[-1] - present[0] + 1)
    density = len(present) / span_ticks
    edge_pad = 0.0
    before = present[0] - 1
    after = present[-1] + 1
    if before >= 0 and not (slot_text.get(before) or "").strip():
        edge_pad += 0.08
    if not (slot_text.get(after) or "").strip():
        edge_pad += 0.08
    if fine_used:
        edge_pad += 0.06
    confs = [slot_conf.get(s, 0.8) for s in present]
    mean_ocr = sum(confs) / len(confs) if confs else 0.7
    confidence = max(0.05, min(1.0, 0.45 * density + 0.4 * mean_ocr + edge_pad))

    ev = TimingEvidence(
        sample_times=times,
        present_flags=flags,
        boundary_refined=True,
        method=method,
        fine_step_s=fine_step_s if fine_used else None,
    )
    return TimingResult(start=start, end=end, confidence=confidence, evidence=ev)


def _frame_level_edges(
    *,
    zh: str,
    coarse_start: float,
    coarse_end: float,
    base_tick: float,
    fine_step_s: float,
    duration: float,
    slot_text: dict[int, str],
    probe_zh: ProbeFn | None,
) -> tuple[float, float, list[float], list[bool]]:
    """
    Inspect ±1 coarse tick around start/end at fine_step_s.
    Uses probe_zh when provided; otherwise interpolates from neighboring slots
    (still records fine timeline for evidence — presence only where slots known).
    """
    fine_times: list[float] = []
    fine_flags: list[bool] = []

    def presence_at(t: float) -> bool:
        if probe_zh is not None:
            txt, _conf = probe_zh(t)
            hit = _text_hit(txt, zh)
            fine_times.append(t)
            fine_flags.append(hit)
            return hit
        # Fallback without live OCR: use nearest slot samples
        s = int(round(t / base_tick))
        hit = _text_hit(slot_text.get(s) or "", zh)
        fine_times.append(t)
        fine_flags.append(hit)
        return hit

    # Start: walk forward from coarse_start - base_tick until first hit
    search_lo = max(0.0, coarse_start - base_tick)
    search_hi = min(duration, coarse_start + base_tick)
    start = coarse_start
    t = search_lo
    first_hit: float | None = None
    while t <= search_hi + 1e-9:
        if presence_at(round(t, 4)):
            first_hit = round(t, 4)
            break
        t += fine_step_s
    if first_hit is not None:
        start = first_hit

    # End: walk backward from coarse_end + base_tick until last hit
    search_lo_e = max(0.0, coarse_end - base_tick)
    search_hi_e = min(duration, coarse_end + base_tick)
    end = coarse_end
    last_hit: float | None = None
    t = search_hi_e
    while t >= search_lo_e - 1e-9:
        if presence_at(round(t, 4)):
            last_hit = round(t, 4)
            break
        t -= fine_step_s
    if last_hit is not None:
        # Inclusive end: add one fine step so cue covers the last visible frame
        end = min(duration, last_hit + fine_step_s)

    if end <= start:
        end = min(duration, start + fine_step_s)
    return start, end, fine_times, fine_flags


def transition_ticks(
    slot_text: dict[int, str],
    done: set[int],
    *,
    around: int,
    radius: int = 3,
) -> list[int]:
    """Ticks near a text change that deserve denser OCR (not yet done)."""
    center_zh = (slot_text.get(around) or "").strip()
    out: list[int] = []
    for s in range(max(0, around - radius), around + radius + 1):
        if s in done:
            continue
        left = (slot_text.get(s - 1) or "").strip() if s - 1 in done else None
        right = (slot_text.get(s + 1) or "").strip() if s + 1 in done else None
        if left is not None and right is not None and left != right:
            out.append(s)
        elif left is not None and left != center_zh:
            out.append(s)
        elif right is not None and right != center_zh:
            out.append(s)
    return out
