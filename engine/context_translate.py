"""Dialogue context window + glossary-aware ZH→EN with candidate comparison."""

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


def pick_best_candidate(bare: str, contextual: str, *, zh: str) -> str:
    """Prefer contextual when length/content looks coherent; else bare."""
    bare = (bare or "").strip()
    contextual = (contextual or "").strip()
    if not contextual:
        return bare
    if not bare:
        return contextual
    if contextual == bare:
        return bare
    # Reject contextual if it swallowed neighboring sentences (too long)
    if len(contextual) > 2.8 * max(len(bare), 1) + 20:
        return bare
    if len(contextual) < 0.35 * max(len(bare), 1):
        return bare
    # Prefer candidate that preserves more Latin letters when ZH has no digits-only
    return contextual


@dataclass
class DialogueContext:
    """Rolling memory for translation consistency (works with DialogueBuffer)."""

    window: int = 6  # line-count fallback when buffer not used
    prior_zh: deque[str] = field(default_factory=deque)
    prior_en: deque[str] = field(default_factory=deque)
    glossary: Any | None = None
    engine_id: str | None = None
    _en_names: deque[str] = field(default_factory=deque)
    _zh_to_en_names: dict[str, str] = field(default_factory=dict)
    _last_gender_pronoun: str | None = None

    def translate(
        self,
        zh: str,
        upcoming: list[str] | None = None,
        prior_override: list[str] | None = None,
        compare_candidates: bool = True,
    ) -> str:
        zh = (zh or "").strip()
        if not zh:
            return ""

        masked, mapping = self._mask(zh)
        upcoming_clean = [u.strip() for u in (upcoming or []) if u and u.strip() and u.strip() != zh]
        upcoming_masked = [self._mask(u)[0] for u in upcoming_clean]

        def _tr(text: str) -> str:
            return _bare_translate(text, self.engine_id)

        bare = _tr(masked)

        prior = (
            list(prior_override)
            if prior_override is not None
            else list(self.prior_zh)[-self.window :]
        )
        # Mask prior for glossary consistency
        prior_masked = [self._mask(p)[0] for p in prior[-48:]]

        contextual = bare
        if prior_masked or upcoming_masked:
            try:
                block_parts = prior_masked + [masked] + upcoming_masked[:24]
                compact: list[str] = []
                for p in block_parts:
                    if not compact or compact[-1] != p:
                        compact.append(p)
                block_zh = "。".join(compact)
                if not block_zh.endswith(("。", "！", "？", ".", "!", "?")):
                    block_zh += "。"
                block_en = _tr(block_zh)
                cur_idx = compact.index(masked) if masked in compact else len(prior_masked)
                picked = _nth_sentence(block_en, cur_idx)
                if picked:
                    contextual = picked
            except Exception as exc:
                logger.debug("context translate failed: %s", exc)

        if compare_candidates:
            en = pick_best_candidate(bare, contextual, zh=zh)
        else:
            en = contextual or bare

        if mapping:
            if self.glossary:
                en = self.glossary.restore_placeholders(en, mapping)
            else:
                en = _restore(en, mapping)

        preferred = dict(self.glossary.terms) if self.glossary else {}
        preferred.update(self._zh_to_en_names)
        en = lock_names_in_en(en, preferred=preferred, prior_en_names=list(self._en_names))
        en = self._stabilize_pronouns(zh, en)

        self._maybe_learn(zh, en)
        for n in remember_en_names(en):
            self._en_names.append(n)
        while len(self._en_names) > 24:
            self._en_names.popleft()

        # Only push into line deque when not using external buffer override for priors
        if prior_override is None:
            self.prior_zh.append(zh)
            self.prior_en.append(en)
            while len(self.prior_zh) > self.window:
                self.prior_zh.popleft()
                self.prior_en.popleft()
        return en.strip()

    def _mask(self, zh: str) -> tuple[str, dict[str, str]]:
        mapping: dict[str, str] = {}
        masked = zh
        if self.glossary and self.glossary.terms:
            masked, mapping = self.glossary.protected_translate_input(zh)
        for zh_name, en_name in sorted(self._zh_to_en_names.items(), key=lambda kv: len(kv[0]), reverse=True):
            if zh_name and zh_name in masked:
                key = f"XNAME{len(mapping)}X"
                masked = masked.replace(zh_name, key)
                mapping[key] = en_name
        return masked, mapping

    def _stabilize_pronouns(self, zh: str, en: str) -> str:
        for cre, eng in _PRONOUN_HINTS:
            if cre.search(zh):
                if self._last_gender_pronoun and eng in {"he", "she"} and self._last_gender_pronoun != eng:
                    if self._last_gender_pronoun in {"he", "she"}:
                        en2 = re.sub(
                            rf"\b{eng}\b",
                            self._last_gender_pronoun,
                            en,
                            count=1,
                            flags=re.IGNORECASE,
                        )
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
