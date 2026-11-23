"""Repeatable end-to-end video suite (synth + sample clips)."""

from __future__ import annotations

import re
import tempfile
import time
import unittest
from pathlib import Path

from pipeline import jobs
from srt_writer import format_ts
from tests.synth_hardsub import make_hardsub_video

_TS = re.compile(
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})"
)


def _parse_ts(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _srt_ranges(path: Path) -> list[tuple[float, float]]:
    text = path.read_text(encoding="utf-8")
    out: list[tuple[float, float]] = []
    for m in _TS.finditer(text):
        out.append((_parse_ts(m.group(1)), _parse_ts(m.group(2))))
    return out


def _assert_no_srt_overlaps(path: Path) -> None:
    ranges = _srt_ranges(path)
    for i in range(1, len(ranges)):
        prev_end = ranges[i - 1][1]
        cur_start = ranges[i][0]
        if prev_end > cur_start + 0.001:
            raise AssertionError(
                f"SRT overlap: {format_ts(ranges[i-1][0])}-->{format_ts(prev_end)} "
                f"vs {format_ts(cur_start)}-->{format_ts(ranges[i][1])}"
            )


def _wait_job(job_id: str, timeout_s: float = 120.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        job = jobs.get(job_id)
        if not job:
            raise AssertionError("job disappeared")
        if job.status in {"done", "error", "cancelled"}:
            return job
        time.sleep(0.25)
    raise TimeoutError(f"job {job_id} did not finish in {timeout_s}s")


class TestVideoSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="video_suite_"))
        cls.synth = cls.tmp / "bottom_720.mp4"
        make_hardsub_video(
            cls.synth,
            width=1280,
            height=720,
            text_y_ratio=0.88,
            text="修仙不如吃饭",
            seconds=3.0,
            fps=2,
        )

    def test_e2e_synth_produces_non_overlapping_srt(self) -> None:
        job = jobs.create(str(self.synth), fps=1.0, lookahead=6.0, playhead=0.0)
        finished = _wait_job(job.id, timeout_s=180.0)
        self.assertEqual(finished.status, "done", finished.error)
        self.assertGreaterEqual(finished.cue_count, 1)
        srt = Path(finished.srt_path)
        self.assertTrue(srt.exists())
        self.assertGreater(srt.stat().st_size, 0)
        _assert_no_srt_overlaps(srt)
        # Confidence / uncertain fields present on cues
        for c in finished.cues:
            self.assertIn("confidence", c)
            self.assertIn("uncertain", c)
            self.assertGreaterEqual(float(c["confidence"]), 0.0)
            self.assertLessEqual(float(c["confidence"]), 1.0)

    def test_sample_demo_zh_if_present(self) -> None:
        demo = Path(__file__).resolve().parents[2] / "samples" / "demo_zh_hardsub.mp4"
        if not demo.exists():
            self.skipTest("samples/demo_zh_hardsub.mp4 missing")
        job = jobs.create(str(demo), fps=1.0, lookahead=8.0, playhead=0.0)
        finished = _wait_job(job.id, timeout_s=240.0)
        self.assertIn(finished.status, {"done", "error"})
        if finished.status == "error":
            self.fail(finished.error or "job error")
        self.assertGreaterEqual(finished.cue_count, 1)
        _assert_no_srt_overlaps(Path(finished.srt_path))
        # Region should have been detected
        self.assertTrue(finished.region)
        self.assertIn("y", finished.region)


if __name__ == "__main__":
    unittest.main()
