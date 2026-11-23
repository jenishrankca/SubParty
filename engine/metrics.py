"""Independent accuracy metrics for SubParty 2.1 benchmarks."""

from __future__ import annotations

from typing import Any


def ocr_hit_rate(pred: list[dict[str, Any]], gold: list[dict[str, Any]]) -> dict[str, Any]:
    """Fraction of gold ZH lines found in predictions (substring OK)."""
    if not gold:
        return {"hit": 0, "gold": 0, "rate": None}
    hit = 0
    for g in gold:
        gzh = str(g.get("text_zh") or "")
        if any(gzh and gzh in str(p.get("text_zh") or "") for p in pred):
            hit += 1
    return {"hit": hit, "gold": len(gold), "rate": hit / len(gold)}


def timing_errors(
    pred: list[dict[str, Any]],
    gold: list[dict[str, Any]],
    *,
    match_tol_s: float = 0.75,
) -> dict[str, Any]:
    """
    Match gold↔pred by ZH, prefer closest start within match_tol_s.
    Reports mean abs start/end error independently of translation quality.
    """
    errs_start: list[float] = []
    errs_end: list[float] = []
    used: set[int] = set()
    matched = 0
    for g in gold:
        gzh = str(g.get("text_zh") or "")
        best_i = None
        best_d = 1e9
        for i, p in enumerate(pred):
            if i in used:
                continue
            if str(p.get("text_zh") or "") != gzh and gzh not in str(p.get("text_zh") or ""):
                continue
            d = abs(float(p["start"]) - float(g["start"]))
            if d < best_d:
                best_d = d
                best_i = i
        if best_i is None or best_d > match_tol_s:
            continue
        used.add(best_i)
        matched += 1
        p = pred[best_i]
        errs_start.append(abs(float(p["start"]) - float(g["start"])))
        errs_end.append(abs(float(p["end"]) - float(g["end"])))
    return {
        "matched": matched,
        "gold": len(gold),
        "missed": len(gold) - matched,
        "mean_start_err": (sum(errs_start) / len(errs_start)) if errs_start else None,
        "mean_end_err": (sum(errs_end) / len(errs_end)) if errs_end else None,
        "max_start_err": max(errs_start) if errs_start else None,
        "match_tol_s": match_tol_s,
    }


def translation_exact(
    pred: list[dict[str, Any]],
    gold: list[dict[str, Any]],
) -> dict[str, Any]:
    """Exact EN match rate when gold provides text_en (optional)."""
    with_en = [g for g in gold if str(g.get("text_en") or "").strip()]
    if not with_en:
        return {"scored": 0, "exact": 0, "rate": None}
    exact = 0
    for g in with_en:
        gzh = str(g.get("text_zh") or "")
        gen = str(g.get("text_en") or "").strip().lower()
        for p in pred:
            if gzh and gzh in str(p.get("text_zh") or ""):
                if str(p.get("text_en") or "").strip().lower() == gen:
                    exact += 1
                break
    return {"scored": len(with_en), "exact": exact, "rate": exact / len(with_en)}


def realtime_factor(processing_s: float, video_duration_s: float) -> float | None:
    """RTF = processing_time / video_duration (<1 means faster than realtime)."""
    if video_duration_s <= 0:
        return None
    return processing_s / video_duration_s


def summarize_job_metrics(
    *,
    pred_cues: list[dict[str, Any]],
    gold_cues: list[dict[str, Any]],
    processing_s: float,
    video_duration_s: float,
    live_latency_s: float | None = None,
    timing_tol_s: float = 0.5,
) -> dict[str, Any]:
    return {
        "ocr": ocr_hit_rate(pred_cues, gold_cues),
        "timing": timing_errors(pred_cues, gold_cues, match_tol_s=max(timing_tol_s, 0.35)),
        "translation": translation_exact(pred_cues, gold_cues),
        "rtf": realtime_factor(processing_s, video_duration_s),
        "live_latency_s": live_latency_s,
        "processing_s": processing_s,
        "video_duration_s": video_duration_s,
        "cue_count": len(pred_cues),
    }
