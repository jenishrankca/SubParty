"""Cue revision model — every change keeps OCR, timing evidence, prior EN, reason."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import time
import uuid


@dataclass
class TimingEvidence:
    sample_times: list[float] = field(default_factory=list)
    present_flags: list[bool] = field(default_factory=list)  # True when ZH matched at sample
    boundary_refined: bool = False
    method: str = "slot-merge"

    def as_dict(self) -> dict[str, Any]:
        return {
            "sample_times": list(self.sample_times),
            "present_flags": list(self.present_flags),
            "boundary_refined": self.boundary_refined,
            "method": self.method,
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
            "created_at": self.created_at,
            "timing_evidence": self.timing_evidence.as_dict() if self.timing_evidence else None,
        }


@dataclass
class CueRecord:
    """Stable cue identity across provisional → refined revisions."""

    cue_id: str
    text_zh: str
    start: float
    end: float
    text_en: str = ""
    ocr_confidence: float = 1.0
    timing_confidence: float = 1.0
    provisional: bool = True
    approved: bool = False
    revisions: list[CueRevision] = field(default_factory=list)
    original_ocr: str = ""
    timing_evidence: TimingEvidence | None = None

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
    ) -> CueRecord:
        cue_id = str(uuid.uuid4())
        ev = timing_evidence or TimingEvidence()
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
            revisions=[rev],
            original_ocr=text_zh,
            timing_evidence=ev,
        )

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
        timing_evidence: TimingEvidence | None = None,
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
        if provisional is not None:
            self.provisional = provisional
        if approved is not None:
            self.approved = approved
        if timing_evidence is not None:
            self.timing_evidence = timing_evidence
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
            "text_en": self.text_en if self.approved or not self.provisional else self.text_en,
            "original_ocr": self.original_ocr,
            "confidence": min(self.ocr_confidence, self.timing_confidence),
            "ocr_confidence": self.ocr_confidence,
            "timing_confidence": self.timing_confidence,
            "uncertain": self.ocr_confidence < 0.78 or self.timing_confidence < 0.7,
            "provisional": self.provisional,
            "approved": self.approved,
            "revision_count": len(self.revisions),
            "revisions": [r.as_dict() for r in self.revisions[-8:]],
            "timing_evidence": self.timing_evidence.as_dict() if self.timing_evidence else None,
        }
