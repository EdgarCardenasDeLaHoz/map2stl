# layers/ — Open Tasks & Improvement Plans

## Open TODOs

### [x] PERF7 — Cache DOM references in stacked-layers.js hot paths
**File:** `stacked-layers.js`

- `document.querySelectorAll('#layerModeSelector .layer-mode-btn')` re-runs on every `setStackMode()` call.
  **Fix:** `let _layerModeBtns = null;` at module scope; lazily assign on first call.
  *(Partially done: variable declared, setStackMode patched. `_cachedDemCanvas` declared but mousemove handler not yet updated.)*

- `mousemove` re-queries `document.getElementById('layerDemCanvas')` on every mouse event (up to 60/sec).
  **Fix:** Cache `_cachedDemCanvas` outside the event handler closure; assign once when `enableStackedZoomPan()` runs:
  ```js
  _cachedDemCanvas = document.getElementById('layerDemCanvas');
  ```
  Then inside `mousemove`: `const demCanvas = _cachedDemCanvas;`

**Impact:** High — reduces DOM thrashing during all hover/pan interactions.

---

### [x] PERF9 — Avoid Float32Array→Array copy in composite-dem.js
**File:** `composite-dem.js` (line 393), depends on PERF9-dep in `dem/`

`applyCompositeToDem` does:
```js
const newValues = Array.from(_compositeValues);   // 160k allocations for 400×400
dem.values = newValues;
```
Once `renderDEMCanvas` accepts Float32Array (see `dem/TODO.md` PERF9-dep), change to:
```js
dem.values = _compositeValues;
```
And remove the `// renderDEMCanvas expects a plain Array` comment.

**Impact:** Eliminates 160k-element allocation + GC hit on every Apply click.

---

### [x] PERF10 — Yield composite DEM computation with RAF chunks
**File:** `composite-dem.js` (lines 276–290)

`computeCompositeDem()` runs synchronously: water/landcover/satellite contribution loops
iterate W×H pixels. For 400×400 with all features: ~640k iterations → 50–100ms main-thread freeze.

**Implemented:** Option 1 — `_yieldToMain()` called between each contribution phase and after the min/max loop and render step. Uses `scheduler.yield()` (Chrome 115+) with RAF fallback. Cancel token (`_computeGen`) aborts stale runs when slider changes arrive faster than computation completes.

**Impact:** High — eliminates UI freeze on Apply with composite enabled.

---

### [x] PERF12 — Pre-calculate visible grid line range
**File:** `stacked-layers.js` (~lines 273–290, 362–406)

The longitude/latitude grid loops currently iterate from `lonStart` to `bbox.east` (or `pixelStart` to `demWidth`), skipping invisible lines with `if (x < -2 || x > gw + 2) continue`. At high zoom or high grid density, many loop iterations fall entirely outside the viewport.

**Fix:** Pre-compute the last visible index before each loop:
```js
// Lon loop — compute lonEnd from the visible right edge
const lonEnd = Math.min(bbox.east + 1e-9,
    bbox.west + (gw - offsetX) / (cw * scale) * lonRange);
for (let lon = lonStart; lon <= lonEnd; ...)
```
Apply same pattern to lat loop and both pixel-mode loops.

**Impact:** Low-Medium — noticeably fewer iterations at high zoom or density > 20.

---

### [x] PERF13 — Destructure appState lookups in hot functions
**Files:** `city-render.js`, `stacked-layers.js`

`window.appState?.currentDemBbox` and `window.appState?.osmCityData` are queried multiple times per function through the Proxy trap.

**Fix:** Destructure once at function entry:
```js
const { currentDemBbox, osmCityData } = window.appState || {};
```
Apply in: `_doRenderCityOverlay` (city-render.js ~line 111–114), `updateStackedLayers` (stacked-layers.js ~line 155), and the mousemove handler tooltip block (stacked-layers.js ~line 523).

**Impact:** Low — code clarity + minor Proxy overhead reduction at 60fps hover.

---

### [~] PERF6B — Web Worker for city rendering (Part A done)
Pre-baking Float32Array buffers is done. Main-thread polygon rendering still causes jank with 500+ buildings.

**Work:**
1. Create `workers/city-worker.js` — self-contained draw logic; receives `{type:'init', buildings, roads, waterways}` with Float32Array buffers as Transferable.
2. In `city-render.js`: call `canvas.transferControlToOffscreen()` on the city overlay canvas; pass the `OffscreenCanvas` to the worker via `postMessage`.
3. For the stacked-layers composite: worker renders to its own `OffscreenCanvas` and posts back an `ImageBitmap`; main thread composites.
4. Main thread posts `{type:'render', zoom, offset}` on each zoom/pan; worker responds with `{type:'done'}`.
5. Cancel in-flight renders with a generation counter.

**Tricky:** `transferControlToOffscreen()` is one-shot; worker cannot access `window`/`appState`.
**Depends on:** PERF13 (clean state access pattern), PERF9 (typed array consistency).

---

## Improvement Plans

### Plan A — Unified layer pipeline
Currently each layer (DEM, Water, ESA, SatImg, CityRaster, CompositeDem) has its own canvas management pattern. A unified `LayerBuffer` class could:
- Allocate and resize canvases in one place
- Track dirty state (so `updateStackedLayers` skips unchanged layers)
- Provide a `render(sourceCanvas)` → `targetCanvas` pipeline

**Files to touch:** `stacked-layers.js`, `composite-dem.js`, `city-render.js`, `water-mask.js`

### Plan B — Progressive composite preview
While `computeCompositeDem()` runs, show a downsampled preview (e.g. every 4th pixel) immediately, then fill in the full-res result. Requires splitting the computation loop into two passes.

### Plan C — Layer blend modes
Allow the stacked view to composite multiple layers (e.g. DEM + CityRaster overlay) using canvas `globalCompositeOperation` rather than displaying only one at a time.

---

## Open TODOs — From Chrome Audit

### [ ] UX-M — Lazy-allocate hidden layer canvases
**File:** `stacked-layers.js`, `index.html`
**Source:** Chrome audit finding 16

All 7+ canvas elements (`layerDemCanvas`, `layerWaterCanvas`, `layerSatCanvas`, `layerSatImgCanvas`, `layerCityRasterCanvas`, `layerCompositeDemCanvas`, `stackViewCanvas`, `layerGridCanvas`) are allocated at full resolution simultaneously, even when hidden. Hidden canvases still occupy GPU memory.

**Fix:** Create each canvas only when its data is first loaded. Free (or zero-dimension) canvases when the user switches away from that layer. Track allocation state per layer in module scope.

**Risk:** `canvas.width = 0` loses the context — must recreate via `document.createElement('canvas')` if re-activated. Coordinate with `setStackMode()` which already switches source buffers.
