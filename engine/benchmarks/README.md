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

**Next PR:** PyAV persistent demux/decode adapter.
