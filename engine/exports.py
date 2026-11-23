"""Subtitle exporters: EN SRT, bilingual SRT, VTT, ASS."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def format_ts_srt(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def format_ts_vtt(seconds: float) -> str:
    return format_ts_srt(seconds).replace(",", ".")


def export_srt(cues: list[dict[str, Any]], path: Path, *, bilingual: bool = False) -> Path:
    path = Path(path)
    blocks: list[str] = []
    for i, c in enumerate(sorted(cues, key=lambda x: float(x["start"])), start=1):
        start = format_ts_srt(float(c["start"]))
        end = format_ts_srt(float(c["end"]))
        en = str(c.get("text_en") or "").strip()
        zh = str(c.get("text_zh") or "").strip()
        if bilingual and zh:
            body = f"{zh}\n{en}" if en else zh
        else:
            body = en or zh
        blocks.append(f"{i}\n{start} --> {end}\n{body}\n\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(blocks), encoding="utf-8")
    return path


def export_vtt(cues: list[dict[str, Any]], path: Path, *, bilingual: bool = False) -> Path:
    path = Path(path)
    lines = ["WEBVTT", ""]
    for c in sorted(cues, key=lambda x: float(x["start"])):
        start = format_ts_vtt(float(c["start"]))
        end = format_ts_vtt(float(c["end"]))
        en = str(c.get("text_en") or "").strip()
        zh = str(c.get("text_zh") or "").strip()
        if bilingual and zh:
            body = f"{zh}\n{en}" if en else zh
        else:
            body = en or zh
        lines.append(f"{start} --> {end}")
        lines.append(body)
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def export_ass(cues: list[dict[str, Any]], path: Path, *, bilingual: bool = False) -> Path:
    """Minimal ASS dialogue track."""
    path = Path(path)
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,40,40,48,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def ass_ts(seconds: float) -> str:
        if seconds < 0:
            seconds = 0.0
        cs = int(round(seconds * 100))
        h, rem = divmod(cs, 360000)
        m, rem = divmod(rem, 6000)
        s, c = divmod(rem, 100)
        return f"{h}:{m:02d}:{s:02d}.{c:02d}"

    events: list[str] = []
    for c in sorted(cues, key=lambda x: float(x["start"])):
        en = str(c.get("text_en") or "").strip()
        zh = str(c.get("text_zh") or "").strip()
        if bilingual and zh:
            text = f"{zh}\\N{en}" if en else zh
        else:
            text = en or zh
        text = text.replace("\n", "\\N")
        events.append(
            f"Dialogue: 0,{ass_ts(float(c['start']))},{ass_ts(float(c['end']))},Default,,0,0,0,,{text}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return path


def export_job_formats(
    video_path: str,
    cues: list[dict[str, Any]],
    *,
    bilingual_srt: bool = True,
    vtt: bool = True,
    ass: bool = True,
) -> dict[str, str]:
    p = Path(video_path)
    stem = p.stem
    parent = p.parent
    out: dict[str, str] = {}
    en_srt = parent / f"{stem}.en.srt"
    export_srt(cues, en_srt, bilingual=False)
    out["en_srt"] = str(en_srt)
    if bilingual_srt:
        bi = parent / f"{stem}.zh-en.srt"
        export_srt(cues, bi, bilingual=True)
        out["bilingual_srt"] = str(bi)
    if vtt:
        vtt_path = parent / f"{stem}.en.vtt"
        export_vtt(cues, vtt_path, bilingual=False)
        out["vtt"] = str(vtt_path)
    if ass:
        ass_path = parent / f"{stem}.en.ass"
        export_ass(cues, ass_path, bilingual=True)
        out["ass"] = str(ass_path)
    return out
