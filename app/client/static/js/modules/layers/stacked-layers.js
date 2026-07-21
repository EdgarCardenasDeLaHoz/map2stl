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
// The {scale, offsetX, offsetY} that drawLayerGrid() last actually redrew the
// grid canvas pixels at. Between redraws (e.g. mid-drag, where only a cheap
// CSS transform runs on every mousemove) the grid canvas gets the same CSS
// transform, relative to this baseline, so it visibly tracks the pan/zoom
// instead of sitting frozen until the drag ends.
let _gridBakedZoom = { scale: 1, offsetX: 0, offsetY: 0 };

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
            sizeLabel.classList.remove('hidden');
        } else {
            sizeLabel.classList.add('hidden');
        }
    }
    window.drawLayerGrid?.();
};

// All layer canvas IDs — render order (first = bottom, last = top).
// Mutable so users can reorder via the UI.
// NOTE: Water and Hydrology are combined into WaterHydrology for unified rendering
let _layerOrder = ['Dem', 'WaterHydrology', 'Sat', 'SatImg', 'CityRaster', 'CityOverlay', 'MeshImport', 'CompositeDem'];
const LAYER_STACK = _layerOrder;  // alias kept for backward compat

/**
 * Maps each layer mode key to its DOM canvas element ID.
 * Kept separate from mode names so the HTML IDs can differ (e.g. Hydrology → layerHydroCanvas).
 */
const LAYER_CANVAS_IDS = {
    Dem: 'layerDemCanvas',
    WaterHydrology: 'layerWaterHydrologyCanvas',
    Sat: 'layerSatCanvas',
    SatImg: 'layerSatImgCanvas',
    CityRaster: 'layerCityRasterCanvas',
    MeshImport: 'layerMeshImportCanvas',
    CompositeDem: 'layerCompositeDemCanvas',
};

/** Return the layer buffer canvas for the given mode, or null if not found. */
function _getLayerBuffer(mode) {
    return getOrCreateCanvas(mode);
}

/**
 * Return (or lazily create) the hidden source canvas for a given layer mode.
 * Checks the in-memory registry first; falls back to the existing static DOM
 * element (kept in DemContainer.vue for backward compat); creates a new canvas
 * and appends it to #layersStack if neither exists.
 *
 * @param {string} layerName - One of the LAYER_CANVAS_IDS keys
 * @returns {HTMLCanvasElement|null}
 */
function getOrCreateCanvas(layerName) {
    if (_canvasRegistry.has(layerName)) return _canvasRegistry.get(layerName);
    const id = LAYER_CANVAS_IDS[layerName];
    let c = id ? document.getElementById(id) : null;
    if (!c) {
        c = document.createElement('canvas');
        if (id) c.id = id;
        c.className = 'layer-canvas hidden';
        document.getElementById('layersStack')?.appendChild(c);
    }
    _canvasRegistry.set(layerName, c);
    return c;
}

const _canvasRegistry = new Map();

/**
 * Release GPU backing store for a layer buffer by zeroing its dimensions.
 * The canvas element remains in the DOM and will be resized again on next render.
 */
function _freeLayerBuffer(mode) {
    const buf = _getLayerBuffer(mode);
    if (buf && (buf.width > 0 || buf.height > 0)) {
        buf.width = 0;
        buf.height = 0;
    }
}

/**
 * Free ALL layer buffer canvases and clear the display canvas.
 * Called on region change to prevent stale layer content from the previous region
 * bleeding into the new render.
 */
window.clearAllLayerBuffers = function clearAllLayerBuffers() {
    for (const mode of LAYER_STACK) {
        _freeLayerBuffer(mode);
    }
    const display = document.getElementById('stackViewCanvas');
    if (display && (display.width > 0 || display.height > 0)) {
        const ctx = display.getContext('2d');
        ctx.clearRect(0, 0, display.width, display.height);
    }
    // Reset zoom/pan so the new region starts at default view
    stackZoom = { scale: 1, offsetX: 0, offsetY: 0 };
    _gridCacheKey = null;
    _gridBakedZoom = { scale: 1, offsetX: 0, offsetY: 0 };
    const gridCanvas = document.getElementById('layerGridCanvas');
    if (gridCanvas) gridCanvas.style.transform = '';
};

// Multi-layer state: set of active layer keys + per-layer opacity (0–1)
let _activeLayers = new Set(['Dem', 'CityOverlay']);
let _layerOpacities = { Dem: 1, WaterHydrology: 0.75, Sat: 0.7, SatImg: 0.8, CityRaster: 0.7, CityOverlay: 0.85, MeshImport: 0.8, CompositeDem: 1 };

// Kept for getStackMode() backward compat — last-toggled-on layer
let _activeMode = 'Dem';

// ─── Composite | Satellite split view ───────────────────────────────────────
// A dual-pane alternative to the opacity-blended stack: draws the Composite
// DEM and satellite image buffers side by side in one canvas instead of
// alpha-blending them together. Both halves are drawn from the same buffers
// used by the normal blended path and the whole canvas still receives one
// shared stackZoom CSS transform, so pan/zoom stays in lockstep between panes
// without any extra synchronization code.
let _splitViewEnabled = false;

/** Enable/disable the Composite | Satellite side-by-side split view. */
window.setSplitViewEnabled = function setSplitViewEnabled(on) {
    _splitViewEnabled = !!on;
    if (_splitViewEnabled) {
        _activeLayers.add('CompositeDem');
        _activeLayers.add('SatImg');
        if (!window.appState?.satImgSourceCanvas) {
            window.loadSatelliteRGBImage?.().then(() => window.updateStackedLayers?.());
        }
    }
    window.updateStackedLayers?.();
};

window.isSplitViewEnabled = function isSplitViewEnabled() { return _splitViewEnabled; };

/** Draw CompositeDem (left) and SatImg (right) side by side into `ctx`. */
function _drawSplitView(ctx, w, h) {
    const halfW = Math.floor(w / 2);
    const compositeBuf = _getLayerBuffer('CompositeDem');
    const satBuf = _getLayerBuffer('SatImg');

    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, halfW, h);
    ctx.clip();
    if (compositeBuf && compositeBuf.width > 0) {
        ctx.drawImage(compositeBuf, 0, 0, compositeBuf.width, compositeBuf.height, 0, 0, w, h);
    }
    ctx.restore();

    ctx.save();
    ctx.beginPath();
    ctx.rect(halfW, 0, w - halfW, h);
    ctx.clip();
    if (satBuf && satBuf.width > 0) {
        ctx.drawImage(satBuf, 0, 0, satBuf.width, satBuf.height, 0, 0, w, h);
    }
    ctx.restore();

    // Divider line
    ctx.strokeStyle = 'rgba(255,255,255,0.6)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(halfW, 0);
    ctx.lineTo(halfW, h);
    ctx.stroke();
}

/** Toggle a layer on/off; at least one layer stays on. */
window.setStackMode = function setStackMode(mode) {
    if (!LAYER_STACK.includes(mode)) return;

    if (_activeLayers.has(mode) && _activeLayers.size > 1) {
        _activeLayers.delete(mode);
        // Free GPU backing store for this buffer — it will be re-allocated on next render
        _freeLayerBuffer(mode);
    } else {
        _activeLayers.add(mode);
        _activeMode = mode;
        // Auto-load satellite imagery if switching to SatImg with no data yet
        if (mode === 'SatImg' && !window.appState?.satImgSourceCanvas) {
            window.loadSatelliteRGBImage?.().then(() => window.updateStackedLayers?.());
            return;
        }
        // Auto-load water+hydrology combined if switching to WaterHydrology with no data yet
        if (mode === 'WaterHydrology' && !window.appState?.waterHydrologyCanvas) {
            window.loadWaterHydrology?.();
            return;
        }
    }

    // Update button active states
    if (!_layerModeBtns) _layerModeBtns = document.querySelectorAll('#layerModeSelector .layer-mode-btn');
    _layerModeBtns.forEach(btn => {
        btn.classList.toggle('active', _activeLayers.has(btn.dataset.mode));
    });

    _updateLayerOpacitySliders();
    _syncCityOverlayLayerState();
    window.updateStackedLayers?.();
};

/** Returns the last-activated layer mode key (backward compat). */
window.getStackMode = function getStackMode() { return _activeMode; };

/** Set per-layer opacity (0–1) and refresh. */
window.setLayerOpacity = function setLayerOpacity(mode, value) {
    _layerOpacities[mode] = Math.max(0, Math.min(1, value));
    if (mode === 'CityOverlay') _syncCityOverlayLayerState();
    window.updateStackedLayers?.();
};

/** Return a copy of the current layer render order (bottom → top). */
window.getLayerOrder = function getLayerOrder() {
    return [..._layerOrder];
};

/**
 * Move a layer up or down in the render order.
 * Performs adjacent swaps until the layer passes the next active (visible) layer,
 * so reorder arrows feel intuitive among the visible subset.
 * @param {string} mode  — layer key (e.g. 'Hydrology')
 * @param {number} delta — direction: -1 = move toward bottom, +1 = move toward top
 */
window.moveLayer = function moveLayer(mode, delta) {
    let idx = _layerOrder.indexOf(mode);
    if (idx < 0) return;
    let swapped = false;
    // Keep swapping in direction until we've passed at least one active layer
    while (true) {
        const next = idx + delta;
        if (next < 0 || next >= _layerOrder.length) break;
        const neighbor = _layerOrder[next];
        [_layerOrder[idx], _layerOrder[next]] = [_layerOrder[next], _layerOrder[idx]];
        idx = next;
        if (_activeLayers.has(neighbor)) { swapped = true; break; }
    }
    if (!swapped) return;  // couldn't move past any active layer
    _updateLayerOpacitySliders();
    window.updateStackedLayers?.();
};

/** Rebuild the per-layer opacity slider rows below the mode buttons.
 *  Shows active layers in render order (bottom → top) with reorder arrows. */
function _updateLayerOpacitySliders() {
    const container = document.getElementById('layerOpacitySliders');
    if (!container) return;
    container.innerHTML = '';
    const labels = { Dem: '🏔 DEM', Water: '💧 Water', Sat: '🌿 ESA', SatImg: '🛰 Sat', CityRaster: '🏙 City Raster', CityOverlay: '🏙 City Polygons', MeshImport: '📐 Mesh Import', CompositeDem: '★ Composite', Hydrology: '🌊 Hydro' };
    // Show active layers in current render order (bottom first, top last)
    const visible = _layerOrder.filter(m => _activeLayers.has(m));
    visible.forEach((mode, vi) => {
        const pct = Math.round((_layerOpacities[mode] ?? 1) * 100);
        const isFirst = vi === 0;
        const isLast = vi === visible.length - 1;
        const row = document.createElement('div');
        row.className = 'layer-stack-row';
        row.innerHTML = `
            <span class="layer-reorder-arrows" style="display:flex;flex-direction:column;line-height:1;font-size:9px;gap:0;">
                <button class="layer-arrow-btn" data-layer="${mode}" data-dir="1"
                    style="background:none;border:none;color:${isLast ? '#333' : '#888'};cursor:${isLast ? 'default' : 'pointer'};padding:0;font-size:9px;line-height:1;"
                    title="Move up (render later / on top)" ${isLast ? 'disabled' : ''}>▲</button>
                <button class="layer-arrow-btn" data-layer="${mode}" data-dir="-1"
                    style="background:none;border:none;color:${isFirst ? '#333' : '#888'};cursor:${isFirst ? 'default' : 'pointer'};padding:0;font-size:9px;line-height:1;"
                    title="Move down (render earlier / behind)" ${isFirst ? 'disabled' : ''}>▼</button>
            </span>
            <span style="font-size:10px;color:#aaa;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${labels[mode]}</span>
            <input type="range" min="0" max="100" value="${pct}" data-layer="${mode}"
                style="width:100%;" title="${labels[mode]} opacity">
            <span style="font-size:10px;color:#888;text-align:right;">${pct}%</span>`;
        // Wire opacity slider
        const slider = row.querySelector('input[type="range"]');
        const label = row.querySelector('span:last-child');
        slider.addEventListener('input', () => {
            label.textContent = slider.value + '%';
            window.setLayerOpacity(mode, slider.value / 100);
        });
        // Wire reorder arrows
        row.querySelectorAll('.layer-arrow-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const dir = parseInt(btn.dataset.dir);
                window.moveLayer(btn.dataset.layer, dir);
            });
        });
        container.appendChild(row);
    });
}

function _syncCityOverlayLayerState() {
    const overlay = document.querySelector('#layersStack .osm-overlay');
    if (!overlay) return;
    const visible = _activeLayers.has('CityOverlay');
    const masterOpacity = (document.getElementById('activeLayerOpacity')?.value ?? 100) / 100;
    overlay.style.display = visible ? '' : 'none';
    overlay.style.opacity = String(masterOpacity * (_layerOpacities.CityOverlay ?? 1));
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
    _applyGridCSSDelta();
}

/**
 * CSS-transform the grid canvas by the delta between the current stackZoom
 * and the zoom last actually baked into its pixels (by drawLayerGrid). This
 * makes the grid visibly follow continuous pan/zoom gestures even though its
 * content is only re-rasterized (cheaply) once the gesture settles — without
 * this, the grid stayed frozen in place while the terrain slid underneath it
 * during an active drag.
 */
function _applyGridCSSDelta() {
    const gridCanvas = document.getElementById('layerGridCanvas');
    if (!gridCanvas) return;
    const dx = stackZoom.offsetX - _gridBakedZoom.offsetX;
    const dy = stackZoom.offsetY - _gridBakedZoom.offsetY;
    const ds = stackZoom.scale / _gridBakedZoom.scale;
    gridCanvas.style.transformOrigin = '0 0';
    gridCanvas.style.transform = `translate(${dx}px, ${dy}px) scale(${ds})`;
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
        if (interval >= 5) dp = 0;
        else if (interval >= 1) dp = 1;
        else if (interval >= 0.1) dp = 2;
        else if (interval >= 0.01) dp = 3;
        else dp = 4;
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
    const demCanvas = document.querySelector(`#demImage ${window.DEM_CANVAS_SELECTOR}`);
    const waterCanvas = document.querySelector('#waterMaskImage canvas');
    const satCanvas = document.querySelector('#satelliteImage canvas');

    const stack = document.getElementById('layersStack');
    if (!stack) return;

    const stackRect = stack.getBoundingClientRect();
    const stackWidth = stackRect.width || 600;
    const stackHeight = stackRect.height || 400;

    const bbox = window.appState?.currentDemBbox ||
        (window.appState?.selectedRegion ? { ...window.appState.selectedRegion } : null);

    let targetWidth = stackWidth;
    let targetHeight = stackHeight;
    let targetX = 0;
    let targetY = 0;

    if (bbox) {
        const latMid = (bbox.north + bbox.south) / 2;
        const latCorrection = Math.cos(latMid * Math.PI / 180);
        const bboxWidth = (bbox.east - bbox.west) * latCorrection;
        const bboxHeight = bbox.north - bbox.south;
        const bboxAspect = bboxWidth / bboxHeight;
        const stackAspect = stackWidth / stackHeight;

        if (bboxAspect > stackAspect) {
            targetWidth = stackWidth;
            targetHeight = stackWidth / bboxAspect;
            targetY = (stackHeight - targetHeight) / 2;
        } else {
            targetHeight = stackHeight;
            targetWidth = stackHeight * bboxAspect;
            targetX = (stackWidth - targetWidth) / 2;
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
        if (destCanvas.width !== stackWidth) destCanvas.width = stackWidth;
        if (destCanvas.height !== stackHeight) destCanvas.height = stackHeight;
        const ctx = destCanvas.getContext('2d');
        ctx.clearRect(0, 0, stackWidth, stackHeight);
        ctx.drawImage(sourceCanvas,
            0, 0, sourceCanvas.width, sourceCanvas.height,
            targetX, targetY, targetWidth, targetHeight);
    }

    // Source canvas for each layer mode
    const sourceMap = {
        Dem: () => demCanvas,
        WaterHydrology: () => window.appState?.waterHydrologyCanvas || null,
        Sat: () => satCanvas,
        SatImg: () => window.appState?.satImgSourceCanvas || null,
        CityRaster: () => window.appState?.cityRasterSourceCanvas || null,
        MeshImport: () => window.appState?.meshSourceCanvas || null,
        CompositeDem: () => window.appState?.compositeDemSourceCanvas || null,
    };

    // Draw each active layer into its own buffer
    LAYER_STACK.forEach(mode => {
        if (!_activeLayers.has(mode)) return;
        if (mode === 'CityOverlay') return;
        const src = sourceMap[mode]?.();
        const buffer = _getLayerBuffer(mode);
        if (src && buffer) drawLayerToTarget(buffer, src);
    });

    // Composite all active layers onto the display canvas in render order
    const displayCanvas = document.getElementById('stackViewCanvas');
    if (displayCanvas) {
        if (displayCanvas.width !== stackWidth) displayCanvas.width = stackWidth;
        if (displayCanvas.height !== stackHeight) displayCanvas.height = stackHeight;
        const dCtx = displayCanvas.getContext('2d');
        dCtx.clearRect(0, 0, stackWidth, stackHeight);

        if (_splitViewEnabled) {
            _drawSplitView(dCtx, stackWidth, stackHeight);
        } else {
            const masterOpacity = (document.getElementById('activeLayerOpacity')?.value ?? 100) / 100;
            LAYER_STACK.forEach(mode => {
                if (!_activeLayers.has(mode)) return;
                if (mode === 'CityOverlay') return;
                const buffer = _getLayerBuffer(mode);
                if (!buffer || buffer.width === 0 || buffer.height === 0) return;
                dCtx.globalAlpha = masterOpacity * (_layerOpacities[mode] ?? 1);
                dCtx.drawImage(buffer, 0, 0);
            });
            dCtx.globalAlpha = 1;
        }
    }

    _syncCityOverlayLayerState();

    drawLayerGrid();

    applyStackedTransform();

    if (window.appState?.osmCityData && _activeLayers.has('CityOverlay')) {
        renderCityOverlay();
    } else {
        window._cancelCityRenders?.();
        document.querySelector('#layersStack .osm-overlay')?.remove();
    }
};

/**
 * Draw a coordinate-accurate graticule on `#layerGridCanvas`.
 * Rendered in screen space (not subject to CSS zoom/pan transform).
 */
window.drawLayerGrid = function drawLayerGrid() {
    const gridCanvas = document.getElementById('layerGridCanvas');
    const demCanvas = _cachedDemCanvas || document.getElementById('layerDemCanvas');
    const stack = document.getElementById('layersStack');
    const yAxis = document.getElementById('layersYAxis');
    const xAxis = document.getElementById('layersXAxis');
    if (!gridCanvas || !stack) return;

    const rect = stack.getBoundingClientRect();
    const gw = rect.width;
    const gh = rect.height;
    if (gw === 0 || gh === 0) return;

    const { currentDemBbox: bbox, lastDemData: demDataRef, demLayout: demLayoutRef } = window.appState || {};
    if (!bbox || !demCanvas || demCanvas.width === 0 || demCanvas.height === 0) return;

    const { scale, offsetX, offsetY } = stackZoom;
    const densityCheck = Math.max(2, parseInt(document.getElementById('gridlineCount')?.value || '10', 10));
    const showGrid = document.getElementById('showGridlines')?.checked ?? true;
    const projection = document.getElementById('paramProjection')?.value || 'none';
    // Independent red pixel grid (DEM pixel space, user-set spacing, default 1000).
    const pixelGridOn = document.getElementById('showPixelGrid')?.checked ?? false;
    let pixelGridSpacing = parseInt(document.getElementById('pixelGridSpacing')?.value, 10);
    if (!Number.isFinite(pixelGridSpacing) || pixelGridSpacing < 1) pixelGridSpacing = 1000;
    const pixelGridColor = document.getElementById('pixelGridColor')?.value || '#ff0000';
    const newKey = `${bbox.north}|${bbox.south}|${bbox.east}|${bbox.west}|${scale.toFixed(3)}|${Math.round(offsetX / 2)}|${Math.round(offsetY / 2)}|${densityCheck}|${gw}|${gh}|${_gridPixelMode}|${showGrid}|${projection}|${pixelGridOn}|${pixelGridSpacing}|${pixelGridColor}`;
    if (newKey === _gridCacheKey) return;
    _gridCacheKey = newKey;

    // We're about to re-rasterize the grid at the current stackZoom — clear
    // any interim CSS transform applied during a drag/zoom gesture (see
    // _applyGridCSSDelta) and record this as the new baseline.
    gridCanvas.style.transform = '';
    _gridBakedZoom = { ...stackZoom };

    gridCanvas.width = gw;
    gridCanvas.height = gh;

    const ctx = gridCanvas.getContext('2d');
    ctx.clearRect(0, 0, gw, gh);

    if (yAxis) yAxis.innerHTML = '';
    if (xAxis) xAxis.innerHTML = '';
    const cw = demCanvas.width;
    const ch = demCanvas.height;

    // ── Red pixel grid (independent of the geographic gridlines) ────────────
    // Draws lines every `pixelGridSpacing` DEM pixels, mapped through the same
    // letterbox + zoom transform as everything else, and updates the dims label.
    if (pixelGridOn) {
        const demW = demDataRef?.width || cw;
        const demH = demDataRef?.height || ch;
        const layout = demLayoutRef || { x: 0, y: 0, w: cw, h: ch };
        const toScreenX = (pxCol) => (layout.x + pxCol / demW * layout.w) * scale + offsetX;
        const toScreenY = (pxRow) => (layout.y + pxRow / demH * layout.h) * scale + offsetY;

        ctx.save();
        ctx.strokeStyle = pixelGridColor;
        ctx.fillStyle = pixelGridColor;
        ctx.lineWidth = 1.25;
        ctx.font = 'bold 10px monospace';
        ctx.shadowColor = 'rgba(0,0,0,0.7)';
        ctx.shadowBlur = 2;

        for (let col = 0; col <= demW; col += pixelGridSpacing) {
            const x = toScreenX(col);
            if (x < -2 || x > gw + 2) continue;
            ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, gh); ctx.stroke();
            ctx.fillText(`${col}px`, Math.min(x + 3, gw - 34), 11);
        }
        // far edge (full width) if spacing doesn't land on it
        if (demW % pixelGridSpacing !== 0) {
            const xe = toScreenX(demW);
            ctx.beginPath(); ctx.moveTo(xe, 0); ctx.lineTo(xe, gh); ctx.stroke();
        }
        for (let row = 0; row <= demH; row += pixelGridSpacing) {
            const y = toScreenY(row);
            if (y < -2 || y > gh + 2) continue;
            ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(gw, y); ctx.stroke();
            ctx.fillText(`${row}px`, 3, Math.min(y + 11, gh - 3));
        }
        if (demH % pixelGridSpacing !== 0) {
            const ye = toScreenY(demH);
            ctx.beginPath(); ctx.moveTo(0, ye); ctx.lineTo(gw, ye); ctx.stroke();
        }
        ctx.restore();

        const dimsLabel = document.getElementById('pixelGridDimsLabel');
        if (dimsLabel) {
            dimsLabel.textContent = `Pixel grid: ${demW} × ${demH} px  (every ${pixelGridSpacing} px)`;
            dimsLabel.classList.remove('hidden');
        }
    } else {
        const dimsLabel = document.getElementById('pixelGridDimsLabel');
        if (dimsLabel) dimsLabel.classList.add('hidden');
    }

    if (!showGrid) {
        return;
    }
    const gridColor = 'rgba(255, 255, 255, 0.2)';
    const tickColor = 'rgba(255, 255, 255, 0.5)';
    ctx.lineWidth = 1;
    ctx.font = '9px monospace';

    if (_gridPixelMode) {
        // ── Pixel index mode ────────────────────────────────────────────────
        // Axes show DEM pixel indices (0 … width/height) instead of lat/lon.
        // The DEM is letterboxed inside the stack container at demLayout.{x,y,w,h}.
        const demData = demDataRef;
        const demWidth = demData?.width || cw;
        const demHeight = demData?.height || ch;
        const layout = demLayoutRef || { x: 0, y: 0, w: cw, h: ch };

        // pixel p maps to: letterbox origin + fraction-of-image * letterbox size,
        // then scaled/panned by the current zoom transform.
        /** @param {number} px @returns {number} Screen x for pixel column px */
        function pixToScreenX(px) { return (layout.x + px / demWidth * layout.w) * scale + offsetX; }
        /** @param {number} py @returns {number} Screen y for pixel row py */
        function pixToScreenY(py) { return (layout.y + py / demHeight * layout.h) * scale + offsetY; }

        const targetLines = Math.max(2, densityCheck);
        const xInterval = nicePixelInterval(demWidth, targetLines);
        const yInterval = nicePixelInterval(demHeight, targetLines);

        // Vertical grid lines (pixel columns) — pre-compute visible pixel range
        const xFrag = xAxis ? document.createDocumentFragment() : null;
        const visPxStart = Math.max(0, Math.floor(((-2 - offsetX) / scale - layout.x) / layout.w * demWidth / xInterval) * xInterval);
        const visPxEnd = Math.min(demWidth, Math.ceil(((gw + 2 - offsetX) / scale - layout.x) / layout.w * demWidth));
        for (let px = visPxStart; px <= visPxEnd; px += xInterval) {
            const x = pixToScreenX(px);
            if (x < -2 || x > gw + 2) continue;
            if (showGrid) {
                ctx.strokeStyle = gridColor;
                ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, gh); ctx.stroke();
            }
            ctx.strokeStyle = tickColor;
            ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, 6); ctx.stroke();
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
        const visPyEnd = Math.min(demHeight, Math.ceil(((gh + 2 - offsetY) / scale - layout.y) / layout.h * demHeight));
        for (let py = visPyStart; py <= visPyEnd; py += yInterval) {
            const y = pixToScreenY(py);
            if (y < -2 || y > gh + 2) continue;
            if (showGrid) {
                ctx.strokeStyle = gridColor;
                ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(gw, y); ctx.stroke();
            }
            ctx.strokeStyle = tickColor;
            ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(6, y); ctx.stroke();
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
        const lonRange = bbox.east - bbox.west;
        const latRange = bbox.north - bbox.south;

        // Projection-aware coordinate transforms
        const projection = document.getElementById('paramProjection')?.value || 'none';
        const toRad = d => d * Math.PI / 180;

        // Mercator helpers
        const _mercY = l => Math.log(Math.tan(Math.PI / 4 + toRad(Math.max(-85, Math.min(85, l))) / 2));
        const mercN = _mercY(Math.min(85, bbox.north));
        const mercS = _mercY(Math.max(-85, bbox.south));
        const mercRange = mercN - mercS;

        // Cosine/Lambert helpers
        const midLat = (bbox.north + bbox.south) / 2;
        const cosMiddle = Math.cos(toRad(midLat));
        const contentW = (projection === 'cosine' || projection === 'lambert')
            ? Math.max(1, cw * cosMiddle) : cw;
        const xOff = (cw - contentW) / 2;

        // Sinusoidal: midLon for centering
        const midLon = (bbox.east + bbox.west) / 2;

        /** @param {number} lon @param {number} [lat] - needed for sinusoidal @returns {number} Canvas x */
        function lonToX(lon, lat) {
            if (projection === 'sinusoidal') {
                const cosLat = Math.cos(toRad(lat ?? midLat));
                const xFrac = 0.5 + (lon - midLon) / lonRange * cosLat;
                return xFrac * cw * scale + offsetX;
            }
            if (projection === 'cosine' || projection === 'lambert') {
                return (xOff + (lon - bbox.west) / lonRange * contentW) * scale + offsetX;
            }
            return (lon - bbox.west) / lonRange * cw * scale + offsetX;
        }

        /** @param {number} lat @returns {number} Canvas y pixel */
        function latToY(lat) {
            if (projection === 'mercator') {
                return (mercN - _mercY(lat)) / mercRange * ch * scale + offsetY;
            }
            return (bbox.north - lat) / latRange * ch * scale + offsetY;
        }

        const pxPerLon = (contentW * scale) / lonRange;
        const pxPerLat = (ch * scale) / latRange;

        const targetLines = Math.max(2, densityCheck);
        const lonInterval = niceGeoInterval(gw, pxPerLon, targetLines);
        const latInterval = niceGeoInterval(gh, pxPerLat, targetLines);

        // Longitude (vertical) grid lines — batch label insertions via DocumentFragment
        const xFrag = xAxis ? document.createDocumentFragment() : null;
        const visLonWest = bbox.west + (-2 - offsetX) / (cw * scale) * lonRange;
        const visLonEast = bbox.west + (gw + 2 - offsetX) / (cw * scale) * lonRange;
        const lonStart = Math.ceil((Math.max(bbox.west, visLonWest) - 1e-9) / lonInterval) * lonInterval;
        const lonEnd = Math.min(bbox.east, visLonEast) + 1e-9;
        for (let lon = lonStart; lon <= lonEnd; lon = Math.round((lon + lonInterval) * 1e8) / 1e8) {
            if (projection === 'sinusoidal') {
                // Sinusoidal: vertical gridlines are curves — draw as polyline
                if (showGrid) {
                    ctx.strokeStyle = gridColor;
                    ctx.beginPath();
                    let first = true;
                    for (let sy = 0; sy <= gh; sy += 3) {
                        const lat = bbox.north - ((sy - offsetY) / (ch * scale)) * latRange;
                        const x = lonToX(lon, lat);
                        if (first) { ctx.moveTo(x, sy); first = false; }
                        else ctx.lineTo(x, sy);
                    }
                    ctx.stroke();
                }
                // Tick + label at midpoint latitude
                const xMid = lonToX(lon, midLat);
                ctx.strokeStyle = tickColor;
                ctx.beginPath(); ctx.moveTo(xMid, 0); ctx.lineTo(xMid, 6); ctx.stroke();
                ctx.beginPath(); ctx.moveTo(xMid, gh); ctx.lineTo(xMid, gh - 6); ctx.stroke();
                if (xFrag) {
                    const span = document.createElement('span');
                    span.className = 'axis-tick';
                    span.style.left = xMid + 'px';
                    span.textContent = formatCoord(lon, false, lonInterval);
                    xFrag.appendChild(span);
                }
            } else {
                // Non-sinusoidal: straight vertical lines
                const x = lonToX(lon);
                if (x < -2 || x > gw + 2) continue;
                if (showGrid) {
                    ctx.strokeStyle = gridColor;
                    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, gh); ctx.stroke();
                }
                ctx.strokeStyle = tickColor;
                ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, 6); ctx.stroke();
                ctx.beginPath(); ctx.moveTo(x, gh); ctx.lineTo(x, gh - 6); ctx.stroke();
                if (xFrag) {
                    const span = document.createElement('span');
                    span.className = 'axis-tick';
                    span.style.left = x + 'px';
                    span.textContent = formatCoord(lon, false, lonInterval);
                    xFrag.appendChild(span);
                }
            }
        }
        if (xAxis && xFrag) xAxis.appendChild(xFrag);

        // Latitude (horizontal) grid lines — batch label insertions via DocumentFragment
        const yFrag = yAxis ? document.createDocumentFragment() : null;
        const visLatNorth = bbox.north - (-2 - offsetY) / (ch * scale) * latRange;
        const visLatSouth = bbox.north - (gh + 2 - offsetY) / (ch * scale) * latRange;
        const latStart = Math.ceil((Math.max(bbox.south, visLatSouth) - 1e-9) / latInterval) * latInterval;
        const latEnd = Math.min(bbox.north, visLatNorth) + 1e-9;
        for (let lat = latStart; lat <= latEnd; lat = Math.round((lat + latInterval) * 1e8) / 1e8) {
            const y = latToY(lat);
            if (y < -2 || y > gh + 2) continue;
            if (showGrid) {
                ctx.strokeStyle = gridColor;
                ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(gw, y); ctx.stroke();
            }
            ctx.strokeStyle = tickColor;
            ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(6, y); ctx.stroke();
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
    if (window.appState?.osmCityData && _activeLayers.has('CityOverlay') && typeof window.renderCityOverlay === 'function') {
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

    stack.classList.add('stack-cursor-grab', 'stack-overflow-hidden');

    // Tooltip for pixel elevation / coordinates
    let stackTooltip = document.createElement('div');
    stackTooltip.id = 'stackTooltip';
    stackTooltip.className = 'stack-tooltip hidden';
    document.body.appendChild(stackTooltip);

    // Lightweight CSS-only pan (called on every mousemove tick) — no expensive
    // grid re-rasterization, but _applyTransformCSS also nudges the grid
    // canvas's own CSS transform (see _applyGridCSSDelta) so it visibly
    // follows the drag instead of sitting frozen until mouseup.
    function _applyCSSTransformOnly() {
        const xfm = `translate(${stackZoom.offsetX}px, ${stackZoom.offsetY}px) scale(${stackZoom.scale})`;
        _applyTransformCSS(xfm);
    }

    stack.addEventListener('mousemove', (e) => {
        if (isPanning) {
            stackZoom.offsetX = e.clientX - startX;
            stackZoom.offsetY = e.clientY - startY;
            _applyCSSTransformOnly();  // CSS only — grid redraws on mouseup
            stackTooltip.classList.add('hidden');
            return;
        }

        const rect = stack.getBoundingClientRect();
        const demCanvas = _cachedDemCanvas;
        const { lastDemData, demLayout, currentDemBbox } = window.appState || {};
        if (!demCanvas || !lastDemData) { stackTooltip.classList.add('hidden'); return; }

        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;
        // Undo zoom/pan to get position in unscaled container space
        const canvasX = (mouseX - stackZoom.offsetX) / stackZoom.scale;
        const canvasY = (mouseY - stackZoom.offsetY) / stackZoom.scale;
        // Normalize relative to the letterboxed DEM rect, not the full container
        const layout = demLayout || { x: 0, y: 0, w: demCanvas.width, h: demCanvas.height };
        const normX = (canvasX - layout.x) / layout.w;
        const normY = (canvasY - layout.y) / layout.h;

        if (normX < 0 || normX > 1 || normY < 0 || normY > 1) {
            stackTooltip.classList.add('hidden');
            return;
        }

        const { width, height, values } = lastDemData;
        const pixelX = Math.min(Math.floor(normX * width), width - 1);
        const pixelY = Math.min(Math.floor(normY * height), height - 1);
        const idx = pixelY * width + pixelX;

        if (idx >= 0 && idx < values.length) {
            const elevation = values[idx];
            let lat = '', lon = '';
            const bbox = currentDemBbox;
            if (bbox) {
                lat = (bbox.north - normY * (bbox.north - bbox.south)).toFixed(4);
                lon = (bbox.west + normX * (bbox.east - bbox.west)).toFixed(4);
            }
            stackTooltip.innerHTML = `
                <b>Elevation:</b> ${elevation.toFixed(1)}m<br>
                <b>Pixel:</b> (${pixelX}, ${pixelY})<br>
                ${lat ? `<b>Lat:</b> ${lat}° <b>Lon:</b> ${lon}°` : ''}
            `;
            stackTooltip.classList.remove('hidden');
            stackTooltip.style.left = (e.clientX + 15) + 'px';
            stackTooltip.style.top = (e.clientY + 15) + 'px';
        } else {
            stackTooltip.classList.add('hidden');
        }
    });

    stack.addEventListener('mouseleave', () => {
        isPanning = false;
        stack.classList.remove('stack-cursor-grabbing');
        stack.classList.add('stack-cursor-grab');
        stackTooltip.classList.add('hidden');
    });

    stack.addEventListener('dblclick', () => {
        stackZoom = { scale: 1, offsetX: 0, offsetY: 0 };
        applyStackedTransform();
        stack.classList.remove('stack-cursor-grabbing');
        stack.classList.add('stack-cursor-grab');
    });

    stack.addEventListener('mousedown', (e) => {
        if (e.button === 0) {
            isPanning = true;
            startX = e.clientX - stackZoom.offsetX;
            startY = e.clientY - stackZoom.offsetY;
            stack.classList.remove('stack-cursor-grab');
            stack.classList.add('stack-cursor-grabbing');
            stackTooltip.classList.add('hidden');
        }
    });

    stack.addEventListener('mouseup', () => {
        if (isPanning) {
            isPanning = false;
            applyStackedTransform();  // Full redraw (grid + city overlay) once pan ends
        }
        stack.classList.remove('stack-cursor-grabbing');
        stack.classList.add('stack-cursor-grab');
    });

    stack.addEventListener('wheel', (e) => {
        e.preventDefault();
        const rect = stack.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;
        const zoomFactor = e.deltaY > 0 ? 0.9 : 1.1;
        const newScale = Math.max(0.5, Math.min(5, stackZoom.scale * zoomFactor));
        const scaleChange = newScale / stackZoom.scale;
        stackZoom.offsetX = mouseX - (mouseX - stackZoom.offsetX) * scaleChange;
        stackZoom.offsetY = mouseY - (mouseY - stackZoom.offsetY) * scaleChange;
        stackZoom.scale = newScale;
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
