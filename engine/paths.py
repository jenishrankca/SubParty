"""Canonical SubParty app-local data paths (never beside videos)."""

from __future__ import annotations

import os
from pathlib import Path

# Repo root = parent of engine/
_REPO_ROOT = Path(__file__).resolve().parents[1]


def repo_root() -> Path:
    return _REPO_ROOT


def app_data_dir() -> Path:
    """
    App-local data root.

    Override with SUBPARTY_DATA_DIR. Default: <repo>/data
    (portable, next to the project — not next to videos).
    """
    override = (os.environ.get("SUBPARTY_DATA_DIR") or "").strip()
    if override:
        root = Path(override).expanduser().resolve()
    else:
        root = _REPO_ROOT / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def glossary_dir() -> Path:
    d = app_data_dir() / "glossary"
    d.mkdir(parents=True, exist_ok=True)
    return d


def jobs_dir() -> Path:
    d = app_data_dir() / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def models_dir() -> Path:
    d = app_data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def reports_dir() -> Path:
    d = app_data_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def batches_dir() -> Path:
    d = app_data_dir() / "batches"
    d.mkdir(parents=True, exist_ok=True)
    return d


def characters_dir() -> Path:
    d = app_data_dir() / "characters"
    d.mkdir(parents=True, exist_ok=True)
    return d


def token_path() -> Path:
    return app_data_dir() / "engine.token"


def legacy_token_path() -> Path:
    """Pre-migration location (repo .subparty/engine.token)."""
    return _REPO_ROOT / ".subparty" / "engine.token"


def legacy_beside_video_dir(video_path: str | Path) -> Path:
    return Path(video_path).parent / ".subparty"
