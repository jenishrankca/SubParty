const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("subparty", {
  pickVideo: () => ipcRenderer.invoke("pick-video"),
  pickVideos: () => ipcRenderer.invoke("pick-videos"),
  openFolder: (targetPath) => ipcRenderer.invoke("open-folder", targetPath),
  engineUrl: () => ipcRenderer.invoke("engine-url"),
  engineToken: () => ipcRenderer.invoke("engine-token"),
  engineHealth: () => ipcRenderer.invoke("engine-health"),
  engineRestart: () => ipcRenderer.invoke("engine-restart"),
  engineFetch: (pathName, init) => ipcRenderer.invoke("engine-fetch", pathName, init),
  pathToFileUrl: (filePath) => ipcRenderer.invoke("path-to-file-url", filePath),
});
