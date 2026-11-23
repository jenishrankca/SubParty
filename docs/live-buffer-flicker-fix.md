# Live buffer flicker fix (main)

## Symptom (Jenish recording)

During **normal 1× play** (no seek) around ~1:17, playback auto-paused; screen flickered between `Preparing subtitles…` and `Ready — Play`; subs vanished.

## Root causes

1. **UI gate thrash** — `bufferStatus.ready` flipping false mid-play re-armed `Preparing`, which **auto-paused** the video and drew the full-screen gate over black frames.
2. **Live OCR** — after priming, picker used `lagging=True` always (`live_mode and not live_priming`), snapping to playhead and never rebuilding ahead. Blank-streak strides up to 3s made holes worse. Buffer drained at 1× when OCR cost ≈ realtime.

## Fix

| Layer | Change |
|-------|--------|
| UI | `watchUnlocked` after Play — stay Ready; no Preparing overlay / auto-pause; soft **Catching up…** badge; keep last cue |
| Pipeline | Lookahead 12s; refill densify when `buffer_ahead < 3s`; end priming at 5s; **don’t** always-lag after priming |
| Scheduler | Live catch-up on thin buffer; denser interval bounds (≤1.6s), refine only when ≥6s ahead |

Seek still re-arms Preparing for the new window.

## Verify

```bat
cd engine
.venv\Scripts\python.exe -m unittest tests.test_live_buffer_policy tests.test_accuracy_core -v
cd ..\app
npx --yes tsx src/watchGate.selftest.ts
```

Manual: Live Process → Ready → Play (no seek). `buffer_ahead` should stay >0; no Preparing flicker.
