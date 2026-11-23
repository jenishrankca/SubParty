"""Unit tests for overlap-safe SRT cue replacement."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from srt_writer import Cue, SrtWriter, cues_conflict, merge_cue_payloads


class TestSrtReplacement(unittest.TestCase):
    def _writer(self) -> SrtWriter:
        td = Path(tempfile.mkdtemp())
        return SrtWriter(td / "out.en.srt")

    def test_extend_same_start_replaces(self) -> None:
        w = self._writer()
        w.upsert_and_rewrite(Cue(1, 10.0, 11.0, "你好", "Hello"))
        w.upsert_and_rewrite(Cue(2, 10.0, 14.0, "你好", "Hello"))
        cues = w.cues
        self.assertEqual(len(cues), 1)
        self.assertAlmostEqual(cues[0].start, 10.0)
        self.assertAlmostEqual(cues[0].end, 14.0)

    def test_overlap_different_text_incoming_wins(self) -> None:
        w = self._writer()
        w.upsert_and_rewrite(Cue(1, 10.0, 12.5, "旧句", "Old"))
        w.upsert_and_rewrite(Cue(2, 11.0, 13.0, "新句", "New"))
        cues = w.cues
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text_en, "New")

    def test_non_overlapping_kept(self) -> None:
        w = self._writer()
        w.upsert_and_rewrite(Cue(1, 1.0, 2.0, "一", "One"))
        w.upsert_and_rewrite(Cue(2, 3.0, 4.0, "二", "Two"))
        self.assertEqual(len(w.cues), 2)

    def test_same_zh_near_start_merges(self) -> None:
        w = self._writer()
        w.upsert_and_rewrite(Cue(1, 10.0, 11.5, "走吧", "Let's go"))
        w.upsert_and_rewrite(Cue(2, 10.4, 12.0, "走吧", "Let's go"))
        cues = w.cues
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text_zh, "走吧")

    def test_srt_file_no_overlap_timestamps(self) -> None:
        w = self._writer()
        w.upsert_and_rewrite(Cue(1, 5.0, 7.0, "A", "A"))
        w.upsert_and_rewrite(Cue(2, 6.5, 8.0, "B", "B"))
        text = w.path.read_text(encoding="utf-8")
        # Only one block should remain (incoming wins)
        self.assertEqual(text.count("-->"), 1)
        self.assertIn("B", text)

    def test_merge_cue_payloads_clamps(self) -> None:
        existing = [
            {"index": 1, "start": 1.0, "end": 3.0, "text_zh": "甲", "text_en": "A"},
            {"index": 2, "start": 5.0, "end": 6.0, "text_zh": "乙", "text_en": "B"},
        ]
        incoming = {"index": 9, "start": 2.5, "end": 4.0, "text_zh": "丙", "text_en": "C"}
        merged = merge_cue_payloads(existing, incoming)
        # First overlaps → dropped; second kept; incoming added
        ens = [c["text_en"] for c in merged]
        self.assertEqual(ens, ["C", "B"])

    def test_cues_conflict_helpers(self) -> None:
        a = Cue(1, 10.0, 12.0, "x", "X")
        b = Cue(2, 10.05, 14.0, "x", "X")
        self.assertTrue(cues_conflict(a, b))
        c = Cue(3, 20.0, 21.0, "y", "Y")
        self.assertFalse(cues_conflict(a, c))


if __name__ == "__main__":
    unittest.main()
