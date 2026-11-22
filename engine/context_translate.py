"""Dialogue context window + glossary-aware ZH→EN translation."""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from translate import translate_zh_to_en as _bare_translate

logger = logging.getLogger("subparty.context")

_SENT_SPLIT = re.compile(r"(?<=[。！？!?.])\s*")


@dataclass
class DialogueContext:
    """Rolling window of prior/upcoming ZH lines for translation consistency."""

    window: int = 4
    prior_zh: deque[str] = field(default_factory=deque)
    prior_en: deque[str] = field(default_factory=deque)
    glossary: Any | None = None

    def translate(self, zh: str, upcoming: list[str] | None = None) -> str:
        zh = (zh or "").strip()
        if not zh:
            return ""

        masked, mapping = (zh, {})
        if self.glossary and self.glossary.terms:
            masked, mapping = self.glossary.protected_translate_input(zh)

        upcoming_clean = [u.strip() for u in (upcoming or []) if u and u.strip() and u.strip() != zh]
        upcoming_masked: list[str] = []
        for u in upcoming_clean[: max(1, self.window // 2)]:
            if self.glossary and self.glossary.terms:
                um, extra = self.glossary.protected_translate_input(u)
                mapping.update(extra)
                upcoming_masked.append(um)
            else:
                upcoming_masked.append(u)

        en = _bare_translate(masked)

        prior = list(self.prior_zh)[-self.window :]
        if prior or upcoming_masked:
            try:
                # prior … current … upcoming — translate as one block, pick current sentence
                block_parts = prior + [masked] + upcoming_masked
                # Deduplicate adjacent identical lines
                compact: list[str] = []
                for p in block_parts:
                    if not compact or compact[-1] != p:
                        compact.append(p)
                block_zh = "。".join(compact)
                if not block_zh.endswith(("。", "！", "？", ".", "!", "?")):
                    block_zh += "。"
                block_en = _bare_translate(block_zh)
                # Current line is at index len(prior) among compact (after dedupe may shift)
                cur_idx = compact.index(masked) if masked in compact else len(prior)
                picked = _nth_sentence(block_en, cur_idx)
                if picked and len(picked) >= 2:
                    if 0.35 * max(len(en), 1) <= len(picked) <= 2.8 * len(en) + 16:
                        en = picked
            except Exception as exc:
                logger.debug("context translate failed: %s", exc)

        if mapping:
            en = self.glossary.restore_placeholders(en, mapping) if self.glossary else en

        self._maybe_learn(zh, en)

        self.prior_zh.append(zh)
        self.prior_en.append(en)
        while len(self.prior_zh) > self.window:
            self.prior_zh.popleft()
            self.prior_en.popleft()
        return en.strip()

    def _maybe_learn(self, zh: str, en: str) -> None:
        if not self.glossary:
            return
        compact = re.sub(r"\s+", "", zh)
        if 2 <= len(compact) <= 3 and re.fullmatch(r"[\u4e00-\u9fff]+", compact):
            en_clean = en.strip().rstrip(".!?")
            if en_clean and en_clean[0].isupper() and len(en_clean.split()) <= 3:
                # Don't learn placeholder leftovers
                if "XNAME" in en_clean.upper():
                    return
                self.glossary.remember(compact, en_clean, persist=True)


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


def _last_sentence(text: str) -> str:
    return _nth_sentence(text, 10**9)
