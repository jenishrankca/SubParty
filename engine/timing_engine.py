"""Independent timing engine — high-accuracy boundaries from dense sample evidence."""

from __future__ import annotations

from dataclasses import dataclass

from revision import TimingEvidence


@dataclass
class TimingResult:
    start: float
    end: float
    confidence: float
    evidence: TimingEvidence


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
) -> TimingResult:
    """
    Walk sample evidence around a dialogue run and snap start/end to first/last
    presence of the same ZH (or near-identical). Optionally densify conceptually
    by using half-tick neighbors already in the grid.
    """
    zh = (zh or "").strip()
    if not zh:
        return TimingResult(
            start=rough_start,
            end=max(rough_start + 0.04, rough_end),
            confidence=0.0,
            evidence=TimingEvidence(method="empty"),
        )

    # Collect ticks where this text (or substring-close) is present
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
        hit = bool(txt) and (txt == zh or zh in txt or txt in zh)
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

    # Confidence: denser consistent hits → higher; gaps inside run → lower
    span_ticks = max(1, present[-1] - present[0] + 1)
    density = len(present) / span_ticks
    edge_pad = 0.0
    # If samples immediately outside are clearly empty, boost confidence
    before = present[0] - 1
    after = present[-1] + 1
    if before >= 0 and not (slot_text.get(before) or "").strip():
        edge_pad += 0.08
    if not (slot_text.get(after) or "").strip():
        edge_pad += 0.08
    confs = [slot_conf.get(s, 0.8) for s in present]
    mean_ocr = sum(confs) / len(confs) if confs else 0.7
    confidence = max(0.05, min(1.0, 0.45 * density + 0.4 * mean_ocr + edge_pad))

    ev = TimingEvidence(
        sample_times=times,
        present_flags=flags,
        boundary_refined=True,
        method="high-accuracy" if high_accuracy else "standard",
    )
    return TimingResult(start=start, end=end, confidence=confidence, evidence=ev)


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
        # Prefer neighbors when adjacent known text differs
        left = (slot_text.get(s - 1) or "").strip() if s - 1 in done else None
        right = (slot_text.get(s + 1) or "").strip() if s + 1 in done else None
        if left is not None and right is not None and left != right:
            out.append(s)
        elif left is not None and left != center_zh:
            out.append(s)
        elif right is not None and right != center_zh:
            out.append(s)
    return out
