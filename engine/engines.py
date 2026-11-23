"""Pluggable ZH→EN translation engines (Argos default; local NLLB / Qwen)."""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

from paths import models_dir

logger = logging.getLogger("subparty.engines")

# Hugging Face ids + on-disk folder names under data/models/
NLLB_HF_ID = "facebook/nllb-200-distilled-600M"
NLLB_LOCAL_NAME = "nllb-200-distilled-600M"
# 1.5B Instruct fits ~3GB weights — safe for 16GB RAM / 6GB VRAM (CPU or light GPU)
QWEN_HF_ID = "Qwen/Qwen2.5-1.5B-Instruct"
QWEN_LOCAL_NAME = "qwen2.5-1.5b-instruct"

_load_lock = Lock()
_loaded_heavy: str | None = None  # which heavy engine currently holds RAM


@dataclass
class EngineInfo:
    id: str
    name: str
    available: bool
    local: bool
    notes: str = ""


def nllb_model_dir() -> Path:
    override = (os.environ.get("SUBPARTY_NLLB_MODEL") or "").strip()
    if override:
        return Path(override).expanduser()
    return models_dir() / NLLB_LOCAL_NAME


def qwen_model_dir() -> Path:
    override = (os.environ.get("SUBPARTY_QWEN_MODEL") or "").strip()
    if override:
        return Path(override).expanduser()
    return models_dir() / QWEN_LOCAL_NAME


def dir_has_weights(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    markers = (
        "config.json",
        "model.safetensors",
        "model.safetensors.index.json",
        "pytorch_model.bin",
        "pytorch_model.bin.index.json",
    )
    return any((path / m).exists() for m in markers)


def _torch_device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            # Prefer GPU only when enough free VRAM (~2.5GB+) for NLLB/Qwen-1.5B
            try:
                free, _total = torch.cuda.mem_get_info()
                if free >= 2_500_000_000:
                    return "cuda"
            except Exception:
                return "cuda"
        return "cpu"
    except Exception:
        return "cpu"


def _torch_dtype(device: str):
    import torch

    if device == "cuda":
        return torch.float16
    return torch.float32


def _release_heavy_unlocked(except_id: str | None = None) -> None:
    """Drop other heavy models so 16GB RAM machines don't OOM. Caller holds _load_lock."""
    global _loaded_heavy
    for eid, eng in list(_REGISTRY.items()):
        if eid in {"nllb", "qwen"} and eid != except_id:
            unload = getattr(eng, "unload", None)
            if callable(unload):
                unload()
    _loaded_heavy = except_id


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
            note = f"[optional] {note}".strip()
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

    raw = argostranslate.translate.translate(text, src, tgt).strip()
    # Light Live-safe cleanup (dedupe / drop CJK echo) — no LLM
    try:
        from en_clean import clean_en

        # Empty = garbage dropped (caller shows ZH); never re-emit raw echo/repeats
        return clean_en(raw, zh=text)
    except Exception:
        return raw


class NLLBEngine(TranslationEngine):
    id = "nllb"
    name = "NLLB-200 distilled 600M"
    notes = (
        f"Local Meta NLLB (~2.3 GB). Uses {NLLB_HF_ID}. "
        "Install via Models tab. Prefer Argos for speed; NLLB for quality experiments."
    )
    experimental = True
    _model: Any | None = None
    _tok: Any | None = None
    _device: str = "cpu"

    def available(self) -> bool:
        try:
            import transformers  # noqa: F401
            import torch  # noqa: F401
        except Exception:
            return False
        return dir_has_weights(nllb_model_dir())

    def unload(self) -> None:
        self._model = None
        self._tok = None
        try:
            import gc

            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def translate(self, text: str, *, src: str = "zh", tgt: str = "en") -> str:
        text = (text or "").strip()
        if not text:
            return ""
        if not self.available():
            raise RuntimeError("NLLB weights missing — use Models tab to download")
        tok, model = self._get_model()
        src_code = "zho_Hans" if src.startswith("zh") else src
        tgt_code = "eng_Latn" if tgt.startswith("en") else tgt
        # transformers v5 removed pipeline("translation") — use generate directly
        if hasattr(tok, "src_lang"):
            tok.src_lang = src_code
        encoded = tok(text, return_tensors="pt")
        if self._device == "cuda":
            encoded = {k: v.to("cuda") for k, v in encoded.items()}
        forced = None
        if hasattr(tok, "lang_code_to_id") and tgt_code in tok.lang_code_to_id:
            forced = tok.lang_code_to_id[tgt_code]
        elif hasattr(tok, "convert_tokens_to_ids"):
            tid = tok.convert_tokens_to_ids(tgt_code)
            if isinstance(tid, int) and tid >= 0:
                forced = tid
        gen_kwargs: dict[str, Any] = {
            "max_new_tokens": 128,
            "max_length": None,
        }
        if forced is not None:
            gen_kwargs["forced_bos_token_id"] = forced
        out = model.generate(**encoded, **gen_kwargs)
        return tok.batch_decode(out, skip_special_tokens=True)[0].strip()

    def _get_model(self):
        if self._model is not None and self._tok is not None:
            return self._tok, self._model
        with _load_lock:
            if self._model is not None and self._tok is not None:
                return self._tok, self._model
            _release_heavy_unlocked("nllb")
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            model_path = str(nllb_model_dir())
            device = _torch_device()
            dtype = _torch_dtype(device)
            logger.info("loading NLLB from %s on %s (%s)", model_path, device, dtype)
            tok = AutoTokenizer.from_pretrained(model_path)
            try:
                model = AutoModelForSeq2SeqLM.from_pretrained(
                    model_path,
                    dtype=dtype,
                    low_cpu_mem_usage=True,
                )
            except TypeError:
                model = AutoModelForSeq2SeqLM.from_pretrained(
                    model_path,
                    torch_dtype=dtype,
                    low_cpu_mem_usage=True,
                )
            if device == "cuda":
                model = model.to("cuda")
            model.eval()
            self._tok = tok
            self._model = model
            self._device = device
            global _loaded_heavy
            _loaded_heavy = "nllb"
            return self._tok, self._model


class QwenEngine(TranslationEngine):
    """Local Qwen2.5-1.5B-Instruct — sized for 16GB RAM / 6GB VRAM laptops."""

    id = "qwen"
    name = "Qwen2.5 1.5B Instruct"
    notes = (
        f"Local free instruct model (~3.1 GB). Default {QWEN_HF_ID} "
        "(not 7B — too large for 6GB VRAM / 16GB RAM). Install via Models tab."
    )
    experimental = True
    _model: Any | None = None
    _tok: Any | None = None
    _device: str = "cpu"

    def available(self) -> bool:
        try:
            import transformers  # noqa: F401
            import torch  # noqa: F401
        except Exception:
            return False
        # Allow env override path even without ALLOW flag once weights exist
        return dir_has_weights(qwen_model_dir())

    def unload(self) -> None:
        self._model = None
        self._tok = None
        try:
            import gc

            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def translate(self, text: str, *, src: str = "zh", tgt: str = "en") -> str:
        text = (text or "").strip()
        if not text:
            return ""
        if not self.available():
            raise RuntimeError("Qwen weights missing — use Models tab to download")
        tok, model = self._get_model()
        messages = [
            {
                "role": "system",
                "content": "Translate Chinese hardsub text to natural English. Reply with English only.",
            },
            {"role": "user", "content": text},
        ]
        prompt = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tok(prompt, return_tensors="pt")
        if self._device == "cuda":
            inputs = {k: v.to("cuda") for k, v in inputs.items()}
        out = model.generate(
            **inputs,
            max_new_tokens=128,
            do_sample=False,
            pad_token_id=tok.eos_token_id,
        )
        gen = out[0][inputs["input_ids"].shape[-1] :]
        decoded = tok.decode(gen, skip_special_tokens=True).strip()
        return decoded.splitlines()[0].strip() if decoded else ""

    def _get_model(self):
        if self._model is not None and self._tok is not None:
            return self._tok, self._model
        with _load_lock:
            if self._model is not None and self._tok is not None:
                return self._tok, self._model
            _release_heavy_unlocked("qwen")
            from transformers import AutoModelForCausalLM, AutoTokenizer

            model_path = str(qwen_model_dir())
            device = _torch_device()
            dtype = _torch_dtype(device)
            logger.info("loading Qwen from %s on %s (%s)", model_path, device, dtype)
            tok = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
            try:
                model = AutoModelForCausalLM.from_pretrained(
                    model_path,
                    dtype=dtype,
                    low_cpu_mem_usage=True,
                    trust_remote_code=True,
                )
            except TypeError:
                model = AutoModelForCausalLM.from_pretrained(
                    model_path,
                    torch_dtype=dtype,
                    low_cpu_mem_usage=True,
                    trust_remote_code=True,
                )
            if device == "cuda":
                model = model.to("cuda")
            model.eval()
            self._tok = tok
            self._model = model
            self._device = device
            global _loaded_heavy
            _loaded_heavy = "qwen"
            return self._tok, self._model


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
