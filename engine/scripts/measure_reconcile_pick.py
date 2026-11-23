"""Measure sequence reconcile + cue-aware translation pick (bench branch).

Runs offline unit-style fixtures (no Whisper) and optional live ASR on a sample
when SUBPARTY_MEASURE_ASR=1 and a video path is given.

Usage (from engine/):
  python scripts/measure_reconcile_pick.py
  python scripts/measure_reconcile_pick.py --video ../samples/demo_zh_hardsub.mp4
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from audio_verify import AudioSegment, verify_cue_records  # noqa: E402
from context_translate import pick_best_candidate, score_en_against_zh  # noqa: E402
from reconcile import apply_reconcile_to_records, match_cues_to_asr_sequence  # noqa: E402
from revision import CueRecord  # noqa: E402


def _fixture_reconcile() -> dict:
    cues = [
        {"original_ocr": "他来了", "start": 1.0, "end": 2.0},
        {"original_ocr": "我们走吧", "start": 3.0, "end": 4.0},
        {"original_ocr": "", "start": 5.0, "end": 6.0},
        {"original_ocr": "修仙不如吃饭", "start": 7.0, "end": 8.0},
    ]
    segs = [
        AudioSegment(1.05, 1.95, "他来了", 0.9),
        AudioSegment(2.95, 3.90, "我们走吧", 0.88),
        AudioSegment(5.10, 5.85, "明天见", 0.91),
        AudioSegment(7.00, 8.00, "今天天气不错", 0.87),
    ]
    t0 = time.perf_counter()
    batch = match_cues_to_asr_sequence(cues, segs)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    records = [
        CueRecord.new(
            text_zh=c["original_ocr"] or "",
            start=float(c["start"]),
            end=float(c["end"]),
            text_en="",
            ocr_confidence=0.85,
            timing_confidence=0.8,
        )
        for c in cues
    ]
    for r, c in zip(records, cues):
        r.original_ocr = c["original_ocr"]
    apply = apply_reconcile_to_records(records, segs)
    return {
        "elapsed_ms": round(elapsed_ms, 3),
        "matched": batch.as_dict()["matched"],
        "inferred_count": batch.as_dict()["inferred_count"],
        "disagreements": apply["disagreements"],
        "timing_sources": [r.timing_source for r in records],
        "visual_vs_speech_delta_ms": [
            None
            if r.speech_start is None
            else round((float(r.speech_start) - float(r.visual_start or r.start)) * 1000, 1)
            for r in records
        ],
        "sequence_aware": True,
    }


def _fixture_pick() -> dict:
    cases = [
        {
            "zh": "她来了吗？",
            "bare": "It is fine.",
            "contextual": "Is she coming?",
            "expect": "Is she coming?",
        },
        {
            "zh": "第3集开始",
            "bare": "Episode next",
            "contextual": "Episode 3 begins.",
            "expect": "Episode 3 begins.",
        },
        {
            "zh": "你好",
            "bare": "Hello",
            "contextual": "Hello there friend and more words" * 3,
            "expect": "Hello",
        },
        {
            "zh": "我们走吧",
            "bare": "Let's go.",
            "contextual": "Let's go.",
            "expect": "Let's go.",
        },
    ]
    t0 = time.perf_counter()
    results = []
    correct = 0
    for c in cases:
        picked = pick_best_candidate(c["bare"], c["contextual"], zh=c["zh"])
        ok = picked == c["expect"]
        if ok:
            correct += 1
        results.append(
            {
                "zh": c["zh"],
                "picked": picked,
                "expect": c["expect"],
                "ok": ok,
                "bare_score": round(score_en_against_zh(c["bare"], zh=c["zh"], bare_len=len(c["bare"])), 3),
                "ctx_score": round(
                    score_en_against_zh(c["contextual"], zh=c["zh"], bare_len=len(c["bare"])), 3
                ),
            }
        )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "elapsed_ms": round(elapsed_ms, 3),
        "cases": len(cases),
        "correct": correct,
        "accuracy": round(correct / max(len(cases), 1), 3),
        "results": results,
        "method": "cue-aware-semantic",
    }


def _optional_video(video: Path) -> dict | None:
    if not video.is_file():
        return {"ok": False, "error": f"missing {video}"}
    if os.environ.get("SUBPARTY_MEASURE_ASR", "").strip() not in {"1", "true", "yes"}:
        return {"ok": False, "skipped": True, "reason": "set SUBPARTY_MEASURE_ASR=1 to run Whisper"}
    # Minimal synthetic cues spanning first few seconds — ASR fills evidence
    records = [
        CueRecord.new(
            text_zh="测试",
            start=0.5,
            end=2.0,
            text_en="test",
            ocr_confidence=0.8,
            timing_confidence=0.7,
        )
    ]
    t0 = time.perf_counter()
    try:
        summary = verify_cue_records(records, video_path=str(video))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "elapsed_s": round(time.perf_counter() - t0, 3),
        "cues": summary.get("cues"),
        "disagreements": summary.get("disagreements"),
        "inferred": summary.get("inferred"),
        "sequence_reconcile_ok": bool((summary.get("sequence_reconcile") or {}).get("ok")),
        "speech_align": summary.get("speech_align"),
        "asr_rtf": summary.get("asr_rtf"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, default=None)
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "benchmarks",
    )
    args = ap.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "stamp": stamp,
        "branch_note": "cursor/subparty-engine-bench-7239",
        "reconcile_fixture": _fixture_reconcile(),
        "pick_fixture": _fixture_pick(),
        "video": None,
    }
    if args.video:
        report["video"] = _optional_video(args.video)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / f"reconcile_pick_{stamp}.json"
    latest = args.out_dir / "reconcile_pick_latest.json"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    out.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
