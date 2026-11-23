"""App-local data paths — glossary/jobs must not write beside videos."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from glossary import load_glossary, series_id_from_video
from job_store import list_recoverable_jobs, save_job_snapshot
from paths import app_data_dir, glossary_dir, jobs_dir


class _FakeJob:
    def __init__(self, video: Path) -> None:
        self.id = "job-test-1"
        self.video_path = str(video)
        self.srt_path = str(video.with_suffix(".en.srt"))
        self.status = "running"
        self.progress = 0.1
        self.cue_count = 0
        self.duration = 10.0
        self.current_time = 1.0
        self.playhead = 0.0
        self.series_id = series_id_from_video(video)
        self.region = None
        self.engine_id = "argos"
        self.paused = False
        self.batch_id = None
        self.cues: list = []
        self.error = None


class TestAppDataPaths(unittest.TestCase):
    def test_glossary_writes_under_data(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            video_dir = Path(td) / "My Series"
            video_dir.mkdir()
            video = video_dir / "ep01.mp4"
            video.write_bytes(b"0")
            g = load_glossary(video)
            g.remember("李逍遥", "Li Xiaoyao", persist=True)
            self.assertTrue(str(g.path).startswith(str(glossary_dir())))
            self.assertFalse((video_dir / ".subparty").exists())
            self.assertTrue(g.path.exists())
            data = json.loads(g.path.read_text(encoding="utf-8"))
            self.assertEqual(data["terms"]["李逍遥"], "Li Xiaoyao")

    def test_migrate_legacy_glossary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            video_dir = Path(td) / "Cultivation"
            legacy = video_dir / ".subparty"
            legacy.mkdir(parents=True)
            sid = series_id_from_video(video_dir / "ep.mp4")
            (legacy / f"{sid}.json").write_text(
                json.dumps({"series_id": sid, "terms": {"修仙": "Cultivation"}}),
                encoding="utf-8",
            )
            video = video_dir / "ep.mp4"
            video.write_bytes(b"0")
            dest = glossary_dir() / f"{sid}.json"
            if dest.exists():
                dest.unlink()
            g = load_glossary(video)
            self.assertEqual(g.terms.get("修仙"), "Cultivation")
            self.assertTrue(dest.exists())

    def test_job_snapshot_under_data(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            video = Path(td) / "clip.mp4"
            video.write_bytes(b"0")
            job = _FakeJob(video)
            path = save_job_snapshot(job)
            self.assertEqual(path.parent, jobs_dir())
            self.assertFalse((video.parent / ".subparty").exists())
            found = list_recoverable_jobs()
            self.assertTrue(any(j.get("id") == "job-test-1" for j in found))

    def test_app_data_dir_exists(self) -> None:
        d = app_data_dir()
        self.assertTrue(d.exists())
        self.assertEqual(d.name, "data")


if __name__ == "__main__":
    unittest.main()
