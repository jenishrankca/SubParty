"""Persist job snapshots for crash recovery."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("subparty.job_store")

_STORE_DIRNAME = ".subparty"
_JOBS_DIR = "jobs"


def _jobs_dir_for_video(video_path: str | Path) -> Path:
    p = Path(video_path)
    d = p.parent / _STORE_DIRNAME / _JOBS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_job_snapshot(job: Any) -> Path:
    path = _jobs_dir_for_video(job.video_path) / f"{job.id}.json"
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


def list_recoverable_jobs(search_roots: list[str | Path] | None = None) -> list[dict[str, Any]]:
    """Scan .subparty/jobs under given roots (or cwd) for unfinished snapshots."""
    roots = [Path(r) for r in (search_roots or [Path.cwd()])]
    found: list[dict[str, Any]] = []
    for root in roots:
        for path in root.rglob(f"{_STORE_DIRNAME}/{_JOBS_DIR}/*.json"):
            data = load_job_snapshot(path)
            if not data:
                continue
            if data.get("status") in {"done"}:
                continue
            data["_snapshot_path"] = str(path)
            found.append(data)
    found.sort(key=lambda d: d.get("id") or "")
    return found


def delete_snapshot(video_path: str, job_id: str) -> None:
    path = _jobs_dir_for_video(video_path) / f"{job_id}.json"
    if path.exists():
        path.unlink()
