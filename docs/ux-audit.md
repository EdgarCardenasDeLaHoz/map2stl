# App Improvement Plan — strm2stl UX/Performance Audit

## Context

Based on deep source code inspection of `index.html`, `app.css`, `main.css`, `export-handlers.js`, and prior session memory. The Chrome browser extension was unavailable during this audit, so live console log and runtime observations are pending — **recommend a Chrome session once the extension is connected to gather actual console errors, network timings, and first-paint metrics.**

The app has three tabs: **Explore** (map, region selection) → **Edit** (DEM visualization, layer settings) → **Extrude** (3D model generation + export). The structure is solid but the UI has accumulated significant clutter and inconsistency across 18+ sessions of incremental feature addition.

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

**7. Cross-section collapsible uses `▶` while all others use `▼`**
Minor but jarring inconsistency in the collapsible icons (`index.html:1318`).

**8. `modelExaggeration` vs `modelDepthScale` — user confusion**
Two separate number inputs for vertical scale. `modelDepthScale` affects the exported file; `modelExaggeration` only affects the live 3D preview. This distinction is not visually obvious — they look identical, positioned next to each other.

**9. Hidden parameter inputs in Edit tab DOM**
`<input type="hidden" id="paramDepthScale">` etc. exist in the Edit tab but duplicate visible inputs in the Extrude tab (`modelDepthScale`, `modelWaterScale`). This hidden-value sync pattern is fragile.

**10. Dead DOM: `mergePanel`, `regionsContainer`, legacy status dots**
- `mergePanel` (lines ~1054–1132): commented "Merge tab removed" but still in DOM
- `regionsContainer` (lines ~333–360): marked "legacy, hidden"
- `status-dem/water/satellite/combined` status dots: hidden, JS-compat only
These add parse/layout weight and confusion to anyone reading the source.

**11. Two CSS files with duplicated variable declarations**
`main.css` defines CSS custom properties (`--bg-primary`, etc.) while `app.css` defines a parallel set (`--bg-dark`, etc.) with different names for the same colours. The HTML only loads `app.css`, making `main.css` effectively dead or partially redundant.

**12. Heavy inline `style=""` use throughout HTML**
Large portions of the UI use inline styles (`font-size:11px`, `padding:3px 4px`, `background:#1a1a1a`) instead of CSS classes. Makes theming impossible and maintenance slow.

### Performance

**13. No bundling/minification (ARCH4 pending)**
30 ES modules imported sequentially — no tree-shaking, no code splitting, no minification. Each module is a separate HTTP round-trip to the local FastAPI server on cold load.

**14. City rendering blocks main thread (PERF6B pending)**
City canvas rendering (buildings, roads, waterways) runs synchronously on the main thread. Dense urban areas cause visible jank.

**15. Leaflet and Leaflet-draw loaded from unpkg CDN**
```html
<link rel="stylesheet" href="https://unpkg.com/leaflet/dist/leaflet.css" />
<link rel="stylesheet" href="https://unpkg.com/leaflet-draw/dist/leaflet.draw.css" />
```
The app silently breaks without internet. Since this is a local tool, assets should be vendored.

**16. 7 canvas elements always allocated**
`layerDemCanvas`, `layerWaterCanvas`, `layerSatCanvas`, `layerSatImgCanvas`, `layerCityRasterCanvas`, `layerCompositeDemCanvas`, `stackViewCanvas`, `layerGridCanvas` — all allocated at full resolution simultaneously, even when hidden.

### Pending Features

**17. P6: Elevation band export (multi-material STL)** — only unimplemented P-feature
**18. ARCH4 + ARCH5: Vite + Vitest** — solves issue 13 + enables unit tests
**19. PERF6B: Web Worker for city rendering** — Part A done, Part B pending; solves issue 14

---

## Proposed Improvements (Prioritised)

### P0 — Chrome console session (prerequisite for runtime data)
Connect Chrome extension, load `http://localhost:9000`, and record:
- Console errors/warnings on page load
- Network waterfall (how many requests, which are slow)
- JS exceptions during DEM load or export flow
- Render performance during city rendering

### P1 — Quick wins (no architectural change needed)

**A. Fix cross-section collapsible icon**
Change `▶` to `▼` in `index.html:1318`.

**B. Remove dead DOM**
Delete `<div id="mergePanel">`, `<div id="regionsContainer">`, and the 4 hidden status dot spans. Verify no remaining JS references before deleting.
*Search:* `grep -r "mergePanel\|regionsContainer\|status-dem" ui/static/js/`

**C. Add text labels to floating map buttons**
Change emoji-only buttons to show text labels (e.g. `🏔️ Terrain`, `🌍 Globe`) or add visible `<span>` labels below each icon.
*Files:* `index.html` lines 249–266, `app.css`

**D. Clarify `modelDepthScale` vs `modelExaggeration`**
Add a `(preview only)` badge or visual separator between the two inputs in the Extrude tab so users understand which affects the exported file.
*File:* `index.html` lines 1193–1195

**E. Vendor Leaflet assets locally**
Download `leaflet.css`, `leaflet.js`, `leaflet-draw.css`, `leaflet-draw.js` to `ui/static/vendor/` and update `<head>` links.
*File:* `index.html` lines 7–8

### P2 — Medium effort

**F. Consolidate region creation to a single entry point**
Keep only `floatingDrawBtn` ("+ New Region") on the map as the primary CTA. Remove `+ New` from the regions panel; keep the panel for viewing/searching only. Add a "Draw a region on the map to begin" hint to the panel empty state.
*Files:* `index.html`, `regions.js`, `event-listeners.js`

**G. Unify CSS variables**
Audit which CSS file is actually loaded (HTML loads `app.css`; check if `main.css` has a second link). Consolidate to a single file with one set of CSS variables. Delete or absorb `main.css`.
*Files:* `app.css`, `main.css`, `index.html`

**H. Replace hidden parameter inputs with appState**
Remove `<input type="hidden" id="paramDepthScale">` etc. Store values directly in `appState` or read live from Model tab inputs. Eliminate the hidden-DOM sync pattern.
*Files:* `index.html` lines 904–911, any JS that reads these via `getElementById`

**I. BBox editor: expand the cramped row**
Give the N/S/E/W coordinate inputs their own collapsible section with full-width labelled inputs (matching sidebar edit view style), or replace the horizontal strip with a 2×2 grid layout at readable font sizes. Keep Reload + mini-map + Save buttons in a separate action row below.
*Files:* `index.html` lines 518–543, `app.css`

**J. Settings panel collapsed tab: make it more visible**
Change `settingsCollapsedTab` from a small bottom-right button to a vertical tab on the right edge of the DEM area, always visible when the panel is hidden.
*Files:* `index.html` line 1136, `app.css`

### P3 — Architecture (higher effort)

**K. ARCH4: Add Vite bundler**
Entry point is `main.js` (already a proper ES module). Config: `vite.config.js` at `strm2stl/` root, `root: 'ui/static'`, output to `ui/static/dist/`. Update `index.html` script tag. Resolves 30-module cold-load problem, enables HMR.

**L. PERF6B: Web Worker for city canvas rendering**
Worker receives GeoJSON building/road/waterway data + resolution, renders to `OffscreenCanvas`, posts `ImageBitmap` back. Main thread applies to `layerCityRasterCanvas`. Eliminates main-thread jank on city loads.

**M. Lazy-allocate hidden canvases**
Create `layerWaterCanvas`, `layerSatCanvas`, etc. only when their data is first loaded. Free or reuse when user switches away. Reduces peak GPU memory usage.

---

## Verification

After implementing changes:
1. **P1A–E**: Visual inspection in Chrome, no console errors
2. **P2F**: Create region using only `floatingDrawBtn` — confirm end-to-end flow
3. **P2G**: DevTools → Sources — confirm only one CSS file defines colour variables
4. **P2H**: Load DEM → Extrude → Generate — check Network tab POST body contains correct `depth_scale`
5. **P3K (Vite)**: `npm run build` produces a single bundle; `npm run dev` starts HMR; existing functionality unchanged
6. **P3L (Worker)**: Load city (≤10km region), toggle buildings — Performance tab shows no long tasks on main thread during render

---

## Files to Modify

| File | Changes |
|------|---------|
| `ui/templates/index.html` | Dead DOM removal, bbox layout, floating button labels, icon fix, hidden input removal |
| `ui/static/css/app.css` | CSS variable consolidation, settings tab visibility, bbox row layout |
| `ui/static/css/main.css` | Delete or merge into app.css |
| `ui/static/js/app.js` | Remove hidden input reads, update appState |
| `ui/static/js/modules/export/export-handlers.js` | Read depth/water scale from Model tab inputs instead of hidden DOM |
| `ui/static/js/modules/regions/regions.js` | Consolidate region creation entry points |
| `ui/static/vendor/` | Add vendored Leaflet/Leaflet-draw assets (new dir) |
| `vite.config.js` (new) | ARCH4 |
