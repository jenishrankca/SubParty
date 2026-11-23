"""Chinese OCR for subtitle bands.

Backends (RapidOCR / ONNX):
  - ``ppocrv6_small`` (default) — explicit PP-OCRv6 det/rec small
  - ``rapidocr`` — legacy alias of the previous implicit RapidOCR init
  - ``ppocrv5_mobile`` — compare / rollback path

DeepSeek-OCR-2 is an optional uncertain-frame stub only (never every-frame).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, Literal

from text_filters import OcrLine

logger = logging.getLogger("subparty.ocr")

OcrBackendName = Literal["ppocrv6_small", "rapidocr", "ppocrv5_mobile"]

_BACKEND_OVERRIDE: str | None = None


def set_ocr_backend(name: str | None) -> None:
    """Force OCR backend for benches/tests. Clears engine cache."""
    global _BACKEND_OVERRIDE
    _BACKEND_OVERRIDE = (name or "").strip().lower() or None
    _engine.cache_clear()


def get_ocr_backend() -> str:
    if _BACKEND_OVERRIDE:
        return _BACKEND_OVERRIDE
    env = (os.environ.get("SUBPARTY_OCR") or "ppocrv6_small").strip().lower()
    if env in {"rapidocr", "legacy"}:
        return "rapidocr"
    if env in {"ppocrv5", "ppocrv5_mobile", "v5"}:
        return "ppocrv5_mobile"
    return "ppocrv6_small"


def list_ocr_backends() -> list[dict[str, Any]]:
    return [
        {
            "id": "ppocrv6_small",
            "label": "PP-OCRv6 small (explicit)",
            "default": True,
            "note": "Det/Rec small ONNX via RapidOCR",
        },
        {
            "id": "rapidocr",
            "label": "RapidOCR legacy init",
            "default": False,
            "note": "Previous SubParty params (also PP-OCRv6 under the hood)",
        },
        {
            "id": "ppocrv5_mobile",
            "label": "PP-OCRv5 mobile",
            "default": False,
            "note": "Compare / rollback",
        },
    ]


def _build_engine(backend: str) -> Any:
    from rapidocr import RapidOCR

    if backend == "ppocrv6_small":
        from rapidocr.utils.typings import ModelType, OCRVersion

        params = {
            "Global.text_score": 0.5,
            "Global.use_cls": False,
            "Det.ocr_version": OCRVersion.PPOCRV6,
            "Rec.ocr_version": OCRVersion.PPOCRV6,
            "Det.model_type": ModelType.SMALL,
            "Rec.model_type": ModelType.SMALL,
            "EngineConfig.onnxruntime.use_dml": True,
            "EngineConfig.onnxruntime.use_cuda": False,
        }
        label = "PP-OCRv6 small (explicit)"
    elif backend == "ppocrv5_mobile":
        from rapidocr.utils.typings import ModelType, OCRVersion

        params = {
            "Global.text_score": 0.5,
            "Global.use_cls": False,
            "Det.ocr_version": OCRVersion.PPOCRV5,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Det.model_type": ModelType.MOBILE,
            "Rec.model_type": ModelType.MOBILE,
            "EngineConfig.onnxruntime.use_dml": True,
            "EngineConfig.onnxruntime.use_cuda": False,
        }
        label = "PP-OCRv5 mobile"
    else:
        # Legacy SubParty RapidOCR init (pre-explicit version keys)
        params = {
            "Global.text_score": 0.5,
            "Global.use_cls": False,
            "EngineConfig.onnxruntime.use_dml": True,
            "EngineConfig.onnxruntime.use_cuda": False,
        }
        label = "RapidOCR legacy"

    try:
        engine = RapidOCR(params=params)
        logger.info("OCR backend: %s (DML preferred, cls=off)", label)
        return engine
    except Exception as exc:
        logger.warning("OCR DML init failed for %s (%s); falling back", backend, exc)
        soft = {k: v for k, v in params.items() if "use_dml" not in k and "use_cuda" not in k}
        soft["Global.use_cls"] = False
        engine = RapidOCR(params=soft)
        logger.info("OCR backend: %s fallback (cls=off)", label)
        return engine


@lru_cache(maxsize=4)
def _engine(backend: str = "ppocrv6_small") -> Any:
    return _build_engine(backend)


def warm_ocr() -> None:
    _engine(get_ocr_backend())


def ocr_lines(path: str) -> list[OcrLine]:
    backend = get_ocr_backend()
    out = _engine(backend)(path)
    lines = _parse_rapid_output(out, path)
    # Optional uncertain-path: DeepSeek only when enabled AND all lines weak
    if lines and _should_try_uncertain(lines):
        alt = ocr_uncertain_deepseek(path, primary=lines)
        if alt:
            return alt
    return lines


def ocr_image(path: str) -> str:
    return "\n".join(ln.text for ln in ocr_lines(path)).strip()


def _should_try_uncertain(lines: list[OcrLine]) -> bool:
    if not os.environ.get("SUBPARTY_DEEPSEEK_OCR"):
        return False
    if not lines:
        return True
    confs = [float(ln.confidence or 0.0) for ln in lines]
    return min(confs) < 0.45 or (sum(confs) / len(confs)) < 0.55


def ocr_uncertain_deepseek(
    path: str,
    *,
    primary: list[OcrLine] | None = None,
) -> list[OcrLine] | None:
    """
    Optional second-pass for low-confidence / empty crops.

    Stub only — not loaded every frame. Enable with SUBPARTY_DEEPSEEK_OCR=1
    when a DeepSeek-OCR-2 adapter is implemented.
    """
    if not os.environ.get("SUBPARTY_DEEPSEEK_OCR"):
        return None
    logger.info(
        "DeepSeek-OCR-2 stub invoked for %s (primary_lines=%s) — not implemented",
        path,
        len(primary or []),
    )
    # Keep primary; do not invent text
    return None


def _parse_rapid_output(out: Any, path: str) -> list[OcrLine]:
    if out is None:
        return []

    img = getattr(out, "img", None)
    ih, iw = _image_size(img, path)

    txts = getattr(out, "txts", None)
    boxes = getattr(out, "boxes", None)
    scores = getattr(out, "scores", None)
    if txts is not None:
        lines: list[OcrLine] = []
        for i, t in enumerate(txts):
            text = str(t).strip()
            if not text:
                continue
            box = boxes[i] if boxes is not None and i < len(boxes) else None
            score = _score_at(scores, i)
            lines.append(_line_from_box(text, box, iw, ih, score))
        return lines

    payload = out[0] if isinstance(out, tuple) and out else out
    if isinstance(payload, list):
        lines = []
        for row in payload:
            if not row or not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            text = str(row[1]).strip()
            if not text:
                continue
            box = row[0] if isinstance(row[0], (list, tuple)) else None
            score = float(row[2]) if len(row) > 2 and row[2] is not None else 1.0
            lines.append(_line_from_box(text, box, iw, ih, score))
        return lines
    return []


def _score_at(scores: Any, index: int) -> float:
    if scores is None:
        return 1.0
    try:
        if index < len(scores):
            return float(scores[index])
    except Exception:
        pass
    return 1.0


def _image_size(img: Any, path: str) -> tuple[int, int]:
    if img is not None:
        try:
            return int(img.shape[0]), int(img.shape[1])
        except Exception:
            pass
    try:
        from PIL import Image

        with Image.open(path) as im:
            w, h = im.size
            return h, w
    except Exception:
        return 1, 1


def _line_from_box(text: str, box: Any, iw: int, ih: int, confidence: float = 1.0) -> OcrLine:
    conf = max(0.0, min(1.0, float(confidence if confidence is not None else 1.0)))
    if box is None or iw <= 0 or ih <= 0:
        return OcrLine(text=text, confidence=conf)
    try:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        cx = ((min_x + max_x) / 2) / float(iw)
        cy = ((min_y + max_y) / 2) / float(ih)
        w = max(0.0, (max_x - min_x) / float(iw))
        h = max(0.0, (max_y - min_y) / float(ih))
        return OcrLine(text=text, cx=cx, cy=cy, w=w, h=h, confidence=conf)
    except Exception:
        return OcrLine(text=text, confidence=conf)
