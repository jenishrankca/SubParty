"""Light EN postfilters for Live Argos (dedupe / garbage / echo).

Keep cheap — no LLM. Applied after translate; Quality can reuse the same helpers.
"""

from __future__ import annotations

import re
from typing import Any

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_WS_RE = re.compile(r"\s+")
# "the the the" / "go go go go"
_REPEAT_WORD_RE = re.compile(r"\b([A-Za-z][\w']*)(?:\s+\1){2,}\b", re.IGNORECASE)
# "hello hello" (exactly once duplicate ok for emphasis; 3+ collapsed)
_DOUBLE_WORD_RE = re.compile(r"\b([A-Za-z][\w']*)(?:\s+\1){1,}\b", re.IGNORECASE)
# Phrase repetition: "I know I know I know"
_REPEAT_PHRASE_RE = re.compile(
    r"\b((?:[A-Za-z][\w']*\s+){1,4}[A-Za-z][\w']*)(?:\s+\1){1,}\b",
    re.IGNORECASE,
)


def dedupe_repeated_tokens(en: str) -> str:
    """Collapse runaway word/phrase repetition from Argos."""
    text = _WS_RE.sub(" ", (en or "").strip())
    if not text:
        return ""
    prev = None
    for _ in range(4):
        text = _REPEAT_PHRASE_RE.sub(r"\1", text)
        text = _REPEAT_WORD_RE.sub(r"\1", text)
        # Keep at most one immediate duplicate ("very very" → "very")
        text = _DOUBLE_WORD_RE.sub(r"\1", text)
        text = _WS_RE.sub(" ", text).strip()
        if text == prev:
            break
        prev = text
    return text


def is_garbage_en(en: str, *, zh: str = "") -> bool:
    """True when EN is empty, CJK echo, or pathological repetition / nonsense."""
    raw = (en or "").strip()
    if not raw:
        return True
    zh_c = re.sub(r"\s+", "", zh or "")
    en_c = re.sub(r"\s+", "", raw)
    # Echo of Chinese (Argos sometimes returns source on failure)
    if zh_c and en_c == zh_c:
        return True
    if _CJK_RE.search(raw) and not _LATIN_WORD_RE.search(raw):
        return True
    words = _LATIN_WORD_RE.findall(raw)
    if not words:
        return True
    # Too many CJK chars mixed into "EN"
    cjk_n = len(_CJK_RE.findall(raw))
    if cjk_n >= 3 and cjk_n >= len(words):
        return True
    # Runaway length vs ZH
    if zh_c and len(raw) > max(80, 8 * len(zh_c) + 40):
        return True
    # Same token ≥40% of words (and ≥4 occurrences)
    lower = [w.lower() for w in words]
    if len(lower) >= 4:
        from collections import Counter

        top, cnt = Counter(lower).most_common(1)[0]
        if cnt >= 4 and cnt / len(lower) >= 0.4 and len(top) >= 2:
            return True
    # Still has triple repeats after naive check
    if _REPEAT_WORD_RE.search(raw):
        return True
    return False


def clean_en(en: str, *, zh: str = "", fallback: str = "") -> str:
    """
    Deduplicate + drop garbage. Prefer ``fallback`` (usually bare provisional)
    when raw is garbage / runaway repetition, or cleaned result is still bad.
    """
    raw = (en or "").strip()
    cleaned = dedupe_repeated_tokens(raw)
    fb = dedupe_repeated_tokens(fallback) if fallback else ""
    fb_ok = bool(fb and not is_garbage_en(fb, zh=zh))

    # Prefer clean provisional when input failed quality (even if dedupe shrinks it)
    if fb_ok and is_garbage_en(raw, zh=zh):
        return fb
    if cleaned and not is_garbage_en(cleaned, zh=zh):
        # Collapsed runaway → stub; prefer fuller provisional sentence
        if fb_ok and len(raw.split()) >= 5 and len(cleaned.split()) <= 2 and len(fb.split()) >= 2:
            return fb
        return cleaned
    if fb_ok:
        return fb
    return ""



def sanitize_translation(en: str, *, zh: str, bare: str | None = None) -> dict[str, Any]:
    """Return cleaned EN + diagnostics for callers/tests."""
    bare_s = (bare if bare is not None else "").strip()
    out = clean_en(en, zh=zh, fallback=bare_s)
    return {
        "text_en": out,
        "was_garbage": is_garbage_en(en or "", zh=zh),
        "used_fallback": bool(out and bare_s and out == dedupe_repeated_tokens(bare_s) and out != dedupe_repeated_tokens(en or "")),
        "raw": (en or "").strip(),
    }
