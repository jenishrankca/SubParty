"""Cancel / remove job discards recovery snapshots."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from job_store import delete_snapshot
from paths import jobs_dir


class TestJobCancelDiscard(unittest.TestCase):
    def test_delete_snapshot_removes_file(self) -> None:
        jdir = jobs_dir()
        jid = "cancel-test-job"
        path = jdir / f"{jid}.json"
        path.write_text(
            json.dumps({"id": jid, "status": "paused", "video_path": "x.mp4"}),
            encoding="utf-8",
        )
        self.assertTrue(path.exists())
        delete_snapshot("x.mp4", jid)
        self.assertFalse(path.exists())

    def test_cancel_discards_snapshot(self) -> None:
        from pipeline import JobManager, JobState

        mgr = JobManager()
        with tempfile.TemporaryDirectory() as td:
            video = Path(td) / "clip.mp4"
            video.write_bytes(b"0")
            job = JobState(id="job-discard-1", video_path=str(video), srt_path=str(video) + ".en.srt")
            job.status = "paused"
            mgr._jobs[job.id] = job
            snap = jobs_dir() / f"{job.id}.json"
            snap.write_text(json.dumps({"id": job.id, "status": "paused"}), encoding="utf-8")
            self.assertTrue(mgr.cancel(job.id, discard=True))
            self.assertTrue(job.discard_on_cancel)
            self.assertFalse(snap.exists())

    def test_list_skips_cancelled(self) -> None:
        from job_store import list_recoverable_jobs

        jdir = jobs_dir()
        path = jdir / "cancelled-only.json"
        path.write_text(
            json.dumps({"id": "cancelled-only", "status": "cancelled", "video_path": "a.mp4"}),
            encoding="utf-8",
        )
        try:
            found = list_recoverable_jobs()
            self.assertFalse(any(j.get("id") == "cancelled-only" for j in found))
        finally:
            if path.exists():
                path.unlink()


if __name__ == "__main__":
    unittest.main()
