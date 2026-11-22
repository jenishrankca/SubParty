"""ffmpeg / ffprobe helpers for SubParty."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"


def ffmpeg_path() -> Path:
    bundled = BIN / "ffmpeg.exe"
    if bundled.exists():
        return bundled
    which = _which("ffmpeg")
    if which:
        return Path(which)
    raise FileNotFoundError(
        "ffmpeg not found. Place ffmpeg.exe in bin/ or add ffmpeg to PATH."
    )


def ffprobe_path() -> Path:
    bundled = BIN / "ffprobe.exe"
    if bundled.exists():
        return bundled
    which = _which("ffprobe")
    if which:
        return Path(which)
    raise FileNotFoundError(
        "ffprobe not found. Place ffprobe.exe in bin/ or add ffprobe to PATH."
    )


def _which(name: str) -> str | None:
    from shutil import which

    return which(name)


def probe_duration(video_path: Path) -> float:
    cmd = [
        str(ffprobe_path()),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip() or result.stdout}")
    data = json.loads(result.stdout)
    duration = float(data.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("Could not read video duration")
    return duration


def probe_size(video_path: Path) -> tuple[int, int]:
    cmd = [
        str(ffprobe_path()),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip() or result.stdout}")
    streams = json.loads(result.stdout).get("streams") or []
    if not streams:
        raise RuntimeError("No video stream found")
    width = int(streams[0]["width"])
    height = int(streams[0]["height"])
    return width, height


def extract_subtitle_band(
    video_path: Path,
    timestamp: float,
    out_path: Path,
    band_ratio: float = 0.25,
    height: int | None = None,
    width: int | None = None,
    side_inset_ratio: float = 0.0,
    scale_width: int = 720,
) -> None:
    """Grab one frame and crop the bottom subtitle band (optional side inset)."""
    if height is None or width is None:
        probed_w, probed_h = probe_size(video_path)
        if width is None:
            width = probed_w
        if height is None:
            height = probed_h
    band_h = max(32, int(height * band_ratio))
    y = max(0, height - band_h)
    inset = max(0.0, min(0.2, side_inset_ratio))
    x = int(width * inset)
    crop_w = max(64, int(width * (1.0 - 2 * inset)))
    sw = max(320, int(scale_width))
    # Input seek before -i for speed; smaller scale for faster OCR
    vf = f"crop={crop_w}:{band_h}:{x}:{y},scale={sw}:-1"
    cmd = [
        str(ffmpeg_path()),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-an",
        "-vf",
        vf,
        "-y",
        str(out_path),
    ]
    env = os.environ.copy()
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError(
            f"ffmpeg frame extract failed at {timestamp:.2f}s: "
            f"{result.stderr.strip() or result.stdout}"
        )
