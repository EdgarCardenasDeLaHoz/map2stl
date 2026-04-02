# dem/ — Open Tasks & Improvement Plans

## Open TODOs

### [x] PERF8 — Reuse ImageData across renders
**File:** `dem-main.js` (~line 303)
`ctx.createImageData(w, h)` allocates a new `Uint8ClampedArray` on every render even when dimensions haven't changed.

**Fix:** Add module-scope `let _demImageData = null;`. In `renderDEMCanvas`, replace:
```js
const img = ctx.createImageData(width, height);
```
with:
```js
if (!_demImageData || _demImageData.width !== width || _demImageData.height !== height) {
    _demImageData = new ImageData(width, height);
}
const img = _demImageData;
```
All 4 channels are always written in the pixel loop, so no pre-fill needed.

**Impact:** Eliminates Uint8ClampedArray allocation + GC hit on every recolor/rescale. Medium.

---

### [x] PERF11 — Expose `_invalidateLutCache()` globally
**File:** `dem-main.js` (~line 16), wiring in `events/event-listeners-map.js`

The module-scope `_lutCache` Map is already keyed by colormap name and cleared inside `composite-dem.js` on colormap change. However, direct calls to `renderDEMCanvas` from other paths may reuse a stale LUT if the colormap function itself changes.

**Fix:** Near the `_lutCache` declaration, add:
```js
window._invalidateLutCache = (colormap) => {
    if (colormap) _lutCache.delete(colormap);
    else          _lutCache.clear();
};
```
In `event-listeners-map.js` line 23, change:
```js
document.getElementById('demColormap').onchange = () => window.recolorDEM?.();
```
to:
```js
document.getElementById('demColormap').onchange = () => {
    window._invalidateLutCache?.();
    window.recolorDEM?.();
};
```
**Impact:** Low — prevents edge-case stale-color rendering when colormap selection changes.

---

### [x] PERF9-dep — Accept Float32Array in `renderDEMCanvas`
**File:** `dem-main.js` (lines 278, 306)

Currently `renderDEMCanvas` silently ignores Float32Array inputs:
- Line 278: `values: (Array.isArray(values) ? values.slice() : [])` → stores empty if Float32Array
- Line 306: `const flat = Array.isArray(values) ? values : [];` → flat is `[]` if Float32Array

**Fix:**
```js
// Line 278
values: (Array.isArray(values) || ArrayBuffer.isView(values)) ? values : [],
// Line 306
const flat = (Array.isArray(values) || ArrayBuffer.isView(values)) ? values : [];
```
This unblocks PERF9 in `composite-dem.js` (removing `Array.from()` there).

---

## Improvement Plans

### Plan A — Off-thread pixel rendering
The `renderDEMCanvas` pixel loop (lines 341–356) is ~40k–160k iterations on a 200–400px DEM. It runs synchronously on the main thread on every recolor, blocking the UI.

**Approach:**
1. Create `workers/dem-render-worker.js` that receives `{values, width, height, lut}` via postMessage with `Transferable` ArrayBuffers.
2. Worker builds the `Uint8ClampedArray` pixels and posts back an `ImageBitmap` (or raw buffer).
3. Main thread calls `ctx.drawImage(imageBitmap, 0, 0)`.

**Dependencies:** PERF8 (reuse buffer), PERF9-dep (Float32Array), PERF11 (stale LUT prevention).
**Risk:** `window.mapElevationToColor` is a global — LUT must be pre-computed before sending to worker.

### Plan B — Streaming histogram
`renderDEMCanvas` and `loadDEM` both compute min/max in separate passes. The min/max pass and histogram pass could be merged into a single typed-array scan.

**Files:** `dem-main.js` (min/max scan), `app.js` (histogram computation).
**Impact:** Halves the number of array passes on large DEMs.

### Plan C — DEM tile stitching (future)
For large regions, stitch multiple low-res DEM tiles instead of requesting a single high-res tile from the backend. Would require splitting `loadDEM` into a tile-aware fetch + merge step (see `dem-merge.js`).
