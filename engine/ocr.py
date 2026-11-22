"""Chinese OCR for subtitle bands (RapidOCR / ONNX DirectML when available)."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from text_filters import OcrLine

logger = logging.getLogger("subparty.ocr")


@lru_cache(maxsize=1)
def _engine() -> Any:
    from rapidocr import RapidOCR

    params = {
        "Global.text_score": 0.5,
        "Global.use_cls": False,  # skip angle cls — big win for hardsubs
        "EngineConfig.onnxruntime.use_dml": True,
        "EngineConfig.onnxruntime.use_cuda": False,
    }
    try:
        engine = RapidOCR(params=params)
        logger.info("OCR backend: RapidOCR (DML preferred, cls=off)")
        return engine
    except Exception as exc:
        logger.warning("OCR DML init failed (%s); falling back", exc)
        engine = RapidOCR(params={"Global.use_cls": False})
        logger.info("OCR backend: RapidOCR fallback (cls=off)")
        return engine


def warm_ocr() -> None:
    _engine()


def ocr_lines(path: str) -> list[OcrLine]:
    out = _engine()(path)
    if out is None:
        return []

    img = getattr(out, "img", None)
    ih, iw = _image_size(img, path)

    txts = getattr(out, "txts", None)
    boxes = getattr(out, "boxes", None)
    if txts is not None:
        lines: list[OcrLine] = []
        for i, t in enumerate(txts):
            text = str(t).strip()
            if not text:
                continue
            box = boxes[i] if boxes is not None and i < len(boxes) else None
            lines.append(_line_from_box(text, box, iw, ih))
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
            lines.append(_line_from_box(text, box, iw, ih))
        return lines
    return []


def ocr_image(path: str) -> str:
    return "\n".join(ln.text for ln in ocr_lines(path)).strip()


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


def _line_from_box(text: str, box: Any, iw: int, ih: int) -> OcrLine:
    if box is None or iw <= 0 or ih <= 0:
        return OcrLine(text=text)
    try:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        cx = ((min_x + max_x) / 2) / float(iw)
        cy = ((min_y + max_y) / 2) / float(ih)
        w = max(0.0, (max_x - min_x) / float(iw))
        h = max(0.0, (max_y - min_y) / float(ih))
        return OcrLine(text=text, cx=cx, cy=cy, w=w, h=h)
    except Exception:
        return OcrLine(text=text)
