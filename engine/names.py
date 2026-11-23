"""Character-name consistency helpers for ZH→EN dialogue."""

from __future__ import annotations

import re
from collections import Counter

_CJK = re.compile(r"[\u4e00-\u9fff]{2,4}")
_NAME_EN = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b")


def extract_zh_name_candidates(zh: str) -> list[str]:
    """Heuristic: short CJK spans that often are names (2–3 chars preferred)."""
    zh = re.sub(r"\s+", "", zh or "")
    out: list[str] = []
    # Prefer 2–3 char windows at the start of clauses (common name position)
    for n in (3, 2):
        for i in range(0, max(0, len(zh) - n + 1)):
            span = zh[i : i + n]
            if _CJK.fullmatch(span):
                out.append(span)
        if out:
            break
    # Deduplicate preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def lock_names_in_en(
    en: str,
    *,
    preferred: dict[str, str],
    prior_en_names: list[str],
) -> str:
    """
    Encourage consistent English renderings:
    - If glossary preferred EN names appear as variants, normalize casing/spacing.
    - Reuse prior EN names when a close variant shows up.
    """
    out = en or ""
    # Prefer exact glossary English forms already known
    for en_name in sorted(set(preferred.values()), key=len, reverse=True):
        if not en_name:
            continue
        # Collapse spaced / case variants of the same tokens
        parts = en_name.split()
        if len(parts) >= 2:
            loose = r"\s+".join(re.escape(p) for p in parts)
            out = re.sub(loose, en_name, out, flags=re.IGNORECASE)
        else:
            out = re.sub(rf"\b{re.escape(en_name)}\b", en_name, out, flags=re.IGNORECASE)

    # If prior dialogue used a name and current has a near-identical token, reuse prior form
    counts = Counter(prior_en_names)
    for name, _ in counts.most_common(12):
        if not name or " " not in name and len(name) < 3:
            continue
        # Replace lowercase/title variants
        out = re.sub(rf"\b{re.escape(name)}\b", name, out, flags=re.IGNORECASE)
    return out


def remember_en_names(en: str) -> list[str]:
    return _NAME_EN.findall(en or "")
