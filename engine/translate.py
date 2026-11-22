"""Free local ZH→EN via Argos Translate (downloads language pack on first use)."""

from __future__ import annotations

import logging
from functools import lru_cache

logger = logging.getLogger("subparty.translate")

_FROM = "zh"
_TO = "en"


@lru_cache(maxsize=1)
def _ensure_package() -> None:
    import argostranslate.package
    import argostranslate.translate

    installed = argostranslate.translate.get_installed_languages()
    from_lang = next((l for l in installed if l.code == _FROM), None)
    to_lang = next((l for l in installed if l.code == _TO), None)
    if from_lang and to_lang and from_lang.get_translation(to_lang):
        logger.info("Argos zh→en already installed")
        return

    logger.info("Downloading Argos zh→en language pack (first run)…")
    argostranslate.package.update_package_index()
    available = argostranslate.package.get_available_packages()
    package = next(
        (p for p in available if p.from_code == _FROM and p.to_code == _TO),
        None,
    )
    if package is None:
        raise RuntimeError("Argos Translate zh→en package not found in index")
    download_path = package.download()
    argostranslate.package.install_from_path(download_path)
    logger.info("Argos zh→en installed")


@lru_cache(maxsize=512)
def _translate_cached(text: str) -> str:
    _ensure_package()
    import argostranslate.translate

    return argostranslate.translate.translate(text, _FROM, _TO).strip()


def warm_translate() -> None:
    try:
        _ensure_package()
        # Force-load SBD / CTranslate2 once so first live cue isn't a multi-second stall
        _translate_cached("你好")
    except Exception as exc:
        logger.warning("translate warm failed: %s", exc)


def translate_zh_to_en(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    return _translate_cached(text)
