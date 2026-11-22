"""SubParty engine sidecar — FastAPI + SSE cue stream."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ffmpeg_util import ffmpeg_path, ffprobe_path
from pipeline import jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("subparty.api")

app = FastAPI(title="SubParty Engine", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartJobRequest(BaseModel):
    video_path: str = Field(..., min_length=1)
    fps: float = Field(1.0, ge=0.5, le=5.0)
    band_ratio: float = Field(0.2, ge=0.1, le=0.5)
    lookahead: float = Field(12.0, ge=4.0, le=60.0)
    playhead: float = Field(0.0, ge=0.0)


class PlayheadRequest(BaseModel):
    t: float = Field(..., ge=0.0)


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
    error: str | None = None
    cues: list[dict[str, Any]] = []


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
        "version": "0.2.0",
        "mode": "playhead-priority",
    }


@app.post("/jobs", response_model=JobResponse)
def start_job(body: StartJobRequest) -> JobResponse:
    try:
        job = jobs.create(
            body.video_path,
            fps=body.fps,
            band_ratio=body.band_ratio,
            lookahead=body.lookahead,
        )
        if body.playhead > 0:
            jobs.set_playhead(job.id, body.playhead)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _to_response(job)


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


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    if not jobs.cancel(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True}


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
    q.put(
        {
            "type": "status",
            "status": job.status,
            "progress": job.progress,
            "cue_count": job.cue_count,
            "error": job.error,
            "srt_path": job.srt_path,
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
        error=job.error,
        cues=list(job.cues),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=18765, log_level="info")
