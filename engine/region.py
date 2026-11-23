"""Auto-detect subtitle region anywhere in frame (not bottom-only).

Confirmable crop: auto-detect produces a ``ConfirmableRegion`` with confidence
and evidence; a user-confirmed override sticks until cleared (hooks for UI).
"""

from __future__ import annotations

import logging
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ffmpeg_util import extract_full_frame, probe_size
from ocr import ocr_lines
from text_filters import is_junk_line

logger = logging.getLogger("subparty.region")

_CONFIRM_LOCK = threading.Lock()
# video_path → user-confirmed region (sticky until clear)
_USER_CONFIRMED: dict[str, "ConfirmableRegion"] = {}


@dataclass
class SubtitleRegion:
    """Normalized crop box (0..1 of full frame)."""

    x: float = 0.06
    y: float = 0.75
    w: float = 0.88
    h: float = 0.22

    def clamp(self) -> SubtitleRegion:
        x = max(0.0, min(0.9, self.x))
        y = max(0.0, min(0.9, self.y))
        w = max(0.2, min(1.0 - x, self.w))
        h = max(0.08, min(1.0 - y, self.h))
        return SubtitleRegion(x=x, y=y, w=w, h=h)

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


@dataclass
class ConfirmableRegion:
    """Region plus provenance for UI confirm / reject / tweak."""

    region: SubtitleRegion
    source: str = "auto"  # auto | default | user
    confidence: float = 0.5
    confirmed: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "region": self.region.as_dict(),
            "source": self.source,
            "confidence": round(float(self.confidence), 3),
            "confirmed": bool(self.confirmed),
            "evidence": dict(self.evidence or {}),
        }


def default_bottom_region() -> SubtitleRegion:
    return SubtitleRegion(x=0.06, y=0.78, w=0.88, h=0.20).clamp()


def _video_key(video_path: Path | str) -> str:
    try:
        return str(Path(video_path).resolve())
    except Exception:
        return str(video_path)


def confirm_region(
    video_path: Path | str,
    region: SubtitleRegion,
    *,
    source: str = "user",
    confidence: float = 1.0,
    evidence: dict[str, Any] | None = None,
) -> ConfirmableRegion:
    """Sticky user/UI confirmation — OCR crops should prefer this until cleared."""
    conf = ConfirmableRegion(
        region=region.clamp(),
        source=source,
        confidence=float(confidence),
        confirmed=True,
        evidence=dict(evidence or {"hook": "confirm_region"}),
    )
    with _CONFIRM_LOCK:
        _USER_CONFIRMED[_video_key(video_path)] = conf
    logger.info("region confirmed for %s: %s", Path(video_path).name, conf.region.as_dict())
    return conf


def clear_confirmed_region(video_path: Path | str | None = None) -> None:
    with _CONFIRM_LOCK:
        if video_path is None:
            _USER_CONFIRMED.clear()
        else:
            _USER_CONFIRMED.pop(_video_key(video_path), None)


def get_confirmed_region(video_path: Path | str) -> ConfirmableRegion | None:
    with _CONFIRM_LOCK:
        return _USER_CONFIRMED.get(_video_key(video_path))


def resolve_region(
    video_path: Path,
    *,
    sample_times: list[float] | None = None,
    duration: float | None = None,
) -> ConfirmableRegion:
    """Prefer user-confirmed crop; else auto-detect with confidence."""
    existing = get_confirmed_region(video_path)
    if existing and existing.confirmed:
        return existing
    return detect_subtitle_region_confirmable(
        video_path, sample_times=sample_times, duration=duration
    )


def detect_subtitle_region(
    video_path: Path,
    *,
    sample_times: list[float] | None = None,
    duration: float | None = None,
) -> SubtitleRegion:
    """
    OCR a few full frames and pick the Y-band with the most dialogue-like CJK text.
    Falls back to bottom band.
    """
    try:
        width, height = probe_size(video_path)
    except Exception as exc:
        logger.warning("region probe size failed: %s", exc)
        return default_bottom_region()

    if not sample_times:
        if duration and duration > 10:
            sample_times = [
                min(duration * 0.05, 20),
                min(duration * 0.12, 60),
                min(duration * 0.25, 180),
                min(duration * 0.4, 400),
            ]
        else:
            sample_times = [1.0, 3.0, 5.0, 8.0]

    # Collect dialogue box centers (normalized y)
    ys: list[float] = []
    boxes: list[tuple[float, float, float, float]] = []  # cx, cy, w, h

    with tempfile.TemporaryDirectory(prefix="subparty_region_") as tmp:
        tmp_dir = Path(tmp)
        for i, t in enumerate(sample_times):
            frame = tmp_dir / f"full_{i}.png"
            try:
                extract_full_frame(video_path, t, frame, scale_width=640)
                for line in ocr_lines(str(frame)):
                    if not line.text or is_junk_line(line.text):
                        continue
                    # Prefer horizontally centered text (subs), skip edge watermarks
                    if line.cx < 0.15 or line.cx > 0.85:
                        continue
                    ys.append(line.cy)
                    boxes.append((line.cx, line.cy, line.w, line.h))
            except Exception as exc:
                logger.debug("region sample @%.1fs failed: %s", t, exc)

    if len(ys) < 2:
        logger.info("region detect: insufficient dialogue boxes; using bottom default")
        return default_bottom_region()

    # Histogram of y into 12 bins; pick densest bin cluster
    bins = 12
    hist = [0] * bins
    for y in ys:
        idx = min(bins - 1, max(0, int(y * bins)))
        hist[idx] += 1
    best = max(range(bins), key=lambda i: hist[i])
    # Expand to neighboring bins with counts
    lo, hi = best, best
    while lo > 0 and hist[lo - 1] >= max(1, hist[best] // 3):
        lo -= 1
    while hi < bins - 1 and hist[hi + 1] >= max(1, hist[best] // 3):
        hi += 1

    y0 = lo / bins
    y1 = (hi + 1) / bins
    # Pad
    pad = 0.03
    y0 = max(0.0, y0 - pad)
    y1 = min(1.0, y1 + pad)
    h = max(0.1, y1 - y0)

    # Horizontal span from dialogue boxes in that band
    in_band = [b for b in boxes if y0 <= b[1] <= y1]
    if in_band:
        lefts = [b[0] - b[2] / 2 for b in in_band]
        rights = [b[0] + b[2] / 2 for b in in_band]
        x0 = max(0.0, min(lefts) - 0.04)
        x1 = min(1.0, max(rights) + 0.04)
        # Keep reasonably wide
        if x1 - x0 < 0.5:
            mid = (x0 + x1) / 2
            x0 = max(0.0, mid - 0.35)
            x1 = min(1.0, mid + 0.35)
    else:
        x0, x1 = 0.06, 0.94

    region = SubtitleRegion(x=x0, y=y0, w=x1 - x0, h=h).clamp()
    logger.info(
        "region detect: y=%.2f-%.2f x=%.2f-%.2f (from %d boxes, frame %dx%d)",
        region.y,
        region.y + region.h,
        region.x,
        region.x + region.w,
        len(boxes),
        width,
        height,
    )
    return region


def detect_subtitle_region_confirmable(
    video_path: Path,
    *,
    sample_times: list[float] | None = None,
    duration: float | None = None,
) -> ConfirmableRegion:
    """Auto-detect wrapped with confidence + evidence for UI confirm hooks."""
    # Count boxes via a thin probe — reuse detect then attach metadata
    try:
        width, height = probe_size(video_path)
    except Exception:
        width, height = 0, 0

    region = detect_subtitle_region(
        video_path, sample_times=sample_times, duration=duration
    )
    is_default = (
        abs(region.y - 0.78) < 0.02
        and abs(region.h - 0.20) < 0.03
        and abs(region.x - 0.06) < 0.02
    )
    # Confidence: default band is weaker; auto with many samples is stronger
    conf = 0.35 if is_default else 0.7
    source = "default" if is_default else "auto"
    return ConfirmableRegion(
        region=region,
        source=source,
        confidence=conf,
        confirmed=False,
        evidence={
            "frame_size": {"w": width, "h": height},
            "sample_times": list(sample_times or []),
            "needs_confirm": conf < 0.6,
            "hook": "detect_subtitle_region_confirmable",
        },
    )
