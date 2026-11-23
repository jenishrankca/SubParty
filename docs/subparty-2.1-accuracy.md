# SubParty 2.1 — Accuracy Validation

Pause new product features. Fix the four 2.0 limitations, then add audio as a separate evidence engine.

## Phase A — Context maturity & timing (on `main`)

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

## Phase B — Audio Verification Engine (stub only)

`engine/audio_verify.py` — outline only until Phase A is solid on real episodes.

- Independent of OCR (FFmpeg extract → faster-whisper candidate)
- Never auto-overwrite OCR; keep original OCR + audio transcript + reconciled ZH + EN + revisions
- Gate: `SUBPARTY_AUDIO_VERIFY=1` later; health reports `audio_verify.ready=false` for now
