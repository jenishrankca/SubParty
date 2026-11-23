"""Persistent video decode (PyAV) with FFmpeg subprocess fallback.

Same crop/scale contract as ``ffmpeg_util.extract_region`` / ``extract_full_frame``.
Default backend: PyAV long-lived demuxer (env ``SUBPARTY_DECODE=ffmpeg`` forces
per-frame FFmpeg for A/B benches).
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("subparty.video_decode")

DecodeBackend = Literal["pyav", "ffmpeg", "auto"]

_BACKEND_OVERRIDE: DecodeBackend | None = None
_CACHE: dict[str, "PersistentVideoDecoder"] = {}
_CACHE_LOCK = threading.Lock()


def set_decode_backend(backend: DecodeBackend | None) -> None:
    """Force backend for tests/benches. None → env / auto."""
    global _BACKEND_OVERRIDE
    _BACKEND_OVERRIDE = backend


def get_decode_backend() -> str:
    if _BACKEND_OVERRIDE and _BACKEND_OVERRIDE != "auto":
        return _BACKEND_OVERRIDE
    env = (os.environ.get("SUBPARTY_DECODE") or "auto").strip().lower()
    if env in {"ffmpeg", "ff", "subprocess"}:
        return "ffmpeg"
    if env in {"pyav", "av"}:
        return "pyav"
    # auto: prefer PyAV when importable
    try:
        import av  # noqa: F401

        return "pyav"
    except ImportError:
        return "ffmpeg"


def pyav_available() -> bool:
    try:
        import av  # noqa: F401

        return True
    except ImportError:
        return False


def close_all_decoders() -> None:
    with _CACHE_LOCK:
        for dec in list(_CACHE.values()):
            try:
                dec.close()
            except Exception:
                pass
        _CACHE.clear()


def _cache_key(video_path: Path) -> str:
    try:
        return str(video_path.resolve())
    except Exception:
        return str(video_path)


def get_decoder(video_path: Path | str) -> "PersistentVideoDecoder":
    path = Path(video_path)
    key = _cache_key(path)
    with _CACHE_LOCK:
        dec = _CACHE.get(key)
        if dec is not None and not dec.closed:
            return dec
        dec = PersistentVideoDecoder(path)
        _CACHE[key] = dec
        return dec


class PersistentVideoDecoder:
    """Long-lived PyAV demuxer for random-ish OCR frame grabs."""

    def __init__(self, video_path: Path) -> None:
        import av

        self.path = Path(video_path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        self._av = av
        self._lock = threading.Lock()
        self.closed = False
        self.container = None
        self.stream = None
        self.width = 0
        self.height = 0
        self._last_time: float | None = None
        self._forward_slack_s = 0.35  # decode forward without seek if within slack
        self._open()

    def _open(self) -> None:
        if self.container is not None:
            try:
                self.container.close()
            except Exception:
                pass
        self.container = self._av.open(str(self.path))
        streams = self.container.streams.video
        if not streams:
            self.container.close()
            self.container = None
            raise RuntimeError(f"No video stream: {self.path}")
        self.stream = streams[0]
        self.stream.thread_type = "AUTO"
        self.width = int(self.stream.width or 0)
        self.height = int(self.stream.height or 0)
        self._last_time = None

    def close(self) -> None:
        with self._lock:
            if self.closed:
                return
            self.closed = True
            if self.container is not None:
                try:
                    self.container.close()
                except Exception:
                    pass
                self.container = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def frame_image(self, timestamp: float):
        """Return a PIL RGB image near ``timestamp`` (seconds)."""
        t = max(0.0, float(timestamp))
        with self._lock:
            if self.closed:
                raise RuntimeError("decoder closed")
            try:
                frame = self._decode_near(t)
            except Exception:
                # EOF / codec stuck after prior seek — reopen and retry once
                self._open()
                frame = self._decode_near(t)
            img = frame.to_image()
            if img.mode != "RGB":
                img = img.convert("RGB")
            return img

    def _decode_near(self, t: float) -> Any:
        # Forward-only: if we already sit just before t, keep decoding (Live path)
        need_seek = True
        if self._last_time is not None and t >= self._last_time - 0.02:
            if t - self._last_time <= self._forward_slack_s:
                need_seek = False
            elif t - self._last_time <= 2.5:
                # Small jump forward — try decode without seek first, seek if we overshoot gap
                need_seek = False

        if need_seek:
            self._seek(t)

        best = None
        best_d = 1e9
        # Decode a short window of frames after seek / forward
        for i, frame in enumerate(self.container.decode(self.stream)):
            ft = float(frame.time) if frame.time is not None else None
            if ft is None:
                continue
            d = abs(ft - t)
            if d < best_d:
                best_d = d
                best = frame
            # Accept first frame at/after target within tolerance
            if ft >= t - 0.04:
                self._last_time = ft
                return frame
            # Don't scan forever on sparse GOPs
            if i > 90 and best is not None:
                break
            if ft > t + 1.5 and best is not None:
                break

        if best is None:
            # Retry with forced seek to keyframe then decode
            self._seek(t, any_frame=True)
            for i, frame in enumerate(self.container.decode(self.stream)):
                ft = float(frame.time) if frame.time is not None else 0.0
                best = frame
                self._last_time = ft
                if ft >= t - 0.1 or i > 60:
                    return frame
            raise RuntimeError(f"PyAV decode failed at t={t:.3f}s for {self.path.name}")

        self._last_time = float(best.time) if best.time is not None else t
        return best

    def _seek(self, t: float, *, any_frame: bool = False) -> None:
        tb = self.stream.time_base
        if tb is not None:
            offset = int(t / tb)
            self.container.seek(
                offset,
                stream=self.stream,
                any_frame=any_frame,
                backward=True,
            )
        else:
            # AV_TIME_BASE = 1_000_000
            offset = int(t * 1_000_000)
            self.container.seek(offset, any_frame=any_frame, backward=True)
        self._last_time = None

    def save_region(
        self,
        timestamp: float,
        out_path: Path,
        *,
        x_ratio: float,
        y_ratio: float,
        w_ratio: float,
        h_ratio: float,
        width: int | None = None,
        height: int | None = None,
        scale_width: int = 640,
    ) -> str:
        """Crop + scale region to PNG. Returns backend id ``pyav``."""
        from PIL import Image

        fw = int(width or self.width)
        fh = int(height or self.height)
        x = max(0, int(fw * max(0.0, min(0.95, x_ratio))))
        y = max(0, int(fh * max(0.0, min(0.95, y_ratio))))
        crop_w = max(64, int(fw * max(0.1, min(1.0, w_ratio))))
        crop_h = max(32, int(fh * max(0.05, min(1.0, h_ratio))))
        if x + crop_w > fw:
            crop_w = max(64, fw - x)
        if y + crop_h > fh:
            crop_h = max(32, fh - y)
        sw = max(240, int(scale_width))

        img = self.frame_image(timestamp)
        # Frame may be stream size; crop in frame pixel space
        iw, ih = img.size
        # If probe size differs from decoded, scale ratios to decoded size
        sx = iw / fw if fw > 0 else 1.0
        sy = ih / fh if fh > 0 else 1.0
        cx = int(x * sx)
        cy = int(y * sy)
        cw = max(1, int(crop_w * sx))
        ch = max(1, int(crop_h * sy))
        cx = min(cx, max(0, iw - 1))
        cy = min(cy, max(0, ih - 1))
        cw = min(cw, iw - cx)
        ch = min(ch, ih - cy)
        cropped = img.crop((cx, cy, cx + cw, cy + ch))
        if cropped.width != sw:
            nh = max(1, int(round(cropped.height * (sw / max(1, cropped.width)))))
            cropped = cropped.resize((sw, nh), Image.Resampling.BILINEAR)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(out_path, format="PNG")
        return "pyav"

    def save_full(
        self,
        timestamp: float,
        out_path: Path,
        *,
        scale_width: int = 640,
    ) -> str:
        from PIL import Image

        sw = max(240, int(scale_width))
        img = self.frame_image(timestamp)
        if img.width != sw:
            nh = max(1, int(round(img.height * (sw / max(1, img.width)))))
            img = img.resize((sw, nh), Image.Resampling.BILINEAR)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, format="PNG")
        return "pyav"


def extract_region_decoded(
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
    ffmpeg_fallback: Any = None,
) -> str:
    """
    Extract cropped region. Returns backend used: ``pyav`` or ``ffmpeg``.
    ``ffmpeg_fallback`` is a callable with the same signature as
    ``ffmpeg_util._extract_region_ffmpeg`` (raises on failure).
    """
    backend = get_decode_backend()
    if backend == "pyav" and pyav_available():
        try:
            dec = get_decoder(video_path)
            return dec.save_region(
                timestamp,
                out_path,
                x_ratio=x_ratio,
                y_ratio=y_ratio,
                w_ratio=w_ratio,
                h_ratio=h_ratio,
                width=width,
                height=height,
                scale_width=scale_width,
            )
        except Exception as exc:
            logger.warning("PyAV extract_region failed @%.2fs (%s); falling back to FFmpeg", timestamp, exc)

    if ffmpeg_fallback is None:
        raise RuntimeError("FFmpeg fallback not provided and PyAV unavailable/failed")
    ffmpeg_fallback(
        video_path,
        timestamp,
        out_path,
        x_ratio=x_ratio,
        y_ratio=y_ratio,
        w_ratio=w_ratio,
        h_ratio=h_ratio,
        height=height,
        width=width,
        scale_width=scale_width,
    )
    return "ffmpeg"


def extract_full_frame_decoded(
    video_path: Path,
    timestamp: float,
    out_path: Path,
    *,
    scale_width: int = 640,
    ffmpeg_fallback: Any = None,
) -> str:
    backend = get_decode_backend()
    if backend == "pyav" and pyav_available():
        try:
            dec = get_decoder(video_path)
            return dec.save_full(timestamp, out_path, scale_width=scale_width)
        except Exception as exc:
            logger.warning("PyAV extract_full_frame failed @%.2fs (%s); falling back to FFmpeg", timestamp, exc)

    if ffmpeg_fallback is None:
        raise RuntimeError("FFmpeg fallback not provided and PyAV unavailable/failed")
    ffmpeg_fallback(video_path, timestamp, out_path, scale_width=scale_width)
    return "ffmpeg"
