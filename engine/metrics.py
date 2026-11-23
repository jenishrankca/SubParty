"""Independent accuracy metrics for SubParty MVP validation (CER / timing ms / RTF / revision)."""

from __future__ import annotations

from typing import Any


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def cer(hyp: str, ref: str) -> float:
    """
    Character error rate = edit_distance / max(1, len(ref)).
    Lower is better. Empty ref + empty hyp → 0.0; empty ref + hyp → 1.0.
    """
    h = (hyp or "").strip()
    r = (ref or "").strip()
    if not r and not h:
        return 0.0
    if not r:
        return 1.0
    return _levenshtein(h, r) / len(r)


def wer(hyp: str, ref: str) -> float:
    """Word error rate on whitespace tokens (EN / spaced ASR). Same formula as CER on tokens."""
    ht = (hyp or "").strip().split()
    rt = (ref or "").strip().split()
    if not rt and not ht:
        return 0.0
    if not rt:
        return 1.0
    # Levenshtein on token sequences
    if ht == rt:
        return 0.0
    prev = list(range(len(rt) + 1))
    for i, ta in enumerate(ht, start=1):
        cur = [i]
        for j, tb in enumerate(rt, start=1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (0 if ta == tb else 1)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1] / len(rt)


def mean_cer(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    """pairs = [(hyp, ref), ...]."""
    if not pairs:
        return {"n": 0, "mean_cer": None, "cers": []}
    vals = [cer(h, r) for h, r in pairs]
    return {
        "n": len(vals),
        "mean_cer": sum(vals) / len(vals),
        "max_cer": max(vals),
        "cers": [round(v, 4) for v in vals],
    }


def ocr_cer_vs_gold(
    pred: list[dict[str, Any]],
    gold: list[dict[str, Any]],
    *,
    match_tol_s: float = 0.75,
) -> dict[str, Any]:
    """CER of predicted ZH vs gold ZH for time-matched cues."""
    pairs: list[tuple[str, str]] = []
    used: set[int] = set()
    for g in gold:
        gzh = str(g.get("text_zh") or "")
        best_i = None
        best_d = 1e9
        for i, p in enumerate(pred):
            if i in used:
                continue
            d = abs(float(p.get("start", 0)) - float(g.get("start", 0)))
            if d < best_d:
                best_d = d
                best_i = i
        if best_i is None or best_d > match_tol_s:
            continue
        used.add(best_i)
        pzh = str(pred[best_i].get("text_zh") or pred[best_i].get("original_ocr") or "")
        pairs.append((pzh, gzh))
    out = mean_cer(pairs)
    out["matched"] = len(pairs)
    out["gold"] = len(gold)
    return out


def asr_cer_vs_gold(
    transcripts: list[str],
    gold_transcripts: list[str],
) -> dict[str, Any]:
    """CER for ASR hypotheses vs gold speech text (CJK-friendly character CER)."""
    n = min(len(transcripts), len(gold_transcripts))
    pairs = [(transcripts[i], gold_transcripts[i]) for i in range(n)]
    return mean_cer(pairs)


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


def _percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def timing_errors(
    pred: list[dict[str, Any]],
    gold: list[dict[str, Any]],
    *,
    match_tol_s: float = 0.75,
) -> dict[str, Any]:
    """
    Match gold↔pred by ZH, prefer closest start within match_tol_s.
    Reports mean abs start/end error in seconds and milliseconds (+ p95).
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

    def _pack(errs: list[float]) -> dict[str, Any]:
        if not errs:
            return {
                "mean_s": None,
                "mean_ms": None,
                "max_s": None,
                "max_ms": None,
                "p95_s": None,
                "p95_ms": None,
            }
        ordered = sorted(errs)
        mean = sum(errs) / len(errs)
        p95 = _percentile(ordered, 95)
        return {
            "mean_s": mean,
            "mean_ms": mean * 1000.0,
            "max_s": max(errs),
            "max_ms": max(errs) * 1000.0,
            "p95_s": p95,
            "p95_ms": (p95 * 1000.0) if p95 is not None else None,
        }

    start_pack = _pack(errs_start)
    end_pack = _pack(errs_end)
    return {
        "matched": matched,
        "gold": len(gold),
        "missed": len(gold) - matched,
        "mean_start_err": start_pack["mean_s"],
        "mean_end_err": end_pack["mean_s"],
        "max_start_err": start_pack["max_s"],
        "mean_start_err_ms": start_pack["mean_ms"],
        "mean_end_err_ms": end_pack["mean_ms"],
        "max_start_err_ms": start_pack["max_ms"],
        "p95_start_err_ms": start_pack["p95_ms"],
        "p95_end_err_ms": end_pack["p95_ms"],
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


def revision_before_after(
    *,
    before_en: str,
    after_en: str,
    gold_en: str,
) -> dict[str, Any]:
    """
    Measure whether a revision moved EN closer to gold (CER on characters, casefold).
    Improvement = before_cer - after_cer (positive = better).
    """
    g = (gold_en or "").strip().lower()
    b = (before_en or "").strip().lower()
    a = (after_en or "").strip().lower()
    before_c = cer(b, g)
    after_c = cer(a, g)
    return {
        "before_cer": round(before_c, 4),
        "after_cer": round(after_c, 4),
        "delta_cer": round(before_c - after_c, 4),
        "improved": after_c < before_c - 1e-9,
        "exact_after": a == g,
        "exact_before": b == g,
    }


def revision_batch_quality(
    items: list[dict[str, str]],
) -> dict[str, Any]:
    """
    items: [{before_en, after_en, gold_en}, ...]
    Aggregates mean CER before/after and fraction improved.
    """
    if not items:
        return {"n": 0, "mean_before_cer": None, "mean_after_cer": None, "improved_rate": None}
    rows = [
        revision_before_after(
            before_en=str(it.get("before_en") or ""),
            after_en=str(it.get("after_en") or ""),
            gold_en=str(it.get("gold_en") or it.get("text_en_gold") or ""),
        )
        for it in items
    ]
    return {
        "n": len(rows),
        "mean_before_cer": sum(r["before_cer"] for r in rows) / len(rows),
        "mean_after_cer": sum(r["after_cer"] for r in rows) / len(rows),
        "improved_rate": sum(1 for r in rows if r["improved"]) / len(rows),
        "exact_after_rate": sum(1 for r in rows if r["exact_after"]) / len(rows),
        "rows": rows,
    }


def realtime_factor(processing_s: float, video_duration_s: float) -> float | None:
    """RTF = processing_time / video_duration (<1 means faster than realtime)."""
    if video_duration_s <= 0:
        return None
    return processing_s / video_duration_s


def multimodal_summary(pred_cues: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate OCR/audio disagreement + reconcile presence from cue payloads."""
    n = len(pred_cues)
    disag = sum(1 for c in pred_cues if c.get("audio_disagreement"))
    with_asr = sum(1 for c in pred_cues if str(c.get("audio_transcript") or "").strip())
    with_recon = sum(
        1
        for c in pred_cues
        if str(c.get("reconciled_zh") or "").strip()
        and str(c.get("reconciled_zh") or "") != str(c.get("original_ocr") or c.get("text_zh") or "")
    )
    return {
        "cues": n,
        "with_asr": with_asr,
        "disagreements": disag,
        "disagreement_rate": (disag / n) if n else None,
        "reconcile_differs": with_recon,
    }


def summarize_job_metrics(
    *,
    pred_cues: list[dict[str, Any]],
    gold_cues: list[dict[str, Any]],
    processing_s: float,
    video_duration_s: float,
    live_latency_s: float | None = None,
    timing_tol_s: float = 0.5,
    asr_hyp: list[str] | None = None,
    asr_gold: list[str] | None = None,
    revision_items: list[dict[str, str]] | None = None,
    asr_processing_s: float | None = None,
    asr_audio_duration_s: float | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ocr": ocr_hit_rate(pred_cues, gold_cues),
        "ocr_cer": ocr_cer_vs_gold(pred_cues, gold_cues, match_tol_s=max(timing_tol_s, 0.35)),
        "timing": timing_errors(pred_cues, gold_cues, match_tol_s=max(timing_tol_s, 0.35)),
        "translation": translation_exact(pred_cues, gold_cues),
        "rtf": realtime_factor(processing_s, video_duration_s),
        "multimodal": multimodal_summary(pred_cues),
        "live_latency_s": live_latency_s,
        "processing_s": processing_s,
        "video_duration_s": video_duration_s,
        "cue_count": len(pred_cues),
    }
    if asr_hyp is not None and asr_gold is not None:
        out["asr_cer"] = asr_cer_vs_gold(asr_hyp, asr_gold)
    if revision_items is not None:
        out["revision"] = revision_batch_quality(revision_items)
    if asr_processing_s is not None and asr_audio_duration_s is not None:
        out["asr_rtf"] = realtime_factor(asr_processing_s, asr_audio_duration_s)
    return out
