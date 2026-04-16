/**
 * stacked-layers.js — Zoom/pan stacked layer view and coordinate grid overlay.
 *
 * Extracted from app.js (TODO item 16).  Loaded as a plain <script> before app.js
 * so the functions are available in global scope when app.js runs its DOMContentLoaded.
 *
 * Shared state is read from window.appState (set up by app.js):
 *   window.appState.currentDemBbox   — bounding box of the currently rendered DEM
 *   window.appState.selectedRegion   — currently selected region object
 *   window.appState.lastDemData      — last rendered DEM data {values, width, height}
 *
 * Internal state kept in module scope (not on appState):
 *   stackZoom           — current zoom/pan transform {scale, offsetX, offsetY}
 *   stackZoomInitialized — guard against double event-listener attachment
 */

let stackZoom = { scale: 1, offsetX: 0, offsetY: 0 };
let stackZoomInitialized = false;
let _gridCacheKey = null;
let _gridPixelMode = false;

// Cached DOM references (populated lazily on first use)
let _layerModeBtns = null;
let _cachedDemCanvas = null;

/** Toggle grid labels between lat/lon coordinates and pixel indices. */
window.setGridPixelMode = function setGridPixelMode(on) {
    _gridPixelMode = on;
    _gridCacheKey = null; // force redraw
    const sizeLabel = document.getElementById('demPixelSizeLabel');
    if (sizeLabel) {
        if (on) {
            const d = window.appState?.lastDemData;
            sizeLabel.textContent = d ? `DEM: ${d.width} × ${d.height} px` : 'DEM: — px';
            sizeLabel.style.display = 'block';
        } else {
            sizeLabel.style.display = 'none';
        }
    }
    window.drawLayerGrid?.();
};

// All layer canvas IDs — kept as hidden source buffers for other modules to write to
const LAYER_STACK = ['Dem', 'Water', 'Sat', 'SatImg', 'CityRaster', 'CompositeDem', 'Hydrology'];

// Multi-layer state: set of active layer keys + per-layer opacity (0–1)
let _activeLayers  = new Set(['Dem']);
let _layerOpacities = { Dem: 1, Water: 0.7, Sat: 0.7, SatImg: 0.8, CityRaster: 0.7, CompositeDem: 1, Hydrology: 0.8 };

// Kept for getStackMode() backward compat — last-toggled-on layer
let _activeMode = 'Dem';

/** Toggle a layer on/off; at least one layer stays on. */
window.setStackMode = function setStackMode(mode) {
    if (!LAYER_STACK.includes(mode)) return;

    if (_activeLayers.has(mode) && _activeLayers.size > 1) {
        _activeLayers.delete(mode);
    } else {
        _activeLayers.add(mode);
        _activeMode = mode;
        // Auto-load satellite imagery if switching to SatImg with no data yet
        if (mode === 'SatImg' && !window.appState?.satImgSourceCanvas) {
            window.loadSatelliteRGBImage?.().then(() => window.updateStackedLayers?.());
            return;
        }
    }

    // Update button active states
    if (!_layerModeBtns) _layerModeBtns = document.querySelectorAll('#layerModeSelector .layer-mode-btn');
    _layerModeBtns.forEach(btn => {
        btn.classList.toggle('active', _activeLayers.has(btn.dataset.mode));
    });

    _updateLayerOpacitySliders();
    window.updateStackedLayers?.();
};

/** Returns the last-activated layer mode key (backward compat). */
window.getStackMode = function getStackMode() { return _activeMode; };

/** Set per-layer opacity (0–1) and refresh. */
window.setLayerOpacity = function setLayerOpacity(mode, value) {
    _layerOpacities[mode] = Math.max(0, Math.min(1, value));
    window.updateStackedLayers?.();
};

/** Rebuild the per-layer opacity slider rows below the mode buttons. */
function _updateLayerOpacitySliders() {
    const container = document.getElementById('layerOpacitySliders');
    if (!container) return;
    container.innerHTML = '';
    // Draw in compositing order so the UI matches render order
    const order = ['Dem', 'Water', 'Sat', 'SatImg', 'CityRaster', 'CompositeDem'];
    const labels = { Dem: '🏔 DEM', Water: '💧 Water', Sat: '🌿 ESA', SatImg: '🛰 Sat', CityRaster: '🏙 City', CompositeDem: '★ Composite', Hydrology: '🌊 Hydro' };
    order.filter(m => _activeLayers.has(m)).forEach(mode => {
        const pct = Math.round((_layerOpacities[mode] ?? 1) * 100);
        const row = document.createElement('div');
        row.style.cssText = 'display:grid;grid-template-columns:70px 1fr 28px;gap:2px 4px;align-items:center;margin-top:3px;';
        row.innerHTML = `
            <span style="font-size:10px;color:#aaa;white-space:nowrap;">${labels[mode]}</span>
            <input type="range" min="0" max="100" value="${pct}" data-layer="${mode}"
                style="width:100%;" title="${labels[mode]} opacity">
            <span style="font-size:10px;color:#888;text-align:right;">${pct}%</span>`;
        const slider = row.querySelector('input');
        const label  = row.querySelector('span:last-child');
        slider.addEventListener('input', () => {
            label.textContent = slider.value + '%';
            window.setLayerOpacity(mode, slider.value / 100);
        });
        container.appendChild(row);
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Internal helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Apply a CSS transform string to the display canvas and OSM overlay. */
function _applyTransformCSS(xfm) {
    const displayCanvas = document.getElementById('stackViewCanvas');
    if (displayCanvas) { displayCanvas.style.transformOrigin = '0 0'; displayCanvas.style.transform = xfm; }
    const osmOverlay = document.querySelector('#layersStack .osm-overlay');
    if (osmOverlay) { osmOverlay.style.transformOrigin = '0 0'; osmOverlay.style.transform = xfm; }
    if (window.appState) window.appState.stackZoom = stackZoom;
}

/**
 * Pick a "nice" pixel interval targeting approximately `targetLines` grid lines
 * across a DEM of `totalPixels` pixels.
 * @param {number} totalPixels - DEM width or height in pixels
 * @param {number} targetLines - Desired approximate number of grid lines
 * @returns {number} Interval in pixel indices
 */
function nicePixelInterval(totalPixels, targetLines) {
    const raw = totalPixels / targetLines;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const mult of [1, 2, 5, 10]) {
        const candidate = mag * mult;
        if (totalPixels / candidate <= targetLines) return candidate;
    }
    return mag * 10;
}

/**
 * Pick a "nice" geographic grid interval in degrees targeting approximately
 * `targetLines` grid lines across the visible range.
 * @param {number} rangeInPixels  - Visible canvas dimension in pixels
 * @param {number} pixelsPerDegree - Current scale factor
 * @param {number} targetLines    - Desired approximate number of grid lines
 * @returns {number} Grid interval in degrees
 */
function niceGeoInterval(rangeInPixels, pixelsPerDegree, targetLines) {
    const candidates = [0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 45, 90];
    const totalRange = rangeInPixels / pixelsPerDegree;
    for (const c of candidates) {
        if (totalRange / c <= targetLines) return c;
    }
    return candidates[candidates.length - 1];
}

/**
 * Format a coordinate value as a degree string with N/S/E/W suffix.
 * @param {number}  val   - Coordinate value in degrees
 * @param {boolean} isLat - true for latitude (N/S), false for longitude (E/W)
 * @returns {string} Formatted coordinate string
 */
function formatCoord(val, isLat, interval) {
    // Choose decimal places based on grid interval so city-scale labels stay distinct.
    // interval undefined → fall back to magnitude-based heuristic.
    let dp;
    if (interval !== undefined) {
        if      (interval >= 5)    dp = 0;
        else if (interval >= 1)    dp = 1;
        else if (interval >= 0.1)  dp = 2;
        else if (interval >= 0.01) dp = 3;
        else                       dp = 4;
    } else {
        const abs = Math.abs(val);
        dp = abs >= 10 ? 1 : abs >= 1 ? 2 : 3;
    }
    const str = Math.abs(val).toFixed(dp);
    if (isLat) return str + (val >= 0 ? 'N' : 'S');
    return str + (val >= 0 ? 'E' : 'W');
}

// ─────────────────────────────────────────────────────────────────────────────
// Exported (window-level) functions
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Copy rendered layer canvases into the stacked view, aligning them to a shared
 * aspect ratio derived from the current DEM bbox.
 * Calls applyStackedTransform, drawLayerGrid, and renderCityOverlay.
 */
window.updateStackedLayers = function updateStackedLayers() {
    const demCanvas   = document.querySelector('#demImage canvas:not(.dem-gridlines-overlay):not(.city-dem-overlay):not(.water-dem-overlay):not(.sat-dem-overlay)');
    const waterCanvas = document.querySelector('#waterMaskImage canvas');
    const satCanvas   = document.querySelector('#satelliteImage canvas');

    const stack = document.getElementById('layersStack');
    if (!stack) return;

    const stackRect  = stack.getBoundingClientRect();
    const stackWidth  = stackRect.width  || 600;
    const stackHeight = stackRect.height || 400;

    const bbox = window.appState?.currentDemBbox ||
        (window.appState?.selectedRegion ? { ...window.appState.selectedRegion } : null);

    let targetWidth  = stackWidth;
    let targetHeight = stackHeight;
    let targetX = 0;
    let targetY = 0;

    if (bbox) {
        const latMid        = (bbox.north + bbox.south) / 2;
        const latCorrection = Math.cos(latMid * Math.PI / 180);
        const bboxWidth     = (bbox.east - bbox.west) * latCorrection;
        const bboxHeight    = bbox.north - bbox.south;
        const bboxAspect    = bboxWidth / bboxHeight;
        const stackAspect   = stackWidth / stackHeight;

        if (bboxAspect > stackAspect) {
            targetWidth  = stackWidth;
            targetHeight = stackWidth / bboxAspect;
            targetY      = (stackHeight - targetHeight) / 2;
        } else {
            targetHeight = stackHeight;
            targetWidth  = stackHeight * bboxAspect;
            targetX      = (stackWidth - targetWidth) / 2;
        }
    }

    // Publish letterbox geometry so drawLayerGrid and tooltip can use it
    if (window.appState) {
        window.appState.demLayout = { x: targetX, y: targetY, w: targetWidth, h: targetHeight };
    }

    /**
     * Draw a source canvas into a destination canvas at the shared target rect.
     * Avoids resetting canvas dimensions when unchanged (prevents GPU context loss).
     * @param {HTMLCanvasElement} destCanvas   - Destination canvas
     * @param {HTMLCanvasElement} sourceCanvas - Source rendered canvas
     */
    function drawLayerToTarget(destCanvas, sourceCanvas) {
        if (!destCanvas || !sourceCanvas) return;
        // Only reset dimensions when they actually change (avoids GPU flush)
        if (destCanvas.width !== stackWidth)  destCanvas.width  = stackWidth;
        if (destCanvas.height !== stackHeight) destCanvas.height = stackHeight;
        const ctx = destCanvas.getContext('2d');
        ctx.clearRect(0, 0, stackWidth, stackHeight);
        ctx.drawImage(sourceCanvas,
            0, 0, sourceCanvas.width, sourceCanvas.height,
            targetX, targetY, targetWidth, targetHeight);
    }

    // Source canvas for each layer mode
    const sourceMap = {
        Dem:          () => demCanvas,
        Water:        () => waterCanvas,
        Sat:          () => satCanvas,
        SatImg:       () => window.appState?.satImgSourceCanvas || null,
        CityRaster:   () => window.appState?.cityRasterSourceCanvas || null,
        CompositeDem: () => window.appState?.compositeDemSourceCanvas || null,
        Hydrology:    () => window.appState?.hydrologySourceCanvas || null,
    };

    // Draw each active layer into its own buffer
    LAYER_STACK.forEach(mode => {
        if (!_activeLayers.has(mode)) return;
        const src    = sourceMap[mode]?.();
        const buffer = document.getElementById(`layer${mode}Canvas`);
        if (src && buffer) drawLayerToTarget(buffer, src);
    });

    // Composite all active layers onto the display canvas in render order
    const displayCanvas = document.getElementById('stackViewCanvas');
    if (displayCanvas) {
        if (displayCanvas.width !== stackWidth)  displayCanvas.width  = stackWidth;
        if (displayCanvas.height !== stackHeight) displayCanvas.height = stackHeight;
        const dCtx = displayCanvas.getContext('2d');
        dCtx.clearRect(0, 0, stackWidth, stackHeight);
        const masterOpacity = (document.getElementById('activeLayerOpacity')?.value ?? 100) / 100;
        LAYER_STACK.forEach(mode => {
            if (!_activeLayers.has(mode)) return;
            const buffer = document.getElementById(`layer${mode}Canvas`);
            if (!buffer || buffer.width === 0 || buffer.height === 0) return;
            dCtx.globalAlpha = masterOpacity * (_layerOpacities[mode] ?? 1);
            dCtx.drawImage(buffer, 0, 0);
        });
        dCtx.globalAlpha = 1;
    }

    drawLayerGrid();

    applyStackedTransform();

    if (window.appState?.osmCityData) renderCityOverlay();
};

/**
 * Draw a coordinate-accurate graticule on `#layerGridCanvas`.
 * Rendered in screen space (not subject to CSS zoom/pan transform).
 */
window.drawLayerGrid = function drawLayerGrid() {
    const gridCanvas  = document.getElementById('layerGridCanvas');
    const demCanvas   = _cachedDemCanvas || document.getElementById('layerDemCanvas');
    const stack       = document.getElementById('layersStack');
    const yAxis       = document.getElementById('layersYAxis');
    const xAxis       = document.getElementById('layersXAxis');
    if (!gridCanvas || !stack) return;

    const rect = stack.getBoundingClientRect();
    const gw = rect.width;
    const gh = rect.height;
    if (gw === 0 || gh === 0) return;

    const { currentDemBbox: bbox, lastDemData: demDataRef, demLayout: demLayoutRef } = window.appState || {};
    if (!bbox || !demCanvas || demCanvas.width === 0 || demCanvas.height === 0) return;

    const { scale, offsetX, offsetY } = stackZoom;
    const densityCheck = 10;  // fixed default — no UI control for graticule density
    const newKey = `${bbox.north}|${bbox.south}|${bbox.east}|${bbox.west}|${scale.toFixed(3)}|${Math.round(offsetX / 2)}|${Math.round(offsetY / 2)}|${densityCheck}|${gw}|${gh}|${_gridPixelMode}`;
    if (newKey === _gridCacheKey) return;
    _gridCacheKey = newKey;

    gridCanvas.width  = gw;
    gridCanvas.height = gh;

    const ctx = gridCanvas.getContext('2d');
    ctx.clearRect(0, 0, gw, gh);

    if (yAxis) yAxis.innerHTML = '';
    if (xAxis) xAxis.innerHTML = '';
    const cw = demCanvas.width;
    const ch = demCanvas.height;

    const showGrid  = document.getElementById('showGridlines')?.checked ?? true;
    const gridColor = 'rgba(255, 255, 255, 0.2)';
    const tickColor = 'rgba(255, 255, 255, 0.5)';
    ctx.lineWidth = 1;
    ctx.font      = '9px monospace';

    if (_gridPixelMode) {
        // ── Pixel index mode ────────────────────────────────────────────────
        // Axes show DEM pixel indices (0 … width/height) instead of lat/lon.
        // The DEM is letterboxed inside the stack container at demLayout.{x,y,w,h}.
        const demData    = demDataRef;
        const demWidth   = demData?.width  || cw;
        const demHeight  = demData?.height || ch;
        const layout     = demLayoutRef || { x: 0, y: 0, w: cw, h: ch };

        // pixel p maps to: letterbox origin + fraction-of-image * letterbox size,
        // then scaled/panned by the current zoom transform.
        /** @param {number} px @returns {number} Screen x for pixel column px */
        function pixToScreenX(px) { return (layout.x + px / demWidth  * layout.w) * scale + offsetX; }
        /** @param {number} py @returns {number} Screen y for pixel row py */
        function pixToScreenY(py) { return (layout.y + py / demHeight * layout.h) * scale + offsetY; }

        const targetLines = Math.max(2, Math.round(densityCheck / 2));
        const xInterval = nicePixelInterval(demWidth,  targetLines);
        const yInterval = nicePixelInterval(demHeight, targetLines);

        // Vertical grid lines (pixel columns) — pre-compute visible pixel range
        const xFrag = xAxis ? document.createDocumentFragment() : null;
        const visPxStart = Math.max(0, Math.floor(((-2 - offsetX) / scale - layout.x) / layout.w * demWidth / xInterval) * xInterval);
        const visPxEnd   = Math.min(demWidth, Math.ceil(((gw + 2 - offsetX) / scale - layout.x) / layout.w * demWidth));
        for (let px = visPxStart; px <= visPxEnd; px += xInterval) {
            const x = pixToScreenX(px);
            if (x < -2 || x > gw + 2) continue;
            if (showGrid) {
                ctx.strokeStyle = gridColor;
                ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, gh); ctx.stroke();
            }
            ctx.strokeStyle = tickColor;
            ctx.beginPath(); ctx.moveTo(x, 0);  ctx.lineTo(x, 6);      ctx.stroke();
            ctx.beginPath(); ctx.moveTo(x, gh); ctx.lineTo(x, gh - 6); ctx.stroke();
            if (xFrag) {
                const span = document.createElement('span');
                span.className = 'axis-tick';
                span.style.left = x + 'px';
                span.textContent = String(px);
                xFrag.appendChild(span);
            }
        }
        if (xAxis && xFrag) xAxis.appendChild(xFrag);

        // Horizontal grid lines (pixel rows) — pre-compute visible pixel range
        const yFrag = yAxis ? document.createDocumentFragment() : null;
        const visPyStart = Math.max(0, Math.floor(((-2 - offsetY) / scale - layout.y) / layout.h * demHeight / yInterval) * yInterval);
        const visPyEnd   = Math.min(demHeight, Math.ceil(((gh + 2 - offsetY) / scale - layout.y) / layout.h * demHeight));
        for (let py = visPyStart; py <= visPyEnd; py += yInterval) {
            const y = pixToScreenY(py);
            if (y < -2 || y > gh + 2) continue;
            if (showGrid) {
                ctx.strokeStyle = gridColor;
                ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(gw, y); ctx.stroke();
            }
            ctx.strokeStyle = tickColor;
            ctx.beginPath(); ctx.moveTo(0,  y); ctx.lineTo(6,      y); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(gw, y); ctx.lineTo(gw - 6, y); ctx.stroke();
            if (yFrag) {
                const span = document.createElement('span');
                span.className = 'axis-tick';
                span.style.top = y + 'px';
                span.textContent = String(py);
                yFrag.appendChild(span);
            }
        }
        if (yAxis && yFrag) yAxis.appendChild(yFrag);

    } else {
        // ── Lat/lon coordinate mode (default) ───────────────────────────────
        const lonRange = bbox.east  - bbox.west;
        const latRange = bbox.north - bbox.south;
        const pxPerLon = (cw * scale) / lonRange;
        const pxPerLat = (ch * scale) / latRange;

        /** @param {number} lon @returns {number} Canvas x pixel */
        function lonToX(lon) { return (lon - bbox.west) / lonRange * cw * scale + offsetX; }
        /** @param {number} lat @returns {number} Canvas y pixel */
        function latToY(lat) { return (bbox.north - lat) / latRange * ch * scale + offsetY; }

        const targetLines = Math.max(2, Math.round(densityCheck / 2));
        const lonInterval = niceGeoInterval(gw, pxPerLon, targetLines);
        const latInterval = niceGeoInterval(gh, pxPerLat, targetLines);

        // Longitude (vertical) grid lines — batch label insertions via DocumentFragment
        const xFrag = xAxis ? document.createDocumentFragment() : null;
        // Pre-compute visible lon range from viewport edges to skip off-screen iterations
        const visLonWest = bbox.west  + (-2 - offsetX) / (cw * scale) * lonRange;
        const visLonEast = bbox.west  + (gw + 2 - offsetX) / (cw * scale) * lonRange;
        const lonStart = Math.ceil((Math.max(bbox.west, visLonWest) - 1e-9) / lonInterval) * lonInterval;
        const lonEnd   = Math.min(bbox.east, visLonEast) + 1e-9;
        for (let lon = lonStart; lon <= lonEnd; lon = Math.round((lon + lonInterval) * 1e8) / 1e8) {
            const x = lonToX(lon);
            if (x < -2 || x > gw + 2) continue;
            if (showGrid) {
                ctx.strokeStyle = gridColor;
                ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, gh); ctx.stroke();
            }
            ctx.strokeStyle = tickColor;
            ctx.beginPath(); ctx.moveTo(x, 0);  ctx.lineTo(x, 6);      ctx.stroke();
            ctx.beginPath(); ctx.moveTo(x, gh); ctx.lineTo(x, gh - 6); ctx.stroke();
            if (xFrag) {
                const span = document.createElement('span');
                span.className = 'axis-tick';
                span.style.left = x + 'px';
                span.textContent = formatCoord(lon, false, lonInterval);
                xFrag.appendChild(span);
            }
        }
        if (xAxis && xFrag) xAxis.appendChild(xFrag);

        // Latitude (horizontal) grid lines — batch label insertions via DocumentFragment
        const yFrag = yAxis ? document.createDocumentFragment() : null;
        // Pre-compute visible lat range from viewport edges to skip off-screen iterations
        const visLatNorth = bbox.north - (-2 - offsetY) / (ch * scale) * latRange;
        const visLatSouth = bbox.north - (gh + 2 - offsetY) / (ch * scale) * latRange;
        const latStart = Math.ceil((Math.max(bbox.south, visLatSouth) - 1e-9) / latInterval) * latInterval;
        const latEnd   = Math.min(bbox.north, visLatNorth) + 1e-9;
        for (let lat = latStart; lat <= latEnd; lat = Math.round((lat + latInterval) * 1e8) / 1e8) {
            const y = latToY(lat);
            if (y < -2 || y > gh + 2) continue;
            if (showGrid) {
                ctx.strokeStyle = gridColor;
                ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(gw, y); ctx.stroke();
            }
            ctx.strokeStyle = tickColor;
            ctx.beginPath(); ctx.moveTo(0,  y); ctx.lineTo(6,      y); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(gw, y); ctx.lineTo(gw - 6, y); ctx.stroke();
            if (yFrag) {
                const span = document.createElement('span');
                span.className = 'axis-tick';
                span.style.top = y + 'px';
                span.textContent = formatCoord(lat, true, latInterval);
                yFrag.appendChild(span);
            }
        }
        if (yAxis && yFrag) yAxis.appendChild(yFrag);
    }

    if (yAxis) yAxis.style.height = gh + 'px';
};

// Track last zoom scale at which city overlay was re-rendered (for LOD threshold)
let _cityOverlayLastScale = 1;
let _cityOverlayDebounceTimer = null;

/**
 * Apply the current stackZoom transform (translate + scale) as CSS to the
 * layer canvases, then redraw the grid at screen resolution.
 *
 * Performance: the city overlay canvas is also CSS-transformed for smooth visual
 * during continuous zoom/pan.  A full canvas re-render is only triggered when:
 *   - zoom scale changes by more than 15% (LOD road-width update), or
 *   - 300 ms after the last zoom/pan event settles.
 * This avoids re-rendering thousands of buildings on every wheel tick.
 */
window.applyStackedTransform = function applyStackedTransform() {
    const xfm = `translate(${stackZoom.offsetX}px, ${stackZoom.offsetY}px) scale(${stackZoom.scale})`;
    _applyTransformCSS(xfm);
    drawLayerGrid();

    // Schedule city re-render only when needed
    if (window.appState?.osmCityData && typeof window.renderCityOverlay === 'function') {
        const scaleChange = Math.abs(stackZoom.scale - _cityOverlayLastScale) / _cityOverlayLastScale;
        if (scaleChange > 0.15) {
            // Significant zoom jump (LOD change) — render immediately
            clearTimeout(_cityOverlayDebounceTimer);
            _cityOverlayLastScale = stackZoom.scale;
            window.renderCityOverlay();
        } else {
            // Small incremental scroll — debounce, render after zoom settles
            clearTimeout(_cityOverlayDebounceTimer);
            _cityOverlayDebounceTimer = setTimeout(() => {
                _cityOverlayLastScale = stackZoom.scale;
                window.renderCityOverlay();
            }, 300);
        }
    }
};

/**
 * Attach wheel-zoom and mouse-drag-pan listeners to the stacked layers container.
 * Also adds a hover tooltip showing elevation and coordinates.
 * Guards against double-initialisation with stackZoomInitialized.
 */
window.enableStackedZoomPan = function enableStackedZoomPan() {
    const stack = document.getElementById('layersStack');
    if (!stack || stackZoomInitialized) return;
    stackZoomInitialized = true;
    _cachedDemCanvas = document.getElementById('layerDemCanvas');

    let isPanning = false;
    let startX, startY;

    stack.style.cursor   = 'grab';
    stack.style.overflow = 'hidden';

    // Tooltip for pixel elevation / coordinates
    let stackTooltip = document.createElement('div');
    stackTooltip.id = 'stackTooltip';
    stackTooltip.style.cssText = `
        position: fixed;
        background: rgba(0,0,0,0.85);
        color: #fff;
        padding: 6px 10px;
        border-radius: 4px;
        font-size: 11px;
        pointer-events: none;
        z-index: 10000;
        display: none;
        white-space: nowrap;
    `;
    document.body.appendChild(stackTooltip);

    // Lightweight CSS-only pan — no grid redraw (called on every mousemove tick)
    function _applyCSSTransformOnly() {
        const xfm = `translate(${stackZoom.offsetX}px, ${stackZoom.offsetY}px) scale(${stackZoom.scale})`;
        _applyTransformCSS(xfm);
    }

    stack.addEventListener('mousemove', (e) => {
        if (isPanning) {
            stackZoom.offsetX = e.clientX - startX;
            stackZoom.offsetY = e.clientY - startY;
            _applyCSSTransformOnly();  // CSS only — grid redraws on mouseup
            stackTooltip.style.display = 'none';
            return;
        }

        const rect      = stack.getBoundingClientRect();
        const demCanvas = _cachedDemCanvas;
        const { lastDemData, demLayout, currentDemBbox } = window.appState || {};
        if (!demCanvas || !lastDemData) { stackTooltip.style.display = 'none'; return; }

        const mouseX  = e.clientX - rect.left;
        const mouseY  = e.clientY - rect.top;
        // Undo zoom/pan to get position in unscaled container space
        const canvasX = (mouseX - stackZoom.offsetX) / stackZoom.scale;
        const canvasY = (mouseY - stackZoom.offsetY) / stackZoom.scale;
        // Normalize relative to the letterboxed DEM rect, not the full container
        const layout  = demLayout || { x: 0, y: 0, w: demCanvas.width, h: demCanvas.height };
        const normX   = (canvasX - layout.x) / layout.w;
        const normY   = (canvasY - layout.y) / layout.h;

        if (normX < 0 || normX > 1 || normY < 0 || normY > 1) {
            stackTooltip.style.display = 'none';
            return;
        }

        const { width, height, values } = lastDemData;
        const pixelX = Math.min(Math.floor(normX * width),  width  - 1);
        const pixelY = Math.min(Math.floor(normY * height), height - 1);
        const idx    = pixelY * width + pixelX;

        if (idx >= 0 && idx < values.length) {
            const elevation = values[idx];
            let lat = '', lon = '';
            const bbox = currentDemBbox;
            if (bbox) {
                lat = (bbox.north - normY * (bbox.north - bbox.south)).toFixed(4);
                lon = (bbox.west  + normX * (bbox.east  - bbox.west )).toFixed(4);
            }
            stackTooltip.innerHTML = `
                <b>Elevation:</b> ${elevation.toFixed(1)}m<br>
                <b>Pixel:</b> (${pixelX}, ${pixelY})<br>
                ${lat ? `<b>Lat:</b> ${lat}° <b>Lon:</b> ${lon}°` : ''}
            `;
            stackTooltip.style.display = 'block';
            stackTooltip.style.left    = (e.clientX + 15) + 'px';
            stackTooltip.style.top     = (e.clientY + 15) + 'px';
        } else {
            stackTooltip.style.display = 'none';
        }
    });

    stack.addEventListener('mouseleave', () => {
        isPanning = false;
        stack.style.cursor = 'grab';
        stackTooltip.style.display = 'none';
    });

    stack.addEventListener('dblclick', () => {
        stackZoom = { scale: 1, offsetX: 0, offsetY: 0 };
        applyStackedTransform();
        stack.style.cursor = 'grab';
    });

    stack.addEventListener('mousedown', (e) => {
        if (e.button === 0) {
            isPanning = true;
            startX = e.clientX - stackZoom.offsetX;
            startY = e.clientY - stackZoom.offsetY;
            stack.style.cursor = 'grabbing';
            stackTooltip.style.display = 'none';
        }
    });

    stack.addEventListener('mouseup', () => {
        if (isPanning) {
            isPanning = false;
            applyStackedTransform();  // Full redraw (grid + city overlay) once pan ends
        }
        stack.style.cursor = 'grab';
    });

    stack.addEventListener('wheel', (e) => {
        e.preventDefault();
        const rect       = stack.getBoundingClientRect();
        const mouseX     = e.clientX - rect.left;
        const mouseY     = e.clientY - rect.top;
        const zoomFactor = e.deltaY > 0 ? 0.9 : 1.1;
        const newScale   = Math.max(0.5, Math.min(5, stackZoom.scale * zoomFactor));
        const scaleChange = newScale / stackZoom.scale;
        stackZoom.offsetX = mouseX - (mouseX - stackZoom.offsetX) * scaleChange;
        stackZoom.offsetY = mouseY - (mouseY - stackZoom.offsetY) * scaleChange;
        stackZoom.scale   = newScale;
        applyStackedTransform();
    });
};

// Listen for STACKED_UPDATE events (replaces scattered direct calls)
window.events?.on(window.EV?.STACKED_UPDATE, () => window.updateStackedLayers());

// Initialise per-layer opacity sliders once DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _updateLayerOpacitySliders);
} else {
    _updateLayerOpacitySliders();
}
