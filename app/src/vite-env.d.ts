/// <reference types="vite/client" />

interface EngineHealthResult {
  ok: boolean;
  version?: string | null;
  mode?: string | null;
  error?: string;
  body?: Record<string, unknown>;
}

interface EngineFetchResult {
  ok: boolean;
  status: number;
  raw: string;
  error?: string;
}

interface SubPartyApi {
  pickVideo: () => Promise<string | null>;
  pickVideos: () => Promise<string[]>;
  openFolder: (targetPath: string) => Promise<void>;
  engineUrl: () => Promise<string>;
  engineToken: () => Promise<string>;
  engineHealth: () => Promise<EngineHealthResult>;
  engineRestart: () => Promise<{ ok: boolean; error?: string }>;
  engineFetch: (pathName: string, init?: { method?: string; body?: string }) => Promise<EngineFetchResult>;
  pathToFileUrl: (filePath: string) => Promise<string>;
}

interface Window {
  subparty: SubPartyApi;
}
