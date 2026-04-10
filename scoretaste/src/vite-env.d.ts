/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_PILOT_MONITOR?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
