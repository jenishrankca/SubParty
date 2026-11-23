# Player audio fix (main)

## Root cause (`the-weird-games.mp4`)

| | |
|--|--|
| Container | MP4 · H.264 + **AAC LC** 44.1 kHz stereo |
| Chromium codecs | AAC is supported |
| Failure | ffprobe: `st: 1 edit list: 1 Missing key frame…` on the **audio** track |
| Effect | Video plays in Electron `<video>` / `subparty://`; **audio silent** |
| Contrast | `samples/the-weird-games-5min.mp4` and demos have **no** edit-list warning and play with sound |

Not a missing track and not an unsupported codec (AC3/DTS). Broken MP4 AAC **edit list** from the download remux (“ISO Media file produced by Google Inc.”).

## Fix

1. **`subparty://` protocol** — explicit `Accept-Ranges` / `206` byte ranges + correct `Content-Type` (no longer bare `net.fetch(file://)`).
2. **Prepare on open** — `prepareVideo` IPC / `POST /playback/prepare` runs ffprobe; if edit-list broken or audio codec unsupported → ffmpeg remux to `data/playback/<hash>.mp4` (`-c copy -movflags +faststart`, or `-c:a aac` when needed). OCR jobs still use the **original** path.
3. **Volume UI** — Mute + slider + % in the transport bar.

Measured: full weird-games remux **~12 s**; post-probe `needs_fix=false`, edit-list cleared; volumedetect shows real levels.

## Verify

```bat
cd engine
.venv\Scripts\python.exe -c "from playback_fix import probe_media, ensure_playable_media; print(probe_media(r'C:\Users\jenis\Downloads\the-weird-games.mp4')['reason']); print(ensure_playable_media(r'C:\Users\jenis\Downloads\the-weird-games.mp4')['play_path'])"
.venv\Scripts\python.exe -m unittest tests.test_playback_fix -v
```
