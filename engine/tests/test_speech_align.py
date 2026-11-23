"""Stage 5 speech-aligned timing + Stage 3 evidence typo adopt."""

from __future__ import annotations

import unittest

from audio_verify import AudioSegment, compare_ocr_vs_speech, reconcile_zh, verify_cue_records
from revision import CueRecord
from speech_align import align_cue_records_to_speech, propose_speech_bounds


class TestSpeechAlign(unittest.TestCase):
    def test_propose_shifts_toward_asr(self) -> None:
        segs = [AudioSegment(1.05, 2.10, "他来了", 0.9)]
        prop = propose_speech_bounds(
            ocr_zh="他来了",
            cue_start=0.7,
            cue_end=2.5,
            segments=segs,
        )
        self.assertIsNotNone(prop)
        assert prop is not None
        self.assertAlmostEqual(prop["start"], 1.05, places=2)
        self.assertAlmostEqual(prop["end"], 2.10, places=2)
        self.assertIn("delta_start_ms", prop)

    def test_align_revises_records(self) -> None:
        rec = CueRecord.new(
            text_zh="他来了",
            start=0.6,
            end=2.6,
            text_en="He came",
            ocr_confidence=0.9,
            timing_confidence=0.7,
        )
        segs = [AudioSegment(1.0, 2.0, "他来了", 0.85)]
        summary = align_cue_records_to_speech([rec], segs)
        self.assertEqual(summary["aligned"], 1)
        self.assertAlmostEqual(rec.start, 1.0, places=2)
        self.assertAlmostEqual(rec.end, 2.0, places=2)
        self.assertTrue(any(r.reason == "speech-align" for r in rec.revisions))

    def test_weak_overlap_skips(self) -> None:
        prop = propose_speech_bounds(
            ocr_zh="修仙不如吃饭",
            cue_start=1.0,
            cue_end=2.0,
            segments=[AudioSegment(1.1, 1.9, "今天天气不错", 0.9)],
        )
        self.assertIsNone(prop)


class TestEvidenceTypoAdopt(unittest.TestCase):
    def test_typo_adopt_keeps_original_ocr(self) -> None:
        # Near-match OCR typo vs ASR
        ocr = "李逍遥来啦"
        asr = "李逍遥来了"
        cmp = compare_ocr_vs_speech(ocr, asr, asr_confidence=0.85)
        zh, note = reconcile_zh(
            original_ocr=ocr,
            audio_zh=asr,
            compare=cmp,
            asr_confidence=0.85,
        )
        self.assertEqual(note, "adopt-audio-ocr-typo")
        self.assertEqual(zh, asr)

    def test_verify_applies_typo_and_align(self) -> None:
        rec = CueRecord.new(
            text_zh="李逍遥来啦",
            start=0.5,
            end=2.5,
            text_en="Li came",
            ocr_confidence=0.88,
            timing_confidence=0.7,
        )
        segs = [AudioSegment(1.0, 2.0, "李逍遥来了", 0.9)]
        summary = verify_cue_records([rec], video_path="dummy.mp4", segments=segs)
        self.assertTrue(summary["ok"])
        self.assertEqual(rec.original_ocr, "李逍遥来啦")
        self.assertEqual(rec.text_zh, "李逍遥来了")
        self.assertEqual(rec.audio_transcript, "李逍遥来了")
        speech = summary.get("speech_align") or {}
        self.assertGreaterEqual(speech.get("aligned", 0), 1)
        self.assertAlmostEqual(rec.start, 1.0, places=2)


if __name__ == "__main__":
    unittest.main()
