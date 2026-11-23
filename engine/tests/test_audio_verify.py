"""Phase B audio verification — compare + reconcile (no Whisper load in unit tests)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audio_verify import (
    AudioSegment,
    audio_for_window,
    char_similarity,
    compare_ocr_vs_speech,
    normalize_zh,
    reconcile_zh,
    verify_cue_records,
)
from ffmpeg_util import extract_audio_wav
from revision import CueRecord


class TestAudioCompare(unittest.TestCase):
    def test_normalize_and_similarity(self) -> None:
        self.assertEqual(normalize_zh("李 逍遥！"), "李逍遥")
        self.assertGreater(char_similarity("李逍遥来了", "李逍遥来了"), 0.9)
        self.assertLess(char_similarity("他来了", "李逍遥进门了"), 0.55)

    def test_disagreement_flags(self) -> None:
        ok = compare_ocr_vs_speech("我们走吧", "我们走吧", asr_confidence=0.8)
        self.assertFalse(ok.disagreement)
        bad = compare_ocr_vs_speech("修仙不如吃饭", "今天天气不错", asr_confidence=0.9)
        self.assertTrue(bad.disagreement)

    def test_reconcile_never_blind_overwrite(self) -> None:
        cmp = compare_ocr_vs_speech("硬字幕", "语音不同", asr_confidence=0.9)
        zh, note = reconcile_zh(
            original_ocr="硬字幕",
            audio_zh="语音不同",
            compare=cmp,
            asr_confidence=0.9,
        )
        self.assertEqual(zh, "硬字幕")
        self.assertIn("ocr", note)

    def test_audio_for_window(self) -> None:
        segs = [
            AudioSegment(0.0, 1.0, "你好", 0.8),
            AudioSegment(1.2, 2.0, "世界", 0.7),
        ]
        text, conf = audio_for_window(segs, 0.5, 1.5)
        self.assertIn("你好", text)
        self.assertIn("世界", text)
        self.assertGreater(conf, 0.5)

    def test_verify_cue_records_mocked(self) -> None:
        rec = CueRecord.new(
            text_zh="他来了",
            start=1.0,
            end=2.0,
            text_en="He came",
            ocr_confidence=0.9,
            timing_confidence=0.85,
        )
        segs = [AudioSegment(0.8, 2.2, "李逍遥来了", 0.85)]
        with patch("audio_verify.extract_and_transcribe", return_value=segs):
            summary = verify_cue_records([rec], video_path="dummy.mp4", segments=segs)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["disagreements"], 1)
        self.assertEqual(rec.original_ocr, "他来了")
        self.assertEqual(rec.audio_transcript, "李逍遥来了")
        self.assertTrue(rec.audio_disagreement)
        # OCR source text_zh unchanged
        self.assertEqual(rec.text_zh, "他来了")

    def test_ffmpeg_extract_from_synth(self) -> None:
        import subprocess

        from ffmpeg_util import ffmpeg_path

        td = Path(tempfile.mkdtemp(prefix="aud_"))
        silent = td / "silent.mp4"
        # Mux color frames + silent mono so extract has an audio stream
        cmd = [
            str(ffmpeg_path()),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x180:d=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            "-y",
            str(silent),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        wav = td / "a.wav"
        extract_audio_wav(silent, wav)
        self.assertTrue(wav.exists())
        self.assertGreater(wav.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
