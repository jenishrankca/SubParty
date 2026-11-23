"""Model management: list / ensure Argos pack and OCR backends."""

from __future__ import annotations

import logging
from typing import Any

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
        }
    )

    # Optional NLLB
    nllb = False
    try:
        import transformers  # noqa: F401
        import torch  # noqa: F401

        nllb = True
    except Exception:
        nllb = False
    models.append(
        {
            "id": "nllb-200-distilled-600M",
            "name": "NLLB-200 distilled 600M",
            "kind": "translate",
            "installed": nllb,
            "engine": "nllb",
            "optional": True,
        }
    )
    return models


def ensure_model(model_id: str) -> dict[str, Any]:
    mid = (model_id or "").strip()
    if mid == "argos-zh-en":
        from engines import ArgosEngine

        ArgosEngine().warm()
        return next(m for m in list_models() if m["id"] == mid)
    if mid == "rapidocr-ppocrv6":
        from ocr import warm_ocr

        warm_ocr()
        return next(m for m in list_models() if m["id"] == mid)
    if mid == "nllb-200-distilled-600M":
        from engines import NLLBEngine

        eng = NLLBEngine()
        if not eng.available():
            raise RuntimeError("Install transformers + torch to use NLLB")
        eng.warm()
        return next(m for m in list_models() if m["id"] == mid)
    raise ValueError(f"Unknown model: {model_id}")
