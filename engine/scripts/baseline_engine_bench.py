"""Stage-1 engine baseline: decode / OCR / Live Ready / short Quality / optional ASR.

Writes JSON under engine/benchmarks/. Does not change Live defaults on main.

Usage:
  .venv\\Scripts\\python.exe scripts\\baseline_engine_bench.py
  .venv\\Scripts\\python.exe scripts\\baseline_engine_bench.py --skip-asr
  .venv\\Scripts\\python.exe scripts\\baseline_engine_bench.py --decode-n 12
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

from ffmpeg_util import extract_audio_wav, extract_region, probe_duration, probe_size
from ocr import ocr_lines, warm_ocr
from pipeline import jobs
from text_filters import filter_ocr_result


SAMPLES = REPO / "samples"
OUT_DIR = ROOT / "benchmarks"


def _pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _ms_stats(samples_s: list[float]) -> dict:
    ms = [x * 1000.0 for x in samples_s]
    if not ms:
        return {"n": 0}
    return {
        "n": len(ms),
        "mean_ms": round(statistics.mean(ms), 1),
        "p50_ms": round(_pct(ms, 50) or 0, 1),
        "p95_ms": round(_pct(ms, 95) or 0, 1),
        "min_ms": round(min(ms), 1),
        "max_ms": round(max(ms), 1),
    }


def _timestamps(duration: float, n: int) -> list[float]:
    if duration <= 1.0:
        return [0.0]
    # Avoid exact EOF; space across the clip
    span = max(0.5, duration - 1.0)
    return [round(0.5 + (span * i / max(1, n - 1)), 3) for i in range(n)]


def bench_decode_ocr(
    video: Path,
    *,
    n: int,
    band_ratio: float = 0.2,
    scale_width: int = 320,
) -> dict:
    duration = probe_duration(video)
    w, h = probe_size(video)
    times = _timestamps(duration, n)
    decode_s: list[float] = []
    ocr_s: list[float] = []
    texts: list[str] = []

    with tempfile.TemporaryDirectory(prefix="bench_dec_") as tmp:
        tmp_dir = Path(tmp)
        for i, t in enumerate(times):
            out = tmp_dir / f"f_{i:03d}.png"
            t0 = time.perf_counter()
            extract_region(
                video,
                t,
                out,
                x_ratio=0.06,
                y_ratio=1.0 - band_ratio,
                w_ratio=0.88,
                h_ratio=band_ratio,
                height=h,
                width=w,
                scale_width=scale_width,
            )
            decode_s.append(time.perf_counter() - t0)

            t1 = time.perf_counter()
            filtered = filter_ocr_result(ocr_lines(str(out)))
            ocr_s.append(time.perf_counter() - t1)
            texts.append(filtered.text or "")

    non_empty = sum(1 for x in texts if x.strip())
    return {
        "video": str(video.relative_to(REPO)) if video.is_relative_to(REPO) else str(video),
        "duration_s": round(duration, 2),
        "size": {"w": w, "h": h},
        "scale_width": scale_width,
        "timestamps": times,
        "decode": _ms_stats(decode_s),
        "ocr": _ms_stats(ocr_s),
        "combined": _ms_stats([a + b for a, b in zip(decode_s, ocr_s)]),
        "ocr_nonempty": non_empty,
        "ocr_samples": texts[:8],
        "decode_path": "per-frame ffmpeg subprocess (_run_ffmpeg_frame)",
    }


def _wait_ready_or_done(job_id: str, *, ready_s: float, hard_s: float) -> dict:
    """Live: stop when buffer_ahead>=2.5 or cue; Quality: wait for done."""
    t0 = time.perf_counter()
    ready_at = None
    last = None
    deadline = time.time() + hard_s
    while time.time() < deadline:
        j = jobs.get(job_id)
        if not j:
            return {"ok": False, "error": "job disappeared"}
        snap = {
            "status": j.status,
            "progress": round(j.progress, 4),
            "cue_count": j.cue_count,
            "buffer_ahead": round(j.buffer_ahead, 2),
            "live_lag": round(j.live_lag, 2),
            "sample_interval": round(j.sample_interval, 2),
            "wall_s": round(time.perf_counter() - t0, 2),
        }
        last = snap
        if ready_at is None and (
            j.buffer_ahead >= 2.5 or (j.cue_count >= 1 and j.buffer_ahead >= 1.5)
        ):
            ready_at = time.perf_counter() - t0
            snap["ready_s"] = round(ready_at, 2)
        if j.live_mode and ready_at is not None and (time.perf_counter() - t0) >= ready_s:
            # Live baseline: capture Ready then cancel (do not full-file)
            jobs.cancel(job_id)
            return {
                "ok": True,
                "mode": "live",
                "ready_s": round(ready_at, 2),
                "cancelled_after_ready": True,
                "final": snap,
                "cues_preview": [
                    {
                        "start": c.get("start"),
                        "end": c.get("end"),
                        "text_zh": c.get("text_zh"),
                        "text_en": c.get("text_en"),
                    }
                    for c in (j.cues or [])[:5]
                ],
            }
        if j.status in {"done", "error", "cancelled"}:
            elapsed = time.perf_counter() - t0
            rtf = None
            if j.duration and j.duration > 0:
                rtf = round(elapsed / j.duration, 4)
            return {
                "ok": j.status == "done",
                "mode": "quality" if not j.live_mode else "live",
                "ready_s": round(ready_at, 2) if ready_at is not None else None,
                "wall_s": round(elapsed, 2),
                "rtf": rtf,
                "status": j.status,
                "error": j.error,
                "cue_count": j.cue_count,
                "duration_s": j.duration,
                "final": snap,
                "cues_preview": [
                    {
                        "start": c.get("start"),
                        "end": c.get("end"),
                        "text_zh": c.get("text_zh"),
                        "text_en": c.get("text_en"),
                        "maturity": c.get("maturity"),
                    }
                    for c in (j.cues or [])[:8]
                ],
            }
        time.sleep(0.2)
    jobs.cancel(job_id)
    return {"ok": False, "error": "timeout", "final": last, "ready_s": ready_at}


def bench_live_ready(video: Path) -> dict:
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
    result = _wait_ready_or_done(job.id, ready_s=2.0, hard_s=90.0)
    result["create_to_end_s"] = round(time.perf_counter() - t0, 2)
    result["video"] = str(video.relative_to(REPO)) if video.is_relative_to(REPO) else str(video)
    result["live_mode"] = True
    return result


def bench_quality_job(video: Path, *, timeout_s: float = 180.0) -> dict:
    t0 = time.perf_counter()
    job = jobs.create(
        str(video),
        fps=1.0,
        lookahead=8.0,
        playhead=0.0,
        engine_id="argos",
        live_mode=False,
        high_accuracy=True,
        audio_verify=False,
        context_window_s=60.0,
    )
    result = _wait_ready_or_done(job.id, ready_s=timeout_s, hard_s=timeout_s)
    result["create_to_end_s"] = round(time.perf_counter() - t0, 2)
    result["video"] = str(video.relative_to(REPO)) if video.is_relative_to(REPO) else str(video)
    result["live_mode"] = False
    return result


def bench_asr(video: Path, *, max_audio_s: float = 15.0) -> dict:
    from audio_verify import whisper_available

    if not whisper_available():
        return {"ok": False, "skipped": True, "reason": "faster-whisper not available"}
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return {"ok": False, "skipped": True, "reason": "faster_whisper import failed"}

    duration = probe_duration(video)
    clip_s = min(max_audio_s, max(1.0, duration))
    try:
        with tempfile.TemporaryDirectory(prefix="bench_asr_") as tmp:
            wav = Path(tmp) / "a.wav"
            t_ex0 = time.perf_counter()
            extract_audio_wav(video, wav, start_s=0.0, duration_s=clip_s)
            extract_s = time.perf_counter() - t_ex0

            t_load0 = time.perf_counter()
            model = WhisperModel("small", device="cpu", compute_type="int8")
            load_s = time.perf_counter() - t_load0

            t_inf0 = time.perf_counter()
            segments, info = model.transcribe(str(wav), language="zh", beam_size=1)
            texts = [s.text.strip() for s in segments if s.text and s.text.strip()]
            infer_s = time.perf_counter() - t_inf0

        return {
            "ok": True,
            "video": str(video.relative_to(REPO)) if video.is_relative_to(REPO) else str(video),
            "audio_s": round(clip_s, 2),
            "extract_ms": round(extract_s * 1000, 1),
            "model_load_ms": round(load_s * 1000, 1),
            "infer_ms": round(infer_s * 1000, 1),
            "asr_rtf": round(infer_s / clip_s, 4) if clip_s > 0 else None,
            "segments": len(texts),
            "preview": texts[:5],
            "language": getattr(info, "language", None),
            "engine": "faster-whisper-small-cpu-int8",
        }
    except Exception as exc:
        # Known: some PyAV builds break faster-whisper decode_audio(metadata_errors=...)
        return {
            "ok": False,
            "skipped": True,
            "reason": f"asr runtime error: {type(exc).__name__}: {exc}",
            "video": str(video.relative_to(REPO)) if video.is_relative_to(REPO) else str(video),
            "audio_s": round(clip_s, 2),
            "note": "Package available; inference failed — pin av/faster-whisper or use audio_verify path later",
        }


def main() -> int:
    ap = argparse.ArgumentParser(description="SubParty stage-1 engine baseline")
    ap.add_argument("--decode-n", type=int, default=10, help="decode/OCR samples per video")
    ap.add_argument("--skip-asr", action="store_true")
    ap.add_argument("--skip-quality", action="store_true")
    ap.add_argument("--skip-live", action="store_true")
    args = ap.parse_args()

    weird = SAMPLES / "the-weird-games-5min.mp4"
    demo_zh = SAMPLES / "demo_zh_hardsub.mp4"
    demo_junk = SAMPLES / "demo_watermark_junk.mp4"
    clips = [p for p in (weird, demo_zh, demo_junk) if p.exists()]
    if not clips:
        print(json.dumps({"ok": False, "error": "no sample clips found"}))
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report: dict = {
        "ok": True,
        "stage": 1,
        "created_utc": stamp,
        "branch_note": "cursor/subparty-engine-bench-7239 — baseline only; Live stays on main",
        "decode_path": {
            "module": "engine/ffmpeg_util.py",
            "function": "_run_ffmpeg_frame",
            "pattern": "subprocess per frame: ffmpeg -ss T -i video -frames:v 1 -vf crop+scale -y out.png",
            "to_beat_next": "PyAV persistent demux/decode adapter",
        },
        "environment": {
            "python": sys.version.split()[0],
            "cwd": str(ROOT),
        },
        "phases": {},
    }

    print("=== warm OCR ===", flush=True)
    warm_t0 = time.perf_counter()
    warm_ocr()
    report["phases"]["warm_ocr_s"] = round(time.perf_counter() - warm_t0, 2)

    print("=== A/B decode + OCR microbench ===", flush=True)
    decode_ocr = []
    for clip in clips:
        print(f"  {clip.name}…", flush=True)
        row = bench_decode_ocr(clip, n=args.decode_n if clip != weird else max(8, args.decode_n))
        decode_ocr.append(row)
        print(
            f"    decode p50={row['decode'].get('p50_ms')}ms  "
            f"ocr p50={row['ocr'].get('p50_ms')}ms  "
            f"combo p50={row['combined'].get('p50_ms')}ms  "
            f"nonempty={row['ocr_nonempty']}/{row['decode'].get('n')}",
            flush=True,
        )
    report["phases"]["decode_ocr"] = decode_ocr

    if not args.skip_live and weird.exists():
        print("=== C Live Ready (weird-games-5min) ===", flush=True)
        live = bench_live_ready(weird)
        report["phases"]["live_ready"] = live
        print(
            f"  ready_s={live.get('ready_s')} ok={live.get('ok')} "
            f"cues={((live.get('final') or {}).get('cue_count'))}",
            flush=True,
        )

    if not args.skip_quality:
        print("=== D Quality short demos ===", flush=True)
        quality = []
        for clip in (demo_zh, demo_junk):
            if not clip.exists():
                continue
            print(f"  {clip.name}…", flush=True)
            row = bench_quality_job(clip, timeout_s=180.0)
            quality.append(row)
            print(
                f"    wall={row.get('wall_s')}s rtf={row.get('rtf')} "
                f"cues={row.get('cue_count')} ok={row.get('ok')}",
                flush=True,
            )
        report["phases"]["quality_demos"] = quality

    if not args.skip_asr:
        print("=== E ASR (faster-whisper, short window) ===", flush=True)
        asr_rows = []
        for clip in (demo_zh, weird):
            if not clip.exists():
                continue
            # weird: only first 15s audio — do not ASR full 5min in stage 1
            print(f"  {clip.name}…", flush=True)
            row = bench_asr(clip, max_audio_s=12.0 if clip == weird else 8.0)
            asr_rows.append(row)
            if row.get("skipped"):
                print(f"    skipped: {row.get('reason')}", flush=True)
            else:
                print(
                    f"    infer_ms={row.get('infer_ms')} asr_rtf={row.get('asr_rtf')} "
                    f"segs={row.get('segments')}",
                    flush=True,
                )
        report["phases"]["asr"] = asr_rows

    # Summary rollup for humans / PRs
    summary: dict = {"decode_path": "per-frame FFmpeg subprocess"}
    if decode_ocr:
        weird_row = next((r for r in decode_ocr if "weird" in r["video"]), decode_ocr[0])
        summary["weird_games_decode_p50_ms"] = (weird_row.get("decode") or {}).get("p50_ms")
        summary["weird_games_ocr_p50_ms"] = (weird_row.get("ocr") or {}).get("p50_ms")
        summary["weird_games_combo_p50_ms"] = (weird_row.get("combined") or {}).get("p50_ms")
    live = report["phases"].get("live_ready") or {}
    if live:
        summary["live_ready_s"] = live.get("ready_s")
    for q in report["phases"].get("quality_demos") or []:
        key = Path(q.get("video") or "demo").stem
        summary[f"quality_{key}_wall_s"] = q.get("wall_s")
        summary[f"quality_{key}_rtf"] = q.get("rtf")
        summary[f"quality_{key}_cues"] = q.get("cue_count")
    for a in report["phases"].get("asr") or []:
        if a.get("ok"):
            key = Path(a.get("video") or "asr").stem
            summary[f"asr_{key}_rtf"] = a.get("asr_rtf")
            summary[f"asr_{key}_infer_ms"] = a.get("infer_ms")
    summary["next_pr"] = "PyAV persistent decode adapter (beat per-frame FFmpeg)"
    report["summary"] = summary

    out_path = OUT_DIR / f"baseline_{stamp}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    latest = OUT_DIR / "baseline_latest.json"
    latest.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=== SUMMARY ===", flush=True)
    print(json.dumps(summary, indent=2), flush=True)
    print(f"wrote {out_path}", flush=True)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
