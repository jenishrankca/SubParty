"""Stage 7 usability: setup checklist + region confirm API hooks."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from models_mgr import setup_checklist
from region import (
    SubtitleRegion,
    clear_confirmed_region,
    confirm_region,
    get_confirmed_region,
)


class TestSetupChecklist(unittest.TestCase):
    def test_checklist_shape(self) -> None:
        s = setup_checklist()
        self.assertIn("ready", s)
        self.assertIn("first_run", s)
        self.assertIn("steps", s)
        self.assertIn("live_vs_quality", s)
        self.assertIn("live", s["live_vs_quality"])
        self.assertIn("quality", s["live_vs_quality"])
        self.assertTrue(any(st["id"] == "ffmpeg" for st in s["steps"]))
        self.assertTrue(any(st.get("required") for st in s["steps"]))


class TestRegionConfirmHooks(unittest.TestCase):
    def test_confirm_and_clear(self) -> None:
        clear_confirmed_region()
        td = Path(tempfile.mkdtemp(prefix="reg_"))
        clip = td / "clip.mp4"
        clip.write_bytes(b"fake")
        r = SubtitleRegion(x=0.1, y=0.7, w=0.8, h=0.2)
        conf = confirm_region(clip, r, evidence={"test": True})
        self.assertTrue(conf.confirmed)
        got = get_confirmed_region(clip)
        self.assertIsNotNone(got)
        assert got is not None
        self.assertAlmostEqual(got.region.y, 0.7, places=3)
        clear_confirmed_region(clip)
        self.assertIsNone(get_confirmed_region(clip))


if __name__ == "__main__":
    unittest.main()
