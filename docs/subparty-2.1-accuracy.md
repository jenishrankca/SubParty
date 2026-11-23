# SubParty 2.1 — Accuracy Validation

Pause new product features. Fix the four 2.0 limitations, then add audio as a separate evidence engine.

## Phase A — Context maturity & timing (on `main` — retestable)

1. **Cue maturity:** `provisional` → `context-refined` → `context-complete` → `final`
   - Horizon = configured future window (default **120s**) or EOF
   - High-confidence approved cues stay eligible when **new future dialogue** arrives (fingerprint change) — no premature permanent skip
2. **Frame-level boundary inspection** around transitions (`fine_step_s=0.1`) for sub-0.5s timing
3. **Benchmarks**
   - Ambiguous early line clarified later (`tests/fixtures/annotated_ambiguous.json`)
   - Multi position/speed/duration scenes (`annotated_multi_scene.json`)
   - Independent metrics: OCR hit / timing error / translation exact / **RTF** / live latency (`engine/metrics.py`)
   - Timing tolerance tightened (bench mean start error **&lt; 0.75s**, match tol **0.5s** — was ±1.5s)
4. **Engine compare** Argos vs NLLB vs Qwen on the same ZH samples (`test_accuracy_21.TestEngineCompare`)
5. **RTF + live latency** emitted on job `done` and documented in status

### Retest Phase A

```bat
cd engine
.venv\Scripts\python.exe -m unittest tests.test_accuracy_21 tests.test_accuracy_core -v
.venv\Scripts\python.exe -m unittest tests.test_benchmark_suite -v
```

Then UI: Process a clip → cues show `maturity` in timeline payloads; after EOF all `final`. Job done SSE includes `rtf` and `live_latency_s`.

## Phase B — Audio Verification Engine (on `main` — retestable)

Independent evidence stream. **Never auto-overwrites OCR** (`text_zh` / `original_ocr` stay as OCR source).

1. **FFmpeg** mono 16 kHz WAV extract (`ffmpeg_util.extract_audio_wav`)
2. **faster-whisper** Chinese ASR + segment timestamps (default size `small` — CUDA float16 or CPU int8; fits RTX 6GB / 16GB RAM)
3. **Compare** OCR vs speech at character level → `audio_disagreement` + confidence; Job UI lists disagreements
4. **Preserve fields:** `original_ocr`, `audio_transcript`, `reconciled_zh` (evidence-only; OCR kept on strong disagreement), EN via existing contextual translate when reconcile note allows
5. **Optional wire:** `job.audio_verify=true` or `SUBPARTY_AUDIO_VERIFY=1`; EOF pass after maturity finalize; SSE `audio` + `done.audio`
6. **Models / UI:** Models lists `faster-whisper-small`; top bar **Audio verify** checkbox; Job meta shows audio status

### Install ASR (once)

```bat
cd engine
.venv\Scripts\pip install -r requirements-audio.txt
```

Then Models → **Download / ensure** on faster-whisper (first transcribe downloads weights into `data/models`).

### Retest Phase B

```bat
cd engine
.venv\Scripts\python.exe -m unittest tests.test_audio_verify -v
```

UI: enable **Audio verify** → Process → after EOF, Job shows Audio status and **Audio disagreements** (OCR vs ASR). Health: `GET /health` → `audio_verify.ready=true` when package installed.
