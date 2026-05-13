/**
 * modules/water-hydrology-combined.js — Unified water mask + hydrology layer.
 *
 * Loads and composites water (ESA water + land cover) and hydrology (rivers) 
 * into a single combined layer for rendering in the stacked view.
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
 *   window.updateCacheStatusUI()    — from app.js
 *   window.updateStackedLayers()    — from stacked-layers.js
 *   (renderer logic mirrored from water-mask.js and hydrology-overlay.js)
 */

// ─────────────────────────────────────────────────────────────────────────────
// Module-scope state
// ─────────────────────────────────────────────────────────────────────────────

let _combinedAbortController = null;
let _combinedInflightPromise = null;
let _combinedInflightKey = null;

let lastWaterData = null;
let lastHydrologyData = null;

// ─────────────────────────────────────────────────────────────────────────────
// Composite Rendering
// ─────────────────────────────────────────────────────────────────────────────

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
    
    // 1. Render water mask first (base layer)
    if (waterValues) {
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

    // 2. Render hydrology on top (rivers with blue tint)
    if (hydroValues && hydroData) {
        const hydroDims = hydroData.river_grid_dimensions || [h, w];
        const hydroH = hydroDims[0] || h;
        const hydroW = hydroDims[1] || w;
        const minD = hydroData.depression_m || -5.0; // keep sign; values are typically <= 0

        // Read current pixels (water already rendered) so hydrology can blend on top.
        const baseImg = ctx.getImageData(0, 0, w, h);
        const px = baseImg.data;

        for (let hy = 0; hy < hydroH; hy++) {
            const ty = Math.min(h - 1, Math.max(0, Math.floor((hy / hydroH) * h)));
            for (let hx = 0; hx < hydroW; hx++) {
                const hi = hy * hydroW + hx;
                const v = hydroValues[hi] ?? 0;
                if (v === 0) continue;

                const tx = Math.min(w - 1, Math.max(0, Math.floor((hx / hydroW) * w)));
                const base = (ty * w + tx) * 4;

                // Match original hydrology overlay behavior: deeper river => more opacity.
                const t = Math.min(1, Math.max(0, v / minD));
                const alpha = Math.round(60 + t * 160); // 60–220
                const riverAlpha = alpha / 255;

                // Existing pixel from water layer.
                const r = px[base];
                const g = px[base + 1];
                const b = px[base + 2];
                const a = px[base + 3];

                // Blend river blue (30, 100, 200) over current pixel.
                px[base] = Math.round(r * (1 - riverAlpha) + 30 * riverAlpha);
                px[base + 1] = Math.round(g * (1 - riverAlpha) + 100 * riverAlpha);
                px[base + 2] = Math.round(b * (1 - riverAlpha) + 200 * riverAlpha);
                px[base + 3] = Math.max(a, alpha);
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
 *   - Hydrology: #hydroSource, #hydroDim, #hydroDepressionM, #hydroMinOrder, etc.
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

    // Build stable key for in-flight dedupe
    const waterDim = parseInt(document.getElementById('waterResolution')?.value || '600');
    const hydroDim = parseInt(document.getElementById('hydroDim')?.value || '600');
    const waterDataset = document.getElementById('waterDataset')?.value || 'esa';
    const hydroSource = document.getElementById('hydroSource')?.value || 'hydrorivers';
    
    const inflightKey = JSON.stringify({
        n: north, s: south, e: east, w: west, 
        waterDim, hydroDim, waterDataset, hydroSource
    });
    
    if (_combinedInflightPromise && _combinedInflightKey === inflightKey) {
        return _combinedInflightPromise;
    }

    // Cancel previous requests
    if (_combinedAbortController) _combinedAbortController.abort();
    _combinedAbortController = new AbortController();
    const signal = _combinedAbortController.signal;

    _combinedInflightKey = inflightKey;
    _combinedInflightPromise = _performCombinedLoad(
        north, south, east, west, waterDim, hydroDim, waterDataset, hydroSource, signal
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
async function _performCombinedLoad(north, south, east, west, waterDim, hydroDim, waterDataset, hydroSource, signal) {
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
        // Fetch water and hydrology in parallel
        const waterPromise = window.api.dem.waterMask(
            new URLSearchParams({ north, south, east, west, dim: waterDim, dataset: waterDataset }),
            signal
        );
        
        const hydroParams = new URLSearchParams({
            north, south, east, west, dim: hydroDim, source: hydroSource,
            depression_m: parseFloat(document.getElementById('hydroDepressionM')?.value ?? '-5.0'),
        });
        if (hydroSource === 'hydrorivers') {
            hydroParams.append('min_order', parseInt(document.getElementById('hydroMinOrder')?.value ?? '3'));
            hydroParams.append('order_exponent', parseFloat(document.getElementById('hydroOrderExponent')?.value ?? '1.5'));
            hydroParams.append('width_factor', parseFloat(document.getElementById('hydroWidthFactor')?.value ?? '0.5'));
        }
        
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

        lastWaterData = waterData;
        lastHydrologyData = hydroData;

        // Render combined canvas
        const combinedCanvas = renderWaterHydrologyCombined(waterData, hydroData);
        if (combinedCanvas) {
            window.appState.waterHydrologyCanvas = combinedCanvas;
        }

        window.setLayerStatus?.('waterHydrology', 'loaded');
        window.updateCacheStatusUI?.();
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
    }
    lastWaterData = null;
    lastHydrologyData = null;
    
    const statusEl = document.getElementById('waterHydrologyStatus');
    if (statusEl) statusEl.textContent = '';
    
    window.setLayerStatus?.('waterHydrology', 'empty');
    window.updateStackedLayers?.();
};

// ─────────────────────────────────────────────────────────────────────────────
// Exports
// ─────────────────────────────────────────────────────────────────────────────

window.lastWaterData = () => lastWaterData;
window.lastHydrologyData = () => lastHydrologyData;
