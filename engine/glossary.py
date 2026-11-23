"""Series-scoped term/character memory (shared across episodes)."""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from paths import glossary_dir, legacy_beside_video_dir

logger = logging.getLogger("subparty.glossary")


@dataclass
class Glossary:
    series_id: str
    path: Path
    terms: dict[str, str] = field(default_factory=dict)  # zh -> en
    _lock: Lock = field(default_factory=Lock, repr=False)

    def protected_translate_input(self, zh: str) -> tuple[str, dict[str, str]]:
        """
        Replace known ZH terms with placeholders so Argos won't mangle names.
        Returns (masked_zh, placeholder->en map).
        """
        zh = zh or ""
        mapping: dict[str, str] = {}
        with self._lock:
            items = sorted(self.terms.items(), key=lambda kv: len(kv[0]), reverse=True)
        out = zh
        i = 0
        for zh_term, en_term in items:
            if not zh_term or zh_term not in out:
                continue
            key = f"XNAME{i}X"
            i += 1
            out = out.replace(zh_term, key)
            mapping[key] = en_term
        return out, mapping

    def restore_placeholders(self, en: str, mapping: dict[str, str]) -> str:
        out = en or ""
        for key, en_term in mapping.items():
            # Argos may alter casing/spacing of placeholders
            pattern = re.compile(re.escape(key), re.IGNORECASE)
            out = pattern.sub(en_term, out)
            # Also handle spaces inserted: "X NAME 0 X"
            loose = re.compile(
                re.escape(key[0]) + r"\s*" + re.escape(key[1:-1]) + r"\s*" + re.escape(key[-1]),
                re.IGNORECASE,
            )
            out = loose.sub(en_term, out)
        return out

    def apply_to_en(self, zh: str, en: str) -> str:
        """Fallback: if EN missing preferred term while ZH has it, leave EN (placeholders preferred)."""
        return en

    def remember(self, zh_term: str, en_term: str, *, persist: bool = True) -> None:
        zh_term = (zh_term or "").strip()
        en_term = (en_term or "").strip()
        if not zh_term or not en_term:
            return
        with self._lock:
            if self.terms.get(zh_term) == en_term:
                return
            self.terms[zh_term] = en_term
            if persist:
                self._save()

    def delete(self, zh_term: str, *, persist: bool = True) -> bool:
        zh_term = (zh_term or "").strip()
        with self._lock:
            if zh_term not in self.terms:
                return False
            del self.terms[zh_term]
            if persist:
                self._save()
            return True

    def replace_all(self, terms: dict[str, str], *, persist: bool = True) -> None:
        cleaned = {
            str(k).strip(): str(v).strip()
            for k, v in (terms or {}).items()
            if str(k).strip() and str(v).strip()
        }
        with self._lock:
            self.terms = cleaned
            if persist:
                self._save()

    def as_dict(self) -> dict:
        with self._lock:
            return {"series_id": self.series_id, "terms": dict(self.terms), "path": str(self.path)}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"series_id": self.series_id, "terms": self.terms}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("glossary saved %s (%d terms)", self.path, len(self.terms))


def series_id_from_video(video_path: str | Path) -> str:
    p = Path(video_path)
    parent = p.parent.name.strip() or "default"
    safe = re.sub(r"[^\w\-]+", "_", parent, flags=re.UNICODE).strip("_") or "default"
    return safe[:80]


def _migrate_legacy_glossary(sid: str, video_path: Path, dest: Path) -> None:
    """One-shot copy from beside-video `.subparty/{sid}.json` if app-local is missing."""
    if dest.exists():
        return
    legacy = legacy_beside_video_dir(video_path) / f"{sid}.json"
    if not legacy.exists():
        return
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, dest)
        logger.info("migrated glossary %s → %s", legacy, dest)
    except Exception as exc:
        logger.warning("glossary migrate skipped (%s): %s", legacy, exc)


def load_glossary(video_path: str | Path) -> Glossary:
    p = Path(video_path)
    sid = series_id_from_video(p)
    store = glossary_dir() / f"{sid}.json"
    _migrate_legacy_glossary(sid, p, store)
    terms: dict[str, str] = {}
    if store.exists():
        try:
            data = json.loads(store.read_text(encoding="utf-8"))
            raw = data.get("terms") or {}
            if isinstance(raw, dict):
                terms = {str(k): str(v) for k, v in raw.items() if str(k).strip() and str(v).strip()}
        except Exception as exc:
            logger.warning("glossary load failed: %s", exc)
    return Glossary(series_id=sid, path=store, terms=terms)


_glossary_cache: dict[str, Glossary] = {}
_cache_lock = Lock()


def glossary_for_video(video_path: str) -> Glossary:
    sid = series_id_from_video(video_path)
    with _cache_lock:
        g = _glossary_cache.get(sid)
        if g is None:
            g = load_glossary(video_path)
            _glossary_cache[sid] = g
        return g


def invalidate_glossary_cache(series_id: str | None = None) -> None:
    with _cache_lock:
        if series_id is None:
            _glossary_cache.clear()
        else:
            _glossary_cache.pop(series_id, None)


def list_series_glossaries() -> list[dict[str, Any]]:
    """List all series glossary files under app data (production memory index)."""
    root = glossary_dir()
    root.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            terms = data.get("terms") or {}
            n = len(terms) if isinstance(terms, dict) else 0
            sid = str(data.get("series_id") or path.stem)
            out.append(
                {
                    "series_id": sid,
                    "path": str(path),
                    "term_count": n,
                    "updated_s": path.stat().st_mtime,
                }
            )
        except Exception as exc:
            logger.warning("skip glossary %s: %s", path, exc)
    return out


def suggest_character_terms(
    cues: list[dict[str, Any]],
    *,
    existing: dict[str, str] | None = None,
    limit: int = 24,
) -> list[dict[str, str]]:
    """
    Suggest ZH name candidates from cue ZH lines that are not yet in glossary.
    EN left blank for human fill (or prior EN name if present in cue).
    """
    from names import extract_zh_name_candidates

    known = existing or {}
    counts: dict[str, int] = {}
    for c in cues:
        zh = str(c.get("text_zh") or c.get("original_ocr") or "")
        for name in extract_zh_name_candidates(zh):
            if name in known:
                continue
            counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    return [{"zh": zh, "en": "", "count": str(n)} for zh, n in ranked[:limit]]
