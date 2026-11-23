"""Live-mode scheduler: coarser catch-up, deferred refine."""

from __future__ import annotations

import unittest

from scheduler import PerformanceScheduler


class TestLiveScheduler(unittest.TestCase):
    def test_live_catch_up_coarser(self) -> None:
        perf = PerformanceScheduler(live_mode=True, live_lag=2.0, buffer_ahead=0.0)
        self.assertEqual(perf.mode(), "catch_up")
        lo, hi = perf.ocr_interval_bounds()
        self.assertGreaterEqual(lo, 1.0)
        self.assertGreaterEqual(hi, 3.0)
        self.assertFalse(perf.should_run_refine())
        self.assertFalse(perf.should_dense_transitions())

    def test_live_refine_deferred_until_buffer(self) -> None:
        perf = PerformanceScheduler(live_mode=True, live_lag=0.1, buffer_ahead=2.0)
        self.assertNotEqual(perf.mode(), "refine")
        self.assertFalse(perf.should_run_refine())
        perf.buffer_ahead = 5.0
        self.assertEqual(perf.mode(), "refine")
        self.assertTrue(perf.should_run_refine())

    def test_quality_still_refines_balanced(self) -> None:
        perf = PerformanceScheduler(live_mode=False, live_lag=0.5, buffer_ahead=1.0)
        self.assertEqual(perf.mode(), "balanced")
        self.assertTrue(perf.should_run_refine())


if __name__ == "__main__":
    unittest.main()
