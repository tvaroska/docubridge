/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DASHBOARD_URL?: string;
  // The provider-portal app's URL, for the per-carrier checklist link (S0-frontend-1).
  readonly VITE_PORTAL_APP_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
