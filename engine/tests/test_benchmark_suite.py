"""Benchmark suite against synthetic annotated ZH + timestamps."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from pipeline import jobs
from tests.synth_hardsub import make_hardsub_video


def _wait(job_id: str, timeout_s: float = 180.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        job = jobs.get(job_id)
        if job and job.status in {"done", "error", "cancelled"}:
            return job
        time.sleep(0.3)
    raise TimeoutError(job_id)


def _timing_error(pred: list[dict], gold: list[dict]) -> dict:
    """Match by ZH equality order; report mean abs start/end error + misses."""
    errs_start: list[float] = []
    errs_end: list[float] = []
    hit = 0
    used = set()
    for g in gold:
        gzh = g["text_zh"]
        best = None
        best_d = 1e9
        for i, p in enumerate(pred):
            if i in used:
                continue
            if p.get("text_zh") != gzh:
                continue
            d = abs(float(p["start"]) - float(g["start"]))
            if d < best_d:
                best_d = d
                best = i
        if best is None:
            continue
        used.add(best)
        hit += 1
        p = pred[best]
        errs_start.append(abs(float(p["start"]) - float(g["start"])))
        errs_end.append(abs(float(p["end"]) - float(g["end"])))
    return {
        "matched": hit,
        "gold": len(gold),
        "missed": len(gold) - hit,
        "mean_start_err": sum(errs_start) / len(errs_start) if errs_start else None,
        "mean_end_err": sum(errs_end) / len(errs_end) if errs_end else None,
    }


class TestBenchmarkSuite(unittest.TestCase):
    def test_synth_annotated_benchmark(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "annotated_synth.json"
        data = json.loads(fixture.read_text(encoding="utf-8"))
        td = Path(tempfile.mkdtemp(prefix="bench_"))
        # Build a longer clip that contains each line in sequence
        # For simplicity: one video per line concatenated isn't needed —
        # use the first gold line as the burned-in text (OCR target),
        # and evaluate that we recover that ZH with reasonable timing.
        primary = data["cues"][0]["text_zh"]
        video = td / "bench.mp4"
        make_hardsub_video(
            video,
            width=int(data["width"]),
            height=int(data["height"]),
            text_y_ratio=float(data["text_y_ratio"]),
            text=primary,
            seconds=3.0,
            fps=2,
        )
        t0 = time.perf_counter()
        job = jobs.create(
            str(video),
            fps=1.0,
            lookahead=6.0,
            playhead=0.0,
            context_window_s=120.0,
            high_accuracy=True,
            engine_id="argos",
        )
        finished = _wait(job.id, timeout_s=180.0)
        elapsed = time.perf_counter() - t0
        self.assertEqual(finished.status, "done", finished.error)
        self.assertGreaterEqual(finished.cue_count, 1)

        # OCR accuracy: primary ZH appears in at least one cue
        zh_hit = any(primary in str(c.get("text_zh") or "") for c in finished.cues)
        self.assertTrue(zh_hit, f"OCR miss for {primary!r} in {finished.cues}")

        # Provisional → refined: after done, cues should not stay provisional
        provisional_left = [c for c in finished.cues if c.get("provisional")]
        self.assertEqual(provisional_left, [], "expected final promote of provisional cues")

        # Timing vs gold first cue (same text, expected near start of clip)
        gold = [{"start": 0.0, "end": 3.0, "text_zh": primary}]
        timing = _timing_error(finished.cues, gold)
        self.assertGreaterEqual(timing["matched"], 1)
        self.assertIsNotNone(timing["mean_start_err"])
        self.assertLess(float(timing["mean_start_err"]), 1.5)

        # Speed: finish short clip in reasonable wall time
        self.assertLess(elapsed, 120.0)

        report = {
            "elapsed_s": round(elapsed, 2),
            "cue_count": finished.cue_count,
            "ocr_hit": zh_hit,
            "timing": timing,
            "scheduler_note": "high_accuracy+context_120s",
        }
        (td / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("BENCH", report)


if __name__ == "__main__":
    unittest.main()
