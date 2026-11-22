# SubParty

Windows portable desktop app: burned-in **Simplified Chinese** hardsubs → **English** softsubs. Watch live while processing; continuously write `{videoname}.en.srt` next to the source video.

**Repo:** https://github.com/jenishrankca/SubParty

Free stack only: **Electron + React** UI, **Python** sidecar (**ffmpeg** → **RapidOCR** / PP-OCR Chinese models via ONNX → **Argos Translate**).

## Layout

```
SubParty/
  app/          Electron + Vite + React UI
  engine/       FastAPI OCR/translate pipeline
  bin/          Portable ffmpeg.exe + ffprobe.exe
  scripts/      start-engine.cmd / start-app.cmd
  samples/      Drop test videos here
  _refs/        Cloned reference repos (not part of runtime)
```

## Prerequisites

- Windows 10/11
- Python 3.13+ (tested with `C:\Python313\python.exe`)
- Node.js 20+ / npm
- NVIDIA GPU optional (RTX ~6GB); CPU OCR works, slower

## First-time setup

```bat
:: Portable ffmpeg (not in git — GitHub 100MB limit)
c:\Users\jenis\App\SubParty\scripts\download-ffmpeg.cmd

cd c:\Users\jenis\App\SubParty\engine
C:\Python313\python.exe -m venv .venv
.venv\Scripts\pip install -r requirements.txt

cd c:\Users\jenis\App\SubParty\app
npm install
npm install-scripts approve electron
npm install-scripts approve esbuild
npm install
```

First process run may download OCR/Argos model packs (internet required once). Engine listens on `http://127.0.0.1:18765`.

## Run (dev)

```bat
cd c:\Users\jenis\App\SubParty\app
npm run dev
```

Electron starts the engine at `http://127.0.0.1:18765`, then opens the UI.

Or use:

```bat
c:\Users\jenis\App\SubParty\scripts\start-app.cmd
```

Engine only:

```bat
c:\Users\jenis\App\SubParty\scripts\start-engine.cmd
```

## Use

1. **Pick video** (MP4 / MKV / TS with Chinese hardsubs)
2. **Process** — frames sampled from the bottom subtitle band → OCR → ZH→EN
3. Watch in the embedded player; English overlay updates as cues arrive
4. `{videoname}.en.srt` grows beside the video for VLC / YouTube
5. **Open folder** to reveal the SRT

Sample hardsub clip: `samples\demo_zh_hardsub.mp4`.

## Portable notes

- Run `scripts\download-ffmpeg.cmd` once so `bin\ffmpeg.exe` / `ffprobe.exe` exist (not committed).
- Zip the folder after `engine\.venv` + `app\node_modules` + `bin\` binaries exist, or re-run setup on the target PC.
- No installer required for v1.

## Out of scope (v1)

Burn-in English, multi-language, batch folders, pause/resume, cue editing.
