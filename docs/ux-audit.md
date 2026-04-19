# App Improvement Plan — strm2stl UX/Performance Audit

_Last updated: 2026-04-19_

## Context

Based on deep source code inspection. The app has been migrated to Vue components since this audit was first written. The template is now a minimal shell (`index.html`) with Vue SFC components under `app/client/static/js/vue/components/`.

The app has three tabs: **Explore** (map, region selection) → **Edit** (DEM visualization, layer settings) → **Extrude** (3D model generation + export).

---

## Findings

### Layout & Discoverability

**1. Duplicate region-creation entry points (high confusion)**
There are 4+ ways to create a region: `+ New Region` floating button on map, `+ New` in the floating regions panel, `+ New Region` collapsible in the sidebar, and `✏️ Draw bbox` in the sidebar edit view. Users can't tell which is canonical.

**2. Emoji-only floating map buttons (low discoverability)**
The 6 floating buttons (`🏔️ 📐 🌍 📋 🏷️ ⚙️`) have no text labels — only tooltips on hover. First-time users have no idea what they do.

**3. Sidebar has 3 ambiguous states**
The toggle cycles "Expanded → Hidden → Normal" but the button label only shows "Hide" / "Show". `sidebarListView`, `sidebarEditView`, and `sidebarTableView` display the same data three different ways — visually inconsistent.

**4. BBox editor strip is extremely cramped**
The row at the bottom of the DEM canvas packs N/S/E/W inputs + Reload + mini-map + Save + divider + colorbar + elevation range + settings button — all at 11px font in a single horizontal strip. Nearly unusable at normal DPI.

**5. Settings panel collapsed tab is too subtle**
When the settings panel is collapsed, a tiny `⚙ Settings` tab appears at the bottom right. Easy to miss — users who collapse the panel can't find it again.

**6. `Load DEM` is not clearly the primary CTA on the Edit tab**
The empty state says "Select a region and click **Load DEM** to begin" but the Load DEM button lives inside the right-panel strip, not obviously positioned as the primary action.

### Inconsistency & Technical Debt

**7. Cross-section collapsible uses `▶` while all others use `▼`** — N/A
The old `index.html` has been replaced by Vue components. This item may no longer apply; needs verification in the Vue cross-section component.

**8. `modelExaggeration` vs `modelDepthScale` — user confusion** — RESOLVED
The dual-input confusion no longer exists. Only a single `paramDepthScale` input remains in `DemSourceSection.vue`.

**9. Hidden parameter inputs in Edit tab DOM** — MOSTLY RESOLVED
The old `paramDepthScale` / `paramWaterScale` hidden-sync pattern is gone. One `type="hidden"` remains: `#regionLabelEdit` in `DemContainer.vue` for the region label datalist (acceptable).

**10. Dead DOM: `mergePanel`, `regionsContainer`, legacy status dots** — RESOLVED
All three have been removed. The template is now a minimal Vue shell with no legacy hidden elements.

**11. Two CSS files with duplicated variable declarations** — RESOLVED
`main.css` no longer exists. Only `app.css` is loaded, with a single set of CSS custom properties.

**12. Heavy inline `style=""` use throughout HTML** — PARTIALLY RESOLVED
`index.html` is now clean (only 2 inline styles on dev overlay). However, inline styles persist in Vue components (`CitiesSection.vue`, `DemSettingsPanel.vue`, `DemContainer.vue`). Tracked as `R-CLEAN1` in `proposals.md`.

### Performance

**13. No bundling/minification (ARCH4 pending)** — RESOLVED
Vite config (`vite.config.js`) is in place with Rollup multi-entry (`main.js`, `vue-main.ts`), chunking, and output to `dist/`.

**14. City rendering blocks main thread (PERF6B pending)** — RESOLVED
City rendering uses a Web Worker (`workers/city-worker.js`) with OffscreenCanvas. Worker receives feature buffers and posts back ImageBitmap. Main thread stays responsive.

**15. Leaflet and Leaflet-draw loaded from unpkg CDN** — RESOLVED
Leaflet CSS/JS now loaded from `/static/vendor/leaflet.css` and `/static/vendor/leaflet.js`. No CDN dependency.

**16. 7 canvas elements always allocated** — OPEN
All layer canvases are still allocated at full resolution simultaneously. Tracked as `F-UX-M` in `proposals.md`.

### Pending Features

**17. P6: Elevation band export (multi-material STL)** — OPEN, only unimplemented P-feature
**18. ~~ARCH4 + ARCH5: Vite + Vitest~~** — RESOLVED (Vite bundler + 58 Vitest tests)
**19. ~~PERF6B: Web Worker for city rendering~~** — RESOLVED (OffscreenCanvas worker)

---

## Proposed Improvements (Prioritised)

### P0 — Chrome console session (prerequisite for runtime data)
Connect Chrome extension, load `http://localhost:9000`, and record:
- Console errors/warnings on page load
- Network waterfall (how many requests, which are slow)
- JS exceptions during DEM load or export flow
- Render performance during city rendering

### P1 — Quick wins

**~~A. Fix cross-section collapsible icon~~** — N/A (Vue migration removed old HTML)

**~~B. Remove dead DOM~~** — DONE (mergePanel, regionsContainer, status dots all removed)

**~~C. Add text labels to floating map buttons~~** — MOSTLY DONE (old floating buttons replaced; sidebar toggle `☰` still emoji-only)

**~~D. Clarify `modelDepthScale` vs `modelExaggeration`~~** — DONE (dual inputs eliminated; single `paramDepthScale` remains)

**~~E. Vendor Leaflet assets locally~~** — DONE (Leaflet + Leaflet-draw in `app/client/static/vendor/`)

### P2 — Medium effort

**F. Consolidate region creation to a single entry point** — OPEN
Still 3 entry points. Tracked as `F-UX1` in `proposals.md`.

**~~G. Unify CSS variables~~** — DONE (`main.css` removed; single `app.css`)

**~~H. Replace hidden parameter inputs with appState~~** — DONE (hidden-sync pattern eliminated)

**~~I. BBox editor: expand the cramped row~~** — DONE (2×2 grid layout in `DemContainer.vue`)

**J. Settings panel collapsed tab: make it more visible** — OPEN
Still a small positioned button in `DemSettingsPanel.vue`.

### P3 — Architecture

**~~K. ARCH4: Add Vite bundler~~** — DONE (`vite.config.js` with Rollup multi-entry, chunking, HMR)

**~~L. PERF6B: Web Worker for city canvas rendering~~** — DONE (`workers/city-worker.js` with OffscreenCanvas + ImageBitmap)

**M. Lazy-allocate hidden canvases** — OPEN
Tracked as `F-UX-M` in `proposals.md`.

---

## Remaining Open Items

| # | Finding | Proposal | Tracker |
|---|---------|----------|---------|
| 1 | Duplicate region creation (3 entry points) | P2-F | `F-UX1` in proposals.md |
| 5 | Settings collapsed tab too subtle | P2-J | — |
| 12 | Inline styles in Vue components | — | `R-CLEAN1` in proposals.md |
| 16 | Canvases always allocated | P3-M | `F-UX-M` in proposals.md |
| 17 | P6 elevation band export | — | P6 in issues.md |

---

## Files for Remaining Work

| File | Open Item |
|------|-----------|
| `app/client/static/js/vue/components/views/MapContainer.vue` | Consolidate region creation (F-UX1) |
| `app/client/static/js/vue/components/sidebar/NewRegionSection.vue` | Consolidate region creation (F-UX1) |
| `app/client/static/js/vue/components/dem/DemSettingsPanel.vue` | Settings tab visibility; inline style cleanup |
| `app/client/static/js/vue/components/dem/CitiesSection.vue` | Inline style cleanup (R-CLEAN1) |
| `app/client/static/js/vue/components/views/DemContainer.vue` | Lazy canvas allocation (F-UX-M); inline style cleanup |
| `app/client/static/css/app.css` | Settings collapsed tab styling |
