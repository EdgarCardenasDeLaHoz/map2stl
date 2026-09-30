// Keyboard accessibility for bbox inputs: arrow keys nudge by 0.1°
// === Constants ===
const BBOX_KEYBOARD_NUDGE_STEP = 0.1;
// Shared by every caller of setBboxRectangle, so the box looks the same however
// the user got to it.
const BBOX_RECT_STYLE = { color: '#e74c3c', weight: 2, fillOpacity: 0.05 };

['bboxNorth', 'bboxSouth', 'bboxEast', 'bboxWest'].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
        el.addEventListener('keydown', (e) => {
            let step = BBOX_KEYBOARD_NUDGE_STEP;
            if (e.key === 'ArrowUp' || e.key === 'ArrowRight') {
                el.value = (parseFloat(el.value) + step).toFixed(2);
                el.dispatchEvent(new Event('change'));
                e.preventDefault();
            } else if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') {
                el.value = (parseFloat(el.value) - step).toFixed(2);
                el.dispatchEvent(new Event('change'));
                e.preventDefault();
            }
        });
    }
});
/**
 * modules/map/bbox-panel.js
 *
 * Exposed on window:
 *   setBboxInputValues, setBboxRectangle, initBboxMiniMap, syncBboxMiniMap,
 *   toggleBboxMiniMap, setupGridToggle, setupBboxKeyboardNav
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

// ─── setupBboxKeyboardNav ────────────────────────────────────────────────────

/**
 * Wire keyboard navigation to N/S/E/W bbox coordinate inputs.
 * Arrow keys (±) adjust by 0.01 degrees (~1 km at equator).
 * Enter confirms the change and reloads DEM.
 * Escape cancels (reverts to previous value).
 */
window.setupBboxKeyboardNav = function setupBboxKeyboardNav() {
    const bboxInputs = [
        { el: document.getElementById('bboxNorth'), key: 'north', isLat: true },
        { el: document.getElementById('bboxSouth'), key: 'south', isLat: true },
        { el: document.getElementById('bboxEast'), key: 'east', isLat: false },
        { el: document.getElementById('bboxWest'), key: 'west', isLat: false }
    ];

    bboxInputs.forEach(({ el }) => {
        if (!el) return;

        el.addEventListener('keydown', (e) => {
            const step = 0.01;  // ~1 km at equator
            let newValue = parseFloat(el.value);

            if (e.key === 'ArrowUp' || e.key === 'ArrowRight') {
                e.preventDefault();
                el.value = (newValue + step).toFixed(5);
            } else if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') {
                e.preventDefault();
                el.value = (newValue - step).toFixed(5);
            } else if (e.key === 'Enter') {
                e.preventDefault();
                const n = parseFloat(document.getElementById('bboxNorth')?.value || 0);
                const s = parseFloat(document.getElementById('bboxSouth')?.value || 0);
                const e_val = parseFloat(document.getElementById('bboxEast')?.value || 0);
                const w = parseFloat(document.getElementById('bboxWest')?.value || 0);

                let selectedRegion = window.appState.selectedRegion;
                if (!selectedRegion) selectedRegion = {};
                selectedRegion.north = n; selectedRegion.south = s;
                selectedRegion.east = e_val; selectedRegion.west = w;
                window.appState.selectedRegion = selectedRegion;
                window.setSelectedRegion?.(selectedRegion);

                const currentDemBbox = { north: n, south: s, east: e_val, west: w };
                window.appState.currentDemBbox = currentDemBbox;

                window.clearLayerCache?.();
                window.loadDEM?.().then(() => {
                    window.loadWaterMask?.();
                    window.loadSatelliteImage?.();
                });

                el.blur();
                window.showToast?.('Bbox updated', 'info');
            } else if (e.key === 'Escape') {
                e.preventDefault();
                el.blur();
            }
        });
    });
};
