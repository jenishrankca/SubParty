/// <reference types="vite/client" />

interface SubPartyApi {
  pickVideo: () => Promise<string | null>;
  pickVideos: () => Promise<string[]>;
  openFolder: (targetPath: string) => Promise<void>;
  engineUrl: () => Promise<string>;
  engineToken: () => Promise<string>;
  pathToFileUrl: (filePath: string) => Promise<string>;
}

interface Window {
  subparty: SubPartyApi;
}
