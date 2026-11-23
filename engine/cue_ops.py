"""Stage 6 — split / merge cue operations (pure; preserve evidence fields)."""

from __future__ import annotations

import uuid
from typing import Any


def _renumber(cues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = sorted(cues, key=lambda c: float(c.get("start") or 0))
    for i, c in enumerate(out, start=1):
        c["index"] = i
    return out


def find_cue_index(cues: list[dict[str, Any]], *, cue_id: str | None = None, start: float | None = None) -> int:
    if cue_id:
        for i, c in enumerate(cues):
            if str(c.get("cue_id") or "") == cue_id:
                return i
    if start is not None:
        best_i = -1
        best_d = 1e9
        for i, c in enumerate(cues):
            d = abs(float(c.get("start") or 0) - float(start))
            if d < best_d:
                best_d = d
                best_i = i
        if best_i >= 0 and best_d < 0.35:
            return best_i
    return -1


def split_cue(
    cues: list[dict[str, Any]],
    *,
    cue_id: str | None = None,
    start: float | None = None,
    at: float | None = None,
    left_zh: str | None = None,
    right_zh: str | None = None,
    left_en: str | None = None,
    right_en: str | None = None,
) -> list[dict[str, Any]]:
    """
    Split one cue into two at `at` (default midpoint).
    Text keeps original_ocr / audio fields from the parent when present.
    """
    idx = find_cue_index(cues, cue_id=cue_id, start=start)
    if idx < 0:
        raise ValueError("cue not found")
    c = dict(cues[idx])
    s = float(c["start"])
    e = float(c["end"])
    if e - s < 0.2:
        raise ValueError("cue too short to split")
    mid = float(at) if at is not None else (s + e) / 2.0
    if mid <= s + 0.05 or mid >= e - 0.05:
        raise ValueError("split point must be inside cue")

    left = dict(c)
    right = dict(c)
    left["end"] = mid
    right["start"] = mid
    left["cue_id"] = str(uuid.uuid4())
    right["cue_id"] = str(uuid.uuid4())
    if left_zh is not None:
        left["text_zh"] = left_zh
    if right_zh is not None:
        right["text_zh"] = right_zh
    if left_en is not None:
        left["text_en"] = left_en
    if right_en is not None:
        right["text_en"] = right_en
    # Mark editor provenance
    left["uncertain"] = True
    right["uncertain"] = True
    left["revision_note"] = "split-left"
    right["revision_note"] = "split-right"

    next_cues = cues[:idx] + [left, right] + cues[idx + 1 :]
    return _renumber(next_cues)


def merge_cues(
    cues: list[dict[str, Any]],
    *,
    cue_id_a: str | None = None,
    cue_id_b: str | None = None,
    start_a: float | None = None,
    start_b: float | None = None,
) -> list[dict[str, Any]]:
    """Merge two cues (by id or start) into one spanning both."""
    ia = find_cue_index(cues, cue_id=cue_id_a, start=start_a)
    ib = find_cue_index(cues, cue_id=cue_id_b, start=start_b)
    if ia < 0 or ib < 0:
        raise ValueError("one or both cues not found")
    if ia == ib:
        raise ValueError("cannot merge a cue with itself")
    if ia > ib:
        ia, ib = ib, ia
    a = dict(cues[ia])
    b = dict(cues[ib])
    merged = dict(a)
    merged["start"] = min(float(a["start"]), float(b["start"]))
    merged["end"] = max(float(a["end"]), float(b["end"]))
    merged["cue_id"] = str(uuid.uuid4())
    zh_a = str(a.get("text_zh") or "").strip()
    zh_b = str(b.get("text_zh") or "").strip()
    en_a = str(a.get("text_en") or "").strip()
    en_b = str(b.get("text_en") or "").strip()
    merged["text_zh"] = zh_a if zh_a == zh_b else " ".join(x for x in (zh_a, zh_b) if x)
    merged["text_en"] = en_a if en_a == en_b else " ".join(x for x in (en_a, en_b) if x)
    # Preserve OCR of first; concatenate ASR if both present
    ocr_a = str(a.get("original_ocr") or "").strip()
    ocr_b = str(b.get("original_ocr") or "").strip()
    if ocr_a or ocr_b:
        merged["original_ocr"] = ocr_a if ocr_a == ocr_b else " ".join(x for x in (ocr_a, ocr_b) if x)
    asr_a = str(a.get("audio_transcript") or "").strip()
    asr_b = str(b.get("audio_transcript") or "").strip()
    if asr_a or asr_b:
        merged["audio_transcript"] = asr_a if asr_a == asr_b else " ".join(x for x in (asr_a, asr_b) if x)
    merged["audio_disagreement"] = bool(a.get("audio_disagreement") or b.get("audio_disagreement"))
    merged["uncertain"] = True
    merged["revision_note"] = "merge"
    next_cues = cues[:ia] + [merged] + cues[ia + 1 : ib] + cues[ib + 1 :]
    return _renumber(next_cues)
