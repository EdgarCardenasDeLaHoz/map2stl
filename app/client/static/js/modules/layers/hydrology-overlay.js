/**
 * modules/hydrology-overlay.js — Hydrology fetch, render, and clear.
 *
 * Loaded as a plain <script> before app.js.
 *
 * Public API (all on window):
 *   loadHydrology()    — fetch river depression grid and render
 *   clearHydrology()   — clear canvas + state + emit update
 *
 * External dependencies:
 *   window.api                         — from modules/api.js
 *   window.getBoundingBox()            — L.Rectangle | null, from app.js
 *   window.appState.selectedRegion
 *   window.appState.hydrologySourceCanvas  — set here; read by stacked-layers.js
 *   window.showToast(msg, type)        — from app.js
 *   window.events / window.EV         — from events/events.js
 */

// ─────────────────────────────────────────────────────────────────────────────
// Module-scope state
// ─────────────────────────────────────────────────────────────────────────────

let _hydroAbortController = null;
let lastHydrologyData = null;

// ─────────────────────────────────────────────────────────────────────────────
// Render
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Render the river depression grid to #layerHydroCanvas as a blue-tinted overlay.
 * Negative values (river depressions) → opaque blue; zero → transparent.
 *
 * @param {object} data  — response from /api/terrain/hydrology
 */
function renderHydrology(data) {
    const canvas = document.getElementById('layerHydroCanvas');
    if (!canvas) return;

    const { river_grid_values: values, river_grid_dimensions: dims } = data;
    if (!values || !dims) return;

    const [h, w] = dims;
    canvas.width  = w;
    canvas.height = h;

    const ctx   = canvas.getContext('2d');
    const img   = ctx.createImageData(w, h);
    const px    = img.data;
    const minD  = data.depression_m ?? -5.0;   // most-negative value → full opacity

    for (let i = 0; i < values.length; i++) {
        const v = values[i];
        const base = i * 4;
        if (v === 0) {
            // Transparent — no river
            px[base]     = 0;
            px[base + 1] = 0;
            px[base + 2] = 0;
            px[base + 3] = 0;
        } else {
            // Blue tint; opacity scales with depth
            const t   = Math.min(1, Math.max(0, v / minD));   // 0 (shallow) → 1 (deep)
            px[base]     = 30;
            px[base + 1] = 100;
            px[base + 2] = 200;
            px[base + 3] = Math.round(60 + t * 160);           // 60–220 alpha
        }
    }

    ctx.putImageData(img, 0, 0);

    // Mirror to appState so updateStackedLayers() can composite it
    if (window.appState) window.appState.hydrologySourceCanvas = canvas;
}

// ─────────────────────────────────────────────────────────────────────────────
// Fetch
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Fetch river hydrology from /api/terrain/hydrology and render.
 * Reads control values from DOM: #hydroSource, #hydroDim, #hydroDepressionM,
 * #hydroMinOrder, #hydroOrderExponent.
 */
window.loadHydrology = async function loadHydrology() {
    if (_hydroAbortController) _hydroAbortController.abort();
    _hydroAbortController = new AbortController();
    const signal = _hydroAbortController.signal;

    const boundingBox    = window.getBoundingBox?.();
    const selectedRegion = window.appState?.selectedRegion;

    const coords = window.getBboxCoords(boundingBox, selectedRegion);
    if (!coords) {
        window.showToast?.('Select a region before loading hydrology.', 'warning');
        return;
    }
    const { north, south, east, west } = coords;

    const source      = document.getElementById('hydroSource')?.value      ?? 'hydrorivers';
    const dim         = parseInt(document.getElementById('hydroDim')?.value ?? '300');
    const depressionM = parseFloat(document.getElementById('hydroDepressionM')?.value ?? '-5.0');
    const minOrder    = parseInt(document.getElementById('hydroMinOrder')?.value     ?? '3');
    const orderExp    = parseFloat(document.getElementById('hydroOrderExponent')?.value ?? '1.5');

    const paramObj = { north, south, east, west, dim, depression_m: depressionM, source };
    if (source === 'hydrorivers') {
        paramObj.min_order      = minOrder;
        paramObj.order_exponent = orderExp;
    }
    const params = new URLSearchParams(paramObj);

    const statusEl = document.getElementById('hydroStatus');
    if (statusEl) statusEl.textContent = source === 'hydrorivers'
        ? 'Fetching HydroRIVERS… (first call downloads ~100 MB)'
        : 'Fetching Natural Earth rivers…';

    const { data, error } = await window.api.dem.hydrology(params, signal);

    if (signal.aborted) return;

    if (error || !data || data.error) {
        const msg = (data?.error) || error || 'Unknown error';
        if (statusEl) statusEl.textContent = `⚠ ${msg}`;
        window.showToast?.(`Hydrology failed: ${msg}`, 'error');
        lastHydrologyData = null;
        return;
    }

    lastHydrologyData = data;
    renderHydrology(data);

    const fc = data.feature_count ?? 0;
    if (statusEl) statusEl.textContent =
        `${fc} river feature${fc !== 1 ? 's' : ''} · ${source === 'hydrorivers' ? 'HydroRIVERS' : 'Natural Earth'}`;

    window.emitStackUpdate();
    window.showToast?.(`Hydrology loaded (${fc} features)`, 'success');
};

// ─────────────────────────────────────────────────────────────────────────────
// Clear
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Clear the hydrology layer canvas and remove it from the stacked view.
 */
window.clearHydrology = function clearHydrology() {
    lastHydrologyData = null;
    if (window.appState) window.appState.hydrologySourceCanvas = null;

    const canvas = document.getElementById('layerHydroCanvas');
    if (canvas) {
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
    }

    const statusEl = document.getElementById('hydroStatus');
    if (statusEl) statusEl.textContent = '';

    window.emitStackUpdate();
};
