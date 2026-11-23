const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");

const ENGINE_PORT = 18765;
const ENGINE_URL = `http://127.0.0.1:${ENGINE_PORT}`;
const isDev = !app.isPackaged;

let mainWindow = null;
let engineProc = null;
let engineToken = "";

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
  // Ensure token exists before spawn so middleware and UI share it
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
      webSecurity: false,
    },
  });

  if (isDev) {
    mainWindow.loadURL("http://127.0.0.1:5173");
  } else {
    mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

app.whenReady().then(async () => {
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
  const normalized = path.resolve(filePath).replace(/\\/g, "/");
  if (/^[A-Za-z]:/.test(normalized)) return `file:///${normalized}`;
  return `file://${normalized}`;
});
