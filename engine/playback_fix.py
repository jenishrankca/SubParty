"""Make source media Chromium/Electron-playable (audio).

Root cause class:
- Unsupported audio (AC3/EAC3/DTS/…) → Chromium silent
- Broken MP4 AAC edit lists (common on some re-muxed downloads) → video OK, audio silent

OCR / jobs always keep the original path; only the player uses the fixed file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from ffmpeg_util import ffmpeg_path, ffprobe_path
from paths import app_data_dir

logger = logging.getLogger("subparty.playback")

# Codecs Chromium typically cannot decode for <video> audio
_UNSUPPORTED_AUDIO = frozenset(
    {
        "ac3",
        "eac3",
        "truehd",
        "dts",
        "dca",
        "mlp",
        "pcm_bluray",
        "hdmv_pgs_subtitle",  # not audio; ignored via codec_type filter
    }
)


def playback_dir() -> Path:
    d = app_data_dir() / "playback"
    d.mkdir(parents=True, exist_ok=True)
    return d


def probe_media(video_path: Path | str) -> dict[str, Any]:
    """Return streams + whether player remux/transcode is needed."""
    path = Path(video_path)
    cmd = [
        str(ffprobe_path()),
        "-hide_banner",
        "-show_entries",
        "stream=index,codec_type,codec_name,channels,sample_rate,bit_rate",
        "-show_entries",
        "format=format_name,duration,size",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    stderr = result.stderr or ""
    stdout = result.stdout or ""
    if result.returncode != 0 and not stdout.strip():
        raise RuntimeError(f"ffprobe failed: {stderr.strip() or stdout}")
    data = json.loads(stdout or "{}")
    streams = list(data.get("streams") or [])
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    video = [s for s in streams if s.get("codec_type") == "video"]
    edit_list_broken = "edit list" in stderr.lower()
    bad_codecs = [
        str(s.get("codec_name") or "").lower()
        for s in audio
        if str(s.get("codec_name") or "").lower() in _UNSUPPORTED_AUDIO
    ]
    needs = bool(edit_list_broken or bad_codecs)
    reason = None
    if edit_list_broken:
        reason = "mp4-aac-edit-list"
    elif bad_codecs:
        reason = f"unsupported-audio:{','.join(bad_codecs)}"
    return {
        "path": str(path),
        "audio": audio,
        "video": video,
        "format": data.get("format") or {},
        "edit_list_broken": edit_list_broken,
        "unsupported_audio": bad_codecs,
        "needs_fix": needs,
        "reason": reason,
        "stderr_tail": stderr.strip()[-400:] if stderr else "",
    }


def _cache_key(path: Path) -> str:
    st = path.stat()
    raw = f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def ensure_playable_media(
    video_path: Path | str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """
    Return a path Chromium can play with sound.

    Uses stream copy remux (+faststart) when only edit-list is broken;
    re-encodes audio to AAC when codec is unsupported.
    """
    src = Path(video_path).resolve()
    if not src.is_file():
        raise FileNotFoundError(str(src))
    probe = probe_media(src)
    if not probe["needs_fix"] and not force:
        return {
            "ok": True,
            "play_path": str(src),
            "source_path": str(src),
            "remuxed": False,
            "cached": False,
            "probe": probe,
        }

    out = playback_dir() / f"{_cache_key(src)}.mp4"
    if out.is_file() and out.stat().st_size > 1000 and not force:
        return {
            "ok": True,
            "play_path": str(out),
            "source_path": str(src),
            "remuxed": True,
            "cached": True,
            "probe": probe,
        }

    reencode_audio = bool(probe["unsupported_audio"])
    cmd = [str(ffmpeg_path()), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
    if reencode_audio:
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "160k"]
    else:
        cmd += ["-c", "copy"]
    cmd += ["-movflags", "+faststart", str(out)]
    logger.info("playback fix %s → %s (%s)", src.name, out.name, probe.get("reason"))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not out.is_file():
        err = (result.stderr or result.stdout or "ffmpeg failed").strip()
        raise RuntimeError(f"playback remux failed: {err}")

    # Verify edit-list warning cleared when that was the reason
    post = probe_media(out)
    return {
        "ok": True,
        "play_path": str(out),
        "source_path": str(src),
        "remuxed": True,
        "cached": False,
        "reencode_audio": reencode_audio,
        "probe": probe,
        "post_probe": {
            "edit_list_broken": post["edit_list_broken"],
            "needs_fix": post["needs_fix"],
            "audio": post["audio"],
        },
    }
