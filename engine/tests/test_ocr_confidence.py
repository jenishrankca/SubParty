"""OCR confidence wiring tests (no video required for filter helper)."""

from __future__ import annotations

import unittest

from text_filters import UNCERTAIN_CONFIDENCE, FilteredOcr, OcrLine, filter_ocr_result


class TestOcrConfidence(unittest.TestCase):
    def test_min_confidence_and_uncertain(self) -> None:
        lines = [
            OcrLine(text="你好世界", cx=0.5, cy=0.8, w=0.4, h=0.1, confidence=0.95),
            OcrLine(text="走吧", cx=0.5, cy=0.85, w=0.2, h=0.08, confidence=0.61),
        ]
        result = filter_ocr_result(lines)
        self.assertIn("你好世界", result.text)
        self.assertIn("走吧", result.text)
        self.assertAlmostEqual(result.confidence, 0.61)
        self.assertTrue(result.uncertain)
        self.assertLess(result.confidence, UNCERTAIN_CONFIDENCE)

    def test_high_confidence_not_uncertain(self) -> None:
        result = filter_ocr_result(
            [OcrLine(text="修仙不如吃饭", cx=0.5, cy=0.88, w=0.5, h=0.1, confidence=0.99)]
        )
        self.assertFalse(result.uncertain)
        self.assertGreaterEqual(result.confidence, UNCERTAIN_CONFIDENCE)

    def test_filtered_ocr_empty(self) -> None:
        empty = FilteredOcr(text="", confidence=0.0)
        self.assertFalse(empty.uncertain)


if __name__ == "__main__":
    unittest.main()
