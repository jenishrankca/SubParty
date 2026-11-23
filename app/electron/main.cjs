const { app, BrowserWindow, dialog, ipcMain, shell, protocol } = require("electron");
const path = require("path");
const { spawn, exec, execFile } = require("child_process");
const http = require("http");
const fs = require("fs");
const { Readable } = require("stream");
const crypto = require("crypto");
const { promisify } = require("util");

const execAsync = promisify(exec);
const execFileAsync = promisify(execFile);

const UNSUPPORTED_AUDIO = new Set([
  "ac3",
  "eac3",
  "truehd",
  "dts",
  "dca",
  "mlp",
  "pcm_bluray",
]);

const ENGINE_PORT = 18765;
const ENGINE_URL = `http://127.0.0.1:${ENGINE_PORT}`;
const isDev = !app.isPackaged;

let mainWindow = null;
let engineProc = null;
let engineToken = "";
let engineStarting = false;

protocol.registerSchemesAsPrivileged([
  {
    scheme: "subparty",
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      stream: true,
      bypassCSP: true,
      corsEnabled: true,
    },
  },
]);

function projectRoot() {
  return path.resolve(__dirname, "..", "..");
}

function tokenPath() {
  // App-local data/ (not beside videos). Migrate from legacy repo .subparty/.
  return path.join(projectRoot(), "data", "engine.token");
}

function legacyTokenPath() {
  return path.join(projectRoot(), ".subparty", "engine.token");
}

function ensureToken() {
  const tp = tokenPath();
  fs.mkdirSync(path.dirname(tp), { recursive: true });
  let raw = "";
  try {
    if (fs.existsSync(tp)) raw = fs.readFileSync(tp, "utf8").trim();
  } catch {
    raw = "";
  }
  if (!raw) {
    try {
      const legacy = legacyTokenPath();
      if (fs.existsSync(legacy)) {
        raw = fs.readFileSync(legacy, "utf8").trim();
        if (raw) fs.writeFileSync(tp, raw, "utf8");
      }
    } catch {
      /* ignore */
    }
  }
  // Reject leftover short/test tokens from debugging
  if (!raw || raw.length < 20 || raw === "test-fix-token") {
    raw = crypto.randomBytes(32).toString("base64url");
    fs.writeFileSync(tp, raw, "utf8");
  }
  engineToken = raw;
  return engineToken;
}

function readToken() {
  return ensureToken();
}

function resolvePython() {
  const venvPy = path.join(projectRoot(), "engine", ".venv", "Scripts", "python.exe");
  if (fs.existsSync(venvPy)) return venvPy;
  return process.env.SUBPARTY_PYTHON || "python";
}

async function listeningPids() {
  const pids = new Set();
  if (process.platform !== "win32") return pids;
  try {
    const { stdout } = await execAsync(`netstat -ano | findstr :${ENGINE_PORT}`, {
      encoding: "utf8",
      windowsHide: true,
    });
    for (const line of String(stdout || "").split(/\r?\n/)) {
      if (!/LISTENING/i.test(line)) continue;
      const parts = line.trim().split(/\s+/);
      const pid = parts[parts.length - 1];
      if (pid && /^\d+$/.test(pid) && pid !== "0") pids.add(Number(pid));
    }
  } catch {
    /* none listening or findstr exit 1 */
  }
  return pids;
}

async function freeEnginePort({ allowPid = null } = {}) {
  const pids = await listeningPids();
  for (const pid of pids) {
    if (allowPid && pid === allowPid) continue;
    try {
      await execFile(
        "taskkill",
        ["/PID", String(pid), "/F"],
        { windowsHide: true }
      );
      console.log(`[engine] killed pid ${pid} on :${ENGINE_PORT}`);
    } catch {
      /* ignore */
    }
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitPortFree(timeoutMs = 5000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const pids = await listeningPids();
    if (pids.size === 0) return true;
    await sleep(200);
  }
  const left = await listeningPids();
  return left.size === 0;
}

async function stopEngineProcess() {
  const proc = engineProc;
  engineProc = null;
  if (!proc) return;
  try {
    if (process.platform === "win32" && proc.pid) {
      await execFile("taskkill", ["/PID", String(proc.pid), "/T", "/F"], {
        windowsHide: true,
      });
    } else {
      proc.kill();
    }
  } catch {
    /* ignore */
  }
}

async function startEngine() {
  if (engineProc || engineStarting) return;
  engineStarting = true;
  try {
    // Clear strangers on the port before spawn — never kill after we own the child
    await freeEnginePort();
    await waitPortFree(4000);
    const py = resolvePython();
    const engineDir = path.join(projectRoot(), "engine");
    const mainPy = path.join(engineDir, "main.py");
    const token = ensureToken();
    console.log(`[engine] spawning ${py} (token ${token.slice(0, 6)}…)`);
    engineProc = spawn(py, ["-u", mainPy], {
      cwd: engineDir,
      env: {
        ...process.env,
        PYTHONUTF8: "1",
        PYTHONUNBUFFERED: "1",
        SUBPARTY_ENGINE_TOKEN: token,
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    const childPid = engineProc.pid;
    engineProc.stdout.on("data", (d) => console.log(`[engine] ${d}`));
    engineProc.stderr.on("data", (d) => console.error(`[engine] ${d}`));
    engineProc.on("exit", (code) => {
      console.log(`[engine] exited ${code} (pid ${childPid})`);
      if (engineProc && engineProc.pid === childPid) engineProc = null;
    });
  } finally {
    engineStarting = false;
  }
}

async function restartEngine() {
  const previous = engineProc;
  await stopEngineProcess();
  // Only reclaim strangers — our child was already taskkilled
  await freeEnginePort({ allowPid: previous && previous.pid ? previous.pid : null });
  await waitPortFree(4000);
  await startEngine();
}

function httpGetJson(urlPath, timeoutMs = 2500) {
  return new Promise((resolve, reject) => {
    const req = http.get(`${ENGINE_URL}${urlPath}`, { timeout: timeoutMs }, (res) => {
      let raw = "";
      res.on("data", (c) => {
        raw += c;
      });
      res.on("end", () => {
        resolve({ status: res.statusCode || 0, raw });
      });
    });
    req.on("timeout", () => {
      req.destroy();
      reject(new Error("timeout"));
    });
    req.on("error", reject);
  });
}

function httpJson(method, urlPath, bodyObj, token) {
  return new Promise((resolve, reject) => {
    const body = bodyObj === undefined ? null : JSON.stringify(bodyObj);
    const u = new URL(`${ENGINE_URL}${urlPath}`);
    const req = http.request(
      {
        hostname: u.hostname,
        port: u.port,
        path: u.pathname + u.search,
        method,
        headers: {
          ...(body ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        timeout: 1_800_000, // model downloads can take a while
      },
      (res) => {
        let raw = "";
        res.on("data", (c) => {
          raw += c;
        });
        res.on("end", () => {
          resolve({ status: res.statusCode || 0, raw, ok: (res.statusCode || 0) >= 200 && (res.statusCode || 0) < 300 });
        });
      }
    );
    req.on("timeout", () => {
      req.destroy();
      reject(new Error("timeout"));
    });
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

async function waitForEngine(timeoutMs = 90000) {
  const start = Date.now();
  let attempt = 0;
  while (Date.now() - start < timeoutMs) {
    attempt += 1;
    try {
      const { status, raw } = await httpGetJson("/health", 3000);
      if (status === 200) {
        let ver = "";
        try {
          ver = String(JSON.parse(raw).version || "");
        } catch {
          ver = "";
        }
        if (ver.startsWith("0.")) {
          console.error(`[engine] outdated ${ver} on port — restarting our sidecar`);
          await restartEngine();
        } else {
          ensureToken();
          console.log(`[engine] ready version=${ver || "unknown"}`);
          return true;
        }
      }
    } catch (err) {
      if (attempt === 1 || attempt % 10 === 0) {
        console.log(`[engine] waiting… (${err && err.message ? err.message : err})`);
      }
      // If our child died, respawn (async — do not block event loop)
      if (!engineProc && !engineStarting) await startEngine();
    }
    await sleep(500);
  }
  throw new Error("Engine did not become ready in time");
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: "#0f1419",
    title: "SubParty",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  if (isDev) {
    mainWindow.loadURL("http://127.0.0.1:5173");
  } else {
    mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

function mimeForMedia(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".mp4" || ext === ".m4v") return "video/mp4";
  if (ext === ".webm") return "video/webm";
  if (ext === ".mkv") return "video/x-matroska";
  if (ext === ".mov") return "video/quicktime";
  if (ext === ".ts" || ext === ".m2ts") return "video/mp2t";
  if (ext === ".avi") return "video/x-msvideo";
  if (ext === ".m4a") return "audio/mp4";
  if (ext === ".mp3") return "audio/mpeg";
  return "application/octet-stream";
}

function resolveMediaPath(requestUrl) {
  const u = new URL(requestUrl);
  let filePath = decodeURIComponent(u.pathname || "");
  if (process.platform === "win32" && filePath.startsWith("/")) {
    filePath = filePath.slice(1);
  }
  return path.normalize(filePath);
}

function registerMediaProtocol() {
  // Explicit Range + Content-Type so Chromium can seek and decode audio on large MP4s.
  // (net.fetch(file://) alone was flaky for some AAC edit-list / large-file cases.)
  protocol.handle("subparty", async (request) => {
    try {
      const filePath = resolveMediaPath(request.url);
      if (!fs.existsSync(filePath)) {
        return new Response("Not found", { status: 404 });
      }
      const stat = fs.statSync(filePath);
      const fileSize = stat.size;
      const mime = mimeForMedia(filePath);
      const rangeHeader = request.headers.get("Range") || request.headers.get("range");

      if (rangeHeader) {
        const m = /bytes=(\d*)-(\d*)/.exec(rangeHeader);
        if (!m) {
          return new Response("Invalid Range", { status: 416 });
        }
        let start = m[1] ? parseInt(m[1], 10) : 0;
        let end = m[2] ? parseInt(m[2], 10) : fileSize - 1;
        if (Number.isNaN(start) || Number.isNaN(end) || start < 0 || end >= fileSize || start > end) {
          return new Response("Range Not Satisfiable", {
            status: 416,
            headers: { "Content-Range": `bytes */${fileSize}` },
          });
        }
        const chunkSize = end - start + 1;
        const nodeStream = fs.createReadStream(filePath, { start, end });
        return new Response(Readable.toWeb(nodeStream), {
          status: 206,
          headers: {
            "Content-Type": mime,
            "Content-Length": String(chunkSize),
            "Content-Range": `bytes ${start}-${end}/${fileSize}`,
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
          },
        });
      }

      const nodeStream = fs.createReadStream(filePath);
      return new Response(Readable.toWeb(nodeStream), {
        status: 200,
        headers: {
          "Content-Type": mime,
          "Content-Length": String(fileSize),
          "Accept-Ranges": "bytes",
          "Cache-Control": "no-cache",
        },
      });
    } catch (err) {
      console.error("[subparty protocol]", err);
      return new Response("Bad request", { status: 400 });
    }
  });
}

function ffmpegBin() {
  const bundled = path.join(projectRoot(), "bin", "ffmpeg.exe");
  if (fs.existsSync(bundled)) return bundled;
  return process.env.SUBPARTY_FFMPEG || "ffmpeg";
}

function ffprobeBin() {
  const bundled = path.join(projectRoot(), "bin", "ffprobe.exe");
  if (fs.existsSync(bundled)) return bundled;
  return process.env.SUBPARTY_FFPROBE || "ffprobe";
}

function playbackCacheDir() {
  const d = path.join(projectRoot(), "data", "playback");
  fs.mkdirSync(d, { recursive: true });
  return d;
}

async function diagnosePlayback(filePath) {
  const { stdout, stderr } = await execFileAsync(
    ffprobeBin(),
    [
      "-hide_banner",
      "-show_entries",
      "stream=index,codec_type,codec_name,channels,sample_rate,bit_rate",
      "-of",
      "json",
      filePath,
    ],
    { encoding: "utf8", windowsHide: true, maxBuffer: 8 * 1024 * 1024 }
  );
  let data = {};
  try {
    data = JSON.parse(stdout || "{}");
  } catch {
    data = {};
  }
  const streams = Array.isArray(data.streams) ? data.streams : [];
  const audio = streams.filter((s) => s.codec_type === "audio");
  const editListBroken = /edit list/i.test(stderr || "");
  const bad = audio
    .map((a) => String(a.codec_name || "").toLowerCase())
    .filter((c) => UNSUPPORTED_AUDIO.has(c));
  const needsRemux = editListBroken || bad.length > 0;
  let reason = null;
  if (editListBroken) reason = "mp4-aac-edit-list";
  else if (bad.length) reason = `unsupported-audio:${bad.join(",")}`;
  return {
    audio,
    editListBroken,
    unsupportedAudio: bad,
    needsRemux,
    reencodeAudio: bad.length > 0,
    reason,
  };
}

async function ensurePlayableMedia(filePath) {
  const abs = path.resolve(filePath);
  const diag = await diagnosePlayback(abs);
  if (!diag.needsRemux) {
    return { playPath: abs, remuxed: false, cached: false, ...diag };
  }
  const st = fs.statSync(abs);
  const key = crypto
    .createHash("sha1")
    .update(`${abs}|${st.size}|${Math.floor(st.mtimeMs)}`)
    .digest("hex")
    .slice(0, 16);
  const out = path.join(playbackCacheDir(), `${key}.mp4`);
  if (fs.existsSync(out) && fs.statSync(out).size > 1000) {
    return { playPath: out, remuxed: true, cached: true, ...diag };
  }
  const args = diag.reencodeAudio
    ? ["-y", "-hide_banner", "-loglevel", "error", "-i", abs, "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out]
    : ["-y", "-hide_banner", "-loglevel", "error", "-i", abs, "-c", "copy", "-movflags", "+faststart", out];
  console.log(`[playback] fixing ${path.basename(abs)} → ${path.basename(out)} (${diag.reason})`);
  await execFileAsync(ffmpegBin(), args, {
    windowsHide: true,
    maxBuffer: 16 * 1024 * 1024,
  });
  return { playPath: out, remuxed: true, cached: false, ...diag };
}

function toSubpartyUrl(filePath) {
  const abs = path.resolve(filePath);
  const encoded = encodeURI(abs.replace(/\\/g, "/"));
  return `subparty://media/${encoded}`;
}

app.whenReady().then(async () => {
  registerMediaProtocol();
  await startEngine();
  try {
    await waitForEngine();
  } catch (err) {
    console.error(err);
  }
  createWindow();
  // Keep trying in background if startup timed out
  if (!(await healthOk())) {
    setInterval(() => {
      if (!engineProc && !engineStarting) void startEngine();
    }, 4000);
  }
});

async function healthOk() {
  try {
    const { status, raw } = await httpGetJson("/health", 2000);
    if (status !== 200) return false;
    const ver = String(JSON.parse(raw).version || "");
    return !ver.startsWith("0.");
  } catch {
    return false;
  }
}

app.on("window-all-closed", () => {
  void stopEngineProcess().finally(() => {
    if (process.platform !== "darwin") app.quit();
  });
});

app.on("before-quit", () => {
  void stopEngineProcess();
});

ipcMain.handle("pick-video", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Pick a video with Chinese hardsubs",
    properties: ["openFile"],
    filters: [{ name: "Video", extensions: ["mp4", "mkv", "ts", "webm", "avi", "mov"] }],
  });
  if (result.canceled || !result.filePaths[0]) return null;
  return result.filePaths[0];
});

ipcMain.handle("pick-videos", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Batch: pick videos with Chinese hardsubs",
    properties: ["openFile", "multiSelections"],
    filters: [{ name: "Video", extensions: ["mp4", "mkv", "ts", "webm", "avi", "mov"] }],
  });
  if (result.canceled || !result.filePaths.length) return [];
  return result.filePaths;
});

ipcMain.handle("pick-json", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Pick annotated gold JSON for QA",
    properties: ["openFile"],
    filters: [{ name: "JSON", extensions: ["json"] }],
  });
  if (result.canceled || !result.filePaths[0]) return null;
  return result.filePaths[0];
});

ipcMain.handle("open-folder", async (_e, targetPath) => {
  if (!targetPath) return;
  const dir =
    fs.existsSync(targetPath) && fs.statSync(targetPath).isDirectory()
      ? targetPath
      : path.dirname(targetPath);
  await shell.openPath(dir);
});

ipcMain.handle("engine-url", async () => ENGINE_URL);
ipcMain.handle("engine-token", async () => ensureToken());

ipcMain.handle("engine-health", async () => {
  try {
    const { status, raw } = await httpGetJson("/health", 3000);
    if (status !== 200) return { ok: false, error: `HTTP ${status}`, version: null };
    const body = JSON.parse(raw);
    return { ok: true, version: body.version || null, mode: body.mode || null, body };
  } catch (err) {
    return { ok: false, error: err && err.message ? err.message : String(err), version: null };
  }
});

ipcMain.handle("engine-restart", async () => {
  try {
    await restartEngine();
    await waitForEngine(60000);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err && err.message ? err.message : String(err) };
  }
});

// Renderer API proxy — avoids CORS / Failed to fetch when sidecar is up
ipcMain.handle("engine-fetch", async (_e, pathName, init = {}) => {
  const method = (init.method || "GET").toUpperCase();
  const token = ensureToken();
  let bodyObj = undefined;
  if (init.body != null && init.body !== "") {
    try {
      bodyObj = typeof init.body === "string" ? JSON.parse(init.body) : init.body;
    } catch {
      bodyObj = init.body;
    }
  }
  try {
    // Ensure engine is alive for mutating calls
    if (!(await healthOk()) && (method === "POST" || method === "PATCH" || method === "DELETE")) {
      if (!engineProc) await startEngine();
      await waitForEngine(30000);
    }
    const result = await httpJson(method, pathName, method === "GET" || method === "HEAD" ? undefined : bodyObj, token);
    return result;
  } catch (err) {
    return {
      ok: false,
      status: 0,
      raw: "",
      error: err && err.message ? err.message : String(err),
    };
  }
});

ipcMain.handle("path-to-file-url", async (_e, filePath) => {
  return toSubpartyUrl(filePath);
});

ipcMain.handle("prepare-video", async (_e, filePath) => {
  try {
    const prepared = await ensurePlayableMedia(filePath);
    return {
      ok: true,
      url: toSubpartyUrl(prepared.playPath),
      source_path: path.resolve(filePath),
      play_path: prepared.playPath,
      remuxed: Boolean(prepared.remuxed),
      cached: Boolean(prepared.cached),
      reason: prepared.reason || null,
      audio: prepared.audio || [],
      edit_list_broken: Boolean(prepared.editListBroken),
    };
  } catch (err) {
    // Fall back to original so video still opens; sound may be missing
    console.error("[playback] prepare failed", err);
    return {
      ok: false,
      url: toSubpartyUrl(filePath),
      source_path: path.resolve(filePath),
      play_path: path.resolve(filePath),
      remuxed: false,
      error: err && err.message ? err.message : String(err),
    };
  }
});
