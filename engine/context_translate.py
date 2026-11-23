"""Dialogue context window + glossary-aware ZH→EN translation."""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from names import extract_zh_name_candidates, lock_names_in_en, remember_en_names
from translate import translate_zh_to_en as _bare_translate

logger = logging.getLogger("subparty.context")

_SENT_SPLIT = re.compile(r"(?<=[。！？!?.])\s*")
_PRONOUN_HINTS = (
    (re.compile(r"他"), "he"),
    (re.compile(r"她"), "she"),
    (re.compile(r"它"), "it"),
    (re.compile(r"他们|他們"), "they"),
    (re.compile(r"她们|她們"), "they"),
)


@dataclass
class DialogueContext:
    """Rolling window of prior/upcoming ZH lines for translation consistency."""

    window: int = 6
    prior_zh: deque[str] = field(default_factory=deque)
    prior_en: deque[str] = field(default_factory=deque)
    glossary: Any | None = None
    engine_id: str | None = None
    _en_names: deque[str] = field(default_factory=deque)
    _zh_to_en_names: dict[str, str] = field(default_factory=dict)
    _last_gender_pronoun: str | None = None

    def translate(self, zh: str, upcoming: list[str] | None = None) -> str:
        zh = (zh or "").strip()
        if not zh:
            return ""

        masked, mapping = (zh, {})
        if self.glossary and self.glossary.terms:
            masked, mapping = self.glossary.protected_translate_input(zh)

        # Also protect session-learned character names
        for zh_name, en_name in sorted(self._zh_to_en_names.items(), key=lambda kv: len(kv[0]), reverse=True):
            if zh_name and zh_name in masked and zh_name not in mapping.values():
                key = f"XNAME{len(mapping)}X"
                masked = masked.replace(zh_name, key)
                mapping[key] = en_name

        upcoming_clean = [u.strip() for u in (upcoming or []) if u and u.strip() and u.strip() != zh]
        upcoming_masked: list[str] = []
        for u in upcoming_clean[: max(1, self.window // 2)]:
            um = u
            if self.glossary and self.glossary.terms:
                um, extra = self.glossary.protected_translate_input(u)
                mapping.update(extra)
            upcoming_masked.append(um)

        def _tr(text: str) -> str:
            return _bare_translate(text, self.engine_id)

        en = _tr(masked)

        prior = list(self.prior_zh)[-self.window :]
        if prior or upcoming_masked:
            try:
                block_parts = prior + [masked] + upcoming_masked
                compact: list[str] = []
                for p in block_parts:
                    if not compact or compact[-1] != p:
                        compact.append(p)
                block_zh = "。".join(compact)
                if not block_zh.endswith(("。", "！", "？", ".", "!", "?")):
                    block_zh += "。"
                # Prefix a tiny speaker memory hint for the engine (stripped after)
                hint_names = list(self._en_names)[-4:]
                if hint_names:
                    block_zh = f"（{'、'.join(hint_names)}）" + block_zh
                block_en = _tr(block_zh)
                # Drop leading parenthetical if model echoed it
                block_en = re.sub(r"^\([^)]*\)\s*", "", block_en)
                cur_idx = compact.index(masked) if masked in compact else len(prior)
                # Adjust index if we injected a hint sentence
                picked = _nth_sentence(block_en, cur_idx)
                if picked and len(picked) >= 2:
                    if 0.35 * max(len(en), 1) <= len(picked) <= 2.8 * len(en) + 16:
                        en = picked
            except Exception as exc:
                logger.debug("context translate failed: %s", exc)

        if mapping:
            en = self.glossary.restore_placeholders(en, mapping) if self.glossary else _restore(en, mapping)

        preferred = dict(self.glossary.terms) if self.glossary else {}
        preferred.update(self._zh_to_en_names)
        en = lock_names_in_en(en, preferred=preferred, prior_en_names=list(self._en_names))
        en = self._stabilize_pronouns(zh, en)

        self._maybe_learn(zh, en)
        for n in remember_en_names(en):
            self._en_names.append(n)
        while len(self._en_names) > 24:
            self._en_names.popleft()

        self.prior_zh.append(zh)
        self.prior_en.append(en)
        while len(self.prior_zh) > self.window:
            self.prior_zh.popleft()
            self.prior_en.popleft()
        return en.strip()

    def _stabilize_pronouns(self, zh: str, en: str) -> str:
        """Keep he/she stable across nearby lines when ZH pronoun repeats."""
        for cre, eng in _PRONOUN_HINTS:
            if cre.search(zh):
                if self._last_gender_pronoun and eng in {"he", "she"} and self._last_gender_pronoun != eng:
                    # Prefer the established pronoun for the scene if ZH didn't clearly switch
                    if self._last_gender_pronoun in {"he", "she"} and eng in {"he", "she"}:
                        # Only rewrite leading subject pronoun once
                        en2 = re.sub(rf"\b{eng}\b", self._last_gender_pronoun, en, count=1, flags=re.IGNORECASE)
                        if en2 != en:
                            en = en2
                            eng = self._last_gender_pronoun
                self._last_gender_pronoun = eng
                break
        return en

    def _maybe_learn(self, zh: str, en: str) -> None:
        candidates = extract_zh_name_candidates(zh)
        en_names = remember_en_names(en)
        if candidates and en_names:
            # Pair shortest ZH candidate with first EN proper noun when lengths look name-like
            zh_name = candidates[0]
            en_name = en_names[0]
            if 2 <= len(zh_name) <= 3 and len(en_name.split()) <= 3:
                self._zh_to_en_names.setdefault(zh_name, en_name)
                if self.glossary:
                    self.glossary.remember(zh_name, en_name, persist=True)
        if not self.glossary:
            return
        compact = re.sub(r"\s+", "", zh)
        if 2 <= len(compact) <= 3 and re.fullmatch(r"[\u4e00-\u9fff]+", compact):
            en_clean = en.strip().rstrip(".!?")
            if en_clean and en_clean[0].isupper() and len(en_clean.split()) <= 3:
                if "XNAME" in en_clean.upper():
                    return
                self.glossary.remember(compact, en_clean, persist=True)


def _restore(en: str, mapping: dict[str, str]) -> str:
    out = en or ""
    for key, en_term in mapping.items():
        out = re.sub(re.escape(key), en_term, out, flags=re.IGNORECASE)
    return out


def _nth_sentence(text: str, index: int) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p.strip()]
    if not parts:
        return text
    if index < 0:
        return parts[0]
    if index >= len(parts):
        return parts[-1]
    return parts[index]
