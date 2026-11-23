"""Build short synthetic hardsub MP4s for region-detection tests."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ffmpeg_util import ffmpeg_path


def _font(size: int):
    for candidate in (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ):
        p = Path(candidate)
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                continue
    return ImageFont.load_default()


def make_hardsub_video(
    out_path: Path,
    *,
    width: int,
    height: int,
    text_y_ratio: float,
    text: str = "修仙不如吃饭",
    seconds: float = 2.0,
    fps: int = 2,
) -> Path:
    """
    Create a short MP4 with centered Chinese text at the given vertical ratio.
    text_y_ratio is the center of the subtitle band (0=top, 1=bottom).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    font = _font(max(28, height // 18))
    n_frames = max(2, int(seconds * fps))

    with tempfile.TemporaryDirectory(prefix="synth_hardsub_") as tmp:
        tmp_dir = Path(tmp)
        for i in range(n_frames):
            im = Image.new("RGB", (width, height), (18, 22, 28))
            draw = ImageDraw.Draw(im)
            # subtle non-subtitle noise (corner watermark-ish)
            draw.text((12, 12), "4K", fill=(60, 60, 60), font=_font(max(14, height // 40)))
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x = (width - tw) // 2
            y = int(height * text_y_ratio - th / 2)
            y = max(4, min(height - th - 4, y))
            # dark bar behind text (hardsub look)
            pad = max(6, th // 4)
            draw.rectangle(
                [x - pad, y - pad // 2, x + tw + pad, y + th + pad // 2],
                fill=(0, 0, 0),
            )
            draw.text((x, y), text, fill=(255, 255, 255), font=font)
            frame = tmp_dir / f"f_{i:04d}.png"
            im.save(frame)

        pattern = str(tmp_dir / "f_%04d.png")
        cmd = [
            str(ffmpeg_path()),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            pattern,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-t",
            f"{seconds:.2f}",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0 or not out_path.exists():
            raise RuntimeError(f"ffmpeg synth failed: {result.stderr or result.stdout}")
    return out_path
