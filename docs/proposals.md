# AI-Proposed Features & Tasks — strm2stl

> **How to use:**
> - Set `Status` to `approved` to queue an item for implementation.
> - Set `Status` to `denied` to permanently drop it (AI will not re-propose).
> - Set `Status` to `deferred` to skip for now without closing the idea.
> - Leave `pending` for items not yet reviewed.
>
> When you approve an item, Claude will implement it in the next session and mark it `done` here.
> Claude will **not** implement any item whose status is not `approved`.

---

## New Features

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| F-P6 | Elevation band export — split STL mesh into per-band solids for multi-material printing | `app/server/routers/export.py`, `export/export-handlers.js` | Large | pending |
| F-EXP1 | Export progress indicator — spinner/bar during STL generation (poll `/api/export/status` or streaming) | `export/export-handlers.js` | Medium | pending |
| F-REG1 | Region list pagination — virtual scroll or 20-per-page for 50+ regions | `regions/region-ui.js` | Medium | pending |
| F-REG2 | Region import/export — download all as `regions.json`; import via file picker | `regions/regions.js`, `regions/region-ui.js` | Small | pending |
| F-REG3 | Region settings inheritance — "use global defaults" override per region | `regions/regions.js`, `app/server/routers/regions.py` | Medium | pending |
| F-UX1 | Consolidate region creation — keep only `floatingDrawBtn`; add empty-state hint to panel | `map/`, `index.html`, `events/event-listeners.js` | Small | pending |
| F-UX2 | Text labels on floating map buttons — visible `<span>` labels below each icon | `index.html`, `app.css` | Small | pending |
| F-UX3 | Clarify sidebar 3-state toggle — use "Expand / Collapse / Hide" instead of "Hide/Show" | `index.html`, `ui/view-management.js` | Small | pending |
| F-UX-M | Lazy-allocate hidden layer canvases — create/destroy canvas elements on show/hide | `layers/stacked-layers.js`, `index.html` | Medium | pending |
| F-FEAT | Preset undo — snapshot slider values before loading a preset; expose `window.revertPreset()` | `ui/presets.js` | Small | pending |

---

## Performance

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| P-PERF6B | Web Worker for city polygon rendering (Part A — Float32Array buffers — done; Part B — OffscreenCanvas) | `layers/city-render.js`, new `workers/city-worker.js` | Large | pending |
| P-PLANB-DEM | Off-thread DEM pixel loop — post `{values, lut}` to Worker, receive `ImageBitmap` | `dem/dem-main.js`, new `workers/dem-render-worker.js` | Medium | pending |
| P-RAF | RAF-gate `applyCurveTodemSilent` if it ever moves back into mousemove | `ui/curve-editor.js` (now `app/client/static/js/modules/ui/curve-editor.js`) | Small | pending |

---

## Refactoring / Code Cleanup

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| R-MAP2 | Bbox drag handle keyboard accessibility — `tabindex` + arrow-key nudge (0.1°) | `map/bbox-panel.js` | Small | pending |
| R-CLEAN1 | Replace remaining inline styles with CSS utility classes (index.html, misc JS) | `index.html`, `app.css`, various | Medium | pending |
| R-LAYERS | LayerBuffer class — unified canvas allocate/resize/dirty-track across all layer canvases | `layers/stacked-layers.js` | Large | pending |
| R-EVENTS-A | Event bus consolidation — add `EV.DEM_LOADED`, `EV.REGION_SELECTED`; replace direct `window.fn()` calls | `events/`, all modules | Large | pending |
| R-EVENTS-B | Keyboard shortcut registry — replace `keydown` switch with `window.registerShortcut(key, label, fn)` | `events/keyboard-shortcuts.js` | Small | pending |
| R-EVENTS-C | Debounce audit — gate `input` handlers where target takes >5ms | all modules | Small | pending |

---

## Architecture

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| A-ARCH4 | Vite bundler — `npm install` (config already at `vite.config.js`); HMR dev server on port 5173 | `package.json`, `index.html` | Medium | pending |
| A-ARCH5 | Vitest unit tests for pure functions (requires A-ARCH4) — `interpolateCurve`, `mapElevationToColor`, `detectContinent`, `haversineDiagKm`, `niceGeoInterval` | `tests/` (new) | Medium | pending |
| A-SW | Service worker for API response caching — stale-while-revalidate for `/api/terrain/dem` and `/api/terrain/satellite` | new `sw.js` | Medium | pending |
| A-OBJ-TEX | OBJ cross-section export with UV map + PNG texture from current colormap | `app/server/core/export.py`, `export/export-handlers.js` | Large | pending |

---

## Backend

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| B-STREAM | Streaming STL generation — Python generators + `StreamingResponse` to reduce peak RAM | `app/server/core/export.py`, `app/server/routers/export.py` | Medium | pending |
| B-MULTI | Print-bed multi-piece export — auto-tile large DEMs into N×M pieces with alignment tabs | `app/server/core/export.py`, `export/export-handlers.js` | Large | pending |
| B-OPENAPI | OpenAPI schema validation in dev — auto-generate JSON Schema from `/openapi.json`; validate in `api.js` | `core/api.js` | Small | pending |

---

## Denied / Deferred

| ID | Reason |
|----|--------|
| _(none yet)_ | |
