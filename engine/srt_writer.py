"""SRT writer that supports out-of-order cue arrival (rewrite sorted)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock


@dataclass
class Cue:
    index: int
    start: float
    end: float
    text_zh: str
    text_en: str

    def to_srt_block(self) -> str:
        return (
            f"{self.index}\n"
            f"{format_ts(self.start)} --> {format_ts(self.end)}\n"
            f"{self.text_en}\n\n"
        )


def format_ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


class SrtWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self._cues: list[Cue] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def upsert_and_rewrite(self, cue: Cue) -> None:
        """Insert/replace by time range and rewrite the whole SRT sorted."""
        with self._lock:
            # Replace overlapping same-text or identical start
            self._cues = [
                c
                for c in self._cues
                if not (abs(c.start - cue.start) < 0.05 and c.text_en == cue.text_en)
            ]
            self._cues.append(cue)
            self._cues.sort(key=lambda c: (c.start, c.end))
            # Renumber for valid SRT
            blocks: list[str] = []
            for i, c in enumerate(self._cues, start=1):
                c.index = i
                blocks.append(c.to_srt_block())
            self.path.write_text("".join(blocks), encoding="utf-8")

    def append(self, cue: Cue) -> None:
        self.upsert_and_rewrite(cue)
