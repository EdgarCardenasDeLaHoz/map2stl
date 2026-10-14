/**
 * modules/dem/projection-preview.js — the Edit map's instant projection preview.
 *
 * Picking a projection re-fetches every layer from the server (~15 s for a continent). Until
 * they land, the layers already in the browser are re-drawn in the new projection here: each
 * target pixel is mapped back to latitude/longitude (graticule.js geoAtFrac, the new
 * projection) and forward into the loaded picture (projectedFrac, the old one), nearest
 * sample. The output has the server's predicted shape (projectedShape), so the frame and the
 * grids take their final size at once and nothing jumps when the real data lands.
 *
 * Each layer's source canvas is warped on its own, not the composed stack: the stack then
 * composes the warped canvases exactly as it composes the real ones (order, per-layer
 * opacity, split view, the buildings in between), and each layer swaps to its real picture
 * the moment it lands. Warping the composed picture would have baked one layer order and
 * opacity into the preview and could not hand over layer by layer.
 *
 * The preview state is module-local, not on window.appState: it holds pixel buffers, and the
 * appState bridge onto the Pinia store would wrap them in reactive proxies.
 *
 * Hand-over: the DEM swaps to its real picture once the render worker has painted it (it is
 * blank for a few hundred ms after loadDEM resolves); every other layer once a new canvas of its
 * own appears. The reload releasing the preview does not end it: a layer that reloads on its own
 * after the DEM (Rivers & lakes, Borders, Trails) keeps its preview until it lands, or HOLD_MS.
 * The preview ends, and its pill (#projectionPreviewStatus) hides, when no layer is held.
 * (Ending it at release left Rivers & lakes with no source while its fetch ran, and the stack
 * kept that layer's last preview buffer at the old pixel density: a misplaced blue band.)
 *
 * warpPixels, layerHasLanded and previewCanEnd are pure (tests/js/projectionPreview.test.js);
 * the rest touches canvases.
 */
import { geoAtFrac, projectedFrac, projectedShape } from './graticule.js';

/**
 * Re-project an RGBA pixel buffer from one projection to another, nearest sample.
 * Pixels the target projection leaves outside the box stay transparent.
 * @param {{data:Uint8ClampedArray, width:number, height:number}} src  e.g. an ImageData
 * @param {{north:number, south:number, east:number, west:number}} bbox
 * @param {{projection:string, maintainDimensions?:boolean}} from  the projection src is drawn in
 * @param {{projection:string, maintainDimensions?:boolean}} to
 * @param {number} [dim]  longest side of the output (default: src's longest side)
 * @returns {{data:Uint8ClampedArray, width:number, height:number}}
 */
export function warpPixels(src, bbox, from, to, dim) {
    const sw = src.width, sh = src.height;
    const { width: W, height: H } = projectedShape(bbox, to.projection, dim || Math.max(sw, sh),
        !!to.maintainDimensions);
    const out = new Uint8ClampedArray(W * H * 4);
    if (!sw || !sh) return { data: out, width: W, height: H };
    const s8 = src.data.byteOffset % 4 ? new Uint8ClampedArray(src.data) : src.data;
    const s32 = new Uint32Array(s8.buffer, s8.byteOffset, sw * sh);
    const o32 = new Uint32Array(out.buffer);
    const inv = geoAtFrac(bbox, to.projection, !!to.maintainDimensions);
    const fwd = projectedFrac(bbox, from.projection, !!from.maintainDimensions);
    const { west, east } = bbox;
    const tol = Math.abs(east - west) * 1e-9;
    // Along one row every supported projection is affine: latitude is fixed by the row, the
    // inverse gives lon = a + b*x and the forward gives x = c + d*lon. So two samples per row
    // give the whole row, and the per-pixel loop is arithmetic only (Mercator's atan/sinh and
    // log/tan per pixel made a 600 px layer ~3x slower).
    for (let j = 0; j < H; j++) {
        const fy = (j + 0.5) / H;
        const g0 = inv(0, fy), g1 = inv(1, fy);
        const s0 = fwd(g0.lat, g0.lon), s1 = fwd(g0.lat, g1.lon);
        if (!(s0.y >= 0 && s0.y <= 1)) continue;   // also skips NaN
        const srcRow = Math.min(sh - 1, (s0.y * sh) | 0) * sw;
        const dLon = g1.lon - g0.lon, dX = s1.x - s0.x;
        const row = j * W;
        for (let i = 0; i < W; i++) {
            const fx = (i + 0.5) / W;
            const lon = g0.lon + dLon * fx;
            if (!(lon >= west - tol && lon <= east + tol)) continue;
            const x = s0.x + dX * fx;
            if (!(x >= 0 && x <= 1)) continue;
            o32[row + i] = s32[srcRow + Math.min(sw - 1, (x * sw) | 0)];
        }
    }
    return { data: out, width: W, height: H };
}

// ── Preview state ────────────────────────────────────────────────────────────

/**
 * @typedef {object} PreviewState
 * @property {object} bbox
 * @property {{projection:string, maintainDimensions:boolean}} from  projection of the snapshots
 * @property {{projection:string, maintainDimensions:boolean}} to    projection shown
 * @property {{width:number, height:number}} shape  predicted DEM raster shape in `to`
 * @property {Map<string, ImageData>} base     loaded pixels per layer mode, in `from`
 * @property {Map<string, HTMLCanvasElement>} warped  base warped to `to`
 * @property {Map<string, HTMLCanvasElement|null>} seen  the real source canvas when last
 *   checked; a different one appearing after the DEM landed is that layer's new data
 * @property {boolean} demLanded  the DEM for `to` has arrived
 * @property {boolean} released   the reload is done; layers still loading keep their preview
 * @property {number} deadline     performance.now() after which a released preview ends anyway
 */
/** @type {PreviewState|null} */
let _state = null;

/** How long a released preview may hold a layer that never lands (a failed fetch). */
const HOLD_MS = 30000;
/** Layers nothing re-fetches after a projection change: never wait for them. */
const NOT_RELOADED = new Set(['MeshImport', 'CompositeDem']);
let _holdTimer = null;

function _setPill(on) {
    document.getElementById('projectionPreviewStatus')?.classList.toggle('hidden', !on);
}

/**
 * Has a layer's real data for the newest pick arrived?
 * @param {string} mode
 * @param {HTMLCanvasElement|null} real   the layer's real source canvas now
 * @param {HTMLCanvasElement|null} seen   its real canvas when the DEM landed
 * @param {boolean} demLanded
 * @returns {boolean}
 */
export function layerHasLanded(mode, real, seen, demLanded) {
    if (!demLanded || !real) return false;
    // The DEM canvas is new on every load and painted later by the render worker (dem-main.js).
    if (mode === 'Dem') return real.dataset?.pending !== '1';
    return real !== seen;
}

/**
 * May the whole preview end? Only once the reload released it, and then when every layer it
 * holds has landed or been switched off, or the hold time ran out.
 * @param {{released:boolean, deadline:number, demLanded:boolean, seen:Map}} st
 * @param {Object<string, HTMLCanvasElement|null>} sources  active layers' real canvases
 * @param {number} now
 * @returns {boolean}
 */
export function previewCanEnd(st, sources, now) {
    if (!st?.released) return false;
    if (now >= st.deadline) return true;
    for (const [mode, seen] of st.seen) {
        if (!(mode in sources) || NOT_RELOADED.has(mode)) continue;   // switched off, or never reloads
        if (!layerHasLanded(mode, sources[mode], seen, st.demLanded)) return false;
    }
    return true;
}

function _snapshot(canvas) {
    if (!canvas?.width || !canvas?.height) return null;
    try {
        return canvas.getContext('2d', { willReadFrequently: true })
            .getImageData(0, 0, canvas.width, canvas.height);
    } catch {
        return null;   // a cross-origin (tainted) picture cannot be read: no preview for it
    }
}

/** Longest side of a warped preview layer (warp in node: 600 px ~1-5 ms, 1500 px ~11 ms; the
 * canvas snapshot and draw cost more than the warp). */
const PREVIEW_MAX_DIM = 1200;

function _warpToCanvas(img, st) {
    const r = warpPixels(img, st.bbox, st.from, st.to, Math.min(PREVIEW_MAX_DIM, Math.max(img.width, img.height)));
    const c = document.createElement('canvas');
    c.width = r.width;
    c.height = r.height;
    c.getContext('2d').putImageData(new ImageData(r.data, r.width, r.height), 0, 0);
    return c;
}

/**
 * Warp the loaded layers into a new projection and keep them as the layers' sources until
 * the real ones land. A second call while a preview is showing re-warps the same loaded
 * pixels (they are still in the projection they were fetched in).
 * @param {object} opts
 * @param {object} opts.bbox      the loaded DEM's box
 * @param {{projection:string, maintainDimensions:boolean}} opts.from  projection of `sources`
 * @param {{projection:string, maintainDimensions:boolean}} opts.to
 * @param {number} opts.dim       longest side of the DEM raster
 * @param {Object<string, HTMLCanvasElement|null>} opts.sources  real source canvas per active layer
 * @returns {boolean} true when a preview is showing
 */
export function startProjectionPreview({ bbox, from, to, dim, sources }) {
    const t0 = performance.now();
    if (!_state) {
        if (!bbox || (from.projection === to.projection
            && !!from.maintainDimensions === !!to.maintainDimensions)) return false;
        const base = new Map();
        for (const [mode, canvas] of Object.entries(sources || {})) {
            const img = _snapshot(canvas);
            if (img) base.set(mode, img);
        }
        if (!base.size) return false;
        _state = { bbox, from, base, warped: new Map(), seen: new Map() };
    }
    const st = _state;
    st.released = false;
    st.deadline = Infinity;
    clearTimeout(_holdTimer);
    st.to = { projection: to.projection, maintainDimensions: !!to.maintainDimensions };
    st.shape = projectedShape(st.bbox, st.to.projection, dim, st.to.maintainDimensions);
    st.demLanded = false;
    st.warped.clear();
    for (const [mode, img] of st.base) st.warped.set(mode, _warpToCanvas(img, st));
    // Whatever is loaded now belongs to an older pick: only a canvas that appears after
    // this pick's DEM lands may replace the preview.
    st.seen.clear();
    for (const mode of st.base.keys()) st.seen.set(mode, sources?.[mode] || null);
    st.ms = performance.now() - t0;
    _setPill(true);
    return true;
}

/**
 * The newest pick's DEM has landed: the DEM shows for real from now on, and every other layer
 * shows for real once its own new canvas appears.
 * @param {Object<string, HTMLCanvasElement|null>} sources  real source canvas per layer, now
 */
export function markPreviewDemLanded(sources) {
    if (!_state) return;
    _state.demLanded = true;
    for (const mode of _state.base.keys()) _state.seen.set(mode, sources?.[mode] || null);
}

/**
 * The reload is done: end the preview once every layer it holds has landed (checked on each
 * stack redraw by settleProjectionPreview), or after HOLD_MS.
 * @param {Object<string, HTMLCanvasElement|null>} sources  active layers' real canvases now
 */
export function releaseProjectionPreview(sources) {
    const st = _state;
    if (!st) return;
    st.released = true;
    st.deadline = performance.now() + HOLD_MS;
    clearTimeout(_holdTimer);
    _holdTimer = setTimeout(() => {
        if (_state === st) { endProjectionPreview(); window.updateStackedLayers?.(); }
    }, HOLD_MS);
    settleProjectionPreview(sources);
}

/**
 * End a released preview whose layers have all landed (updateStackedLayers calls this).
 * @param {Object<string, HTMLCanvasElement|null>} sources  active layers' real canvases now
 */
export function settleProjectionPreview(sources) {
    if (_state && previewCanEnd(_state, sources || {}, performance.now())) endProjectionPreview();
}

/** Drop the preview: every layer shows its real source again. */
export function endProjectionPreview() {
    _state = null;
    clearTimeout(_holdTimer);
    _setPill(false);
}

/** @returns {boolean} */
export function isProjectionPreviewActive() {
    return !!_state;
}

/**
 * The canvas to draw for a layer: its warped preview until its real data for the newest
 * pick has landed, else the real one.
 * @param {string} mode   layer mode (Dem, WaterHydrology, …)
 * @param {HTMLCanvasElement|null} real
 * @returns {HTMLCanvasElement|null}
 */
export function projectionPreviewSource(mode, real) {
    const st = _state;
    const pv = st?.warped.get(mode);
    if (!pv) return real;
    return layerHasLanded(mode, real, st.seen.get(mode), st.demLanded) ? real : pv;
}

/**
 * The frame's raster shape and box while the DEM is still a preview, else null.
 * @returns {{bbox:object, width:number, height:number}|null}
 */
export function projectionPreviewFrame() {
    if (!_state || _state.demLanded) return null;
    return { bbox: _state.bbox, ..._state.shape };
}

/** Milliseconds the last warp took (diagnostics). */
export function projectionPreviewTiming() {
    return _state?.ms ?? null;
}
