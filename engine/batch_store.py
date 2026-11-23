"""Stage 9 — durable batch queue (survives engine restarts)."""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from paths import batches_dir

logger = logging.getLogger("subparty.batch")


def batch_path(batch_id: str) -> Path:
    return batches_dir() / f"{batch_id}.json"


def save_batch(manifest: dict[str, Any]) -> Path:
    bid = str(manifest.get("batch_id") or "")
    if not bid:
        raise ValueError("batch_id required")
    path = batch_path(bid)
    manifest["updated_at"] = time.time()
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_batch(batch_id: str) -> dict[str, Any] | None:
    path = batch_path(batch_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("load batch failed %s: %s", path, exc)
        return None


def list_batches(*, limit: int = 40) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in sorted(batches_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        data = load_batch(path.stem)
        if not data:
            continue
        jobs = data.get("jobs") or []
        items.append(
            {
                "batch_id": data.get("batch_id"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "engine_id": data.get("engine_id"),
                "job_count": len(jobs),
                "by_status": _count_status(jobs),
            }
        )
        if len(items) >= limit:
            break
    return items


def _count_status(jobs: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for j in jobs:
        st = str(j.get("status") or "unknown")
        out[st] = out.get(st, 0) + 1
    return out


def create_batch_manifest(
    *,
    video_paths: list[str],
    engine_id: str,
    fps: float,
    lookahead: float,
    job_rows: list[dict[str, Any]],
    batch_id: str | None = None,
) -> dict[str, Any]:
    bid = (batch_id or "").strip() or str(uuid.uuid4())
    now = time.time()
    rows = list(job_rows)
    for row in rows:
        row["batch_id"] = bid
    manifest = {
        "batch_id": bid,
        "created_at": now,
        "updated_at": now,
        "engine_id": engine_id,
        "fps": fps,
        "lookahead": lookahead,
        "video_paths": list(video_paths),
        "jobs": rows,
    }
    save_batch(manifest)
    return manifest


def update_batch_job(batch_id: str, job_id: str, **fields: Any) -> dict[str, Any] | None:
    data = load_batch(batch_id)
    if not data:
        return None
    matched = False
    for row in data.get("jobs") or []:
        if str(row.get("id") or "") == job_id:
            row.update(fields)
            matched = True
            break
    if not matched and fields.get("video_path"):
        vp = str(fields.get("video_path"))
        for row in data.get("jobs") or []:
            if str(row.get("video_path") or "") == vp and str(row.get("status") or "") in {
                "error",
                "cancelled",
                "unknown",
                "",
            }:
                row.update(fields)
                matched = True
                break
    if not matched and fields.get("video_path"):
        # New job row for retry of a missing entry
        row = {"id": job_id, **fields}
        data.setdefault("jobs", []).append(row)
    save_batch(data)
    return data


def mark_job_status_from_live(batch_id: str, live_jobs: list[Any]) -> dict[str, Any] | None:
    data = load_batch(batch_id)
    if not data:
        return None
    by_id = {j.id: j for j in live_jobs if getattr(j, "batch_id", None) == batch_id}
    for row in data.get("jobs") or []:
        jid = str(row.get("id") or "")
        live = by_id.get(jid)
        if live:
            row["status"] = live.status
            row["progress"] = live.progress
            row["cue_count"] = live.cue_count
            row["srt_path"] = live.srt_path
            row["error"] = live.error
            row["audio_disagreements"] = (getattr(live, "audio_summary", None) or {}).get(
                "disagreements"
            )
    save_batch(data)
    return data


def failed_job_rows(batch_id: str) -> list[dict[str, Any]]:
    data = load_batch(batch_id) or {}
    return [
        j
        for j in (data.get("jobs") or [])
        if str(j.get("status") or "") in {"error", "cancelled"} or j.get("error")
    ]
