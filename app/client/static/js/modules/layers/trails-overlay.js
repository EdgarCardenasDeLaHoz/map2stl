/**
 * modules/trails-overlay.js — Trails fetch, render, and clear.
 *
 * Loaded as a plain <script> before app.js. Modelled on hydrology-overlay.js.
 *
 * The server returns both categories (ski and hiking) in one response, so the
 * per-category checkboxes are a client-side repaint: the last payload is kept
 * in module scope and re-rendered rather than refetched.
 *
 * Public API (all on window):
 *   loadTrails()      — fetch ski + hiking relief grids and render
 *   renderTrails()    — repaint from the retained payload (checkbox toggles)
 *   clearTrails()     — clear canvas + state + emit update
 *   cancelTrailsLoad()— abort an in-flight request
 *
 * External dependencies:
 *   window.api                          — from modules/core/api.js
 *   window.getBoundingBox()             — L.Rectangle | null, from app.js
 *   window.appState.selectedRegion
 *   window.appState.trailsSourceCanvas  — set here; read by stacked-layers.js
 *   window.appState.lastTrailsData      — retained payload for local repaints
 *   window.showToast(msg, type)         — from app.js
 *   window.events / window.EV           — from events/events.js
 */

// ─────────────────────────────────────────────────────────────────────────────
// Module-scope state
// ─────────────────────────────────────────────────────────────────────────────

let _trailsAbortController = null;
// In-flight dedupe: when a request is already running, additional callers
// receive the same promise instead of starting a duplicate fetch.
let _trailsInflightPromise = null;
let _trailsInflightKey = null;

// Ski cyan, hiking orange — chosen to stay distinguishable over both the
// terrain colormaps and the satellite basemap. These are defaults only: the
// Trails Display view section can override either with a colour picker.
const SKI_RGB = [40, 190, 230];
const HIKING_RGB = [235, 130, 40];

// Colours for `piste:difficulty`, keyed by class index. Index 0 means the piste
// carried no usable difficulty tag, so it keeps the plain ski colour and is not
// listed here. The order matches SKI_DIFFICULTY_CLASSES in geo2stl/trails.py;
// the server sends that list as `difficulty_classes` so the legend never has to
// hardcode it, but the palette does have to line up with it by index.
//
// The hues follow the European piste convention where one exists (green, blue,
// red, black), with the ungraded off-piste categories given distinct hues of
// their own rather than reusing black, which is unreadable over dark terrain.
const DIFFICULTY_RGB = [
    null,                 // 0 — untagged, falls back to the ski colour
    [102, 211, 110],      // novice
    [61, 142, 245],       // easy
    [232, 68, 58],        // intermediate
    [32, 32, 32],         // advanced
    [139, 47, 201],       // expert
    [245, 179, 1],        // freeride
    [255, 94, 196],       // extreme
];

/** Parse a `#rrggbb` colour input into an [r, g, b] triple, or null. */
function _hexRgb(hex) {
    if (typeof hex !== 'string') return null;
    const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
    if (!m) return null;
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Read a checkbox by id, defaulting when the control is not in the DOM yet. */
function _chk(id, fallback) {
    const el = document.getElementById(id);
    return el ? el.checked : fallback;
}

// ─────────────────────────────────────────────────────────────────────────────
// Render
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Read every Trails view control. All of these are repaint-only: none of them
 * changes what was fetched, so toggling one re-renders the retained payload
 * rather than hitting the server.
 *
 * Every control is optional. The section may not be mounted yet when the first
 * payload lands, and a preset saved before a control existed will not restore
 * it, so each falls back to the value that reproduces the previous behaviour.
 */
function _viewSettings() {
    return {
        ski: _chk('trailsShowSki', true),
        hiking: _chk('trailsShowHiking', true),
        areas: _chk('trailsShowAreas', true),
        byDifficulty: _chk('trailsColorByDifficulty', false),
        skiRgb: _hexRgb(document.getElementById('trailsSkiColor')?.value) || SKI_RGB,
        hikingRgb: _hexRgb(document.getElementById('trailsHikingColor')?.value)
            || HIKING_RGB,
    };
}

/**
 * Render the retained trails payload to an offscreen canvas mirrored on
 * appState. Pixels with zero relief stay transparent; where ski and hiking
 * overlap, ski wins so pistes stay readable through a dense path network.
 *
 * Features mapped as closed ways contribute twice: their boundary is linework
 * in the relief grid, and their interior is a faint tint from the area mask, in
 * the same colour as the category. The tint is display only — the area mask is
 * never merged into the DEM, so a ski-area polygon cannot engrave the terrain.
 *
 * @param {object} [data] — response from /api/terrain/trails; defaults to the
 *     retained payload so checkbox toggles can repaint without a refetch.
 */
// Exposed so the Trails Display legend is built from the same palette the
// renderer uses, rather than a second copy that can drift out of step.
window.TRAILS_DIFFICULTY_RGB = DIFFICULTY_RGB;

window.renderTrails = function renderTrails(data) {
    const payload = data || window.appState?.lastTrailsData;
    if (!payload) return;

    const dims = payload.grid_dimensions;
    if (!dims) return;
    const [h, w] = dims;

    const show = _viewSettings();
    const skiRgb = show.skiRgb;
    const hikingRgb = show.hikingRgb;
    const ski = show.ski ? window.decodeSkiTrailValues(payload) : null;
    const hiking = show.hiking ? window.decodeHikingTrailValues(payload) : null;
    // Interiors of areal features, drawn in the category's own colour at a low
    // alpha so a ski-area boundary reads as an extent rather than a solid blob.
    const skiArea = (show.ski && show.areas) ? window.decodeSkiAreaValues(payload) : null;
    const hikingArea = (show.hiking && show.areas)
        ? window.decodeHikingAreaValues(payload) : null;
    // Piste grade per pixel. Only decoded when the user asked to colour by it,
    // and only usable when the payload came from a server new enough to send it.
    const difficulty = (show.ski && show.byDifficulty)
        ? window.decodeSkiDifficultyValues(payload) : null;

    // A separate offscreen canvas, never the DOM canvas: updateStackedLayers()
    // uses the DOM canvas as its destination buffer, and drawing a canvas onto
    // itself clears it.
    const sourceCanvas = document.createElement('canvas');
    sourceCanvas.width = w;
    sourceCanvas.height = h;

    const ctx = sourceCanvas.getContext('2d');
    const img = ctx.createImageData(w, h);
    const px = img.data;
    // Most-extreme relief maps to full opacity; the sign follows relief_m.
    const peak = Math.abs(payload.relief_m || -2.0) || 1.0;

    const AREA_ALPHA = 46;   // faint enough to read terrain through

    for (let i = 0; i < w * h; i++) {
        const s = ski ? ski[i] : 0;
        const k = hiking ? hiking[i] : 0;
        const base = i * 4;
        if (!s && !k) {
            // No linework here, so fall back to the areal tint if this pixel is
            // inside a piste or ski-area polygon.
            const sa = skiArea ? skiArea[i] : 0;
            const ka = hikingArea ? hikingArea[i] : 0;
            if (!sa && !ka) {
                px[base] = 0;
                px[base + 1] = 0;
                px[base + 2] = 0;
                px[base + 3] = 0;
                continue;
            }
            const areaRgb = sa ? skiRgb : hikingRgb;
            px[base] = areaRgb[0];
            px[base + 1] = areaRgb[1];
            px[base + 2] = areaRgb[2];
            px[base + 3] = AREA_ALPHA;
            continue;
        }
        const useSki = s !== 0;
        // An untagged piste keeps the plain ski colour, so a run with no
        // `piste:difficulty` in OSM stays visible instead of vanishing.
        let rgb = useSki ? skiRgb : hikingRgb;
        if (useSki && difficulty) {
            const cls = Math.round(difficulty[i]);
            if (cls > 0 && DIFFICULTY_RGB[cls]) rgb = DIFFICULTY_RGB[cls];
        }
        const t = Math.min(1, Math.abs(useSki ? s : k) / peak);
        px[base] = rgb[0];
        px[base + 1] = rgb[1];
        px[base + 2] = rgb[2];
        px[base + 3] = Math.round(80 + t * 160);   // 80–240 alpha
    }

    ctx.putImageData(img, 0, 0);

    if (window.appState) window.appState.trailsSourceCanvas = sourceCanvas;

    // Announced on `window` rather than through `window.events`, because the Vue
    // bundle mounts independently of main.js: a component subscribing at mount
    // time may find the bus undefined and silently never hear anything. The
    // native target always exists.
    window.dispatchEvent(new CustomEvent('trails-rendered'));
};

/**
 * Repaint from the retained payload and push the result into the stacked view.
 * Bound to every control in the Trails Display view section — all of them are
 * repaint-only, so none of them refetches.
 */
window.refreshTrailsCategories = function refreshTrailsCategories() {
    if (!window.appState?.lastTrailsData) return;
    window.renderTrails();
    window.emitStackUpdate();
};

// ─────────────────────────────────────────────────────────────────────────────
// Fetch
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Fetch trails from /api/terrain/trails and render.
 * Reads fetch parameters from the DOM: #trailsSource, #trailsDim,
 * #trailsReliefM, #trailsWidthM. Everything that only affects appearance lives
 * in the Trails Display view section and is read by _viewSettings() at render
 * time instead, so changing one of those never triggers a fetch.
 *
 * @param {{activate?: boolean}} [opts] `activate` switches the Trails layer on
 *   once the fetch succeeds. That is what the Load Trails button wants, but not
 *   what a caller that did not ask to see trails wants: `loadAllLayers` fetches
 *   every layer for a region and must leave the visible stack alone, and
 *   `LAYER_AUTOLOAD` has already turned the layer on before it calls in.
 */
window.loadTrails = async function loadTrails({ activate = true } = {}) {
    const boundingBox = window.getBoundingBox?.();
    const selectedRegion = window.appState?.selectedRegion;

    const coords = window.getBboxCoords(boundingBox, selectedRegion);
    if (!coords) {
        window.showToast?.('Select a region before loading trails.', 'warning');
        return;
    }
    const { north, south, east, west } = coords;

    const source = document.getElementById('trailsSource')?.value ?? 'all';
    const dim = parseInt(
        document.getElementById('trailsDim')?.value
        ?? document.getElementById('paramDim')?.value
        ?? '600'
    );
    const reliefM = parseFloat(document.getElementById('trailsReliefM')?.value ?? '-2.0');
    const widthM = parseFloat(document.getElementById('trailsWidthM')?.value ?? '8.0');
    const { projection, maintainDimensions, clipValidRegion } = window.getProjectionParams();
    const maintainDims = maintainDimensions ? 'true' : 'false';
    const clipNans = clipValidRegion ? 'true' : 'false';

    // Categories are deliberately absent from the request: the server always
    // computes both grids, and asking for both keeps one cache entry serving
    // every checkbox combination.
    const inflightKey = JSON.stringify({
        n: north, s: south, e: east, w: west, dim, source,
        rel: reliefM, wid: widthM,
        proj: projection, md: maintainDims, cn: clipNans,
    });
    if (_trailsInflightPromise && _trailsInflightKey === inflightKey) {
        return _trailsInflightPromise;
    }
    if (_trailsAbortController) _trailsAbortController.abort();
    _trailsAbortController = new AbortController();
    const signal = _trailsAbortController.signal;

    const paramObj = {
        north, south, east, west, dim,
        relief_m: reliefM, width_m: widthM, source,
    };
    if (projection !== 'none') {
        paramObj.projection = projection;
        paramObj.maintain_dimensions = maintainDims;
        paramObj.clip_valid_region = clipNans;
    }
    const params = new URLSearchParams(paramObj);

    const statusEl = document.getElementById('trailsStatus');
    const loadBtn = document.getElementById('loadTrailsBtn');
    if (statusEl) statusEl.textContent = 'Fetching trails…';
    if (loadBtn) {
        loadBtn.disabled = true;
        loadBtn.dataset.origText = loadBtn.textContent;
        loadBtn.textContent = '⏳ Loading…';
    }
    window.setLayerStatus?.('trails', 'loading');

    const promise = (async () => {
        try {
            const { data, error } = await window.api.dem.trails(params, signal);
            if (signal.aborted) return;
            // "No trails found" arrives as feature_count 0 plus an error string.
            // That is an empty region, not a failure — no red toast.
            const isEmptyResult = data && data.feature_count === 0 && !error;
            if (isEmptyResult) {
                if (window.appState) window.appState.lastTrailsData = null;
                if (window.appState) window.appState.trailsSourceCanvas = null;
                if (statusEl) statusEl.textContent = 'No trails found in this region';
                window.setLayerStatus?.('trails', 'loaded');
                window.emitStackUpdate();
                return;
            }
            if (error || !data || data.error) {
                const msg = (data?.error) || error || 'Unknown error';
                if (statusEl) statusEl.textContent = `⚠ ${msg}`;
                window.showToast?.(`Trails failed: ${msg}`, 'error');
                window.setLayerStatus?.('trails', 'error');
                return;
            }

            // Retain the payload so the category checkboxes repaint locally.
            if (window.appState) window.appState.lastTrailsData = data;
            window.renderTrails(data);

            const skiCount = data.ski_count ?? 0;
            const hikeCount = data.hiking_count ?? 0;
            const sources = (data.sources || []).join('+') || source;
            if (statusEl) statusEl.textContent =
                `${skiCount} ski · ${hikeCount} hiking · ${sources}`;
            window.setLayerStatus?.('trails', 'loaded');
            if (activate) {
                const trailsBtn = document.querySelector('#layerModeSelector .layer-mode-btn[data-mode="Trails"]');
                if (trailsBtn && !trailsBtn.classList.contains('active')) trailsBtn.click();
            }
            window.emitStackUpdate();
            window.showToast?.(`Trails loaded (${skiCount + hikeCount} features)`, 'success');
        } finally {
            if (loadBtn) {
                loadBtn.disabled = false;
                if (loadBtn.dataset.origText) loadBtn.textContent = loadBtn.dataset.origText;
            }
            _trailsInflightPromise = null;
            _trailsInflightKey = null;
        }
    })();

    _trailsInflightPromise = promise;
    _trailsInflightKey = inflightKey;
    return promise;
};

// ─────────────────────────────────────────────────────────────────────────────
// Clear
// ─────────────────────────────────────────────────────────────────────────────

/** Clear the trails layer canvas, retained payload, and stacked view entry. */
window.clearTrails = function clearTrails() {
    if (window.appState) {
        window.appState.trailsSourceCanvas = null;
        window.appState.lastTrailsData = null;
    }
    window.setLayerStatus?.('trails', 'empty');

    const canvas = document.getElementById('layerTrailsCanvas');
    if (canvas) {
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
    }

    const statusEl = document.getElementById('trailsStatus');
    if (statusEl) statusEl.textContent = '';

    window.emitStackUpdate();
};

/** Abort any in-flight trails request. */
window.cancelTrailsLoad = function cancelTrailsLoad() {
    if (_trailsAbortController) _trailsAbortController.abort();
};
