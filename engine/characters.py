"""Stage 8 — series character sheets (beyond glossary term map)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from paths import characters_dir

logger = logging.getLogger("subparty.characters")


@dataclass
class Character:
    zh: str
    en: str = ""
    aliases: list[str] = field(default_factory=list)
    notes: str = ""
    role: str = ""  # e.g. lead / support

    def as_dict(self) -> dict[str, Any]:
        return {
            "zh": self.zh,
            "en": self.en,
            "aliases": list(self.aliases),
            "notes": self.notes,
            "role": self.role,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Character:
        return Character(
            zh=str(d.get("zh") or "").strip(),
            en=str(d.get("en") or "").strip(),
            aliases=[str(a).strip() for a in (d.get("aliases") or []) if str(a).strip()],
            notes=str(d.get("notes") or "").strip(),
            role=str(d.get("role") or "").strip(),
        )


@dataclass
class CharacterSheet:
    series_id: str
    path: Path
    characters: list[Character] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "series_id": self.series_id,
                "path": str(self.path),
                "characters": [c.as_dict() for c in self.characters],
            }

    def upsert(self, char: Character, *, persist: bool = True) -> None:
        if not char.zh:
            return
        with self._lock:
            for i, c in enumerate(self.characters):
                if c.zh == char.zh:
                    self.characters[i] = char
                    break
            else:
                self.characters.append(char)
            if persist:
                self._save()

    def delete(self, zh: str, *, persist: bool = True) -> bool:
        zh = (zh or "").strip()
        with self._lock:
            before = len(self.characters)
            self.characters = [c for c in self.characters if c.zh != zh]
            ok = len(self.characters) < before
            if ok and persist:
                self._save()
            return ok

    def to_glossary_terms(self) -> dict[str, str]:
        with self._lock:
            return {c.zh: c.en for c in self.characters if c.zh and c.en}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "series_id": self.series_id,
            "characters": [c.as_dict() for c in self.characters],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def sheet_path(series_id: str) -> Path:
    safe = re.sub(r"[^\w\-]+", "_", series_id or "default").strip("_") or "default"
    return characters_dir() / f"{safe}.json"


def load_character_sheet(series_id: str) -> CharacterSheet:
    path = sheet_path(series_id)
    chars: list[Character] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for raw in data.get("characters") or []:
                if isinstance(raw, dict) and str(raw.get("zh") or "").strip():
                    chars.append(Character.from_dict(raw))
        except Exception as exc:
            logger.warning("character sheet load failed: %s", exc)
    return CharacterSheet(series_id=series_id, path=path, characters=chars)


def sync_glossary_into_sheet(series_id: str, terms: dict[str, str]) -> CharacterSheet:
    """Ensure glossary name terms appear on the character sheet (EN fill)."""
    sheet = load_character_sheet(series_id)
    for zh, en in (terms or {}).items():
        zh = str(zh).strip()
        en = str(en).strip()
        if not zh or not en:
            continue
        if any(c.zh == zh for c in sheet.characters):
            continue
        # Prefer short CJK as characters
        if len(zh) > 4:
            continue
        sheet.upsert(Character(zh=zh, en=en, role="glossary"), persist=False)
    sheet._save()
    return sheet


def list_character_sheets() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(characters_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            chars = data.get("characters") or []
            out.append(
                {
                    "series_id": str(data.get("series_id") or path.stem),
                    "path": str(path),
                    "character_count": len(chars) if isinstance(chars, list) else 0,
                }
            )
        except Exception as exc:
            logger.warning("skip character sheet %s: %s", path, exc)
    return out
