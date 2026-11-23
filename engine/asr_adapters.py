"""ASR adapter registry — faster-whisper baseline + optional Qwen3 / FunASR.

Licences (product path):
  - faster-whisper / Whisper weights: MIT (model cards vary; OpenAI Whisper MIT)
  - Qwen3-ASR / ForcedAligner: Apache-2.0 (OK to evaluate commercially)
  - FunASR: Apache-2.0 models typically; confirm per model card before shipping

Aligners (ForcedAligner / TinyAligner) live in ``align_experiments`` — separate
from the live OCR timing path (visual ≠ speech timing).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("subparty.asr_adapters")


@dataclass
class AsrSegment:
    start: float
    end: float
    text: str
    confidence: float = 0.0
    words: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "confidence": self.confidence,
            "words": list(self.words or []),
        }


@dataclass
class AsrRunResult:
    engine_id: str
    ok: bool
    segments: list[AsrSegment] = field(default_factory=list)
    infer_s: float = 0.0
    audio_s: float = 0.0
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def rtf(self) -> float | None:
        if self.audio_s <= 0:
            return None
        return round(self.infer_s / self.audio_s, 4)

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine_id": self.engine_id,
            "ok": self.ok,
            "infer_s": round(self.infer_s, 3),
            "audio_s": round(self.audio_s, 3),
            "rtf": self.rtf,
            "segment_count": len(self.segments),
            "preview": [s.text[:60] for s in self.segments[:5]],
            "error": self.error,
            "meta": dict(self.meta or {}),
        }


class AsrAdapter(Protocol):
    id: str
    license: str

    def available(self) -> bool: ...

    def status(self) -> dict[str, Any]: ...

    def transcribe_wav(
        self,
        wav_path: Path | str,
        *,
        language: str = "zh",
        audio_duration_s: float | None = None,
    ) -> AsrRunResult: ...


class FasterWhisperAdapter:
    """Baseline Chinese ASR (SubParty Phase B)."""

    id = "faster-whisper-small"
    license = "MIT (faster-whisper); Whisper weights MIT"

    def available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401

            return True
        except Exception:
            return False

    def status(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "available": self.available(),
            "license": self.license,
            "role": "baseline",
            "install": "pip install -r requirements-audio.txt",
        }

    def transcribe_wav(
        self,
        wav_path: Path | str,
        *,
        language: str = "zh",
        audio_duration_s: float | None = None,
    ) -> AsrRunResult:
        from asr_io import load_wav_mono_f32
        from audio_verify import WHISPER_SIZE, _whisper_model

        t0 = time.perf_counter()
        try:
            audio, sr = load_wav_mono_f32(wav_path)
            dur = float(audio_duration_s) if audio_duration_s else (len(audio) / max(1, sr))
            # Benches default CPU — broken CUDA toolkit (missing cublas) is common on this box
            device_key = (os.environ.get("SUBPARTY_WHISPER_DEVICE") or "cpu").strip().lower()
            model = _whisper_model(WHISPER_SIZE, device_key)
            segments, info = model.transcribe(
                audio,
                language=language,
                beam_size=1 if os.environ.get("SUBPARTY_ASR_FAST") else 3,
                vad_filter=True,
                word_timestamps=True,
            )
            out: list[AsrSegment] = []
            for seg in segments:
                text = (seg.text or "").strip()
                if not text:
                    continue
                lp = float(getattr(seg, "avg_logprob", -1.0) or -1.0)
                conf = max(0.05, min(0.99, (lp + 1.2) / 1.2))
                words = []
                for w in getattr(seg, "words", None) or []:
                    wt = (getattr(w, "word", None) or "").strip()
                    if not wt:
                        continue
                    words.append(
                        {
                            "start": float(getattr(w, "start", 0) or 0),
                            "end": float(getattr(w, "end", 0) or 0),
                            "word": wt,
                            "probability": float(getattr(w, "probability", 0) or 0),
                        }
                    )
                out.append(
                    AsrSegment(
                        start=float(seg.start or 0),
                        end=float(seg.end or 0),
                        text=text,
                        confidence=conf,
                        words=words,
                    )
                )
            return AsrRunResult(
                engine_id=self.id,
                ok=True,
                segments=out,
                infer_s=time.perf_counter() - t0,
                audio_s=dur,
                meta={
                    "language": getattr(info, "language", language),
                    "device_note": "faster-whisper small",
                },
            )
        except Exception as exc:
            return AsrRunResult(
                engine_id=self.id,
                ok=False,
                infer_s=time.perf_counter() - t0,
                audio_s=float(audio_duration_s or 0),
                error=f"{type(exc).__name__}: {exc}",
            )


class Qwen3AsrAdapter:
    """Qwen3-ASR-0.6B (Apache-2.0) — optional compare path."""

    id = "qwen3-asr-0.6b"
    license = "Apache-2.0"
    model_id = "Qwen/Qwen3-ASR-0.6B"

    def available(self) -> bool:
        # Prefer official package; transformers HF path is experimental
        try:
            import qwen_asr  # noqa: F401

            return True
        except Exception:
            pass
        if (os.environ.get("SUBPARTY_QWEN3_ASR") or "").strip() in {"1", "true", "yes"}:
            try:
                import transformers  # noqa: F401

                return True
            except Exception:
                return False
        return False

    def status(self) -> dict[str, Any]:
        has_pkg = False
        try:
            import qwen_asr  # noqa: F401

            has_pkg = True
        except Exception:
            pass
        return {
            "id": self.id,
            "available": self.available(),
            "license": self.license,
            "role": "compare",
            "package_installed": has_pkg,
            "install": 'pip install -U qwen-asr  # Apache-2.0; prefer fresh env (pins transformers)',
            "model": self.model_id,
            "note": "Heavy deps; optional. Enable transformers fallback with SUBPARTY_QWEN3_ASR=1",
        }

    def transcribe_wav(
        self,
        wav_path: Path | str,
        *,
        language: str = "zh",
        audio_duration_s: float | None = None,
    ) -> AsrRunResult:
        from asr_io import load_wav_mono_f32

        t0 = time.perf_counter()
        audio, sr = load_wav_mono_f32(wav_path)
        dur = float(audio_duration_s) if audio_duration_s else (len(audio) / max(1, sr))
        try:
            try:
                from qwen_asr import Qwen3ASRModel  # type: ignore
            except Exception:
                Qwen3ASRModel = None  # type: ignore

            if Qwen3ASRModel is not None:
                # API may evolve — keep defensive
                model = getattr(Qwen3ASRModel, "from_pretrained", None)
                if model is None:
                    raise RuntimeError("qwen_asr.Qwen3ASRModel.from_pretrained missing")
                asr = Qwen3ASRModel.from_pretrained(self.model_id)  # type: ignore[misc]
                # Common pattern: model.transcribe(audio=path) or generate
                text = ""
                if hasattr(asr, "transcribe"):
                    out = asr.transcribe(str(wav_path))
                    if isinstance(out, str):
                        text = out
                    elif isinstance(out, dict):
                        text = str(out.get("text") or out.get("transcript") or "")
                    elif isinstance(out, list) and out:
                        text = str(getattr(out[0], "text", out[0]))
                    else:
                        text = str(out)
                else:
                    raise RuntimeError("qwen_asr model has no transcribe()")
                segs = (
                    [AsrSegment(start=0.0, end=dur, text=text.strip(), confidence=0.7)]
                    if text.strip()
                    else []
                )
                return AsrRunResult(
                    engine_id=self.id,
                    ok=True,
                    segments=segs,
                    infer_s=time.perf_counter() - t0,
                    audio_s=dur,
                    meta={"backend": "qwen-asr", "model": self.model_id},
                )

            # Transformers path (optional)
            raise RuntimeError(
                "qwen-asr not installed; pip install -U qwen-asr (Apache-2.0) in a dedicated env"
            )
        except Exception as exc:
            return AsrRunResult(
                engine_id=self.id,
                ok=False,
                infer_s=time.perf_counter() - t0,
                audio_s=dur,
                error=f"{type(exc).__name__}: {exc}",
                meta={"license": self.license},
            )


class FunAsrAdapter:
    """FunASR optional Chinese ASR (typically Apache-2.0 models)."""

    id = "funasr"
    license = "Apache-2.0 (confirm model card)"
    model_id = "paraformer-zh"

    def available(self) -> bool:
        try:
            import funasr  # noqa: F401

            return True
        except Exception:
            return False

    def status(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "available": self.available(),
            "license": self.license,
            "role": "optional",
            "install": "pip install funasr modelscope",
            "model": self.model_id,
        }

    def transcribe_wav(
        self,
        wav_path: Path | str,
        *,
        language: str = "zh",
        audio_duration_s: float | None = None,
    ) -> AsrRunResult:
        from asr_io import load_wav_mono_f32

        t0 = time.perf_counter()
        audio, sr = load_wav_mono_f32(wav_path)
        dur = float(audio_duration_s) if audio_duration_s else (len(audio) / max(1, sr))
        try:
            from funasr import AutoModel

            model = AutoModel(model=self.model_id, disable_update=True)
            res = model.generate(input=str(wav_path))
            text = ""
            if isinstance(res, list) and res:
                item = res[0]
                if isinstance(item, dict):
                    text = str(item.get("text") or "")
                else:
                    text = str(item)
            segs = (
                [AsrSegment(start=0.0, end=dur, text=text.strip(), confidence=0.65)]
                if text.strip()
                else []
            )
            return AsrRunResult(
                engine_id=self.id,
                ok=True,
                segments=segs,
                infer_s=time.perf_counter() - t0,
                audio_s=dur,
                meta={"model": self.model_id},
            )
        except Exception as exc:
            return AsrRunResult(
                engine_id=self.id,
                ok=False,
                infer_s=time.perf_counter() - t0,
                audio_s=dur,
                error=f"{type(exc).__name__}: {exc}",
            )


def list_asr_adapters() -> list[AsrAdapter]:
    return [FasterWhisperAdapter(), Qwen3AsrAdapter(), FunAsrAdapter()]


def asr_registry_status() -> list[dict[str, Any]]:
    return [a.status() for a in list_asr_adapters()]


def run_asr_engine(
    engine_id: str,
    wav_path: Path | str,
    *,
    language: str = "zh",
    audio_duration_s: float | None = None,
) -> AsrRunResult:
    for a in list_asr_adapters():
        if a.id == engine_id or a.id.startswith(engine_id):
            return a.transcribe_wav(
                wav_path, language=language, audio_duration_s=audio_duration_s
            )
    return AsrRunResult(
        engine_id=engine_id, ok=False, error=f"unknown engine_id: {engine_id}"
    )
