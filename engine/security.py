"""Shared token auth between Electron and the local engine."""

from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path

from paths import legacy_token_path, token_path

_TOKEN: str | None = None


def get_or_create_token() -> str:
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    env = os.environ.get("SUBPARTY_ENGINE_TOKEN", "").strip()
    if env:
        _TOKEN = env
        return _TOKEN

    dest = token_path()
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Migrate from repo `.subparty/engine.token` once
    legacy = legacy_token_path()
    if not dest.exists() and legacy.exists():
        try:
            shutil.copy2(legacy, dest)
        except Exception:
            pass

    if dest.exists():
        raw = dest.read_text(encoding="utf-8").strip()
        if raw and len(raw) >= 20 and raw != "test-fix-token":
            _TOKEN = raw
            return _TOKEN

    _TOKEN = secrets.token_urlsafe(32)
    dest.write_text(_TOKEN, encoding="utf-8")
    try:
        os.chmod(dest, 0o600)
    except Exception:
        pass
    return _TOKEN


def token_ok(header_value: str | None) -> bool:
    expected = get_or_create_token()
    if not header_value:
        return False
    got = header_value.strip()
    if got.lower().startswith("bearer "):
        got = got[7:].strip()
    return secrets.compare_digest(got, expected)
