const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("subparty", {
  pickVideo: () => ipcRenderer.invoke("pick-video"),
  pickVideos: () => ipcRenderer.invoke("pick-videos"),
  openFolder: (targetPath) => ipcRenderer.invoke("open-folder", targetPath),
  engineUrl: () => ipcRenderer.invoke("engine-url"),
  engineToken: () => ipcRenderer.invoke("engine-token"),
  pathToFileUrl: (filePath) => ipcRenderer.invoke("path-to-file-url", filePath),
});
