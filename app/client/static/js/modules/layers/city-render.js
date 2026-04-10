/**
 * city-render.js — Render functions for the city/OSM overlay.
 *
 * Loaded immediately after city-overlay.js.  Depends on city-overlay.js for:
 *   window._drawCityCanvas      — core per-layer draw routine
 *   window._buildGeoToPx        — projection-aware coordinate mapper
 *   window._prebakeFeatures     — pre-bake pixel coords into Float32Array
 *   window._makeCacheKey        — stable cache-key serialiser
 *   window._cityRenderState     — shared accessor for _stackLayer, _demLayer,
 *                                 _offscreenOk, _LAYER_NAMES, _LAYER_TOGGLES
 *   window._invalidateCityCache — bump data version & clear all layer caches
 *
 * Public API (all attached to window):
 *   window.renderCityOverlay        — debounced render onto stacked-layers canvas
 *   window.renderCityOnDEM          — debounced render onto DEM canvas overlay
 *   window._clearCityRasterCache    — reset city raster cache on region change
 *   window._updateCitiesLoadButton  — show/hide "Load Cities" button by region size
 *   window.loadCityRaster           — fetch /api/cities/raster and paint result
 *   window._setupCityRasterLayer    — wire city raster visibility toggle & opacity slider
 *   window._cancelCityRenders       — cancel any pending RAF renders (called by clearCityOverlay)
 *
 * PERF6B — Web Worker offload
 *   _doRenderCityOverlay dispatches the draw to city-worker.js when OffscreenCanvas
 *   is available.  The worker receives pre-baked Float32Array buffers (already computed
 *   by _prebakeFeatures) + DOM-extracted style/toggle values, so it never touches the DOM.
 *   The main thread blits the returned ImageBitmap onto the visible overlay canvas.
 *   Stale replies are discarded via a monotonically-increasing generation counter.
 *   Falls back to the synchronous _drawCityCanvas path if workers are unavailable.
 */

// ---------------------------------------------------------------------------
// PERF6B — Web Worker singleton
// ---------------------------------------------------------------------------

let _cityWorker = null;
let _cityWorkerGen = 0;   // incremented per dispatch; stale replies are dropped

/** Initialise (or reuse) the city rendering worker. Returns null if unsupported. */
function _getCityWorker() {
    if (_cityWorker) return _cityWorker;
    if (typeof Worker === 'undefined') return null;
    try {
        _cityWorker = new Worker('/static/js/workers/city-worker.js');
        _cityWorker.onerror = (e) => {
            console.warn('city-worker error — disabling worker path:', e.message);
            _cityWorker = null;
        };
    } catch (_) {
        _cityWorker = null;
    }
    return _cityWorker;
}

/**
 * Serialise a GeoJSON FeatureCollection's pre-baked pixel data into a
 * plain-object array that can be structured-cloned to the worker.
 * Only features with valid _px (baked) buffers are included; unbaked features
 * are silently skipped (they'll be picked up on the next bake cycle).
 *
 * @param {Object|null} geojson  - GeoJSON FeatureCollection with _px on features
 * @param {string}      propKey  - property to copy ('height_m' or 'road_width_m')
 * @returns {{ features: BakedFeature[] } | null}
 */
function _serialiseLayer(geojson, propKey) {
    if (!geojson?.features?.length) return null;
    const features = [];
    for (const feat of geojson.features) {
        const px = feat._px;
        if (!px?.buf) continue;
        features.push({
            buf:          px.buf,         // Float32Array — transferred, not copied
            counts:       px.counts,      // Uint16Array  — transferred
            x0: px.x0, y0: px.y0,
            x1: px.x1, y1: px.y1,
            type:         feat.geometry?.type || '',
            [propKey]:    feat.properties?.[propKey] || 0,
        });
    }
    return features.length ? { features } : null;
}

/**
 * Collect all transferable ArrayBuffers from a serialised layer set.
 * Worker ownership means these buffers become neutered on the main thread —
 * but _prebakeFeatures will rebake them on the next render call, so this is safe.
 */
function _collectTransferables(layers) {
    const list = [];
    for (const layer of Object.values(layers)) {
        if (!layer) continue;
        for (const feat of layer.features) {
            if (feat.buf?.buffer)    list.push(feat.buf.buffer);
            if (feat.counts?.buffer) list.push(feat.counts.buffer);
        }
    }
    return list;
}

// ---------------------------------------------------------------------------
// Pure raster canvas renderer (no DEM state side-effects)
// ---------------------------------------------------------------------------

/**
 * Render an array of values to a canvas using the current colormap LUT.
 * Unlike renderDEMCanvas, this does NOT touch lastDemData or workflow state.
 * @param {number[]} values  - Flat array of values
 * @param {number}   width   - Grid width
 * @param {number}   height  - Grid height
 * @param {string}   colormap - Colormap name
 * @param {number}   vmin    - Min value for color mapping
 * @param {number}   vmax    - Max value for color mapping
 * @returns {HTMLCanvasElement}
 */
function _renderRasterCanvas(values, width, height, colormap, vmin, vmax) {
    const canvas = document.createElement('canvas');
    canvas.width  = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    const img = ctx.createImageData(width, height);
    const data = img.data;
    const range = (vmax - vmin) || 1;
    const invRange = 1 / range;

    // Build colour LUT
    const lut = new Uint8Array(1024 * 3);
    for (let i = 0; i < 1024; i++) {
        const t = i / 1023;
        const [r, g, b] = window.mapElevationToColor?.(t, colormap) || [0, 0, 0];
        lut[i * 3]     = Math.round((r || 0) * 255);
        lut[i * 3 + 1] = Math.round((g || 0) * 255);
        lut[i * 3 + 2] = Math.round((b || 0) * 255);
    }

    for (let i = 0; i < values.length; i++) {
        const v = values[i];
        const idx = i * 4;
        if (!Number.isFinite(v) || v === 0) {
            // Transparent for zero/nodata (no building/road here)
            data[idx] = data[idx + 1] = data[idx + 2] = data[idx + 3] = 0;
        } else {
            const t = Math.max(0, Math.min(1023, ((v - vmin) * invRange * 1023) | 0));
            data[idx]     = lut[t * 3];
            data[idx + 1] = lut[t * 3 + 1];
            data[idx + 2] = lut[t * 3 + 2];
            data[idx + 3] = 255;
        }
    }

    ctx.putImageData(img, 0, 0);
    return canvas;
}

// ---------------------------------------------------------------------------
// Debounce tokens — only used by render functions defined in this file.
// ---------------------------------------------------------------------------
let _stackRafId = null;
let _demRafId   = null;

/** Cancel any in-flight RAF renders.  Called by city-overlay.js clearCityOverlay(). */
window._cancelCityRenders = function () {
    if (_stackRafId) { cancelAnimationFrame(_stackRafId); _stackRafId = null; }
    if (_demRafId)   { cancelAnimationFrame(_demRafId);   _demRafId   = null; }
    // Bump generation so any in-flight worker reply is discarded
    _cityWorkerGen++;
};

// ---------------------------------------------------------------------------
// City raster state — only used by raster functions in this file.
// ---------------------------------------------------------------------------
let _lastCityRasterData = null;

// ---------------------------------------------------------------------------
// Public render functions — both debounced via requestAnimationFrame
// ---------------------------------------------------------------------------

/**
 * Render window.appState.osmCityData as a canvas overlay on the stacked layers view.
 * Debounced — multiple synchronous calls in the same frame coalesce to one render.
 */
window.renderCityOverlay = function renderCityOverlay() {
    if (_stackRafId) return;   // already scheduled this frame
    _stackRafId = requestAnimationFrame(() => {
        _stackRafId = null;
        _doRenderCityOverlay();
    });
};

function _doRenderCityOverlay() {
    const osmCityData = window.appState?.osmCityData;
    if (!osmCityData) return;

    const bbox = window.appState?.currentDemBbox || window.appState?.selectedRegion;
    if (!bbox) return;

    const stack = document.getElementById('layersStack');
    if (!stack) return;

    const { north, south, east, west } = bbox;
    const latRange = north - south;

    // Get or create overlay canvas inside the stack
    let overlay = stack.querySelector('.osm-overlay');
    if (!overlay) {
        overlay = document.createElement('canvas');
        overlay.className = 'osm-overlay layer-canvas';
        overlay.style.pointerEvents = 'none';
        overlay.style.zIndex = '10';
        stack.appendChild(overlay);
    }

    // Size the overlay to match the stack's full pixel dimensions
    const stackRect = stack.getBoundingClientRect();
    const W = Math.round(stackRect.width)  || 600;
    const H = Math.round(stackRect.height) || 400;

    // Letterbox target rect matching updateStackedLayers
    const latMid     = (north + south) / 2;
    const latCos     = Math.cos(latMid * Math.PI / 180);
    const bboxAspect = ((east - west) * latCos) / latRange;
    const stackAspect = W / H;
    let tX = 0, tY = 0, tW = W, tH = H;
    if (bboxAspect > stackAspect) {
        tW = W; tH = W / bboxAspect; tY = (H - tH) / 2;
    } else {
        tH = H; tW = H * bboxAspect; tX = (W - tW) / 2;
    }

    const stackZoom = window.appState?.stackZoom || { scale: 1, offsetX: 0, offsetY: 0 };
    const invZ      = 1 / (stackZoom.scale || 1);
    const bboxLonM  = (east - west) * latCos * 111_000;
    const bboxKey   = `${north.toFixed(4)},${south.toFixed(4)},${east.toFixed(4)},${west.toFixed(4)}`;
    // PERF2: invZ excluded from cache key — CSS transform covers intermediate zoom frames
    const cacheKey  = window._makeCacheKey(window._cityRenderState.cityDataVersion, W, H, bboxKey);

    overlay.width  = W;
    overlay.height = H;
    const ctx = overlay.getContext('2d');

    // Test OffscreenCanvas support once (same browser capability for the entire session)
    if (window._cityRenderState.offscreenOk === null) {
        try { new OffscreenCanvas(1, 1); window._cityRenderState.offscreenOk = true; }
        catch (_) { window._cityRenderState.offscreenOk = false; }
    }

    // Common setup used by all render paths
    const geoToPx  = window._buildGeoToPx(north, south, east, west, tX, tY, tW, tH);
    const clipRect = { x0: tX, y0: tY, x1: tX + tW, y1: tY + tH };
    const bakKey   = `stack|${W}|${H}|${bboxKey}|${document.getElementById('paramProjection')?.value || 'none'}`;

    const rs = window._cityRenderState;

    // PERF4: pre-bake pixel coords for all layers (no-op for features already baked for this bakKey)
    for (const layer of rs.LAYER_NAMES) {
        if (osmCityData[layer]?.features) window._prebakeFeatures(osmCityData[layer].features, geoToPx, bakKey);
    }
    if (osmCityData.walls?.features) window._prebakeFeatures(osmCityData.walls.features, geoToPx, bakKey);

    // Collect DOM style values now — worker cannot access the DOM
    const styles = {
        buildingsColor: document.getElementById('layerBuildingsColor')?.value || '#c8b89a',
        roadsColor:     document.getElementById('layerRoadsColor')?.value     || '#cc8844',
        waterwaysColor: document.getElementById('layerWaterwaysColor')?.value || '#4488cc',
        roadBaseWidth:  parseFloat(document.getElementById('cityRoadWidth')?.value) || 1.5,
        bboxLonM,
    };
    const toggles = {
        buildings: !!document.getElementById(rs.LAYER_TOGGLES.buildings)?.checked,
        roads:     !!document.getElementById(rs.LAYER_TOGGLES.roads)?.checked,
        waterways: !!document.getElementById(rs.LAYER_TOGGLES.waterways)?.checked,
    };

    // ── PERF6B: try Web Worker path ────────────────────────────────────────────
    const worker = rs.offscreenOk ? _getCityWorker() : null;

    if (worker) {
        // Check if all toggled layers are already cached for this cacheKey
        const allCached = rs.LAYER_NAMES.every(
            layer => !toggles[layer] || (rs.stackLayer[layer].key === cacheKey && rs.stackLayer[layer].canvas)
        );

        if (allCached) {
            // Fast path: just composite from existing per-layer caches
            ctx.clearRect(0, 0, W, H);
            for (const layer of rs.LAYER_NAMES) {
                if (!toggles[layer] || !rs.stackLayer[layer].canvas) continue;
                ctx.drawImage(rs.stackLayer[layer].canvas, 0, 0);
            }
        } else {
            // Slow path: dispatch to worker. Buffers are transferred (zero-copy).
            // _prebakeFeatures will rebake them on the next render call after transfer.
            const gen = ++_cityWorkerGen;
            const layers = {
                buildings: _serialiseLayer(osmCityData.buildings, 'height_m'),
                roads:     _serialiseLayer(osmCityData.roads,     'road_width_m'),
                waterways: _serialiseLayer(osmCityData.waterways, 'height_m'),
                walls:     _serialiseLayer(osmCityData.walls,     'height_m'),
            };
            const transferables = _collectTransferables(layers);

            // Invalidate baked caches for transferred layers so next bake recreates them
            window._invalidateCityCache();

            worker.onmessage = (e) => {
                const reply = e.data;
                if (reply.gen !== gen) return;  // stale — a newer render is in flight
                if (reply.type === 'bitmap') {
                    ctx.clearRect(0, 0, W, H);
                    ctx.drawImage(reply.bitmap, 0, 0);
                    reply.bitmap.close();
                } else if (reply.type === 'error') {
                    console.warn('city-worker render error:', reply.message);
                    _syncRenderCityOverlay(ctx, geoToPx, invZ, osmCityData, W, tW, bboxLonM, clipRect, cacheKey, rs);
                }
            };

            worker.postMessage({ type: 'render', gen, W, H, tX, tY, tW, tH, invZ, layers, styles, toggles }, transferables);
        }
    } else if (rs.offscreenOk) {
        // PERF6 Part A: per-layer OffscreenCanvas on main thread (no worker)
        _syncRenderCityOverlay(ctx, geoToPx, invZ, osmCityData, W, tW, bboxLonM, clipRect, cacheKey, rs);
    } else {
        // Fallback: draw all visible layers in one pass directly to visible canvas
        ctx.clearRect(0, 0, W, H);
        ctx.save();
        ctx.beginPath(); ctx.rect(tX, tY, tW, tH); ctx.clip();
        window._drawCityCanvas(ctx, geoToPx, invZ, osmCityData, W, tW, bboxLonM, clipRect, null);
        ctx.restore();
    }

    // Re-apply the current stackZoom CSS transform so this canvas stays aligned
    // with the DEM/Water/Sat layer canvases (which always carry the zoom transform).
    overlay.style.transformOrigin = '0 0';
    overlay.style.transform = `translate(${stackZoom.offsetX}px, ${stackZoom.offsetY}px) scale(${stackZoom.scale})`;

    // Also update the DEM canvas overlay (Cities 8) — scheduled, not inline.
    window.renderCityOnDEM?.();
}

/**
 * Synchronous per-layer OffscreenCanvas render (PERF6 Part A).
 * Used when workers are unavailable or as a fallback after worker error.
 */
function _syncRenderCityOverlay(ctx, geoToPx, invZ, osmCityData, W, tW, bboxLonM, clipRect, cacheKey, rs) {
    const H = ctx.canvas.height;
    for (const layer of rs.LAYER_NAMES) {
        if (rs.stackLayer[layer].key === cacheKey && rs.stackLayer[layer].canvas) continue;
        const offscreen = new OffscreenCanvas(W, H);
        const octx = offscreen.getContext('2d');
        octx.save();
        octx.beginPath(); octx.rect(clipRect.x0, clipRect.y0, clipRect.x1 - clipRect.x0, clipRect.y1 - clipRect.y0); octx.clip();
        window._drawCityCanvas(octx, geoToPx, invZ, osmCityData, W, tW, bboxLonM, clipRect, layer);
        octx.restore();
        rs.stackLayer[layer].canvas = offscreen;
        rs.stackLayer[layer].key    = cacheKey;
    }
    ctx.clearRect(0, 0, W, H);
    for (const layer of rs.LAYER_NAMES) {
        if (!document.getElementById(rs.LAYER_TOGGLES[layer])?.checked) continue;
        if (rs.stackLayer[layer].canvas) ctx.drawImage(rs.stackLayer[layer].canvas, 0, 0);
    }
}

/**
 * Render the city overlay directly onto the main DEM canvas in the Edit tab.
 * Cities 8: buildings + roads painted on top of the terrain image.
 * Debounced — multiple calls coalesce to one render per frame.
 * Only runs if the DEM canvas element is present and has non-zero dimensions.
 */
window.renderCityOnDEM = function renderCityOnDEM() {
    if (_demRafId) return;   // already scheduled this frame
    _demRafId = requestAnimationFrame(() => {
        _demRafId = null;
        _doRenderCityOnDEM();
    });
};

function _doRenderCityOnDEM() {
    const osmCityData = window.appState?.osmCityData;
    if (!osmCityData) return;

    const bbox = window.appState?.currentDemBbox || window.appState?.selectedRegion;
    if (!bbox) return;

    const demContainer = document.getElementById('demImage');
    if (!demContainer) return;

    // Match the DEM canvas pixel resolution (exclude gridline/overlay canvases)
    const demCanvas = demContainer.querySelector(
        'canvas:not(.dem-gridlines-overlay):not(.city-dem-overlay):not(.water-dem-overlay):not(.sat-dem-overlay)'
    );
    if (!demCanvas) return;
    const W = demCanvas.width;
    const H = demCanvas.height;
    if (!W || !H) return;

    let overlay = demContainer.querySelector('.city-dem-overlay');
    if (!overlay) {
        overlay = document.createElement('canvas');
        overlay.className = 'city-dem-overlay';
        overlay.style.cssText = 'position:absolute;pointer-events:none;z-index:5;';
        demContainer.appendChild(overlay);
    }
    overlay.width  = W;
    overlay.height = H;

    // Position overlay to match the DEM canvas's actual CSS rect within #demImage
    // (#demImage uses flexbox centering, so the canvas may not start at top:0)
    const demCSSRect = demCanvas.getBoundingClientRect();
    const containerCSSRect = demContainer.getBoundingClientRect();
    overlay.style.left   = (demCSSRect.left - containerCSSRect.left) + 'px';
    overlay.style.top    = (demCSSRect.top  - containerCSSRect.top)  + 'px';
    overlay.style.width  = demCSSRect.width  + 'px';
    overlay.style.height = demCSSRect.height + 'px';

    const { north, south, east, west } = bbox;
    const lonRange = east - west;
    const latMid   = (north + south) / 2;
    const bboxLonM = lonRange * Math.cos(latMid * Math.PI / 180) * 111_000;
    const bboxKey  = `${north.toFixed(4)},${south.toFixed(4)},${east.toFixed(4)},${west.toFixed(4)}`;
    // PERF2: invZ (=1 for DEM view) excluded for consistency; cacheKey only needs data+layout
    const cacheKey = window._makeCacheKey(window._cityRenderState.cityDataVersion, W, H, bboxKey);

    const ctx = overlay.getContext('2d');

    // _offscreenOk already set by _doRenderCityOverlay; test here too in case
    // _doRenderCityOnDEM is called first (e.g., DEM view only, no stacked view).
    if (window._cityRenderState.offscreenOk === null) {
        try { new OffscreenCanvas(1, 1); window._cityRenderState.offscreenOk = true; }
        catch (_) { window._cityRenderState.offscreenOk = false; }
    }

    const geoToPx  = window._buildGeoToPx(north, south, east, west, 0, 0, W, H);
    const clipRect = { x0: 0, y0: 0, x1: W, y1: H };

    // PERF4: pre-bake pixel coords (DEM view has different geoToPx than stack view)
    const bakKey = `dem|${W}|${H}|${bboxKey}|${document.getElementById('paramProjection')?.value || 'none'}`;
    const rs = window._cityRenderState;
    for (const layer of rs.LAYER_NAMES) {
        if (osmCityData[layer]?.features) window._prebakeFeatures(osmCityData[layer].features, geoToPx, bakKey);
    }
    // Walls rendered inside buildings layer pass but stored separately
    if (osmCityData.walls?.features) window._prebakeFeatures(osmCityData.walls.features, geoToPx, bakKey);
    if (rs.offscreenOk) {
        // PERF6 Part A: per-layer offscreen cache for DEM view
        for (const layer of rs.LAYER_NAMES) {
            if (rs.demLayer[layer].key === cacheKey && rs.demLayer[layer].canvas) continue;
            const offscreen = new OffscreenCanvas(W, H);
            const octx = offscreen.getContext('2d');
            window._drawCityCanvas(octx, geoToPx, 1, osmCityData, W, W, bboxLonM, clipRect, layer);
            rs.demLayer[layer].canvas = offscreen;
            rs.demLayer[layer].key    = cacheKey;
        }
        ctx.clearRect(0, 0, W, H);
        for (const layer of rs.LAYER_NAMES) {
            if (!document.getElementById(rs.LAYER_TOGGLES[layer])?.checked) continue;
            ctx.drawImage(rs.demLayer[layer].canvas, 0, 0);
        }
    } else {
        // Fallback: draw all visible layers directly to visible canvas
        ctx.clearRect(0, 0, W, H);
        window._drawCityCanvas(ctx, geoToPx, 1, osmCityData, W, W, bboxLonM, clipRect, null);
    }
}

// ---------------------------------------------------------------------------
// City Heights raster layer (loadCityRaster, _setupCityRasterLayer)
// ---------------------------------------------------------------------------

/** Called by app.js clearLayerCache() when the region changes. */
window._clearCityRasterCache = function() { _lastCityRasterData = null; };

/**
 * Show or hide the "Load Cities" button based on region diagonal (max 10 km).
 * @param {Object} region - Region object with north/south/east/west
 */
window._updateCitiesLoadButton = function _updateCitiesLoadButton(region) {
    const loadBtn = document.getElementById('loadCityDataBtn');
    const infoRow = document.getElementById('cityInfoRow');
    if (!loadBtn || !region) return;
    const haversineDiagKm = window.appState?.haversineDiagKm;
    if (!haversineDiagKm) return;
    const diagKm   = haversineDiagKm(region.north, region.south, region.east, region.west);
    const available = diagKm <= 10;
    loadBtn.disabled     = !available;
    loadBtn.style.opacity = available ? '' : '0.4';
    loadBtn.style.cursor  = available ? '' : 'not-allowed';
    loadBtn.title = available
        ? `Fetch OSM data for this region (${diagKm.toFixed(1)} km)`
        : `Region too large (${diagKm.toFixed(1)} km — max 10 km)`;
    if (infoRow) {
        infoRow.textContent = available
            ? `Region diagonal: ${diagKm.toFixed(1)} km — OSM data available.`
            : `Region too large (${diagKm.toFixed(1)} km). Max 10 km for city data.`;
    }
};

/**
 * Fetch the City Heights raster from /api/cities/raster using the already-loaded
 * osmCityData GeoJSON. Renders the result into #layerCityRasterCanvas.
 */
window.loadCityRaster = async function loadCityRaster() {
    const cityData = window.appState?.osmCityData;
    const bbox     = window.appState?.currentDemBbox || window.appState?.selectedRegion;
    if (!cityData || !bbox) return;

    const dim           = parseInt(document.getElementById('paramDim')?.value) || 200;
    const buildingScale = parseFloat(document.getElementById('cityBuildingScale')?.value) || 1.0;
    const waterOffset   = parseFloat(document.getElementById('cityWaterOffset')?.value) ?? -2.0;

    setLayerStatus('cityRaster', 'loading');
    try {
        const { data, error: rasterErr } = await api.cities.raster({
            north: bbox.north, south: bbox.south,
            east:  bbox.east,  west:  bbox.west,
            dim,
            buildings:  cityData.buildings  || { type: 'FeatureCollection', features: [] },
            roads:      cityData.roads       || { type: 'FeatureCollection', features: [] },
            waterways:  cityData.waterways   || { type: 'FeatureCollection', features: [] },
            building_scale:     buildingScale,
            road_depression_m:  0,
            water_depression_m: waterOffset,
        });
        if (rasterErr) throw new Error(rasterErr);
        _lastCityRasterData = data;

        const colormap = document.getElementById('demColormap')?.value || 'terrain';
        // Render city raster to a standalone canvas WITHOUT overwriting DEM state.
        // Do NOT call renderDEMCanvas here — it clobbers lastDemData/curveData/workflow.
        const canvas = _renderRasterCanvas(
            data.values, data.width, data.height, colormap, data.vmin, data.vmax
        );
        if (canvas) {
            // Store raw (unprojected) canvas + bbox so we can re-project on demand
            if (window.appState) {
                window.appState._cityRasterRawCanvas = canvas;
                window.appState._cityRasterBbox = bbox;
            }
            // Apply projection to match the DEM canvas
            const projCanvas = window.applyProjection?.(canvas, bbox) || canvas;
            if (window.appState) window.appState.cityRasterSourceCanvas = projCanvas;
        }
        setLayerStatus('cityRaster', 'ready');
        window.events?.emit(window.EV?.STACKED_UPDATE);
    } catch (e) {
        setLayerStatus('cityRaster', 'error');
        window.showToast('City raster failed: ' + e.message, 'error');
    }
};

/**
 * Re-project the city raster canvas using the current projection setting.
 * Called when the projection dropdown changes.
 */
window._reprojectCityRaster = function _reprojectCityRaster() {
    const raw  = window.appState?._cityRasterRawCanvas;
    const bbox = window.appState?._cityRasterBbox;
    if (!raw || !bbox) return;
    const projCanvas = window.applyProjection?.(raw, bbox) || raw;
    if (window.appState) window.appState.cityRasterSourceCanvas = projCanvas;
};

/** Wire the City Heights visibility toggle and opacity slider. */
window._setupCityRasterLayer = function _setupCityRasterLayer() {
    const toggle  = document.getElementById('layerCityRasterVisible');
    const opacity = document.getElementById('layerCityRasterOpacity');
    const label   = document.getElementById('layerCityRasterOpacityLabel');
    const canvas  = document.getElementById('layerCityRasterCanvas');
    if (!toggle || !canvas) return;

    toggle.addEventListener('change', () => {
        if (toggle.checked) {
            canvas.style.display = '';
            if (!_lastCityRasterData && window.appState?.osmCityData) window.loadCityRaster();
        } else {
            canvas.style.display = 'none';
        }
        window.events?.emit(window.EV?.STACKED_UPDATE);
    });

    if (opacity && label) {
        opacity.addEventListener('input', () => {
            label.textContent  = opacity.value + '%';
            canvas.style.opacity = opacity.value / 100;
        });
    }

    if (window.appState?.on) {
        window.appState.on('osmCityData', (data) => {
            const badge = document.getElementById('citiesSettingsBadge');
            if (badge) {
                if (data) {
                    const nb = data.buildings?.features?.length || 0;
                    const nr = data.roads?.features?.length     || 0;
                    badge.textContent = `${nb} buildings · ${nr} roads`;
                    badge.style.color = '#4a9';
                } else {
                    badge.textContent = '';
                }
            }
            if (data) {
                const sec = document.getElementById('citiesSettingsSection');
                if (sec?.classList.contains('collapsed')) sec.classList.remove('collapsed');
            }
            _lastCityRasterData = null;
            if (document.getElementById('layerCityRasterVisible')?.checked) window.loadCityRaster();
        });
    }
};
