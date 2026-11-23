"""
Stage 5 — speech-aligned timing.

Uses ASR segment bounds to refine cue start/end when speech overlaps the OCR window.
Never invents cues from audio alone; preserves OCR text unless caller also reconciles ZH.
"""

from __future__ import annotations

import logging
from typing import Any

from audio_verify import AudioSegment, char_similarity, normalize_zh

logger = logging.getLogger("subparty.speech_align")


def overlapping_segments(
    segments: list[AudioSegment],
    start: float,
    end: float,
    *,
    pad_s: float = 0.45,
) -> list[AudioSegment]:
    lo, hi = start - pad_s, end + pad_s
    return [s for s in segments if s.end >= lo and s.start <= hi and (s.text or "").strip()]


def propose_speech_bounds(
    *,
    ocr_zh: str,
    cue_start: float,
    cue_end: float,
    segments: list[AudioSegment],
    max_shift_s: float = 0.85,
    min_overlap_sim: float = 0.35,
) -> dict[str, Any] | None:
    """
    Propose start/end from overlapping ASR segments when text agrees enough.
    Returns None when evidence is weak (keep OCR timing).
    """
    hits = overlapping_segments(segments, cue_start, cue_end)
    if not hits:
        return None
    # Prefer segments that resemble OCR ZH
    scored: list[tuple[float, AudioSegment]] = []
    for s in hits:
        sim = char_similarity(ocr_zh, s.text)
        scored.append((sim, s))
    scored.sort(key=lambda x: (-x[0], x[1].start))
    best_sim, best = scored[0]
    if best_sim < min_overlap_sim:
        # No text agreement — do not snap timing (OCR timing remains primary)
        if normalize_zh(ocr_zh):
            return None
        # OCR empty: allow high-confidence single speech segment
        if not (len(hits) == 1 and hits[0].confidence >= 0.7):
            return None
        best = hits[0]
        best_sim = 0.0

    new_start = float(best.start)
    new_end = float(best.end)
    # Prefer word-level timestamps when available (finer than segment bounds)
    words = list(getattr(best, "words", None) or [])
    if words:
        new_start = float(words[0].start)
        new_end = float(words[-1].end)
    # If multiple strong hits, expand to cover all with sim >= threshold
    strong = [s for sim, s in scored if sim >= min_overlap_sim]
    if len(strong) >= 2:
        new_start = min(s.start for s in strong)
        new_end = max(s.end for s in strong)
        # Expand with outermost words if present
        all_words = []
        for s in strong:
            all_words.extend(list(getattr(s, "words", None) or []))
        if all_words:
            new_start = min(w.start for w in all_words)
            new_end = max(w.end for w in all_words)
    if new_end <= new_start:
        new_end = new_start + 0.04

    # Clamp shift so OCR timing remains primary when ASR drifts far
    if abs(new_start - cue_start) > max_shift_s or abs(new_end - cue_end) > max_shift_s:
        # Soft clamp toward ASR but limited
        new_start = max(cue_start - max_shift_s, min(cue_start + max_shift_s, new_start))
        new_end = max(cue_end - max_shift_s, min(cue_end + max_shift_s, new_end))
        if new_end <= new_start:
            new_end = new_start + max(0.04, cue_end - cue_start)

    delta_start = new_start - cue_start
    delta_end = new_end - cue_end
    if abs(delta_start) < 0.03 and abs(delta_end) < 0.03:
        return None

    return {
        "start": round(new_start, 3),
        "end": round(new_end, 3),
        "delta_start_s": round(delta_start, 4),
        "delta_end_s": round(delta_end, 4),
        "delta_start_ms": round(delta_start * 1000.0, 1),
        "delta_end_ms": round(delta_end * 1000.0, 1),
        "char_similarity": round(best_sim, 4),
        "asr_confidence": round(float(best.confidence), 4),
        "method": "speech-align-words" if words else "speech-align",
        "word_count": len(words),
    }


def align_cue_records_to_speech(
    records: list[Any],
    segments: list[AudioSegment],
    *,
    max_shift_s: float = 0.85,
) -> dict[str, Any]:
    """
    Revise cue start/end toward ASR when evidence is strong.
    Does not change text_zh / original_ocr.
    """
    aligned = 0
    skipped = 0
    details: list[dict[str, Any]] = []
    for rec in records:
        ocr = getattr(rec, "original_ocr", None) or getattr(rec, "text_zh", "") or ""
        proposal = propose_speech_bounds(
            ocr_zh=ocr,
            cue_start=float(rec.start),
            cue_end=float(rec.end),
            segments=segments,
            max_shift_s=max_shift_s,
        )
        if not proposal:
            skipped += 1
            continue
        if hasattr(rec, "revise"):
            conf = float(getattr(rec, "timing_confidence", 0.7) or 0.7)
            # Slight boost when speech agrees
            new_tc = min(0.98, conf + 0.05 * float(proposal.get("char_similarity") or 0))
            rec.revise(
                start=float(proposal["start"]),
                end=float(proposal["end"]),
                reason="speech-align",
                timing_confidence=new_tc,
                maturity=getattr(rec, "maturity", None),
            )
        else:
            rec.start = float(proposal["start"])
            rec.end = float(proposal["end"])
        aligned += 1
        details.append(
            {
                "cue_id": str(getattr(rec, "cue_id", "")),
                **proposal,
            }
        )
    return {
        "ok": True,
        "aligned": aligned,
        "skipped": skipped,
        "cues": len(records),
        "details": details,
    }
