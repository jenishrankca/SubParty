"""Simulate 1× Live watch: Ready ≤15s and buffer_ahead >2s majority.

Usage:
  .venv\\Scripts\\python.exe scripts\\measure_live_keepup.py [video] [--sim-s 90]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import jobs


def main() -> int:
    video = Path(
        sys.argv[1]
        if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
        else ROOT.parent / "samples" / "the-weird-games-5min.mp4"
    )
    sim_s = 90.0
    if "--sim-s" in sys.argv:
        sim_s = float(sys.argv[sys.argv.index("--sim-s") + 1])

    if not video.exists():
        print(json.dumps({"ok": False, "error": f"missing {video}"}))
        return 1

    t0 = time.perf_counter()
    job = jobs.create(
        str(video),
        fps=1.0,
        lookahead=14.0,
        playhead=0.0,
        engine_id="argos",
        live_mode=True,
        high_accuracy=False,
        audio_verify=False,
        context_window_s=30.0,
    )
    print(json.dumps({"job_id": job.id, "video": str(video), "sim_s": sim_s}), flush=True)

    ready_at: float | None = None
    samples: list[dict] = []
    en_samples: list[dict] = []
    last_ph_wall = time.perf_counter()
    playhead = 0.0

    deadline = t0 + sim_s + 20.0  # wall budget includes Ready + sim
    while time.perf_counter() < deadline:
        j = jobs.get(job.id)
        if j is None:
            break
        if j.status in {"error", "cancelled", "done"} and ready_at is not None:
            if j.status == "error":
                print(json.dumps({"ok": False, "error": j.error, "status": j.status}))
                return 1
            break

        now = time.perf_counter()
        # Advance 1× playhead after Ready
        if ready_at is not None:
            playhead = min(float(j.duration or 999), now - ready_at)
            jobs.set_playhead(job.id, playhead)
        else:
            jobs.set_playhead(job.id, 0.0)

        buf = float(j.buffer_ahead or 0)
        lag = float(j.live_lag or 0)
        wall = now - t0

        if ready_at is None and buf >= 2.5:
            ready_at = now
            print(
                json.dumps(
                    {
                        "event": "ready",
                        "wall_s": round(wall, 2),
                        "buffer_ahead": round(buf, 2),
                        "cues": j.cue_count,
                    }
                ),
                flush=True,
            )

        if ready_at is not None and (now - last_ph_wall) >= 1.0:
            last_ph_wall = now
            samples.append(
                {
                    "t": round(playhead, 1),
                    "buf": round(buf, 2),
                    "lag": round(lag, 2),
                    "interval": round(float(j.sample_interval or 0), 2),
                    "cues": int(j.cue_count or 0),
                }
            )
            # Capture a few EN lines for before/after report
            for c in list(j.cues or [])[-3:]:
                en = (c.get("text_en") or "").strip()
                zh = (c.get("text_zh") or "").strip()
                if zh and {"zh": zh, "en": en} not in en_samples:
                    en_samples.append({"zh": zh, "en": en})
            if playhead >= sim_s:
                jobs.cancel(job.id, discard=True)
                break

        time.sleep(0.25)

    j = jobs.get(job.id) or job
    ready_s = (ready_at - t0) if ready_at else None
    above = sum(1 for s in samples if s["buf"] > 2.0)
    majority = (above / len(samples)) if samples else 0.0
    ok = (
        ready_s is not None
        and ready_s <= 15.0
        and len(samples) >= 20
        and majority >= 0.5
    )
    out = {
        "ok": ok,
        "ready_s": round(ready_s, 2) if ready_s is not None else None,
        "ready_limit_s": 15.0,
        "sim_s": sim_s,
        "samples": len(samples),
        "buffer_ahead_gt2_ratio": round(majority, 3),
        "buffer_ahead_gt2_count": above,
        "avg_buf": round(sum(s["buf"] for s in samples) / len(samples), 2) if samples else 0,
        "min_buf": min((s["buf"] for s in samples), default=None),
        "avg_interval": round(
            sum(s["interval"] for s in samples) / len(samples), 2
        )
        if samples
        else None,
        "cues": int(j.cue_count or 0),
        "en_samples": en_samples[:12],
        "tail": samples[-8:],
        "status": j.status,
        "error": j.error,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
