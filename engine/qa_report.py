"""Stage 7 — independent QA report builder (scores only; does not mutate cues)."""

from __future__ import annotations

from typing import Any

from metrics import asr_cer_vs_gold, multimodal_summary, summarize_job_metrics


def build_qa_report(
    *,
    job: Any,
    gold_cues: list[dict[str, Any]] | None = None,
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

    gold = gold_cues or [
        {
            "start": float(c.get("start") or 0),
            "end": float(c.get("end") or 0),
            "text_zh": str(c.get("original_ocr") or c.get("text_zh") or ""),
            "text_en": str(c.get("text_en") or ""),
        }
        for c in cues
    ]

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
