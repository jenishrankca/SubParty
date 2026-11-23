"""
SubParty 2.1 Phase B — Audio Verification Engine (stub).

Independent of OCR. Do not implement full Whisper path until Phase A metrics
are green on real episodes. Outline only:

1. FFmpeg extract mono WAV from the same video timeline
2. Candidate ASR: faster-whisper (local, free) first; FunASR / WhisperX later
3. Compare OCR ZH vs speech ZH as *evidence*, never auto-overwrite OCR
4. Persist: original_ocr, audio_transcript, reconciled_zh, text_en, revisions

Enable later via SUBPARTY_AUDIO_VERIFY=1 once Phase A is retestable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AudioEvidence:
    cue_id: str
    start: float
    end: float
    audio_transcript: str = ""
    asr_confidence: float = 0.0
    engine: str = "faster-whisper"
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "cue_id": self.cue_id,
            "start": self.start,
            "end": self.end,
            "audio_transcript": self.audio_transcript,
            "asr_confidence": self.asr_confidence,
            "engine": self.engine,
            "notes": self.notes,
        }


@dataclass
class AudioVerifyPlan:
    """Declarative plan — no model load in Phase A."""

    enabled: bool = False
    candidate_engines: list[str] = field(
        default_factory=lambda: ["faster-whisper", "funasr", "whisperx"]
    )
    never_overwrite_ocr: bool = True

    def status(self) -> dict[str, Any]:
        return {
            "phase": "B-stub",
            "enabled": self.enabled,
            "candidate_engines": list(self.candidate_engines),
            "never_overwrite_ocr": self.never_overwrite_ocr,
            "ready": False,
            "note": "Finish Phase A accuracy validation before enabling audio verify.",
        }


def audio_verify_status() -> dict[str, Any]:
    return AudioVerifyPlan().status()
