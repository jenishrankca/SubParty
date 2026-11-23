# SubParty portable packaging

## Dev / source portable zip

1. Clone or copy the repo.
2. Run `scripts\download-ffmpeg.cmd` (puts `bin\ffmpeg.exe` + `ffprobe.exe`).
3. Create the engine venv and install deps:

```bat
cd engine
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

4. Install the app deps:

```bat
cd app
npm install
npm install-scripts approve electron
npm install-scripts approve esbuild
npm install
```

5. Launch:

```bat
scripts\start-app.cmd
```

or `cd app && npm run dev`.

## Secure local engine

- Electron writes/reads `.subparty\engine.token` and passes `SUBPARTY_ENGINE_TOKEN` to the Python sidecar.
- API calls (except `/health`) require `Authorization: Bearer <token>` (SSE uses `?token=`).
- Engine binds `127.0.0.1:18765` only.
- **webSecurity:** enabled. Local video uses privileged `subparty://media/...` protocol (not `webSecurity: false`).
- **Engine IPC:** renderer health + REST go through Electron `engine-fetch` / `engine-health` (avoids browser CORS “Failed to fetch” when the sidecar is slow to bind). SSE still uses `http://127.0.0.1:18765` with `?token=`.
- **Startup:** Electron frees stale listeners on `:18765`, refuses `0.x` health, respawns the venv python, and exposes **Retry engine** in the UI.
- **Residual risk:** the custom scheme can read any path the app user can access if a compromised renderer crafted URLs; preload still has no Node integration, and paths normally come only from the file picker / IPC. Do not load untrusted remote pages in the BrowserWindow.

## Packaging path (working)

```bat
cd app
npm run build
```

Then run packaged-style from source tree:

```bat
cd app
npx electron .
```

Electron resolves `projectRoot()` two levels above `app/electron`, starts `engine\.venv\Scripts\python.exe engine\main.py`, and loads `app\dist\index.html` when not in Vite dev mode.

For a friend-laptop zip: include `app\dist`, `app\electron`, `engine` (with `.venv`), `bin`, `scripts`, and root `.subparty` ignore patterns — **do not** ship your personal `engine.token`; a new token is minted on first run.

## Optional translators

- **Argos** — default, auto-downloaded on first use.
- **NLLB** — `pip install transformers torch sentencepiece` then select NLLB in the UI / Models tab.
- **Qwen** — set `SUBPARTY_QWEN_MODEL` to a local model id/path (stub wired; not downloaded by default).
