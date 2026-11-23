# OCR↔ASR reconcile + cue-aware translation pick (bench branch)

Branch: `cursor/subparty-engine-bench-7239` only (not main).

## Stage 5 — Sequence-aware reconciliation

| Piece | Location |
|-------|----------|
| Matcher | `engine/reconcile.py` — greedy L→R OCR↔ASR, non-decreasing segment index |
| Wire-in | `verify_cue_records` → `apply_reconcile_to_records` then speech attach |
| Speech bounds | `speech_align.align_cue_records_to_speech(..., apply_to_display_bounds=False)` |

### Bounds contract

- **visual_*** — hardsub OCR window (display default)
- **speech_*** — ASR segment / word span (evidence; may differ)
- **timing_source** — `visual` \| `hybrid` \| `speech`
- **inferred** — ZH or timing adopted from audio without strong OCR (`adopt-audio-ocr-empty` / typo path); `original_ocr` never silently dropped

### Measured (fixture, no Whisper)

| Metric | Value |
|--------|-------|
| Match wall | **0.10 ms** / 4 cues |
| Matched | 4 / 4 |
| Inferred | 1 |
| Disagreements | 2 |
| Visual↔speech Δ | +50 / −50 / +100 / 0 ms |

JSON: `engine/benchmarks/reconcile_pick_latest.json`

## Stage 6 — Cue-aware translation pick

Replaces length-only `pick_best_candidate` with `score_en_against_zh` (digits, mood `?`/`!`, pronoun hints, length priors vs CJK, neighbor-swallow hard reject). **CueRecord revision history unchanged** — pick returns an EN string; callers still `revise()`.

| Fixture cases | Correct |
|---------------|---------|
| 4 (question, digits, swallow, identical) | **4 / 4** |

```bat
cd engine
.venv\Scripts\python.exe scripts\measure_reconcile_pick.py
REM optional live ASR:
set SUBPARTY_MEASURE_ASR=1
.venv\Scripts\python.exe scripts\measure_reconcile_pick.py --video ..\samples\demo_zh_hardsub.mp4
```

## Tests

`python -m unittest tests.test_reconcile tests.test_speech_align tests.test_audio_verify tests.test_accuracy_core`

## Not on main

Live keep-up defaults stay on main. This branch only.
