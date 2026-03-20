/**
 * city-overlay.js — OSM/city data overlay for the stacked layers view.
 *
 * Extracted from app.js (TODO item 15).  Loaded as a plain <script> before app.js
 * so the functions are available in global scope when app.js runs its DOMContentLoaded.
 *
 * Shared state is read from / written to window.appState (set up by app.js):
 *   window.appState.selectedRegion   — currently selected region object
 *   window.appState.currentDemBbox   — bounding box of the currently rendered DEM
 *   window.appState.osmCityData      — last fetched OSM feature collections
 *   window.appState.showToast        — toast notification function
 *   window.appState.haversineDiagKm  — bbox diagonal distance helper
 *
 * Performance notes:
 *   Buildings are batched into ALPHA_BUCKETS opacity groups so ctx.globalAlpha is
 *   set once per bucket rather than once per feature (~8 vs ~3000 state changes).
 *   Roads are batched by rounded lineWidth for the same reason.
 *   Both render functions are debounced through requestAnimationFrame.
 */

const ALPHA_BUCKETS = 8;   // number of opacity bands for building height shading

// ---------------------------------------------------------------------------
// Debounce tokens — prevents duplicate renders when called multiple times
// within the same animation frame.
// ---------------------------------------------------------------------------
let _stackRafId  = null;
let _demRafId    = null;

// ---------------------------------------------------------------------------
// Offscreen canvas cache — keyed by (dataVersion, W, H, zoom.scale).
// When the key matches, blit from cache instead of re-drawing all features.
// Invalidated on data load (osmCityData reassignment increments _cityDataVersion).
// ---------------------------------------------------------------------------
let _cityDataVersion  = 0;   // incremented every time osmCityData changes
let _stackCacheKey    = '';
let _stackOffscreen   = null; // OffscreenCanvas or null
let _demCacheKey      = '';
let _demOffscreen     = null;

/** Bump version so next render bypasses the cache. Call after loading new data. */
window._invalidateCityCache = function () { _cityDataVersion++; };

/** Serialise a cache key from the parameters that affect pixel output. */
function _makeCacheKey(version, W, H, invZ, bboxKey) {
    return `${version}|${W}|${H}|${invZ.toFixed(3)}|${bboxKey}`;
}

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------

/**
 * Fetch city/OSM data from `/api/cities` for the selected region.
 * Checks the server cache first. Stores result in window.appState.osmCityData
 * and renders the overlay.
 * @returns {Promise<void>}
 */
window.loadCityData = async function loadCityData() {
    const { selectedRegion, showToast, haversineDiagKm } = window.appState || {};
    if (!selectedRegion) { if (showToast) showToast('Select a region first', 'warning'); return; }
    const diagKm = haversineDiagKm
        ? haversineDiagKm(selectedRegion.north, selectedRegion.south, selectedRegion.east, selectedRegion.west)
        : 0;
    if (diagKm > 10) {
        if (showToast) showToast(`Region too large (${diagKm.toFixed(1)} km). Max 10 km.`, 'error');
        return;
    }

    const statusEl = document.getElementById('cityDataStatus');
    const loadBtn  = document.getElementById('loadCityDataBtn');
    if (statusEl) statusEl.textContent = 'Checking cache…';
    if (loadBtn)  loadBtn.disabled = true;

    try {
        // Check server cache first
        const cacheResp = await fetch(
            `/api/cities/cached?north=${selectedRegion.north}&south=${selectedRegion.south}` +
            `&east=${selectedRegion.east}&west=${selectedRegion.west}`
        );
        const cacheInfo = await cacheResp.json();
        if (statusEl) statusEl.textContent = cacheInfo.cached ? 'Loading from cache…' : 'Fetching from OpenStreetMap…';

        const layers = [];
        if (document.getElementById('layerBuildingsToggle')?.checked)  layers.push('buildings');
        if (document.getElementById('layerRoadsToggle')?.checked)      layers.push('roads');
        if (document.getElementById('layerWaterwaysToggle')?.checked)  layers.push('waterways');
        if (document.getElementById('layerPoisToggle')?.checked)       layers.push('pois');

        const simplifyTol = parseFloat(document.getElementById('citySimplifyTolerance')?.value) || 2.0;
        const minArea     = parseFloat(document.getElementById('cityMinArea')?.value) || 20.0;

        const resp = await fetch('/api/cities', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                north: selectedRegion.north, south: selectedRegion.south,
                east:  selectedRegion.east,  west:  selectedRegion.west,
                layers,
                simplify_tolerance: simplifyTol,
                min_area: minArea
            })
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            if (showToast) showToast('OSM error: ' + (err.error || resp.status), 'error');
            if (statusEl) statusEl.textContent = 'Failed.';
            return;
        }

        const data = await resp.json();
        // Cities 7: annotate features with terrain_z from the current DEM
        const demData = window.appState?.lastDemData;
        _computeTerrainZ(data.buildings, demData);
        _computeTerrainZ(data.roads,     demData);
        window.appState.osmCityData = data;
        window._invalidateCityCache();   // new data → force full re-render

        _updateCityLayerCount('buildings', data.buildings?.features?.length);
        _updateCityLayerCount('roads',     data.roads?.features?.length);
        _updateCityLayerCount('waterways', data.waterways?.features?.length);
        _updateCityLayerCount('pois',      data.pois?.features?.length);

        renderCityOverlay();
        if (statusEl) statusEl.textContent = `Loaded (${data.diagonal_km?.toFixed(1) ?? '?'} km)`;
        if (showToast) showToast('City data loaded', 'success');
    } catch (e) {
        const showToastFn = window.appState?.showToast;
        if (showToastFn) showToastFn('City data error: ' + e.message, 'error');
        if (statusEl) statusEl.textContent = 'Error.';
    } finally {
        if (loadBtn) loadBtn.disabled = false;
    }
};

// ---------------------------------------------------------------------------
// Terrain Z annotation
// ---------------------------------------------------------------------------

/**
 * Return the [lon, lat] centroid of any GeoJSON geometry by averaging all coordinates.
 * @param {Object} geom - GeoJSON geometry object
 * @returns {[number, number]|null} [lon, lat] or null if no coordinates found
 */
function _geomCentroid(geom) {
    if (!geom?.coordinates) return null;
    const coords = [];
    function collect(c) {
        if (!Array.isArray(c)) return;
        if (typeof c[0] === 'number') { coords.push(c); return; }
        c.forEach(collect);
    }
    collect(geom.coordinates);
    if (!coords.length) return null;
    const lon = coords.reduce((s, c) => s + c[0], 0) / coords.length;
    const lat = coords.reduce((s, c) => s + c[1], 0) / coords.length;
    return [lon, lat];
}

/**
 * Compute the geo bounding box of a GeoJSON geometry by scanning all coordinates.
 * Result is stored as feat._bbox = {minLon, maxLon, minLat, maxLat}.
 * @param {Object} geom - GeoJSON geometry
 * @returns {{minLon:number, maxLon:number, minLat:number, maxLat:number}|null}
 */
function _computeGeomBbox(geom) {
    if (!geom?.coordinates) return null;
    let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
    function scan(c) {
        if (!Array.isArray(c)) return;
        if (typeof c[0] === 'number') {
            if (c[0] < minLon) minLon = c[0]; if (c[0] > maxLon) maxLon = c[0];
            if (c[1] < minLat) minLat = c[1]; if (c[1] > maxLat) maxLat = c[1];
            return;
        }
        c.forEach(scan);
    }
    scan(geom.coordinates);
    return isFinite(minLon) ? { minLon, maxLon, minLat, maxLat } : null;
}

/**
 * Annotate each feature in a FeatureCollection with:
 *  - `terrain_z` property: DEM elevation sampled at the feature centroid (Cities 7)
 *  - `_bbox`: geo bounding box {minLon, maxLon, minLat, maxLat} for sub-pixel culling
 *
 * @param {Object|null} geojson - GeoJSON FeatureCollection (mutated in-place)
 * @param {Object|null} demData - lastDemData: {dem_values, dimensions, bbox}
 */
function _computeTerrainZ(geojson, demData) {
    if (!geojson?.features) return;

    // Pre-compute geo bbox for every feature (used for sub-pixel culling in draw)
    for (const feat of geojson.features) {
        feat._bbox = _computeGeomBbox(feat.geometry);
    }

    if (!demData?.dem_values) return;
    const vals = demData.dem_values;
    const [demH, demW] = demData.dimensions || [0, 0];
    if (!demH || !demW) return;
    // bbox may be [west, south, east, north] array or {north,south,east,west} object
    let bWest, bSouth, bEast, bNorth;
    const b = demData.bbox;
    if (Array.isArray(b)) {
        [bWest, bSouth, bEast, bNorth] = b;
    } else if (b && typeof b === 'object') {
        ({ west: bWest, south: bSouth, east: bEast, north: bNorth } = b);
    } else {
        return;
    }
    if (bWest == null) return;
    const latRange = bNorth - bSouth;
    const lonRange = bEast - bWest;
    geojson.features.forEach(feat => {
        const ctr = _geomCentroid(feat.geometry);
        if (!ctr) return;
        const [lon, lat] = ctr;
        const col = Math.round(((lon - bWest)  / lonRange) * (demW - 1));
        const row = Math.round(((bNorth - lat)  / latRange) * (demH - 1));
        const ci  = Math.max(0, Math.min(demH - 1, row)) * demW
                  + Math.max(0, Math.min(demW - 1, col));
        feat.properties = feat.properties || {};
        feat.properties.terrain_z = vals[ci] ?? 0;
    });
}

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

/**
 * Update the feature-count badge next to a city layer checkbox.
 * @param {'buildings'|'roads'|'waterways'|'pois'} layer
 * @param {number|null|undefined} count - Feature count, or falsy to clear
 */
window._updateCityLayerCount = function _updateCityLayerCount(layer, count) {
    const ids = {
        buildings: 'cityBuildingCount',
        roads:     'cityRoadCount',
        waterways: 'cityWaterwayCount',
        pois:      'cityPoiCount'
    };
    const el = document.getElementById(ids[layer]);
    if (el) el.textContent = count != null ? `(${count})` : '';
};

// Convenience alias used internally
const _updateCityLayerCount = window._updateCityLayerCount;

/**
 * Clear osmCityData and remove the OSM overlay canvas from the stacked-layers view.
 */
window.clearCityOverlay = function clearCityOverlay() {
    if (window.appState) window.appState.osmCityData = null;
    // Cancel pending renders
    if (_stackRafId) { cancelAnimationFrame(_stackRafId); _stackRafId = null; }
    if (_demRafId)   { cancelAnimationFrame(_demRafId);   _demRafId   = null; }
    // Remove stacked-layers OSM overlay
    document.querySelector('#layersStack .osm-overlay')?.remove();
    // Remove DEM canvas OSM overlay (Cities 8)
    document.querySelector('#demImage .city-dem-overlay')?.remove();
    const statusEl = document.getElementById('cityDataStatus');
    if (statusEl) statusEl.textContent = '';
    ['cityBuildingCount', 'cityRoadCount', 'cityWaterwayCount', 'cityPoiCount'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = '';
    });
};

// ---------------------------------------------------------------------------
// Core draw routine (shared between stacked-layers and DEM canvas views)
// ---------------------------------------------------------------------------

/**
 * Draw OSM city features onto a canvas context.
 *
 * Buildings are batched into ALPHA_BUCKETS opacity groups to minimise
 * ctx.globalAlpha state changes (the main performance bottleneck).
 * Roads are batched by rounded lineWidth for the same reason.
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {function(number, number): [number, number]} geoToPx  (lat, lon) → [x, y]
 * @param {number} invZ   CSS zoom compensation factor (1 / stackZoom.scale)
 * @param {Object} osmCityData
 * @param {number} W      canvas pixel width  (for road metre-to-pixel scaling)
 * @param {number} [tW]   letterboxed draw width (for stacked view); defaults to W
 * @param {number} bboxLonM  longitudinal span of the bbox in metres
 */
function _drawCityCanvas(ctx, geoToPx, invZ, osmCityData, W, tW, bboxLonM) {
    const drawW = tW != null ? tW : W;
    const metrePerPx = bboxLonM / drawW;

    // ── Waterways ──────────────────────────────────────────────────────────
    if (document.getElementById('layerWaterwaysToggle')?.checked && osmCityData.waterways?.features) {
        const c = document.getElementById('layerWaterwaysColor')?.value || '#4488cc';
        ctx.fillStyle   = c + '88';
        ctx.strokeStyle = c;
        ctx.lineWidth   = 1.5 * invZ;
        ctx.globalAlpha = 0.65;
        ctx.beginPath();
        osmCityData.waterways.features.forEach(feat => {
            const geom = feat.geometry;
            if (!geom) return;
            const rings =
                geom.type === 'Polygon'         ? geom.coordinates :
                geom.type === 'MultiPolygon'    ? geom.coordinates.flat(1) :
                geom.type === 'LineString'      ? [geom.coordinates] :
                geom.type === 'MultiLineString' ? geom.coordinates : null;
            if (!rings) return;
            rings.forEach(ring => {
                let first = true;
                for (const [lo, la] of ring) {
                    const [px, py] = geoToPx(la, lo);
                    if (first) { ctx.moveTo(px, py); first = false; } else ctx.lineTo(px, py);
                }
                ctx.closePath();
            });
        });
        ctx.fill();
        ctx.stroke();
        ctx.globalAlpha = 1;
    }

    // ── Buildings — batched by opacity bucket ──────────────────────────────
    // Batching reduces globalAlpha changes from ~N_buildings to ALPHA_BUCKETS.
    if (document.getElementById('layerBuildingsToggle')?.checked && osmCityData.buildings?.features?.length) {
        const baseC = document.getElementById('layerBuildingsColor')?.value || '#c8b89a';
        ctx.strokeStyle = baseC;
        ctx.lineWidth   = 0.5 * invZ;
        ctx.fillStyle   = baseC;

        // Assign each feature to a bucket index based on height_m.
        // Skip features whose pre-computed geo bbox would render < 1.5 px in each
        // dimension — they are invisible at the current zoom level.
        const buckets = Array.from({ length: ALPHA_BUCKETS }, () => []);
        for (const feat of osmCityData.buildings.features) {
            // Sub-pixel culling using pre-computed geo bbox (_bbox set at load time)
            if (feat._bbox) {
                const [x0] = geoToPx(feat._bbox.minLat, feat._bbox.minLon);
                const [x1] = geoToPx(feat._bbox.maxLat, feat._bbox.maxLon);
                if (Math.abs(x1 - x0) < 1.5) continue;   // too small to see
            }
            const h = feat.properties?.height_m || 10;
            const t = Math.min(1, Math.max(0, (h - 3) / 77));
            const bi = Math.min(ALPHA_BUCKETS - 1, Math.floor(t * ALPHA_BUCKETS));
            buckets[bi].push(feat);
        }

        for (let bi = 0; bi < ALPHA_BUCKETS; bi++) {
            if (!buckets[bi].length) continue;
            // Set alpha once for the whole bucket
            ctx.globalAlpha = 0.40 + (bi / (ALPHA_BUCKETS - 1)) * 0.45;
            ctx.beginPath();
            for (const feat of buckets[bi]) {
                const geom = feat.geometry;
                if (!geom) continue;
                const rings =
                    geom.type === 'Polygon'      ? geom.coordinates :
                    geom.type === 'MultiPolygon' ? geom.coordinates.flat(1) : null;
                if (!rings) continue;
                for (const ring of rings) {
                    let first = true;
                    for (const [lo, la] of ring) {
                        const [px, py] = geoToPx(la, lo);
                        if (first) { ctx.moveTo(px, py); first = false; } else ctx.lineTo(px, py);
                    }
                    ctx.closePath();
                }
            }
            ctx.fill();
            ctx.stroke();
        }
        ctx.globalAlpha = 1;
    }

    // ── Roads — batched by lineWidth ───────────────────────────────────────
    // Group features sharing the same rounded pixel width into a single path.
    if (document.getElementById('layerRoadsToggle')?.checked && osmCityData.roads?.features?.length) {
        const c         = document.getElementById('layerRoadsColor')?.value || '#cc8844';
        const baseWidth = parseFloat(document.getElementById('cityRoadWidth')?.value) || 1.5;
        ctx.strokeStyle = c;
        ctx.globalAlpha = 0.75;

        // Group by rounded lineWidth (0.5-px resolution avoids too many groups)
        const groups = new Map();
        for (const feat of osmCityData.roads.features) {
            const widthM  = feat.properties?.road_width_m || baseWidth;
            const widthPx = Math.max(0.5, (widthM / metrePerPx) * invZ);
            const key     = Math.round(widthPx * 2) / 2;   // round to nearest 0.5
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push(feat);
        }

        for (const [lineWidth, feats] of groups) {
            ctx.lineWidth = lineWidth;
            ctx.beginPath();
            for (const feat of feats) {
                const geom = feat.geometry;
                if (!geom) continue;
                const rings =
                    geom.type === 'LineString'      ? [geom.coordinates] :
                    geom.type === 'MultiLineString' ? geom.coordinates : null;
                if (!rings) continue;
                for (const ring of rings) {
                    let first = true;
                    for (const [lo, la] of ring) {
                        const [px, py] = geoToPx(la, lo);
                        if (first) { ctx.moveTo(px, py); first = false; } else ctx.lineTo(px, py);
                    }
                }
            }
            ctx.stroke();
        }
        ctx.globalAlpha = 1;
    }

    // ── POIs — only above zoom threshold ──────────────────────────────────
    if (document.getElementById('layerPoisToggle')?.checked && osmCityData.pois?.features) {
        const zoom = window.appState?.stackZoom?.scale || 1;
        if (zoom >= 1.5) {
            const c = document.getElementById('layerPoisColor')?.value || '#ff6644';
            ctx.fillStyle   = c;
            ctx.strokeStyle = c;
            ctx.lineWidth   = 1 * invZ;
            ctx.globalAlpha = 0.65;
            for (const feat of osmCityData.pois.features) {
                const geom = feat.geometry;
                if (geom?.type !== 'Point') continue;
                const [px, py] = geoToPx(geom.coordinates[1], geom.coordinates[0]);
                ctx.beginPath();
                ctx.arc(px, py, 3, 0, 2 * Math.PI);
                ctx.fill();
            }
            ctx.globalAlpha = 1;
        }
    }
}

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
    const lonRange = east - west;

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
    const cacheKey  = _makeCacheKey(_cityDataVersion, W, H, invZ, bboxKey);

    overlay.width  = W;
    overlay.height = H;
    const ctx = overlay.getContext('2d');

    if (cacheKey === _stackCacheKey && _stackOffscreen) {
        // Cache hit — blit stored pixels; skip re-drawing thousands of features
        ctx.drawImage(_stackOffscreen, 0, 0);
    } else {
        // Cache miss — full draw then store result
        ctx.clearRect(0, 0, W, H);

        function geoToPx(lat, lon) {
            const xFrac = Math.max(0, Math.min(1, (lon - west) / lonRange));
            const yFrac = Math.max(0, Math.min(1, (north - lat) / latRange));
            return [tX + xFrac * tW, tY + yFrac * tH];
        }

        _drawCityCanvas(ctx, geoToPx, invZ, osmCityData, W, tW, bboxLonM);

        // Store in offscreen cache for subsequent re-renders with same parameters
        try {
            _stackOffscreen = new OffscreenCanvas(W, H);
            _stackOffscreen.getContext('2d').drawImage(overlay, 0, 0);
            _stackCacheKey  = cacheKey;
        } catch (_) { _stackOffscreen = null; _stackCacheKey = ''; }
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
    const latRange = north - south;
    const lonRange = east - west;
    const latMid   = (north + south) / 2;
    const bboxLonM = lonRange * Math.cos(latMid * Math.PI / 180) * 111_000;
    const bboxKey  = `${north.toFixed(4)},${south.toFixed(4)},${east.toFixed(4)},${west.toFixed(4)}`;
    const cacheKey = _makeCacheKey(_cityDataVersion, W, H, 1, bboxKey);

    const ctx = overlay.getContext('2d');

    if (cacheKey === _demCacheKey && _demOffscreen) {
        ctx.drawImage(_demOffscreen, 0, 0);
        return;
    }

    ctx.clearRect(0, 0, W, H);

    function geoToPx(lat, lon) {
        return [
            ((lon - west) / lonRange) * W,
            ((north - lat) / latRange) * H,
        ];
    }

    _drawCityCanvas(ctx, geoToPx, 1, osmCityData, W, W, bboxLonM);

    try {
        _demOffscreen = new OffscreenCanvas(W, H);
        _demOffscreen.getContext('2d').drawImage(overlay, 0, 0);
        _demCacheKey  = cacheKey;
    } catch (_) { _demOffscreen = null; _demCacheKey = ''; }
}
