# SubParty engine audit & benchmark brief (stage 1)

**Branch:** `cursor/subparty-engine-bench-7239` (do **not** merge to `main` until asked)  
**Policy:** Live keep-up stays on `main`. This track is measured-before/after only — no broad refactor, no model swaps yet.

Commercial context (validate, don’t assume): see store `docs/subparty-commercial-and-engine.md`. NLLB-200 distilled 600M is **CC-BY-NC-4.0** — do not bundle for commercial without permission.

---

## Goal of stage 1

1. Establish a **reproducible baseline** on real clips (decode / OCR / timing / ASR where feasible).
2. Document the **current decode path** (per-frame FFmpeg subprocess) as the thing to beat.
3. Leave a **PR-ready next step**: persistent PyAV decode adapter (same crop/OCR API).

Do **not** yet: swap OCR/ASR/MT models, change Live defaults on main, or rewrite the pipeline.

---

## Current architecture (what we are measuring)

```
playhead tick
  → ffmpeg_util.extract_region()   # NEW ffmpeg process per frame (-ss seek, 1 frame, crop+scale)
  → RapidOCR (PP-OCRv6 small via rapidocr / onnxruntime CPU|AzureEP)
  → filter → coarse slot merge → (Quality) fine boundary probes (more ffmpeg+OCR)
  → Argos translate (Live) / optional compare (Quality)
  → optional faster-whisper audio_verify (Quality / off Live path)
  → SRT + SSE cues
```

**Bottleneck hypothesis (to confirm with numbers):** process-spawn + keyframe seek per sample dominates Live keep-up on CPU; OCR is second; translate/refine are deferred on Live.

---

## Decode path to beat

| Item | Today |
|------|--------|
| API | `engine/ffmpeg_util.py` → `_run_ffmpeg_frame` |
| Pattern | `subprocess.run([ffmpeg, -ss T, -i video, -frames:v 1, -vf crop+scale, -y out.png])` |
| Cost drivers | Process spawn, container demux, seek (often keyframe), encode PNG, tear-down — **per tick** |
| Call sites | Live/Quality OCR loop; region detect; Quality fine `probe_zh` timing |

**Next PR (stage 2, not this commit):** PyAV (or ffmpeg pipe) **persistent** demuxer/decoder; seek once, pull frames; same `extract_region`-shaped adapter so pipeline call sites stay stable. Optional NVIDIA hwaccel behind the adapter later.

---

## Benchmark protocol

Script: `engine/scripts/baseline_engine_bench.py`

```bat
cd engine
.venv\Scripts\python.exe scripts\baseline_engine_bench.py
```

Writes `engine/benchmarks/baseline_<timestamp>.json` and prints a summary table.

| Phase | What | Metrics |
|-------|------|---------|
| A Decode | N `extract_region` calls at spaced timestamps | mean/p50/p95 ms, fps-equivalent |
| B OCR | RapidOCR on those crops (after warm) | mean/p50/p95 ms |
| C Live Ready | `live_mode=True` on weird-games-5min until buffer Ready | wall Ready s, cues, buffer_ahead |
| D Quality short | Full job on `demo_zh_hardsub` / `demo_watermark_junk` | wall s, RTF, cue_count, maturity |
| E ASR (optional) | faster-whisper on short demo WAV extract | wall s, RTF if duration known |
| F Synth CER | Existing annotated synth path (if run) | CER / timing err / RTF |

**Clips (required):**

- `samples/the-weird-games-5min.mp4` — real drama hardsub (~300s)
- `samples/demo_zh_hardsub.mp4` — short burned ZH
- `samples/demo_watermark_junk.mp4` — watermark / junk interference

**Optional later:** GeekLink OCR image set for region-interference demos (not required for stage 1 pass).

---

## Engine next (ordered; each needs before/after)

1. **This stage — baseline** ← you are here  
2. Persistent decode (PyAV)  
3. OCR + confirmable region; DeepSeek-OCR-2 only on uncertain frames  
4. ASR/align adapters (faster-whisper baseline vs others); visual ≠ speech timing  
5. OCR↔ASR reconciliation  
6. Cue-aware translation pick (replace length-based `pick_best_candidate`)  
7. Usability (first-run models, evidence inspector, Live vs Quality clarity)

---

## Acceptance for stage 1

- [x] Feature branch off main (`cursor/subparty-engine-bench-7239` — branch-only push)  
- [x] Baseline JSON under `engine/benchmarks/baseline_latest.json`  
- [x] Decode path documented as above  
- [x] Report: branch + numbers + next step = PyAV persistent decode  

### Headline baseline (this workstation, 2026-11-23)

| Metric | Value |
|--------|-------|
| weird-games decode p50 | **239 ms**/frame |
| weird-games OCR p50 | **1679 ms**/crop |
| weird-games combo p50 | **1886 ms**/sample |
| Live Ready | **8.67 s** |
| Quality `demo_zh_hardsub` (~8s) | wall **79.6 s**, RTF **9.96**, 3 cues |
| Quality `demo_watermark_junk` (~8s) | wall **89.5 s**, RTF **11.2**, 3 cues |
| ASR | skipped (PyAV `metadata_errors` vs faster-whisper) |
| GeekLink set | deferred |

**Next PR-ready step:** PyAV persistent demux/decode adapter — same crop API, measure decode p50 before/after.  
