"""MVP Critical metrics — CER, timing ms, revision before/after, ASR CER."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from metrics import (
    asr_cer_vs_gold,
    cer,
    ocr_cer_vs_gold,
    revision_before_after,
    revision_batch_quality,
    summarize_job_metrics,
    timing_errors,
    wer,
)


class TestCerWer(unittest.TestCase):
    def test_cer_exact_and_typo(self) -> None:
        self.assertEqual(cer("李逍遥来了", "李逍遥来了"), 0.0)
        self.assertGreater(cer("李逍遥来啦", "李逍遥来了"), 0.0)
        self.assertLess(cer("李逍遥来啦", "李逍遥来了"), 0.35)

    def test_wer_english(self) -> None:
        self.assertEqual(wer("hello world", "hello world"), 0.0)
        self.assertGreater(wer("hello there", "hello world"), 0.0)


class TestTimingMs(unittest.TestCase):
    def test_timing_reports_ms(self) -> None:
        gold = [{"start": 1.0, "end": 2.0, "text_zh": "他来了"}]
        pred = [{"start": 1.12, "end": 2.05, "text_zh": "他来了"}]
        t = timing_errors(pred, gold, match_tol_s=0.5)
        self.assertEqual(t["matched"], 1)
        self.assertAlmostEqual(t["mean_start_err_ms"], 120.0, places=0)
        self.assertIsNotNone(t["p95_start_err_ms"])


class TestOcrCer(unittest.TestCase):
    def test_ocr_cer_vs_gold(self) -> None:
        gold = [{"start": 0.0, "end": 1.0, "text_zh": "修仙不如吃饭"}]
        pred = [{"start": 0.05, "end": 1.0, "text_zh": "修仙不如吃饭"}]
        m = ocr_cer_vs_gold(pred, gold)
        self.assertEqual(m["matched"], 1)
        self.assertEqual(m["mean_cer"], 0.0)


class TestAsrCer(unittest.TestCase):
    def test_asr_cer(self) -> None:
        m = asr_cer_vs_gold(["我们走吧"], ["我们走吧"])
        self.assertEqual(m["mean_cer"], 0.0)
        m2 = asr_cer_vs_gold(["我们走吧"], ["你们走吧"])
        self.assertGreater(m2["mean_cer"], 0.0)


class TestRevisionQuality(unittest.TestCase):
    def test_ambiguous_fixture_before_after(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "annotated_ambiguous.json"
        data = json.loads(fixture.read_text(encoding="utf-8"))
        first = data["cues"][0]
        row = revision_before_after(
            before_en=first["text_en_provisional"],
            after_en=first["text_en_gold"],
            gold_en=first["text_en_gold"],
        )
        self.assertTrue(row["improved"])
        self.assertEqual(row["after_cer"], 0.0)
        self.assertGreater(row["before_cer"], 0.0)

        batch = revision_batch_quality(
            [
                {
                    "before_en": first["text_en_provisional"],
                    "after_en": first["text_en_gold"],
                    "gold_en": first["text_en_gold"],
                }
            ]
        )
        self.assertEqual(batch["n"], 1)
        self.assertEqual(batch["improved_rate"], 1.0)


class TestSummarize(unittest.TestCase):
    def test_summarize_includes_cer_ms_revision(self) -> None:
        gold = [{"start": 0.0, "end": 1.0, "text_zh": "你好", "text_en": "Hello"}]
        pred = [{"start": 0.1, "end": 1.0, "text_zh": "你好", "text_en": "Hello"}]
        m = summarize_job_metrics(
            pred_cues=pred,
            gold_cues=gold,
            processing_s=2.0,
            video_duration_s=10.0,
            asr_hyp=["你好"],
            asr_gold=["你好"],
            revision_items=[
                {"before_en": "Hi", "after_en": "Hello", "gold_en": "Hello"},
            ],
        )
        self.assertIn("ocr_cer", m)
        self.assertIn("mean_start_err_ms", m["timing"])
        self.assertIn("asr_cer", m)
        self.assertEqual(m["asr_cer"]["mean_cer"], 0.0)
        self.assertTrue(m["revision"]["improved_rate"] > 0)
        self.assertEqual(m["rtf"], 0.2)


if __name__ == "__main__":
    unittest.main()
