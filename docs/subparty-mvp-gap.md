# SubParty MVP — Gap analysis (stages 1–9 vs `main`)

**Date:** 2026-11-23  
**Baseline:** `main` @ `416b808` (2.1 Phase A + Phase B audio verify)  
**Spec:** [subparty-mvp-spec.md](./subparty-mvp-spec.md) · **Roadmap:** [subparty-roadmap.md](./subparty-roadmap.md)

**Method:** Evidence engines and measurable benches first; stages 6–9 deferred until Critical (1–5) are solid.

| Stage | Priority | Verdict vs `main` |
| --- | --- | --- |
| 1 OCR + timing | Critical | **Partial** |
| 2 Audio + Chinese ASR | Critical | **Partial** |
| 3 OCR/audio reconcile | Critical | **Partial** |
| 4 Context translate + refine | Critical | **Landed** (bench gap) |
| 5 Frame + speech-aligned timing | Critical | **Partial** (speech align **Missing**) |
| 6 Professional editor | High | **Partial** — next after 1–5 |
| 7 Independent QA + benches | High | **Partial** — next after 1–5 |
| 8 Glossary / series memory | High | **Partial** — next after 1–5 |
| 9 Batch + production workflow | High | **Partial** — next after 1–5 |

---

## Stage 1 — Stable OCR + accurate timing

**Landed**

- RapidOCR + filters + region band (`engine/ocr.py`, `text_filters.py`, `region.py`)
- Coarse + frame-edge timing (`timing_engine.py`, `fine_step_s=0.1`, pipeline probes)
- Synth benches: hit-rate OCR, mean start error &lt; 0.75s, RTF (`metrics.py`, `test_benchmark_suite.py`, `test_accuracy_21.py`)

**Gaps (Critical)**

| Gap | Action |
| --- | --- |
| No **CER** (edit-distance) — only substring hit-rate | Add `cer()` + wire into `summarize_job_metrics` |
| Timing reported in seconds only | Add **ms** mean/max/p95 fields |
| Thin annotated suite (1 primary line in live bench) | Expand metric coverage; keep multi-fixture unit benches |

---

## Stage 2 — Audio extract + Chinese ASR

**Landed**

- FFmpeg mono 16 kHz WAV (`ffmpeg_util.extract_audio_wav`)
- faster-whisper `small` ZH ASR + segment timestamps (`audio_verify.py`)
- Optional EOF job path + Models UI + unit tests (mocked ASR)

**Gaps (Critical)**

| Gap | Action |
| --- | --- |
| No ASR **CER/WER** vs gold | Add `asr_cer` / simple WER helpers + fixture tests |
| `word_timestamps=False` | Enable when speech-align needs finer bounds (stage 5) |
| ASR only at EOF | OK for MVP Critical; incremental ASR = later |

---

## Stage 3 — OCR/audio compare + reconcile

**Landed**

- Character similarity + disagreement flags
- `original_ocr` / `audio_transcript` / `reconciled_zh`; never blind-overwrite OCR
- Optional EN refresh via contextual translate

**Gaps (Critical)**

| Gap | Action |
| --- | --- |
| High-agreement ASR cannot fix OCR typos into displayed ZH | Evidence path: high sim + small edit distance → `text_zh=reconciled` while **keeping** `original_ocr` |
| Multimodal scores not in job metrics summary | Include disagreement / reconcile notes in metric helpers |

---

## Stage 4 — Context-aware translate + retroactive refine

**Landed**

- Dialogue buffer, glossary, Argos/NLLB/Qwen
- Maturity: provisional → context-refined → context-complete → final
- Retroactive refine on new future dialogue; EOF finalize

**Gaps (Critical)**

| Gap | Action |
| --- | --- |
| No **before/after revision quality** metric | Score provisional EN vs refined EN vs gold (exact / CER-like) |

---

## Stage 5 — Frame timing + speech alignment

**Landed**

- OCR frame-level boundary inspection

**Gaps (Critical)**

| Gap | Action |
| --- | --- |
| **Speech-aligned timing Missing** — ASR never revises cue `start`/`end` | `align_cues_to_speech()` using overlapping ASR segments; revise with evidence; measure ms delta |

---

## Stages 6–9 (High — after Critical)

| Stage | Landed now | Defer until |
| --- | --- | --- |
| 6 Editor | Cue PATCH, timeline list, uncertain review | Critical metrics green |
| 7 QA | Partial `metrics.py` + synth benches | CER/WER/revision/speech-align benches landed |
| 8 Glossary | File glossary + auto-learn | Series memory / characters polish |
| 9 Batch | `POST /jobs/batch` + `batch_id` | Queue UX / production reports |

---

## Close order (this pass)

1. Metrics: CER, ASR CER, timing ms, revision before/after  
2. Reconcile: evidence typo-adopt (preserve `original_ocr`)  
3. Speech-aligned timing pass after ASR verify  
4. Unit benches for all of the above; incremental commits to `main`  
5. Stages 6–9 only after Critical benches hold

## Validation targets (MVP)

- OCR CER ↓ on annotated ZH  
- ASR CER on gold speech (when fixture present)  
- Timing mean start/end error in **ms**  
- RTF on job done  
- Revision before→after EN quality ↑ on ambiguous fixture  
- Speech-align reduces start/end error vs OCR-only when ASR overlaps
