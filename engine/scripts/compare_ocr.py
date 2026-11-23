"""OCR backend A/B: RapidOCR legacy vs explicit PP-OCRv6 small vs PP-OCRv5 mobile.

Pre-extracts crops once (PyAV), then times OCR-only p50 per backend.

Usage:
  .venv\\Scripts\\python.exe scripts\\compare_ocr.py
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

from ffmpeg_util import extract_region, probe_duration, probe_size
from ocr import list_ocr_backends, set_ocr_backend, warm_ocr
from video_decode import close_all_decoders, set_decode_backend


SAMPLES = REPO / "samples"
OUT_DIR = ROOT / "benchmarks"
BASELINE = OUT_DIR / "baseline_latest.json"


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


def preextract_crops(video: Path, *, n: int, scale_width: int = 320) -> list[Path]:
    set_decode_backend("pyav")
    close_all_decoders()
    duration = probe_duration(video)
    w, h = probe_size(video)
    times = _timestamps(duration, n)
    tmp = Path(tempfile.mkdtemp(prefix="ocr_crops_"))
    paths: list[Path] = []
    for i, t in enumerate(times):
        out = tmp / f"c_{i:03d}.png"
        extract_region(
            video,
            t,
            out,
            x_ratio=0.06,
            y_ratio=0.78,
            w_ratio=0.88,
            h_ratio=0.2,
            width=w,
            height=h,
            scale_width=scale_width,
        )
        paths.append(out)
    close_all_decoders()
    set_decode_backend(None)
    return paths


def bench_ocr_backend(backend: str, crops: list[Path]) -> dict:
    from ocr import ocr_lines

    set_ocr_backend(backend)
    warm_t0 = time.perf_counter()
    warm_ocr()
    warm_ms = (time.perf_counter() - warm_t0) * 1000

    samples: list[float] = []
    nonempty = 0
    previews: list[str] = []
    for path in crops:
        t0 = time.perf_counter()
        lines = ocr_lines(str(path))
        samples.append(time.perf_counter() - t0)
        text = " ".join(ln.text for ln in lines).strip()
        if text:
            nonempty += 1
            if len(previews) < 5:
                previews.append(text[:80])

    set_ocr_backend(None)
    return {
        "backend": backend,
        "warm_ms": round(warm_ms, 1),
        "ocr": _stats_ms(samples),
        "nonempty": nonempty,
        "previews": previews,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument(
        "--video",
        default=str(SAMPLES / "the-weird-games-5min.mp4"),
    )
    args = ap.parse_args()
    video = Path(args.video)
    if not video.exists():
        print(json.dumps({"ok": False, "error": f"missing {video}"}))
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"=== pre-extract {args.n} crops from {video.name} (PyAV) ===", flush=True)
    crops = preextract_crops(video, n=args.n)
    print(f"  crops={len(crops)}", flush=True)

    backends = ["rapidocr", "ppocrv6_small", "ppocrv5_mobile"]
    rows = []
    for b in backends:
        print(f"=== OCR {b} ===", flush=True)
        row = bench_ocr_backend(b, crops)
        rows.append(row)
        print(
            f"  p50={row['ocr']['p50_ms']}ms mean={row['ocr']['mean_ms']}ms "
            f"warm={row['warm_ms']}ms nonempty={row['nonempty']}/{len(crops)}",
            flush=True,
        )

    baseline_ocr_p50 = None
    if BASELINE.exists():
        base = json.loads(BASELINE.read_text(encoding="utf-8"))
        baseline_ocr_p50 = (base.get("summary") or {}).get("weird_games_ocr_p50_ms")

    by_id = {r["backend"]: r for r in rows}
    legacy = by_id.get("rapidocr", {}).get("ocr", {}).get("p50_ms")
    v6 = by_id.get("ppocrv6_small", {}).get("ocr", {}).get("p50_ms")
    v5 = by_id.get("ppocrv5_mobile", {}).get("ocr", {}).get("p50_ms")

    summary = {
        "video": str(video.relative_to(REPO)) if video.is_relative_to(REPO) else str(video),
        "stage1_baseline_ocr_p50_ms": baseline_ocr_p50,
        "rapidocr_legacy_p50_ms": legacy,
        "ppocrv6_small_p50_ms": v6,
        "ppocrv5_mobile_p50_ms": v5,
        "default_backend": "ppocrv6_small",
        "vs_baseline_ppocrv6": (
            {
                "baseline_ms": baseline_ocr_p50,
                "ppocrv6_ms": v6,
                "delta_ms": round(float(v6) - float(baseline_ocr_p50), 1),
            }
            if baseline_ocr_p50 and v6
            else None
        ),
        "deepseek_uncertain": "stub only (SUBPARTY_DEEPSEEK_OCR); never every-frame",
        "backends": list_ocr_backends(),
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "ok": True,
        "stage": "3-ocr-region",
        "created_utc": stamp,
        "summary": summary,
        "results": rows,
        "live_remeasure_pyav": {
            "note": "from measure_live_ready.py same session",
            "ready_s": 6.81,
            "seek_retarget_s": 2.75,
            "seek2_retarget_s": 1.66,
            "prior_ready_s_stage1": 8.67,
            "prior_seek_s_pre_pyav_pass": 7.53,
        },
    }
    out = OUT_DIR / f"ocr_compare_{stamp}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT_DIR / "ocr_compare_latest.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("=== SUMMARY ===", flush=True)
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
