# Known Issues & Status — strm2stl

## Active Technical Debt

### 1. app.js DOMContentLoaded Closure
`renderDEMCanvas` and `window.loadDEM` stay in app.js because they write closure vars (`lastDemData`, `originalDemValues`) and call closure functions (`addCurvePoint`, `drawCurve`). Extracting requires either fully mirroring those vars to appState or restructuring the closure.

### 2. ~20 Closure-Only State Vars Not on appState
`boundingBox`, `drawnItems`, `coordinatesData`, `stackedLayerData`, `compareData`, etc. Not yet needed by any module so not mirrored. If a new module needs them, mirror via `window.appState` first.

### 3. `<script>` vs Module Boundary
HTML inline `onclick=`/`onchange=` attributes have been removed (converted to `addEventListener` in event-listeners.js). One intentional inline `onclick=` remains on the dev-only debug error overlay dismiss button. The last non-intentional inline handler (`onclick="goToEdit()"` in regions.js divIcon) was replaced with a Leaflet `.on('click')` listener. Converting app.js itself to a full ES module is not planned — keep public functions on `window.*`.

## Feature Status

| ID | Feature | Status |
|----|---------|--------|
| P1 | Physical dimensions panel | ✅ Done |
| P2 | Print-bed fit optimizer | ✅ Done |
| P3 | Contour lines in STL | ✅ Done |
| P4 | Base label engraving | ✅ Done |
| P5 | STL mesh repair (trimesh) | ✅ Done |
| P6 | Elevation band export (multi-material STL) | ⏳ Pending |
| P7 | Cross-section OBJ export | ✅ Done |
| P8 | Flat water surface cap | ✅ Done |
| P9 | Region label editor | ✅ Done |
| P10 | Curve undo/redo | ✅ Done |
| P11 | Region thumbnails | ✅ Done |
| P12 | Map quick-preview tooltips | ✅ Done |

## Open Tasks (see TODO.md)

| ID | Task | Status |
|----|------|--------|
| ARCH4 | Add Vite bundler (HMR + production build) | ⏳ Open |
| ARCH5 | Vitest unit tests for pure functions (requires ARCH4) | ⏳ Open |
| PERF6B | Web Worker for city rendering (Part A done) | ⏳ Open |

## Completed Refactoring Milestones

- IMP4 ✅ — dem-loader.js owns all DEM canvas helpers
- IMP5 ✅ — window.appState unified across modules
- ARCH1 ✅ — state.js Proxy appState + events.js event bus
- ARCH3 ✅ — api.js centralizes all fetch calls
- FA2 ✅ — No duplicate functions between app.js and modules
- Backend split ✅ — server.py + schemas.py + config.py + core/ + routers/
- SQLite migration ✅ — data.db with WAL mode
- Backend DEAD-1 ✅ — removed JSON fallback (~150 lines) from regions.py
- Backend REFACTOR-1–5 ✅ — split fetch_osm_data, merge _fill_heights, split _rasterize_city, extract H5 tile helpers, satellite tile math
- Backend EXTRACT-1 ✅ — fetch_water_mask extracted from terrain router to core/dem.py
- Backend DEAD-2/4 ✅ — removed unused dim param and local import math from terrain.py
- Frontend CLEAN-1–5 ✅ — regions.js: inline onclick, haversineDiagKm bug, AUTO_SCALE constants, globe marker colors, selectCoordinate JSDoc
- Frontend DEM-CLEAN-1–3 ✅ — dem-main.js: extracted _applyDemResult, moved progress bar/cancel/sat-unavailable inline styles to CSS

Full history: `docs/functionality_doc.md`
