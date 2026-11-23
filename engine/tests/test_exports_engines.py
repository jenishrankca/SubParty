"""Exports + engine registry smoke tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engines import list_engines, get_engine
from exports import export_ass, export_srt, export_vtt
from names import extract_zh_name_candidates, lock_names_in_en


class TestExportsEngines(unittest.TestCase):
    def test_engines_include_argos(self) -> None:
        ids = {e.id for e in list_engines()}
        self.assertIn("argos", ids)
        self.assertIn("nllb", ids)
        self.assertIn("qwen", ids)
        eng = get_engine("argos")
        self.assertTrue(eng.available())

    def test_exports_roundtrip(self) -> None:
        cues = [
            {"index": 1, "start": 1.0, "end": 2.5, "text_zh": "你好", "text_en": "Hello"},
            {"index": 2, "start": 3.0, "end": 4.0, "text_zh": "走吧", "text_en": "Let's go"},
        ]
        td = Path(tempfile.mkdtemp())
        srt = export_srt(cues, td / "a.en.srt", bilingual=False)
        bi = export_srt(cues, td / "a.zh-en.srt", bilingual=True)
        vtt = export_vtt(cues, td / "a.en.vtt")
        ass = export_ass(cues, td / "a.en.ass", bilingual=True)
        self.assertTrue(srt.exists())
        self.assertIn("Hello", srt.read_text(encoding="utf-8"))
        self.assertIn("你好", bi.read_text(encoding="utf-8"))
        self.assertTrue(vtt.read_text(encoding="utf-8").startswith("WEBVTT"))
        self.assertIn("Dialogue:", ass.read_text(encoding="utf-8"))

    def test_name_helpers(self) -> None:
        self.assertIn("李逍遥", extract_zh_name_candidates("李逍遥来了"))
        locked = lock_names_in_en(
            "li xiaoyao arrived",
            preferred={"李逍遥": "Li Xiaoyao"},
            prior_en_names=["Li Xiaoyao"],
        )
        self.assertIn("Li Xiaoyao", locked)


if __name__ == "__main__":
    unittest.main()
