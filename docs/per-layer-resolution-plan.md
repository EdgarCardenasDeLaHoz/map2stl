# Per-Layer Resolution Implementation Plan

## Overview

This plan covers splitting the water mask and ESA land cover fetch into two independent, separately-cached operations, each driven by its own resolution dropdown, so that water data can be fetched at fine resolution (e.g. 10m for rivers) while land cover remains at a coarser resolution (e.g. 200m), or vice versa. It also removes the `target_width/target_height` override that currently forces both outputs to match the DEM pixel count regardless of the chosen resolution.

---

## 1. What Is Broken and Why

### 1.1 Single combined endpoint returns both layers

`GET /api/terrain/water-mask` (terrain.py lines 309–441) fetches ESA land cover and optionally JRC water data together in one call. It returns `water_mask_values`, `water_mask_dimensions`, `esa_values`, and `esa_dimensions` in the same JSON response. There is no way to re-fetch one without re-fetching the other.

### 1.2 Resolution is coupled via `sat_scale` minimum

In `water-mask.js` lines 89–91:

```javascript
const waterRes     = parseInt(document.getElementById('waterResolution')?.value || '200');
const landCoverRes = parseInt(document.getElementById('landCoverResolution')?.value || '200');
const satScale     = Math.min(waterRes, landCoverRes);
```

Both layers are fetched at the same scale: the finest of the two dropdowns. Changing `#landCoverResolution` to 100 when `#waterResolution` is 30 causes both to be fetched at 30m/px, making the land cover dropdown meaningless in practice.

### 1.3 `target_width/target_height` overrides native resolution

`water-mask.js` lines 128–131 append `target_width=lastDemData.width` and `target_height=lastDemData.height` when a DEM is loaded. The backend resizes the ESA and JRC images to match these dimensions regardless of `sat_scale`. This means the water and land cover resolution dropdowns have no visible effect once a DEM is loaded — the output always matches the DEM pixel count.

### 1.4 Cache key encodes DEM dimensions

`water-mask.js` lines 96–100 include `demWidth` and `demHeight` in the cache key. After removing `target_width/target_height`, these are no longer semantically correct and cause unnecessary cache misses when the DEM size changes without the bbox or scale changing.

### 1.5 Resolution change listeners trigger a full combined refetch

`setupLandCoverEditor` (water-mask.js line 521) wires `#landCoverResolution` onChange to call `loadWaterMask()`, which fetches both layers. There is no separate fetch path for land cover alone.

---

## 2. Current State Summary

### Data flow (before this change)

```
User changes #waterResolution or #landCoverResolution
  → loadWaterMask()
      → sat_scale = min(waterRes, landCoverRes)   ← coupled
      → params include target_width/target_height from lastDemData  ← overrides scale
      → GET /api/terrain/water-mask?sat_scale=X&target_width=W&target_height=H
          → ESA + JRC fetched at sat_scale, then resized to (W, H)
          → returns {water_mask_values, esa_values} at DEM dimensions
      → renderWaterMask(data)    → canvas at water_mask_dimensions
      → renderEsaLandCover(data) → canvas at esa_dimensions
      → updateStackedLayers()    → ctx.drawImage scales both to letterbox rect
```

### What is already correct (no changes needed)

- `renderWaterMask` and `renderEsaLandCover` each read their own `_dimensions` field — they already support differently-sized inputs.
- `updateStackedLayers` in `stacked-layers.js` uses `ctx.drawImage(source, 0,0,srcW,srcH, targetX,targetY,targetW,targetH)` — the destination rect is derived from the bbox aspect ratio, not the source pixel count. Canvases of any size are already scaled correctly.
- `applyProjection` is called per canvas before DOM placement and already handles arbitrary resolutions.

---

## 3. Chosen Approach: Split Endpoints

Two separate backend endpoints, one per layer, each with its own `scale` parameter and own disk cache. The frontend splits `loadWaterMask()` into `loadWaterMask()` + `loadLandCover()`. Each has its own abort controller, cache, status field, and resolution dropdown listener.

Step 1 (remove `target_width/target_height`) is a fast precondition fix that immediately makes the resolution dropdowns work and can be shipped alone.

---

## 4. Implementation Steps

### Step 1 — Remove `target_width/target_height` from the water-mask fetch (precondition fix)

**File:** `ui/static/js/modules/layers/water-mask.js`

- Delete lines 97–100: remove `demWidth`/`demHeight` from the cache key.
- Delete lines 128–131: stop sending `target_width`/`target_height` in params.
- Delete lines 104–108: remove the dimension-match guard on cache hits (it existed only because DEM size could differ from the cached entry; without target resizing, any cached entry for the same bbox+scale is valid).

**File:** `ui/core/dem.py`, function `fetch_water_mask_images`

- Remove `target_width` and `target_height` parameters from the function signature.
- Delete the resize block that applied them to the output images.

**File:** `ui/routers/terrain.py`, `get_terrain_water_mask`

- Remove `_parse_int(params, "target_width")` and `_parse_int(params, "target_height")` (lines 322–323).
- Remove them from the cache key dict (lines 334–335).
- Remove them from the `run_in_executor` call (line 388).
- Update the TEST_MODE block (line 370) to use `dim × dim` output size instead of `target_height or dim`.

**Effect:** After step 1, both water and ESA are returned at their native `sat_scale` resolution. The resolution dropdowns now visibly affect output dimensions. Ship and verify manually before continuing.

---

### Step 2 — Fix `renderCombinedView` / `previewWaterSubtract` / `applyWaterSubtract` for mismatched dimensions

**File:** `ui/static/js/modules/layers/water-mask.js`

These functions iterate `lastDemData.values` pixel-by-pixel and index into `lastWaterMaskData.water_mask_values` at the same index. After step 1, water mask may be at a different resolution than the DEM.

Add a module-scope helper:

```javascript
/**
 * Resample a flat pixel array from (srcH × srcW) to (dstH × dstW).
 * Uses bilinear interpolation for float data (water mask),
 * nearest-neighbour for integer class data (ESA).
 * @param {number[]} src - flat source array (row-major)
 * @param {number} srcH  - source height
 * @param {number} srcW  - source width
 * @param {number} dstH  - destination height
 * @param {number} dstW  - destination width
 * @param {boolean} [nearest=false] - use nearest-neighbour (for integer class data)
 * @returns {Float32Array}
 */
function _resampleToSize(src, srcH, srcW, dstH, dstW, nearest = false) { ... }
```

In `renderCombinedView`, `previewWaterSubtract`, and `applyWaterSubtract`: before the pixel loop, check if water mask dimensions match DEM dimensions. If not, resample:

```javascript
let waterValues = lastWaterMaskData.water_mask_values;
const [wmH, wmW] = lastWaterMaskData.water_mask_dimensions;
if (wmW !== width || wmH !== height) {
    waterValues = _resampleToSize(waterValues, wmH, wmW, height, width);
}
```

Also remove the dimension-mismatch guard at line ~296–300 that re-calls `loadWaterMask()` when sizes differ — resampling handles the mismatch at render time without a refetch.

---

### Step 3 — Backend: New `/api/terrain/land-cover` endpoint

**File:** `ui/routers/terrain.py`

Add a new route after `get_terrain_water_mask`:

```python
@router.api_route("/api/terrain/land-cover", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_land_cover(request: Request):
    """Fetch ESA WorldCover land-cover data independently of water mask."""
    params       = request.query_params
    north        = _parse_float(params, "north")
    south        = _parse_float(params, "south")
    east         = _parse_float(params, "east")
    west         = _parse_float(params, "west")
    esa_scale    = _parse_int(params, "esa_scale", 200)
    dim          = _parse_int(params, "dim", 200)
    ...
```

Response shape:

```json
{
  "esa_values": [...],
  "esa_dimensions": [h, w]
}
```

Apply the same auto-scale pixel-limit guard as the water endpoint (currently lines 364–367).

Cache key: `make_cache_key("esa", north, south, east, west, {"ss": esa_scale})` — separate `"esa"` namespace avoids collision with `"water"` entries.

**File:** `ui/core/dem.py`

Extract a `fetch_esa_image(north, south, east, west, scale)` helper — a thin wrapper around the first part of `fetch_water_mask_images` (the ESA WorldCover fetch). Call it from both the new endpoint and the existing combined endpoint, so the ESA fetch logic lives in one place.

---

### Step 4 — Frontend: Add `window.landCoverCache`

**File:** `ui/static/js/modules/core/cache.js`

Add a second LRU cache alongside `window.waterMaskCache`:

```javascript
window.landCoverCache = new LRUCache(50);
```

Add `window.landCoverCache` to `clearClientCache()` and `updateCacheStatusUI()`.

Update `waterMaskCache.generateKey` to drop the stale `demWidth/demHeight` suffix. New key pattern: `{north}_{south}_{east}_{west}_sc{sat_scale}_ds{dataset}`.

---

### Step 5 — Frontend: Add `window.api.dem.landCover`

**File:** `ui/static/js/modules/core/api.js`

Add inside the `dem` object:

```javascript
landCover: (params, signal) => _fetch(`/api/terrain/land-cover?${params}`, signal ? { signal } : {}),
```

---

### Step 6 — Frontend: Split `loadWaterMask()` / add `loadLandCover()`

**File:** `ui/static/js/modules/layers/water-mask.js`

Add module-scope state:

```javascript
let _landCoverAbortController = null;
let lastLandCoverData = null;
```

**Refactor `loadWaterMask()`:**
- Use only `#waterResolution` as `water_scale` (no more `Math.min`).
- Cache key: `{ ...bbox, sat_scale: waterScale, dataset: waterDataset }`.
- After success: call `renderWaterMask(data)` only. Do not call `renderEsaLandCover`.
- Set `layerStatus.water` only.

**Add `loadLandCover()`:**
- Aborts via `_landCoverAbortController`.
- Reads `#landCoverResolution` as `esa_scale`.
- Calls `window.api.dem.landCover(params, signal)`.
- Caches in `window.landCoverCache`.
- Stores result: `lastLandCoverData = data; window.appState.lastLandCoverData = data;`
- Calls `renderEsaLandCover(data)`.
- Sets `layerStatus.landCover`.

**Update `setupLandCoverEditor()`** (line ~521): change `onchange` from `() => loadWaterMask()` to `() => loadLandCover()`.

**Add `#waterResolution` change listener** inside `setupWaterMaskListeners()`:
```javascript
document.getElementById('waterResolution')?.addEventListener('change', () => loadWaterMask());
```

**Expose on window:**
```javascript
window.loadLandCover = loadLandCover;
```

---

### Step 7 — HTML: Add separate load button for land cover

**File:** `ui/templates/index.html`

In the Land Cover sub-group (around line 893), add:

```html
<button id="loadLandCoverBtn" class="btn btn-secondary" style="width:100%;font-size:11px;margin-top:6px;"
    title="Load ESA land cover at the selected resolution">
    🌿 Load Land Cover
</button>
```

Wire in JS: `document.getElementById('loadLandCoverBtn')?.addEventListener('click', () => window.loadLandCover?.())`.

The existing `#loadWaterMaskBtn` (line ~871) continues to load water mask only. Its label can be updated to "💧 Load Water Mask" for clarity.

---

### Step 8 — appState and state.md

**File:** `ui/static/js/app.js`

Add to the `appState` initialisation block:

```javascript
window.appState.lastLandCoverData = null;
```

**File:** `docs/state.md`

Add new entry:

| Key | Type | Source | Used by |
|---|---|---|---|
| `lastLandCoverData` | object \| null | water-mask.js `loadLandCover` | water-mask composite, pixel-mode label |

---

### Step 9 — Per-layer pixel size label

**File:** `ui/static/js/modules/layers/stacked-layers.js`, `setGridPixelMode` (line 27)

Extend the label to show dimensions for all loaded layers:

```javascript
const d  = window.appState?.lastDemData;
const w  = window.appState?.lastWaterMaskData;
const lc = window.appState?.lastLandCoverData;
const parts = [];
if (d)  parts.push(`DEM: ${d.width}×${d.height}`);
if (w)  parts.push(`Water: ${w.water_mask_dimensions[1]}×${w.water_mask_dimensions[0]}`);
if (lc) parts.push(`ESA: ${lc.esa_dimensions[1]}×${lc.esa_dimensions[0]}`);
sizeLabel.textContent = parts.length ? parts.join('  |  ') : '—';
```

---

### Step 10 — Tests

**File:** `tests/test_terrain.py` (or new `tests/test_land_cover.py`)

Add:
1. `GET /api/terrain/land-cover` with valid bbox returns `{"esa_values": [...], "esa_dimensions": [h, w]}`.
2. Different `esa_scale` values produce different `esa_dimensions`.
3. `GET /api/terrain/water-mask` no longer accepts or uses `target_width/target_height` — output dimensions are determined by `sat_scale` alone.
4. Cache hit on `/api/terrain/land-cover` returns `from_cache: True`.

Follow the project pattern: use `TEST_MODE` flag, patch at short module path (e.g. `routers.terrain`).

---

## 5. File Change Summary

| File | Change |
|---|---|
| `ui/routers/terrain.py` | Remove `target_width/target_height` from water-mask handler; add `/api/terrain/land-cover` endpoint |
| `ui/core/dem.py` | Remove `target_width/target_height` from `fetch_water_mask_images`; extract `fetch_esa_image` helper |
| `ui/static/js/modules/layers/water-mask.js` | Split `loadWaterMask` / add `loadLandCover`; add `_resampleToSize`; fix combined-view mismatch; update listeners |
| `ui/static/js/modules/core/api.js` | Add `window.api.dem.landCover` |
| `ui/static/js/modules/core/cache.js` | Add `window.landCoverCache`; fix `waterMaskCache.generateKey` |
| `ui/static/js/modules/layers/stacked-layers.js` | Update `setGridPixelMode` for per-layer dimensions |
| `ui/templates/index.html` | Add `#loadLandCoverBtn`; update `#loadWaterMaskBtn` label |
| `ui/static/js/app.js` | Add `lastLandCoverData: null` to appState init |
| `docs/state.md` | Add `lastLandCoverData` entry |
| `tests/test_terrain.py` | Add tests for new endpoint and step 1 behaviour |

---

## 6. Execution Order

1. **Step 1** — Remove `target_width/target_height`. Ship alone. Verify resolution dropdowns visibly affect output pixel count.
2. **Step 2** — Add `_resampleToSize`, fix `renderCombinedView` / `previewWaterSubtract` / `applyWaterSubtract`. Must precede step 6 (which introduces mismatched dims).
3. **Steps 3 + 4 + 5** — Add backend endpoint, frontend cache, and API method. Pure additions; verify backend via curl before wiring frontend.
4. **Step 6** — Split `loadWaterMask` / add `loadLandCover` in water-mask.js.
5. **Step 7** — Add `#loadLandCoverBtn` to HTML and wire it.
6. **Steps 8 + 9** — appState additions and pixel-mode label update.
7. **Step 10** — Tests.

---

## 7. What Does Not Change

- **`stacked-layers.js` compositing** (`updateStackedLayers`, `drawLayerToTarget`): already handles canvases of any size via `ctx.drawImage` destination scaling.
- **`applyProjection`**: operates on the canvas at its native dimensions; unaffected by resolution changes.
- **`renderLandCoverLegend`** and the land cover editor: colour mapping is independent of resolution.
- **DEM fetch endpoint and `loadDem()`**: unchanged.
- **`renderWaterMask(data)`** and **`renderEsaLandCover(data)`** render functions: unchanged — they already read their own `_dimensions` fields.
- **Backend `core/cache.py`**: `make_cache_key`, `write_array_cache`, `read_array_cache` used as-is.

---

## 8. Edge Cases and Risks

**Resampling quality.** Use bilinear interpolation for water mask values (continuous float 0–1) and nearest-neighbour for ESA class values (discrete integers — bilinear would produce non-existent intermediate class codes).

**Composite DEM reads ESA values.** `composite-dem.js` currently reads `window.appState.lastWaterMaskData.esa_values` for land-cover height contributions. After the split, this field moves to `lastLandCoverData.esa_values`. Update composite-dem.js to fall back: `appState.lastLandCoverData?.esa_values ?? appState.lastWaterMaskData?.esa_values`. This keeps backward compatibility if only `loadWaterMask` was called (the combined endpoint still returns `esa_values` in step 3 for backward compat).

**Preload in `cache.js`.** `preloadAllRegions` calls `window.api.dem.waterMask`. After the split, add a parallel call to `window.api.dem.landCover` to warm the new cache.

**`renderCombinedView` size guard removal.** Line ~296–300 currently checks if `demSize !== waterSize` and re-calls `loadWaterMask()`. After step 2 adds resampling, this guard is no longer needed and should be deleted to avoid redundant fetches.
