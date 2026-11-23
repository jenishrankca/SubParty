"""Broader MVP annotated suite — CER / timing ms / revision / speech-align / ASR RTF (no Whisper load)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from audio_verify import AudioSegment, AudioWord, asr_rtf_report, reconcile_zh, compare_ocr_vs_speech
from metrics import asr_cer_vs_gold, ocr_cer_vs_gold, revision_batch_quality, summarize_job_metrics, timing_errors
from revision import CueRecord
from speech_align import align_cue_records_to_speech, propose_speech_bounds


FIXTURE = Path(__file__).parent / "fixtures" / "annotated_suite.json"


class TestAnnotatedSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_suite_ocr_cer_and_timing_ms(self) -> None:
        gold = self.data["cues"]
        # Simulate strong OCR predictions (exact ZH) with small timing drift
        pred = [
            {
                "start": float(c["start"]) + 0.08,
                "end": float(c["end"]) + 0.05,
                "text_zh": c["text_zh"],
                "text_en": c.get("text_en_gold") or "",
            }
            for c in gold
        ]
        cer = ocr_cer_vs_gold(pred, gold, match_tol_s=1.0)
        self.assertEqual(cer["matched"], len(gold))
        self.assertEqual(cer["mean_cer"], 0.0)
        timing = timing_errors(pred, gold, match_tol_s=1.0)
        self.assertIsNotNone(timing["mean_start_err_ms"])
        self.assertLess(float(timing["mean_start_err_ms"]), float(self.data["timing_tol_ms"]))

    def test_suite_ocr_ne_speech_and_typo(self) -> None:
        ne = next(c for c in self.data["cues"] if c["id"] == "ocr-ne-speech")
        cmp = compare_ocr_vs_speech(ne["text_zh"], ne["audio_zh"], asr_confidence=0.9)
        self.assertTrue(cmp.disagreement)
        zh, note = reconcile_zh(
            original_ocr=ne["original_ocr"],
            audio_zh=ne["audio_zh"],
            compare=cmp,
            asr_confidence=0.9,
        )
        self.assertEqual(zh, ne["original_ocr"])
        self.assertIn("ocr", note)

        typo = next(c for c in self.data["cues"] if c["id"] == "typo-ocr")
        cmp2 = compare_ocr_vs_speech(typo["text_zh"], typo["audio_zh"], asr_confidence=0.9)
        zh2, note2 = reconcile_zh(
            original_ocr=typo["original_ocr"],
            audio_zh=typo["audio_zh"],
            compare=cmp2,
            asr_confidence=0.9,
        )
        self.assertEqual(note2, "adopt-audio-ocr-typo")
        self.assertEqual(zh2, typo["audio_zh"])

    def test_suite_revision_and_asr_cer(self) -> None:
        items = []
        for c in self.data["cues"]:
            if c.get("text_en_provisional") and c.get("text_en_gold"):
                items.append(
                    {
                        "before_en": c["text_en_provisional"],
                        "after_en": c["text_en_gold"],
                        "gold_en": c["text_en_gold"],
                    }
                )
        batch = revision_batch_quality(items)
        self.assertGreaterEqual(batch["n"], 1)
        self.assertEqual(batch["improved_rate"], 1.0)

        asr = asr_cer_vs_gold(self.data["asr_gold"], self.data["asr_gold"])
        self.assertEqual(asr["mean_cer"], 0.0)

    def test_word_timestamp_align_and_asr_rtf(self) -> None:
        rec = CueRecord.new(
            text_zh="走吧",
            start=2.7,
            end=4.2,
            text_en="Let's go",
            ocr_confidence=0.9,
            timing_confidence=0.7,
        )
        seg = AudioSegment(
            3.0,
            3.8,
            "走吧",
            0.9,
            words=[AudioWord(3.05, 3.75, "走吧", 0.95)],
        )
        prop = propose_speech_bounds(
            ocr_zh="走吧",
            cue_start=rec.start,
            cue_end=rec.end,
            segments=[seg],
        )
        self.assertIsNotNone(prop)
        assert prop is not None
        self.assertEqual(prop["method"], "speech-align-words")
        self.assertAlmostEqual(prop["start"], 3.05, places=2)

        summary = align_cue_records_to_speech([rec], [seg])
        self.assertEqual(summary["aligned"], 1)

        rtf = asr_rtf_report(audio_duration_s=10.0, processing_s=4.0)
        self.assertEqual(rtf["rtf"], 0.4)
        self.assertTrue(rtf["faster_than_realtime"])

        metrics = summarize_job_metrics(
            pred_cues=[
                {
                    "start": 0.5,
                    "end": 2.0,
                    "text_zh": "修仙不如吃饭",
                    "audio_transcript": "修仙不如吃饭",
                    "audio_disagreement": False,
                }
            ],
            gold_cues=[self.data["cues"][0]],
            processing_s=5.0,
            video_duration_s=12.0,
            asr_hyp=self.data["asr_gold"][:1],
            asr_gold=self.data["asr_gold"][:1],
            asr_processing_s=3.0,
            asr_audio_duration_s=12.0,
        )
        self.assertIn("asr_rtf", metrics)
        self.assertIn("multimodal", metrics)
        self.assertLess(float(metrics["asr_rtf"]), 1.0)


if __name__ == "__main__":
    unittest.main()
