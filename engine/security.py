"""Shared token auth between Electron and the local engine."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

_TOKEN: str | None = None
_TOKEN_FILE = Path(__file__).resolve().parents[1] / ".subparty" / "engine.token"


def get_or_create_token() -> str:
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    env = os.environ.get("SUBPARTY_ENGINE_TOKEN", "").strip()
    if env:
        _TOKEN = env
        return _TOKEN
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _TOKEN_FILE.exists():
        raw = _TOKEN_FILE.read_text(encoding="utf-8").strip()
        if raw:
            _TOKEN = raw
            return _TOKEN
    _TOKEN = secrets.token_urlsafe(32)
    _TOKEN_FILE.write_text(_TOKEN, encoding="utf-8")
    try:
        os.chmod(_TOKEN_FILE, 0o600)
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
