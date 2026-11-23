"""Stage 7 — independent QA report builder (scores only; does not mutate cues)."""

from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Any

from metrics import asr_cer_vs_gold, multimodal_summary, summarize_job_metrics
from paths import reports_dir


def load_gold_cues(gold_path: str | Path | None = None, gold_cues: list[dict[str, Any]] | None = None) -> list[dict[str, Any]] | None:
    if gold_cues:
        return list(gold_cues)
    if not gold_path:
        return None
    path = Path(gold_path)
    if not path.exists():
        raise FileNotFoundError(f"gold file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [c for c in data if isinstance(c, dict)]
    if isinstance(data, dict):
        cues = data.get("cues") or data.get("gold_cues") or []
        if isinstance(cues, list):
            return [c for c in cues if isinstance(c, dict)]
    raise ValueError("gold JSON must be a list of cues or {cues:[...]}")


def build_qa_report(
    *,
    job: Any,
    gold_cues: list[dict[str, Any]] | None = None,
    gold_path: str | Path | None = None,
) -> dict[str, Any]:
    cues = [dict(c) for c in (getattr(job, "cues", None) or [])]
    records = list(getattr(job, "cue_records", None) or [])
    if records:
        by_id = {str(getattr(r, "cue_id", "")): r for r in records}
        for i, c in enumerate(cues):
            rid = str(c.get("cue_id") or "")
            rec = by_id.get(rid)
            if rec is None:
                nearest = min(
                    records,
                    key=lambda r: abs(float(r.start) - float(c.get("start") or 0)),
                )
                if abs(float(nearest.start) - float(c.get("start") or 0)) < 0.25:
                    rec = nearest
            if rec is not None and hasattr(rec, "as_payload"):
                extra = rec.as_payload(i + 1)
                for k in (
                    "original_ocr",
                    "audio_transcript",
                    "reconciled_zh",
                    "audio_disagreement",
                    "maturity",
                    "ocr_confidence",
                    "timing_confidence",
                ):
                    if k in extra and extra[k] is not None:
                        c[k] = extra[k]

    loaded = load_gold_cues(gold_path=gold_path, gold_cues=gold_cues)
    gold = loaded or [
        {
            "start": float(c.get("start") or 0),
            "end": float(c.get("end") or 0),
            "text_zh": str(c.get("original_ocr") or c.get("text_zh") or ""),
            "text_en": str(c.get("text_en") or ""),
        }
        for c in cues
    ]
    gold_source = str(gold_path) if gold_path else ("inline" if gold_cues else "self")

    processing_s = float(getattr(job, "processing_s", 0) or 0)
    duration = float(getattr(job, "duration", 0) or 0) or 1.0
    audio = dict(getattr(job, "audio_summary", None) or {})
    asr_rtf_block = audio.get("asr_rtf") if isinstance(audio.get("asr_rtf"), dict) else {}

    with_asr = [c for c in cues if str(c.get("audio_transcript") or "").strip()]
    metrics = summarize_job_metrics(
        pred_cues=cues,
        gold_cues=gold,
        processing_s=processing_s or max(0.01, duration * 0.01),
        video_duration_s=duration,
        live_latency_s=float(getattr(job, "live_lag", 0) or 0) or None,
        asr_processing_s=asr_rtf_block.get("processing_s"),
        asr_audio_duration_s=asr_rtf_block.get("audio_duration_s"),
    )
    if with_asr:
        metrics["asr_cer"] = asr_cer_vs_gold(
            [str(c.get("audio_transcript") or "") for c in with_asr],
            [str(c.get("original_ocr") or c.get("text_zh") or "") for c in with_asr],
        )

    uncertain = [
        c
        for c in cues
        if c.get("uncertain")
        or c.get("audio_disagreement")
        or (
            isinstance(c.get("ocr_confidence"), (int, float))
            and float(c["ocr_confidence"]) < 0.78
        )
    ]
    return {
        "ok": True,
        "job_id": getattr(job, "id", None),
        "status": getattr(job, "status", None),
        "series_id": getattr(job, "series_id", ""),
        "cue_count": len(cues),
        "gold_source": gold_source,
        "gold_count": len(gold),
        "metrics": metrics,
        "multimodal": multimodal_summary(cues),
        "audio_summary": audio,
        "uncertain_count": len(uncertain),
        "review_queue": [
            {
                "index": c.get("index"),
                "start": c.get("start"),
                "end": c.get("end"),
                "text_zh": c.get("text_zh"),
                "text_en": c.get("text_en"),
                "audio_transcript": c.get("audio_transcript"),
                "audio_disagreement": c.get("audio_disagreement"),
                "maturity": c.get("maturity"),
            }
            for c in uncertain[:40]
        ],
    }


def export_qa_html(report: dict[str, Any], *, job_id: str | None = None) -> Path:
    """Write a self-contained HTML QA report under data/reports (no PDF deps)."""
    jid = job_id or str(report.get("job_id") or "job")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = reports_dir() / f"qa-{jid[:8]}-{stamp}.html"
    metrics = report.get("metrics") or {}
    timing = metrics.get("timing") or {}
    ocr_cer = metrics.get("ocr_cer") or {}
    mm = report.get("multimodal") or {}
    rows = ""
    for c in report.get("review_queue") or []:
        rows += (
            "<tr>"
            f"<td>{html.escape(str(c.get('index') or ''))}</td>"
            f"<td>{html.escape(str(c.get('start') or ''))}–{html.escape(str(c.get('end') or ''))}</td>"
            f"<td>{html.escape(str(c.get('text_zh') or ''))}</td>"
            f"<td>{html.escape(str(c.get('text_en') or ''))}</td>"
            f"<td>{html.escape(str(c.get('audio_transcript') or ''))}</td>"
            f"<td>{'yes' if c.get('audio_disagreement') else ''}</td>"
            "</tr>"
        )
    body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>SubParty QA {html.escape(jid)}</title>
<style>
body{{font-family:Segoe UI,system-ui,sans-serif;margin:2rem;background:#111;color:#eee}}
table{{border-collapse:collapse;width:100%;margin-top:1rem}}
th,td{{border:1px solid #444;padding:.4rem .55rem;text-align:left;vertical-align:top}}
th{{background:#222}} .meta span{{display:inline-block;margin-right:1.25rem}}
code{{color:#9cf}}
</style></head><body>
<h1>SubParty QA report</h1>
<div class="meta">
<span>Job <code>{html.escape(jid)}</code></span>
<span>Status {html.escape(str(report.get('status') or ''))}</span>
<span>Series {html.escape(str(report.get('series_id') or ''))}</span>
<span>Gold {html.escape(str(report.get('gold_source') or ''))}</span>
</div>
<ul>
<li>Cues: {html.escape(str(report.get('cue_count')))}</li>
<li>Uncertain: {html.escape(str(report.get('uncertain_count')))}</li>
<li>OCR CER mean: {html.escape(str(ocr_cer.get('mean_cer')))}</li>
<li>Mean start err ms: {html.escape(str(timing.get('mean_start_err_ms')))}</li>
<li>RTF: {html.escape(str(metrics.get('rtf')))}</li>
<li>Audio disagreements: {html.escape(str(mm.get('disagreements')))}</li>
</ul>
<h2>Review queue</h2>
<table><thead><tr><th>#</th><th>Time</th><th>ZH</th><th>EN</th><th>ASR</th><th>Disagree</th></tr></thead>
<tbody>{rows or '<tr><td colspan="6">None</td></tr>'}</tbody></table>
<details><summary>Raw JSON</summary><pre>{html.escape(json.dumps(report, ensure_ascii=False, indent=2))}</pre></details>
</body></html>"""
    path.write_text(body, encoding="utf-8")
    # Also drop JSON sibling for tooling
    json_path = path.with_suffix(".json")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
