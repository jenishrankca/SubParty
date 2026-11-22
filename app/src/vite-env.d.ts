/// <reference types="vite/client" />

interface SubPartyCue {
  index: number;
  start: number;
  end: number;
  text_zh: string;
  text_en: string;
}

interface SubPartyApi {
  pickVideo: () => Promise<string | null>;
  openFolder: (targetPath: string) => Promise<void>;
  engineUrl: () => Promise<string>;
  pathToFileUrl: (filePath: string) => Promise<string>;
}

interface Window {
  subparty: SubPartyApi;
}
