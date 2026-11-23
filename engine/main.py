"""SubParty engine sidecar — FastAPI + SSE cue stream."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from engines import list_engines, set_active_engine
from exports import export_job_formats
from ffmpeg_util import ffmpeg_path, ffprobe_path
from glossary import glossary_for_video, invalidate_glossary_cache, load_glossary, series_id_from_video
from job_store import (
    delete_snapshot,
    delete_snapshot_file,
    discard_all_snapshots,
    list_recoverable_jobs,
    snapshot_path_for,
)
from models_mgr import ensure_model, list_models
from audio_verify import audio_verify_status
from paths import app_data_dir
from pipeline import jobs
from security import get_or_create_token, token_ok

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("subparty.api")

app = FastAPI(title="SubParty Engine", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "null",
        "file://",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_OPEN_PATHS = {"/health", "/auth/token-check", "/docs", "/openapi.json", "/redoc"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in _OPEN_PATHS or path.startswith("/docs"):
        return await call_next(request)
    # SSE EventSource cannot set Authorization headers easily — allow token query param
    header = request.headers.get("authorization") or request.headers.get("x-subparty-token")
    q = request.query_params.get("token")
    if not token_ok(header) and not token_ok(q):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


class StartJobRequest(BaseModel):
    video_path: str = Field(..., min_length=1)
    fps: float = Field(1.0, ge=0.5, le=5.0)
    band_ratio: float = Field(0.2, ge=0.1, le=0.5)
    lookahead: float = Field(12.0, ge=4.0, le=60.0)
    playhead: float = Field(0.0, ge=0.0)
    engine_id: str = Field("argos", min_length=1)
    context_window_s: float = Field(120.0, ge=15.0, le=600.0)
    high_accuracy: bool = True
    audio_verify: bool | None = None


class BatchJobRequest(BaseModel):
    video_paths: list[str] = Field(..., min_length=1)
    fps: float = Field(1.0, ge=0.5, le=5.0)
    lookahead: float = Field(12.0, ge=4.0, le=60.0)
    engine_id: str = "argos"


class PlayheadRequest(BaseModel):
    t: float = Field(..., ge=0.0)


class GlossaryTermRequest(BaseModel):
    video_path: str = Field(..., min_length=1)
    zh: str = Field(..., min_length=1)
    en: str = Field(..., min_length=1)


class GlossaryDeleteRequest(BaseModel):
    video_path: str = Field(..., min_length=1)
    zh: str = Field(..., min_length=1)


class EngineSelectRequest(BaseModel):
    engine_id: str = Field(..., min_length=1)


class CueUpdateRequest(BaseModel):
    index: int = 0
    start: float
    end: float
    text_zh: str = ""
    text_en: str = ""
    confidence: float = 1.0
    uncertain: bool = False


class ExportRequest(BaseModel):
    bilingual_srt: bool = True
    vtt: bool = True
    ass: bool = True


class ModelEnsureRequest(BaseModel):
    model_id: str


class RecoverRequest(BaseModel):
    snapshot_path: str | None = None
    video_path: str | None = None
    job_id: str | None = None
    engine_id: str = "argos"


class CancelRequest(BaseModel):
    """discard=True (default) also deletes the recovery snapshot."""

    discard: bool = True


class DiscardRecoveryRequest(BaseModel):
    job_id: str | None = None
    snapshot_path: str | None = None
    video_path: str | None = None


class JobResponse(BaseModel):
    id: str
    video_path: str
    srt_path: str
    status: str
    progress: float
    cue_count: int
    duration: float
    current_time: float
    playhead: float = 0.0
    live_lag: float = 0.0
    sample_interval: float = 1.0
    buffer_ahead: float = 0.0
    series_id: str = ""
    region: dict[str, float] = {}
    engine_id: str = "argos"
    paused: bool = False
    batch_id: str | None = None
    context_window_s: float = 120.0
    high_accuracy: bool = True
    audio_verify: bool = False
    audio_summary: dict[str, Any] = {}
    error: str | None = None
    cues: list[dict[str, Any]] = []


@app.on_event("startup")
def _startup() -> None:
    token = get_or_create_token()
    logger.info("engine token ready (%s…)", token[:6])


@app.get("/health")
def health() -> dict[str, Any]:
    ffmpeg_ok = False
    ffprobe_ok = False
    ffmpeg_err = None
    try:
        ffmpeg_ok = Path(ffmpeg_path()).exists()
    except Exception as exc:
        ffmpeg_err = str(exc)
    try:
        ffprobe_ok = Path(ffprobe_path()).exists()
    except Exception as exc:
        ffmpeg_err = ffmpeg_err or str(exc)
    return {
        "ok": True,
        "ffmpeg": ffmpeg_ok,
        "ffprobe": ffprobe_ok,
        "error": ffmpeg_err,
        "version": "2.1.0",
        "mode": "accuracy-validation+maturity+frame-timing+audio-verify",
        "auth": True,
        "context_window_s_default": 120,
        "data_dir": str(app_data_dir()),
        "cue_maturity": [
            "provisional",
            "context-refined",
            "context-complete",
            "final",
        ],
        "timing_fine_step_s": 0.1,
        "audio_verify": audio_verify_status(),
    }


@app.get("/auth/bootstrap")
def auth_bootstrap(request: Request) -> dict[str, Any]:
    """Only callable with a valid token (Electron reads file and calls this)."""
    return {"ok": True, "token": get_or_create_token()}


# Make bootstrap require token — remove from open paths by using file-based exchange via Electron only.
# Electron reads token file directly; this endpoint stays protected.


@app.post("/jobs", response_model=JobResponse)
def start_job(body: StartJobRequest) -> JobResponse:
    try:
        set_active_engine(body.engine_id)
        job = jobs.create(
            body.video_path,
            fps=body.fps,
            band_ratio=body.band_ratio,
            lookahead=body.lookahead,
            playhead=body.playhead,
            engine_id=body.engine_id,
            context_window_s=body.context_window_s,
            high_accuracy=body.high_accuracy,
            audio_verify=body.audio_verify,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _to_response(job)


@app.post("/jobs/batch")
def start_batch(body: BatchJobRequest) -> dict[str, Any]:
    batch_id = str(uuid.uuid4())
    created: list[dict[str, Any]] = []
    for vp in body.video_paths:
        try:
            set_active_engine(body.engine_id)
            job = jobs.create(
                vp,
                fps=body.fps,
                lookahead=body.lookahead,
                playhead=0.0,
                engine_id=body.engine_id,
                batch_id=batch_id,
            )
            created.append({"id": job.id, "video_path": job.video_path, "status": job.status})
        except Exception as exc:
            created.append({"video_path": vp, "error": str(exc)})
    return {"batch_id": batch_id, "jobs": created}


@app.get("/jobs")
def list_jobs() -> dict[str, Any]:
    return {"jobs": [_to_response(j).model_dump() for j in jobs.list_jobs()]}


@app.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str) -> JobResponse:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _to_response(job)


@app.post("/jobs/{job_id}/playhead")
def set_playhead(job_id: str, body: PlayheadRequest) -> dict[str, Any]:
    if not jobs.set_playhead(job_id, body.t):
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs.get(job_id)
    return {"ok": True, "playhead": job.playhead if job else body.t}


@app.post("/jobs/{job_id}/pause")
def pause_job(job_id: str) -> dict[str, Any]:
    if not jobs.pause(job_id):
        raise HTTPException(status_code=404, detail="Job not found or not running")
    return {"ok": True, "status": "paused"}


@app.post("/jobs/{job_id}/resume")
def resume_job(job_id: str) -> dict[str, Any]:
    if not jobs.resume(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True, "status": "running"}


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, body: CancelRequest | None = None) -> dict[str, Any]:
    discard = True if body is None else bool(body.discard)
    if not jobs.cancel(job_id, discard=discard):
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs.get(job_id)
    return {
        "ok": True,
        "status": "cancelled",
        "discarded": discard,
        "job_id": job_id,
        "video_path": job.video_path if job else None,
    }


@app.post("/jobs/{job_id}/remove")
def remove_job(job_id: str) -> dict[str, Any]:
    """Stop processing if active, discard recovery snapshot, mark cancelled."""
    if not jobs.remove(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True, "status": "cancelled", "discarded": True, "job_id": job_id}


@app.patch("/jobs/{job_id}/cues")
def patch_cue(job_id: str, body: CueUpdateRequest) -> dict[str, Any]:
    updated = jobs.update_cue(job_id, body.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True, "cue": updated}


@app.post("/jobs/{job_id}/export")
def export_job(job_id: str, body: ExportRequest) -> dict[str, Any]:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    paths = export_job_formats(
        job.video_path,
        job.cues,
        bilingual_srt=body.bilingual_srt,
        vtt=body.vtt,
        ass=body.ass,
    )
    return {"ok": True, "paths": paths}


@app.get("/engines")
def engines() -> dict[str, Any]:
    return {"engines": [e.__dict__ for e in list_engines()]}


@app.post("/engines/select")
def select_engine(body: EngineSelectRequest) -> dict[str, Any]:
    try:
        eng = set_active_engine(body.engine_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "engine": eng.info().__dict__}


@app.get("/glossary")
def get_glossary(video_path: str) -> dict[str, Any]:
    if not Path(video_path).exists():
        raise HTTPException(status_code=404, detail="Video not found")
    g = load_glossary(video_path)
    return g.as_dict()


@app.post("/glossary/term")
def put_glossary_term(body: GlossaryTermRequest) -> dict[str, Any]:
    if not Path(body.video_path).exists():
        raise HTTPException(status_code=404, detail="Video not found")
    sid = series_id_from_video(body.video_path)
    invalidate_glossary_cache(sid)
    g = glossary_for_video(body.video_path)
    g.remember(body.zh.strip(), body.en.strip(), persist=True)
    return g.as_dict()


@app.delete("/glossary/term")
def delete_glossary_term(body: GlossaryDeleteRequest) -> dict[str, Any]:
    if not Path(body.video_path).exists():
        raise HTTPException(status_code=404, detail="Video not found")
    sid = series_id_from_video(body.video_path)
    invalidate_glossary_cache(sid)
    g = glossary_for_video(body.video_path)
    g.delete(body.zh.strip(), persist=True)
    return g.as_dict()


@app.get("/models")
def models() -> dict[str, Any]:
    return {"models": list_models()}


@app.post("/models/ensure")
def models_ensure(body: ModelEnsureRequest) -> dict[str, Any]:
    try:
        info = ensure_model(body.model_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "model": info}


@app.get("/recovery")
def recovery(root: str | None = None) -> dict[str, Any]:
    roots = [root] if root else None
    return {"jobs": list_recoverable_jobs(roots), "data_dir": str(app_data_dir())}


@app.get("/data/info")
def data_info() -> dict[str, Any]:
    return {
        "data_dir": str(app_data_dir()),
        "glossary_dir": str(app_data_dir() / "glossary"),
        "jobs_dir": str(app_data_dir() / "jobs"),
        "models_dir": str(app_data_dir() / "models"),
        "note": "SRT still writes beside the video; glossary/recovery/models stay under data/.",
    }


@app.post("/recovery/discard")
def recovery_discard(body: DiscardRecoveryRequest) -> dict[str, Any]:
    """Drop one unfinished recovery snapshot without resuming."""
    removed = False
    if body.snapshot_path:
        removed = delete_snapshot_file(body.snapshot_path) or removed
    jid = (body.job_id or "").strip()
    if jid:
        delete_snapshot(body.video_path, jid)
        removed = True
        # If that job is still in memory, cancel+discard it too
        if jobs.get(jid):
            jobs.cancel(jid, discard=True)
    if not body.snapshot_path and not jid:
        raise HTTPException(status_code=400, detail="job_id or snapshot_path required")
    return {"ok": True, "removed": removed, "jobs": list_recoverable_jobs()}


@app.post("/recovery/discard-all")
def recovery_discard_all() -> dict[str, Any]:
    """Discard every unfinished recovery snapshot and cancel matching in-memory jobs."""
    pending = list_recoverable_jobs()
    for snap in pending:
        jid = str(snap.get("id") or "")
        if jid and jobs.get(jid):
            jobs.cancel(jid, discard=True)
    n = discard_all_snapshots()
    return {"ok": True, "removed": n, "jobs": list_recoverable_jobs()}


@app.post("/recovery/resume")
def recovery_resume(body: RecoverRequest) -> JobResponse:
    from job_store import load_job_snapshot

    data = None
    if body.snapshot_path:
        data = load_job_snapshot(Path(body.snapshot_path))
    elif body.video_path and body.job_id:
        snap = snapshot_path_for(body.job_id, body.video_path)
        data = load_job_snapshot(snap)
    if not data:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    video = data["video_path"]
    try:
        job = jobs.create(
            video,
            playhead=float(data.get("playhead") or 0),
            engine_id=body.engine_id or data.get("engine_id") or "argos",
            resume_job_id=str(data.get("id")),
        )
        # Restore prior cues into memory for editor/export continuity
        job.cues = list(data.get("cues") or [])
        job.cue_count = len(job.cues)
        job.progress = float(data.get("progress") or 0)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _to_response(job)


@app.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    q: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def on_event(event: dict[str, Any]) -> None:
        q.put(event)

    jobs.subscribe(job_id, on_event)

    for cue in list(job.cues):
        q.put({"type": "cue", "cue": cue, "cue_count": job.cue_count, "srt_path": job.srt_path})
    if job.region:
        q.put({"type": "region", "region": job.region, "series_id": job.series_id})
    q.put(
        {
            "type": "status",
            "status": job.status,
            "progress": job.progress,
            "cue_count": job.cue_count,
            "error": job.error,
            "srt_path": job.srt_path,
            "series_id": job.series_id,
            "region": job.region,
        }
    )
    if job.status in {"done", "error", "cancelled"}:
        q.put(None)

    async def gen():
        try:
            while True:
                try:
                    item = await asyncio.get_event_loop().run_in_executor(None, q.get)
                except Exception:
                    break
                if item is None:
                    yield f"data: {json.dumps({'type': 'end'})}\n\n"
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                if item.get("type") in {"done", "error"} or item.get("status") in {
                    "done",
                    "error",
                    "cancelled",
                }:
                    if item.get("type") != "cue":
                        yield f"data: {json.dumps({'type': 'end'})}\n\n"
                        break
        finally:
            jobs.unsubscribe(job_id, on_event)

    return StreamingResponse(gen(), media_type="text/event-stream")


def _to_response(job: Any) -> JobResponse:
    return JobResponse(
        id=job.id,
        video_path=job.video_path,
        srt_path=job.srt_path,
        status=job.status,
        progress=job.progress,
        cue_count=job.cue_count,
        duration=job.duration,
        current_time=job.current_time,
        playhead=job.playhead,
        live_lag=job.live_lag,
        sample_interval=job.sample_interval,
        buffer_ahead=job.buffer_ahead,
        series_id=job.series_id,
        region=dict(job.region or {}),
        engine_id=getattr(job, "engine_id", "argos"),
        paused=bool(getattr(job, "paused", False)),
        batch_id=getattr(job, "batch_id", None),
        context_window_s=float(getattr(job, "context_window_s", 120.0)),
        high_accuracy=bool(getattr(job, "high_accuracy", True)),
        audio_verify=bool(getattr(job, "audio_verify", False)),
        audio_summary=dict(getattr(job, "audio_summary", None) or {}),
        error=job.error,
        cues=list(job.cues),
    )


if __name__ == "__main__":
    import uvicorn

    get_or_create_token()
    uvicorn.run(app, host="127.0.0.1", port=18765, log_level="info")
