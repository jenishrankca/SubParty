"""Pluggable ZH→EN translation engines (Argos default; NLLB/Qwen stubs)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

logger = logging.getLogger("subparty.engines")


@dataclass
class EngineInfo:
    id: str
    name: str
    available: bool
    local: bool
    notes: str = ""


class TranslationEngine(ABC):
    id: str
    name: str

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def translate(self, text: str, *, src: str = "zh", tgt: str = "en") -> str: ...

    def warm(self) -> None:
        try:
            self.translate("你好")
        except Exception as exc:
            logger.warning("%s warm failed: %s", self.id, exc)

    def info(self) -> EngineInfo:
        ok = False
        try:
            ok = self.available()
        except Exception:
            ok = False
        note = getattr(self, "notes", "")
        if getattr(self, "experimental", False):
            note = f"[experimental] {note}".strip()
        return EngineInfo(
            id=self.id,
            name=self.name,
            available=ok,
            local=True,
            notes=note,
        )


class ArgosEngine(TranslationEngine):
    id = "argos"
    name = "Argos Translate"
    notes = "Free local default (CTranslate2)."

    def available(self) -> bool:
        try:
            import argostranslate.translate  # noqa: F401

            return True
        except Exception:
            return False

    def translate(self, text: str, *, src: str = "zh", tgt: str = "en") -> str:
        text = (text or "").strip()
        if not text:
            return ""
        return _argos_cached(text, src, tgt)


@lru_cache(maxsize=1)
def _ensure_argos(src: str, tgt: str) -> None:
    import argostranslate.package
    import argostranslate.translate

    installed = argostranslate.translate.get_installed_languages()
    from_lang = next((l for l in installed if l.code == src), None)
    to_lang = next((l for l in installed if l.code == tgt), None)
    if from_lang and to_lang and from_lang.get_translation(to_lang):
        return
    argostranslate.package.update_package_index()
    available = argostranslate.package.get_available_packages()
    package = next((p for p in available if p.from_code == src and p.to_code == tgt), None)
    if package is None:
        raise RuntimeError(f"Argos {src}→{tgt} package not found")
    argostranslate.package.install_from_path(package.download())


@lru_cache(maxsize=512)
def _argos_cached(text: str, src: str, tgt: str) -> str:
    _ensure_argos(src, tgt)
    import argostranslate.translate

    return argostranslate.translate.translate(text, src, tgt).strip()


class NLLBEngine(TranslationEngine):
    """Optional local NLLB — available when deps installed; not the default (unvalidated at scale)."""

    id = "nllb"
    name = "NLLB-200 (optional)"
    notes = (
        "Optional local model (facebook/nllb-200-distilled-600M). "
        "Not production-validated in SubParty benchmarks yet — use for experiments."
    )
    experimental = True
    _pipe: Callable | None = None

    def available(self) -> bool:
        try:
            import transformers  # noqa: F401
            import torch  # noqa: F401

            return True
        except Exception:
            return False

    def translate(self, text: str, *, src: str = "zh", tgt: str = "en") -> str:
        text = (text or "").strip()
        if not text:
            return ""
        if not self.available():
            raise RuntimeError("NLLB not installed")
        pipe = self._get_pipe()
        src_code = "zho_Hans" if src.startswith("zh") else src
        tgt_code = "eng_Latn" if tgt.startswith("en") else tgt
        out = pipe(text, src_lang=src_code, tgt_lang=tgt_code, max_length=256)
        if isinstance(out, list) and out:
            return str(out[0].get("translation_text") or "").strip()
        return str(out).strip()

    def _get_pipe(self):
        if self._pipe is not None:
            return self._pipe
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, pipeline

        model_id = "facebook/nllb-200-distilled-600M"
        tok = AutoTokenizer.from_pretrained(model_id)
        model = AutoModelForSeq2SeqLM.from_pretrained(model_id)
        self._pipe = pipeline("translation", model=model, tokenizer=tok)
        return self._pipe


class QwenEngine(TranslationEngine):
    """Experimental stub — NOT production-validated. Opt-in only."""

    id = "qwen"
    name = "Qwen (experimental)"
    notes = (
        "NOT production-ready. Requires SUBPARTY_QWEN_MODEL and SUBPARTY_QWEN_ALLOW=1. "
        "Prefer Argos (+ contextual candidate compare). Validate offline before trusting."
    )
    experimental = True

    def available(self) -> bool:
        import os

        if os.environ.get("SUBPARTY_QWEN_ALLOW", "").strip() != "1":
            return False
        return bool(os.environ.get("SUBPARTY_QWEN_MODEL"))

    def translate(self, text: str, *, src: str = "zh", tgt: str = "en") -> str:
        text = (text or "").strip()
        if not text:
            return ""
        if not self.available():
            raise RuntimeError("Qwen model not configured (set SUBPARTY_QWEN_MODEL)")
        # Minimal transformers text-generation path when a model path is set
        import os

        from transformers import AutoModelForCausalLM, AutoTokenizer

        model_id = os.environ["SUBPARTY_QWEN_MODEL"]
        tok = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(model_id, trust_remote_code=True)
        prompt = f"Translate Chinese to English only. No notes.\nZH: {text}\nEN:"
        inputs = tok(prompt, return_tensors="pt")
        out = model.generate(**inputs, max_new_tokens=128)
        decoded = tok.decode(out[0], skip_special_tokens=True)
        if "EN:" in decoded:
            return decoded.split("EN:")[-1].strip().splitlines()[0].strip()
        return decoded.strip()


_REGISTRY: dict[str, TranslationEngine] = {
    ArgosEngine.id: ArgosEngine(),
    NLLBEngine.id: NLLBEngine(),
    QwenEngine.id: QwenEngine(),
}
_active_id = "argos"


def list_engines() -> list[EngineInfo]:
    return [e.info() for e in _REGISTRY.values()]


def get_engine(engine_id: str | None = None) -> TranslationEngine:
    eid = (engine_id or _active_id or "argos").strip().lower()
    eng = _REGISTRY.get(eid) or _REGISTRY["argos"]
    if not eng.available() and eid != "argos":
        logger.warning("engine %s unavailable; falling back to argos", eid)
        return _REGISTRY["argos"]
    return eng


def set_active_engine(engine_id: str) -> TranslationEngine:
    global _active_id
    eid = (engine_id or "argos").strip().lower()
    if eid not in _REGISTRY:
        raise ValueError(f"Unknown engine: {engine_id}")
    _active_id = eid
    return get_engine(eid)


def translate_zh_to_en(text: str, engine_id: str | None = None) -> str:
    return get_engine(engine_id).translate(text, src="zh", tgt="en")


def warm_translate(engine_id: str | None = None) -> None:
    get_engine(engine_id).warm()
