# Engine benchmarks

Stage-1 baselines for the commercial/engine track (`cursor/subparty-engine-bench-*`).

## Run

```bat
cd engine
.venv\Scripts\python.exe scripts\baseline_engine_bench.py
```

Outputs `baseline_<utc>.json` and `baseline_latest.json`.

## Decode path (thing to beat)

Per-frame FFmpeg subprocess — see `docs/engine-benchmark-brief.md` and `ffmpeg_util._run_ffmpeg_frame`.

Also on this branch: `decode_compare_*.json`, `ocr_compare_*.json`, `asr_compare_*.json`, `reconcile_pick_*.json`.

```bat
.venv\Scripts\python.exe scripts\measure_reconcile_pick.py
```

See `docs/reconcile-translate-stage.md`.
