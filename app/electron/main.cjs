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

function projectRoot() {
  // app/ is one level under SubParty when running from source
  return path.resolve(__dirname, "..", "..");
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
  engineProc = spawn(py, [mainPy], {
    cwd: engineDir,
    env: { ...process.env, PYTHONUTF8: "1" },
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
        if (res.statusCode === 200) resolve(true);
        else retry();
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
    width: 1180,
    height: 780,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: "#0f1419",
    title: "SubParty",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: false, // allow file:// video playback
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
    filters: [
      { name: "Video", extensions: ["mp4", "mkv", "ts", "webm", "avi", "mov"] },
    ],
  });
  if (result.canceled || !result.filePaths[0]) return null;
  return result.filePaths[0];
});

ipcMain.handle("open-folder", async (_e, targetPath) => {
  if (!targetPath) return;
  const dir = fs.existsSync(targetPath) && fs.statSync(targetPath).isDirectory()
    ? targetPath
    : path.dirname(targetPath);
  await shell.openPath(dir);
});

ipcMain.handle("engine-url", async () => ENGINE_URL);

ipcMain.handle("path-to-file-url", async (_e, filePath) => {
  const normalized = path.resolve(filePath).replace(/\\/g, "/");
  // Windows drive letter
  if (/^[A-Za-z]:/.test(normalized)) {
    return `file:///${normalized}`;
  }
  return `file://${normalized}`;
});
