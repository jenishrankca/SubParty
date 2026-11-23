"""Cue revision model — maturity states + revision history for SubParty 2.1."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

Maturity = Literal["provisional", "context-refined", "context-complete", "final"]

MATURITY_ORDER: dict[str, int] = {
    "provisional": 0,
    "context-refined": 1,
    "context-complete": 2,
    "final": 3,
}


@dataclass
class TimingEvidence:
    sample_times: list[float] = field(default_factory=list)
    present_flags: list[bool] = field(default_factory=list)  # True when ZH matched at sample
    boundary_refined: bool = False
    method: str = "slot-merge"
    fine_step_s: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sample_times": list(self.sample_times),
            "present_flags": list(self.present_flags),
            "boundary_refined": self.boundary_refined,
            "method": self.method,
            "fine_step_s": self.fine_step_s,
        }


@dataclass
class CueRevision:
    revision_id: str
    text_en: str
    text_zh: str
    start: float
    end: float
    reason: str
    previous_en: str | None = None
    ocr_confidence: float = 1.0
    timing_confidence: float = 1.0
    provisional: bool = True
    approved: bool = False
    maturity: Maturity = "provisional"
    created_at: float = field(default_factory=time.time)
    timing_evidence: TimingEvidence | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "text_en": self.text_en,
            "text_zh": self.text_zh,
            "start": self.start,
            "end": self.end,
            "reason": self.reason,
            "previous_en": self.previous_en,
            "ocr_confidence": self.ocr_confidence,
            "timing_confidence": self.timing_confidence,
            "provisional": self.provisional,
            "approved": self.approved,
            "maturity": self.maturity,
            "created_at": self.created_at,
            "timing_evidence": self.timing_evidence.as_dict() if self.timing_evidence else None,
        }


@dataclass
class CueRecord:
    """Stable cue identity across provisional → refined → complete → final."""

    cue_id: str
    text_zh: str
    start: float
    end: float
    text_en: str = ""
    ocr_confidence: float = 1.0
    timing_confidence: float = 1.0
    provisional: bool = True
    approved: bool = False
    maturity: Maturity = "provisional"
    revisions: list[CueRevision] = field(default_factory=list)
    original_ocr: str = ""
    timing_evidence: TimingEvidence | None = None
    # Fingerprint of upcoming ZH used at last refine — new future dialogue reopens refine
    context_fingerprint: str = ""
    future_horizon_s: float = 120.0
    # Latest media time observed by the pipeline (for maturity clock)
    observed_until: float = 0.0
    # Phase B audio evidence (never overwrites original_ocr / text_zh source)
    audio_transcript: str = ""
    reconciled_zh: str = ""
    audio_disagreement: bool = False
    audio_evidence: Any | None = None
    # Sequence reconcile: visual (OCR) vs speech (ASR) bounds stay distinct
    visual_start: float | None = None
    visual_end: float | None = None
    speech_start: float | None = None
    speech_end: float | None = None
    timing_source: str = "visual"  # visual | speech | hybrid
    inferred: bool = False
    reconcile_note: str = ""
    asr_match_score: float = 0.0
    asr_segment_index: int | None = None

    @staticmethod
    def new(
        *,
        text_zh: str,
        start: float,
        end: float,
        text_en: str,
        ocr_confidence: float,
        timing_confidence: float,
        reason: str = "provisional",
        timing_evidence: TimingEvidence | None = None,
        provisional: bool = True,
        future_horizon_s: float = 120.0,
    ) -> CueRecord:
        cue_id = str(uuid.uuid4())
        ev = timing_evidence or TimingEvidence()
        maturity: Maturity = "provisional" if provisional else "context-refined"
        rev = CueRevision(
            revision_id=str(uuid.uuid4()),
            text_en=text_en,
            text_zh=text_zh,
            start=start,
            end=end,
            reason=reason,
            previous_en=None,
            ocr_confidence=ocr_confidence,
            timing_confidence=timing_confidence,
            provisional=provisional,
            approved=not provisional,
            maturity=maturity,
            timing_evidence=ev,
        )
        return CueRecord(
            cue_id=cue_id,
            text_zh=text_zh,
            start=start,
            end=end,
            text_en=text_en,
            ocr_confidence=ocr_confidence,
            timing_confidence=timing_confidence,
            provisional=provisional,
            approved=not provisional,
            maturity=maturity,
            revisions=[rev],
            original_ocr=text_zh,
            timing_evidence=ev,
            future_horizon_s=max(1.0, float(future_horizon_s)),
            observed_until=end,
            visual_start=start,
            visual_end=end,
            timing_source="visual",
        )

    def set_maturity(self, maturity: Maturity) -> None:
        if MATURITY_ORDER.get(maturity, 0) < MATURITY_ORDER.get(self.maturity, 0):
            # Allow demotion only from final→complete never; otherwise only advance
            if self.maturity == "final":
                return
            if maturity == "provisional" and self.maturity != "provisional":
                # Explicit reopen for new future context
                pass
            elif MATURITY_ORDER.get(maturity, 0) < MATURITY_ORDER.get(self.maturity, 0):
                return
        self.maturity = maturity
        self.provisional = maturity == "provisional"
        if maturity in {"context-refined", "context-complete", "final"}:
            self.approved = True
        if maturity == "final":
            self.provisional = False
            self.approved = True

    def revise(
        self,
        *,
        text_en: str | None = None,
        text_zh: str | None = None,
        start: float | None = None,
        end: float | None = None,
        reason: str,
        ocr_confidence: float | None = None,
        timing_confidence: float | None = None,
        provisional: bool | None = None,
        approved: bool | None = None,
        maturity: Maturity | None = None,
        timing_evidence: TimingEvidence | None = None,
        context_fingerprint: str | None = None,
    ) -> CueRevision:
        prev_en = self.text_en
        if text_en is not None:
            self.text_en = text_en
        if text_zh is not None:
            self.text_zh = text_zh
        if start is not None:
            self.start = start
        if end is not None:
            self.end = end
        if ocr_confidence is not None:
            self.ocr_confidence = ocr_confidence
        if timing_confidence is not None:
            self.timing_confidence = timing_confidence
        if maturity is not None:
            self.set_maturity(maturity)
        elif provisional is not None or approved is not None:
            if provisional is not None:
                self.provisional = provisional
            if approved is not None:
                self.approved = approved
            if provisional is False and approved is True and self.maturity == "provisional":
                self.maturity = "context-refined"
        if timing_evidence is not None:
            self.timing_evidence = timing_evidence
        if context_fingerprint is not None:
            self.context_fingerprint = context_fingerprint
        rev = CueRevision(
            revision_id=str(uuid.uuid4()),
            text_en=self.text_en,
            text_zh=self.text_zh,
            start=self.start,
            end=self.end,
            reason=reason,
            previous_en=prev_en if prev_en != self.text_en else None,
            ocr_confidence=self.ocr_confidence,
            timing_confidence=self.timing_confidence,
            provisional=self.provisional,
            approved=self.approved,
            maturity=self.maturity,
            timing_evidence=self.timing_evidence,
        )
        self.revisions.append(rev)
        return rev

    def latest_approved_en(self) -> str:
        for rev in reversed(self.revisions):
            if rev.approved and not rev.provisional:
                return rev.text_en
        return self.text_en

    def as_payload(self, index: int = 0) -> dict[str, Any]:
        return {
            "index": index,
            "cue_id": self.cue_id,
            "start": self.start,
            "end": self.end,
            "text_zh": self.text_zh,
            "text_en": self.text_en,
            "original_ocr": self.original_ocr,
            "confidence": min(self.ocr_confidence, self.timing_confidence),
            "ocr_confidence": self.ocr_confidence,
            "timing_confidence": self.timing_confidence,
            "uncertain": self.ocr_confidence < 0.78 or self.timing_confidence < 0.7,
            "provisional": self.provisional,
            "approved": self.approved,
            "maturity": self.maturity,
            "revision_count": len(self.revisions),
            "revisions": [r.as_dict() for r in self.revisions[-8:]],
            "timing_evidence": self.timing_evidence.as_dict() if self.timing_evidence else None,
            "context_fingerprint": self.context_fingerprint,
            "future_horizon_s": self.future_horizon_s,
            "audio_transcript": self.audio_transcript,
            "reconciled_zh": self.reconciled_zh or self.text_zh,
            "audio_disagreement": self.audio_disagreement,
            "audio_evidence": (
                self.audio_evidence.as_dict()
                if self.audio_evidence is not None and hasattr(self.audio_evidence, "as_dict")
                else self.audio_evidence
            ),
            "visual_start": self.visual_start if self.visual_start is not None else self.start,
            "visual_end": self.visual_end if self.visual_end is not None else self.end,
            "speech_start": self.speech_start,
            "speech_end": self.speech_end,
            "timing_source": self.timing_source,
            "inferred": self.inferred,
            "reconcile_note": self.reconcile_note,
            "asr_match_score": self.asr_match_score,
            "asr_segment_index": self.asr_segment_index,
        }
