"""A/B decode microbench: FFmpeg subprocess vs PyAV persistent.

Compares against engine/benchmarks/baseline_latest.json when present.

Usage:
  .venv\\Scripts\\python.exe scripts\\compare_decode.py
  .venv\\Scripts\\python.exe scripts\\compare_decode.py --n 12
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from ffmpeg_util import probe_duration, probe_size
from video_decode import close_all_decoders, set_decode_backend


SAMPLES = REPO / "samples"
OUT_DIR = ROOT / "benchmarks"


def _pct(vals: list[float], p: float) -> float:
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _stats_ms(samples_s: list[float]) -> dict:
    ms = [x * 1000.0 for x in samples_s]
    return {
        "n": len(ms),
        "mean_ms": round(statistics.mean(ms), 1),
        "p50_ms": round(_pct(ms, 50), 1),
        "p95_ms": round(_pct(ms, 95), 1),
        "min_ms": round(min(ms), 1),
        "max_ms": round(max(ms), 1),
    }


def _timestamps(duration: float, n: int) -> list[float]:
    span = max(0.5, duration - 1.0)
    return [round(0.5 + (span * i / max(1, n - 1)), 3) for i in range(n)]


def bench_backend(video: Path, *, backend: str, n: int, scale_width: int = 320) -> dict:
    from ffmpeg_util import extract_region

    set_decode_backend(backend)  # type: ignore[arg-type]
    close_all_decoders()
    duration = probe_duration(video)
    w, h = probe_size(video)
    times = _timestamps(duration, n)
    samples: list[float] = []

    with tempfile.TemporaryDirectory(prefix=f"dec_{backend}_") as tmp:
        tmp_dir = Path(tmp)
        # Warm first call (PyAV open / FFmpeg path resolve) — exclude from stats? keep in but report warm separately
        warm_out = tmp_dir / "warm.png"
        tw0 = time.perf_counter()
        extract_region(
            video,
            times[0],
            warm_out,
            x_ratio=0.06,
            y_ratio=0.8,
            w_ratio=0.88,
            h_ratio=0.2,
            width=w,
            height=h,
            scale_width=scale_width,
        )
        warm_ms = (time.perf_counter() - tw0) * 1000

        for i, t in enumerate(times):
            out = tmp_dir / f"f_{i:03d}.png"
            t0 = time.perf_counter()
            extract_region(
                video,
                t,
                out,
                x_ratio=0.06,
                y_ratio=0.8,
                w_ratio=0.88,
                h_ratio=0.2,
                width=w,
                height=h,
                scale_width=scale_width,
            )
            samples.append(time.perf_counter() - t0)
            if not out.exists() or out.stat().st_size < 100:
                raise RuntimeError(f"{backend} empty output @ {t}")

    close_all_decoders()
    set_decode_backend(None)
    return {
        "backend": backend,
        "video": str(video.relative_to(REPO)) if video.is_relative_to(REPO) else str(video),
        "duration_s": round(duration, 2),
        "warm_ms": round(warm_ms, 1),
        "decode": _stats_ms(samples),
        "timestamps": times,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument(
        "--video",
        type=str,
        default=str(SAMPLES / "the-weird-games-5min.mp4"),
    )
    args = ap.parse_args()
    video = Path(args.video)
    if not video.exists():
        print(json.dumps({"ok": False, "error": f"missing {video}"}))
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"=== decode A/B on {video.name} n={args.n} ===", flush=True)

    ff = bench_backend(video, backend="ffmpeg", n=args.n)
    print(
        f"  ffmpeg  p50={ff['decode']['p50_ms']}ms  mean={ff['decode']['mean_ms']}ms  "
        f"warm={ff['warm_ms']}ms",
        flush=True,
    )
    py = bench_backend(video, backend="pyav", n=args.n)
    print(
        f"  pyav    p50={py['decode']['p50_ms']}ms  mean={py['decode']['mean_ms']}ms  "
        f"warm={py['warm_ms']}ms",
        flush=True,
    )

    # Also short demos
    extras = []
    for name in ("demo_zh_hardsub.mp4", "demo_watermark_junk.mp4"):
        clip = SAMPLES / name
        if not clip.exists():
            continue
        print(f"=== {name} ===", flush=True)
        e_ff = bench_backend(clip, backend="ffmpeg", n=min(8, args.n))
        e_py = bench_backend(clip, backend="pyav", n=min(8, args.n))
        extras.append({"ffmpeg": e_ff, "pyav": e_py})
        print(
            f"  ffmpeg p50={e_ff['decode']['p50_ms']}ms  pyav p50={e_py['decode']['p50_ms']}ms",
            flush=True,
        )

    baseline_path = OUT_DIR / "baseline_latest.json"
    baseline_p50 = None
    if baseline_path.exists():
        base = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline_p50 = (base.get("summary") or {}).get("weird_games_decode_p50_ms")

    ff_p50 = ff["decode"]["p50_ms"]
    py_p50 = py["decode"]["p50_ms"]
    speedup = round(ff_p50 / py_p50, 2) if py_p50 else None
    vs_baseline = None
    if baseline_p50 and py_p50:
        vs_baseline = {
            "baseline_ffmpeg_p50_ms": baseline_p50,
            "pyav_p50_ms": py_p50,
            "delta_ms": round(py_p50 - float(baseline_p50), 1),
            "speedup_vs_baseline": round(float(baseline_p50) / py_p50, 2),
        }

    summary = {
        "weird_games_ffmpeg_p50_ms": ff_p50,
        "weird_games_pyav_p50_ms": py_p50,
        "speedup_ffmpeg_over_pyav": speedup,
        "vs_stage1_baseline": vs_baseline,
        "default_backend": "pyav (FFmpeg fallback on error / SUBPARTY_DECODE=ffmpeg)",
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "ok": True,
        "stage": "2-decode-pyav",
        "created_utc": stamp,
        "summary": summary,
        "weird_games": {"ffmpeg": ff, "pyav": py},
        "demos": extras,
    }
    out = OUT_DIR / f"decode_compare_{stamp}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    (OUT_DIR / "decode_compare_latest.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print("=== SUMMARY ===", flush=True)
    print(json.dumps(summary, indent=2), flush=True)
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
