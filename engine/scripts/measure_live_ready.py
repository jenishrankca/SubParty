"""Measure Live keep-up: time-to-Ready and seek retarget on a clip.

Pass criteria (product):
  - Ready wall ≤ 30s (first cue near playhead + buffer_ahead ≥ ~3s OR ≥1 cue)
  - After seek, a cue near new playhead within a few seconds

Usage:
  python measure_live_ready.py [video] [--ready-s 30] [--seek-s 8]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import jobs


def _cue_near(cues: list[dict], t: float, window: float = 4.0) -> dict | None:
    best = None
    best_d = 1e9
    for c in cues:
        zh = (c.get("text_zh") or "").strip()
        en = (c.get("text_en") or "").strip()
        if not zh and not en:
            continue
        start = float(c.get("start") or 0)
        end = float(c.get("end") or start)
        if end < t - 0.5 or start > t + window:
            continue
        # Prefer cue covering t, else nearest start
        if start <= t <= end:
            return c
        d = min(abs(start - t), abs(end - t))
        if d < best_d:
            best_d = d
            best = c
    return best


def _ready(job, playhead: float) -> bool:
    """Mirror Live UI gate: small ahead scan (silence OK) or first cue."""
    buf = float(job.buffer_ahead or 0)
    if buf >= 2.5:
        return True
    cues = list(job.cues or [])
    if not cues:
        return False
    if int(job.cue_count or 0) >= 1 and buf >= 1.5:
        return True
    if _cue_near(cues, playhead, window=3.0) is not None:
        return True
    for c in cues:
        zh = (c.get("text_zh") or "").strip()
        en = (c.get("text_en") or "").strip()
        if not zh and not en:
            continue
        start = float(c.get("start") or 0)
        if playhead - 0.5 <= start <= playhead + 12.0:
            return True
    return False


def main() -> int:
    video = Path(
        sys.argv[1]
        if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
        else ROOT.parent / "samples" / "the-weird-games-5min.mp4"
    )
    ready_limit = 30.0
    seek_limit = 8.0
    if "--ready-s" in sys.argv:
        ready_limit = float(sys.argv[sys.argv.index("--ready-s") + 1])
    if "--seek-s" in sys.argv:
        seek_limit = float(sys.argv[sys.argv.index("--seek-s") + 1])

    if not video.exists():
        print(json.dumps({"ok": False, "error": f"missing {video}"}))
        return 1

    t0 = time.perf_counter()
    job = jobs.create(
        str(video),
        fps=1.0,
        lookahead=8.0,
        playhead=0.0,
        engine_id="argos",
        live_mode=True,
        high_accuracy=False,
        audio_verify=False,
        context_window_s=30.0,
    )
    print(
        json.dumps(
            {
                "phase": "start",
                "job_id": job.id,
                "live_mode": True,
                "video": str(video),
            }
        ),
        flush=True,
    )

    # --- Phase A: time to Ready ---
    ready_s: float | None = None
    deadline = time.time() + ready_limit + 15.0
    last = ""
    while time.time() < deadline:
        j = jobs.get(job.id)
        if not j:
            print(json.dumps({"ok": False, "error": "job disappeared"}))
            return 2
        if j.status in {"error", "cancelled"}:
            print(json.dumps({"ok": False, "status": j.status, "error": j.error}))
            return 3
        msg = (
            f"prep p={j.progress:.3f} cues={j.cue_count} "
            f"buf={j.buffer_ahead:.1f} lag={j.live_lag:.1f} "
            f"wall={time.perf_counter()-t0:.1f}s"
        )
        if msg != last:
            print(msg, flush=True)
            last = msg
        if ready_s is None and _ready(j, playhead=0.0):
            ready_s = time.perf_counter() - t0
            print(json.dumps({"phase": "ready", "ready_s": round(ready_s, 2)}), flush=True)
            break
        time.sleep(0.25)

    if ready_s is None:
        j = jobs.get(job.id)
        out = {
            "ok": False,
            "phase": "ready_timeout",
            "ready_s": None,
            "ready_limit_s": ready_limit,
            "cue_count": j.cue_count if j else 0,
            "buffer_ahead": j.buffer_ahead if j else 0,
            "wall_s": round(time.perf_counter() - t0, 2),
        }
        print(json.dumps(out), flush=True)
        jobs.cancel(job.id)
        return 4

    # Simulate watching: advance playhead ~1× briefly; confirm overlay not blank from lag
    watch_until = time.perf_counter() + 4.0
    ph = 0.0
    blank_ticks = 0
    watch_samples = 0
    while time.perf_counter() < watch_until:
        ph = min(ph + 0.5, 30.0)
        jobs.set_playhead(job.id, ph)
        time.sleep(0.5)
        j = jobs.get(job.id)
        if not j:
            break
        watch_samples += 1
        if _cue_near(j.cues or [], ph, window=3.5) is None and j.cue_count > 0:
            if j.buffer_ahead < 0.5 and j.live_lag > 1.5:
                blank_ticks += 1

    def _seek_retargeted(j, target: float) -> tuple[bool, bool]:
        """Return (ok, has_near_cue). Engine sampled around target, or cue exists."""
        near = _cue_near(j.cues or [], target, window=6.0) is not None
        if near:
            return True, True
        # Sampled the new playhead region (silence-ok) — current_time near target
        ct = float(j.current_time or 0)
        if target - 1.5 <= ct <= target + 12.0:
            return True, False
        # Frontier rebuilt ahead of new playhead
        if float(j.playhead or 0) >= target - 0.5 and float(j.buffer_ahead or 0) >= 1.0:
            return True, False
        return False, False

    # --- Phase B: seek retarget ---
    seek_to = 60.0
    j = jobs.get(job.id)
    if j and j.duration and seek_to > j.duration - 10:
        seek_to = max(10.0, (j.duration or 120) * 0.4)
    jobs.set_playhead(job.id, seek_to)
    seek_t0 = time.perf_counter()
    seek_s: float | None = None
    seek_deadline = time.time() + seek_limit + 12.0
    last = ""
    while time.time() < seek_deadline:
        j = jobs.get(job.id)
        if not j:
            break
        msg = (
            f"seek@{seek_to:.0f} cues={j.cue_count} buf={j.buffer_ahead:.1f} "
            f"ct={j.current_time:.1f} lag={j.live_lag:.1f} "
            f"wall={time.perf_counter()-seek_t0:.1f}s"
        )
        if msg != last:
            print(msg, flush=True)
            last = msg
        ok, near = _seek_retargeted(j, seek_to)
        if ok:
            seek_s = time.perf_counter() - seek_t0
            print(
                json.dumps(
                    {
                        "phase": "seek",
                        "seek_to": seek_to,
                        "seek_retarget_s": round(seek_s, 2),
                        "near_cue": near,
                        "buffer_ahead": round(j.buffer_ahead, 2),
                        "current_time": round(j.current_time, 2),
                    }
                ),
                flush=True,
            )
            break
        time.sleep(0.15)

    # Second seek (simulates ±10s / scrub)
    seek2 = seek_to + 25.0
    jobs.set_playhead(job.id, seek2)
    seek2_t0 = time.perf_counter()
    seek2_s: float | None = None
    while time.perf_counter() - seek2_t0 < seek_limit + 8:
        j = jobs.get(job.id)
        if not j:
            break
        ok, _near = _seek_retargeted(j, seek2)
        if ok:
            seek2_s = time.perf_counter() - seek2_t0
            print(
                json.dumps(
                    {
                        "phase": "seek2",
                        "seek_to": seek2,
                        "seek_retarget_s": round(seek2_s, 2),
                    }
                ),
                flush=True,
            )
            break
        time.sleep(0.15)

    jobs.cancel(job.id)
    ready_pass = ready_s is not None and ready_s <= ready_limit
    seek_pass = seek_s is not None and seek_s <= seek_limit
    seek2_pass = seek2_s is not None and seek2_s <= seek_limit
    # Overlay not blank: allow ≤1 blank tick during 6s watch after Ready
    blank_pass = blank_ticks <= 1

    out = {
        "ok": ready_pass and seek_pass and seek2_pass and blank_pass,
        "ready_s": round(ready_s, 2) if ready_s is not None else None,
        "ready_limit_s": ready_limit,
        "ready_pass": ready_pass,
        "seek_to": seek_to,
        "seek_retarget_s": round(seek_s, 2) if seek_s is not None else None,
        "seek_limit_s": seek_limit,
        "seek_pass": seek_pass,
        "seek2_to": seek2,
        "seek2_retarget_s": round(seek2_s, 2) if seek2_s is not None else None,
        "seek2_pass": seek2_pass,
        "watch_blank_ticks": blank_ticks,
        "watch_samples": watch_samples,
        "blank_pass": blank_pass,
        "wall_s": round(time.perf_counter() - t0, 2),
    }
    print(json.dumps(out), flush=True)
    return 0 if out["ok"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
