"""Configurable rolling dialogue buffer (default 120s) for contextual translate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BufferLine:
    t: float
    zh: str
    en: str = ""
    cue_id: str | None = None
    ocr_confidence: float = 1.0


@dataclass
class DialogueBuffer:
    """Time-windowed ZH/EN memory for translation context and retroactive refine."""

    window_s: float = 120.0
    lines: list[BufferLine] = field(default_factory=list)

    def add(
        self,
        t: float,
        zh: str,
        *,
        en: str = "",
        cue_id: str | None = None,
        ocr_confidence: float = 1.0,
    ) -> None:
        zh = (zh or "").strip()
        if not zh:
            return
        # Replace same cue_id or near-duplicate at same time
        if cue_id:
            self.lines = [ln for ln in self.lines if ln.cue_id != cue_id]
        else:
            self.lines = [
                ln
                for ln in self.lines
                if not (abs(ln.t - t) < 0.35 and ln.zh == zh)
            ]
        self.lines.append(
            BufferLine(t=t, zh=zh, en=en, cue_id=cue_id, ocr_confidence=ocr_confidence)
        )
        self.lines.sort(key=lambda ln: ln.t)
        self._trim(t)

    def update_en(self, cue_id: str, en: str) -> None:
        for ln in self.lines:
            if ln.cue_id == cue_id:
                ln.en = en

    def _trim(self, now: float) -> None:
        lo = now - self.window_s
        hi = now + self.window_s
        self.lines = [ln for ln in self.lines if lo <= ln.t <= hi]

    def prior(self, t: float, *, limit: int | None = None) -> list[BufferLine]:
        prior = [ln for ln in self.lines if ln.t < t - 0.05]
        if limit is not None:
            return prior[-limit:]
        return prior

    def upcoming(self, t: float, *, limit: int | None = None) -> list[BufferLine]:
        up = [ln for ln in self.lines if ln.t > t + 0.05]
        if limit is not None:
            return up[:limit]
        return up

    def context_zh(self, t: float) -> tuple[list[str], list[str]]:
        """All ZH in the rolling window split into prior / upcoming around t."""
        prior = [ln.zh for ln in self.prior(t)]
        upcoming = [ln.zh for ln in self.upcoming(t)]
        return prior, upcoming

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_s": self.window_s,
            "count": len(self.lines),
            "span": (
                (self.lines[-1].t - self.lines[0].t) if len(self.lines) >= 2 else 0.0
            ),
        }
