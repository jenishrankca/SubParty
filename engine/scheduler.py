"""Quality-based + performance schedulers for OCR / translate / refine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkItem:
    kind: str  # "ocr" | "refine" | "timing"
    priority: float  # higher = sooner
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class PerformanceScheduler:
    """
    Keep live keep-up: when lagging, favor OCR near playhead;
    when buffered, spend cycles on refine / timing of uncertain cues.
    Live mode: coarser catch-up strides; defer refine until buffer healthy.
    """

    live_lag: float = 0.0
    buffer_ahead: float = 0.0
    avg_slot_s: float = 0.8
    live_mode: bool = True

    def mode(self) -> str:
        # Live: catch up when lagging OR buffer thin — prefer feeding the overlay
        if self.live_mode and (self.live_lag > 0.6 or self.buffer_ahead < 3.0):
            return "catch_up"
        if self.live_lag > 1.0:
            return "catch_up"
        if self.buffer_ahead >= (5.0 if self.live_mode else 2.0) and self.live_lag < 0.4:
            return "refine"
        return "balanced"

    def ocr_interval_bounds(self) -> tuple[float, float]:
        """min,max sample interval for current mode."""
        mode = self.mode()
        if self.live_mode:
            # Catch-up densifies (smaller max stride) — do not jump to 3.5s holes at 1×
            if mode == "catch_up":
                return 0.8, 1.6
            if mode == "refine":
                return 0.9, 1.8
            return 0.9, 1.8
        if mode == "catch_up":
            return 0.9, 2.8
        if mode == "refine":
            return 0.45, 1.2
        return 0.5, 1.8

    def should_run_refine(self) -> bool:
        if self.live_mode:
            # Defer heavy refine off the live path until comfortably ahead
            return (
                self.mode() == "refine"
                and self.live_lag < 0.3
                and self.buffer_ahead >= 6.0
            )
        return self.mode() in {"refine", "balanced"} and self.live_lag < 1.2

    def should_dense_transitions(self) -> bool:
        if self.live_mode:
            return False
        return self.mode() != "catch_up"


def rank_uncertain_cues(cues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Quality scheduler: immature / uncertain first; high-confidence still eligible."""

    maturity_boost = {
        "provisional": 4.0,
        "context-refined": 2.0,
        "context-complete": 0.2,
        "final": 0.0,
    }

    def score(c: dict[str, Any]) -> float:
        ocr = float(c.get("ocr_confidence") or c.get("confidence") or 1.0)
        timing = float(c.get("timing_confidence") or 1.0)
        provisional = 1.0 if c.get("provisional") else 0.0
        uncertain = 1.0 if c.get("uncertain") else 0.0
        mat = str(c.get("maturity") or ("provisional" if provisional else "context-refined"))
        # Higher score = refine sooner — do NOT zero out high OCR confidence
        return (
            maturity_boost.get(mat, 1.0)
            + provisional * 2.0
            + uncertain * 2.0
            + (1.0 - ocr) * 0.5
            + (1.0 - timing)
        )

    return sorted(cues, key=score, reverse=True)
