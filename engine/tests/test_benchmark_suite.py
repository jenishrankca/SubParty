"""Benchmark suite against synthetic annotated ZH + timestamps."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from metrics import summarize_job_metrics
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

        # Maturity: after done, cues should be final (not stuck provisional)
        provisional_left = [c for c in finished.cues if c.get("provisional")]
        self.assertEqual(provisional_left, [], "expected final promote of provisional cues")
        for c in finished.cues:
            mat = c.get("maturity")
            if mat is not None:
                self.assertEqual(mat, "final")

        gold = [{"start": 0.0, "end": 3.0, "text_zh": primary}]
        metrics = summarize_job_metrics(
            pred_cues=finished.cues,
            gold_cues=gold,
            processing_s=elapsed,
            video_duration_s=float(finished.duration or 3.0),
            live_latency_s=float(getattr(finished, "live_lag", 0.0) or 0.0),
            timing_tol_s=0.5,
        )
        self.assertGreaterEqual(metrics["ocr"]["hit"], 1)
        self.assertGreaterEqual(metrics["timing"]["matched"], 1)
        self.assertIsNotNone(metrics["timing"]["mean_start_err"])
        # 2.1: tighten vs old ±1.5s — allow up to 0.75s mean start error on synth
        self.assertLess(float(metrics["timing"]["mean_start_err"]), 0.75)
        self.assertIsNotNone(metrics["rtf"])
        self.assertLess(elapsed, 120.0)

        report = {
            "elapsed_s": round(elapsed, 2),
            "cue_count": finished.cue_count,
            "metrics": metrics,
            "scheduler_note": "2.1 high_accuracy+maturity+frame_timing",
        }
        (td / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("BENCH", report)


if __name__ == "__main__":
    unittest.main()
