const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("subparty", {
  pickVideo: () => ipcRenderer.invoke("pick-video"),
  openFolder: (targetPath) => ipcRenderer.invoke("open-folder", targetPath),
  engineUrl: () => ipcRenderer.invoke("engine-url"),
  pathToFileUrl: (filePath) => ipcRenderer.invoke("path-to-file-url", filePath),
});
