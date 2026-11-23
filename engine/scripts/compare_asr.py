"""ASR adapter compare: faster-whisper baseline (+ Qwen3/FunASR if installed).

Also records ForcedAligner/TinyAligner experiment status (separate from Live).

Usage:
  .venv\\Scripts\\python.exe scripts\\compare_asr.py
  .venv\\Scripts\\python.exe scripts\\compare_asr.py --audio-s 12
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from align_experiments import list_align_experiments, run_forced_aligner, run_tiny_aligner
from asr_adapters import asr_registry_status, list_asr_adapters
from ffmpeg_util import extract_audio_wav, probe_duration


SAMPLES = REPO / "samples"
OUT_DIR = ROOT / "benchmarks"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--video",
        default=str(SAMPLES / "the-weird-games-5min.mp4"),
    )
    ap.add_argument("--demo", default=str(SAMPLES / "demo_zh_hardsub.mp4"))
    ap.add_argument("--audio-s", type=float, default=12.0)
    args = ap.parse_args()

    # Stable bench path: avoid broken CUDA (cublas) unless user opts in
    import os

    os.environ.setdefault("SUBPARTY_WHISPER_DEVICE", "cpu")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Before: harness previously skipped with PyAV metadata_errors
    before = {
        "harness": "faster-whisper decode_audio → av.open(metadata_errors=...)",
        "result": "TypeError on PyAV 19 — ASR skipped in stage-1 baseline",
        "baseline_asr_status": "skipped",
    }

    registry = asr_registry_status()
    print("=== ASR registry ===", flush=True)
    for r in registry:
        print(f"  {r['id']}: available={r.get('available')} ({r.get('role')})", flush=True)

    runs: list[dict] = []
    clips = []
    for label, path, dur_cap in (
        ("demo_zh_hardsub", Path(args.demo), min(8.0, args.audio_s)),
        ("weird-games", Path(args.video), args.audio_s),
    ):
        if path.exists():
            clips.append((label, path, dur_cap))

    with tempfile.TemporaryDirectory(prefix="asr_cmp_") as tmp:
        tmp_dir = Path(tmp)
        for label, video, cap in clips:
            vdur = probe_duration(video)
            clip_s = min(cap, max(1.0, vdur))
            wav = tmp_dir / f"{label}.wav"
            print(f"=== extract {label} audio {clip_s:.1f}s ===", flush=True)
            t_ex = time.perf_counter()
            extract_audio_wav(video, wav, start_s=0.0, duration_s=clip_s)
            extract_ms = (time.perf_counter() - t_ex) * 1000

            for adapter in list_asr_adapters():
                print(f"--- {adapter.id} on {label} ---", flush=True)
                if not adapter.available():
                    row = {
                        "clip": label,
                        "engine_id": adapter.id,
                        "ok": False,
                        "skipped": True,
                        "reason": "not installed",
                        "status": adapter.status(),
                        "extract_ms": round(extract_ms, 1),
                    }
                    print(f"  skipped (not installed)", flush=True)
                    runs.append(row)
                    continue
                result = adapter.transcribe_wav(wav, audio_duration_s=clip_s)
                row = {
                    "clip": label,
                    **result.as_dict(),
                    "extract_ms": round(extract_ms, 1),
                }
                runs.append(row)
                if result.ok:
                    print(
                        f"  ok infer={result.infer_s:.2f}s rtf={result.rtf} "
                        f"segs={len(result.segments)} preview={row.get('preview')}",
                        flush=True,
                    )
                else:
                    print(f"  FAIL {result.error}", flush=True)

            # Align experiments on demo only (need a transcript)
            if label.startswith("demo"):
                # Use whisper text if we got any, else a placeholder ZH line
                whisper_text = ""
                for r in runs:
                    if r.get("clip") == label and r.get("engine_id", "").startswith("faster") and r.get("ok"):
                        whisper_text = " ".join(r.get("preview") or [])
                        break
                transcript = whisper_text or "你好世界"
                fa = run_forced_aligner(wav, transcript)
                ta = run_tiny_aligner(wav, transcript)
                runs.append({"clip": label, "align": fa.as_dict()})
                runs.append({"clip": label, "align": ta.as_dict()})
                print(f"  forced_aligner: skipped={fa.skipped} ok={fa.ok} {fa.reason}", flush=True)
                print(f"  tiny_aligner: skipped={ta.skipped} {ta.reason}", flush=True)

    whisper_ok = [r for r in runs if r.get("engine_id", "").startswith("faster") and r.get("ok")]
    after = {
        "harness": "FFmpeg WAV extract → asr_io.load_wav_mono_f32 → ndarray to WhisperModel",
        "faster_whisper_ok": len(whisper_ok) > 0,
        "faster_whisper_runs": [
            {
                "clip": r["clip"],
                "infer_s": r.get("infer_s"),
                "rtf": r.get("rtf"),
                "segments": r.get("segment_count"),
            }
            for r in whisper_ok
        ],
    }

    summary = {
        "before": before,
        "after": after,
        "registry": registry,
        "align_experiments": list_align_experiments(),
        "qwen3_license": "Apache-2.0",
        "default_baseline": "faster-whisper-small",
    }

    report = {
        "ok": bool(whisper_ok),
        "stage": "4-asr-align",
        "created_utc": stamp,
        "summary": summary,
        "runs": runs,
    }
    out = OUT_DIR / f"asr_compare_{stamp}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT_DIR / "asr_compare_latest.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("=== SUMMARY ===", flush=True)
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    print(f"wrote {out}", flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
