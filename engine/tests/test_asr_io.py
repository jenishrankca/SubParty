"""ASR I/O + faster-whisper ndarray path (no PyAV metadata_errors)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from asr_io import load_wav_mono_f32
from ffmpeg_util import extract_audio_wav


SAMPLES = Path(__file__).resolve().parents[2] / "samples"


class TestAsrIo(unittest.TestCase):
    def test_load_wav_mono_f32(self) -> None:
        clip = SAMPLES / "demo_zh_hardsub.mp4"
        if not clip.exists():
            self.skipTest("missing demo")
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "a.wav"
            extract_audio_wav(clip, wav, duration_s=1.5)
            audio, sr = load_wav_mono_f32(wav)
            self.assertEqual(sr, 16000)
            self.assertEqual(audio.ndim, 1)
            self.assertGreater(len(audio), 1000)

    def test_faster_whisper_ndarray_transcribe(self) -> None:
        from audio_verify import whisper_available, transcribe_zh

        if not whisper_available():
            self.skipTest("faster-whisper missing")
        clip = SAMPLES / "demo_zh_hardsub.mp4"
        if not clip.exists():
            self.skipTest("missing demo")
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "a.wav"
            extract_audio_wav(clip, wav, duration_s=2.0)
            # Must not raise TypeError metadata_errors
            segs = transcribe_zh(wav, word_timestamps=False)
            self.assertIsInstance(segs, list)


if __name__ == "__main__":
    unittest.main()
