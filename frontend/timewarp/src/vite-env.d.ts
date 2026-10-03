/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_TIMEWARP_URL?: string;
  readonly VITE_PORTAL_APP_URL?: string;
  readonly VITE_CONSOLE_APP_URL?: string;
  readonly VITE_DASHBOARD_APP_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
