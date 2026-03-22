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

// ─────────────────────────────────────────────────────────────────────────────
// Internal helpers
// ─────────────────────────────────────────────────────────────────────────────

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
 * Update the lat/lon axis label elements along the stacked layer view edges.
 * (Currently a no-op — labels are updated inside drawLayerGrid.)
 */
window.updateLayerAxisLabels = function updateLayerAxisLabels() {
    // Axis label update is handled inside drawLayerGrid — no-op here
};

/**
 * Copy rendered layer canvases into the stacked view, aligning them to a shared
 * aspect ratio derived from the current DEM bbox.
 * Calls applyStackedTransform, drawLayerGrid, and renderCityOverlay.
 */
window.updateStackedLayers = function updateStackedLayers() {
    const demCanvas   = document.querySelector('#demImage canvas:not(.dem-gridlines-overlay)');
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

    /**
     * Draw a source canvas into a layer canvas at the shared target rect.
     * @param {HTMLCanvasElement} layerCanvas  - Destination layer canvas
     * @param {HTMLCanvasElement} sourceCanvas - Source rendered canvas
     */
    function drawLayerToTarget(layerCanvas, sourceCanvas) {
        if (!layerCanvas || !sourceCanvas) return;
        layerCanvas.width  = stackWidth;
        layerCanvas.height = stackHeight;
        const ctx = layerCanvas.getContext('2d');
        ctx.clearRect(0, 0, stackWidth, stackHeight);
        ctx.drawImage(sourceCanvas,
            0, 0, sourceCanvas.width, sourceCanvas.height,
            targetX, targetY, targetWidth, targetHeight);
    }

    drawLayerToTarget(document.getElementById('layerDemCanvas'),   demCanvas);
    drawLayerToTarget(document.getElementById('layerWaterCanvas'), waterCanvas);
    drawLayerToTarget(document.getElementById('layerSatCanvas'),   satCanvas);

    // City Heights raster — source canvas is stored on appState by loadCityRaster()
    const cityRasterSrc = window.appState?.cityRasterSourceCanvas;
    drawLayerToTarget(document.getElementById('layerCityRasterCanvas'), cityRasterSrc || null);

    ['Dem', 'Water', 'Sat', 'CityRaster'].forEach(layer => {
        const checkbox = document.getElementById(`layer${layer}Visible`);
        const slider   = document.getElementById(`layer${layer}Opacity`);
        const canvas   = document.getElementById(`layer${layer}Canvas`);
        if (canvas) {
            canvas.style.display = checkbox && checkbox.checked ? 'block' : 'none';
            canvas.style.opacity = slider ? slider.value / 100 : 1;
        }
    });

    updateLayerAxisLabels();

    const gridCheckbox = document.getElementById('layerGridVisible');
    if (gridCheckbox && gridCheckbox.checked) drawLayerGrid();

    applyStackedTransform();

    if (window.appState?.osmCityData) renderCityOverlay();
};

/**
 * Draw a coordinate-accurate graticule on `#layerGridCanvas`.
 * Rendered in screen space (not subject to CSS zoom/pan transform).
 */
window.drawLayerGrid = function drawLayerGrid() {
    const gridCanvas  = document.getElementById('layerGridCanvas');
    const demCanvas   = document.getElementById('layerDemCanvas');
    const stack       = document.getElementById('layersStack');
    const gridVisible = document.getElementById('layerGridVisible');
    const yAxis       = document.getElementById('layersYAxis');
    const xAxis       = document.getElementById('layersXAxis');
    if (!gridCanvas || !stack) return;

    const rect = stack.getBoundingClientRect();
    const gw = rect.width;
    const gh = rect.height;
    if (gw === 0 || gh === 0) return;
    gridCanvas.width  = gw;
    gridCanvas.height = gh;

    const ctx = gridCanvas.getContext('2d');
    ctx.clearRect(0, 0, gw, gh);

    if (yAxis) yAxis.innerHTML = '';
    if (xAxis) xAxis.innerHTML = '';

    const bbox     = window.appState?.currentDemBbox;
    const showGrid = !gridVisible || gridVisible.checked;
    if (!bbox || !demCanvas || demCanvas.width === 0 || demCanvas.height === 0) return;

    const { scale, offsetX, offsetY } = stackZoom;
    const cw       = demCanvas.width;
    const ch       = demCanvas.height;
    const lonRange = bbox.east  - bbox.west;
    const latRange = bbox.north - bbox.south;

    const pxPerLon = (cw * scale) / lonRange;
    const pxPerLat = (ch * scale) / latRange;

    /** @param {number} lon @returns {number} Canvas x pixel */
    function lonToX(lon) { return (lon - bbox.west) / lonRange * cw * scale + offsetX; }
    /** @param {number} lat @returns {number} Canvas y pixel */
    function latToY(lat) { return (bbox.north - lat) / latRange * ch * scale + offsetY; }

    const densityVal  = parseInt(document.getElementById('layerGridDensity')?.value || 10);
    const targetLines = Math.max(2, Math.round(densityVal / 2));

    const lonInterval = niceGeoInterval(gw, pxPerLon, targetLines);
    const latInterval = niceGeoInterval(gh, pxPerLat, targetLines);

    const gridColor = 'rgba(255, 255, 255, 0.2)';
    const tickColor = 'rgba(255, 255, 255, 0.5)';
    ctx.lineWidth = 1;
    ctx.font      = '9px monospace';

    // Longitude (vertical) grid lines — batch label insertions via DocumentFragment
    const xFrag = xAxis ? document.createDocumentFragment() : null;
    const lonStart = Math.ceil((bbox.west - 1e-9) / lonInterval) * lonInterval;
    for (let lon = lonStart; lon <= bbox.east + 1e-9; lon = Math.round((lon + lonInterval) * 1e8) / 1e8) {
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
    const latStart = Math.ceil((bbox.south - 1e-9) / latInterval) * latInterval;
    for (let lat = latStart; lat <= bbox.north + 1e-9; lat = Math.round((lat + latInterval) * 1e8) / 1e8) {
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

    ['Dem', 'Water', 'Sat', 'CityRaster'].forEach(layer => {
        const canvas = document.getElementById(`layer${layer}Canvas`);
        if (canvas) { canvas.style.transformOrigin = '0 0'; canvas.style.transform = xfm; }
    });

    // Apply same transform to city overlay so it moves with the other layers
    const osmOverlay = document.querySelector('#layersStack .osm-overlay');
    if (osmOverlay) { osmOverlay.style.transformOrigin = '0 0'; osmOverlay.style.transform = xfm; }

    drawLayerGrid();
    if (window.appState) window.appState.stackZoom = stackZoom;

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
        ['Dem', 'Water', 'Sat', 'CityRaster'].forEach(layer => {
            const canvas = document.getElementById(`layer${layer}Canvas`);
            if (canvas) { canvas.style.transformOrigin = '0 0'; canvas.style.transform = xfm; }
        });
        const osmOverlay = document.querySelector('#layersStack .osm-overlay');
        if (osmOverlay) { osmOverlay.style.transformOrigin = '0 0'; osmOverlay.style.transform = xfm; }
        if (window.appState) window.appState.stackZoom = stackZoom;
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
        const demCanvas = document.getElementById('layerDemCanvas');
        const lastDemData = window.appState?.lastDemData;
        if (!demCanvas || !lastDemData) { stackTooltip.style.display = 'none'; return; }

        const mouseX  = e.clientX - rect.left;
        const mouseY  = e.clientY - rect.top;
        const canvasX = (mouseX - stackZoom.offsetX) / stackZoom.scale;
        const canvasY = (mouseY - stackZoom.offsetY) / stackZoom.scale;
        const normX   = canvasX / demCanvas.width;
        const normY   = canvasY / demCanvas.height;

        if (normX < 0 || normX > 1 || normY < 0 || normY > 1) {
            stackTooltip.style.display = 'none';
            return;
        }

        const { width, height, values } = lastDemData;
        const pixelX = Math.floor(normX * width);
        const pixelY = Math.floor(normY * height);
        const idx    = pixelY * width + pixelX;

        if (idx >= 0 && idx < values.length) {
            const elevation = values[idx];
            let lat = '', lon = '';
            const bbox = window.appState?.currentDemBbox;
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

/**
 * Alias for drawLayerGrid — kept for backward compatibility.
 */
window.drawGridOverlay = function drawGridOverlay() {
    drawLayerGrid();
};
