/**
 * modules/ui-helpers.js — Toast notifications, layer-status UI,
 * collapsible sections, and coordinate search.
 *
 * Imported by main.js. All functions exposed on window.*.
 * Reads layer status via window.appState.layerStatus (shared reference with app.js).
 *
 * Public API:
 *   window.escapeHtml(value)
 *   window.showToast(message, type, duration)
 *   window.toastAnimation(duration)
 *   window.toastDropIndex(types, max)
 *   window.toggleCollapsible(header)
 *   window.setLayerStatus(layer, status)
 *   window.updateLayerStatusUI()
 *   window.updateLayerStatusIndicators()
 *   window.setupCoordinateSearch()
 */

// ============================================================
// HTML ESCAPING
// ============================================================

const _HTML_ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

/**
 * Escape a value for safe interpolation into innerHTML (text or quoted attribute).
 * null/undefined become ''. Non-strings are stringified first.
 * @param {*} value
 * @returns {string}
 */
window.escapeHtml = function escapeHtml(value) {
    if (value == null) return '';
    return String(value).replace(/[&<>"']/g, (c) => _HTML_ESCAPES[c]);
};

// ============================================================
// TOAST NOTIFICATIONS
// ============================================================

/**
 * CSS `animation` value for a toast shown for `duration` ms: slide in, stay,
 * then fade out so the fade ends exactly at `duration` (when the element is
 * removed). The fade timing lives here, not in app.css, so a 6–8 s toast
 * stays readable for its whole requested duration.
 * @param {number} duration - Total on-screen time in milliseconds
 * @returns {string}
 */
window.toastAnimation = function toastAnimation(duration) {
    const total = Number.isFinite(duration) && duration > 0 ? duration : 3000;
    const fadeMs = Math.min(300, total / 2);
    const delayMs = Math.max(0, total - fadeMs);
    return `slideIn 0.3s ease, fadeOut ${fadeMs / 1000}s ease ${delayMs / 1000}s forwards`;
};

/** Most toasts on screen at once; older ones are dropped (see toastDropIndex). */
window.TOAST_MAX_VISIBLE = 3;

/**
 * Which toast to drop when more than `max` are shown: the oldest non-error one,
 * or the oldest error when all are errors. Returns -1 when nothing needs dropping.
 * Pure (takes the types oldest-first) so it is unit-tested without a DOM.
 * @param {string[]} types - Toast types, oldest first
 * @param {number} [max=window.TOAST_MAX_VISIBLE]
 * @returns {number}
 */
window.toastDropIndex = function toastDropIndex(types, max = window.TOAST_MAX_VISIBLE) {
    if (types.length <= max) return -1;
    const i = types.findIndex((t) => t !== 'error');
    return i === -1 ? 0 : i;
};

/**
 * Show a toast in the stack at the bottom centre of the main view (#toastContainer).
 *
 * - The message is plain text (set with textContent, never parsed as HTML);
 *   "
" becomes a line break. There is no allow-HTML option: no caller needs one.
 * - Errors persist until the user closes them (✕) and are announced with
 *   role="alert"; other types auto-hide after `duration` and use role="status".
 * - At most TOAST_MAX_VISIBLE are shown; the oldest non-error toast is dropped first.
 * @param {string} message - Message text to display
 * @param {'success'|'error'|'warning'|'info'} [type='info'] - Visual style
 * @param {number} [duration=3000] - Auto-dismiss delay in ms (ignored for errors)
 * @returns {HTMLElement|undefined} the toast element
 */
window.showToast = function showToast(message, type = 'info', duration = 3000) {
    const container = document.getElementById('toastContainer');
    if (!container) return undefined;

    const icons = { success: '✓', error: '✕', warning: '⚠', info: 'ℹ' };
    const kind = icons[type] ? type : 'info';
    const persist = kind === 'error';

    const toast = document.createElement('div');
    toast.className = `toast ${kind}`;
    toast.dataset.type = kind;
    toast.setAttribute('role', persist ? 'alert' : 'status');

    const icon = document.createElement('span');
    icon.className = 'toast-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = icons[kind];

    const msg = document.createElement('span');
    msg.className = 'toast-message';
    msg.textContent = message == null ? '' : String(message);

    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'toast-close';
    close.setAttribute('aria-label', 'Dismiss notification');
    close.title = 'Dismiss';
    close.textContent = '✕';
    close.addEventListener('click', () => toast.remove());

    toast.append(icon, msg, close);
    toast.style.animation = persist ? 'slideIn 0.3s ease' : window.toastAnimation(duration);
    container.appendChild(toast);

    const shown = Array.from(container.children);
    const drop = window.toastDropIndex(shown.map((el) => el.dataset.type || 'info'));
    if (drop >= 0) shown[drop].remove();

    if (!persist) setTimeout(() => toast.remove(), duration);
    return toast;
};

// ============================================================
// COLLAPSIBLE SECTIONS
// ============================================================

/**
 * Toggle a collapsible section open or closed.
 * Also reinitialises the curve canvas if it was hidden.
 * @param {HTMLElement} header - The collapsible section header element
 */
window.toggleCollapsible = function toggleCollapsible(header) {
    const section = header.closest('.collapsible-section');
    if (section) {
        const wasCollapsed = section.classList.contains('collapsed');
        section.classList.toggle('collapsed');

        // If section is being expanded, reinitialise any canvases inside it
        if (wasCollapsed) {
            setTimeout(() => {
                // Curve editor canvas
                const cc = section.querySelector('#curveCanvas');
                if (cc) {
                    const container = cc.parentElement;
                    if (container.clientWidth > 0 && container.clientHeight > 0) {
                        cc.width = container.clientWidth;
                        cc.height = container.clientHeight;
                        window.drawCurve?.();
                    }
                }
                // Histogram + colorbar — redraw at current panel width
                if (section.querySelector('#histogram') && window.appState.lastDemData?.values?.length) {
                    window.recolorDEM?.();
                }
            }, 50);
        }
    }
};

// ============================================================
// LAYER STATUS
// ============================================================

/**
 * Update one or more layer statuses and refresh the status UI.
 * Accepts a single layer name or an array for atomic multi-layer updates.
 * @param {string|string[]} layer - Layer name(s): 'dem', 'water', 'landCover'
 * @param {'empty'|'loading'|'loaded'|'error'} status - New status value
 */
window.setLayerStatus = function setLayerStatus(layer, status) {
    if (window.appState?.layerStatus) {
        const layers = Array.isArray(layer) ? layer : [layer];
        layers.forEach(l => { window.appState.layerStatus[l] = status; });
    }
    window.events?.emit(window.EV?.STATUS_UPDATE);
};

/**
 * Sync all layer status badge elements in the DOM from window.appState.layerStatus.
 */
window.updateLayerStatusUI = function updateLayerStatusUI() {
    const layerStatus = window.appState?.layerStatus || {};

    // Update strip button status dots
    const stripDotMap = { 'dem': 'stripDotDem', 'water': 'stripDotWater', 'landCover': 'stripDotLandCover' };
    Object.entries(stripDotMap).forEach(([layer, dotId]) => {
        const dot = document.getElementById(dotId);
        if (dot) {
            dot.classList.remove('loaded', 'loading', 'error');
            const s = layerStatus[layer] || 'empty';
            if (s !== 'empty') dot.classList.add(s);
        }
    });
};

/**
 * Update layer status indicator UI — updates both the new tab-status indicators
 * and the legacy badge system.
 */
window.updateLayerStatusIndicators = function updateLayerStatusIndicators() {
    window.updateLayerStatusUI();

    const layerStatus = window.appState?.layerStatus || {};
    const statusIcons = {
        'empty': '○',
        'loading': '◐',
        'loaded': '●',
        'error': '⚠️',
        'stale': '◔'
    };

    document.querySelectorAll('.layer-tab').forEach(tab => {
        const subtab = tab.dataset.subtab;
        let layerName = subtab;
        if (subtab === 'satellite') layerName = 'landCover';
        if (subtab === 'combined') return;

        const status = layerStatus[layerName] || 'empty';
        let badge = tab.querySelector('.layer-badge');

        if (!badge) {
            badge = document.createElement('span');
            badge.className = 'layer-badge';
            tab.appendChild(badge);
        }

        badge.textContent = statusIcons[status];
        badge.title = status;
    });
};

// ============================================================
// COORDINATE SEARCH
// ============================================================

/**
 * Wire the coordinate search input to filter the region list by name.
 * Guards against double-wiring with a _searchWired flag.
 */
window.setupCoordinateSearch = function setupCoordinateSearch() {
    const searchInput = document.getElementById('coordSearch');
    if (!searchInput || searchInput._searchWired) return;
    searchInput._searchWired = true;

    // Trigger a full re-render so the pagination in region-ui.js resets and
    // filters correctly (simple show/hide would break page boundaries).
    searchInput.addEventListener('input', function () {
        window.renderCoordinatesList?.();
    });
};

// ============================================================
// SHARED FETCH HELPERS
// ============================================================

/**
 * Extract { north, south, east, west } from a Leaflet bounding box or a
 * selectedRegion object. Returns null when neither is available.
 * Used by all layer loaders to avoid repeating this 15-line block.
 * @param {L.Rectangle|L.LatLngBounds|null} boundingBox
 * @param {object|null} selectedRegion
 * @returns {{ north, south, east, west }|null}
 */
window.getBboxCoords = function getBboxCoords(boundingBox, selectedRegion) {
    if (boundingBox) {
        const b = typeof boundingBox.getBounds === 'function'
            ? boundingBox.getBounds()
            : boundingBox;
        return { north: b.getNorth(), south: b.getSouth(), east: b.getEast(), west: b.getWest() };
    }
    if (selectedRegion) {
        return {
            north: selectedRegion.north, south: selectedRegion.south,
            east: selectedRegion.east, west: selectedRegion.west
        };
    }
    return null;
};

/**
 * Read the current projection + maintain_dimensions UI state, shared by
 * every layer fetch (DEM, water, ESA, satellite, hydrology, city, composite)
 * so they all request the same projection settings and stay aligned
 * (F-PROJ-DIMS). maintain_dimensions defaults to false — output reflects
 * each projection's true geographic aspect ratio unless the user opts into
 * the legacy fixed-canvas-shape behavior via #paramMaintainDimensions.
 * @returns {{projection: string, maintainDimensions: boolean, clipValidRegion: boolean}}
 */
window.getProjectionParams = function getProjectionParams() {
    return {
        projection: document.getElementById('paramProjection')?.value || 'none',
        maintainDimensions: document.getElementById('paramMaintainDimensions')?.checked ?? false,
        clipValidRegion: document.getElementById('paramClipNans')?.checked ?? true,
    };
};

/**
 * Replace the contents of a container element with a plain error paragraph.
 * @param {string} containerId - ID of the container element
 * @param {string} msg         - Error message (will be prefixed with "Error: ")
 */
window.showErrInEl = function showErrInEl(containerId, msg) {
    const p = document.createElement('p');
    p.textContent = `Error: ${msg}`;
    document.getElementById(containerId)?.replaceChildren(p);
};

// ============================================================
// BINARY TRANSPORT HELPERS
// ============================================================

/**
 * Decode a DEM (or any float32 grid) from a server response object.
 * Handles both the legacy `dem_values` plain array and the newer
 * `dem_values_b64` base64-encoded float32 binary format.
 *
 * Returns a Float32Array (b64 path) or the original array (legacy path).
 * @param {Object} data - Server response containing dem_values or dem_values_b64
 * @returns {Float32Array|Array}
 */
/**
 * Decode a base64 float32 grid, falling back to a plain array.
 * @param {string|undefined} b64  - base64-encoded little-endian float32 bytes
 * @param {Array|undefined}  arr  - fallback plain JS array
 * @returns {Float32Array|Array}
 */
function _decodeGrid(b64, arr) {
    if (b64) {
        const bin = atob(b64);
        const buf = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
        return new Float32Array(buf.buffer);
    }
    return arr;
}

window.decodeDemValues = function decodeDemValues(data) {
    return _decodeGrid(data.dem_values_b64, data.dem_values);
};

window.decodeWaterMask = function decodeWaterMask(data) {
    return _decodeGrid(data.water_mask_values_b64, data.water_mask_values);
};

window.decodeEsaValues = function decodeEsaValues(data) {
    return _decodeGrid(data.esa_values_b64, data.esa_values);
};

window.decodeHydrologyValues = function decodeHydrologyValues(data) {
    return _decodeGrid(data.river_grid_values_b64, data.river_grid_values);
};

/** Strahler order per pixel of a HydroRIVERS response (0 none, order_water_code = open water). */
window.decodeHydrologyOrders = function decodeHydrologyOrders(data) {
    return data?.order_grid_b64 ? _decodeGrid(data.order_grid_b64, null) : null;
};

window.decodeSkiTrailValues = function decodeSkiTrailValues(data) {
    return _decodeGrid(data.ski_grid_values_b64, data.ski_grid_values);
};

window.decodeHikingTrailValues = function decodeHikingTrailValues(data) {
    return _decodeGrid(data.hiking_grid_values_b64, data.hiking_grid_values);
};

// Interiors of trail features mapped as closed ways — ski-area and piste
// polygons. Display only: they tint the overlay and never reach the DEM.
window.decodeSkiAreaValues = function decodeSkiAreaValues(data) {
    return _decodeGrid(data.ski_area_grid_values_b64, data.ski_area_grid_values);
};

window.decodeHikingAreaValues = function decodeHikingAreaValues(data) {
    return _decodeGrid(data.hiking_area_grid_values_b64, data.hiking_area_grid_values);
};

// Piste grade per pixel, as an index into the response's `difficulty_classes`
// (0 = no usable `piste:difficulty` tag). Sent as float32 like every other grid
// so it can share this decoder; the values are small integers, so nothing is
// lost, but a reader must treat them as classes and never interpolate them.
window.decodeSkiDifficultyValues = function decodeSkiDifficultyValues(data) {
    return _decodeGrid(data.ski_difficulty_grid_values_b64,
                       data.ski_difficulty_grid_values);
};

window.decodeMeshValues = function decodeMeshValues(data) {
    return _decodeGrid(data.mesh_values_b64, data.mesh_values);
};

/**
 * Decode a base64 packed-bool mask (1 byte/px, 1=valid, 0=invalid).
 * Distinct from _decodeGrid because mask bytes are not float32 samples.
 * @param {string} b64
 * @returns {Uint8Array}
 */
window.decodeMeshMask = function decodeMeshMask(b64) {
    const bin = atob(b64);
    const buf = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
    return buf;
};

/**
 * Schedule a STACKED_UPDATE event on the next animation frame.
 * Centralises the repeated `requestAnimationFrame(() => window.events?.emit(...))` pattern.
 */
window.emitStackUpdate = function emitStackUpdate() {
    requestAnimationFrame(() => window.events?.emit(window.EV?.STACKED_UPDATE));
};

// ============================================================
// SHARED CONSTANTS
// ============================================================

/** Metres per degree of longitude at the equator. */
window.GEO_M_PER_DEG_LON = 111320;
/** Metres per degree of latitude (roughly constant). */
window.GEO_M_PER_DEG_LAT = 110540;

/**
 * CSS selector for the primary DEM canvas inside #demImage.
 * Excludes overlay canvases (gridlines, city, water, sat).
 */
window.DEM_CANVAS_SELECTOR = 'canvas:not(.dem-gridlines-overlay):not(.city-dem-overlay):not(.water-dem-overlay):not(.sat-dem-overlay)';

// ============================================================
// COLOR LUT BUILDER
// ============================================================

/**
 * Build a colour lookup table for a given colormap.
 * Returns a Uint8Array of length size*3 (RGB triplets).
 * @param {string} colormap - Colormap name (e.g. 'terrain', 'viridis')
 * @param {number} [size=1024] - Number of LUT entries
 * @returns {Uint8Array}
 */
/**
 * Resolve the effective colormap for a layer.
 *
 * Each colormap-using layer may have its own `<select id="{layerKey}Colormap">`.
 * A value of '' or 'inherit' means "use the DEM colormap" (#demColormap), which
 * keeps the previous single-colormap behaviour as the default. Pass no layerKey
 * to just read the DEM colormap.
 *
 * @param {string} [layerKey] e.g. 'city', 'composite'
 * @returns {string} colormap name (e.g. 'terrain')
 */
window.getLayerColormap = function getLayerColormap(layerKey) {
    const demCm = document.getElementById('demColormap')?.value || 'terrain';
    if (!layerKey) return demCm;
    const own = document.getElementById(`${layerKey}Colormap`)?.value;
    if (!own || own === 'inherit') return demCm;
    return own;
};

window.buildColorLUT = function buildColorLUT(colormap, size = 1024) {
    const lut = new Uint8Array(size * 3);
    const maxIdx = size - 1;
    for (let i = 0; i < size; i++) {
        const t = i / maxIdx;
        const [r, g, b] = window.mapElevationToColor?.(t, colormap) || [0, 0, 0];
        lut[i * 3] = Math.round((r || 0) * 255);
        lut[i * 3 + 1] = Math.round((g || 0) * 255);
        lut[i * 3 + 2] = Math.round((b || 0) * 255);
    }
    return lut;
};

// Listen for STATUS_UPDATE events (replaces scattered direct calls)
window.events?.on(window.EV?.STATUS_UPDATE, () => window.updateLayerStatusIndicators());
