/**
 * modules/water-hydrology-combined.js — Unified water mask + hydrology layer.
 *
 * Loads and composites water (ESA water + land cover) and hydrology (rivers) 
 * into a single combined layer for rendering in the stacked view.
 *
 * Preview = print: the hydrology half is requested with the loaded DEM's
 * `dem_source` and grid, so the server returns exactly the river carve the
 * composite / export applies (hydrology-print.js builds the query). Without a
 * loaded DEM nothing is requested.
 *
 * Public API (all on window):
 *   loadWaterHydrology()    — fetch water + hydrology in parallel and render combined
 *   clearWaterHydrology()   — clear canvas + state
 *
 * Dependencies:
 *   window.api                      — from modules/api.js
 *   window.waterMaskCache           — from modules/cache.js
 *   window.appState.waterHydrologyCanvas  — set here; read by stacked-layers.js
 *   window.getBoundingBox()         — L.Rectangle | null, from app.js
 *   window.getBboxCoords(bb, region) — helper from app.js
 *   window.showToast(msg, type)     — from app.js
 *   window.decodeWaterMask(data)     — from water-mask.js
 *   window.decodeHydrologyValues(data) — from hydrology-overlay.js
 *   window.setLayerStatus(name, status) — from app.js
 *   window.updateStackedLayers()    — from stacked-layers.js
 *   (renderer logic mirrored from water-mask.js and hydrology-overlay.js)
 */

import {
  readHydrologyRiverControls, loadedDemGrid, hydrologyPrintQuery, readRiverDepthScale,
  NEED_DEM_MESSAGE,
} from './hydrology-print.js';

// ─────────────────────────────────────────────────────────────────────────────
// Module-scope state
// ─────────────────────────────────────────────────────────────────────────────

let _combinedAbortController = null;
let _combinedInflightPromise = null;
let _combinedInflightKey = null;


// ─────────────────────────────────────────────────────────────────────────────
// Composite Rendering
// ─────────────────────────────────────────────────────────────────────────────

// Colour per Strahler order 1..10 (viridis steps: small streams dark, trunk rivers yellow).
const HYDRO_ORDER_COLORS = [
  [68, 1, 84], [72, 40, 120], [62, 73, 137], [49, 104, 142], [38, 130, 142],
  [31, 158, 137], [53, 183, 121], [110, 206, 88], [181, 222, 43], [253, 231, 37],
];

/** 'depth' (blue, opacity by depth) or 'order' (colour per Strahler order). */
function _hydroColorMode() {
  return document.getElementById('hydroColorMode')?.value || 'depth';
}

/** Legend chips for the orders present (order mode only). */
function _renderOrderLegend(hydroData) {
  const el = document.getElementById('hydroOrderLegend');
  if (!el) return;
  const counts = hydroData?.order_counts;
  if (_hydroColorMode() !== 'order' || !counts || !hydroData?.order_grid_b64) {
    el.innerHTML = '';
    el.hidden = true;
    return;
  }
  el.hidden = false;
  // The print model counts carved pixels per order; the older path counted reaches.
  const unit = hydroData.order_counts_unit === 'px' ? 'px' : 'reaches';
  el.innerHTML = Object.keys(counts).map(Number).sort((a, b) => a - b).map((o) => {
    const [r, g, b] = HYDRO_ORDER_COLORS[Math.min(o, HYDRO_ORDER_COLORS.length) - 1];
    return `<span class="hydro-order-chip" title="${counts[o].toLocaleString()} ${unit}">`
      + `<span class="hydro-order-swatch" style="background:rgb(${r},${g},${b})"></span>${o}</span>`;
  }).join('');
}

/** Redraw the loaded layer after a view-setting change (no new request). */
window.rerenderWaterHydrology = function rerenderWaterHydrology() {
  const last = window.appState?.lastWaterHydrology;
  if (!last) return;
  const canvas = renderWaterHydrologyCombined(last.waterData, last.hydroData);
  if (canvas) window.appState.waterHydrologyCanvas = canvas;
  _renderOrderLegend(last.hydroData);
  window.emitStackUpdate?.();
};
document.addEventListener('change', (e) => {
  if (e.target?.id === 'hydroColorMode') window.rerenderWaterHydrology();
});

function renderWaterHydrologyCombined(waterData, hydroData) {
  // Decode the water mask array
  const waterValues = waterData ? window.decodeWaterMask?.(waterData) : null;
  // Decode the hydrology values array
  const hydroValues = hydroData ? window.decodeHydrologyValues?.(hydroData) : null;

  if (!waterValues && !hydroValues) {
    console.warn('No water or hydrology data to render');
    return null;
  }


  // Determine canvas size from dimensions
  let w, h;
  if (waterData?.water_mask_dimensions) {
    [h, w] = waterData.water_mask_dimensions;
  } else if (hydroData?.river_grid_dimensions) {
    [h, w] = hydroData.river_grid_dimensions;
  } else {
    console.warn('Could not determine canvas dimensions');
    return null;
  }
  // Create offscreen canvas for compositing
  const combinedCanvas = document.createElement('canvas');
  combinedCanvas.width = w;
  combinedCanvas.height = h;

  const ctx = combinedCanvas.getContext('2d');

  // 1. Render water mask first (base layer) - unless the hydrology grid already
  //    carries the open water (water_surface): drawing both on grids of slightly
  //    different size left a bright double-drawn outline along every coast.
  if (waterValues && !hydroData?.water_surface) {
    const waterImg = ctx.createImageData(w, h);
    for (let i = 0; i < waterValues.length; i++) {
      const val = waterValues[i];
      const idx = i * 4;
      if (val > 0.5) {
        // Water pixel: semi-transparent blue
        waterImg.data[idx] = 0;
        waterImg.data[idx + 1] = 100;
        waterImg.data[idx + 2] = 255;
        waterImg.data[idx + 3] = 200;
      } else {
        // Land pixel: transparent
        waterImg.data[idx] = 0;
        waterImg.data[idx + 1] = 0;
        waterImg.data[idx + 2] = 0;
        waterImg.data[idx + 3] = 0;
      }
    }
    ctx.putImageData(waterImg, 0, 0);
  }

  // 2. Render hydrology on top. Every canvas pixel samples its source pixel
  // (inverse mapping): copying source pixels forward left ~1 in 67 canvas columns
  // unwritten whenever the projected grid was narrower than the canvas (591 vs
  // 600 px for the Amazon) - the vertical stripes over the sea.
  if (hydroValues && hydroData) {
    const hydroDims = hydroData.river_grid_dimensions || [h, w];
    const hydroH = hydroDims[0] || h;
    const hydroW = hydroDims[1] || w;
    // Deepest carve (print model) or the old max depression; keep the sign.
    const minD = hydroData.depression_m || -5.0;
    const byOrder = _hydroColorMode() === 'order';
    const orders = byOrder ? window.decodeHydrologyOrders?.(hydroData) : null;
    const waterCode = hydroData.order_water_code ?? 100;

    // Read current pixels (water already rendered) so hydrology can blend on top.
    const baseImg = ctx.getImageData(0, 0, w, h);
    const px = baseImg.data;

    for (let ty = 0; ty < h; ty++) {
      const hy = Math.min(hydroH - 1, Math.floor(((ty + 0.5) / h) * hydroH));
      for (let tx = 0; tx < w; tx++) {
        const hx = Math.min(hydroW - 1, Math.floor(((tx + 0.5) / w) * hydroW));
        const hi = hy * hydroW + hx;
        const v = hydroValues[hi] ?? 0;
        if (v === 0) continue;
        const base = (ty * w + tx) * 4;

        let cr = 30, cg = 100, cb = 200, alpha;
        if (orders) {
          const o = orders[hi] | 0;
          if (o === waterCode) {
            [cr, cg, cb] = [0, 100, 255];
            alpha = 150;
          } else if (o > 0) {
            [cr, cg, cb] = HYDRO_ORDER_COLORS[Math.min(o, HYDRO_ORDER_COLORS.length) - 1];
            alpha = 235;
          } else {
            continue;
          }
        } else {
          // Deeper river => more opacity.
          const t = Math.min(1, Math.max(0, v / minD));
          alpha = Math.round(60 + t * 160); // 60–220
        }
        const a01 = alpha / 255;
        px[base] = Math.round(px[base] * (1 - a01) + cr * a01);
        px[base + 1] = Math.round(px[base + 1] * (1 - a01) + cg * a01);
        px[base + 2] = Math.round(px[base + 2] * (1 - a01) + cb * a01);
        px[base + 3] = Math.max(px[base + 3], alpha);
      }
    }

    ctx.putImageData(baseImg, 0, 0);
  }

  return combinedCanvas;
}

// ─────────────────────────────────────────────────────────────────────────────
// Main Load Function
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Load water mask + hydrology in parallel and render combined.
 * Reads settings from DOM controls:
 *   - Water: #waterResolution, #waterDataset
 *   - Hydrology: #hydroSource, #hydroMinOrder, #hydroWidthFactor (Fetch) and
 *     #compositeRiverDepthScale (Composite Depth ×); grid = the loaded DEM's.
 */
window.loadWaterHydrology = async function loadWaterHydrology() {
  const boundingBox = window.getBoundingBox?.();
  const selectedRegion = window.appState?.selectedRegion;

  const coords = window.getBboxCoords(boundingBox, selectedRegion);
  if (!coords) {
    window.showToast?.('Select a region before loading hydrology.', 'warning');
    return;
  }
  const { north, south, east, west } = coords;

  // The preview is the print carve on the loaded DEM, so it needs that DEM.
  const grid = loadedDemGrid(window.appState);
  if (!grid) {
    const statusEl = document.getElementById('waterHydrologyStatus');
    if (statusEl) statusEl.textContent = NEED_DEM_MESSAGE;
    window.showToast?.(NEED_DEM_MESSAGE, 'warning');
    return;
  }

  const waterDim = parseInt(document.getElementById('waterResolution')?.value || '600');
  const waterDataset = document.getElementById('waterDataset')?.value || 'esa';
  const river = readHydrologyRiverControls();
  const depthScale = readRiverDepthScale();
  const { projection, maintainDimensions, clipValidRegion } = window.getProjectionParams();

  // Build stable key for in-flight dedupe
  const inflightKey = JSON.stringify({
    n: north, s: south, e: east, w: west, waterDim, waterDataset, grid, river, depthScale,
    projection, maintainDimensions, clipValidRegion,
  });

  if (_combinedInflightPromise && _combinedInflightKey === inflightKey) {
    return _combinedInflightPromise;
  }

  // Cancel previous requests
  if (_combinedAbortController) _combinedAbortController.abort();
  _combinedAbortController = new AbortController();
  const signal = _combinedAbortController.signal;

  const hydroParams = hydrologyPrintQuery({
    coords, grid, river, depthScale, projection, maintainDimensions, clipValidRegion,
  });

  _combinedInflightKey = inflightKey;
  _combinedInflightPromise = _performCombinedLoad(
    north, south, east, west, waterDim, waterDataset, hydroParams, river.hydroSource, signal,
    projection, maintainDimensions ? 'true' : 'false', clipValidRegion ? 'true' : 'false',
  );

  try {
    await _combinedInflightPromise;
  } finally {
    _combinedInflightPromise = null;
    _combinedInflightKey = null;
  }
};

/**
 * Internal function to perform the actual load and render.
 */
async function _performCombinedLoad(north, south, east, west, waterDim, waterDataset, hydroParams, hydroSource, signal, projection = 'none', maintainDims = 'false', clipNans = 'false') {

  const statusEl = document.getElementById('waterHydrologyStatus');
  const loadBtn = document.getElementById('loadWaterHydrologyBtn');

  if (statusEl) statusEl.textContent = 'Loading hydrology…';
  if (loadBtn) {
    loadBtn.disabled = true;
    loadBtn.dataset.origText = loadBtn.textContent;
    loadBtn.textContent = '⏳ Loading Hydrology…';
  }

  window.setLayerStatus?.('waterHydrology', 'loading');

  try {
    // Fetch water and hydrology in parallel, passing projection and clip_valid_region.
    // Water mask is shared with the standalone Water layer (water-mask.js) — use
    // the identical cache key + in-flight dedupe so loading both layers for the
    // same region shares one /api/terrain/water-mask request rather than firing
    // it twice. This matters most when Earth Engine isn't authenticated: each
    // failed EE init costs ~3-4s, and a plain result-cache can't help two
    // concurrent callers since neither has a result to cache yet — dedupe()
    // shares the one in-flight (possibly failing) promise instead.
    const waterCacheKey = {
      north, south, east, west,
      dim: waterDim, dataset: waterDataset,
      projection, maintain_dimensions: maintainDims, clip_valid_region: clipNans,
    };
    const cachedWater = window.waterMaskCache?.get(waterCacheKey);
    const waterParams = new URLSearchParams({
      north, south, east, west, dim: waterDim, dataset: waterDataset
    });
    if (projection && projection !== 'none') {
      waterParams.append('projection', projection);
      waterParams.append('maintain_dimensions', maintainDims);
      waterParams.append('clip_valid_region', clipNans);
    }
    const waterPromise = cachedWater
      ? Promise.resolve({ data: cachedWater, error: null })
      : window.waterMaskCache.dedupe(waterCacheKey, () =>
          window.api.dem.waterMask(waterParams, signal).then(res => {
            if (!res.error && res.data && !res.data.error) {
              window.waterMaskCache?.set(waterCacheKey, res.data);
            }
            return res;
          })
        );

    const hydroPromise = window.api.dem.hydrology(hydroParams, signal);

    const [waterRes, hydroRes] = await Promise.all([waterPromise, hydroPromise]);

    if (waterRes.error || hydroRes.error) {
      const err = waterRes.error || hydroRes.error;
      window.showToast?.('Failed to load: ' + err, 'error');
      window.setLayerStatus?.('waterHydrology', 'error');
      if (statusEl) statusEl.textContent = 'Error: ' + err;
      return;
    }

    const { data: waterData } = waterRes;
    const { data: hydroData } = hydroRes;


    // Render combined canvas
    window.appState.lastWaterHydrology = { waterData, hydroData };
    const combinedCanvas = renderWaterHydrologyCombined(waterData, hydroData);
    if (combinedCanvas) {
      window.appState.waterHydrologyCanvas = combinedCanvas;
    }
    _renderOrderLegend(hydroData);

    window.setLayerStatus?.('waterHydrology', 'loaded');
    window.emitStackUpdate?.();

    const waterPct = waterData?.water_percentage?.toFixed(1) || '?';
    if (statusEl) {
      statusEl.textContent = `✓ Hydrology loaded | Water: ${waterPct}% | Source: ${hydroSource}`;
    }
    window.showToast?.('Hydrology loaded', 'success');

  } catch (err) {
    if (err.name !== 'AbortError') {
      window.showToast?.('Load failed: ' + err.message, 'error');
      window.setLayerStatus?.('waterHydrology', 'error');
      if (statusEl) statusEl.textContent = 'Error: ' + err.message;
    }
  } finally {
    if (loadBtn) {
      loadBtn.disabled = false;
      loadBtn.textContent = loadBtn.dataset.origText || '🌊 Load Hydrology';
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Clear
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Clear the combined layer and reset state.
 */
window.clearWaterHydrology = function clearWaterHydrology() {
  if (window.appState) {
    window.appState.waterHydrologyCanvas = null;
    window.appState.lastWaterHydrology = null;
  }
  _renderOrderLegend(null);

  const statusEl = document.getElementById('waterHydrologyStatus');
  if (statusEl) statusEl.textContent = '';

  window.setLayerStatus?.('waterHydrology', 'empty');
  window.updateStackedLayers?.();
};

// ─────────────────────────────────────────────────────────────────────────────
// Exports
// ─────────────────────────────────────────────────────────────────────────────

