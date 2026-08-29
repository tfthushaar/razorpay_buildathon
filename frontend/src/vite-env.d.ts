/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  /** Local builds default to ollama; the deployed build sets this to a provider the host can run. */
  readonly VITE_DEFAULT_PROVIDER?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
