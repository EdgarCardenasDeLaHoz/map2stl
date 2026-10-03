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
 * PERF6B — Web Worker offload (_renderViaWorker)
 *   Both views (stacked layers and DEM canvas) draw in city-worker.js when OffscreenCanvas
 *   is available.  The worker receives copies of the pre-baked Float32Array buffers
 *   (stack view: feat._px, DEM view: feat._pxDem) + DOM-extracted style/toggle values.
 *   Each view keeps its last ImageBitmap and repaints it while the pixels' key is unchanged;
 *   a render whose key is already being drawn waits for that reply; older replies are dropped.
 *   Falls back to the synchronous _drawCityCanvas path if workers are unavailable.
 */

// ---------------------------------------------------------------------------
// PERF6B — Web Worker singleton
// ---------------------------------------------------------------------------

let _cityWorker = null;
let _cityWorkerGen = 0;   // incremented per dispatch
const _workerHandlers = new Map();                // gen → reply handler
const _workerLatest = { stack: 0, dem: 0 };       // newest gen per view; older replies are dropped
const _workerInFlight = { stack: '', dem: '' };   // key of the picture being drawn per view
/** Last worker picture per view, painted again until something that changes its pixels changes. */
const _workerResult = { stack: { key: '', bitmap: null }, dem: { key: '', bitmap: null } };

/** Initialise (or reuse) the city rendering worker. Returns null if unsupported. */
function _getCityWorker() {
    if (_cityWorker) return _cityWorker;
    if (typeof Worker === 'undefined') return null;
    try {
        _cityWorker = new Worker('/static/js/workers/city-worker.js');
        _cityWorker.onmessage = (e) => {
            const handler = _workerHandlers.get(e.data.gen);
            _workerHandlers.delete(e.data.gen);
            if (handler) handler(e.data);
            else e.data.bitmap?.close();
        };
        _cityWorker.onerror = (e) => {
            console.warn('city-worker error — disabling worker path:', e.message);
            _cityWorker = null;
            _workerHandlers.clear();
            _workerInFlight.stack = _workerInFlight.dem = '';
        };
    } catch (_) {
        _cityWorker = null;
    }
    return _cityWorker;
}

/**
 * Serialise a GeoJSON FeatureCollection's pre-baked pixel data into a
 * plain-object array that can be structured-cloned to the worker.
 * The buffers are copied, not transferred: the baked coords stay valid for the
 * next render and for building picking. Unbaked features are skipped.
 *
 * @param {Object|null} geojson  - GeoJSON FeatureCollection with baked features
 * @param {string}      propKey  - property to copy ('height_m' or 'road_width_m')
 * @param {string}      [slot]   - baked-coords property (`_px` stack view, `_pxDem` DEM view)
 * @returns {{ features: BakedFeature[] } | null}
 */
function _serialiseLayer(geojson, propKey, slot = '_px') {
    if (!geojson?.features?.length) return null;
    const features = [];
    for (let i = 0; i < geojson.features.length; i++) {
        const feat = geojson.features[i];
        const px = feat[slot];
        if (!px?.buf) continue;
        features.push({
            buf: px.buf,
            counts: px.counts,
            x0: px.x0, y0: px.y0,
            x1: px.x1, y1: px.y1,
            type: feat.geometry?.type || '',
            srcIndex: feat._cityIndex ?? i,
            [propKey]: feat.properties?.[propKey] || 0,
        });
    }
    return features.length ? { features } : null;
}

/** Serialise every city layer for the worker from one baked-coords slot. */
function _serialiseLayers(osmCityData, slot) {
    return {
        buildings: _serialiseLayer(osmCityData.buildings, 'height_m', slot),
        roads: _serialiseLayer(osmCityData.roads, 'road_width_m', slot),
        waterways: _serialiseLayer(osmCityData.waterways, 'height_m', slot),
        walls: _serialiseLayer(osmCityData.walls, 'height_m', slot),
    };
}

/**
 * Draw one view's city layers in the worker and paint the picture.
 * `key` covers everything that changes the pixels (data, size, bbox, selection,
 * toggles, colours). The last picture with the same key is painted again without
 * drawing; a render whose key is already being drawn waits for that reply.
 * (Each redraw used to run again from scratch: 58 k Philadelphia buildings froze
 * the page for 100 s of a 2-minute Edit load.)
 *
 * @param {'stack'|'dem'} view
 * @param {string}   key
 * @param {Function} paint     - paint(bitmap) onto the view's overlay canvas
 * @param {Function} buildMsg  - () → render message fields (built only when drawing)
 * @param {Function} onError   - main-thread fallback draw
 * @returns {boolean} false when no worker is available
 */
function _renderViaWorker(view, key, paint, buildMsg, onError) {
    const worker = _getCityWorker();
    if (!worker) return false;
    const done = _workerResult[view];
    if (done.key === key && done.bitmap) { paint(done.bitmap); return true; }
    if (_workerInFlight[view] === key) return true;
    const gen = ++_cityWorkerGen;
    _workerLatest[view] = gen;
    _workerInFlight[view] = key;
    _workerHandlers.set(gen, (reply) => {
        if (_workerLatest[view] !== gen) { reply.bitmap?.close(); return; }
        _workerInFlight[view] = '';
        if (reply.type === 'bitmap') {
            done.bitmap?.close();
            done.key = key;
            done.bitmap = reply.bitmap;
            paint(reply.bitmap);
        } else {
            console.warn('city-worker render error:', reply.message);
            onError();
        }
    });
    try {
        worker.postMessage({ type: 'render', gen, ...buildMsg() });
    } catch (err) {
        _workerHandlers.delete(gen);
        _workerInFlight[view] = '';
        console.warn('city-worker postMessage failed, using sync fallback:', err?.message || err);
        onError();
    }
    return true;
}

/** Paint a worker picture onto the current overlay canvas (looked up again: it may have been removed). */
function _paintOnto(container, selector) {
    return (bitmap) => {
        const canvas = container.querySelector(selector);
        if (!canvas) return;
        const c = canvas.getContext('2d');
        c.clearRect(0, 0, canvas.width, canvas.height);
        c.drawImage(bitmap, 0, 0);
    };
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
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    const img = ctx.createImageData(width, height);
    const data = img.data;
    const range = (vmax - vmin) || 1;
    const invRange = 1 / range;

    // Build colour LUT
    const lut = window.buildColorLUT(colormap);

    for (let i = 0; i < values.length; i++) {
        const v = values[i];
        const idx = i * 4;
        if (!Number.isFinite(v) || v === 0) {
            // Transparent for zero/nodata (no building/road here)
            data[idx] = data[idx + 1] = data[idx + 2] = data[idx + 3] = 0;
        } else {
            const t = Math.max(0, Math.min(1023, ((v - vmin) * invRange * 1023) | 0));
            data[idx] = lut[t * 3];
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
let _demRafId = null;

if (window.appState?.on) {
    window.appState.on('selectedCityBuildingIndex', () => {
        window._invalidateCityCache?.();
        window.renderCityOverlay?.();
        window.renderCityOnDEM?.();
    });
}

/** Cancel any in-flight RAF renders.  Called by city-overlay.js clearCityOverlay(). */
window._cancelCityRenders = function () {
    if (_stackRafId) { cancelAnimationFrame(_stackRafId); _stackRafId = null; }
    if (_demRafId) { cancelAnimationFrame(_demRafId); _demRafId = null; }
    // Drop any in-flight worker reply and the kept pictures
    _workerLatest.stack = _workerLatest.dem = 0;
    _workerInFlight.stack = _workerInFlight.dem = '';
    for (const r of Object.values(_workerResult)) { r.bitmap?.close(); r.bitmap = null; r.key = ''; }
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
    const selectedBuildingIndex = window.appState?.selectedCityBuildingIndex ?? null;

    const bbox = window.appState?.currentDemBbox || window.appState?.selectedRegion;
    if (!bbox) return;

    const stack = document.getElementById('layersStack');
    // Hidden (Extrude, Explore): nothing to draw. Entering Extrude resized it and re-baked
    // 58 k Philadelphia buildings (0.84 s); updateStackedLayers renders it when shown.
    if (!stack?.offsetParent) return;

    const { north, south, east, west } = bbox;
    const latRange = north - south;

    // Get or create overlay canvas inside the stack
    let overlay = stack.querySelector('.osm-overlay');
    if (!overlay) {
        overlay = document.createElement('canvas');
        overlay.className = 'osm-overlay layer-canvas';
        overlay.classList.add('overlay-z10');
        stack.appendChild(overlay);
    }

    // Size the overlay to match the stack's full pixel dimensions
    const stackRect = stack.getBoundingClientRect();
    const W = Math.round(stackRect.width) || 600;
    const H = Math.round(stackRect.height) || 400;

    // Letterbox target rect matching updateStackedLayers
    const latMid = (north + south) / 2;
    const latCos = Math.cos(latMid * Math.PI / 180);
    const bboxAspect = ((east - west) * latCos) / latRange;
    const stackAspect = W / H;
    let tX = 0, tY = 0, tW = W, tH = H;
    if (bboxAspect > stackAspect) {
        tW = W; tH = W / bboxAspect; tY = (H - tH) / 2;
    } else {
        tH = H; tW = H * bboxAspect; tX = (W - tW) / 2;
    }

    const stackZoom = window.appState?.stackZoom || { scale: 1, offsetX: 0, offsetY: 0 };
    const invZ = 1 / (stackZoom.scale || 1);
    const bboxLonM = (east - west) * latCos * window.GEO_M_PER_DEG_LON;
    const bboxKey = `${north.toFixed(4)},${south.toFixed(4)},${east.toFixed(4)},${west.toFixed(4)}`;
    // PERF2: invZ excluded from cache key — CSS transform covers intermediate zoom frames
    const cacheKey = window._makeCacheKey(
        window._cityRenderState.cityDataVersion,
        W,
        H,
        `${bboxKey}|sel:${selectedBuildingIndex ?? -1}`
    );

    // Setting the size clears the canvas: only when it changed, so the last picture
    // stays up while the worker draws the next one.
    if (overlay.width !== W) overlay.width = W;
    if (overlay.height !== H) overlay.height = H;
    const ctx = overlay.getContext('2d');

    // Test OffscreenCanvas support once (same browser capability for the entire session)
    if (window._cityRenderState.offscreenOk === null) {
        try { new OffscreenCanvas(1, 1); window._cityRenderState.offscreenOk = true; }
        catch (_) { window._cityRenderState.offscreenOk = false; }
    }

    // Common setup used by all render paths
    const geoToPx = window._buildGeoToPx(north, south, east, west, tX, tY, tW, tH);
    const clipRect = { x0: tX, y0: tY, x1: tX + tW, y1: tY + tH };
    const bakKey = `stack|${W}|${H}|${bboxKey}|${document.getElementById('paramProjection')?.value || 'none'}`;

    const rs = window._cityRenderState;

    // PERF4: pre-bake pixel coords for all layers (no-op for features already baked for this bakKey)
    for (const layer of rs.LAYER_NAMES) {
        if (osmCityData[layer]?.features) window._prebakeFeatures(osmCityData[layer].features, geoToPx, bakKey);
    }
    if (osmCityData.walls?.features) window._prebakeFeatures(osmCityData.walls.features, geoToPx, bakKey);

    // Collect DOM style values now — worker cannot access the DOM
    const styles = {
        buildingsColor: document.getElementById('layerBuildingsColor')?.value || '#c8b89a',
        roadsColor: document.getElementById('layerRoadsColor')?.value || '#cc8844',
        waterwaysColor: document.getElementById('layerWaterwaysColor')?.value || '#4488cc',
        roadBaseWidth: 1.5, // canvas road display width (fixed default; road_depression_m controls 3D export)
        bboxLonM,
    };
    const toggles = {
        buildings: !!document.getElementById(rs.LAYER_TOGGLES.buildings)?.checked,
        roads: !!document.getElementById(rs.LAYER_TOGGLES.roads)?.checked,
        waterways: !!document.getElementById(rs.LAYER_TOGGLES.waterways)?.checked,
    };

    // ── PERF6B: worker path ────────────────────────────────────────────────────
    const syncDraw = () => _syncRenderCityOverlay(ctx, geoToPx, invZ, osmCityData, W, tW, bboxLonM, clipRect, cacheKey, rs);
    const workerKey = `${cacheKey}|${JSON.stringify(toggles)}|${styles.buildingsColor}${styles.roadsColor}${styles.waterwaysColor}`;
    const viaWorker = rs.offscreenOk && _renderViaWorker('stack', workerKey, _paintOnto(stack, '.osm-overlay'),
        () => ({ W, H, tX, tY, tW, tH, invZ, layers: _serialiseLayers(osmCityData, '_px'), styles, toggles, selectedBuildingIndex }),
        syncDraw);

    if (viaWorker) {
        // drawn (or repainted from the last picture) by _renderViaWorker
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
        rs.stackLayer[layer].key = cacheKey;
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
    const selectedBuildingIndex = window.appState?.selectedCityBuildingIndex ?? null;

    const bbox = window.appState?.currentDemBbox || window.appState?.selectedRegion;
    if (!bbox) return;

    const demContainer = document.getElementById('demImage');
    if (!demContainer?.offsetParent) return;      // hidden: drawn by renderDEMCanvas when shown

    // Match the DEM canvas pixel resolution (exclude gridline/overlay canvases)
    const demCanvas = demContainer.querySelector(window.DEM_CANVAS_SELECTOR);
    if (!demCanvas) return;
    const W = demCanvas.width;
    const H = demCanvas.height;
    if (!W || !H) return;

    let overlay = demContainer.querySelector('.city-dem-overlay');
    if (!overlay) {
        overlay = document.createElement('canvas');
        overlay.className = 'city-dem-overlay';
        demContainer.appendChild(overlay);
    }
    if (overlay.width !== W) overlay.width = W;
    if (overlay.height !== H) overlay.height = H;

    // Position overlay to match the DEM canvas's actual CSS rect within #demImage
    // (#demImage uses flexbox centering, so the canvas may not start at top:0)
    const demCSSRect = demCanvas.getBoundingClientRect();
    const containerCSSRect = demContainer.getBoundingClientRect();
    overlay.style.left = (demCSSRect.left - containerCSSRect.left) + 'px';
    overlay.style.top = (demCSSRect.top - containerCSSRect.top) + 'px';
    overlay.style.width = demCSSRect.width + 'px';
    overlay.style.height = demCSSRect.height + 'px';

    const { north, south, east, west } = bbox;
    const lonRange = east - west;
    const latMid = (north + south) / 2;
    const bboxLonM = lonRange * Math.cos(latMid * Math.PI / 180) * window.GEO_M_PER_DEG_LON;
    const bboxKey = `${north.toFixed(4)},${south.toFixed(4)},${east.toFixed(4)},${west.toFixed(4)}`;
    // PERF2: invZ (=1 for DEM view) excluded for consistency; cacheKey only needs data+layout
    const cacheKey = window._makeCacheKey(
        window._cityRenderState.cityDataVersion,
        W,
        H,
        `${bboxKey}|sel:${selectedBuildingIndex ?? -1}`
    );

    const ctx = overlay.getContext('2d');

    // _offscreenOk already set by _doRenderCityOverlay; test here too in case
    // _doRenderCityOnDEM is called first (e.g., DEM view only, no stacked view).
    if (window._cityRenderState.offscreenOk === null) {
        try { new OffscreenCanvas(1, 1); window._cityRenderState.offscreenOk = true; }
        catch (_) { window._cityRenderState.offscreenOk = false; }
    }

    const geoToPx = window._buildGeoToPx(north, south, east, west, 0, 0, W, H);
    const clipRect = { x0: 0, y0: 0, x1: W, y1: H };

    // PERF4: pre-bake pixel coords. The DEM view has its own geoToPx, so with the worker
    // it bakes into its own slot (_pxDem) and leaves the stack view's _px alone.
    const bakKey = `dem|${W}|${H}|${bboxKey}|${document.getElementById('paramProjection')?.value || 'none'}`;
    const rs = window._cityRenderState;
    const bake = (slot) => {
        for (const layer of rs.LAYER_NAMES) {
            if (osmCityData[layer]?.features) window._prebakeFeatures(osmCityData[layer].features, geoToPx, bakKey, slot);
        }
        // Walls rendered inside buildings layer pass but stored separately
        if (osmCityData.walls?.features) window._prebakeFeatures(osmCityData.walls.features, geoToPx, bakKey, slot);
    };
    const syncDraw = () => {
        // _drawCityCanvas reads _px: bake the DEM layout there for this draw
        bake('_px');
        for (const layer of rs.LAYER_NAMES) {
            if (rs.demLayer[layer].key === cacheKey && rs.demLayer[layer].canvas) continue;
            const offscreen = new OffscreenCanvas(W, H);
            const octx = offscreen.getContext('2d');
            window._drawCityCanvas(octx, geoToPx, 1, osmCityData, W, W, bboxLonM, clipRect, layer);
            rs.demLayer[layer].canvas = offscreen;
            rs.demLayer[layer].key = cacheKey;
        }
        ctx.clearRect(0, 0, W, H);
        for (const layer of rs.LAYER_NAMES) {
            if (!document.getElementById(rs.LAYER_TOGGLES[layer])?.checked) continue;
            ctx.drawImage(rs.demLayer[layer].canvas, 0, 0);
        }
    };
    const toggles = Object.fromEntries(rs.LAYER_NAMES.map(l => [l, !!document.getElementById(rs.LAYER_TOGGLES[l])?.checked]));
    const styles = {
        buildingsColor: document.getElementById('layerBuildingsColor')?.value || '#c8b89a',
        roadsColor: document.getElementById('layerRoadsColor')?.value || '#cc8844',
        waterwaysColor: document.getElementById('layerWaterwaysColor')?.value || '#4488cc',
        roadBaseWidth: 1.5,
        bboxLonM,
    };
    const workerKey = `${cacheKey}|${JSON.stringify(toggles)}|${styles.buildingsColor}${styles.roadsColor}${styles.waterwaysColor}`;
    if (rs.offscreenOk && _getCityWorker()) {
        _renderViaWorker('dem', workerKey, _paintOnto(demContainer, '.city-dem-overlay'), () => {
            bake('_pxDem');
            return { W, H, tX: 0, tY: 0, tW: W, tH: H, invZ: 1, layers: _serialiseLayers(osmCityData, '_pxDem'), styles, toggles, selectedBuildingIndex };
        }, syncDraw);
    } else if (rs.offscreenOk) {
        syncDraw();
    } else {
        // Fallback: draw all visible layers directly to visible canvas
        bake('_px');
        ctx.clearRect(0, 0, W, H);
        window._drawCityCanvas(ctx, geoToPx, 1, osmCityData, W, W, bboxLonM, clipRect, null);
    }
}

// ---------------------------------------------------------------------------
// City Heights raster layer (loadCityRaster, _setupCityRasterLayer)
// ---------------------------------------------------------------------------

/** Called by app.js clearLayerCache() when the region changes. */
window._clearCityRasterCache = function () { _lastCityRasterData = null; };

/**
 * Show or hide the "Load Cities" button based on region diagonal (max 10 km).
 * @param {Object} region - Region object with north/south/east/west
 */
window._updateCitiesLoadButton = function _updateCitiesLoadButton(region) {
    const loadBtn = document.getElementById('loadCityDataBtn');
    const infoRow = document.getElementById('cityInfoRow');
    if (!loadBtn || !region) return;
    // haversineDiagKm lives on window (model-viewer.js), not appState.
    const haversineDiagKm = window.haversineDiagKm;
    if (typeof haversineDiagKm !== 'function') return;
    const diagKm = haversineDiagKm(region.north, region.south, region.east, region.west);
    const maxDiag = window.CITY_MAX_DIAG_KM ?? 10;
    const maxDiagCoarse = window.CITY_COARSE_MAX_DIAG_KM ?? 25;
    const available = diagKm <= maxDiagCoarse;
    const isCoarse = diagKm > maxDiag && available;
    loadBtn.disabled = !available;
    loadBtn.style.opacity = available ? '' : '0.4';
    loadBtn.style.cursor = available ? '' : 'not-allowed';
    loadBtn.title = !available
        ? `Region too large (${diagKm.toFixed(1)} km — max ${maxDiagCoarse} km)`
        : isCoarse
        ? `Fetch coarse OSM data — roads, water, large buildings only (${diagKm.toFixed(1)} km, no wall/small-building detail above ${maxDiag} km)`
        : `Fetch OSM data for this region (${diagKm.toFixed(1)} km)`;
    if (infoRow) {
        infoRow.textContent = !available
            ? `Region too large (${diagKm.toFixed(1)} km). Max ${maxDiagCoarse} km for city data.`
            : isCoarse
            ? `Region diagonal: ${diagKm.toFixed(1)} km — coarse city data only (roads, water, large buildings; no walls/small-building detail above ${maxDiag} km).`
            : `Region diagonal: ${diagKm.toFixed(1)} km — OSM data available.`;
    }

    // Keep the buildings table and selection feature disabled when city data is unavailable.
    window.setCityBuildingsTableAvailable?.(available);
    if (!available) {
        window.hideCityBuildingsPanel?.();
        window.appState.selectedCityBuildingIndex = null;
        window.syncSelectedCityBuilding?.(null);
        if (window.appState?.osmCityData) {
            window.clearCityOverlay?.();
        }
    }
};

/**
 * Fetch the City Heights raster from /api/cities/raster using the already-loaded
 * osmCityData GeoJSON. Renders the result into #layerCityRasterCanvas.
 */
window.loadCityRaster = async function loadCityRaster() {
    const cityData = window.appState?.osmCityData;
    const bbox = window.appState?.currentDemBbox || window.appState?.selectedRegion;
    if (!cityData || !bbox) return;

    const dim = parseInt(document.getElementById('paramDim')?.value) || 200;
    const buildingScale = parseFloat(document.getElementById('cityBuildingScale')?.value) || 1.0;
    const waterOffset = parseFloat(document.getElementById('cityWaterOffset')?.value) ?? -2.0;

    window.setLayerStatus('cityRaster', 'loading');
    try {
        const cityProj = window.getProjectionParams();
        const { data, error: rasterErr } = await window.api.cities.raster({
            north: bbox.north, south: bbox.south,
            east: bbox.east, west: bbox.west,
            dim,
            // Always pass current fetched city features so backend rasterization uses
            // the latest geometry flow (including MultiPolygon footprints).
            buildings: cityData.buildings || { type: 'FeatureCollection', features: [] },
            roads: cityData.roads || { type: 'FeatureCollection', features: [] },
            waterways: cityData.waterways || { type: 'FeatureCollection', features: [] },
            building_scale: buildingScale,
            road_depression_m: 0,
            water_depression_m: waterOffset,
            projection: cityProj.projection,
            maintain_dimensions: cityProj.maintainDimensions,
            clip_valid_region: cityProj.clipValidRegion,
        });
        if (rasterErr) throw new Error(rasterErr);
        _lastCityRasterData = data;

        const colormap = window.getLayerColormap?.('city') || document.getElementById('demColormap')?.value || 'terrain';
        // Render city raster to a standalone canvas WITHOUT overwriting DEM state.
        // Do NOT call renderDEMCanvas here — it clobbers lastDemData/curveData/workflow.
        const canvas = _renderRasterCanvas(
            data.values, data.width, data.height, colormap, data.vmin, data.vmax
        );
        if (canvas) {
            if (window.appState) {
                window.appState._cityRasterRawCanvas = canvas;
                window.appState._cityRasterBbox = bbox;
                window.appState.cityRasterSourceCanvas = canvas;
            }
        }
        window.setLayerStatus('cityRaster', 'loaded');
        window.events?.emit(window.EV?.STACKED_UPDATE);
    } catch (e) {
        window.setLayerStatus('cityRaster', 'error');
        window.showToast('City raster failed: ' + e.message, 'error');
    }
};

/** Wire the City Heights visibility toggle and opacity slider. */
window._setupCityRasterLayer = function _setupCityRasterLayer() {
    const toggle = document.getElementById('layerCityRasterVisible');
    const opacity = document.getElementById('layerCityRasterOpacity');
    const label = document.getElementById('layerCityRasterOpacityLabel');
    const canvas = document.getElementById('layerCityRasterCanvas');
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
            label.textContent = opacity.value + '%';
            canvas.style.opacity = opacity.value / 100;
        });
    }

    // Per-layer city colormap — re-colour the cached raster without re-fetching.
    document.getElementById('cityColormap')?.addEventListener('change', () => {
        if (_lastCityRasterData) {
            const d = _lastCityRasterData;
            const cm = window.getLayerColormap?.('city') || 'terrain';
            const c = _renderRasterCanvas(d.values, d.width, d.height, cm, d.vmin, d.vmax);
            if (c && window.appState) {
                window.appState._cityRasterRawCanvas = c;
                window.appState.cityRasterSourceCanvas = c;
            }
            window.events?.emit(window.EV?.STACKED_UPDATE);
        } else if (document.getElementById('layerCityRasterVisible')?.checked && window.appState?.osmCityData) {
            window.loadCityRaster?.();
        }
    });

    if (window.appState?.on) {
        window.appState.on('osmCityData', (data) => {
            const badge = document.getElementById('citiesSettingsBadge');
            if (badge) {
                if (data) {
                    const nb = data.buildings?.features?.length || 0;
                    const nr = data.roads?.features?.length || 0;
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
