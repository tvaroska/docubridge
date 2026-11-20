# DocuBridge Design System Contract (`.stitch/DESIGN.md`)

**Visual Tier:** Tier 2 — Reusable Showcase Platform (**Google Cloud / Material 3 `GM3` Light**)
**Scope:** Standalone interactive demo consoles (`demo/enterprise-demo.*`, `demo/ebc-live.*`), MUI v6 frontend workspace (`frontend/*`), and hero architecture graphic (`docs/assets/architecture_card.svg`).

---

## 1. Visual Identity & Surfaces

- **Default Showcase Surface (`/demo`, `/console`, `frontend/*`):** Clean `#f8f9fa` canvas (`--surface`), `#ffffff` elevated cards (`--surface-container`) with `1px solid #dadce0` hairline borders (`--outline-variant`), `#f1f3f4` muted containers (`--surface-container-low`), `#202124` primary ink (`--on-surface`), `#5f6368` secondary ink (`--on-surface-variant`), and `#1a73e8` Google Blue primary accent (`--primary`). Radii: `8px`–`12px` for workspace panels, `6px`–`8px` for buttons and chips.
- **Google Cloud Console Variant (`/ebc`):** Crisp `#ffffff` top app bar with the 4-color Google Cloud mark and scope chip, flat `2px` `#1a73e8` active tabs, `4px`–`8px` radii, dense tabular KPI strips, and explicit amber **`SIMULATED IN-PROCESS SHIM`** badges wherever control-plane panels (Skill Registry, Agent Registry, Agent Gateway) run as local in-process shims rather than managed platform APIs.
- **Frontend Stack Governance (Non-Rewrite Invariant):** The 4 Vite workspaces (`frontend/agent-console`, `frontend/ops-dashboard`, `frontend/provider-portal`, `frontend/timewarp`) plus `@docubridge/shared` remain on **MUI v6 + React 18 + Vite + Vitest** (no forced Tailwind rewrite). Zone-specific primary accents are preserved via `makeTheme(primaryMain)`:
  - `provider-portal` (External Portal): `#1f6feb`
  - `agent-console` (Service Console): `#6f42c1`
  - `ops-dashboard` (Ops Dashboard): `#1a73e8`
  - `timewarp` (Presenter Stage): `#0b7285`

---

## 2. Semantic CSS Token Table

All standalone CSS stylesheets (`demo/enterprise-demo.css`, `demo/ebc-live.css`) and `@docubridge/shared` (`frontend/shared/src/theme.ts`) expose the same canonical Tier-2 GM3 Light custom properties so adapting DocuBridge for a customer demo is a single `:root` token swap:

| Token | Value (GM3 Light) | Role |
| :--- | :--- | :--- |
| `--surface` | `#f8f9fa` | Page canvas background |
| `--surface-container` | `#ffffff` | Elevated cards, panes, top bar |
| `--surface-container-low` | `#f1f3f4` | Dense table headers, subtle pills, secondary strips |
| `--on-surface` | `#202124` | Primary text ink |
| `--on-surface-variant` | `#5f6368` | Secondary labels, captions, metadata |
| `--outline-variant` | `#dadce0` | `1px` hairline card/table dividers |
| `--primary` | `#1a73e8` | Primary interactive action & focus ring |
| `--on-primary` | `#ffffff` | Foreground ink on primary fill |
| `--status-ok-fg` / `--status-ok-bg` | `#137333` / `#e6f4ea` | Verified / satisfied / PASS chip |
| `--status-warn-fg` / `--status-warn-bg` | `#b06000` / `#fef7e0` | Required / warning / simulated-shim chip |
| `--status-err-fg` / `--status-err-bg` | `#c5221f` / `#fce8e6` | Rejected / error / policy denial chip |
| `--status-info-fg` / `--status-info-bg` | `#1967d2` / `#e8f0fe` | Waived / informational chip |
| `--font-sans` | `"Google Sans", "Inter Variable", "Inter", system-ui, -apple-system, sans-serif` | Primary UI & display stack |
| `--font-mono` | `"Roboto Mono", "JetBrains Mono Variable", "JetBrains Mono", ui-monospace, monospace` | Monospace hashes, SQL, telemetry, IDs |
| `--radius` | `0.5rem` | Default component border radius |

---

## 3. Offline Typography, Calm-UX & Asset Standards

1. **Zero External CDN Dependencies (BeyondCorp-Safe):** Bundle open variable fonts locally under `demo/fonts/` (`@fontsource-variable/inter` + `@fontsource-variable/jetbrains-mono` served at `/demo/fonts/fonts.css`) and import them in `@docubridge/shared`. Never reference `https://fonts.googleapis.com`, `https://fonts.gstatic.com`, or third-party script/CSS CDNs in `demo/` or `frontend/`.
2. **Tabular Numerals (`tabular-nums`):** Apply `font-variant-numeric: tabular-nums` to every KPI tile, monetary amount, LTV/DSCR metric, token/latency counter, table cell, and status strip so numbers align vertically and never jitter on live updates.
3. **Reduced Motion (`prefers-reduced-motion`):** Include `@media (prefers-reduced-motion: reduce)` across all stylesheets and MUI `CssBaseline` overrides to collapse non-essential animations and transitions to `0.01ms`. Reserve subtle motion strictly for active AI/async processing states.
4. **Distinct High-Contrast Favicons:** Every HTML entrypoint (`/demo`, `/ebc`, and all 4 Vite `frontend/*/index.html` apps) ships a `<link rel="icon" type="image/svg+xml" ...>` favicon with a distinct zone-accented glyph so browser tabs are scannable by icon.
5. **Hero Architecture SVG (`docs/assets/architecture_card.svg`):** Colorful Swiss Closed-Loop `1600×940` viewBox. Every `<text>` carries direct inline `font-family`, `font-size`, `font-weight`, and `fill` attributes (zero `<style>` classes, zero `<linearGradient>`, zero `<marker>`) so both browsers and ImageMagick `MSVG` render identically.

---

## Do Not Generate

- **No decorative edge accent bars on generic metric cards:** Do not add decorative left or top colored accent stripes to KPI cards or generic panels (colored left borders are reserved solely for semantic checklist/row status indicators).
- **No icon-in-circle KPI headers:** Keep KPI headers flat uppercase micro-labels with crisp tabular values underneath.
- **No warm/cream paper backgrounds:** Never use `#faf8f5`, `#fdf6ec`, or warm beige canvases in GM3 Light showcase views; stay on `#f8f9fa` / `#ffffff`.
- **No serif display headings:** Use the sans-serif geometric stack (`Google Sans` / `Inter Variable`) across all headings and body copy.
- **No sensory clutter:** Never add full-viewport `feTurbulence` grain/noise overlays, decorative staggered list reveal delays, pulsing animations on static badges, or unrequested dark-mode/font randomization.
- **No external font or CDN requests:** Never add `fonts.googleapis.com`, `fonts.gstatic.com`, `cdn.jsdelivr.net`, or `unpkg.com` links to HTML, CSS, or Vite configs.
- **No CSS `<style>` classes, `<linearGradient>`, or `<marker>` in `docs/assets/architecture_card.svg`:** All SVG typography must use inline presentation attributes for ImageMagick `MSVG` safety.
- **No `ADR-005` real customer or personal GCP identifiers:** Keep all sample names, entities, and domains 100% fictional.
