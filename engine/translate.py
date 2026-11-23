"""ZH→EN facade — delegates to pluggable engines (Argos default)."""

from __future__ import annotations

from engines import get_engine, list_engines, set_active_engine, translate_zh_to_en, warm_translate

__all__ = [
    "translate_zh_to_en",
    "warm_translate",
    "list_engines",
    "set_active_engine",
    "get_engine",
]
