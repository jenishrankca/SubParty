"""High stages 7–9 — QA report, glossary series/suggest, batch status (unit)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from glossary import list_series_glossaries, suggest_character_terms
from paths import glossary_dir
from qa_report import build_qa_report


class TestQaReport(unittest.TestCase):
    def test_build_qa_report(self) -> None:
        job = MagicMock()
        job.id = "j1"
        job.status = "done"
        job.series_id = "demo"
        job.duration = 10.0
        job.processing_s = 2.0
        job.live_lag = 0.5
        job.audio_summary = {"disagreements": 1, "asr_rtf": {"processing_s": 1.0, "audio_duration_s": 10.0}}
        job.cue_records = []
        job.cues = [
            {
                "index": 1,
                "start": 1.0,
                "end": 2.0,
                "text_zh": "他来了",
                "text_en": "He came",
                "original_ocr": "他来了",
                "audio_transcript": "李逍遥来了",
                "audio_disagreement": True,
                "uncertain": True,
                "ocr_confidence": 0.9,
            }
        ]
        report = build_qa_report(job=job)
        self.assertTrue(report["ok"])
        self.assertEqual(report["uncertain_count"], 1)
        self.assertIn("ocr_cer", report["metrics"])
        self.assertIn("multimodal", report)
        self.assertEqual(report["multimodal"]["disagreements"], 1)


class TestGlossarySeries(unittest.TestCase):
    def test_suggest_and_list(self) -> None:
        cues = [{"text_zh": "李逍遥来了"}, {"text_zh": "李逍遥进门了"}]
        sug = suggest_character_terms(cues, existing={})
        self.assertTrue(any(s["zh"] == "李逍遥" for s in sug))
        # list should not crash even if empty
        items = list_series_glossaries()
        self.assertIsInstance(items, list)


class TestBatchStatusShape(unittest.TestCase):
    def test_batch_route_importable(self) -> None:
        from main import batch_status

        # empty batch
        res = batch_status("no-such-batch")
        self.assertEqual(res["total"], 0)
        self.assertEqual(res["batch_id"], "no-such-batch")


if __name__ == "__main__":
    unittest.main()
