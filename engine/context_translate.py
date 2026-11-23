"""Dialogue context window + glossary-aware ZH→EN with candidate comparison."""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from en_clean import clean_en, is_garbage_en
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


_DIGIT_RE = re.compile(r"\d+")
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _zh_cues(zh: str) -> dict[str, Any]:
    """Extract lightweight semantic cues from the ZH source line."""
    zh = zh or ""
    digits = _DIGIT_RE.findall(zh)
    cjk_n = len(_CJK_RE.findall(zh))
    has_q = bool(re.search(r"[吗嗎呢？?]", zh))
    has_excl = bool(re.search(r"[！!]", zh))
    # Pronoun / person hints used downstream for EN scoring
    pronouns = [eng for cre, eng in _PRONOUN_HINTS if cre.search(zh)]
    return {
        "digits": digits,
        "cjk_n": cjk_n,
        "has_q": has_q,
        "has_excl": has_excl,
        "pronouns": pronouns,
        "compact_len": len(re.sub(r"\s+", "", zh)),
    }


def score_en_against_zh(en: str, *, zh: str, bare_len: int | None = None) -> float:
    """
    Cue-aware semantic score for an EN candidate vs ZH source.
    Higher is better. Soft length signals only — not sole decision.
    """
    en = (en or "").strip()
    if not en:
        return -1e9
    cues = _zh_cues(zh)
    score = 0.0

    # Digit preservation (episode numbers, counts)
    en_digits = set(_DIGIT_RE.findall(en))
    for d in cues["digits"]:
        score += 1.2 if d in en_digits else -0.8

    # Question / exclamation mood
    if cues["has_q"]:
        score += 0.9 if ("?" in en) else -0.4
    if cues["has_excl"]:
        score += 0.5 if ("!" in en) else 0.0

    # Pronoun presence when ZH signals gender/person
    en_l = en.lower()
    for p in cues["pronouns"]:
        score += 0.7 if re.search(rf"\b{re.escape(p)}\b", en_l) else -0.25

    # Expected EN length vs CJK density (rough MT prior)
    cjk_n = max(1, int(cues["cjk_n"]) or int(cues["compact_len"]) or 1)
    ratio = len(en) / float(cjk_n)
    if 1.2 <= ratio <= 6.5:
        score += 0.6
    elif ratio > 10.0:
        score -= 1.5
    elif ratio < 0.5:
        score -= 1.0

    # Completeness: ends with sentence punct or is short fragment
    if re.search(r"[.!?…]$", en) or len(en.split()) <= 4:
        score += 0.2

    # Relative to bare length (neighbor-swallow / truncation)
    if bare_len is not None and bare_len > 0:
        rel = len(en) / float(bare_len)
        if rel > 2.8:
            score -= 2.5 + 0.15 * (rel - 2.8)
        elif rel < 0.35:
            score -= 1.8
        elif 0.7 <= rel <= 1.6:
            score += 0.35

    # Prefer some Latin content for non-empty ZH (avoid empty-ish junk)
    tokens = _LATIN_TOKEN_RE.findall(en)
    if cues["cjk_n"] > 0 and tokens:
        score += min(0.8, 0.15 * len(tokens))

    return score


def pick_best_candidate(bare: str, contextual: str, *, zh: str) -> str:
    """
    Cue-aware semantic compare of bare vs contextual EN.

    Prefer clean provisional (bare) when contextual is garbage / fails score.
    Callers that revise cues keep full revision history on ``CueRecord``.
    """
    bare_c = clean_en(bare or "", zh=zh) or ""
    contextual_c = clean_en(contextual or "", zh=zh, fallback=bare_c) or ""
    if not contextual_c:
        return bare_c
    if not bare_c:
        return contextual_c
    if contextual_c == bare_c:
        return bare_c

    # Prefer clean provisional when contextual fails score / still garbage
    if is_garbage_en(contextual_c, zh=zh) and not is_garbage_en(bare_c, zh=zh):
        return bare_c

    # Hard reject: contextual swallowed neighboring dialogue
    if len(contextual_c) > 2.8 * max(len(bare_c), 1) + 20:
        return bare_c
    if len(contextual_c) < 0.35 * max(len(bare_c), 1):
        return bare_c

    bare_score = score_en_against_zh(bare_c, zh=zh, bare_len=len(bare_c))
    ctx_score = score_en_against_zh(contextual_c, zh=zh, bare_len=len(bare_c))
    # Prefer bare on near-ties when contextual looks inflated / repetitive
    if ctx_score >= bare_score + 0.15:
        return contextual_c
    return bare_c


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

        bare_raw = _tr(masked)
        bare = clean_en(bare_raw, zh=zh) or ""

        # Live / fast path: bare only — Argos block context produces repeats/nonsense
        if not compare_candidates:
            en = bare
            if en and mapping:
                if self.glossary:
                    en = self.glossary.restore_placeholders(en, mapping)
                else:
                    en = _restore(en, mapping)
            if en:
                preferred = dict(self.glossary.terms) if self.glossary else {}
                preferred.update(self._zh_to_en_names)
                en = lock_names_in_en(en, preferred=preferred, prior_en_names=list(self._en_names))
                en = self._stabilize_pronouns(zh, en)
                en = clean_en(en, zh=zh, fallback=bare) or ""
            if prior_override is None:
                self.prior_zh.append(zh)
                self.prior_en.append(en)
                while len(self.prior_zh) > self.window:
                    self.prior_zh.popleft()
                    self.prior_en.popleft()
            return (en or "").strip()

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

        en = pick_best_candidate(bare, contextual, zh=zh)

        if mapping:
            if self.glossary:
                en = self.glossary.restore_placeholders(en, mapping)
            else:
                en = _restore(en, mapping)

        preferred = dict(self.glossary.terms) if self.glossary else {}
        preferred.update(self._zh_to_en_names)
        en = lock_names_in_en(en, preferred=preferred, prior_en_names=list(self._en_names))
        en = self._stabilize_pronouns(zh, en)
        en = clean_en(en, zh=zh, fallback=bare) or ""

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
