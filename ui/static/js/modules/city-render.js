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
 */

// ---------------------------------------------------------------------------
// Debounce tokens — only used by render functions defined in this file.
// ---------------------------------------------------------------------------
let _stackRafId = null;
let _demRafId   = null;

/** Cancel any in-flight RAF renders.  Called by city-overlay.js clearCityOverlay(). */
window._cancelCityRenders = function () {
    if (_stackRafId) { cancelAnimationFrame(_stackRafId); _stackRafId = null; }
    if (_demRafId)   { cancelAnimationFrame(_demRafId);   _demRafId   = null; }
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

    // Common setup used by both the per-layer and fallback paths
    const geoToPx  = window._buildGeoToPx(north, south, east, west, tX, tY, tW, tH);
    const clipRect = { x0: tX, y0: tY, x1: tX + tW, y1: tY + tH };
    const bakKey   = `stack|${W}|${H}|${bboxKey}|${document.getElementById('paramProjection')?.value || 'none'}`;

    // PERF4: pre-bake pixel coords (no-op for features already baked for this bakKey)
    if (osmCityData.buildings?.features) window._prebakeFeatures(osmCityData.buildings.features, geoToPx, bakKey);
    if (osmCityData.roads?.features)     window._prebakeFeatures(osmCityData.roads.features,     geoToPx, bakKey);
    if (osmCityData.waterways?.features) window._prebakeFeatures(osmCityData.waterways.features, geoToPx, bakKey);
    if (osmCityData.pois?.features)      window._prebakeFeatures(osmCityData.pois.features,      geoToPx, bakKey);

    const rs = window._cityRenderState;
    if (rs.offscreenOk) {
        // PERF6 Part A: render each stale layer to its own OffscreenCanvas.
        // Layers whose cacheKey already matches are skipped (no re-draw).
        // Compositing is always just 4 drawImage blits.
        for (const layer of rs.LAYER_NAMES) {
            if (rs.stackLayer[layer].key === cacheKey && rs.stackLayer[layer].canvas) continue;
            const offscreen = new OffscreenCanvas(W, H);
            const octx = offscreen.getContext('2d');
            octx.save();
            octx.beginPath(); octx.rect(tX, tY, tW, tH); octx.clip();
            window._drawCityCanvas(octx, geoToPx, invZ, osmCityData, W, tW, bboxLonM, clipRect, layer);
            octx.restore();
            rs.stackLayer[layer].canvas = offscreen;
            rs.stackLayer[layer].key    = cacheKey;
        }
        // Composite: blit only the visible layers (toggle state checked here, not at bake time)
        ctx.clearRect(0, 0, W, H);
        for (const layer of rs.LAYER_NAMES) {
            if (!document.getElementById(rs.LAYER_TOGGLES[layer])?.checked) continue;
            ctx.drawImage(rs.stackLayer[layer].canvas, 0, 0);
        }
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

    // Also update the DEM canvas overlay (Cities 8) — but only schedule it,
    // don't run it inline so this frame stays fast.
    window.renderCityOnDEM?.();
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
        'canvas:not(.dem-gridlines-overlay):not(.city-dem-overlay)'
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
    if (osmCityData.buildings?.features) window._prebakeFeatures(osmCityData.buildings.features, geoToPx, bakKey);
    if (osmCityData.roads?.features)     window._prebakeFeatures(osmCityData.roads.features,     geoToPx, bakKey);
    if (osmCityData.waterways?.features) window._prebakeFeatures(osmCityData.waterways.features, geoToPx, bakKey);
    if (osmCityData.pois?.features)      window._prebakeFeatures(osmCityData.pois.features,      geoToPx, bakKey);

    const rs = window._cityRenderState;
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
        const canvas = window.renderDEMCanvas?.(
            data.values, data.width, data.height, colormap, data.vmin, data.vmax
        );
        if (canvas && window.appState) {
            window.appState.cityRasterSourceCanvas = canvas;
        }
        setLayerStatus('cityRaster', 'ready');
        window.updateStackedLayers?.();
    } catch (e) {
        setLayerStatus('cityRaster', 'error');
        showToast('City raster failed: ' + e.message, 'error');
    }
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
        window.updateStackedLayers?.();
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
