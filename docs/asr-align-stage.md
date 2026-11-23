# ASR / align adapters (bench branch)

Branch: `cursor/subparty-engine-bench-7239`

## Harness fix (before → after)

| | Before | After |
|--|--------|-------|
| Path | faster-whisper `decode_audio` → `av.open(..., metadata_errors=)` | FFmpeg WAV → `asr_io.load_wav_mono_f32` → ndarray |
| Stage-1 baseline | **ASR skipped** (PyAV 19 TypeError) | **faster-whisper runs** |

## Engines

| id | Role | Licence | Default |
|----|------|---------|---------|
| `faster-whisper-small` | Baseline | MIT | yes |
| `qwen3-asr-0.6b` | Compare | Apache-2.0 | optional (`pip install qwen-asr`, prefer separate env) |
| `funasr` | Optional | Apache (confirm card) | optional |

## Align experiments (not Live OCR timing)

Visual hardsub bounds ≠ speech bounds.

| Experiment | Status |
|------------|--------|
| Qwen3-ForcedAligner-0.6B | Apache-2.0; enable `SUBPARTY_FORCED_ALIGNER=1` + qwen-asr |
| TinyAligner | Stub only |

## Measured (this workstation, CPU whisper)

| Clip | Engine | infer | RTF | segs |
|------|--------|-------|-----|------|
| demo_zh_hardsub (8s) | faster-whisper-small | 10.9 s | 1.36 | 0 (silent/synth) |
| weird-games (10s) | faster-whisper-small | **16.2 s** | **1.62** | 2 (`都躲好了吗?…`) |
| Qwen3-ASR / FunASR | — | not installed in this venv | — | — |

Stage-1 baseline: ASR **skipped**. After harness fix: **runs**.

Qwen3-ASR is Apache-2.0 but `qwen-asr` pins transformers — install in a **separate** env for compare. FunASR pip was aborted (heavy); adapter ready when installed.

## Measure

```bat
cd engine
set SUBPARTY_WHISPER_DEVICE=cpu
.venv\Scripts\python.exe scripts\compare_asr.py
```

## Follow-on (stages 5–6 on this branch)

Sequence-aware OCR↔ASR reconcile + cue-aware `pick_best_candidate`: see **`docs/reconcile-translate-stage.md`**. Display bounds stay **visual**; `speech_*` attached separately; inferred samples marked.
