/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_PORTAL_URL?: string;
  // Themeable portal (S0-frontend-1): primary accent + app title.
  readonly VITE_PORTAL_ACCENT?: string;
  readonly VITE_PORTAL_TITLE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
