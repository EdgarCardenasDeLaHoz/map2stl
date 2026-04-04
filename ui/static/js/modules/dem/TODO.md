# dem/ — Open Tasks & Improvement Plans

## Code Cleanup

### [x] DEM-CLEAN-1 — Extract _applyDemResult from loadDEM (dem-main.js)
**File:** `dem-main.js` lines 142–267

`loadDEM` is 242 lines. The success block starting at line 142 (`if (data.dem_values && data.dimensions)`)
is 115 lines that:
- Flattens nested dem arrays
- Calls `renderDEMCanvas`, `applyProjection`, appends canvas
- Updates overlays (axes, colorbar, histogram, gridlines)
- Updates stacked layers, bbox inputs, elev range display
- Handles sat_values landuse panel
- Captures region thumbnail
- Sets lastDemData.bbox
- Re-renders city overlay
- Auto-loads city data if layer toggles are on
- Calls `updatePrintDimensions`

**Fix:** Extract to `_applyDemResult(data, north, south, east, west)`:
```js
function _applyDemResult(data, north, south, east, west) { ... }
```
`loadDEM` becomes: fetch → error check → `_applyDemResult(data, ...)` → toast.
The extracted function stays in dem-main.js (same module scope, no window.* needed since it's only called by loadDEM).

---

### [x] DEM-CLEAN-2 — Move progress bar inline styles to CSS (dem-main.js)
**File:** `dem-main.js` lines 104–110

The DEM loading progress bar is assembled with explicit `.style.*` assignments:
```js
progressBar.style.width = '100%';
progressBar.style.height = '6px';
...
```
**Fix:** Add a `.dem-progress-bar` rule to `app.css` (the rule already uses `.dem-progress-bar`
as className but the class has no CSS definition — the styles are applied inline). Move the
container styles there; keep only the `id="demProgress"` inner bar's `width:0%` as inline
(it's dynamically updated during fetch via `document.getElementById('demProgress').style.width`).

---

### [x] DEM-CLEAN-3 — Extract satellite unavailable placeholder (dem-main.js)
**File:** `dem-main.js` lines 638–644

`loadSatelliteImage` contains an inline multi-line template literal:
```js
document.getElementById('satelliteImage').innerHTML = `
    <div style="background:#333;padding:30px;text-align:center;border-radius:4px;">
        <p style="color:#888;margin:0;">Satellite data not available</p>
        <p style="color:#666;font-size:12px;margin-top:5px;">Earth Engine module required</p>
    </div>
`;
```
**Fix:** Extract to a `_satUnavailableHTML()` helper (one-liner template) or use a CSS class +
simple static string. The inline `style=` attrs should move to a `.sat-unavailable` CSS rule.

---

## Improvement Plans

### Plan A — Off-thread pixel rendering
The `renderDEMCanvas` pixel loop (~40k–160k iterations at 200–400px) runs synchronously on the main thread, blocking the UI.

**Approach:**
1. Create `workers/dem-render-worker.js` receiving `{values, width, height, lut}` via Transferable ArrayBuffers
2. Worker builds `Uint8ClampedArray` pixels and posts back `ImageBitmap`
3. Main thread: `ctx.drawImage(imageBitmap, 0, 0)`

**Dependencies:** All PERF items already done (PERF8 ImageData reuse, PERF9-dep Float32Array, PERF11 LUT cache).

### Plan B — Streaming histogram
Merge the min/max pass and histogram pass into a single typed-array scan (currently two separate passes in `renderDEMCanvas` and `loadDEM`).

### Plan C — DEM tile stitching (future)
Stitch multiple low-res tiles for large regions instead of one high-res backend request.
