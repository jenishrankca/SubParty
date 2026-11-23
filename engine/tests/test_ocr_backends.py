"""OCR backend selection + confirmable region hooks."""

from __future__ import annotations

import unittest
from pathlib import Path

from ocr import get_ocr_backend, list_ocr_backends, ocr_uncertain_deepseek, set_ocr_backend
from region import (
    ConfirmableRegion,
    SubtitleRegion,
    clear_confirmed_region,
    confirm_region,
    get_confirmed_region,
)


SAMPLES = Path(__file__).resolve().parents[2] / "samples"


class TestOcrBackends(unittest.TestCase):
    def tearDown(self) -> None:
        set_ocr_backend(None)

    def test_default_is_ppocrv6_small(self) -> None:
        set_ocr_backend(None)
        self.assertEqual(get_ocr_backend(), "ppocrv6_small")

    def test_list_backends(self) -> None:
        ids = {b["id"] for b in list_ocr_backends()}
        self.assertIn("ppocrv6_small", ids)
        self.assertIn("rapidocr", ids)
        self.assertIn("ppocrv5_mobile", ids)

    def test_deepseek_stub_noop(self) -> None:
        self.assertIsNone(ocr_uncertain_deepseek("x.png", primary=[]))


class TestConfirmableRegion(unittest.TestCase):
    def tearDown(self) -> None:
        clear_confirmed_region()

    def test_confirm_and_get(self) -> None:
        clip = SAMPLES / "demo_zh_hardsub.mp4"
        if not clip.exists():
            self.skipTest("no demo clip")
        r = SubtitleRegion(x=0.1, y=0.7, w=0.8, h=0.25).clamp()
        conf = confirm_region(clip, r, evidence={"test": True})
        self.assertTrue(conf.confirmed)
        got = get_confirmed_region(clip)
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got.region.x, r.x)
        self.assertIsInstance(got, ConfirmableRegion)
        clear_confirmed_region(clip)
        self.assertIsNone(get_confirmed_region(clip))


if __name__ == "__main__":
    unittest.main()
