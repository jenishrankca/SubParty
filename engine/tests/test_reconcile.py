"""Sequence-aware OCR↔ASR reconciliation + visual/speech bounds."""

from __future__ import annotations

import unittest

from audio_verify import AudioSegment
from reconcile import apply_reconcile_to_records, match_cues_to_asr_sequence
from revision import CueRecord


class TestSequenceReconcile(unittest.TestCase):
    def test_sequence_greedy_nondecreasing(self) -> None:
        cues = [
            {"original_ocr": "第一句", "start": 1.0, "end": 2.0},
            {"original_ocr": "第二句", "start": 3.0, "end": 4.0},
            {"original_ocr": "第三句", "start": 5.0, "end": 6.0},
        ]
        segs = [
            AudioSegment(0.9, 1.8, "第一句", 0.9),
            AudioSegment(2.9, 3.9, "第二句", 0.85),
            AudioSegment(4.9, 5.8, "第三句", 0.88),
        ]
        batch = match_cues_to_asr_sequence(cues, segs)
        idxs = [m.segment_index for m in batch.matches]
        self.assertEqual(idxs, [0, 1, 2])
        self.assertTrue(batch.sequence_aware)
        # Visual ≠ speech may still share values; both present
        for m in batch.matches:
            self.assertIsNotNone(m.bounds.visual_start)
            self.assertIsNotNone(m.bounds.speech_start)

    def test_distinct_visual_speech_and_inferred(self) -> None:
        # Empty OCR → adopt audio → inferred
        rec = CueRecord.new(
            text_zh="",
            start=1.0,
            end=2.0,
            text_en="",
            ocr_confidence=0.2,
            timing_confidence=0.5,
        )
        # CueRecord.new stores original_ocr from text_zh
        rec.original_ocr = ""
        segs = [AudioSegment(1.1, 1.9, "他来了", 0.92)]
        summary = apply_reconcile_to_records([rec], segs)
        self.assertTrue(summary["ok"])
        self.assertGreaterEqual(summary["inferred"], 1)
        self.assertEqual(rec.visual_start, 1.0)
        self.assertEqual(rec.visual_end, 2.0)
        self.assertIsNotNone(rec.speech_start)
        self.assertAlmostEqual(float(rec.speech_start), 1.1, places=2)
        self.assertTrue(rec.inferred)
        self.assertEqual(rec.text_zh, "他来了")
        self.assertEqual(rec.original_ocr, "")  # never silently drop / invent OCR source

    def test_disagreement_keeps_ocr(self) -> None:
        rec = CueRecord.new(
            text_zh="修仙不如吃饭",
            start=1.0,
            end=2.0,
            text_en="Cultivation beats lunch",
            ocr_confidence=0.9,
            timing_confidence=0.8,
        )
        segs = [AudioSegment(1.0, 2.0, "今天天气不错", 0.9)]
        summary = apply_reconcile_to_records([rec], segs)
        self.assertEqual(summary["disagreements"], 1)
        self.assertEqual(rec.original_ocr, "修仙不如吃饭")
        self.assertEqual(rec.text_zh, "修仙不如吃饭")
        self.assertEqual(rec.audio_transcript, "今天天气不错")
        payload = rec.as_payload(1)
        self.assertIn("visual_start", payload)
        self.assertIn("speech_start", payload)
        self.assertIn("inferred", payload)


if __name__ == "__main__":
    unittest.main()
