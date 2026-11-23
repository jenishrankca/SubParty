"""High polish — split/merge, QA gold+HTML, characters, durable batch."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from batch_store import create_batch_manifest, failed_job_rows, load_batch
from characters import Character, load_character_sheet, sync_glossary_into_sheet
from cue_ops import merge_cues, split_cue
from qa_report import build_qa_report, export_qa_html, load_gold_cues


class TestCueOps(unittest.TestCase):
    def test_split_and_merge(self) -> None:
        cues = [
            {
                "index": 1,
                "cue_id": "a",
                "start": 1.0,
                "end": 3.0,
                "text_zh": "你好世界",
                "text_en": "Hello world",
                "original_ocr": "你好世界",
            }
        ]
        split = split_cue(cues, cue_id="a", at=2.0, left_zh="你好", right_zh="世界")
        self.assertEqual(len(split), 2)
        self.assertAlmostEqual(split[0]["end"], 2.0)
        self.assertEqual(split[0]["text_zh"], "你好")
        self.assertEqual(split[0]["original_ocr"], "你好世界")
        merged = merge_cues(split, start_a=1.0, start_b=2.0)
        self.assertEqual(len(merged), 1)
        self.assertAlmostEqual(merged[0]["start"], 1.0)
        self.assertAlmostEqual(merged[0]["end"], 3.0)


class TestQaGoldExport(unittest.TestCase):
    def test_gold_path_and_html(self) -> None:
        td = Path(tempfile.mkdtemp(prefix="qa_"))
        gold = td / "gold.json"
        gold.write_text(
            json.dumps({"cues": [{"start": 1.0, "end": 2.0, "text_zh": "他来了", "text_en": "He came"}]}),
            encoding="utf-8",
        )
        cues = load_gold_cues(gold_path=gold)
        self.assertEqual(len(cues or []), 1)
        job = MagicMock()
        job.id = "job-qa"
        job.status = "done"
        job.series_id = "demo"
        job.duration = 5.0
        job.processing_s = 1.0
        job.live_lag = 0.1
        job.audio_summary = {}
        job.cue_records = []
        job.cues = [{"index": 1, "start": 1.05, "end": 2.0, "text_zh": "他来了", "text_en": "He came"}]
        report = build_qa_report(job=job, gold_path=gold)
        self.assertEqual(report["gold_source"], str(gold))
        path = export_qa_html(report, job_id="job-qa")
        self.assertTrue(path.exists())
        self.assertTrue(path.with_suffix(".json").exists())
        self.assertIn("SubParty QA", path.read_text(encoding="utf-8"))


class TestCharacters(unittest.TestCase):
    def test_sheet_upsert_and_sync(self) -> None:
        sid = "test_series_chars"
        sheet = load_character_sheet(sid)
        sheet.upsert(Character(zh="李逍遥", en="Li Xiaoyao", role="lead", notes="protagonist"))
        again = load_character_sheet(sid)
        self.assertTrue(any(c.zh == "李逍遥" and c.en == "Li Xiaoyao" for c in again.characters))
        synced = sync_glossary_into_sheet(sid, {"赵灵儿": "Zhao Linger", "长长的术语不要": "skip"})
        self.assertTrue(any(c.zh == "赵灵儿" for c in synced.characters))
        self.assertFalse(any(c.zh == "长长的术语不要" for c in synced.characters))


class TestDurableBatch(unittest.TestCase):
    def test_manifest_and_failed(self) -> None:
        rows = [
            {"id": "1", "video_path": "a.mp4", "status": "done"},
            {"id": "2", "video_path": "b.mp4", "status": "error", "error": "boom"},
        ]
        m = create_batch_manifest(
            video_paths=["a.mp4", "b.mp4"],
            engine_id="argos",
            fps=1.0,
            lookahead=12.0,
            job_rows=rows,
        )
        bid = m["batch_id"]
        loaded = load_batch(bid)
        self.assertIsNotNone(loaded)
        failed = failed_job_rows(bid)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["id"], "2")


if __name__ == "__main__":
    unittest.main()
