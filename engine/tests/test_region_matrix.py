"""Subtitle region detection matrix: positions × resolutions."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from region import detect_subtitle_region
from tests.synth_hardsub import make_hardsub_video

# (label, text_y_ratio center, expected y band must overlap this range)
_POSITIONS = (
    ("bottom", 0.88, (0.70, 1.00)),
    ("middle", 0.50, (0.35, 0.65)),
    ("top", 0.12, (0.00, 0.30)),
)

_RESOLUTIONS = (
    ("480p", 854, 480),
    ("720p", 1280, 720),
    ("1080p", 1920, 1080),
)


class TestRegionMatrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = Path(tempfile.mkdtemp(prefix="region_matrix_"))
        cls.cases: list[tuple[str, Path, tuple[float, float]]] = []
        for pos_name, y_ratio, expected in _POSITIONS:
            for res_name, w, h in _RESOLUTIONS:
                name = f"{pos_name}_{res_name}.mp4"
                path = cls._tmpdir / name
                make_hardsub_video(path, width=w, height=h, text_y_ratio=y_ratio, seconds=2.0, fps=2)
                cls.cases.append((f"{pos_name}/{res_name}", path, expected))

    def test_matrix_detects_expected_band(self) -> None:
        failures: list[str] = []
        for label, path, (y_lo, y_hi) in self.cases:
            region = detect_subtitle_region(
                path,
                sample_times=[0.3, 0.8, 1.2, 1.6],
                duration=2.0,
            )
            mid = region.y + region.h / 2
            band_lo, band_hi = region.y, region.y + region.h
            overlaps = band_hi >= y_lo and band_lo <= y_hi
            # Also require center of detected band near expected center zone
            center_ok = y_lo <= mid <= y_hi
            if not (overlaps and center_ok):
                failures.append(
                    f"{label}: got y={region.y:.2f}-{band_hi:.2f} mid={mid:.2f}; "
                    f"expected mid in {y_lo:.2f}-{y_hi:.2f}"
                )
        if failures:
            self.fail("Region matrix failures:\n  " + "\n  ".join(failures))

    def test_demo_hardsub_bottomish(self) -> None:
        demo = Path(__file__).resolve().parents[2] / "samples" / "demo_zh_hardsub.mp4"
        if not demo.exists():
            self.skipTest("samples/demo_zh_hardsub.mp4 missing")
        region = detect_subtitle_region(demo, sample_times=[0.5, 1.5, 2.5, 3.5], duration=8.0)
        mid = region.y + region.h / 2
        self.assertGreaterEqual(mid, 0.55, f"demo mid={mid:.2f} expected lower-half")


if __name__ == "__main__":
    unittest.main()
