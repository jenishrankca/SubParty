"""Unit tests for SubParty 2.0 accuracy core (buffer, timing, revision, scheduler)."""

from __future__ import annotations

import unittest

from context_translate import pick_best_candidate
from dialogue_buffer import DialogueBuffer
from revision import CueRecord
from scheduler import PerformanceScheduler, rank_uncertain_cues
from timing_engine import refine_boundaries


class TestAccuracyCore(unittest.TestCase):
    def test_buffer_window_120s(self) -> None:
        buf = DialogueBuffer(window_s=120.0)
        buf.add(10.0, "甲")
        buf.add(50.0, "乙")
        buf.add(100.0, "丙")
        buf.add(200.0, "丁")  # trims old relative to 200
        self.assertTrue(all(ln.t >= 80.0 for ln in buf.lines))
        prior, upcoming = buf.context_zh(150.0)
        self.assertIn("丙", prior)
        self.assertIn("丁", upcoming)

    def test_revision_history(self) -> None:
        rec = CueRecord.new(
            text_zh="你好",
            start=1.0,
            end=2.0,
            text_en="Hi",
            ocr_confidence=0.9,
            timing_confidence=0.8,
            reason="provisional",
        )
        rec.revise(text_en="Hello", reason="retroactive-context", provisional=False, approved=True)
        self.assertEqual(len(rec.revisions), 2)
        self.assertEqual(rec.revisions[-1].previous_en, "Hi")
        self.assertEqual(rec.latest_approved_en(), "Hello")
        payload = rec.as_payload(1)
        self.assertIn("revisions", payload)
        self.assertEqual(payload["original_ocr"], "你好")

    def test_timing_refine(self) -> None:
        slot_text = {10: "", 11: "走吧", 12: "走吧", 13: "走吧", 14: ""}
        slot_conf = {11: 0.95, 12: 0.9, 13: 0.92}
        result = refine_boundaries(
            zh="走吧",
            rough_start=5.5,
            rough_end=6.5,
            slot_text=slot_text,
            slot_conf=slot_conf,
            base_tick=0.5,
            duration=20.0,
            high_accuracy=True,
        )
        self.assertAlmostEqual(result.start, 5.5)
        self.assertGreater(result.end, result.start)
        self.assertTrue(result.evidence.boundary_refined)
        self.assertGreater(result.confidence, 0.4)

    def test_scheduler_modes(self) -> None:
        perf = PerformanceScheduler(live_lag=2.0, buffer_ahead=0.0)
        self.assertEqual(perf.mode(), "catch_up")
        self.assertFalse(perf.should_run_refine())
        perf.live_lag = 0.1
        perf.buffer_ahead = 3.0
        self.assertEqual(perf.mode(), "refine")
        self.assertTrue(perf.should_run_refine())

    def test_quality_rank(self) -> None:
        ranked = rank_uncertain_cues(
            [
                {"cue_id": "a", "ocr_confidence": 0.95, "timing_confidence": 0.9, "provisional": False},
                {"cue_id": "b", "ocr_confidence": 0.5, "timing_confidence": 0.4, "provisional": True, "uncertain": True},
            ]
        )
        self.assertEqual(ranked[0]["cue_id"], "b")

    def test_candidate_compare(self) -> None:
        self.assertEqual(pick_best_candidate("Hello", "Hello there friend and more words" * 3, zh="你好"), "Hello")
        self.assertEqual(pick_best_candidate("Hi", "Hello", zh="你好"), "Hello")


if __name__ == "__main__":
    unittest.main()
