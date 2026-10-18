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
 * Hand-over is per layer and per pick (the change handler numbers each pick): the DEM swaps to
 * its real picture once the render worker has painted it (it is blank for a few hundred ms after
 * loadDEM resolves); every other layer once its reload for this pick has settled
 * (settlePreviewLayer), whatever it produced: a new canvas, the same cached one, nothing (no
 * rivers in the box) or a failure. Settled, not "a new canvas appeared": Rivers & lakes with no
 * rivers never gets a canvas, and waiting for one held the pill for HOLD_MS (Everest, 31 s).
 * Rivers & lakes, Borders and Trails reload on their own, in parallel with the DEM, and settle
 * when their own fetch does; the other reloaded layers settle at release (the reload awaited
 * them). The preview ends, and its pill (#projectionPreviewStatus) hides, when every held layer
 * has settled, or HOLD_MS after release. (Ending it at release left Rivers & lakes with no source
 * while its fetch ran, and the stack kept that layer's last preview buffer at the old pixel
 * density: a misplaced blue band.)
 *
 * Each layer is snapshotted and warped at no more than PREVIEW_MAX_DIM px (a stand-in for 1-3 s;
 * at 1000 px the full-size snapshot, warp and draw took ~250 ms of main thread on Colombia).
 *
 * warpPixels, previewSize, layerHasLanded, layerSettled, settleLayer and previewCanEnd are pure
 * (tests/js/projectionPreview.test.js); the rest touches canvases. projTiming logs the
 * switch's timeline when window.DEBUG_PROJ_TIMING is set.
 */
import { geoAtFrac, projectedFrac, projectedShape } from './graticule.js';

/** Azimuthal and conic projections: rows are curved, so the per-row shortcut does not hold. */
const isConic = (p) => p === 'laea' || p === 'lcc';

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
    // laea / lcc (as target or source) bend the rows: latitude varies along one, so each pixel
    // is mapped on its own.
    if (isConic(to.projection) || isConic(from.projection)) {
        const { north, south } = bbox;
        const latTol = Math.abs(north - south) * 1e-9;
        for (let j = 0; j < H; j++) {
            const fy = (j + 0.5) / H;
            const row = j * W;
            for (let i = 0; i < W; i++) {
                const g = inv((i + 0.5) / W, fy);
                if (!(g.lon >= west - tol && g.lon <= east + tol
                    && g.lat >= south - latTol && g.lat <= north + latTol)) continue;
                const s = fwd(g.lat, g.lon);
                if (!(s.x >= 0 && s.x <= 1 && s.y >= 0 && s.y <= 1)) continue;
                o32[row + i] = s32[Math.min(sh - 1, (s.y * sh) | 0) * sw + Math.min(sw - 1, (s.x * sw) | 0)];
            }
        }
        return { data: out, width: W, height: H };
    }
    // Along one row every other supported projection is affine: latitude is fixed by the row, the
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

// ── Timing log ───────────────────────────────────────────────────────────────

let _timingT0 = 0;

/** Start the clock of one projection switch (the change event). */
export function projTimingStart() {
    _timingT0 = performance.now();
    projTiming('change event');
}

/**
 * Log a step of the projection switch, in ms after the change event, when
 * window.DEBUG_PROJ_TIMING is set: `[proj-timing] 1234 ms dem landed`.
 * @param {string} what
 */
export function projTiming(what) {
    if (!globalThis.window?.DEBUG_PROJ_TIMING) return;
    console.debug('[proj-timing]', `${Math.round(performance.now() - _timingT0)} ms`, what);
}

// ── Preview state ────────────────────────────────────────────────────────────

/**
 * @typedef {object} PreviewState
 * @property {number} pick    the change handler's pick number this preview stands in for
 * @property {object} bbox
 * @property {{projection:string, maintainDimensions:boolean}} from  projection of the snapshots
 * @property {{projection:string, maintainDimensions:boolean}} to    projection shown
 * @property {{width:number, height:number}} shape  predicted DEM raster shape in `to`
 * @property {Map<string, ImageData>} base     loaded pixels per layer mode, in `from`
 * @property {Map<string, HTMLCanvasElement>} warped  base warped to `to`
 * @property {Map<string, HTMLCanvasElement|null>} seen  each layer's real canvas when the pick
 *   started or the DEM landed (display only: a layer nothing settles, e.g. the composite,
 *   shows its real canvas once a different one appears after the DEM landed)
 * @property {Set<string>} settled  layers whose reload for this pick has finished
 * @property {boolean} demLanded  the DEM for `to` has arrived
 * @property {boolean} released   the reload is done; layers still loading keep their preview
 * @property {number} deadline     performance.now() after which a released preview ends anyway
 */
/** @type {PreviewState|null} */
let _state = null;

/** How long a released preview may hold a layer that never settles. */
const HOLD_MS = 30000;
/** Layers nothing re-fetches after a projection change: never wait for them. */
const NOT_RELOADED = new Set(['MeshImport', 'CompositeDem']);
let _holdTimer = null;

function _setPill(on) {
    document.getElementById('projectionPreviewStatus')?.classList.toggle('hidden', !on);
}

/**
 * Has a layer's real data for the newest pick visibly arrived? (Display fallback for layers
 * nothing settles; the end condition is layerSettled.)
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
 * Is a layer done for this pick? The DEM once it has landed and the render worker has painted
 * it; any other layer once its reload settled (landed, empty, cached or failed).
 * @param {string} mode
 * @param {HTMLCanvasElement|null} real
 * @param {{demLanded:boolean, settled:Set<string>}} st
 * @returns {boolean}
 */
export function layerSettled(mode, real, st) {
    if (mode === 'Dem') return !!st.demLanded && (!real || real.dataset?.pending !== '1');
    return !!st.settled?.has(mode);
}

/**
 * May the whole preview end? Only once the reload released it, and then when every layer it
 * holds has settled or been switched off, or the hold time ran out.
 * @param {{released:boolean, deadline:number, demLanded:boolean, settled:Set, base:Map}} st
 * @param {Object<string, HTMLCanvasElement|null>} sources  active layers' real canvases
 * @param {number} now
 * @returns {boolean}
 */
export function previewCanEnd(st, sources, now) {
    if (!st?.released) return false;
    return now >= st.deadline || !unsettledModes(st, sources).length;
}

/**
 * The held layers the preview still waits for (switched-off layers and those nothing reloads
 * are not waited for). Pure; also named in the hold-timeout log.
 * @param {{demLanded:boolean, settled:Set<string>, base:Map}} st
 * @param {Object<string, HTMLCanvasElement|null>} sources  active layers' real canvases
 * @returns {string[]}
 */
export function unsettledModes(st, sources) {
    const out = [];
    for (const mode of st.base.keys()) {
        if (!(mode in sources) || NOT_RELOADED.has(mode)) continue;   // switched off, or never reloads
        if (!layerSettled(mode, sources[mode], st)) out.push(mode);
    }
    return out;
}

/** Longest side of a preview layer: snapshot and warp. Half of Colombia's 1000 px. */
const PREVIEW_MAX_DIM = 600;

/**
 * Size a layer is snapshotted at: its own, scaled down to `max` on the longest side.
 * @param {number} w
 * @param {number} h
 * @param {number} [max=PREVIEW_MAX_DIM]
 * @returns {{width:number, height:number}}
 */
export function previewSize(w, h, max = PREVIEW_MAX_DIM) {
    const s = Math.min(1, max / Math.max(w, h, 1));
    return { width: Math.max(1, Math.round(w * s)), height: Math.max(1, Math.round(h * s)) };
}

/** A layer's pixels at no more than PREVIEW_MAX_DIM (drawn down on the GPU, then read back). */
function _snapshot(canvas) {
    if (!canvas?.width || !canvas?.height) return null;
    try {
        const { width, height } = previewSize(canvas.width, canvas.height);
        const c = document.createElement('canvas');
        c.width = width;
        c.height = height;
        const ctx = c.getContext('2d', { willReadFrequently: true });
        ctx.imageSmoothingQuality = 'high';   // one-pixel rivers fade rather than break up
        ctx.drawImage(canvas, 0, 0, width, height);
        return ctx.getImageData(0, 0, width, height);
    } catch {
        return null;   // a cross-origin (tainted) picture cannot be read: no preview for it
    }
}

function _warpToCanvas(img, st) {
    const r = warpPixels(img, st.bbox, st.from, st.to, Math.max(img.width, img.height));
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
 * @param {number} opts.pick      the change handler's pick number (settlePreviewLayer checks it)
 * @param {object} opts.bbox      the loaded DEM's box
 * @param {{projection:string, maintainDimensions:boolean}} opts.from  projection of `sources`
 * @param {{projection:string, maintainDimensions:boolean}} opts.to
 * @param {number} opts.dim       longest side of the DEM raster
 * @param {Object<string, HTMLCanvasElement|null>} opts.sources  real source canvas per active
 *   layer, read before the reload cleared them
 * @returns {boolean} true when a preview is showing
 */
export function startProjectionPreview({ pick = 0, bbox, from, to, dim, sources }) {
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
        _state = { bbox, from, base, warped: new Map(), seen: new Map(), settled: new Set() };
    }
    const st = _state;
    st.pick = pick;
    st.released = false;
    st.deadline = Infinity;
    clearTimeout(_holdTimer);
    st.to = { projection: to.projection, maintainDimensions: !!to.maintainDimensions };
    st.shape = projectedShape(st.bbox, st.to.projection, dim, st.to.maintainDimensions);
    st.demLanded = false;
    st.settled.clear();
    st.warped.clear();
    for (const [mode, img] of st.base) st.warped.set(mode, _warpToCanvas(img, st));
    // Whatever is loaded now belongs to an older pick.
    st.seen.clear();
    for (const mode of st.base.keys()) st.seen.set(mode, sources?.[mode] || null);
    st.ms = performance.now() - t0;
    projTiming(`preview warped (${Math.round(st.ms)} ms, ${st.base.size} layers)`);
    _setPill(true);
    return true;
}

/**
 * Record that one layer's reload for pick `pick` has finished (pure on `st`). A stale pick,
 * or no preview, changes nothing.
 * @param {{pick:number, settled:Set<string>, base:Map}|null} st
 * @param {string} mode
 * @param {number} pick
 * @returns {boolean} true when the preview held that layer (the stack should redraw)
 */
export function settleLayer(st, mode, pick) {
    if (!st || st.pick !== pick || st.settled.has(mode)) return false;
    st.settled.add(mode);
    return st.base.has(mode);
}

/**
 * One layer's reload for pick `pick` has finished, with or without a picture: it shows for
 * real from now on, and the preview no longer waits for it. A stale pick is ignored.
 *
 * Checks the end condition here, not only on the next stack redraw: the redraw runs in a
 * requestAnimationFrame (emitStackUpdate), which a hidden tab or pane never fires, and
 * updateStackedLayers returns before its check when #layersStack is absent. A switch whose
 * last layers settled after the release then held the pill the full 30 s (Colombia, cosine
 * to mercator, 2026-10-14).
 * @param {string} mode
 * @param {number} pick
 * @param {Object<string, HTMLCanvasElement|null>} [sources]  active layers' real canvases now
 * @returns {boolean} true when it changed what the stack shows (redraw it)
 */
export function settlePreviewLayer(mode, pick, sources = globalThis.window?.getLayerSourceCanvases?.()) {
    const changed = settleLayer(_state, mode, pick);
    if (!_state || !sources) return changed;
    settleProjectionPreview(sources);
    return changed || !_state;   // an ended preview shows every real layer: redraw
}

/**
 * The newest pick's DEM has landed: the DEM shows for real from now on (once painted).
 * @param {Object<string, HTMLCanvasElement|null>} sources  real source canvas per layer, now
 */
export function markPreviewDemLanded(sources) {
    if (!_state) return;
    _state.demLanded = true;
    for (const mode of _state.base.keys()) {
        if (!_state.settled.has(mode)) _state.seen.set(mode, sources?.[mode] || null);
    }
}

/**
 * What a release settles: every held layer except the DEM (it settles when painted), those
 * still reloading on their own (`pending`), and those nothing reloads (NOT_RELOADED: settling
 * one would show its real canvas, which the reload nulled, e.g. a blank composite; they keep
 * their preview until a new canvas appears or the preview ends). Pure.
 * @param {{settled:Set<string>, base:Map}} st
 * @param {Iterable<string>} [pending]
 */
export function releaseSettles(st, pending = []) {
    const wait = new Set(pending);
    for (const mode of st.base.keys()) {
        if (mode !== 'Dem' && !wait.has(mode) && !NOT_RELOADED.has(mode)) st.settled.add(mode);
    }
}

/**
 * The reload is done: every held layer settles except those whose own reload is still
 * running (`pending`); the preview ends once those settle too (checked on each stack redraw by
 * settleProjectionPreview), or after HOLD_MS.
 * @param {Object<string, HTMLCanvasElement|null>} sources  active layers' real canvases now
 * @param {Iterable<string>} [pending]  layers still reloading on their own
 */
export function releaseProjectionPreview(sources, pending = []) {
    const st = _state;
    if (!st) return;
    releaseSettles(st, pending);
    st.released = true;
    st.deadline = performance.now() + HOLD_MS;
    clearTimeout(_holdTimer);
    _holdTimer = setTimeout(() => {
        if (_state !== st) return;
        const left = unsettledModes(st, window.getLayerSourceCanvases?.() || {});
        projTiming(`hold timeout: still unsettled ${left.join(', ') || '(none: no check ran)'}`);
        endProjectionPreview();
        window.updateStackedLayers?.();
    }, HOLD_MS);
    settleProjectionPreview(sources);
}

/**
 * End a released preview whose layers have all settled (updateStackedLayers calls this).
 * @param {Object<string, HTMLCanvasElement|null>} sources  active layers' real canvases now
 */
export function settleProjectionPreview(sources) {
    if (_state && previewCanEnd(_state, sources || {}, performance.now())) endProjectionPreview();
}

/** Drop the preview: every layer shows its real source again. */
export function endProjectionPreview() {
    if (_state) projTiming('preview ended (pill hidden)');
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
 * pick has settled (or visibly landed), else the real one (null: the layer has nothing).
 * @param {string} mode   layer mode (Dem, WaterHydrology, …)
 * @param {HTMLCanvasElement|null} real
 * @returns {HTMLCanvasElement|null}
 */
export function projectionPreviewSource(mode, real) {
    const st = _state;
    const pv = st?.warped.get(mode);
    if (!pv) return real;
    if (mode === 'Dem') return layerHasLanded(mode, real, null, st.demLanded) ? real : pv;
    if (st.settled.has(mode)) return real;
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
