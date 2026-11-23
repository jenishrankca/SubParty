"""Persist job snapshots for crash recovery (app-local data/jobs)."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from paths import jobs_dir, legacy_beside_video_dir

logger = logging.getLogger("subparty.job_store")


def _jobs_dir() -> Path:
    return jobs_dir()


def save_job_snapshot(job: Any) -> Path:
    path = _jobs_dir() / f"{job.id}.json"
    payload = {
        "id": job.id,
        "video_path": job.video_path,
        "srt_path": job.srt_path,
        "status": job.status,
        "progress": job.progress,
        "cue_count": job.cue_count,
        "duration": job.duration,
        "current_time": job.current_time,
        "playhead": job.playhead,
        "series_id": job.series_id,
        "region": job.region,
        "engine_id": getattr(job, "engine_id", "argos"),
        "paused": getattr(job, "paused", False),
        "batch_id": getattr(job, "batch_id", None),
        "cues": list(job.cues),
        "error": job.error,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_job_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("load snapshot failed %s: %s", path, exc)
        return None


def snapshot_path_for(job_id: str, video_path: str | None = None) -> Path:
    """Prefer app-local snapshot; fall back to legacy beside-video path for resume."""
    local = _jobs_dir() / f"{job_id}.json"
    if local.exists():
        return local
    if video_path:
        legacy = legacy_beside_video_dir(video_path) / "jobs" / f"{job_id}.json"
        if legacy.exists():
            return legacy
    return local


def list_recoverable_jobs(search_roots: list[str | Path] | None = None) -> list[dict[str, Any]]:
    """
    List unfinished snapshots from app-local data/jobs.

    Optionally also scan given roots for legacy beside-video `.subparty/jobs`
    (read-only discover; does not keep writing there).
    """
    found: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for path in sorted(_jobs_dir().glob("*.json")):
        data = load_job_snapshot(path)
        if not data or data.get("status") in {"done", "cancelled"}:
            continue
        jid = str(data.get("id") or path.stem)
        seen_ids.add(jid)
        data["_snapshot_path"] = str(path)
        found.append(data)

    # Optional legacy scan (ignore after migrate; discover only)
    roots = [Path(r) for r in (search_roots or [])]
    for root in roots:
        if not root.exists():
            continue
        try:
            matches = list(root.rglob(".subparty/jobs/*.json"))
        except Exception:
            matches = []
        for path in matches:
            data = load_job_snapshot(path)
            if not data or data.get("status") in {"done", "cancelled"}:
                continue
            jid = str(data.get("id") or path.stem)
            if jid in seen_ids:
                continue
            # One-shot migrate into app data so future lists stay local
            dest = _jobs_dir() / f"{jid}.json"
            try:
                if not dest.exists():
                    shutil.copy2(path, dest)
                    logger.info("migrated job snapshot %s → %s", path, dest)
                    data["_snapshot_path"] = str(dest)
                else:
                    data["_snapshot_path"] = str(dest)
            except Exception as exc:
                logger.warning("job migrate skipped (%s): %s", path, exc)
                data["_snapshot_path"] = str(path)
            seen_ids.add(jid)
            found.append(data)

    found.sort(key=lambda d: d.get("id") or "")
    return found


def delete_snapshot(video_path: str | None, job_id: str) -> None:
    path = _jobs_dir() / f"{job_id}.json"
    if path.exists():
        path.unlink()
    # Also clear legacy copy if present (best-effort)
    if video_path:
        try:
            legacy = legacy_beside_video_dir(video_path) / "jobs" / f"{job_id}.json"
            if legacy.exists():
                legacy.unlink()
        except Exception:
            pass


def delete_snapshot_file(snapshot_path: str | Path) -> bool:
    path = Path(snapshot_path)
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except Exception as exc:
        logger.warning("delete snapshot file failed %s: %s", path, exc)
        return False


def discard_all_snapshots() -> int:
    """Remove every unfinished recovery snapshot from app-local jobs dir."""
    removed = 0
    for path in list(_jobs_dir().glob("*.json")):
        data = load_job_snapshot(path)
        if data and data.get("status") in {"done"}:
            continue
        try:
            path.unlink()
            removed += 1
        except Exception as exc:
            logger.warning("discard_all failed %s: %s", path, exc)
    return removed
