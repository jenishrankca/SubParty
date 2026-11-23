"""SRT writer that supports out-of-order cue arrival and overlap-safe replacement."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock


# Same dialogue if starts within this many seconds
_SAME_START_EPS = 0.12
# Treat as overlapping if ranges intersect by more than this
_MIN_OVERLAP = 0.05
# Prefer replacing when Jaccard-ish overlap ratio of intervals exceeds this
_OVERLAP_RATIO = 0.25


@dataclass
class Cue:
    index: int
    start: float
    end: float
    text_zh: str
    text_en: str
    confidence: float = 1.0
    uncertain: bool = False

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


def _overlap_seconds(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _duration(start: float, end: float) -> float:
    return max(0.0, end - start)


def cues_conflict(existing: Cue, incoming: Cue) -> bool:
    """True if incoming should replace / displace existing."""
    # Identical / near-identical start → always replace (extension or rewrite)
    if abs(existing.start - incoming.start) <= _SAME_START_EPS:
        return True
    # Same ZH dialogue with close timing (OCR tick drift)
    if existing.text_zh and existing.text_zh == incoming.text_zh:
        if abs(existing.start - incoming.start) <= 0.6:
            return True
        ov = _overlap_seconds(existing.start, existing.end, incoming.start, incoming.end)
        if ov >= _MIN_OVERLAP:
            return True
    ov = _overlap_seconds(existing.start, existing.end, incoming.start, incoming.end)
    if ov < _MIN_OVERLAP:
        return False
    shorter = min(_duration(existing.start, existing.end), _duration(incoming.start, incoming.end))
    if shorter <= 0:
        return ov > 0
    return (ov / shorter) >= _OVERLAP_RATIO


def merge_cue_payloads(existing: list[dict], incoming: dict) -> list[dict]:
    """Overlap-safe merge for in-memory job cue dicts (same rules as SrtWriter)."""
    start = float(incoming["start"])
    end = float(incoming["end"])
    zh = str(incoming.get("text_zh") or "")
    en = str(incoming.get("text_en") or "")
    probe = Cue(
        index=0,
        start=start,
        end=end,
        text_zh=zh,
        text_en=en,
        confidence=float(incoming.get("confidence") or 1.0),
        uncertain=bool(incoming.get("uncertain")),
    )
    kept: list[dict] = []
    for c in existing:
        other = Cue(
            index=0,
            start=float(c["start"]),
            end=float(c["end"]),
            text_zh=str(c.get("text_zh") or ""),
            text_en=str(c.get("text_en") or ""),
        )
        if cues_conflict(other, probe):
            continue
        kept.append(c)
    kept.append(dict(incoming))
    kept.sort(key=lambda c: (float(c["start"]), float(c["end"])))
    # Clamp residual overlaps: trim previous end to next start
    for i in range(1, len(kept)):
        prev_end = float(kept[i - 1]["end"])
        cur_start = float(kept[i]["start"])
        if prev_end > cur_start:
            kept[i - 1] = {**kept[i - 1], "end": cur_start}
    # Drop zero/negative duration leftovers
    kept = [c for c in kept if float(c["end"]) - float(c["start"]) >= 0.04]
    for i, c in enumerate(kept, start=1):
        c["index"] = i
    return kept


class SrtWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self._cues: list[Cue] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    @property
    def cues(self) -> list[Cue]:
        with self._lock:
            return list(self._cues)

    def upsert_and_rewrite(self, cue: Cue) -> Cue:
        """
        Insert/replace overlapping cues and rewrite the whole SRT sorted.
        Incoming cue wins conflicts (extensions, OCR corrections, overlaps).
        Returns the cue after renumber (index may change).
        """
        with self._lock:
            if cue.end < cue.start:
                cue.start, cue.end = cue.end, cue.start
            if cue.end - cue.start < 0.04:
                cue.end = cue.start + 0.04

            kept = [c for c in self._cues if not cues_conflict(c, cue)]
            kept.append(cue)
            kept.sort(key=lambda c: (c.start, c.end))

            # Clamp residual overlaps so SRT never stacks two active lines
            for i in range(1, len(kept)):
                if kept[i - 1].end > kept[i].start:
                    kept[i - 1].end = kept[i].start
            kept = [c for c in kept if c.end - c.start >= 0.04]

            blocks: list[str] = []
            out_cue = cue
            for i, c in enumerate(kept, start=1):
                c.index = i
                if c is cue or (
                    abs(c.start - cue.start) < 1e-6
                    and c.text_zh == cue.text_zh
                    and c.text_en == cue.text_en
                ):
                    out_cue = c
                blocks.append(c.to_srt_block())
            self._cues = kept
            self.path.write_text("".join(blocks), encoding="utf-8")
            return out_cue

    def append(self, cue: Cue) -> Cue:
        return self.upsert_and_rewrite(cue)

    def rewrite_all(self, cues: list[Cue]) -> list[Cue]:
        """Replace entire cue list and rewrite SRT (used by editor saves)."""
        with self._lock:
            cleaned = sorted(cues, key=lambda c: (c.start, c.end))
            # Clamp overlaps
            for i in range(1, len(cleaned)):
                if cleaned[i - 1].end > cleaned[i].start:
                    cleaned[i - 1].end = cleaned[i].start
            cleaned = [c for c in cleaned if c.end - c.start >= 0.04]
            blocks: list[str] = []
            for i, c in enumerate(cleaned, start=1):
                c.index = i
                blocks.append(c.to_srt_block())
            self._cues = cleaned
            self.path.write_text("".join(blocks), encoding="utf-8")
            return list(cleaned)
