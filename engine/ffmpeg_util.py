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


def extract_full_frame(
    video_path: Path,
    timestamp: float,
    out_path: Path,
    scale_width: int = 640,
) -> None:
    """Grab one full frame (scaled) for region detection."""
    sw = max(320, int(scale_width))
    vf = f"scale={sw}:-1"
    _run_ffmpeg_frame(video_path, timestamp, out_path, vf)


def extract_region(
    video_path: Path,
    timestamp: float,
    out_path: Path,
    *,
    x_ratio: float,
    y_ratio: float,
    w_ratio: float,
    h_ratio: float,
    height: int | None = None,
    width: int | None = None,
    scale_width: int = 640,
) -> None:
    """Crop a normalized region of the frame for OCR."""
    if height is None or width is None:
        probed_w, probed_h = probe_size(video_path)
        if width is None:
            width = probed_w
        if height is None:
            height = probed_h
    x = max(0, int(width * max(0.0, min(0.95, x_ratio))))
    y = max(0, int(height * max(0.0, min(0.95, y_ratio))))
    crop_w = max(64, int(width * max(0.1, min(1.0, w_ratio))))
    crop_h = max(32, int(height * max(0.05, min(1.0, h_ratio))))
    if x + crop_w > width:
        crop_w = max(64, width - x)
    if y + crop_h > height:
        crop_h = max(32, height - y)
    sw = max(240, int(scale_width))
    vf = f"crop={crop_w}:{crop_h}:{x}:{y},scale={sw}:-1"
    _run_ffmpeg_frame(video_path, timestamp, out_path, vf)


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
    """Grab one frame and crop the bottom subtitle band (legacy helper)."""
    inset = max(0.0, min(0.2, side_inset_ratio))
    extract_region(
        video_path,
        timestamp,
        out_path,
        x_ratio=inset,
        y_ratio=1.0 - band_ratio,
        w_ratio=1.0 - 2 * inset,
        h_ratio=band_ratio,
        height=height,
        width=width,
        scale_width=scale_width,
    )


def extract_audio_wav(
    video_path: Path,
    out_wav: Path,
    *,
    sample_rate: int = 16000,
    start_s: float | None = None,
    duration_s: float | None = None,
) -> Path:
    """
    Extract mono PCM WAV for ASR (16 kHz default).
    Optional start/duration for cue-window extracts.
    """
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(ffmpeg_path()),
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if start_s is not None and start_s > 0:
        cmd.extend(["-ss", f"{float(start_s):.3f}"])
    cmd.extend(["-i", str(video_path)])
    if duration_s is not None and duration_s > 0:
        cmd.extend(["-t", f"{float(duration_s):.3f}"])
    cmd.extend(
        [
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(int(sample_rate)),
            "-c:a",
            "pcm_s16le",
            "-y",
            str(out_wav),
        ]
    )
    env = os.environ.copy()
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    if result.returncode != 0 or not out_wav.exists() or out_wav.stat().st_size < 44:
        raise RuntimeError(
            f"ffmpeg audio extract failed: {result.stderr.strip() or result.stdout}"
        )
    return out_wav


def _run_ffmpeg_frame(video_path: Path, timestamp: float, out_path: Path, vf: str) -> None:
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
