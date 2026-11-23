"""
SubParty 2.1 Phase B — Audio Verification Engine.

Independent evidence stream (never auto-overwrites OCR text_zh):
1. FFmpeg mono WAV extract
2. faster-whisper Chinese ASR + timestamps (small model for 6GB/16GB)
3. Sentence/character compare → disagreement flags + confidence
4. Preserve original_ocr, audio_transcript, reconciled_zh (evidence-only), EN via contextual translate
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from ffmpeg_util import extract_audio_wav
from paths import models_dir

logger = logging.getLogger("subparty.audio")

WHISPER_MODEL_ID = "faster-whisper-small"
WHISPER_SIZE = "small"  # fits RTX 6GB / 16GB RAM (int8 CPU or float16 CUDA)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def audio_verify_enabled(default: bool | None = None) -> bool:
    env = (os.environ.get("SUBPARTY_AUDIO_VERIFY") or "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "no", "off"}:
        return False
    if default is not None:
        return bool(default)
    return False


def whisper_model_dir() -> Path:
    override = (os.environ.get("SUBPARTY_WHISPER_MODEL") or "").strip()
    if override:
        return Path(override).expanduser()
    return models_dir() / WHISPER_MODEL_ID


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:
        return False


def whisper_weights_ready() -> bool:
    """True when package installed; weights download lazily on first transcribe."""
    return whisper_available()


@dataclass
class AudioSegment:
    start: float
    end: float
    text: str
    confidence: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "confidence": self.confidence,
        }


@dataclass
class CompareResult:
    ocr_zh: str
    audio_zh: str
    char_similarity: float
    disagreement: bool
    confidence: float
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ocr_zh": self.ocr_zh,
            "audio_zh": self.audio_zh,
            "char_similarity": self.char_similarity,
            "disagreement": self.disagreement,
            "confidence": self.confidence,
            "notes": self.notes,
        }


@dataclass
class AudioEvidence:
    cue_id: str
    start: float
    end: float
    audio_transcript: str = ""
    asr_confidence: float = 0.0
    engine: str = "faster-whisper"
    model_size: str = WHISPER_SIZE
    disagreement: bool = False
    char_similarity: float = 1.0
    reconciled_zh: str = ""
    compare: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "cue_id": self.cue_id,
            "start": self.start,
            "end": self.end,
            "audio_transcript": self.audio_transcript,
            "asr_confidence": self.asr_confidence,
            "engine": self.engine,
            "model_size": self.model_size,
            "disagreement": self.disagreement,
            "char_similarity": self.char_similarity,
            "reconciled_zh": self.reconciled_zh,
            "compare": dict(self.compare),
            "notes": self.notes,
        }


def normalize_zh(text: str) -> str:
    text = (text or "").strip()
    # Drop whitespace / punctuation for character compare
    chars = _CJK_RE.findall(text)
    return "".join(chars)


def char_similarity(a: str, b: str) -> float:
    """Character-level Dice-like overlap on CJK runs (0..1)."""
    na = normalize_zh(a)
    nb = normalize_zh(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    # bigram Dice if long enough, else set overlap
    if len(na) < 2 or len(nb) < 2:
        sa, sb = set(na), set(nb)
        inter = len(sa & sb)
        return (2.0 * inter) / (len(sa) + len(sb)) if (sa or sb) else 0.0
    ba = {na[i : i + 2] for i in range(len(na) - 1)}
    bb = {nb[i : i + 2] for i in range(len(nb) - 1)}
    inter = len(ba & bb)
    return (2.0 * inter) / (len(ba) + len(bb)) if (ba or bb) else 0.0


def compare_ocr_vs_speech(
    ocr_zh: str,
    audio_zh: str,
    *,
    asr_confidence: float = 0.0,
    disagree_threshold: float = 0.55,
) -> CompareResult:
    sim = char_similarity(ocr_zh, audio_zh)
    ocr_n = normalize_zh(ocr_zh)
    aud_n = normalize_zh(audio_zh)
    notes = ""
    if not aud_n:
        notes = "no-audio-text"
        disagreement = False
    elif not ocr_n and aud_n:
        notes = "ocr-empty-audio-present"
        disagreement = True
    elif sim < disagree_threshold:
        notes = "char-mismatch"
        disagreement = True
    else:
        notes = "aligned"
        disagreement = False
    conf = max(0.0, min(1.0, 0.55 * sim + 0.45 * float(asr_confidence or 0.0)))
    return CompareResult(
        ocr_zh=ocr_zh or "",
        audio_zh=audio_zh or "",
        char_similarity=round(sim, 4),
        disagreement=disagreement,
        confidence=round(conf, 4),
        notes=notes,
    )


def _edit_distance_cjk(a: str, b: str) -> int:
    na, nb = normalize_zh(a), normalize_zh(b)
    if na == nb:
        return 0
    if not na:
        return len(nb)
    if not nb:
        return len(na)
    prev = list(range(len(nb) + 1))
    for i, ca in enumerate(na, start=1):
        cur = [i]
        for j, cb in enumerate(nb, start=1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def reconcile_zh(
    *,
    original_ocr: str,
    audio_zh: str,
    compare: CompareResult,
    asr_confidence: float,
) -> tuple[str, str]:
    """
    Return (reconciled_zh, note). Never silently replace OCR without evidence.
    `original_ocr` must always be preserved by the caller as the OCR source field.
    """
    ocr = (original_ocr or "").strip()
    aud = (audio_zh or "").strip()
    if not aud:
        return ocr, "keep-ocr-no-audio"
    if not ocr and aud and asr_confidence >= 0.45:
        return aud, "adopt-audio-ocr-empty"
    # High ASR + near-OCR: treat as OCR typo fix (small edit distance)
    if (
        ocr
        and aud
        and asr_confidence >= 0.7
        and compare.char_similarity >= 0.72
        and normalize_zh(ocr) != normalize_zh(aud)
    ):
        dist = _edit_distance_cjk(ocr, aud)
        max_len = max(len(normalize_zh(ocr)), len(normalize_zh(aud)), 1)
        if dist <= 2 and dist / max_len <= 0.35:
            return aud, "adopt-audio-ocr-typo"
    if compare.disagreement and asr_confidence >= 0.75 and compare.char_similarity < 0.35:
        # Strong ASR, weak overlap — keep OCR as source of truth; flag only
        return ocr, "keep-ocr-flag-disagreement"
    if not compare.disagreement and aud:
        # Aligned — prefer OCR spelling (hardsub), audio confirms
        return ocr or aud, "confirm-ocr"
    return ocr, "keep-ocr"


@lru_cache(maxsize=1)
def _whisper_model(size: str = WHISPER_SIZE):
    from faster_whisper import WhisperModel

    device = "cpu"
    compute = "int8"
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            device = "cuda"
            compute = "float16"
    except Exception:
        pass
    # Local cache dir under data/models
    download_root = str(whisper_model_dir().parent)
    logger.info("loading faster-whisper size=%s device=%s compute=%s", size, device, compute)
    return WhisperModel(
        size,
        device=device,
        compute_type=compute,
        download_root=download_root,
    )


def ensure_whisper_model(size: str = WHISPER_SIZE) -> dict[str, Any]:
    if not whisper_available():
        raise RuntimeError(
            "faster-whisper not installed. pip install -r requirements-audio.txt"
        )
    _whisper_model(size)
    return {
        "id": WHISPER_MODEL_ID,
        "name": f"faster-whisper {size}",
        "kind": "asr",
        "installed": True,
        "engine": "audio",
        "size_hint": "~500 MB" if size == "small" else f"whisper-{size}",
        "path": str(whisper_model_dir().parent),
        "notes": "Chinese ASR for audio verify. small fits 6GB VRAM / 16GB RAM.",
    }


def transcribe_zh(
    wav_path: Path | str,
    *,
    model_size: str = WHISPER_SIZE,
    language: str = "zh",
) -> list[AudioSegment]:
    model = _whisper_model(model_size)
    segments, _info = model.transcribe(
        str(wav_path),
        language=language,
        beam_size=3,
        vad_filter=True,
        word_timestamps=False,
    )
    out: list[AudioSegment] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        # avg_logprob ~ [-1, 0] → map to 0..1
        lp = float(getattr(seg, "avg_logprob", -1.0) or -1.0)
        conf = max(0.05, min(0.99, (lp + 1.2) / 1.2))
        out.append(
            AudioSegment(
                start=float(seg.start or 0.0),
                end=float(seg.end or 0.0),
                text=text,
                confidence=conf,
            )
        )
    return out


def audio_for_window(
    segments: list[AudioSegment],
    start: float,
    end: float,
    *,
    pad_s: float = 0.35,
) -> tuple[str, float]:
    """Join ASR segments overlapping [start,end]; return (text, mean conf)."""
    lo, hi = start - pad_s, end + pad_s
    hits = [s for s in segments if s.end >= lo and s.start <= hi and s.text.strip()]
    if not hits:
        return "", 0.0
    text = "".join(s.text.strip() for s in hits)
    conf = sum(s.confidence for s in hits) / len(hits)
    return text, conf


def extract_and_transcribe(
    video_path: Path | str,
    *,
    model_size: str = WHISPER_SIZE,
    work_dir: Path | None = None,
) -> list[AudioSegment]:
    video = Path(video_path)
    own_tmp = work_dir is None
    tmp = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="sp_audio_"))
    tmp.mkdir(parents=True, exist_ok=True)
    wav = tmp / "audio_16k.wav"
    try:
        extract_audio_wav(video, wav)
        return transcribe_zh(wav, model_size=model_size)
    finally:
        if own_tmp:
            try:
                if wav.exists():
                    wav.unlink()
                tmp.rmdir()
            except Exception:
                pass


TranslateFn = Callable[[str], str]


def verify_cue_records(
    records: list[Any],
    *,
    video_path: str | Path,
    model_size: str = WHISPER_SIZE,
    translate_fn: TranslateFn | None = None,
    segments: list[AudioSegment] | None = None,
) -> dict[str, Any]:
    """
    Attach audio evidence to cue records. Does not overwrite record.text_zh / original_ocr.
    May set reconciled fields on the record and optionally refresh EN via translate_fn
    when reconciled_zh differs and evidence supports confirmation — still keeps original_ocr.
    """
    if not records:
        return {"ok": True, "cues": 0, "disagreements": 0, "segments": 0}

    segs = segments if segments is not None else extract_and_transcribe(video_path, model_size=model_size)
    disagreements = 0
    evidence_list: list[dict[str, Any]] = []

    for rec in records:
        ocr = getattr(rec, "original_ocr", None) or getattr(rec, "text_zh", "") or ""
        aud, asr_conf = audio_for_window(segs, float(rec.start), float(rec.end))
        cmp = compare_ocr_vs_speech(ocr, aud, asr_confidence=asr_conf)
        reconciled, note = reconcile_zh(
            original_ocr=ocr,
            audio_zh=aud,
            compare=cmp,
            asr_confidence=asr_conf,
        )
        if cmp.disagreement:
            disagreements += 1

        ev = AudioEvidence(
            cue_id=str(getattr(rec, "cue_id", "")),
            start=float(rec.start),
            end=float(rec.end),
            audio_transcript=aud,
            asr_confidence=round(asr_conf, 4),
            disagreement=cmp.disagreement,
            char_similarity=cmp.char_similarity,
            reconciled_zh=reconciled,
            compare=cmp.as_dict(),
            notes=note,
        )
        # Attach without clobbering OCR source field
        setattr(rec, "audio_transcript", aud)
        setattr(rec, "audio_evidence", ev)
        setattr(rec, "reconciled_zh", reconciled)
        setattr(rec, "audio_disagreement", cmp.disagreement)
        if not getattr(rec, "original_ocr", None):
            setattr(rec, "original_ocr", ocr)

        # Evidence-only ZH update: keep original_ocr; may revise text_zh on typo/empty adopt
        apply_zh = bool(
            note in {"adopt-audio-ocr-typo", "adopt-audio-ocr-empty"}
            and reconciled
            and reconciled != (getattr(rec, "text_zh", "") or "")
        )
        setattr(rec, "original_ocr", ocr)
        if apply_zh:
            if hasattr(rec, "revise"):
                rec.revise(
                    text_zh=reconciled,
                    reason=f"audio-reconcile:{note}",
                    maturity=getattr(rec, "maturity", "context-refined"),
                )
            else:
                setattr(rec, "text_zh", reconciled)

        # EN refresh when evidence supports adopt (ZH already updated) or confirm path
        if translate_fn and reconciled and (
            apply_zh or note in {"confirm-ocr", "adopt-audio-ocr-empty", "adopt-audio-ocr-typo"}
        ):
            try:
                new_en = translate_fn(reconciled if apply_zh else (getattr(rec, "text_zh", "") or reconciled))
                if new_en and hasattr(rec, "revise"):
                    rec.revise(
                        text_en=new_en,
                        reason="audio-reconcile-en",
                        maturity=getattr(rec, "maturity", "context-refined"),
                    )
            except Exception as exc:
                logger.warning("audio EN refresh failed: %s", exc)

        evidence_list.append(ev.as_dict())

    # Stage 5: speech-aligned timing (ASR bounds → cue start/end)
    speech_summary: dict[str, Any] = {}
    try:
        from speech_align import align_cue_records_to_speech

        speech_summary = align_cue_records_to_speech(records, segs)
    except Exception as exc:
        logger.warning("speech align skipped: %s", exc)
        speech_summary = {"ok": False, "error": str(exc), "aligned": 0}

    return {
        "ok": True,
        "cues": len(records),
        "disagreements": disagreements,
        "segments": len(segs),
        "engine": "faster-whisper",
        "model_size": model_size,
        "evidence": evidence_list,
        "speech_align": speech_summary,
    }


def audio_verify_status() -> dict[str, Any]:
    pkg = whisper_available()
    enabled = audio_verify_enabled(False)
    return {
        "phase": "B",
        "enabled": enabled,
        "ready": pkg,
        "package": "faster-whisper" if pkg else None,
        "model_size_default": WHISPER_SIZE,
        "model_id": WHISPER_MODEL_ID,
        "candidate_engines": ["faster-whisper", "funasr", "whisperx"],
        "never_overwrite_ocr": True,
        "device_note": "Prefers CUDA float16 when CTranslate2 sees a GPU; else CPU int8.",
        "note": (
            "Optional. Set SUBPARTY_AUDIO_VERIFY=1 or job.audio_verify=true. "
            "Install: pip install -r requirements-audio.txt then Models → ensure."
            if not pkg
            else "faster-whisper ready. Enable per job or SUBPARTY_AUDIO_VERIFY=1."
        ),
    }
