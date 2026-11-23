const { app, BrowserWindow, dialog, ipcMain, shell, protocol, net } = require("electron");
const path = require("path");
const { spawn, execSync } = require("child_process");
const http = require("http");
const fs = require("fs");
const { pathToFileURL } = require("url");
const crypto = require("crypto");

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

function listeningPids() {
  const pids = new Set();
  if (process.platform !== "win32") return pids;
  try {
    const out = execSync(`netstat -ano | findstr :${ENGINE_PORT}`, {
      encoding: "utf8",
      windowsHide: true,
    });
    for (const line of out.split(/\r?\n/)) {
      if (!/LISTENING/i.test(line)) continue;
      const parts = line.trim().split(/\s+/);
      const pid = parts[parts.length - 1];
      if (pid && /^\d+$/.test(pid) && pid !== "0") pids.add(Number(pid));
    }
  } catch {
    /* none */
  }
  return pids;
}

function freeEnginePort({ allowPid = null } = {}) {
  const pids = listeningPids();
  for (const pid of pids) {
    if (allowPid && pid === allowPid) continue;
    try {
      execSync(`taskkill /PID ${pid} /F`, { windowsHide: true, stdio: "ignore" });
      console.log(`[engine] killed pid ${pid} on :${ENGINE_PORT}`);
    } catch {
      /* ignore */
    }
  }
}

function sleepSync(ms) {
  const sab = new Int32Array(new SharedArrayBuffer(4));
  Atomics.wait(sab, 0, 0, ms);
}

function waitPortFree(timeoutMs = 5000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (listeningPids().size === 0) return true;
    sleepSync(200);
  }
  return listeningPids().size === 0;
}

function stopEngineProcess() {
  const proc = engineProc;
  engineProc = null;
  if (!proc) return;
  try {
    if (process.platform === "win32" && proc.pid) {
      execSync(`taskkill /PID ${proc.pid} /T /F`, { windowsHide: true, stdio: "ignore" });
    } else {
      proc.kill();
    }
  } catch {
    /* ignore */
  }
}

function startEngine() {
  if (engineProc || engineStarting) return;
  engineStarting = true;
  try {
    // Clear strangers on the port before spawn — never kill after we own the child
    freeEnginePort();
    waitPortFree(4000);
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

function restartEngine() {
  const previous = engineProc;
  stopEngineProcess();
  // Only reclaim strangers — our child was already taskkilled
  freeEnginePort({ allowPid: previous && previous.pid ? previous.pid : null });
  waitPortFree(4000);
  startEngine();
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
        timeout: 120000,
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
          restartEngine();
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
      // If our child died, respawn
      if (!engineProc && !engineStarting) startEngine();
    }
    await new Promise((r) => setTimeout(r, 500));
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

function registerMediaProtocol() {
  protocol.handle("subparty", (request) => {
    try {
      const u = new URL(request.url);
      let filePath = decodeURIComponent(u.pathname || "");
      if (process.platform === "win32" && filePath.startsWith("/")) {
        filePath = filePath.slice(1);
      }
      filePath = path.normalize(filePath);
      if (!fs.existsSync(filePath)) {
        return new Response("Not found", { status: 404 });
      }
      return net.fetch(pathToFileURL(filePath).toString());
    } catch (err) {
      console.error("[subparty protocol]", err);
      return new Response("Bad request", { status: 400 });
    }
  });
}

app.whenReady().then(async () => {
  registerMediaProtocol();
  startEngine();
  try {
    await waitForEngine();
  } catch (err) {
    console.error(err);
  }
  createWindow();
  // Keep trying in background if startup timed out
  if (!(await healthOk())) {
    setInterval(() => {
      if (!engineProc && !engineStarting) startEngine();
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
  stopEngineProcess();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  stopEngineProcess();
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
  restartEngine();
  try {
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
      if (!engineProc) startEngine();
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
  const abs = path.resolve(filePath);
  const encoded = encodeURI(abs.replace(/\\/g, "/"));
  return `subparty://media/${encoded}`;
});
