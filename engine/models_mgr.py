"""Model management: list / ensure Argos, OCR, NLLB, Qwen under data/models."""

from __future__ import annotations

import logging
from typing import Any

from engines import (
    NLLB_HF_ID,
    NLLB_LOCAL_NAME,
    QWEN_HF_ID,
    QWEN_LOCAL_NAME,
    ArgosEngine,
    NLLBEngine,
    QwenEngine,
    dir_has_weights,
    nllb_model_dir,
    qwen_model_dir,
)
from paths import models_dir

logger = logging.getLogger("subparty.models")


def list_models() -> list[dict[str, Any]]:
    models: list[dict[str, Any]] = []

    # Argos
    argos_installed = False
    try:
        import argostranslate.translate

        installed = argostranslate.translate.get_installed_languages()
        zh = next((l for l in installed if l.code == "zh"), None)
        en = next((l for l in installed if l.code == "en"), None)
        argos_installed = bool(zh and en and zh.get_translation(en))
    except Exception as exc:
        logger.debug("argos probe: %s", exc)
    models.append(
        {
            "id": "argos-zh-en",
            "name": "Argos Translate zh→en",
            "kind": "translate",
            "installed": argos_installed,
            "engine": "argos",
            "required": True,
            "size_hint": "~100 MB",
            "notes": "Default Live engine. Auto-downloads language pack on first use.",
            "first_run": "Ensure once so Live Process can translate without waiting mid-stream.",
        }
    )

    # OCR via RapidOCR package presence
    ocr_ok = False
    try:
        import rapidocr  # noqa: F401

        ocr_ok = True
    except Exception:
        ocr_ok = False
    models.append(
        {
            "id": "rapidocr-ppocrv6",
            "name": "RapidOCR PP-OCRv6 (Chinese)",
            "kind": "ocr",
            "installed": ocr_ok,
            "engine": "ocr",
            "required": True,
            "size_hint": "~50 MB",
            "notes": "ONNX DirectML when available.",
            "first_run": "Required for hardsub OCR. Warm once from Models before first Process.",
        }
    )

    nllb_ok = dir_has_weights(nllb_model_dir())
    models.append(
        {
            "id": NLLB_LOCAL_NAME,
            "name": "NLLB-200 distilled 600M",
            "kind": "translate",
            "installed": nllb_ok,
            "engine": "nllb",
            "optional": True,
            "size_hint": "~2.4 GB",
            "hf_id": NLLB_HF_ID,
            "path": str(nllb_model_dir()),
            "notes": "Select engine “NLLB-200 distilled 600M” after install. CPU OK; GPU if CUDA torch + ≥2.5GB free VRAM.",
        }
    )

    qwen_ok = dir_has_weights(qwen_model_dir())
    models.append(
        {
            "id": QWEN_LOCAL_NAME,
            "name": "Qwen2.5 1.5B Instruct",
            "kind": "translate",
            "installed": qwen_ok,
            "engine": "qwen",
            "optional": True,
            "size_hint": "~3.0 GB",
            "hf_id": QWEN_HF_ID,
            "path": str(qwen_model_dir()),
            "notes": (
                "Local free Qwen sized for 16GB RAM / 6GB VRAM (not 7B). "
                "Select engine “Qwen2.5 1.5B Instruct” after install. Only one of NLLB/Qwen stays loaded."
            ),
        }
    )

    from audio_verify import WHISPER_MODEL_ID, WHISPER_SIZE, whisper_available

    models.append(
        {
            "id": WHISPER_MODEL_ID,
            "name": f"faster-whisper {WHISPER_SIZE} (Chinese ASR)",
            "kind": "asr",
            "installed": whisper_available(),
            "engine": "audio",
            "optional": True,
            "size_hint": "~500 MB",
            "notes": (
                "Phase B audio verify. Fits 6GB VRAM / 16GB RAM (CUDA float16 or CPU int8). "
                "Enable Audio verify on Process or SUBPARTY_AUDIO_VERIFY=1."
            ),
        }
    )
    return models


def _download_hf(repo_id: str, local_dir: Any) -> None:
    from huggingface_hub import snapshot_download

    models_dir().mkdir(parents=True, exist_ok=True)
    logger.info("downloading %s → %s", repo_id, local_dir)
    snapshot_download(repo_id=repo_id, local_dir=str(local_dir))


def ensure_model(model_id: str) -> dict[str, Any]:
    mid = (model_id or "").strip()
    if mid == "argos-zh-en":
        ArgosEngine().warm()
        return next(m for m in list_models() if m["id"] == mid)
    if mid == "rapidocr-ppocrv6":
        from ocr import warm_ocr

        warm_ocr()
        return next(m for m in list_models() if m["id"] == mid)
    if mid in {NLLB_LOCAL_NAME, "nllb-200-distilled-600M", "nllb"}:
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except Exception as exc:
            raise RuntimeError(
                "Install transformers + torch + sentencepiece first "
                "(pip install -r requirements-ml.txt)"
            ) from exc
        dest = nllb_model_dir()
        if not dir_has_weights(dest):
            _download_hf(NLLB_HF_ID, dest)
        # Do not warm here — loading ~2GB during ensure can pressure 16GB RAM
        # alongside Electron. First Process/select loads on demand.
        if not NLLBEngine().available():
            raise RuntimeError("NLLB download finished but weights not detected")
        return next(m for m in list_models() if m["id"] == NLLB_LOCAL_NAME)
    if mid in {QWEN_LOCAL_NAME, "qwen", "qwen2.5-1.5b-instruct"}:
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except Exception as exc:
            raise RuntimeError(
                "Install transformers + torch + sentencepiece first "
                "(pip install -r requirements-ml.txt)"
            ) from exc
        dest = qwen_model_dir()
        if not dir_has_weights(dest):
            _download_hf(QWEN_HF_ID, dest)
        if not QwenEngine().available():
            raise RuntimeError("Qwen download finished but weights not detected")
        return next(m for m in list_models() if m["id"] == QWEN_LOCAL_NAME)
    if mid in {"faster-whisper-small", "faster-whisper", "whisper-small", "audio-asr"}:
        from audio_verify import ensure_whisper_model

        return ensure_whisper_model()
    raise ValueError(f"Unknown model: {model_id}")


def setup_checklist() -> dict[str, Any]:
    """First-run / setup clarity for UI (required vs optional models + Live/Quality)."""
    from pathlib import Path

    from ffmpeg_util import ffmpeg_path, ffprobe_path

    models = list_models()
    ffmpeg_ok = False
    ffprobe_ok = False
    try:
        ffmpeg_ok = Path(ffmpeg_path()).exists()
    except Exception:
        ffmpeg_ok = False
    try:
        ffprobe_ok = Path(ffprobe_path()).exists()
    except Exception:
        ffprobe_ok = False

    required = [m for m in models if m.get("required") or not m.get("optional")]
    optional = [m for m in models if m.get("optional")]
    missing_required = [m["id"] for m in required if not m.get("installed")]
    missing_optional = [m["id"] for m in optional if not m.get("installed")]

    steps: list[dict[str, Any]] = [
        {
            "id": "ffmpeg",
            "label": "FFmpeg + ffprobe",
            "ok": bool(ffmpeg_ok and ffprobe_ok),
            "required": True,
            "hint": "Bundled under tools/ or PATH",
        },
    ]
    for m in required:
        steps.append(
            {
                "id": m["id"],
                "label": m["name"],
                "ok": bool(m.get("installed")),
                "required": True,
                "hint": m.get("first_run") or m.get("notes") or "",
                "kind": m.get("kind"),
            }
        )
    for m in optional:
        steps.append(
            {
                "id": m["id"],
                "label": m["name"],
                "ok": bool(m.get("installed")),
                "required": False,
                "hint": m.get("notes") or "",
                "kind": m.get("kind"),
            }
        )

    ready = not missing_required and ffmpeg_ok and ffprobe_ok
    return {
        "ready": ready,
        "first_run": not ready,
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "steps": steps,
        "models_dir": str(models_dir()),
        "live_vs_quality": {
            "live": {
                "label": "Live",
                "summary": "Keep-up watch while you play. Argos, provisional cues, playhead-ahead OCR. Audio verify off.",
                "buffer_ahead_s": 2.5,
            },
            "quality": {
                "label": "Quality",
                "summary": "Offline heavy pass. Fine timing probes, optional ASR verify, deeper buffer before Ready.",
                "audio_verify": "optional (Verify checkbox)",
            },
        },
        "next_action": (
            "Open Models and Ensure missing required packs"
            if missing_required
            else ("Install FFmpeg" if not (ffmpeg_ok and ffprobe_ok) else "Ready — pick Live or Quality and Process")
        ),
    }
