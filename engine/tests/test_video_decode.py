"""PyAV persistent decode vs FFmpeg fallback."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_decode import (
    close_all_decoders,
    get_decode_backend,
    pyav_available,
    set_decode_backend,
)


SAMPLES = Path(__file__).resolve().parents[2] / "samples"


class TestVideoDecode(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.clip = SAMPLES / "demo_zh_hardsub.mp4"
        if not cls.clip.exists():
            raise unittest.SkipTest(f"missing {cls.clip}")

    def tearDown(self) -> None:
        close_all_decoders()
        set_decode_backend(None)

    def test_pyav_available(self) -> None:
        self.assertTrue(pyav_available())

    def test_extract_region_pyav_writes_png(self) -> None:
        set_decode_backend("pyav")
        from ffmpeg_util import extract_region, probe_size

        w, h = probe_size(self.clip)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "r.png"
            extract_region(
                self.clip,
                1.0,
                out,
                x_ratio=0.06,
                y_ratio=0.8,
                w_ratio=0.88,
                h_ratio=0.2,
                width=w,
                height=h,
                scale_width=320,
            )
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 200)

    def test_ffmpeg_fallback_backend(self) -> None:
        set_decode_backend("ffmpeg")
        self.assertEqual(get_decode_backend(), "ffmpeg")
        from ffmpeg_util import extract_region, probe_size

        w, h = probe_size(self.clip)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "f.png"
            extract_region(
                self.clip,
                1.0,
                out,
                x_ratio=0.06,
                y_ratio=0.8,
                w_ratio=0.88,
                h_ratio=0.2,
                width=w,
                height=h,
                scale_width=320,
            )
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 200)

    def test_persistent_reuse_same_path(self) -> None:
        from video_decode import get_decoder

        set_decode_backend("pyav")
        a = get_decoder(self.clip)
        b = get_decoder(self.clip)
        self.assertIs(a, b)


if __name__ == "__main__":
    unittest.main()
