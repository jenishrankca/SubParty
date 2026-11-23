const { app, BrowserWindow, dialog, ipcMain, shell, protocol, net } = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const { pathToFileURL } = require("url");

const ENGINE_PORT = 18765;
const ENGINE_URL = `http://127.0.0.1:${ENGINE_PORT}`;
const isDev = !app.isPackaged;

let mainWindow = null;
let engineProc = null;
let engineToken = "";

// Custom scheme so renderer can play local videos with webSecurity: true
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

function readToken() {
  try {
    const p = tokenPath();
    if (fs.existsSync(p)) {
      engineToken = fs.readFileSync(p, "utf8").trim();
      return engineToken;
    }
  } catch (err) {
    console.error("[engine] token read failed", err);
  }
  return "";
}

function resolvePython() {
  const venvPy = path.join(projectRoot(), "engine", ".venv", "Scripts", "python.exe");
  if (fs.existsSync(venvPy)) return venvPy;
  return process.env.SUBPARTY_PYTHON || "python";
}

function startEngine() {
  if (engineProc) return;
  const py = resolvePython();
  const engineDir = path.join(projectRoot(), "engine");
  const mainPy = path.join(engineDir, "main.py");
  const tp = tokenPath();
  fs.mkdirSync(path.dirname(tp), { recursive: true });
  if (!fs.existsSync(tp) || !fs.readFileSync(tp, "utf8").trim()) {
    const crypto = require("crypto");
    fs.writeFileSync(tp, crypto.randomBytes(32).toString("base64url"), "utf8");
  }
  engineToken = readToken();
  engineProc = spawn(py, [mainPy], {
    cwd: engineDir,
    env: {
      ...process.env,
      PYTHONUTF8: "1",
      SUBPARTY_ENGINE_TOKEN: engineToken,
    },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  engineProc.stdout.on("data", (d) => console.log(`[engine] ${d}`));
  engineProc.stderr.on("data", (d) => console.error(`[engine] ${d}`));
  engineProc.on("exit", (code) => {
    console.log(`[engine] exited ${code}`);
    engineProc = null;
  });
}

function waitForEngine(timeoutMs = 60000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      const req = http.get(`${ENGINE_URL}/health`, (res) => {
        res.resume();
        if (res.statusCode === 200) {
          readToken();
          resolve(true);
        } else retry();
      });
      req.on("error", retry);
      function retry() {
        if (Date.now() - start > timeoutMs) {
          reject(new Error("Engine did not become ready in time"));
          return;
        }
        setTimeout(tick, 400);
      }
    };
    tick();
  });
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
      // Local media via privileged `subparty://` protocol — keep Chromium webSecurity on.
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
  // subparty://media/<absolute-path>  → local file stream
  protocol.handle("subparty", (request) => {
    try {
      const u = new URL(request.url);
      // Expect host "media" and pathname = absolute path (/C:/... or /Users/...)
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
});

app.on("window-all-closed", () => {
  if (engineProc) {
    engineProc.kill();
    engineProc = null;
  }
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  if (engineProc) {
    engineProc.kill();
    engineProc = null;
  }
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
ipcMain.handle("engine-token", async () => {
  if (!engineToken) readToken();
  return engineToken;
});

ipcMain.handle("path-to-file-url", async (_e, filePath) => {
  const abs = path.resolve(filePath);
  // Prefer custom scheme for secure local playback
  const encoded = encodeURI(abs.replace(/\\/g, "/"));
  return `subparty://media/${encoded}`;
});
