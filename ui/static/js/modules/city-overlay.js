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
 *
 * Render functions (renderCityOverlay, renderCityOnDEM) and raster helpers
 * (loadCityRaster, _setupCityRasterLayer, _clearCityRasterCache, _updateCitiesLoadButton)
 * live in city-render.js, which must be loaded immediately after this file.
 */

const ALPHA_BUCKETS = 8;   // number of opacity bands for building height shading

// ---------------------------------------------------------------------------
// PERF1 — shared output object for geoToPx so every call reuses one allocation
// instead of creating a new [x, y] array per vertex.
// Callers must read _pt.x / _pt.y immediately and never store the reference.
// ---------------------------------------------------------------------------
const _pt = { x: 0, y: 0 };

// ---------------------------------------------------------------------------
// PERF6 Part A — per-layer OffscreenCanvas cache.
//
// Each of the four city layers (buildings, roads, waterways, pois) is rendered
// to its own OffscreenCanvas and cached independently.  Toggling a layer only
// re-composites (4 drawImage blits) without re-drawing anything.  Only the
// specific layer whose data or canvas layout changed is re-rendered.
//
// Two cache sets: one for the stacked-layers view, one for the DEM canvas view.
// Invalidated by _invalidateCityCache() (data change) or by cache-key mismatch
// (canvas resize / bbox change / projection change).
// ---------------------------------------------------------------------------
const _LAYER_NAMES = ['waterways', 'buildings', 'roads', 'pois'];

/** Maps layer name → the DOM toggle checkbox ID for that layer. */
const _LAYER_TOGGLES = {
    buildings: 'layerBuildingsToggle',
    roads:     'layerRoadsToggle',
    waterways: 'layerWaterwaysToggle',
    pois:      'layerPoisToggle',
};

function _makeLayerCache() {
    return Object.fromEntries(_LAYER_NAMES.map(n => [n, { canvas: null, key: '' }]));
}

let _cityDataVersion = 0;   // incremented every time osmCityData changes
const _stackLayer    = _makeLayerCache();  // per-layer offscreen cache for stacked view
const _demLayer      = _makeLayerCache();  // per-layer offscreen cache for DEM view

/** null = untested, true = OffscreenCanvas available, false = not available */
let _offscreenOk = null;

// ---------------------------------------------------------------------------
// Shared render state object — city-render.js reads/writes these through
// window._cityRenderState so module-scoped variables stay encapsulated.
// ---------------------------------------------------------------------------
window._cityRenderState = {
    get offscreenOk()      { return _offscreenOk; },
    set offscreenOk(v)     { _offscreenOk = v; },
    get stackLayer()       { return _stackLayer; },
    get demLayer()         { return _demLayer; },
    get LAYER_NAMES()      { return _LAYER_NAMES; },
    get LAYER_TOGGLES()    { return _LAYER_TOGGLES; },
    get cityDataVersion()  { return _cityDataVersion; },
};

/**
 * Bump the data version and invalidate all per-layer caches.
 * Call after loading new city data so the next render re-draws all layers.
 */
window._invalidateCityCache = function () {
    _cityDataVersion++;
    for (const obj of Object.values(_stackLayer)) { obj.canvas = null; obj.key = ''; }
    for (const obj of Object.values(_demLayer))   { obj.canvas = null; obj.key = ''; }
};

/**
 * Serialise a cache key from the parameters that affect pixel output.
 * PERF2: invZ removed — CSS transform handles intermediate zoom frames.
 * The cache is only invalidated when the actual pixel layout or data changes,
 * not on every zoom step.
 */
function _makeCacheKey(version, W, H, bboxKey) {
    const proj = document.getElementById('paramProjection')?.value || 'none';
    return `${version}|${W}|${H}|${bboxKey}|${proj}`;
}

// Expose for city-render.js
window._makeCacheKey = _makeCacheKey;

/**
 * Build a projection-aware geoToPx function.
 * Maps (lat, lon) → writes result into the shared _pt object (PERF1).
 * Caller MUST read _pt.x / _pt.y immediately after the call and never store
 * the returned reference, since _pt is overwritten by the next call.
 *
 * @param {number} north/south/east/west  – bbox bounds
 * @param {number} canvasX/Y             – top-left of the drawing rect within the canvas
 * @param {number} canvasW/H             – size of the drawing rect
 * @returns {function(lat:number, lon:number): typeof _pt}
 */
function _buildGeoToPx(north, south, east, west, canvasX, canvasY, canvasW, canvasH) {
    const latRange   = north - south;
    const lonRange   = east  - west;
    const projection = document.getElementById('paramProjection')?.value || 'none';
    const toRad      = d => d * Math.PI / 180;
    const mercYfn    = l => Math.log(Math.tan(Math.PI / 4 + toRad(Math.max(-85, Math.min(85, l))) / 2));
    const mercN      = mercYfn(north), mercS = mercYfn(south), mercRange = mercN - mercS;
    const midCos     = Math.cos(toRad((north + south) / 2));

    return function geoToPx(lat, lon) {
        const xLin = (lon - west) / lonRange;
        const yLin = (north - lat) / latRange;
        let xFrac, yFrac;
        switch (projection) {
            case 'mercator':
                xFrac = xLin;
                yFrac = mercRange > 1e-10 ? (mercN - mercYfn(lat)) / mercRange : yLin;
                break;
            case 'cosine':
            case 'lambert':
                xFrac = (1 - midCos) / 2 + xLin * midCos;
                yFrac = yLin;
                break;
            case 'sinusoidal': {
                const rowCos = Math.cos(toRad(lat));
                xFrac = (1 - rowCos) / 2 + xLin * rowCos;
                yFrac = yLin;
                break;
            }
            default:
                xFrac = xLin;
                yFrac = yLin;
        }
        // PERF1: write into shared object, no allocation
        _pt.x = canvasX + xFrac * canvasW;
        _pt.y = canvasY + yFrac * canvasH;
        return _pt;
    };
}

// Expose for city-render.js
window._buildGeoToPx = _buildGeoToPx;

/**
 * PERF4: Pre-bake all features' coordinates into a flat Float32Array in pixel space,
 * and record the pixel bounding box for PERF5 viewport culling.
 *
 * Each feature gets `feat._px = { buf, counts, key, x0, y0, x1, y1 }` where:
 *   buf    — flat Float32Array of [x0,y0, x1,y1, ...] for every vertex across all rings
 *   counts — Uint16Array of vertex count per ring
 *   key    — the bakKey this was baked for (used to detect stale caches)
 *   x0/y0/x1/y1 — pixel bounding box (for PERF5 culling)
 *
 * If bakKey matches feat._px.key the feature is already up to date and skipped.
 * Baking is O(total vertices) and only runs when the canvas layout or bbox changes.
 *
 * @param {Array}    features  — GeoJSON Feature array (mutated in place)
 * @param {Function} geoToPx  — projection function from _buildGeoToPx
 * @param {string}   bakKey   — stable key for the current canvas/bbox/projection config
 */
function _prebakeFeatures(features, geoToPx, bakKey) {
    for (const feat of features) {
        if (feat._px?.key === bakKey) continue;   // already baked for this config

        const geom = feat.geometry;
        if (!geom?.coordinates) { feat._px = null; continue; }

        let rings;
        switch (geom.type) {
            case 'Polygon':         rings = geom.coordinates;          break;
            case 'MultiPolygon':    rings = geom.coordinates.flat(1);  break;
            case 'LineString':      rings = [geom.coordinates];        break;
            case 'MultiLineString': rings = geom.coordinates;          break;
            case 'Point':           rings = [[geom.coordinates]];      break;
            default:                rings = null;
        }
        if (!rings) { feat._px = null; continue; }

        // Count total vertices across all rings
        const counts = new Uint16Array(rings.length);
        let total = 0;
        rings.forEach((r, i) => { counts[i] = r.length; total += r.length; });

        // Fill pixel coords and compute pixel bbox in one pass
        const buf = new Float32Array(total * 2);
        let idx = 0;
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        for (const ring of rings) {
            for (const coord of ring) {
                geoToPx(coord[1], coord[0]);   // writes into _pt
                buf[idx++] = _pt.x;
                buf[idx++] = _pt.y;
                if (_pt.x < x0) x0 = _pt.x;  if (_pt.x > x1) x1 = _pt.x;
                if (_pt.y < y0) y0 = _pt.y;  if (_pt.y > y1) y1 = _pt.y;
            }
        }
        feat._px = { buf, counts, key: bakKey, x0, y0, x1, y1 };
    }
}

// Expose for city-render.js
window._prebakeFeatures = _prebakeFeatures;

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

    // Show loading dot on cities strip button
    const citiesDot = document.getElementById('stripDotCities');
    if (citiesDot) { citiesDot.classList.remove('loaded', 'error'); citiesDot.classList.add('loading'); }

    try {
        const layers = [];
        if (document.getElementById('layerBuildingsToggle')?.checked)  layers.push('buildings');
        if (document.getElementById('layerRoadsToggle')?.checked)      layers.push('roads');
        if (document.getElementById('layerWaterwaysToggle')?.checked)  layers.push('waterways');
        if (document.getElementById('layerPoisToggle')?.checked)       layers.push('pois');

        const simplifyTol = parseFloat(document.getElementById('citySimplifyTolerance')?.value) || 0.5;
        const minArea     = parseFloat(document.getElementById('cityMinArea')?.value) || 5.0;

        // Check cache using the same key params as the actual data endpoint
        const cacheResp = await fetch(
            `/api/cities/cached?north=${selectedRegion.north}&south=${selectedRegion.south}` +
            `&east=${selectedRegion.east}&west=${selectedRegion.west}` +
            `&simplify_tolerance=${simplifyTol}&min_area=${minArea}`
        );
        const cacheInfo = await cacheResp.json();
        if (statusEl) statusEl.textContent = cacheInfo.cached ? 'Loading from cache…' : 'Fetching from OpenStreetMap…';

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

        // Update cities strip dot
        const citiesDot = document.getElementById('stripDotCities');
        if (citiesDot) { citiesDot.classList.remove('loading', 'error'); citiesDot.classList.add('loaded'); }

        // Count malformed features (missing geometry or unrecognised type)
        let skippedCount = 0;
        for (const collection of [data.buildings, data.roads, data.waterways, data.pois]) {
            for (const feat of (collection?.features || [])) {
                if (!feat.geometry || !feat.geometry.coordinates) skippedCount++;
            }
        }
        if (skippedCount > 0 && showToast) {
            showToast(`Warning: ${skippedCount} feature${skippedCount > 1 ? 's' : ''} had missing geometry and were skipped`, 'warning');
        }

        window.renderCityOverlay?.();
        if (statusEl) statusEl.textContent = `Loaded (${data.diagonal_km?.toFixed(1) ?? '?'} km)`;
        if (showToast) showToast('City data loaded', 'success');
    } catch (e) {
        const showToastFn = window.appState?.showToast;
        if (showToastFn) showToastFn('City data error: ' + e.message, 'error');
        if (statusEl) statusEl.textContent = 'Error.';
        const cd = document.getElementById('stripDotCities');
        if (cd) { cd.classList.remove('loading', 'loaded'); cd.classList.add('error'); }
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

    // Support both {values, width, height} (lastDemData) and legacy {dem_values, dimensions}
    const vals = demData?.values ?? demData?.dem_values;
    if (!vals) return;
    const demH = demData.height ?? (demData.dimensions || [])[0] ?? 0;
    const demW = demData.width  ?? (demData.dimensions || [])[1] ?? 0;
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
    // Cancel pending renders (RAF IDs live in city-render.js)
    window._cancelCityRenders?.();
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
    const cd = document.getElementById('stripDotCities');
    if (cd) cd.classList.remove('loaded', 'loading', 'error');
};

// ---------------------------------------------------------------------------
// Core draw routine (shared between stacked-layers and DEM canvas views)
// ---------------------------------------------------------------------------

/**
 * Draw OSM city features onto a canvas context.
 *
 * PERF4: Uses pre-baked Float32Array pixel coords (feat._px.buf) when available,
 * eliminating all per-frame projection math and per-vertex allocations.
 * Falls back to geoToPx for features that haven't been baked yet.
 *
 * PERF5: Viewport culling skips features whose pixel bbox doesn't intersect
 * the visible draw rectangle, cutting path commands 60–80% at high zoom.
 *
 * Buildings are batched into ALPHA_BUCKETS opacity groups (minimises globalAlpha changes).
 * Roads are batched by rounded lineWidth (minimises lineWidth state changes).
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {Function} geoToPx    - fallback for unbaked features: geoToPx(lat, lon) → _pt
 * @param {number}   invZ       - CSS zoom compensation (1 / stackZoom.scale) for stroke widths
 * @param {Object}   osmCityData
 * @param {number}   W          - canvas pixel width (for road metre→pixel scaling)
 * @param {number}   [tW]       - letterboxed draw width; defaults to W
 * @param {number}   bboxLonM   - longitudinal bbox span in metres
 * @param {Object}   [clipRect] - visible rectangle {x0,y0,x1,y1} for viewport culling (PERF5)
 * @param {string|null} [onlyLayer] - PERF6: if set, draw only this layer unconditionally
 *   (toggle state ignored).  If null/undefined, check toggle state for all layers.
 */
function _drawCityCanvas(ctx, geoToPx, invZ, osmCityData, W, tW, bboxLonM, clipRect, onlyLayer) {
    const drawW      = tW != null ? tW : W;
    const metrePerPx = bboxLonM / drawW;

    /**
     * PERF6: Return true if this layer should be drawn in the current call.
     * When onlyLayer is set (per-layer offscreen bake), always draw that one layer.
     * When onlyLayer is null/undefined (legacy / fallback path), check toggle state.
     */
    function _shouldDraw(layerName) {
        if (onlyLayer != null) return onlyLayer === layerName;
        return !!document.getElementById(_LAYER_TOGGLES[layerName])?.checked;
    }

    /**
     * Draw a feature's path using pre-baked pixel coords when available.
     * @param {Object} feat        - GeoJSON feature with optional feat._px
     * @param {boolean} closePaths - true for polygons, false for linestrings
     */
    function _drawFeatPath(feat, closePaths) {
        const px = feat._px;
        if (px?.buf) {
            // PERF4 hot path — pure array iteration, no projection math
            const { buf, counts } = px;
            let i = 0;
            for (const count of counts) {
                ctx.moveTo(buf[i], buf[i + 1]); i += 2;
                for (let v = 1; v < count; v++, i += 2) ctx.lineTo(buf[i], buf[i + 1]);
                if (closePaths) ctx.closePath();
            }
        } else {
            // Fallback: live geoToPx (feature not yet baked)
            const geom = feat.geometry;
            if (!geom) return;
            const rings =
                geom.type === 'Polygon'         ? geom.coordinates :
                geom.type === 'MultiPolygon'    ? geom.coordinates.flat(1) :
                geom.type === 'LineString'      ? [geom.coordinates] :
                geom.type === 'MultiLineString' ? geom.coordinates : null;
            if (!rings) return;
            for (const ring of rings) {
                let first = true;
                for (const coord of ring) {
                    geoToPx(coord[1], coord[0]);
                    if (first) { ctx.moveTo(_pt.x, _pt.y); first = false; }
                    else ctx.lineTo(_pt.x, _pt.y);
                }
                if (closePaths) ctx.closePath();
            }
        }
    }

    /**
     * PERF5: Check whether a feature's pixel bbox is visible in clipRect.
     * Returns true if the feature should be culled (skipped).
     */
    function _culled(feat) {
        if (!clipRect) return false;
        const px = feat._px;
        if (px) {
            return px.x1 < clipRect.x0 || px.x0 > clipRect.x1 ||
                   px.y1 < clipRect.y0 || px.y0 > clipRect.y1;
        }
        // Fallback geo bbox cull for unbaked features
        if (feat._bbox) {
            geoToPx(feat._bbox.minLat, feat._bbox.minLon); const bx0 = _pt.x;
            geoToPx(feat._bbox.maxLat, feat._bbox.maxLon); const bx1 = _pt.x;
            return bx1 < clipRect.x0 || bx0 > clipRect.x1;
        }
        return false;
    }

    // ── Waterways ──────────────────────────────────────────────────────────
    if (_shouldDraw('waterways') && osmCityData.waterways?.features) {
        const c = document.getElementById('layerWaterwaysColor')?.value || '#4488cc';
        ctx.fillStyle   = c + '88';
        ctx.strokeStyle = c;
        ctx.lineWidth   = 1.5 * invZ;
        ctx.globalAlpha = 0.65;
        ctx.beginPath();
        for (const feat of osmCityData.waterways.features) {
            if (!feat.geometry || _culled(feat)) continue;
            const isLine = feat.geometry.type === 'LineString' || feat.geometry.type === 'MultiLineString';
            _drawFeatPath(feat, !isLine);
        }
        ctx.fill();
        ctx.stroke();
        ctx.globalAlpha = 1;
    }

    // ── Buildings — batched by opacity bucket ──────────────────────────────
    if (_shouldDraw('buildings') && osmCityData.buildings?.features?.length) {
        const baseC = document.getElementById('layerBuildingsColor')?.value || '#c8b89a';
        ctx.strokeStyle = baseC;
        ctx.lineWidth   = 0.5 * invZ;
        ctx.fillStyle   = baseC;

        const buckets = Array.from({ length: ALPHA_BUCKETS }, () => []);
        for (const feat of osmCityData.buildings.features) {
            // PERF5: sub-pixel + viewport cull using pre-baked pixel bbox
            const px = feat._px;
            if (px) {
                if (px.x1 - px.x0 < 0.5 && px.y1 - px.y0 < 0.5) continue;  // sub-pixel
                if (clipRect && (px.x1 < clipRect.x0 || px.x0 > clipRect.x1 ||
                                 px.y1 < clipRect.y0 || px.y0 > clipRect.y1)) continue;
            } else if (feat._bbox) {
                // Fallback cull for unbaked features
                geoToPx(feat._bbox.minLat, feat._bbox.minLon); const bx0 = _pt.x;
                geoToPx(feat._bbox.maxLat, feat._bbox.maxLon); const bx1 = _pt.x;
                if (Math.abs(bx1 - bx0) < 0.5) continue;
            }
            const h  = feat.properties?.height_m || 10;
            const t  = Math.min(1, Math.max(0, (h - 3) / 77));
            const bi = Math.min(ALPHA_BUCKETS - 1, Math.floor(t * ALPHA_BUCKETS));
            buckets[bi].push(feat);
        }

        for (let bi = 0; bi < ALPHA_BUCKETS; bi++) {
            if (!buckets[bi].length) continue;
            ctx.globalAlpha = 0.40 + (bi / (ALPHA_BUCKETS - 1)) * 0.45;
            ctx.beginPath();
            for (const feat of buckets[bi]) {
                _drawFeatPath(feat, true);
            }
            ctx.fill();
            ctx.stroke();
        }
        ctx.globalAlpha = 1;
    }

    // ── Roads — batched by lineWidth ───────────────────────────────────────
    if (_shouldDraw('roads') && osmCityData.roads?.features?.length) {
        const c         = document.getElementById('layerRoadsColor')?.value || '#cc8844';
        const baseWidth = parseFloat(document.getElementById('cityRoadWidth')?.value) || 1.5;
        ctx.strokeStyle = c;
        ctx.globalAlpha = 0.75;

        const groups = new Map();
        for (const feat of osmCityData.roads.features) {
            if (_culled(feat)) continue;  // PERF5: viewport cull
            const widthM  = feat.properties?.road_width_m || baseWidth;
            const widthPx = Math.max(0.5, (widthM / metrePerPx) * invZ);
            const key     = Math.round(widthPx * 2) / 2;
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push(feat);
        }

        for (const [lineWidth, feats] of groups) {
            ctx.lineWidth = lineWidth;
            ctx.beginPath();
            for (const feat of feats) {
                _drawFeatPath(feat, false);
            }
            ctx.stroke();
        }
        ctx.globalAlpha = 1;
    }

    // ── POIs — only above zoom threshold ──────────────────────────────────
    if (_shouldDraw('pois') && osmCityData.pois?.features) {
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
                if (clipRect) {
                    geoToPx(geom.coordinates[1], geom.coordinates[0]);
                    if (_pt.x < clipRect.x0 || _pt.x > clipRect.x1 ||
                        _pt.y < clipRect.y0 || _pt.y > clipRect.y1) continue;
                    ctx.beginPath();
                    ctx.arc(_pt.x, _pt.y, 3, 0, 2 * Math.PI);
                } else {
                    geoToPx(geom.coordinates[1], geom.coordinates[0]);
                    ctx.beginPath();
                    ctx.arc(_pt.x, _pt.y, 3, 0, 2 * Math.PI);
                }
                ctx.fill();
            }
            ctx.globalAlpha = 1;
        }
    }
}

// Expose for city-render.js
window._drawCityCanvas = _drawCityCanvas;

// ---------------------------------------------------------------------------
// Reactive subscriptions via appState (ARCH1)
// Re-render overlays automatically when data or bbox changes.
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
    // Wire city heights raster layer controls
    window._setupCityRasterLayer?.();

    if (!window.appState?.on) return;   // state.js not loaded

    // When DEM data changes, re-compute terrain Z for existing city features and re-render
    window.appState.on('lastDemData', (demData) => {
        const city = window.appState.osmCityData;
        if (!city) return;
        _computeTerrainZ(city.buildings, demData);
        _computeTerrainZ(city.roads, demData);
        window._invalidateCityCache();
        window.renderCityOverlay?.();
        window.renderCityOnDEM?.();
    });

    // When bbox changes, cache is stale — re-render with new projection
    window.appState.on('currentDemBbox', () => {
        if (!window.appState.osmCityData) return;
        window._invalidateCityCache();
        window.renderCityOverlay?.();
        window.renderCityOnDEM?.();
    });
});
