"""Playback fix: detect broken AAC edit lists / unsupported codecs."""

from __future__ import annotations

import unittest
from pathlib import Path

from playback_fix import ensure_playable_media, probe_media

REPO = Path(__file__).resolve().parents[2]
WEIRD = Path(r"C:\Users\jenis\Downloads\the-weird-games.mp4")
SAMPLE = REPO / "samples" / "demo_zh_hardsub.mp4"
WEIRD5 = REPO / "samples" / "the-weird-games-5min.mp4"


class TestPlaybackProbe(unittest.TestCase):
    @unittest.skipUnless(SAMPLE.is_file(), "demo sample missing")
    def test_demo_needs_no_fix(self) -> None:
        p = probe_media(SAMPLE)
        self.assertFalse(p["edit_list_broken"])
        self.assertFalse(p["needs_fix"])
        self.assertTrue(p["audio"])

    @unittest.skipUnless(WEIRD5.is_file(), "weird-games-5min missing")
    def test_weird5_needs_no_fix(self) -> None:
        p = probe_media(WEIRD5)
        self.assertFalse(p["needs_fix"], msg=p.get("stderr_tail"))

    @unittest.skipUnless(WEIRD.is_file(), "full weird-games download missing")
    def test_weird_full_edit_list_and_remux(self) -> None:
        p = probe_media(WEIRD)
        self.assertTrue(p["edit_list_broken"])
        self.assertEqual(p["reason"], "mp4-aac-edit-list")
        self.assertTrue(p["needs_fix"])
        # Short path: remux is expensive for full file — only assert ensure on
        # a forced cache miss is skipped here; unit covers 20s clip via copy remux
        # of samples instead when full remux is too heavy for CI.
        fixed = ensure_playable_media(WEIRD)
        self.assertTrue(fixed["remuxed"])
        self.assertTrue(Path(fixed["play_path"]).is_file())
        post = probe_media(fixed["play_path"])
        self.assertFalse(post["edit_list_broken"])
        self.assertFalse(post["needs_fix"])
        self.assertTrue(post["audio"])
        self.assertEqual(str(post["audio"][0].get("codec_name")), "aac")


if __name__ == "__main__":
    unittest.main()
