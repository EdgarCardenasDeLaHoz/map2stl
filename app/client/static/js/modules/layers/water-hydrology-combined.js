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
 *   loadWaterHydrology({grid}) — fetch water + hydrology in parallel and render combined
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
 *   window.decodeHydrologyValues(data) — from ui-helpers.js
 *   window.setLayerStatus(name, status) — from app.js
 *   window.emitStackUpdate()        — from ui-helpers.js
 *   (renderer logic mirrored from water-mask.js)
 */

import {
  readHydrologyRiverControls, loadedDemGrid, hydrologyPrintQuery, readRiverDepthScale,
  NEED_DEM_MESSAGE, riverLoadingMessage,
} from './hydrology-print.js';
import {
  riverPixelColor, riverEdgeMask, orderColor, RIVER_EDGE_RGB, RIVER_EDGE_ALPHA,
} from './river-style.js';

// ─────────────────────────────────────────────────────────────────────────────
// Module-scope state
// ─────────────────────────────────────────────────────────────────────────────

let _combinedAbortController = null;
let _combinedInflightPromise = null;
let _combinedInflightKey = null;


// ─────────────────────────────────────────────────────────────────────────────
// Composite Rendering
// ─────────────────────────────────────────────────────────────────────────────


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
    const [r, g, b] = orderColor(o);
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


  // Canvas size: the rivers grid when there is one. Sizing it from the water mask
  // (600 px) sampled a 1000 px river grid down and broke the one-pixel rivers
  // into dashes (Colombia, 2026-10-04); the mask is resampled onto it instead.
  let w, h;
  if (hydroValues && hydroData?.river_grid_dimensions) {
    [h, w] = hydroData.river_grid_dimensions;
  } else if (waterData?.water_mask_dimensions) {
    [h, w] = waterData.water_mask_dimensions;
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
    const [wmH, wmW] = waterData.water_mask_dimensions || [h, w];
    for (let i = 0; i < w * h; i++) {
      // Nearest water-mask cell of canvas pixel i (the grids may differ in size).
      const my = Math.min(wmH - 1, Math.floor(((Math.floor(i / w) + 0.5) / h) * wmH));
      const mx = Math.min(wmW - 1, Math.floor((((i % w) + 0.5) / w) * wmW));
      const val = waterValues[my * wmW + mx];
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
  // Rivers are light cyan with a dark edge (river-style.js), so they read over land
  // cover and satellite; open water keeps the mid blue.
  if (hydroValues && hydroData) {
    const hydroDims = hydroData.river_grid_dimensions || [h, w];
    const hydroH = hydroDims[0] || h;
    const hydroW = hydroDims[1] || w;
    // Deepest carve (print model) or the old max depression; keep the sign.
    const minD = hydroData.depression_m || -5.0;
    const byOrder = _hydroColorMode() === 'order';
    // Orders also in Depth mode: they tell open water from rivers.
    const orders = window.decodeHydrologyOrders?.(hydroData) || null;
    const waterCode = hydroData.order_water_code ?? 100;

    // Read current pixels (water already rendered) so hydrology can blend on top.
    const baseImg = ctx.getImageData(0, 0, w, h);
    const px = baseImg.data;
    const riverMask = new Uint8Array(w * h);
    const blend = (base, r, g, b, alpha) => {
      const a01 = alpha / 255;
      px[base] = Math.round(px[base] * (1 - a01) + r * a01);
      px[base + 1] = Math.round(px[base + 1] * (1 - a01) + g * a01);
      px[base + 2] = Math.round(px[base + 2] * (1 - a01) + b * a01);
      px[base + 3] = Math.max(px[base + 3], alpha);
    };

    for (let ty = 0; ty < h; ty++) {
      const hy = Math.min(hydroH - 1, Math.floor(((ty + 0.5) / h) * hydroH));
      for (let tx = 0; tx < w; tx++) {
        const hx = Math.min(hydroW - 1, Math.floor(((tx + 0.5) / w) * hydroW));
        const hi = hy * hydroW + hx;
        const o = orders ? (orders[hi] | 0) : null;
        const c = riverPixelColor(hydroValues[hi] ?? 0, minD, o, waterCode, byOrder);
        if (!c) continue;
        const i = ty * w + tx;
        if (o !== waterCode) riverMask[i] = 1;
        blend(i * 4, c[0], c[1], c[2], c[3]);
      }
    }
    const edge = riverEdgeMask(riverMask, w, h);
    const [er, eg, eb] = RIVER_EDGE_RGB;
    for (let i = 0; i < edge.length; i++) {
      if (edge[i]) blend(i * 4, er, eg, eb, RIVER_EDGE_ALPHA);
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
 * @param {{grid?: {dim:number, demSource:string}}} [opts]  grid: carve on the DEM being
 *   loaded (hydrology-print.js nextDemGrid) instead of the loaded one, so a projection switch
 *   can request rivers in parallel with its DEM (event-listeners-map.js)
 * @returns {Promise<boolean|undefined>} true once rendered (with or without rivers)
 */
window.loadWaterHydrology = async function loadWaterHydrology({ grid: nextGrid = null } = {}) {
  const boundingBox = window.getBoundingBox?.();
  const selectedRegion = window.appState?.selectedRegion;

  const coords = window.getBboxCoords(boundingBox, selectedRegion);
  if (!coords) {
    window.showToast?.('Select a region before loading hydrology.', 'warning');
    return;
  }
  const { north, south, east, west } = coords;

  // The preview is the print carve on the loaded DEM, so it needs that DEM.
  const grid = nextGrid || loadedDemGrid(window.appState);
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

  const mine = _performCombinedLoad(
    north, south, east, west, waterDim, waterDataset, hydroParams, river.hydroSource, signal,
    projection, maintainDimensions ? 'true' : 'false', clipValidRegion ? 'true' : 'false',
  );
  _combinedInflightKey = inflightKey;
  _combinedInflightPromise = mine;

  try {
    return await mine;
  } finally {
    // A newer call may own the slot by now: clearing it would drop its dedupe.
    if (_combinedInflightPromise === mine) {
      _combinedInflightPromise = null;
      _combinedInflightKey = null;
    }
  }
};

/**
 * Internal function to perform the actual load and render.
 */
async function _performCombinedLoad(north, south, east, west, waterDim, waterDataset, hydroParams, hydroSource, signal, projection = 'none', maintainDims = 'false', clipNans = 'false') {

  const statusEl = document.getElementById('waterHydrologyStatus');
  const loadBtn = document.getElementById('loadWaterHydrologyBtn');

  // "Loading rivers… large region, about a minute": the Amazon at size 1 took 57 s.
  const loadingText = riverLoadingMessage(
    { north, south, east, west }, parseInt(hydroParams.get?.('min_order'), 10));
  if (statusEl) statusEl.textContent = loadingText;
  if (loadBtn) {
    loadBtn.disabled = true;
    loadBtn.dataset.origText = loadBtn.textContent;
    loadBtn.textContent = '⏳ Loading rivers…';
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
    // Superseded: api.js turns the AbortError into {error}, which must not show as a failure
    // over the newer request.
    if (signal.aborted) return;

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
    return true;

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
  // Next frame, not now: loadDEM clears this on every reload, and a synchronous full stack
  // redraw there cost a long task before the DEM request went out.
  window.emitStackUpdate?.();
};

// ─────────────────────────────────────────────────────────────────────────────
// Exports
// ─────────────────────────────────────────────────────────────────────────────

