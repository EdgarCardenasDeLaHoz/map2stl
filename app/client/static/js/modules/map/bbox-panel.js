import { REGION_ACCENT } from '../regions/region-geometry.js';

// === Constants ===
// Shared by every caller of setBboxRectangle, so the box looks the same however
// the user got to it. The working extent: a dashed accent outline that sits on
// the selected region's solid box (region-boxes.js) and moves off it while the
// region editor's N/S/E/W fields are edited. Not interactive, so hovers and
// clicks reach the saved-region boxes underneath.
const BBOX_RECT_STYLE = { color: REGION_ACCENT, weight: 2, dashArray: '6 4', fill: false, interactive: false };

/**
 * modules/map/bbox-panel.js
 *
 * Exposed on window:
 *   setBboxInputValues, setBboxRectangle, initBboxMiniMap, syncBboxMiniMap,
 *   toggleBboxMiniMap, setupGridToggle
 *
 * Depends on:
 *   window.appState.selectedRegion, window.appState.currentDemBbox
 *   window.getBoundingBox?.(), window.setBoundingBox?.(), window.getMap?.(),
 *   window.setSelectedRegion?.(), window.showToast?.(), window.events / window.EV
 *   window.drawGridlinesOverlay?.(), window.loadDEM?.(), window.loadWaterMask?.(),
 *   window.loadSatelliteImage?.(), window.clearLayerCache?.()
 */

// ─── Mini-map state ──────────────────────────────────────────────────────────
let _bboxMiniMapInstance = null;
let _bboxMiniRect = null;
let _bboxMiniMapInited = false;
let _bboxReloadTimeout = null;
let _bboxMiniDragging = false;

// ─── setBboxInputValues ───────────────────────────────────────────────────────

/**
 * Fill the N/S/E/W coordinate input fields with values rounded to 5 decimals.
 * @param {number} n - North latitude
 * @param {number} s - South latitude
 * @param {number} e - East longitude
 * @param {number} w - West longitude
 */
window.setBboxInputValues = function setBboxInputValues(n, s, e, w) {
    const decimals = 5;
    const bboxN = document.getElementById('bboxNorth');
    const bboxS = document.getElementById('bboxSouth');
    const bboxE = document.getElementById('bboxEast');
    const bboxW = document.getElementById('bboxWest');
    if (bboxN) bboxN.value = parseFloat(n).toFixed(decimals);
    if (bboxS) bboxS.value = parseFloat(s).toFixed(decimals);
    if (bboxE) bboxE.value = parseFloat(e).toFixed(decimals);
    if (bboxW) bboxW.value = parseFloat(w).toFixed(decimals);
};

// ─── setBboxRectangle ─────────────────────────────────────────────────────────

/**
 * Point `appState.boundingBox` at the given coordinates.
 *
 * `appState.boundingBox` is the Leaflet rectangle every layer fetch reads its
 * extent from, so anything that changes which area the app is looking at has to
 * come through here. Setting only the coordinate input fields is not enough:
 * the fields are display, this is the value.
 *
 * The rectangle is updated in place when one already exists, because the bbox
 * inputs call this on every keystroke and re-adding a map layer per character
 * is visible churn.
 *
 * @param {number} n - North latitude
 * @param {number} s - South latitude
 * @param {number} e - East longitude
 * @param {number} w - West longitude
 * @returns {object|null} The rectangle, or null when there is no map yet.
 */
window.setBboxRectangle = function setBboxRectangle(n, s, e, w) {
    const map = window.getMap?.();
    if (!map) return null;
    const bounds = [[s, w], [n, e]];
    let bb = window.getBoundingBox?.();
    if (bb?.setBounds) {
        bb.setBounds(bounds);
        // A previous caller may have detached it from the map.
        if (!map.hasLayer(bb)) bb.addTo(map);
    } else {
        bb = L.rectangle(bounds, BBOX_RECT_STYLE);
        bb.addTo(map);
    }
    window.setBoundingBox?.(bb);
    window.events?.emit(window.EV?.BBOX_CHANGED, { north: n, south: s, east: e, west: w });
    return bb;
};

// ─── initBboxMiniMap ──────────────────────────────────────────────────────────

/**
 * Initialise the inline bbox mini-map Leaflet instance.
 * Draws a draggable rectangle for the current bbox and listens for drag events.
 * Guards against double-initialisation with `_bboxMiniMapInited`.
 */
window.initBboxMiniMap = function initBboxMiniMap() {
    if (_bboxMiniMapInited) return;
    _bboxMiniMapInited = true;

    _bboxMiniMapInstance = L.map('bboxMiniMap', {
        zoomControl: true,
        attributionControl: false,
        scrollWheelZoom: true
    });

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 18, opacity: 0.85
    }).addTo(_bboxMiniMapInstance);

    // Use current bbox or fall back to world view
    const currentDemBbox = window.appState.currentDemBbox;
    const selectedRegion = window.appState.selectedRegion;
    const bbox = currentDemBbox || (selectedRegion ? {
        north: selectedRegion.north, south: selectedRegion.south,
        east: selectedRegion.east, west: selectedRegion.west
    } : null);

    if (bbox) {
        _bboxMiniRect = L.rectangle(
            [[bbox.south, bbox.west], [bbox.north, bbox.east]],
            { color: '#ff9900', weight: 2, fillOpacity: 0.15 }
        ).addTo(_bboxMiniMapInstance);
        _bboxMiniMapInstance.fitBounds(_bboxMiniRect.getBounds(), { padding: [30, 30] });
        _bboxMiniRect.editing.enable();
    } else {
        _bboxMiniMapInstance.setView([20, 0], 2);
    }

    // Live-update inputs while dragging a handle
    const container = _bboxMiniMapInstance.getContainer();
    container.addEventListener('mousedown', () => { _bboxMiniDragging = true; });
    document.addEventListener('mouseup', window._onBboxMiniMouseUp);
    container.addEventListener('mousemove', window._onBboxMiniMouseMove);
};

// ─── _onBboxMiniMouseMove ─────────────────────────────────────────────────────

/**
 * Live-update bbox coordinate inputs while the mini-map rectangle is being dragged.
 */
window._onBboxMiniMouseMove = function _onBboxMiniMouseMove() {
    if (!_bboxMiniDragging || !_bboxMiniRect) return;
    const b = _bboxMiniRect.getBounds();
    window.setBboxInputValues(b.getNorth(), b.getSouth(), b.getEast(), b.getWest());
};

// ─── _onBboxMiniMouseUp ───────────────────────────────────────────────────────

/**
 * On drag end: update bbox inputs and debounce a DEM reload (400 ms).
 */
window._onBboxMiniMouseUp = function _onBboxMiniMouseUp() {
    if (!_bboxMiniDragging) return;
    _bboxMiniDragging = false;
    if (!_bboxMiniRect) return;

    const b = _bboxMiniRect.getBounds();
    const n = parseFloat(b.getNorth().toFixed(5));
    const s = parseFloat(b.getSouth().toFixed(5));
    const e = parseFloat(b.getEast().toFixed(5));
    const w = parseFloat(b.getWest().toFixed(5));

    window.setBboxInputValues(n, s, e, w);

    // Debounced reload after drag ends
    clearTimeout(_bboxReloadTimeout);
    _bboxReloadTimeout = setTimeout(() => {
        let selectedRegion = window.appState.selectedRegion;
        if (!selectedRegion) selectedRegion = {};
        selectedRegion.north = n; selectedRegion.south = s;
        selectedRegion.east = e; selectedRegion.west = w;
        window.appState.selectedRegion = selectedRegion;
        window.setSelectedRegion?.(selectedRegion);

        const currentDemBbox = { north: n, south: s, east: e, west: w };
        window.appState.currentDemBbox = currentDemBbox;
        window.events?.emit(window.EV?.BBOX_CHANGED, currentDemBbox);

        window.clearLayerCache?.();
        window.loadDEM?.().then(() => {
            window.loadWaterMask?.();
            window.loadSatelliteImage?.();
        });
    }, 400);
};

// ─── syncBboxMiniMap ──────────────────────────────────────────────────────────

/**
 * Sync the mini-map rectangle bounds to `currentDemBbox`.
 * Called after a DEM load or region change.
 */
window.syncBboxMiniMap = function syncBboxMiniMap() {
    if (!_bboxMiniMapInited || !_bboxMiniMapInstance) return;
    const currentDemBbox = window.appState.currentDemBbox;
    const selectedRegion = window.appState.selectedRegion;
    const bbox = currentDemBbox || (selectedRegion ? {
        north: selectedRegion.north, south: selectedRegion.south,
        east: selectedRegion.east, west: selectedRegion.west
    } : null);
    if (!bbox) return;

    if (_bboxMiniRect) {
        _bboxMiniRect.editing.disable();
        _bboxMiniRect.setBounds([[bbox.south, bbox.west], [bbox.north, bbox.east]]);
        _bboxMiniRect.editing.enable();
    } else {
        _bboxMiniRect = L.rectangle(
            [[bbox.south, bbox.west], [bbox.north, bbox.east]],
            { color: '#ff9900', weight: 2, fillOpacity: 0.15 }
        ).addTo(_bboxMiniMapInstance);
        _bboxMiniRect.editing.enable();
    }
    _bboxMiniMapInstance.fitBounds(_bboxMiniRect.getBounds(), { padding: [30, 30] });
};

// ─── toggleBboxMiniMap ────────────────────────────────────────────────────────

/**
 * Toggle the inline bbox mini-map panel open or closed.
 * Initialises the Leaflet map on first open.
 */
window.toggleBboxMiniMap = function toggleBboxMiniMap() {
    const container = document.getElementById('bboxMiniMap');
    const btn = document.getElementById('editBboxOnMapBtn');
    if (!container) return;

    const opening = container.classList.contains('hidden');
    container.classList.toggle('hidden');
    if (btn) btn.classList.toggle('mini-map-open', opening);

    if (opening) {
        // Wait one frame for the div to become visible before initialising Leaflet
        requestAnimationFrame(() => {
            if (!_bboxMiniMapInited) {
                window.initBboxMiniMap();
            } else {
                window.syncBboxMiniMap();
                _bboxMiniMapInstance.invalidateSize();
            }
        });
    }
};

// ─── setupGridToggle ──────────────────────────────────────────────────────────

/**
 * Wire the `#showGridlines` checkbox and `#gridlineCount` select to redraw
 * gridline overlays on the DEM and stacked layer canvases.
 */
window.setupGridToggle = function setupGridToggle() {
    const showGridlines = document.getElementById('showGridlines');
    const gridlineCount = document.getElementById('gridlineCount');

    const redrawAllGridlines = () => {
        window.drawGridlinesOverlay?.('demImage');
        window.drawGridlinesOverlay?.('inlineLayersCanvas');
    };

    if (showGridlines) {
        showGridlines.addEventListener('change', redrawAllGridlines);
    }

    if (gridlineCount) {
        gridlineCount.addEventListener('change', redrawAllGridlines);
    }

    // Redraw gridlines on window resize (debounced)
    let _resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(_resizeTimer);
        _resizeTimer = setTimeout(() => {
            if (window.appState.currentDemBbox) redrawAllGridlines();
        }, 200);
    });
};
