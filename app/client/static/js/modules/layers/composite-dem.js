/**
 * composite-dem.js — Composite DEM layer.
 *
 * Combines per-layer height contributions (in metres) onto the base DEM
 * and renders the result as a new canvas in the stacked layers view.
 *
 * Each loaded data source (water mask, city/OSM, land cover, trails) produces
 * a signed height-delta array.  A user-adjustable weight (scalar) controls
 * each contribution.  The architecture is designed so a neural network can
 * later replace the linear scalers with learned weights.
 *
 * Two outputs (F-ARCH "one two-stage mesh pipeline"):
 *   - the 2D preview: terrain channels + the rasterised OSM feature channels
 *     (buildings, roads, waterways, walls), so the user sees everything;
 *   - the terrain heightfield: terrain channels only. This is what Apply
 *     writes into lastDemData.values and what the export spec describes. In
 *     3D, OSM features come from the City Model layers (vector stage), and
 *     baking their pixels into the terrain as well would print them twice.
 *
 * Public API (on window):
 *   window.computeCompositeDem()       — recompute & render the composite layer
 *   window.applyCompositeToDem()       — replace lastDemData with the terrain-only composite
 *   window.buildCompositeLayerSpec(o)  — the same stack as a server layer list
 *                                        (terrain only unless o.includeFeatures)
 *   window.setupCompositeDemControls() — wire UI event listeners
 *
 * The pure spec builder lives in composite-spec.js.
 * See docs/design/composite-dem-design.md for full design rationale.
 */

import { buildCompositeLayerSpec as _buildSpec, anyFeatureChannelEnabled } from './composite-spec.js';

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
    landcoverEnabled: true,
    treeHeight: 8.0,   // metres to add for tree-cover pixels
    landcoverWeight: 0.0,   // off by default — speculative
    satEnabled: true,
    vegHeight: 5.0,   // max metres for satellite-derived vegetation
    satWeight: 0.0,   // off by default — weakest signal
    trailsEnabled: true,
    trailsSkiEnabled: true,
    trailsHikingEnabled: true,
    // Off by default, like land cover and vegetation: a user who has loaded the
    // Trails layer to look at it has not thereby asked for it to be carved into
    // an export, and switching it on silently would change every mesh built
    // from a region that happens to have trails loaded.
    trailsWeight: 0.0,
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

/**
 * Last computed terrain-only composite (same dims as DEM): every channel
 * except the rasterised OSM features. This — not the preview — is what
 * applyCompositeToDem() makes the active DEM.
 */
let _terrainValues = null;
let _terrainMin = 0;
let _terrainMax = 0;
/** True when the last preview included OSM feature channels the terrain omits. */
let _previewHadFeatures = false;

/** Cached satellite pixel data to avoid repeated getImageData() calls. */
let _satPixelCache = null;  // { canvas, width, height, data }


// ─── Private helpers ─────────────────────────────────────────────────────────

/** Add weight × feature into composite in-place; no-op if weight==0 or feat==null. */
function _addWeightedFeature(composite, feat, weight) {
    if (!(weight > 0) || !feat) return;
    if (feat.length !== composite.length) {
        // Reading past a short channel yields undefined, and `+= weight * undefined`
        // turns the rest of the composite into NaN — which renders as a blank
        // region rather than an error. Refuse the channel and say so.
        console.warn(`[composite] channel length ${feat.length} != ${composite.length}; skipped`);
        return;
    }
    for (let i = 0; i < composite.length; i++) composite[i] += weight * feat[i];
}

/** [min, max] of a numeric array. */
function _minMax(values) {
    let lo = Infinity, hi = -Infinity;
    for (let i = 0; i < values.length; i++) {
        const v = values[i];
        if (v < lo) lo = v;
        if (v > hi) hi = v;
    }
    return [lo, hi];
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

    // The server rasterizes at the requested size and THEN projects. demW/demH are
    // the DEM's post-projection size, so sending them re-projects the raster a
    // second time: the returned grid comes back narrower than the DEM (a 600×505
    // DEM projects to 463×505; feeding 463 back yields 357) and its contents are
    // squeezed relative to the terrain underneath. Send the DEM's pre-projection
    // size instead, so one projection lands the raster exactly on the DEM grid.
    const srcDims = window.appState?.lastDemData?.sourceDimensions;
    const reqH = (projection !== 'none' && srcDims?.length === 2) ? srcDims[0] : demH;
    const reqW = (projection !== 'none' && srcDims?.length === 2) ? srcDims[1] : demW;

    const cacheKey = `${bbox.north.toFixed(4)}_${bbox.south.toFixed(4)}_${bbox.east.toFixed(4)}_${bbox.west.toFixed(4)}_${reqW}x${reqH}->${demW}x${demH}_${projection}_${maintainDimensions}_${detail}`;
    const cached = window.appState.compositeCityRaster;
    if (cached?.cacheKey === cacheKey) return cached;

    const { data, error } = await (window.api?.composite?.cityRaster({
        north: bbox.north, south: bbox.south,
        east: bbox.east, west: bbox.west,
        width: reqW, height: reqH,
        projection,
        maintain_dimensions: maintainDimensions,
        detail,
        clip_valid_region: clipValidRegion,
    }) ?? Promise.resolve({ data: null, error: 'api not ready' }));

    if (error || !data) {
        console.warn('[composite] city-raster fetch failed:', error);
        return null;
    }

    // Store normalized component arrays for slider-driven recombination.
    // Resample to the DEM grid if the server's output still differs: every
    // consumer indexes these arrays with the DEM's stride, and a length or
    // stride mismatch silently produces NaNs and a sheared city layer rather
    // than an error.
    const gotW = Number(data.width) || demW;
    const gotH = Number(data.height) || demH;
    const fit = (arr) => _resampleGrid(new Float32Array(arr), gotW, gotH, demW, demH);
    if (gotW !== demW || gotH !== demH) {
        console.warn(`[composite] city raster ${gotW}×${gotH} != DEM ${demW}×${demH}; resampling`);
    }
    const raster = {
        cacheKey,
        buildings: fit(data.buildings),
        roads: fit(data.roads),
        waterways: fit(data.waterways),
        walls: fit(data.walls),
        width: demW,
        height: demH,
    };
    if (window.appState) window.appState.compositeCityRaster = raster;
    return raster;
}

/**
 * Nearest-neighbour resample of a flat grid onto another grid size.
 * Returns the input untouched when the sizes already match.
 * @param {Float32Array} src - Source values, row-major
 */
function _resampleGrid(src, srcW, srcH, dstW, dstH) {
    if (srcW === dstW && srcH === dstH) return src;
    const out = new Float32Array(dstW * dstH);
    if (!srcW || !srcH) return out;
    for (let y = 0; y < dstH; y++) {
        const sy = Math.min(Math.floor(y * srcH / dstH), srcH - 1);
        for (let x = 0; x < dstW; x++) {
            const sx = Math.min(Math.floor(x * srcW / dstW), srcW - 1);
            out[y * dstW + x] = src[sy * srcW + sx];
        }
    }
    return out;
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
 * Trail contribution: the signed relief the trails layer already rasterized.
 *
 * The grids come straight from the last /api/terrain/trails response and are
 * already in metres, signed the way the fetch section's Relief control asked
 * for — negative engraves the trail into the terrain. Nothing is rescaled here;
 * the composite weight is the only multiplier.
 *
 * Only the linework contributes. The area masks are display-only tints, and
 * folding one in would drop or raise a whole ski area's worth of mountain face.
 */
function _trailsContribution(demW, demH) {
    const data = window.appState?.lastTrailsData;
    if (!data) return null;
    const dims = data.grid_dimensions;
    if (!dims || dims.length < 2) return null;
    const gh = dims[0], gw = dims[1];
    if (!gw || !gh) return null;

    const ski = params.trailsSkiEnabled ? window.decodeSkiTrailValues?.(data) : null;
    const hiking = params.trailsHikingEnabled
        ? window.decodeHikingTrailValues?.(data) : null;
    if (!ski?.length && !hiking?.length) return null;

    const out = new Float32Array(demW * demH);
    for (let y = 0; y < demH; y++) {
        const srcY = Math.min(Math.floor(y * gh / demH), gh - 1);
        for (let x = 0; x < demW; x++) {
            const srcX = Math.min(Math.floor(x * gw / demW), gw - 1);
            const idx = srcY * gw + srcX;
            const s = ski ? ski[idx] : 0;
            // Where a piste and a path cross, take the deeper cut rather than
            // summing them — two overlapping trails are one trench, not one
            // twice as deep.
            const k = hiking ? hiking[idx] : 0;
            out[y * demW + x] = Math.abs(s) >= Math.abs(k) ? s : k;
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
    if (window.scheduler?.yield) return window.scheduler.yield();
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
        _terrainValues = null;
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
        _terrainValues = null;
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
    const cityAnyEnabled = anyFeatureChannelEnabled(params);

    let waterFeat = null, cityFeat = null, cityComponents = null, lcFeat = null,
        satFeat = null, trailsFeat = null;
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
    if (params.landcoverEnabled && params.landcoverWeight > 0) {
        try { lcFeat = _landcoverContribution(W, H); } catch (e) { console.warn('[composite] landcover:', e); }
        await _yieldToMain(); if (gen !== _computeGen) return;
    }
    if (params.satEnabled && params.satWeight > 0) {
        try { satFeat = _satelliteContribution(W, H); } catch (e) { console.warn('[composite] satellite:', e); }
        await _yieldToMain(); if (gen !== _computeGen) return;
    }
    if (params.trailsEnabled && params.trailsWeight > 0) {
        try { trailsFeat = _trailsContribution(W, H); } catch (e) { console.warn('[composite] trails:', e); }
        await _yieldToMain(); if (gen !== _computeGen) return;
    }

    // Store feature channels on appState for ML pipeline access (lazy — only non-null)
    if (window.appState) {
        window.appState.compositeFeatures = {
            dem: demFeat, water: waterFeat, city: cityFeat, cityComponents,
            landcover: lcFeat, satellite: satFeat, trails: trailsFeat,
            width: W, height: H,
        };
    }

    // Terrain stage first (DEM already folded into `composite` above).
    _addWeightedFeature(composite, waterFeat, params.waterEnabled ? params.waterWeight : 0);
    _addWeightedFeature(composite, lcFeat, params.landcoverEnabled ? params.landcoverWeight : 0);
    _addWeightedFeature(composite, satFeat, params.satEnabled ? params.satWeight : 0);
    _addWeightedFeature(composite, trailsFeat, params.trailsEnabled ? params.trailsWeight : 0);

    // Snapshot the terrain heightfield before the OSM feature channels go on:
    // Apply uses this, so the mesh gets buildings/roads/waterways/walls only
    // from the City Model's vector stage, never also as raster pixels.
    const terrain = new Float32Array(composite);
    const [tMin, tMax] = _minMax(terrain);

    // Feature channels: 2D preview only.
    _addWeightedFeature(composite, cityFeat, cityAnyEnabled ? 1 : 0);

    await _yieldToMain(); if (gen !== _computeGen) return;

    const [cMin, cMax] = cityFeat ? _minMax(composite) : [tMin, tMax];
    _terrainValues = terrain;
    _terrainMin = tMin;
    _terrainMax = tMax;
    _previewHadFeatures = !!cityFeat;

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
        landcover: lcFeat, satellite: satFeat, trails: trailsFeat,
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
    trails: 'compositeHistTrails',
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

// ─── Server-side layer spec ───────────────────────────────────────

/**
 * Translate this panel's flat parameters into the ordered layer list the
 * server understands (MergeLayerSpec in app/server/schemas.py).
 *
 * The browser and the server both know how to composite: the browser keeps
 * computing the live preview so a slider drag stays instant, and the server
 * recomputes the terrain stack for the 3D mesh and every export. The pure
 * translation is composite-spec.js; this wrapper supplies the DEM snapshot.
 *
 * Default is the terrain-only spec (what Apply publishes and export sends as
 * `composite_layers`). `{ includeFeatures: true }` adds the rasterised OSM
 * channels, for a 2D preview only — never send that one to a mesh endpoint.
 *
 * @param {{includeFeatures?:boolean}} [opts]
 * @returns {{layers: Array<Object>, unsupported: string[]}}
 */
window.buildCompositeLayerSpec = function buildCompositeLayerSpec(opts = {}) {
    const snapshot = window.appState?.lastDemRequest?.dem;
    const dim = parseInt(snapshot?.dim
        ?? document.getElementById('paramDim')?.value, 10) || 600;
    const demSource = snapshot?.dem_source
        || document.getElementById('paramDemSource')?.value || 'local';
    const detail = window.appState?.osmCityDetail || 'full';
    return _buildSpec(params, { dim, demSource, detail }, opts);
};

// ─── Apply to DEM ────────────────────────────────────────────────────────────

/**
 * Replace lastDemData.values with the terrain-only composite, making it the
 * active DEM for export and 3D preview. The OSM feature channels shown in the
 * 2D preview are left out on purpose — see the module header.
 */
window.applyCompositeToDem = function applyCompositeToDem() {
    if (!_terrainValues) {
        window.showToast?.('No composite data — enable and compute first', 'warning');
        return;
    }
    const dem = window.appState?.lastDemData;
    if (!dem) return;

    // A copy, so a later recompute (which replaces _terrainValues) and an
    // in-place edit of lastDemData.values can never alias each other.
    dem.values = new Float32Array(_terrainValues);
    dem.min = _terrainMin;
    dem.max = _terrainMax;
    window.appState.lastDemData = dem;

    // Also update originalDemValues so curve editor works from the composite
    if (window.appState) {
        window.appState.originalDemValues = new Float32Array(_terrainValues);
        // Tell export-handlers.js a composite is active. It prefers the
        // server-side layer spec below; the values stay available as the
        // fallback for a channel the server cannot yet build.
        window.appState._newCompositeApplied = true;
        window.appState.compositeLayerSpec = window.buildCompositeLayerSpec();
    }

    // Re-render the DEM canvas
    window.recolorDEM?.();
    window.showToast?.(_previewHadFeatures
        ? 'Composite terrain applied as DEM (OSM buildings/roads/waterways/walls '
            + 'come from the City Model layers in 3D, not the terrain)'
        : 'Composite applied as DEM', 'success', _previewHadFeatures ? 6000 : undefined);
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
    if (params.landcoverEnabled && params.landcoverWeight > 0) parts.push(`+ LC (${params.landcoverWeight.toFixed(1)}×)`);
    if (params.satEnabled && params.satWeight > 0) parts.push(`+ Veg (${params.satWeight.toFixed(1)}×)`);
    if (params.trailsEnabled && params.trailsWeight > 0) parts.push(`± Trails (${params.trailsWeight.toFixed(1)}×)`);
    el.textContent = (parts.join(' ') || '(no channels enabled)')
        + (anyFeatureChannelEnabled(params) ? ' — OSM channels: 2D preview only' : '');
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
        compositeTrailsWeight: 'trailsWeight',
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
        compositeLandcoverEnabled: 'landcoverEnabled',
        compositeSatEnabled: 'satEnabled',
        compositeTrailsEnabled: 'trailsEnabled',
        compositeTrailsSkiEnabled: 'trailsSkiEnabled',
        compositeTrailsHikingEnabled: 'trailsHikingEnabled',
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
