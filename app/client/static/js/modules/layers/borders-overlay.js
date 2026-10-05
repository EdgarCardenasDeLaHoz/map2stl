/**
 * modules/layers/borders-overlay.js — country and state/province borders on the Edit map.
 *
 * Imported by main.js. View only: nothing here reaches the 3D model.
 *
 * GET /api/terrain/borders returns a grid over the loaded terrain's box (0 none, 1 state or
 * province line, 2 country border; geo2stl/borders.py, Natural Earth 10 m). The grid is kept,
 * so the switches and colours (#bordersShowCountries, #bordersShowStates,
 * #bordersCountryColor, #bordersStateColor) repaint without a new request.
 *
 * Public API (on window):
 *   loadBorders({activate})  — fetch for the loaded terrain and paint
 *   renderBorders()          — repaint from the kept grid
 *   clearBorders()           — drop the grid and the canvas
 *
 * Reads: window.appState.currentDemBbox, lastDemData (size), getProjectionParams().
 * Writes: window.appState.bordersSourceCanvas (stacked-layers.js draws it, key 'Borders').
 */

let _abort = null;
let _last = null;     // { values, h, w, key }
let _inflight = null; // { key, promise }

function _hexToRgb(hex, fallback) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
    if (!m) return fallback;
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Repaint the borders canvas from the kept grid with the current switches and colours. */
window.renderBorders = function renderBorders() {
    if (!_last) return null;
    const { values, h, w } = _last;
    const showCountries = document.getElementById('bordersShowCountries')?.checked ?? true;
    const showStates = document.getElementById('bordersShowStates')?.checked ?? true;
    const country = _hexToRgb(document.getElementById('bordersCountryColor')?.value, [255, 69, 58]);
    const state = _hexToRgb(document.getElementById('bordersStateColor')?.value, [255, 214, 10]);
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    const img = ctx.createImageData(w, h);
    const px = img.data;
    for (let i = 0; i < values.length; i++) {
        const v = values[i];
        let c = null;
        if (v >= 1.5) { if (showCountries) c = country; } else if (v >= 0.5 && showStates) c = state;
        if (!c) continue;
        const o = i * 4;
        px[o] = c[0]; px[o + 1] = c[1]; px[o + 2] = c[2]; px[o + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    window.appState.bordersSourceCanvas = canvas;
    window.emitStackUpdate?.();
    return canvas;
};

/**
 * Fetch the borders for the loaded terrain (same box, size and projection) and paint them.
 * @param {{activate?: boolean}} [opts] activate: also switch the layer on in the stack
 */
window.loadBorders = async function loadBorders({ activate = true } = {}) {
    const bbox = window.appState?.currentDemBbox;
    const dem = window.appState?.lastDemData;
    if (!bbox || !dem) {
        window.setLayerStatus?.('borders', 'empty');
        return null;
    }
    const dim = Math.max(dem.width || 0, dem.height || 0) || 600;
    const { projection, maintainDimensions, clipValidRegion } = window.getProjectionParams();
    const p = { north: bbox.north, south: bbox.south, east: bbox.east, west: bbox.west, dim };
    if (projection !== 'none') {
        p.projection = projection;
        p.maintain_dimensions = maintainDimensions ? 'true' : 'false';
        p.clip_valid_region = clipValidRegion ? 'true' : 'false';
    }
    const params = new URLSearchParams(p);
    const key = params.toString();
    if (_last?.key === key) {
        window.renderBorders();
        if (activate && !window.getActiveLayers?.().has('Borders')) window.setStackMode?.('Borders');
        return _last;
    }
    if (_inflight?.key === key) return _inflight.promise;
    _abort?.abort();
    _abort = new AbortController();
    const signal = _abort.signal;
    window.setLayerStatus?.('borders', 'loading');
    const promise = (async () => {
        const { data, error } = await window.api.dem.borders(params, signal);
        if (signal.aborted) return null;
        if (error || !data || data.error) {
            window.setLayerStatus?.('borders', 'error');
            window.showToast?.(`Borders: ${data?.error || error || 'unavailable'}`, 'warning');
            return null;
        }
        const [h, w] = data.grid_dimensions;
        _last = { values: window.decodeBordersGrid(data), h, w, key, counts: data.counts || {} };
        window.appState.lastBordersData = { counts: _last.counts, h, w };
        window.renderBorders();
        window.setLayerStatus?.('borders', 'loaded');
        if (activate && !window.getActiveLayers?.().has('Borders')) window.setStackMode?.('Borders');
        return _last;
    })();
    _inflight = { key, promise };
    promise.finally(() => { if (_inflight?.promise === promise) _inflight = null; });
    return promise;
};

/** Drop the kept grid and the canvas (a new region). */
window.clearBorders = function clearBorders() {
    _abort?.abort();
    _last = null;
    if (window.appState) {
        window.appState.bordersSourceCanvas = null;
        window.appState.lastBordersData = null;
    }
    window.setLayerStatus?.('borders', 'empty');
    window.emitStackUpdate?.();
};

document.addEventListener('change', (e) => {
    const id = e.target?.id || '';
    if (id.startsWith('borders') && _last) window.renderBorders();
});
document.addEventListener('input', (e) => {
    const id = e.target?.id || '';
    if ((id === 'bordersCountryColor' || id === 'bordersStateColor') && _last) window.renderBorders();
});

// A new terrain (box, detail or projection) re-fetches the borders when they are shown.
window.addEventListener('load', () => {
    window.events?.on?.(window.EV?.DEM_LOADED, () => {
        if (window.getActiveLayers?.().has('Borders')) window.loadBorders({ activate: false });
    });
});
