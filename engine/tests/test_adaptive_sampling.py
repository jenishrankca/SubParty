"""Unit tests for playhead-first adaptive tick picker."""

from __future__ import annotations

import unittest

from pipeline import _pick_next_tick


class TestPickNextTick(unittest.TestCase):
    def test_lagging_snaps_to_playhead(self) -> None:
        done = set(range(0, 40))  # 0..19.5s covered at 0.5 tick
        tick = _pick_next_tick(
            done=done,
            n_ticks=200,
            base_tick=0.5,
            playhead=30.0,
            lookahead=6.0,
            stride_ticks=2,
            lagging=True,
        )
        self.assertEqual(tick, 60)  # 30/0.5

    def test_near_window_fills_dense_holes(self) -> None:
        done = {60, 62, 64}  # holes at 61, 63 near playhead
        tick = _pick_next_tick(
            done=done,
            n_ticks=200,
            base_tick=0.5,
            playhead=30.0,
            lookahead=8.0,
            stride_ticks=2,
            lagging=False,
            near_window_s=1.2,
        )
        self.assertEqual(tick, 61)

    def test_returns_none_when_complete(self) -> None:
        done = set(range(10))
        tick = _pick_next_tick(
            done=done,
            n_ticks=10,
            base_tick=0.5,
            playhead=1.0,
            lookahead=4.0,
            stride_ticks=1,
            lagging=False,
        )
        self.assertIsNone(tick)


if __name__ == "__main__":
    unittest.main()
