# SubParty MVP — Gap analysis (stages 1–9 vs `main`)

**Date:** 2026-11-23 (updated)  
**Baseline tip:** push after this pass  
**Spec:** [subparty-mvp-spec.md](./subparty-mvp-spec.md) · **Roadmap:** [subparty-roadmap.md](./subparty-roadmap.md)

**Method:** Evidence engines and measurable benches first; High stages as incremental product slices.

| Stage | Priority | Verdict |
| --- | --- | --- |
| 1 OCR + timing | Critical | **Landed** (+ suite polish) |
| 2 Audio + Chinese ASR | Critical | **Landed** (word ts + ASR RTF) |
| 3 OCR/audio reconcile | Critical | **Landed** |
| 4 Context translate + refine | Critical | **Landed** |
| 5 Frame + speech-aligned timing | Critical | **Landed** (word-aware align) |
| 6 Professional editor | High | **Partial → slice landed** |
| 7 Independent QA + benches | High | **Partial → slice landed** |
| 8 Glossary / series memory | High | **Partial → slice landed** |
| 9 Batch + production workflow | High | **Partial → slice landed** |

---

## Critical (closed)

| Item | Status |
| --- | --- |
| CER / ASR CER / timing ms+p95 | Done — `metrics.py` |
| Revision before/after | Done — ambiguous + suite fixtures |
| `adopt-audio-ocr-typo` | Done — keeps `original_ocr` |
| Speech-aligned timing | Done — `speech_align.py` |
| Broader annotated suite | Done — `fixtures/annotated_suite.json` + `test_annotated_suite.py` |
| Word timestamps | Done — `transcribe_zh(word_timestamps=True)` → word-aware align |
| ASR RTF | Done — `asr_rtf_report` + verify summary / metrics |
| Multimodal scores in metrics | Done — `multimodal_summary` |

```bat
cd engine
.venv\Scripts\python.exe -m unittest tests.test_annotated_suite tests.test_mvp_metrics tests.test_speech_align -v
```

---

## High stages (first slices)

### 6 Editor — landed slice
- Timeline → **Production editor**: OCR / ASR / reconciled evidence, maturity, prev/next, disagreement highlighting

### 7 QA — landed slice
- `GET/POST /jobs/{id}/qa` → CER, timing ms, RTF, multimodal, review queue
- UI **qa** panel

### 8 Glossary / series — landed slice
- `GET /glossary/series` index · `GET /glossary/suggest` character candidates
- UI suggestions + all-series memory list

### 9 Batch — landed slice
- `GET /jobs/batch/{batch_id}` aggregate status
- UI **batch** panel with progress / per-job rows

```bat
.venv\Scripts\python.exe -m unittest tests.test_mvp_high -v
```

### Still open (next polish)
- Deeper editor (split/merge cues, TM archive UI)
- QA gold upload from disk + exportable HTML/PDF reports
- Series character sheets beyond glossary terms
- Batch export-all / retry-failed / persistent queue across restarts

---

## Validation targets

- OCR CER ↓ · ASR CER · timing ms · RTF · revision before→after · speech-align ms delta · multimodal disagreement rate
