"""Live buffer policy: densify when thin; don't always-lag after priming."""

from __future__ import annotations

import unittest

from pipeline import _DEFAULT_LOOKAHEAD_LIVE, _LIVE_BUFFER_REFILL_S, _LIVE_BUFFER_TARGET_S, _pick_next_tick
from scheduler import PerformanceScheduler


class TestLiveBufferPolicy(unittest.TestCase):
    def test_targets(self) -> None:
        self.assertGreaterEqual(_DEFAULT_LOOKAHEAD_LIVE, 10.0)
        self.assertGreaterEqual(_LIVE_BUFFER_TARGET_S, 4.0)
        self.assertLess(_LIVE_BUFFER_REFILL_S, _LIVE_BUFFER_TARGET_S)

    def test_thin_buffer_is_catch_up(self) -> None:
        perf = PerformanceScheduler(live_mode=True, live_lag=0.2, buffer_ahead=1.0)
        self.assertEqual(perf.mode(), "catch_up")
        lo, hi = perf.ocr_interval_bounds()
        self.assertLessEqual(hi, 1.8)

    def test_pick_builds_ahead_when_not_lagging(self) -> None:
        # Playhead at tick 10; ticks 10..14 done; should walk forward into lookahead
        done = set(range(10, 15))
        tick = _pick_next_tick(
            done=done,
            n_ticks=100,
            base_tick=1.0,
            playhead=10.0,
            lookahead=12.0,
            stride_ticks=1,
            lagging=False,
            near_window_s=2.0,
        )
        self.assertIsNotNone(tick)
        assert tick is not None
        self.assertGreaterEqual(tick, 15)
        self.assertLessEqual(tick, 22)

    def test_pick_snaps_when_truly_lagging(self) -> None:
        done = set(range(0, 5))  # behind playhead
        tick = _pick_next_tick(
            done=done,
            n_ticks=100,
            base_tick=1.0,
            playhead=20.0,
            lookahead=8.0,
            stride_ticks=2,
            lagging=True,
            near_window_s=0.5,
        )
        self.assertEqual(tick, 20)


if __name__ == "__main__":
    unittest.main()
