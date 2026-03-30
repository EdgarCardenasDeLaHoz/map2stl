/**
 * composite-dem.js — Composite DEM layer.
 *
 * Combines per-layer height contributions (in metres) onto the base DEM
 * and renders the result as a new canvas in the stacked layers view.
 *
 * Each loaded data source (water mask, city/OSM, land cover) produces a
 * signed height-delta array.  A user-adjustable weight (scalar) controls
 * each contribution.  The architecture is designed so a neural network can
 * later replace the linear scalers with learned weights.
 *
 * Public API (on window):
 *   window.computeCompositeDem()       — recompute & render the composite layer
 *   window.applyCompositeToDem()       — replace lastDemData with composite
 *   window.setupCompositeDemControls() — wire UI event listeners
 *
 * See COMPOSITE_DEM_DESIGN.md for full design rationale.
 */

// ─── Defaults ────────────────────────────────────────────────────────────────

const DEFAULTS = {
    waterDepth:       5.0,   // metres to subtract where water detected
    waterWeight:      1.0,
    buildingScale:    1.0,   // multiplier on OSM building heights
    roadCut:          0.5,   // metres to subtract for roads
    riverDepth:       3.0,   // metres to subtract for waterway LineStrings
    cityWeight:       1.0,
    treeHeight:       8.0,   // metres to add for tree-cover pixels
    landcoverWeight:  0.0,   // off by default — speculative
    vegHeight:        5.0,   // max metres for satellite-derived vegetation
    satWeight:        0.0,   // off by default — weakest signal
};

// ESA WorldCover class → height offset (metres)
const ESA_HEIGHT_TABLE = {
    10:  8.0,   // Tree cover
    20:  1.5,   // Shrubland
    30:  0.3,   // Grassland
    40:  0.8,   // Cropland
    50:  6.0,   // Built-up
    60:  0.0,   // Bare / sparse
    70:  0.0,   // Snow and ice
    80:  0.0,   // Water bodies (handled by water layer)
    90: -0.5,   // Herbaceous wetland
    95:  3.0,   // Mangroves
    100: 0.0,   // Moss and lichen
};

// ─── State ───────────────────────────────────────────────────────────────────

/** Current parameter values — initialised from DEFAULTS, updated by UI. */
const params = { ...DEFAULTS };

/** LUT cache — keyed by colormap name, invalidated on colormap change. */
const _lutCache = {};

/** Last computed composite Float32Array (same dims as DEM). */
let _compositeValues = null;
let _compositeWidth  = 0;
let _compositeHeight = 0;
let _compositeMin    = 0;
let _compositeMax    = 0;

/** Cached satellite pixel data to avoid repeated getImageData() calls. */
let _satPixelCache = null;  // { canvas, width, height, data }


// ─── Private helpers ─────────────────────────────────────────────────────────

/** Add weight × feature into composite in-place; no-op if weight==0 or feat==null. */
function _addWeightedFeature(composite, feat, weight) {
    if (weight > 0 && feat) {
        for (let i = 0; i < composite.length; i++) composite[i] += weight * feat[i];
    }
}

/** Unit suffix for a slider label ('m' for distance, '' for weights/scales). */
function _unitSuffix(elemId) {
    return (elemId.includes('Weight') || elemId.includes('Scale')) ? '' : ' m';
}

// ─── Contribution functions ──────────────────────────────────────────────────
// Each returns a Float32Array of signed height deltas (metres), same length as
// the base DEM values array.  Returns null if the source data is unavailable.

/**
 * Water contribution: subtract depth where water mask == 1.
 */
function _waterContribution(demW, demH) {
    const wm = window.appState?.lastWaterMaskData;
    if (!wm?.water_mask_values?.length) return null;

    const wmVals = wm.water_mask_values;
    const dims = wm.water_mask_dimensions;
    if (!dims || dims.length < 2) return null;
    const wmH = dims[0], wmW = dims[1];
    if (!wmW || !wmH) return null;

    const out = new Float32Array(demW * demH);
    const depth = params.waterDepth;

    for (let y = 0; y < demH; y++) {
        const srcY = Math.min(Math.floor(y * wmH / demH), wmH - 1);
        for (let x = 0; x < demW; x++) {
            const srcX = Math.min(Math.floor(x * wmW / demW), wmW - 1);
            const val = wmVals[srcY * wmW + srcX];
            if (val > 0.5) out[y * demW + x] = -depth;
        }
    }
    return out;
}

/**
 * Fetch (or return cached) city raster from the backend.
 * Returns a Float32Array for the combined city contribution, or null.
 * Computation is done server-side with PIL — far faster than JS polygon fill.
 */
async function _fetchCityRaster(demW, demH) {
    if (!window.appState?.osmCityData) return null;   // no city data loaded
    const bbox = window.appState?.currentDemBbox;
    if (!bbox) return null;

    // Client-side cache keyed by bbox + dims so re-fetching only happens on change
    const cacheKey = `${bbox.north.toFixed(4)}_${bbox.south.toFixed(4)}_${bbox.east.toFixed(4)}_${bbox.west.toFixed(4)}_${demW}x${demH}`;
    const cached = window.appState.compositeCityRaster;
    if (cached?.cacheKey === cacheKey) return cached;

    const { data, error } = await (window.api?.composite?.cityRaster({
        north: bbox.north, south: bbox.south,
        east:  bbox.east,  west:  bbox.west,
        width: demW, height: demH,
    }) ?? Promise.resolve({ data: null, error: 'api not ready' }));

    if (error || !data) {
        console.warn('[composite] city-raster fetch failed:', error);
        return null;
    }

    // Store normalized component arrays for slider-driven recombination
    const raster = {
        cacheKey,
        buildings:  new Float32Array(data.buildings),
        roads:      new Float32Array(data.roads),
        waterways:  new Float32Array(data.waterways),
        walls:      new Float32Array(data.walls),
        width:      data.width,
        height:     data.height,
    };
    if (window.appState) window.appState.compositeCityRaster = raster;
    return raster;
}

/**
 * Combine city raster components using current slider values.
 * Returns a Float32Array or null.
 */
function _cityContributionFromRaster(raster) {
    if (!raster) return null;
    const { buildings, roads, waterways, walls } = raster;
    const n = buildings.length;
    const out = new Float32Array(n);
    const bScale  = params.buildingScale;
    const rCut    = params.roadCut;
    const rDepth  = params.riverDepth;
    for (let i = 0; i < n; i++) {
        out[i] = bScale * buildings[i]
               - rCut   * roads[i]
               - rDepth * waterways[i]
               + bScale * walls[i];
    }
    return out;
}

/**
 * Land cover contribution: map ESA class → height offset.
 */
function _landcoverContribution(demW, demH) {
    const wm = window.appState?.lastWaterMaskData;
    if (!wm?.esa_values?.length) return null;

    const esaVals = wm.esa_values;
    const esaDims = wm.esa_dimensions || wm.water_mask_dimensions;
    if (!esaDims || esaDims.length < 2) return null;
    const esaH = esaDims[0], esaW = esaDims[1];
    if (!esaW || !esaH) return null;

    // Allow user to override tree height
    const treeH = params.treeHeight;
    const table = { ...ESA_HEIGHT_TABLE, 10: treeH };

    const out = new Float32Array(demW * demH);
    for (let y = 0; y < demH; y++) {
        const srcY = Math.min(Math.floor(y * esaH / demH), esaH - 1);
        for (let x = 0; x < demW; x++) {
            const srcX = Math.min(Math.floor(x * esaW / demW), esaW - 1);
            const cls = esaVals[srcY * esaW + srcX];
            out[y * demW + x] = table[cls] || 0;
        }
    }
    return out;
}

/**
 * Satellite contribution: derive pseudo-NDVI from RGB and map to vegetation height.
 */
function _satelliteContribution(demW, demH) {
    const satCanvas = window.appState?.satImgSourceCanvas;
    if (!satCanvas) return null;

    const satW = satCanvas.width;
    const satH = satCanvas.height;
    if (!satW || !satH) return null;

    // Cache pixel data — getImageData() is expensive (~5-15ms for 512x512)
    if (!_satPixelCache || _satPixelCache.canvas !== satCanvas ||
        _satPixelCache.width !== satW || _satPixelCache.height !== satH) {
        const ctx = satCanvas.getContext('2d');
        _satPixelCache = {
            canvas: satCanvas, width: satW, height: satH,
            data: ctx.getImageData(0, 0, satW, satH).data,
        };
    }
    const imgData = _satPixelCache.data;
    const vegH = params.vegHeight;

    const out = new Float32Array(demW * demH);
    for (let y = 0; y < demH; y++) {
        const srcY = Math.min(Math.floor(y * satH / demH), satH - 1);
        for (let x = 0; x < demW; x++) {
            const srcX = Math.min(Math.floor(x * satW / demW), satW - 1);
            const idx = (srcY * satW + srcX) * 4;
            const r = imgData[idx];
            const g = imgData[idx + 1];
            const greenness = (g - r) / (g + r + 1);
            if (greenness > 0.1) {
                out[y * demW + x] = greenness * vegH;
            }
        }
    }
    return out;
}

// ─── Orchestrator ────────────────────────────────────────────────────────────

/**
 * Recompute the composite DEM from base DEM + all layer contributions.
 * Renders result to the layerCompositeDemCanvas in the stacked view.
 */
window.computeCompositeDem = async function computeCompositeDem() {
    const enabled = document.getElementById('compositeEnabled')?.checked;
    if (!enabled) {
        _compositeValues = null;
        // Clear offscreen canvas so stacked view shows nothing
        if (window.appState?.compositeDemSourceCanvas) {
            const src = window.appState.compositeDemSourceCanvas;
            src.getContext('2d').clearRect(0, 0, src.width, src.height);
        }
        return;
    }

    const dem = window.appState?.lastDemData;
    if (!dem?.values?.length) {
        _compositeValues = null;
        return;
    }

    const { values, width, height } = dem;
    const W = width, H = height;

    // Start from base DEM (values is a plain Array; Float32Array constructor copies it)
    const composite = new Float32Array(values);

    // Only compute contributions for non-zero weights (skip expensive work)
    let waterFeat = null, cityFeat = null, lcFeat = null, satFeat = null;
    if (params.waterWeight > 0) {
        try { waterFeat = _waterContribution(W, H); } catch (e) { console.warn('[composite] water:', e); }
    }
    if (params.cityWeight > 0) {
        try {
            const cityRaster = await _fetchCityRaster(W, H);
            cityFeat = _cityContributionFromRaster(cityRaster);
        } catch (e) { console.warn('[composite] city:', e); }
    }
    if (params.landcoverWeight > 0) {
        try { lcFeat  = _landcoverContribution(W, H); } catch (e) { console.warn('[composite] landcover:', e); }
    }
    if (params.satWeight > 0) {
        try { satFeat = _satelliteContribution(W, H); } catch (e) { console.warn('[composite] satellite:', e); }
    }

    // Store feature channels on appState for ML pipeline access (lazy — only non-null)
    if (window.appState) {
        window.appState.compositeFeatures = {
            water: waterFeat, city: cityFeat,
            landcover: lcFeat, satellite: satFeat, width: W, height: H,
        };
    }

    // Add weighted contributions
    _addWeightedFeature(composite, waterFeat, params.waterWeight);
    _addWeightedFeature(composite, cityFeat,  params.cityWeight);
    _addWeightedFeature(composite, lcFeat,    params.landcoverWeight);
    _addWeightedFeature(composite, satFeat,   params.satWeight);

    // Compute min/max
    let cMin = Infinity, cMax = -Infinity;
    for (let i = 0; i < composite.length; i++) {
        const v = composite[i];
        if (v < cMin) cMin = v;
        if (v > cMax) cMax = v;
    }
    _compositeValues = composite;
    _compositeWidth  = W;
    _compositeHeight = H;
    _compositeMin    = cMin;
    _compositeMax    = cMax;

    // Render to a hidden source canvas (stacked-layers.js copies it to the DOM layer canvas)
    const rawCanvas = document.createElement('canvas');
    rawCanvas.width  = W;
    rawCanvas.height = H;
    _renderCompositeCanvas(rawCanvas, composite, W, H, cMin, cMax);

    // Apply projection to match DEM/water/satellite canvases (prevents overlay misalignment)
    const bbox = window.appState?.currentDemBbox;
    const projCanvas = (bbox && window.applyProjection) ? window.applyProjection(rawCanvas, bbox) : rawCanvas;
    if (window.appState) window.appState.compositeDemSourceCanvas = projCanvas;

    // Update stats display
    const statsEl = document.getElementById('compositeStats');
    if (statsEl) {
        statsEl.textContent = `${cMin.toFixed(1)}m — ${cMax.toFixed(1)}m`;
    }
};

/**
 * Render composite values to a canvas using the DEM colormap.
 */
function _renderCompositeCanvas(canvas, values, width, height, vmin, vmax) {
    canvas.width  = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    const img = ctx.createImageData(width, height);
    const data = img.data;
    const range = (vmax - vmin) || 1;
    const invRange = 1 / range;

    const colormap = document.getElementById('demColormap')?.value || 'terrain';

    // LUT cached by colormap — only rebuilt when colormap changes
    if (!_lutCache[colormap]) {
        const lut = new Uint8Array(1024 * 3);
        for (let i = 0; i < 1024; i++) {
            const t = i / 1023;
            const rgb = window.mapElevationToColor?.(t, colormap) || [t, t, t];
            lut[i * 3]     = Math.round(rgb[0] * 255);
            lut[i * 3 + 1] = Math.round(rgb[1] * 255);
            lut[i * 3 + 2] = Math.round(rgb[2] * 255);
        }
        _lutCache[colormap] = lut;
    }
    const lut = _lutCache[colormap];

    for (let i = 0; i < values.length; i++) {
        const v = values[i];
        const t = Math.max(0, Math.min(1, (v - vmin) * invRange));
        const li = Math.round(t * 1023) * 3;
        const di = i * 4;
        data[di]     = lut[li];
        data[di + 1] = lut[li + 1];
        data[di + 2] = lut[li + 2];
        data[di + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
}

// ─── Apply to DEM ────────────────────────────────────────────────────────────

/**
 * Replace lastDemData.values with the composite values, making it the
 * active DEM for export and 3D preview.
 */
window.applyCompositeToDem = function applyCompositeToDem() {
    if (!_compositeValues) {
        window.showToast?.('No composite data — enable and compute first', 'warning');
        return;
    }
    const dem = window.appState?.lastDemData;
    if (!dem) return;

    // renderDEMCanvas expects a plain Array (not Float32Array)
    const newValues = Array.from(_compositeValues);
    dem.values = newValues;
    dem.min = _compositeMin;
    dem.max = _compositeMax;
    window.appState.lastDemData = dem;

    // Also update originalDemValues so curve editor works from the composite
    if (window.appState) {
        window.appState.originalDemValues = new Float32Array(_compositeValues);
    }

    // Re-render the DEM canvas
    window.recolorDEM?.();
    window.showToast?.('Composite applied as DEM', 'success');
};

// ─── UI wiring ───────────────────────────────────────────────────────────────

/**
 * Wire all composite DEM control event listeners.
 * Called once from DOMContentLoaded.
 */
window.setupCompositeDemControls = function setupCompositeDemControls() {
    const enableCb = document.getElementById('compositeEnabled');

    // Recompute (or clear) when enable checkbox changes
    enableCb?.addEventListener('change', () => {
        // Switch to composite view mode when enabling
        if (enableCb.checked) window.setStackMode?.('CompositeDem');
        _scheduleRecompute();
    });

    // Wire all sliders
    const sliderMap = {
        compositeWaterDepth:      'waterDepth',
        compositeWaterWeight:     'waterWeight',
        compositeBuildingScale:   'buildingScale',
        compositeRoadCut:         'roadCut',
        compositeRiverDepth:      'riverDepth',
        compositeCityWeight:      'cityWeight',
        compositeTreeHeight:      'treeHeight',
        compositeLandcoverWeight: 'landcoverWeight',
        compositeVegHeight:       'vegHeight',
        compositeSatWeight:       'satWeight',
    };

    for (const [elemId, paramKey] of Object.entries(sliderMap)) {
        const slider = document.getElementById(elemId);
        if (!slider) continue;
        slider.value = params[paramKey];
        const label = document.getElementById(elemId + 'Label');
        const unit = _unitSuffix(elemId);
        if (label) label.textContent = parseFloat(params[paramKey]).toFixed(1) + unit;

        slider.addEventListener('input', () => {
            params[paramKey] = parseFloat(slider.value);
            if (label) label.textContent = parseFloat(slider.value).toFixed(1) + unit;
            _scheduleRecompute();
        });
    }

    // Apply button
    document.getElementById('applyCompositeToDemBtn')?.addEventListener('click', () => {
        window.applyCompositeToDem();
    });
};

// Debounced async recompute — uses setTimeout so async completion is awaited
let _recomputeTimer = null;
function _scheduleRecompute() {
    clearTimeout(_recomputeTimer);
    _recomputeTimer = setTimeout(async () => {
        await window.computeCompositeDem();
        window.updateStackedLayers?.();
    }, 80);
}

// ─── Reactive subscriptions ──────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    window.setupCompositeDemControls?.();

    // Recompute when source data changes
    if (window.appState?.on) {
        window.appState.on('lastDemData', () => _scheduleRecompute());
        window.appState.on('lastWaterMaskData', () => _scheduleRecompute());
        window.appState.on('osmCityData', () => _scheduleRecompute());
    }

    // Invalidate LUT cache when colormap changes so colours stay correct
    document.getElementById('demColormap')?.addEventListener('change', () => {
        for (const k of Object.keys(_lutCache)) delete _lutCache[k];
        _scheduleRecompute();
    });

    // Note: No STACKED_UPDATE listener needed — the offscreen source canvas
    // persists and updateStackedLayers reads it each time it redraws.
});
