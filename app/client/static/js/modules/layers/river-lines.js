/**
 * river-lines.js — river lines on the Edit map (#hydroShowLines, "River lines" in the
 * Rivers & lakes settings).
 *
 * HydroRIVERS reaches drawn as polylines over the map, in the model's projection, so a
 * river reads at any zoom; the raster carve underneath is what prints. Lines come from
 * GET /api/terrain/river-lines (app/server/core/river_lines.py) for the part of the
 * region on screen, simplified to about one screen pixel, and the Rivers size control
 * (#hydroMinOrder) is the smallest order asked for. Zooming in asks again when the view
 * leaves the fetched area or the zoom changes by more than REFETCH_ZOOM_RATIO, so
 * detail grows with the zoom.
 *
 * Drawn on #riverLinesCanvas in screen space, like the grid (stacked-layers.js
 * drawLayerGrid): sharp at any zoom. stacked-layers.js calls window.drawRiverLines after
 * every settled transform and window.riverLinesCSSDelta during a drag.
 *
 * Geo -> map: modules/dem/graticule.js::projectedFrac onto appState.demLayout, the
 * mapping the carve's grid uses. Style: modules/layers/river-style.js.
 *
 * The exports above the "Map wiring" line are pure, for vitest.
 */

import { geoAtFrac, projectedFrac } from '../dem/graticule.js';
import {
    RIVER_RGB, RIVER_EDGE_RGB, RIVER_EDGE_ALPHA, orderColor, riverLineWidthPx, rgb,
} from './river-style.js';

/** Fetch again when the zoom has changed by more than this factor since the last fetch. */
export const REFETCH_ZOOM_RATIO = 1.6;
/** The view asked for is the visible area plus this fraction of it on every side. */
export const VIEW_PAD = 0.25;
/** Wait this long after the last pan / zoom before fetching, ms. */
const FETCH_DEBOUNCE_MS = 250;

/** base64 -> typed array of `Type` (little-endian, as the server writes). */
function b64Array(b64, Type) {
    if (!b64) return new Type(0);
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Type(bytes.buffer, 0, bytes.byteLength / Type.BYTES_PER_ELEMENT);
}

/**
 * Decode a /api/terrain/river-lines payload.
 * @returns {{coords:Float32Array, offsets:Uint32Array, orders:Uint8Array, count:number}}
 *   coords: lon, lat pairs; line i is points offsets[i] .. offsets[i+1] - 1
 */
export function decodeRiverLines(p) {
    const coords = b64Array(p?.coords_b64, Float32Array);
    const offsets = b64Array(p?.offsets_b64, Uint32Array);
    const orders = b64Array(p?.orders_b64, Uint8Array);
    return { coords, offsets, orders, count: Math.max(0, offsets.length - 1) };
}

/**
 * Project decoded lines to fractions of the projected raster (x 0 = west edge, y 0 = north
 * edge), once per payload, so a redraw is only an affine map.
 * @returns {Float32Array} fx, fy pairs, same order as `coords`
 */
export function projectLines(lines, bbox, projection = 'none', maintainDimensions = false) {
    const at = projectedFrac(bbox, projection, maintainDimensions);
    const n = lines.coords.length / 2;
    const out = new Float32Array(n * 2);
    for (let i = 0; i < n; i++) {
        const f = at(lines.coords[2 * i + 1], lines.coords[2 * i]);
        out[2 * i] = f.x;
        out[2 * i + 1] = f.y;
    }
    return out;
}

/**
 * The part of the map raster on screen, as fractions (clamped to 0..1), padded by `pad`.
 * @param {{x:number,y:number,w:number,h:number}} layout  appState.demLayout (stack CSS px)
 * @param {{scale:number, offsetX:number, offsetY:number}} zoom  appState.stackZoom
 * @param {number} viewW  stack width, CSS px
 * @param {number} viewH  stack height, CSS px
 * @returns {{x0:number,y0:number,x1:number,y1:number}|null} null when nothing is on screen
 */
export function visibleFrac(layout, zoom, viewW, viewH, pad = 0) {
    if (!layout?.w || !layout?.h || !(zoom?.scale > 0)) return null;
    const fx = (sx) => ((sx - zoom.offsetX) / zoom.scale - layout.x) / layout.w;
    const fy = (sy) => ((sy - zoom.offsetY) / zoom.scale - layout.y) / layout.h;
    let x0 = fx(0), x1 = fx(viewW), y0 = fy(0), y1 = fy(viewH);
    const px = (x1 - x0) * pad, py = (y1 - y0) * pad;
    x0 = Math.max(0, x0 - px); x1 = Math.min(1, x1 + px);
    y0 = Math.max(0, y0 - py); y1 = Math.min(1, y1 + py);
    if (!(x1 > x0 && y1 > y0)) return null;
    return { x0, y0, x1, y1 };
}

/** Is fraction rect `a` inside `b`? */
export function fracInside(a, b) {
    const e = 1e-6;
    return !!a && !!b && a.x0 >= b.x0 - e && a.y0 >= b.y0 - e && a.x1 <= b.x1 + e && a.y1 <= b.y1 + e;
}

/**
 * Latitude/longitude box of a fraction rect of the projected raster (5 × 5 samples, so a
 * projection that bends meridians is still covered), clamped to the region.
 */
export function fracToBbox(frac, bbox, projection = 'none', maintainDimensions = false) {
    const geo = geoAtFrac(bbox, projection, maintainDimensions);
    let n = -Infinity, s = Infinity, e = -Infinity, w = Infinity;
    for (let i = 0; i <= 4; i++) {
        for (let j = 0; j <= 4; j++) {
            const p = geo(frac.x0 + (frac.x1 - frac.x0) * i / 4, frac.y0 + (frac.y1 - frac.y0) * j / 4);
            n = Math.max(n, p.lat); s = Math.min(s, p.lat);
            e = Math.max(e, p.lon); w = Math.min(w, p.lon);
        }
    }
    return {
        north: Math.min(bbox.north, n), south: Math.max(bbox.south, s),
        east: Math.min(bbox.east, e), west: Math.max(bbox.west, w),
    };
}

/**
 * Query for GET /api/terrain/river-lines.
 * @param {{bbox:object, view:object, widthPx:number, heightPx:number, minOrder:number}} o
 */
export function riverLinesQuery({ bbox, view, widthPx, heightPx, minOrder }) {
    const r6 = (v) => Number(v).toFixed(6);
    const q = new URLSearchParams({
        north: bbox.north, south: bbox.south, east: bbox.east, west: bbox.west,
        min_order: minOrder,
        width_px: Math.max(16, Math.round(widthPx)),
        height_px: Math.max(16, Math.round(heightPx)),
    });
    if (view) {
        q.set('view_north', r6(view.north)); q.set('view_south', r6(view.south));
        q.set('view_east', r6(view.east)); q.set('view_west', r6(view.west));
    }
    return q;
}

/**
 * Stroke the lines: a dark edge pass, then the colour pass, small orders first so the big
 * rivers sit on top.
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {{offsets:Uint32Array, orders:Uint8Array, count:number}} lines
 * @param {Float32Array} frac  projectLines() output
 * @param {(fx:number, fy:number) => [number, number]} toScreen  fraction -> canvas px
 * @param {{zoom?:number, pxRatio?:number, byOrder?:boolean}} [o]
 * @returns {number} lines drawn
 */
export function strokeRiverLines(ctx, lines, frac, toScreen, { zoom = 1, pxRatio = 1, byOrder = false } = {}) {
    const byOrd = new Map();
    for (let i = 0; i < lines.count; i++) {
        const o = lines.orders[i] || 1;
        if (!byOrd.has(o)) byOrd.set(o, []);
        byOrd.get(o).push(i);
    }
    const orders = [...byOrd.keys()].sort((a, b) => a - b);
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    const edge = `rgba(${RIVER_EDGE_RGB.join(', ')}, ${(RIVER_EDGE_ALPHA / 255).toFixed(3)})`;
    for (const pass of ['edge', 'fill']) {
        for (const o of orders) {
            const w = riverLineWidthPx(o, zoom) * pxRatio;
            ctx.lineWidth = pass === 'edge' ? w + 2 * pxRatio : w;
            ctx.strokeStyle = pass === 'edge' ? edge : rgb(byOrder ? orderColor(o) : RIVER_RGB);
            ctx.beginPath();
            for (const i of byOrd.get(o)) {
                const a = lines.offsets[i], b = lines.offsets[i + 1];
                if (b - a < 2) continue;
                for (let k = a; k < b; k++) {
                    const [x, y] = toScreen(frac[2 * k], frac[2 * k + 1]);
                    if (k === a) ctx.moveTo(x, y); else ctx.lineTo(x, y);
                }
            }
            ctx.stroke();
        }
    }
    return lines.count;
}

// ─────────────────────────────────────────────────────────────────────────────
// Map wiring
// ─────────────────────────────────────────────────────────────────────────────

/** The lines on show: payload, its projection to fractions and what it was fetched for. */
let _shown = null;   // { regionKey, lines, frac, fetchedFrac, fetchedScale }
let _baked = { scale: 1, offsetX: 0, offsetY: 0 };
let _fetchTimer = null;
let _abort = null;
let _pendingKey = null;
let _failedKey = null;   // a request that failed is not repeated on every redraw

const _on = () => !!document.getElementById('hydroShowLines')?.checked;
const _canvas = () => document.getElementById('riverLinesCanvas');

function _minOrder() {
    const v = parseInt(document.getElementById('hydroMinOrder')?.value, 10);
    return Number.isFinite(v) ? Math.max(1, Math.min(9, v)) : 3;
}

/** What a fetched payload depends on besides the view: region, projection, size floor. */
function _regionKey(bbox) {
    const { projection, maintainDimensions } = window.getProjectionParams?.() || {};
    return [bbox.north, bbox.south, bbox.east, bbox.west, projection || 'none',
        !!maintainDimensions, _minOrder()].join('|');
}

function _clear() {
    const c = _canvas();
    if (c && c.width) { c.width = 0; c.height = 0; }
}

/**
 * Redraw the river lines at the current zoom, and fetch finer or wider ones when the view
 * has moved out of what was fetched. Called by stacked-layers.js applyStackedTransform.
 */
function drawRiverLines() {
    const c = _canvas();
    const stack = document.getElementById('layersStack');
    const app = window.appState || {};
    const bbox = app.currentDemBbox;
    if (!c || !stack || !bbox || !_on()) { _clear(); return; }
    const rect = stack.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const zoom = app.stackZoom || { scale: 1, offsetX: 0, offsetY: 0 };
    const layout = app.demLayout;

    const key = _regionKey(bbox);
    if (_shown && _shown.regionKey !== key) _shown = null;
    _scheduleFetch(key, bbox, layout, zoom, rect);

    // Screen-space canvas at device resolution, like the grid.
    const dpr = Math.min(3, window.devicePixelRatio || 1);
    const pw = Math.round(rect.width * dpr), ph = Math.round(rect.height * dpr);
    if (c.width !== pw) c.width = pw;
    if (c.height !== ph) c.height = ph;
    c.style.width = `${rect.width}px`;
    c.style.height = `${rect.height}px`;
    c.style.transform = '';
    _baked = { ...zoom };
    const ctx = c.getContext('2d');
    ctx.clearRect(0, 0, pw, ph);
    if (!_shown || !layout?.w) return;

    const sx = layout.w * zoom.scale * dpr, ox = (layout.x * zoom.scale + zoom.offsetX) * dpr;
    const sy = layout.h * zoom.scale * dpr, oy = (layout.y * zoom.scale + zoom.offsetY) * dpr;
    const pt = [0, 0];
    const toScreen = (fx, fy) => { pt[0] = fx * sx + ox; pt[1] = fy * sy + oy; return pt; };
    ctx.save();
    // Only inside the map's frame: a line reaching past the region edge stops there.
    ctx.beginPath();
    ctx.rect(ox, oy, sx, sy);
    ctx.clip();
    strokeRiverLines(ctx, _shown.lines, _shown.frac, toScreen, {
        zoom: zoom.scale, pxRatio: dpr,
        byOrder: document.getElementById('hydroColorMode')?.value === 'order',
    });
    ctx.restore();
}

/** During a drag or wheel gesture: move the last drawing with the map (CSS only). */
function riverLinesCSSDelta() {
    const c = _canvas();
    const zoom = window.appState?.stackZoom;
    if (!c || !c.width || !zoom) return;
    const ds = zoom.scale / (_baked.scale || 1);
    c.style.transformOrigin = '0 0';
    c.style.transform = `translate(${zoom.offsetX - _baked.offsetX * ds}px, ${zoom.offsetY - _baked.offsetY * ds}px) scale(${ds})`;
}

function _scheduleFetch(key, bbox, layout, zoom, rect) {
    const visible = visibleFrac(layout, zoom, rect.width, rect.height, 0);
    if (!visible) return;
    if (_shown && fracInside(visible, _shown.fetchedFrac)) {
        const ratio = zoom.scale / _shown.fetchedScale;
        if (ratio < REFETCH_ZOOM_RATIO && ratio > 1 / REFETCH_ZOOM_RATIO) return;
    }
    clearTimeout(_fetchTimer);
    _fetchTimer = setTimeout(() => _fetch(key, bbox, layout, { ...zoom }, rect), _shown ? FETCH_DEBOUNCE_MS : 0);
}

async function _fetch(key, bbox, layout, zoom, rect) {
    const frac = visibleFrac(layout, zoom, rect.width, rect.height, VIEW_PAD);
    if (!frac || !window.api?.dem?.riverLines) return;
    const { projection, maintainDimensions } = window.getProjectionParams?.() || {};
    const reqKey = `${key}|${frac.x0.toFixed(4)},${frac.y0.toFixed(4)},${frac.x1.toFixed(4)},${frac.y1.toFixed(4)}|${zoom.scale.toFixed(3)}`;
    if (reqKey === _pendingKey || reqKey === _failedKey) return;
    _pendingKey = reqKey;
    _abort?.abort();
    _abort = new AbortController();
    const signal = _abort.signal;
    const whole = frac.x0 <= 0 && frac.y0 <= 0 && frac.x1 >= 1 && frac.y1 >= 1;
    const q = riverLinesQuery({
        bbox,
        view: whole ? null : fracToBbox(frac, bbox, projection, maintainDimensions),
        widthPx: (frac.x1 - frac.x0) * layout.w * zoom.scale,
        heightPx: (frac.y1 - frac.y0) * layout.h * zoom.scale,
        minOrder: _minOrder(),
    });
    window.setLayerStatus?.('riverLines', 'loading');
    const { data, error } = await window.api.dem.riverLines(q, signal);
    if (signal.aborted) return;
    _pendingKey = null;
    if (error || !data || data.error) {
        _failedKey = reqKey;
        console.warn('[river-lines] fetch failed:', error || data?.error);
        window.setLayerStatus?.('riverLines', 'error');
        return;
    }
    // The region or the size floor may have changed while it ran.
    if (!window.appState?.currentDemBbox || _regionKey(window.appState.currentDemBbox) !== key) return;
    const lines = decodeRiverLines(data);
    _shown = {
        regionKey: key, lines,
        frac: projectLines(lines, bbox, projection, maintainDimensions),
        fetchedFrac: frac, fetchedScale: zoom.scale,
    };
    window.setLayerStatus?.('riverLines', 'loaded');
    drawRiverLines();
}

// Guarded so vitest (node, no DOM) can import the pure helpers above.
if (typeof window !== 'undefined' && typeof document !== 'undefined') {
    window.drawRiverLines = drawRiverLines;
    window.riverLinesCSSDelta = riverLinesCSSDelta;
    // The switch, the size floor and the colour mode redraw (the size floor refetches).
    document.addEventListener('change', (e) => {
        const id = e.target?.id;
        if (id === 'hydroShowLines' || id === 'hydroMinOrder' || id === 'hydroColorMode') drawRiverLines();
    });
}
