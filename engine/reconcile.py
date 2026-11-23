"""Sequence-aware OCR↔ASR reconciliation.

Keeps visual (OCR hardsub) bounds distinct from speech (ASR) bounds.
Marks inferred samples when ZH or timing is adopted from audio without strong OCR.
Never silently drops ``original_ocr``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from audio_verify import (
    AudioSegment,
    char_similarity,
    compare_ocr_vs_speech,
    normalize_zh,
    reconcile_zh,
)

logger = logging.getLogger("subparty.reconcile")


@dataclass
class BoundPair:
    """Visual = hardsub OCR window; speech = ASR evidence (may be absent)."""

    visual_start: float
    visual_end: float
    speech_start: float | None = None
    speech_end: float | None = None
    timing_source: str = "visual"  # visual | speech | hybrid
    inferred: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "visual_start": round(self.visual_start, 3),
            "visual_end": round(self.visual_end, 3),
            "speech_start": None if self.speech_start is None else round(self.speech_start, 3),
            "speech_end": None if self.speech_end is None else round(self.speech_end, 3),
            "timing_source": self.timing_source,
            "inferred": self.inferred,
        }


@dataclass
class CueAsrMatch:
    cue_index: int
    segment_index: int | None
    score: float
    char_similarity: float
    audio_zh: str
    asr_confidence: float
    reconciled_zh: str
    reconcile_note: str
    bounds: BoundPair
    inferred: bool = False
    disagreement: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "cue_index": self.cue_index,
            "segment_index": self.segment_index,
            "score": round(self.score, 4),
            "char_similarity": round(self.char_similarity, 4),
            "audio_zh": self.audio_zh,
            "asr_confidence": round(self.asr_confidence, 4),
            "reconciled_zh": self.reconciled_zh,
            "reconcile_note": self.reconcile_note,
            "bounds": self.bounds.as_dict(),
            "inferred": self.inferred,
            "disagreement": self.disagreement,
        }


@dataclass
class ReconcileBatchResult:
    matches: list[CueAsrMatch] = field(default_factory=list)
    unmatched_asr: list[int] = field(default_factory=list)
    sequence_aware: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence_aware": self.sequence_aware,
            "matched": sum(1 for m in self.matches if m.segment_index is not None),
            "cues": len(self.matches),
            "unmatched_asr": list(self.unmatched_asr),
            "inferred_count": sum(1 for m in self.matches if m.inferred),
            "matches": [m.as_dict() for m in self.matches],
        }


def _overlap_s(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _match_score(
    *,
    cue_zh: str,
    cue_start: float,
    cue_end: float,
    seg: AudioSegment,
) -> float:
    sim = char_similarity(cue_zh, seg.text)
    ov = _overlap_s(cue_start, cue_end, float(seg.start), float(seg.end))
    cue_dur = max(0.04, cue_end - cue_start)
    seg_dur = max(0.04, float(seg.end) - float(seg.start))
    ov_ratio = ov / min(cue_dur, seg_dur)
    # Center distance penalty
    c_mid = 0.5 * (cue_start + cue_end)
    s_mid = 0.5 * (float(seg.start) + float(seg.end))
    dist = abs(c_mid - s_mid)
    dist_pen = max(0.0, 1.0 - dist / 3.0)
    return 0.55 * sim + 0.30 * ov_ratio + 0.15 * dist_pen * float(seg.confidence or 0.5)


def match_cues_to_asr_sequence(
    cues: list[dict[str, Any]],
    segments: list[AudioSegment],
    *,
    pad_s: float = 0.85,
    min_score: float = 0.22,
) -> ReconcileBatchResult:
    """
    Greedy left-to-right match: each cue picks the best remaining ASR segment
    that does not go backwards in the speech timeline (sequence-aware).
    """
    ordered = sorted(range(len(cues)), key=lambda i: float(cues[i].get("start") or 0))
    segs = list(segments or [])
    next_j = 0
    used: set[int] = set()
    by_cue: dict[int, CueAsrMatch] = {}

    for cue_i in ordered:
        c = cues[cue_i]
        zh = str(c.get("original_ocr") or c.get("text_zh") or "")
        cs = float(c.get("start") or 0)
        ce = float(c.get("end") or cs)
        bounds = BoundPair(visual_start=cs, visual_end=ce, timing_source="visual")

        best_j: int | None = None
        best_score = -1.0
        for j in range(next_j, len(segs)):
            seg = segs[j]
            if j in used:
                continue
            if float(seg.end) < cs - pad_s:
                continue
            if float(seg.start) > ce + pad_s:
                break
            score = _match_score(cue_zh=zh, cue_start=cs, cue_end=ce, seg=seg)
            if score > best_score:
                best_score = score
                best_j = j

        if best_j is None or best_score < min_score:
            cmp = compare_ocr_vs_speech(zh, "", asr_confidence=0.0)
            recon, note = reconcile_zh(
                original_ocr=zh, audio_zh="", compare=cmp, asr_confidence=0.0
            )
            by_cue[cue_i] = CueAsrMatch(
                cue_index=cue_i,
                segment_index=None,
                score=0.0,
                char_similarity=0.0,
                audio_zh="",
                asr_confidence=0.0,
                reconciled_zh=recon,
                reconcile_note=note,
                bounds=bounds,
                inferred=False,
                disagreement=False,
            )
            continue

        seg = segs[best_j]
        used.add(best_j)
        next_j = best_j  # allow same-ish region; still non-decreasing
        aud = (seg.text or "").strip()
        asr_conf = float(seg.confidence or 0.0)
        cmp = compare_ocr_vs_speech(zh, aud, asr_confidence=asr_conf)
        recon, note = reconcile_zh(
            original_ocr=zh, audio_zh=aud, compare=cmp, asr_confidence=asr_conf
        )
        speech_s = float(seg.start)
        speech_e = float(seg.end)
        words = list(getattr(seg, "words", None) or [])
        if words:
            speech_s = float(words[0].start)
            speech_e = float(words[-1].end)

        inferred = note in {"adopt-audio-ocr-empty", "adopt-audio-ocr-typo"}
        # Hybrid bounds: keep visual primary; attach speech separately
        timing_source = "hybrid" if abs(speech_s - cs) > 0.05 or abs(speech_e - ce) > 0.05 else "visual"
        bounds = BoundPair(
            visual_start=cs,
            visual_end=ce,
            speech_start=speech_s,
            speech_end=speech_e,
            timing_source=timing_source,
            inferred=inferred and not normalize_zh(zh),
        )
        by_cue[cue_i] = CueAsrMatch(
            cue_index=cue_i,
            segment_index=best_j,
            score=best_score,
            char_similarity=cmp.char_similarity,
            audio_zh=aud,
            asr_confidence=asr_conf,
            reconciled_zh=recon,
            reconcile_note=note,
            bounds=bounds,
            inferred=inferred,
            disagreement=cmp.disagreement,
        )

    matches = [by_cue[i] for i in range(len(cues))]
    unmatched = [j for j in range(len(segs)) if j not in used]
    return ReconcileBatchResult(matches=matches, unmatched_asr=unmatched, sequence_aware=True)


def apply_reconcile_to_records(
    records: list[Any],
    segments: list[AudioSegment],
) -> dict[str, Any]:
    """
    Attach sequence-aware matches to cue records.
    Sets visual_*/speech_* bounds; may revise text_zh only on adopt notes;
    marks ``inferred`` when ZH/timing comes from ASR without strong OCR.
    """
    cues = [
        {
            "original_ocr": getattr(r, "original_ocr", None) or getattr(r, "text_zh", "") or "",
            "text_zh": getattr(r, "text_zh", "") or "",
            "start": float(r.start),
            "end": float(r.end),
        }
        for r in records
    ]
    batch = match_cues_to_asr_sequence(cues, segments)
    disagreements = 0
    inferred_n = 0

    for rec, match in zip(records, batch.matches):
        ocr = getattr(rec, "original_ocr", None) or getattr(rec, "text_zh", "") or ""
        if not getattr(rec, "original_ocr", None):
            setattr(rec, "original_ocr", ocr)

        setattr(rec, "visual_start", match.bounds.visual_start)
        setattr(rec, "visual_end", match.bounds.visual_end)
        setattr(rec, "speech_start", match.bounds.speech_start)
        setattr(rec, "speech_end", match.bounds.speech_end)
        setattr(rec, "timing_source", match.bounds.timing_source)
        setattr(rec, "inferred", bool(match.inferred))
        setattr(rec, "audio_transcript", match.audio_zh)
        setattr(rec, "reconciled_zh", match.reconciled_zh)
        setattr(rec, "audio_disagreement", match.disagreement)
        setattr(rec, "reconcile_note", match.reconcile_note)
        setattr(rec, "asr_match_score", match.score)
        setattr(rec, "asr_confidence", match.asr_confidence)
        setattr(rec, "asr_segment_index", match.segment_index)

        if match.disagreement:
            disagreements += 1
        if match.inferred:
            inferred_n += 1

        apply_zh = bool(
            match.reconcile_note in {"adopt-audio-ocr-typo", "adopt-audio-ocr-empty"}
            and match.reconciled_zh
            and match.reconciled_zh != (getattr(rec, "text_zh", "") or "")
        )
        if apply_zh:
            if hasattr(rec, "revise"):
                rec.revise(
                    text_zh=match.reconciled_zh,
                    reason=f"seq-reconcile:{match.reconcile_note}",
                    maturity=getattr(rec, "maturity", "context-refined"),
                )
            else:
                setattr(rec, "text_zh", match.reconciled_zh)

    return {
        "ok": True,
        "sequence_aware": True,
        "disagreements": disagreements,
        "inferred": inferred_n,
        "batch": batch.as_dict(),
    }
