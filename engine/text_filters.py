"""Drop quality tags / site watermarks; keep real Simplified Chinese dialogue."""

from __future__ import annotations

import re
from dataclasses import dataclass

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")

# Resolution / quality / encode tags (often OCR'd from overlays)
_QUALITY_RE = re.compile(
    r"(?ix)"
    r"("
    r"\b(?:4k|8k|2k|720p|1080p|1440p|2160p|480p|360p)\b"
    r"|超清|高清|蓝光|藍光|原画|原畫|修复|修復|枪版|槍版|清晰"
    r"|无损|無損|杜比|视界|視界"
    r"|\b(?:hdr|hevc|h\.?265|h\.?264|x264|x265|avc|aac|flac|web-?dl|bluray|remux|encode)\b"
    r"|\b(?:uhd|fhd|hd|sd)\b"
    r")"
)

# Chinese video-site / ripper watermarks (incl. OCR misspellings like Lilibili)
_SITE_RE = re.compile(
    r"(?ix)"
    r"("
    r"bilibili|bili\s*bili|lilibili|哩哩哔哩|哔哩哔哩|嗶哩嗶哩"
    r"|b站|B站"
    r"|优酷|優酷|youku"
    r"|爱奇艺|愛奇藝|iqiyi|qiyi"
    r"|腾讯视频|騰訊視頻|tencent"
    r"|芒果|mgtv|芒果TV"
    r"|西瓜视频|西瓜視頻"
    r"|抖音|douyin|tiktok"
    r"|youtube|youtu\.?be"
    r"|搜狐|sohu|pptv|乐视|樂視"
    r"|字幕组|字幕組|压制组|壓制組|发布组|發布組"
    r"|国漫|國漫|动漫屋"
    r")"
)

# "4K修复超清2590" style: quality word(s) glued to digits / bitrate-ish numbers
_QUALITY_DIGIT_RE = re.compile(
    r"(?ix)"
    r"(?:4k|8k|720p|1080p|2160p|超清|高清|蓝光|修復|修复).{0,8}\d{3,}"
    r"|\d{3,}.{0,8}(?:4k|8k|720p|1080p|2160p|超清|高清|蓝光|修復|修复)"
)

# Mostly latin + digits chrome (channel IDs), little dialogue
_MOSTLY_LATIN_DIGITS = re.compile(r"^[\sA-Za-z0-9\-_.|/·•:：]+$")


# Lines / cues below this OCR score are flagged uncertain for review
UNCERTAIN_CONFIDENCE = 0.78


@dataclass
class OcrLine:
    text: str
    # Normalized box in the OCR image (0..1): center_x, center_y, width, height
    cx: float = 0.5
    cy: float = 0.5
    w: float = 0.0
    h: float = 0.0
    confidence: float = 1.0


@dataclass
class FilteredOcr:
    text: str
    confidence: float = 1.0

    @property
    def uncertain(self) -> bool:
        return bool(self.text) and self.confidence < UNCERTAIN_CONFIDENCE


def is_junk_line(text: str) -> bool:
    """True if this OCR line is a watermark / quality tag, not dialogue."""
    raw = (text or "").strip()
    if not raw:
        return True

    compact = re.sub(r"\s+", "", raw)
    if not compact:
        return True

    # Pure latin/digit chrome
    if _MOSTLY_LATIN_DIGITS.match(compact):
        return True

    # Must have some CJK for Chinese hardsubs v1
    if not _CJK_RE.search(compact):
        return True

    if _SITE_RE.search(raw) or _SITE_RE.search(compact):
        return True
    if _QUALITY_RE.search(raw) or _QUALITY_RE.search(compact):
        return True
    if _QUALITY_DIGIT_RE.search(compact):
        return True

    # Short tags that are mostly quality/site fragments after stripping digits
    stripped = re.sub(r"[\d\s\-_.|/·•:：]+", "", compact)
    if stripped and (_SITE_RE.search(stripped) or _QUALITY_RE.search(stripped)):
        return True

    # Very short CJK with heavy digits (e.g. "超清2590") — not dialogue
    cjk_chars = _CJK_RE.findall(compact)
    digit_count = sum(ch.isdigit() for ch in compact)
    if len(cjk_chars) <= 4 and digit_count >= 3 and _QUALITY_RE.search(compact):
        return True

    # High latin ratio mixed into a short watermark-like string
    latin = len(_LATIN_RE.findall(compact))
    if latin >= 4 and len(cjk_chars) <= 6 and digit_count >= 2:
        return True

    return False


def is_dialogue_position(
    line: OcrLine,
    *,
    prefer_center: bool = True,
    max_edge_frac: float = 0.18,
    center_min: float = 0.22,
    center_max: float = 0.78,
) -> bool:
    """
    Within the bottom subtitle-band crop, keep center-ish lines.
    Reject left/right edge boxes typical of corner watermarks.
    """
    if not prefer_center:
        return True
    # No geometry → keep (text filters still apply)
    if line.w <= 0 and line.h <= 0 and line.cx == 0.5 and line.cy == 0.5:
        return True

    # Edge rejection: box center near left/right of band
    if line.cx < max_edge_frac or line.cx > (1.0 - max_edge_frac):
        # Allow if the text is wide and clearly spans center (true centered subs)
        left = line.cx - line.w / 2
        right = line.cx + line.w / 2
        if right < center_min or left > center_max:
            return False

    # Prefer lines whose horizontal span overlaps the center region
    left = line.cx - line.w / 2
    right = line.cx + line.w / 2
    overlaps_center = right >= center_min and left <= center_max
    return overlaps_center


def filter_ocr_result(lines: list[OcrLine]) -> FilteredOcr:
    """Return kept dialogue text + min confidence, or empty if nothing usable."""
    kept: list[OcrLine] = []
    for line in lines:
        text = re.sub(r"\s+", " ", (line.text or "").strip())
        if not text:
            continue
        if is_junk_line(text):
            continue
        if not is_dialogue_position(line):
            continue
        kept.append(
            OcrLine(
                text=text,
                cx=line.cx,
                cy=line.cy,
                w=line.w,
                h=line.h,
                confidence=float(line.confidence or 0.0),
            )
        )
    # De-dupe consecutive identical text (keep higher confidence)
    out: list[OcrLine] = []
    for ln in kept:
        if out and out[-1].text == ln.text:
            if ln.confidence > out[-1].confidence:
                out[-1] = ln
            continue
        out.append(ln)
    if not out:
        return FilteredOcr(text="", confidence=0.0)
    conf = min(ln.confidence for ln in out)
    return FilteredOcr(text="\n".join(ln.text for ln in out), confidence=conf)


def filter_ocr_lines(lines: list[OcrLine]) -> str:
    """Return kept dialogue text (newline-joined), or empty if nothing usable."""
    return filter_ocr_result(lines).text


def normalize_ocr_text(text: str) -> str:
    """Filter multi-line plain OCR string without geometry."""
    lines = [
        OcrLine(text=re.sub(r"\s+", " ", ln).strip())
        for ln in (text or "").splitlines()
        if ln.strip()
    ]
    # If single blob with spaces, also try as one line (common for "4K修复超清2590 Lilibili")
    if len(lines) == 1 and " " in lines[0].text:
        # Drop if the whole blob is junk
        if is_junk_line(lines[0].text):
            return ""
        # Split tokens and filter parts that are junk, keep dialogue fragments
        parts = [p for p in lines[0].text.split(" ") if p]
        kept = [p for p in parts if not is_junk_line(p) and _CJK_RE.search(p)]
        # If splitting removed everything junk-like but leftover is still tagged, drop
        joined = " ".join(kept).strip()
        if not joined or is_junk_line(joined):
            return ""
        return joined
    return filter_ocr_lines(lines)
