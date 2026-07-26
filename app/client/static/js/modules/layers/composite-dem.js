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
    demEnabled: true,
    demWeight: 1.0,   // scales the base DEM elevation itself
    waterEnabled: true,
    waterDepth: 5.0,   // metres to subtract where water detected
    waterWeight: 1.0,
    buildingsEnabled: true,
    buildingScale: 1.0,   // multiplier on OSM building heights
    roadsEnabled: true,
    roadCut: 0.5,   // metres to subtract for roads
    waterwaysEnabled: true,
    riverDepth: 3.0,   // metres to subtract for waterway LineStrings
    wallsEnabled: true,
    wallScale: 1.0,   // multiplier on OSM wall heights
    treeHeight: 8.0,   // metres to add for tree-cover pixels
    landcoverWeight: 0.0,   // off by default — speculative
    vegHeight: 5.0,   // max metres for satellite-derived vegetation
    satWeight: 0.0,   // off by default — weakest signal
};

// ESA WorldCover class → height offset (metres)
const ESA_HEIGHT_TABLE = {
    10: 8.0,   // Tree cover
    20: 1.5,   // Shrubland
    30: 0.3,   // Grassland
    40: 0.8,   // Cropland
    50: 6.0,   // Built-up
    60: 0.0,   // Bare / sparse
    70: 0.0,   // Snow and ice
    80: 0.0,   // Water bodies (handled by water layer)
    90: -0.5,   // Herbaceous wetland
    95: 3.0,   // Mangroves
    100: 0.0,   // Moss and lichen
};

// ─── State ───────────────────────────────────────────────────────────────────

/** Current parameter values — initialised from DEFAULTS, updated by UI. */
const params = { ...DEFAULTS };

/** LUT cache — keyed by colormap name, invalidated on colormap change. */
const _lutCache = {};

/** Last computed composite Float32Array (same dims as DEM). */
let _compositeValues = null;
let _compositeWidth = 0;
let _compositeHeight = 0;
let _compositeMin = 0;
let _compositeMax = 0;

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
    if (!wm) return null;

    const wmVals = window.decodeWaterMask?.(wm);
    if (!wmVals?.length) return null;
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

    // Must match the tier loadCityData() actually fetched with, or the OSM
    // cache lookup (keyed by min_area, which differs per tier) misses.
    const detail = window.appState?.osmCityDetail || 'full';

    // Client-side cache keyed by bbox + dims + projection so re-fetching only happens on change
    const { projection, maintainDimensions, clipValidRegion } = window.getProjectionParams();
    const cacheKey = `${bbox.north.toFixed(4)}_${bbox.south.toFixed(4)}_${bbox.east.toFixed(4)}_${bbox.west.toFixed(4)}_${demW}x${demH}_${projection}_${maintainDimensions}_${detail}`;
    const cached = window.appState.compositeCityRaster;
    if (cached?.cacheKey === cacheKey) return cached;

    const { data, error } = await (window.api?.composite?.cityRaster({
        north: bbox.north, south: bbox.south,
        east: bbox.east, west: bbox.west,
        width: demW, height: demH,
        projection,
        maintain_dimensions: maintainDimensions,
        detail,
        clip_valid_region: clipValidRegion,
    }) ?? Promise.resolve({ data: null, error: 'api not ready' }));

    if (error || !data) {
        console.warn('[composite] city-raster fetch failed:', error);
        return null;
    }

    // Store normalized component arrays for slider-driven recombination
    const raster = {
        cacheKey,
        buildings: new Float32Array(data.buildings),
        roads: new Float32Array(data.roads),
        waterways: new Float32Array(data.waterways),
        walls: new Float32Array(data.walls),
        width: data.width,
        height: data.height,
    };
    if (window.appState) window.appState.compositeCityRaster = raster;
    return raster;
}

/**
 * Combine city raster components using current per-component toggle/weight
 * values. Each of buildings/roads/waterways/walls contributes independently
 * (or not at all when its toggle is off) — no single combined cityWeight.
 * Returns a Float32Array or null.
 */
function _cityContributionFromRaster(raster) {
    if (!raster) return null;
    const { buildings, roads, waterways, walls } = raster;
    const n = buildings.length;
    const out = new Float32Array(n);
    const bScale = params.buildingsEnabled ? params.buildingScale : 0;
    const rCut = params.roadsEnabled ? params.roadCut : 0;
    const rDepth = params.waterwaysEnabled ? params.riverDepth : 0;
    const wScale = params.wallsEnabled ? params.wallScale : 0;
    for (let i = 0; i < n; i++) {
        out[i] = bScale * buildings[i]
            - rCut * roads[i]
            - rDepth * waterways[i]
            + wScale * walls[i];
    }
    return out;
}

/**
 * Split a combined city raster into its four per-component contribution
 * arrays (each already scaled by its own toggle/weight), for per-sublayer
 * histograms. Returns null if no raster is available.
 */
function _cityComponentContributions(raster) {
    if (!raster) return null;
    const { buildings, roads, waterways, walls } = raster;
    const n = buildings.length;
    const mk = (src, scale, sign) => {
        if (!scale) return null;
        const out = new Float32Array(n);
        for (let i = 0; i < n; i++) out[i] = sign * scale * src[i];
        return out;
    };
    return {
        buildings: mk(buildings, params.buildingsEnabled ? params.buildingScale : 0, 1),
        roads: mk(roads, params.roadsEnabled ? params.roadCut : 0, -1),
        waterways: mk(waterways, params.waterwaysEnabled ? params.riverDepth : 0, -1),
        walls: mk(walls, params.wallsEnabled ? params.wallScale : 0, 1),
    };
}

/**
 * Land cover contribution: map ESA class → height offset.
 */
function _landcoverContribution(demW, demH) {
    // The server sends ESA class data base64-packed (esa_values_b64), not as a
    // plain esa_values array — window.decodeEsaValues() handles that (and the
    // legacy plain-array shape too). Both the standalone ESA endpoint response
    // (lastEsaData) and the water-mask response (lastWaterMaskData, which also
    // embeds ESA classes) use the same esa_values_b64/esa_dimensions fields.
    const esa = window.appState?.lastEsaData;
    const wm = window.appState?.lastWaterMaskData;
    const hasEsa = (d) => d && (d.esa_values_b64 || d.esa_values?.length);
    const src = hasEsa(esa) ? esa : wm;
    if (!hasEsa(src)) return null;

    const esaVals = window.decodeEsaValues(src);
    if (!esaVals?.length) return null;
    const esaDims = src.esa_dimensions || src.water_mask_dimensions;
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

// ─── Async yield helper ──────────────────────────────────────────────────────

/**
 * Yield to the browser event loop between expensive computation chunks.
 * Uses scheduler.yield() when available (Chrome 115+), falls back to RAF.
 */
function _yieldToMain() {
    if (typeof scheduler !== 'undefined' && scheduler.yield) return scheduler.yield();
    return new Promise(resolve => requestAnimationFrame(resolve));
}

// ─── Orchestrator ────────────────────────────────────────────────────────────

/** Cancel token — incremented on each new computeCompositeDem() call to abort stale runs. */
let _computeGen = 0;

/**
 * Recompute the composite DEM from base DEM + all layer contributions.
 * Renders result to the layerCompositeDemCanvas in the stacked view.
 * Yields to the browser every ~10k pixels to avoid main-thread freezes.
 */
window.computeCompositeDem = async function computeCompositeDem() {
    const gen = ++_computeGen;
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

    // A region must be selected (we need a bbox for water/city/landcover
    // fetches), but a real DEM is not required — some regions have no local
    // elevation coverage (see terrain.py's dem_empty flag) or the user simply
    // hasn't loaded one yet. In that case fall back to a flat zero-elevation
    // baseline at a reasonable resolution so water/city/land-cover/satellite
    // contributions can still be computed and rendered on their own, instead
    // of the composite silently producing nothing.
    const region = window.appState?.currentDemBbox || window.appState?.selectedRegion;
    if (!region) {
        _compositeValues = null;
        return;
    }

    const dem = window.appState?.lastDemData;
    let values, width, height;
    if (dem?.values?.length) {
        ({ values, width, height } = dem);
    } else {
        const dim = parseInt(document.getElementById('paramDim')?.value) || 200;
        width = dim;
        height = dim;
        values = new Float32Array(width * height); // all-zero baseline
    }
    const W = width, H = height;

    // DEM is itself a toggleable, weighted channel now rather than an
    // unconditional starting point — when disabled, composite starts from
    // zero and is built purely from the other contributions.
    const composite = new Float32Array(W * H);
    let demFeat = null;
    if (params.demEnabled) {
        demFeat = new Float32Array(values);
        for (let i = 0; i < composite.length; i++) composite[i] = params.demWeight * demFeat[i];
    }

    // Only compute contributions when at least one relevant toggle is on
    // (skip expensive work). Yield between each contribution so the browser
    // can handle input events.
    const cityAnyEnabled = params.buildingsEnabled || params.roadsEnabled
        || params.waterwaysEnabled || params.wallsEnabled;

    let waterFeat = null, cityFeat = null, cityComponents = null, lcFeat = null, satFeat = null;
    if (params.waterEnabled && params.waterWeight > 0) {
        try { waterFeat = _waterContribution(W, H); } catch (e) { console.warn('[composite] water:', e); }
        await _yieldToMain(); if (gen !== _computeGen) return;
    }
    if (cityAnyEnabled) {
        try {
            const cityRaster = await _fetchCityRaster(W, H);
            if (gen !== _computeGen) return;
            cityFeat = _cityContributionFromRaster(cityRaster);
            cityComponents = _cityComponentContributions(cityRaster);
        } catch (e) { console.warn('[composite] city:', e); }
        await _yieldToMain(); if (gen !== _computeGen) return;
    }
    if (params.landcoverWeight > 0) {
        try { lcFeat = _landcoverContribution(W, H); } catch (e) { console.warn('[composite] landcover:', e); }
        await _yieldToMain(); if (gen !== _computeGen) return;
    }
    if (params.satWeight > 0) {
        try { satFeat = _satelliteContribution(W, H); } catch (e) { console.warn('[composite] satellite:', e); }
        await _yieldToMain(); if (gen !== _computeGen) return;
    }

    // Store feature channels on appState for ML pipeline access (lazy — only non-null)
    if (window.appState) {
        window.appState.compositeFeatures = {
            dem: demFeat, water: waterFeat, city: cityFeat, cityComponents,
            landcover: lcFeat, satellite: satFeat, width: W, height: H,
        };
    }

    // Add weighted contributions (DEM already folded into `composite` above)
    _addWeightedFeature(composite, waterFeat, params.waterEnabled ? params.waterWeight : 0);
    _addWeightedFeature(composite, cityFeat, cityAnyEnabled ? 1 : 0);
    _addWeightedFeature(composite, lcFeat, params.landcoverWeight);
    _addWeightedFeature(composite, satFeat, params.satWeight);

    await _yieldToMain(); if (gen !== _computeGen) return;

    // Compute min/max
    let cMin = Infinity, cMax = -Infinity;
    for (let i = 0; i < composite.length; i++) {
        const v = composite[i];
        if (v < cMin) cMin = v;
        if (v > cMax) cMax = v;
    }
    _compositeValues = composite;
    _compositeWidth = W;
    _compositeHeight = H;
    _compositeMin = cMin;
    _compositeMax = cMax;

    await _yieldToMain(); if (gen !== _computeGen) return;

    // Render to a hidden source canvas (stacked-layers.js copies it to the DOM layer canvas)
    const rawCanvas = document.createElement('canvas');
    rawCanvas.width = W;
    rawCanvas.height = H;
    _renderCompositeCanvas(rawCanvas, composite, W, H, cMin, cMax);

    if (window.appState) window.appState.compositeDemSourceCanvas = rawCanvas;

    // Update stats display
    const statsEl = document.getElementById('compositeStats');
    if (statsEl) {
        statsEl.textContent = `${cMin.toFixed(1)}m — ${cMax.toFixed(1)}m`;
    }

    _renderAllHistograms({
        dem: params.demEnabled ? demFeat : null,
        water: waterFeat, ...cityComponents,
        landcover: lcFeat, satellite: satFeat,
        composite,
    });
};

/**
 * Render composite values to a canvas using the DEM colormap.
 */
function _renderCompositeCanvas(canvas, values, width, height, vmin, vmax) {
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    const img = ctx.createImageData(width, height);
    const data = img.data;
    const range = (vmax - vmin) || 1;
    const invRange = 1 / range;

    const colormap = window.getLayerColormap?.('composite') || document.getElementById('demColormap')?.value || 'terrain';

    // LUT cached by colormap — only rebuilt when colormap changes
    if (!_lutCache[colormap]) {
        _lutCache[colormap] = window.buildColorLUT(colormap);
    }
    const lut = _lutCache[colormap];

    for (let i = 0; i < values.length; i++) {
        const v = values[i];
        const t = Math.max(0, Math.min(1, (v - vmin) * invRange));
        const li = Math.round(t * 1023) * 3;
        const di = i * 4;
        data[di] = lut[li];
        data[di + 1] = lut[li + 1];
        data[di + 2] = lut[li + 2];
        data[di + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
}

// ─── Histograms ──────────────────────────────────────────────────────────────

/** Maps a contribution-channel key to the id prefix of its histogram <canvas>. */
const _HISTOGRAM_CANVAS_IDS = {
    dem: 'compositeHistDem',
    water: 'compositeHistWater',
    buildings: 'compositeHistBuildings',
    roads: 'compositeHistRoads',
    waterways: 'compositeHistWaterways',
    walls: 'compositeHistWalls',
    landcover: 'compositeHistLandcover',
    satellite: 'compositeHistSatellite',
    composite: 'compositeHistCombined',
};

/**
 * Draw a simple binned-count histogram of `values` into `canvas`, with the
 * actual min/max elevation range for this layer's data labeled in the
 * corners (the bin range is already fully dynamic per-layer; this just
 * surfaces that range instead of leaving the axis unlabeled).
 * No chart library — a few dozen filled rects is plenty for a quick
 * "is this layer actually contributing anything" glance.
 */
function _drawHistogram(canvas, values, bins = 24) {
    if (!canvas || !values || !values.length) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    let vmin = Infinity, vmax = -Infinity;
    for (let i = 0; i < values.length; i++) {
        const v = values[i];
        if (v < vmin) vmin = v;
        if (v > vmax) vmax = v;
    }
    const range = (vmax - vmin) || 1;
    const counts = new Uint32Array(bins);
    for (let i = 0; i < values.length; i++) {
        let bi = Math.floor(((values[i] - vmin) / range) * bins);
        if (bi >= bins) bi = bins - 1;
        if (bi < 0) bi = 0;
        counts[bi]++;
    }
    let maxCount = 1;
    for (let i = 0; i < bins; i++) if (counts[i] > maxCount) maxCount = counts[i];

    const labelH = 10;
    const plotH = h - labelH;
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent-color') || '#4a90d9';
    const barW = w / bins;
    for (let i = 0; i < bins; i++) {
        const barH = (counts[i] / maxCount) * (plotH - 2);
        ctx.fillRect(i * barW, plotH - barH, Math.max(1, barW - 1), barH);
    }

    ctx.font = '8px sans-serif';
    ctx.fillStyle = '#888';
    ctx.textBaseline = 'bottom';
    ctx.textAlign = 'left';
    ctx.fillText(`${vmin.toFixed(1)}m`, 1, h);
    ctx.textAlign = 'right';
    ctx.fillText(`${vmax.toFixed(1)}m`, w - 1, h);
}

/**
 * Render every per-layer histogram plus the combined composite histogram.
 * Silently skips channels whose canvas isn't in the DOM or whose feature is
 * null (layer disabled / not yet computed) — canvas keeps its last frame.
 */
function _renderAllHistograms(channels) {
    for (const [key, canvasId] of Object.entries(_HISTOGRAM_CANVAS_IDS)) {
        const canvas = document.getElementById(canvasId);
        if (!canvas) continue;
        const values = channels[key];
        if (!values || !values.length) { canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height); continue; }
        _drawHistogram(canvas, values);
    }
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

    dem.values = _compositeValues;
    dem.min = _compositeMin;
    dem.max = _compositeMax;
    window.appState.lastDemData = dem;

    // Also update originalDemValues so curve editor works from the composite
    if (window.appState) {
        window.appState.originalDemValues = new Float32Array(_compositeValues);
        // Tell export-handlers.js to ship these values inline instead of
        // resolving the plain (non-composite) DEM from the server cache.
        window.appState._newCompositeApplied = true;
    }

    // Re-render the DEM canvas
    window.recolorDEM?.();
    window.showToast?.('Composite applied as DEM', 'success');
};

// ─── Preview & thumbnail ─────────────────────────────────────────────────────

/**
 * Preview the composite layer — switch view mode and trigger recompute.
 */
window.previewComposite = async function previewComposite() {
    const enableCb = document.getElementById('compositeEnabled');
    if (enableCb && !enableCb.checked) {
        enableCb.checked = true;
    }
    window.setStackMode?.('CompositeDem');
    await window.computeCompositeDem();
    window.updateStackedLayers?.();
    _updatePreviewThumb();
    _updateContribStatus();
    window.showToast?.('Composite preview updated', 'info');
};

/**
 * Render a scaled-down preview of the composite into the thumbnail canvas.
 */
function _updatePreviewThumb() {
    const thumbCanvas = document.getElementById('compositePreviewThumb');
    const src = window.appState?.compositeDemSourceCanvas;
    if (!thumbCanvas || !src || !src.width || !src.height) return;

    const ctx = thumbCanvas.getContext('2d');
    ctx.clearRect(0, 0, thumbCanvas.width, thumbCanvas.height);
    ctx.drawImage(src, 0, 0, thumbCanvas.width, thumbCanvas.height);
}

/**
 * Update the contribution status text showing which layers are active.
 */
function _updateContribStatus() {
    const el = document.getElementById('compositeContribStatus');
    if (!el) return;

    const parts = [];
    if (params.demEnabled) parts.push(`DEM (${params.demWeight.toFixed(1)}×)`);
    if (params.waterEnabled && params.waterWeight > 0) parts.push(`− Water (${params.waterDepth.toFixed(1)}m, ${params.waterWeight.toFixed(1)}×)`);
    if (params.buildingsEnabled) parts.push(`+ Buildings (${params.buildingScale.toFixed(1)}×)`);
    if (params.roadsEnabled) parts.push(`− Roads (${params.roadCut.toFixed(1)}m)`);
    if (params.waterwaysEnabled) parts.push(`− Waterways (${params.riverDepth.toFixed(1)}m)`);
    if (params.wallsEnabled) parts.push(`+ Walls (${params.wallScale.toFixed(1)}×)`);
    if (params.landcoverWeight > 0) parts.push(`+ LC (${params.landcoverWeight.toFixed(1)}×)`);
    if (params.satWeight > 0) parts.push(`+ Veg (${params.satWeight.toFixed(1)}×)`);
    el.textContent = parts.join(' ') || '(no channels enabled)';
}

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
        compositeDemWeight: 'demWeight',
        compositeWaterDepth: 'waterDepth',
        compositeWaterWeight: 'waterWeight',
        compositeBuildingScale: 'buildingScale',
        compositeRoadCut: 'roadCut',
        compositeRiverDepth: 'riverDepth',
        compositeWallScale: 'wallScale',
        compositeTreeHeight: 'treeHeight',
        compositeLandcoverWeight: 'landcoverWeight',
        compositeVegHeight: 'vegHeight',
        compositeSatWeight: 'satWeight',
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

    // Wire per-channel enable toggles (DEM + each city sub-layer).
    const toggleMap = {
        compositeDemEnabled: 'demEnabled',
        compositeWaterEnabled: 'waterEnabled',
        compositeBuildingsEnabled: 'buildingsEnabled',
        compositeRoadsEnabled: 'roadsEnabled',
        compositeWaterwaysEnabled: 'waterwaysEnabled',
        compositeWallsEnabled: 'wallsEnabled',
    };
    for (const [elemId, paramKey] of Object.entries(toggleMap)) {
        const cb = document.getElementById(elemId);
        if (!cb) continue;
        cb.checked = params[paramKey];
        cb.addEventListener('change', () => {
            params[paramKey] = cb.checked;
            _scheduleRecompute();
        });
    }

    // Per-layer composite colormap — re-render on change (invalidate LUT first).
    document.getElementById('compositeColormap')?.addEventListener('change', () => {
        for (const k of Object.keys(_lutCache)) delete _lutCache[k];
        _scheduleRecompute();
    });

    // Preview button
    document.getElementById('previewCompositeBtn')?.addEventListener('click', () => {
        window.previewComposite();
    });

    // Apply button
    document.getElementById('applyCompositeToDemBtn')?.addEventListener('click', () => {
        window.applyCompositeToDem();
    });

    // Split view toggle — Composite DEM | Satellite side-by-side
    const splitBtn = document.getElementById('splitViewToggleBtn');
    splitBtn?.addEventListener('click', () => {
        const next = !window.isSplitViewEnabled?.();
        window.setSplitViewEnabled?.(next);
        splitBtn.classList.toggle('active', next);
    });
};

// Debounced async recompute — uses setTimeout so async completion is awaited
let _recomputeTimer = null;
function _scheduleRecompute() {
    clearTimeout(_recomputeTimer);
    _recomputeTimer = setTimeout(async () => {
        await window.computeCompositeDem();
        window.updateStackedLayers?.();
        _updatePreviewThumb();
        _updateContribStatus();
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

    // Invalidate LUT cache when colormap changes so colours stay correct.
    // Listens on the event bus (emitted by event-listeners-map.js) to avoid
    // a second direct DOM listener on the same element.
    window.events?.on(window.EV?.COLORMAP_CHANGE, () => {
        for (const k of Object.keys(_lutCache)) delete _lutCache[k];
        _scheduleRecompute();
    });

    // Note: No STACKED_UPDATE listener needed — the offscreen source canvas
    // persists and updateStackedLayers reads it each time it redraws.
});
