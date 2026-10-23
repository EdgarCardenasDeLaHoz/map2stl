/**
 * city-quickview.js — buildings raised on the Extrude 3D preview within seconds.
 *
 * The preview mesh is the terrain stage only; buildings come from the City Model
 * build, which takes minutes (Philadelphia: 140 s). Until it is done the viewer shows
 * this quick view: every building footprint as a prism, by the City Model's own rule
 * (`city2stl/city_model.py`): top = highest ground under the footprint +
 * max(height_m × z_mm_per_m × multiplier, MIN_HEIGHT_MM). Footprints are not merged,
 * simplified or widened, so it is close to the print, not the print.
 *
 * Roofs: a building with `roof:shape` gabled / hipped (pyramidal counts as hipped) and a
 * single outline gets a pitched roof, `roof:height` m tall (else 30 % of its height, never
 * above 50 %, as `city_model._roof`); the eave sits that far below the top. Flat, missing or
 * unknown shapes, outlines with holes, and roofs under 0.2 mm stay flat prisms.
 *
 * Pure: the geometry builder takes its inputs as arguments (vitest imports it).
 */

/** The City Model never prints a building thinner than this (style.min_height_mm). */
export const MIN_HEIGHT_MM = 0.4;
/** Height for a building without height_m, as city_model's OSM fallback. */
export const DEFAULT_HEIGHT_M = 10;

/** Roof kinds the quick view draws, by OSM `roof:shape` value. */
const ROOF_KIND = { gabled: 'gable', gable: 'gable', hipped: 'hip', hip: 'hip', pyramidal: 'hip' };
/** Default roof height as a share of the building height, and its cap (city_model._roof). */
export const ROOF_HEIGHT_SHARE = 0.3;
export const ROOF_MAX_SHARE = 0.5;
/** A roof lower than this prints flat (city_model._roof). */
export const MIN_ROOF_MM = 0.2;

/** `'gable' | 'hip' | null` for a feature's properties. */
export function roofKind(props) {
    return ROOF_KIND[String(props?.['roof:shape'] ?? '').trim().toLowerCase()] ?? null;
}

/** Roof height in metres for a building `hM` tall: `roof:height` (first number) else 30 %, capped at 50 %. */
export function roofHeightM(props, hM) {
    const raw = parseFloat(String(props?.['roof:height'] ?? '').trim().split(/\s+/)[0]);
    const m = Number.isFinite(raw) ? raw : ROOF_HEIGHT_SHARE * hM;
    return Math.min(Math.max(m, 0), ROOF_MAX_SHARE * hM);
}

function _isConvex(ring) {
    let sign = 0;
    for (let i = 0; i < ring.length; i++) {
        const [ax, ay] = ring[i], [bx, by] = ring[(i + 1) % ring.length], [cx, cy] = ring[(i + 2) % ring.length];
        const c = (bx - ax) * (cy - by) - (by - ay) * (cx - bx);
        if (Math.abs(c) < 1e-12) continue;
        if (sign && Math.sign(c) !== sign) return false;
        sign = Math.sign(c);
    }
    return true;
}

/**
 * Pitched roof for one outline: pushes roof triangles (and the gable-end wall tops) into
 * `pos` / `idx`. `ring` is [[x,y]...] in preview pixels, `tris` its triangulation, `eave` and
 * `roofMm` in mm. Returns false (nothing pushed) when the outline suits no roof.
 */
function _pitchedRoof(kind, ring, tris, k, eave, roofMm, pos, idx) {
    const n = ring.length;
    if (kind === 'hip') {
        if (!_isConvex(ring)) return false;
        let cx = 0, cy = 0;
        for (const [x, y] of ring) { cx += x; cy += y; }
        const base = pos.n / 3;
        for (const [x, y] of ring) pos.push3(x * k, eave, y * k);
        pos.push3(cx / n * k, eave + roofMm, cy / n * k);
        for (let i = 0; i < n; i++) idx.push3(base + i, base + (i + 1) % n, base + n);
        return true;
    }
    // Gable: ridge along the outline's long axis, through the middle of its width.
    let mx = 0, my = 0;
    for (const [x, y] of ring) { mx += x; my += y; }
    mx /= n; my /= n;
    let sxx = 0, syy = 0, sxy = 0;
    for (const [x, y] of ring) { sxx += (x - mx) ** 2; syy += (y - my) ** 2; sxy += (x - mx) * (y - my); }
    const th = 0.5 * Math.atan2(2 * sxy, sxx - syy);
    const vx = -Math.sin(th), vy = Math.cos(th);               // across the ridge
    let lo = Infinity, hi = -Infinity;
    for (const [x, y] of ring) { const v = x * vx + y * vy; if (v < lo) lo = v; if (v > hi) hi = v; }
    const vc = (lo + hi) / 2, hw = (hi - lo) / 2;
    if (!(hw > 1e-9)) return false;
    const pt = ([x, y]) => ({ x, y, d: x * vx + y * vy - vc });
    const z = (p) => eave + roofMm * (1 - Math.min(Math.abs(p.d) / hw, 1));
    const emit = (a, b, c) => {
        const w = pos.n / 3;
        for (const p of [a, b, c]) pos.push3(p.x * k, z(p), p.y * k);
        idx.push3(w, w + 1, w + 2);
    };
    const cut = (a, b) => {                                    // where edge a-b meets the ridge
        const t = a.d / (a.d - b.d);
        return { x: a.x + t * (b.x - a.x), y: a.y + t * (b.y - a.y), d: 0 };
    };
    for (const [ia, ib, ic] of tris) {
        const t = [pt(ring[ia]), pt(ring[ib]), pt(ring[ic])];
        const pos_ = t.filter((p) => p.d > 1e-9), neg = t.filter((p) => p.d < -1e-9);
        if (!pos_.length || !neg.length) { emit(...t); continue; }
        const lone = pos_.length === 1 ? pos_[0] : neg[0];
        const [b, c] = t.filter((p) => p !== lone);
        const ab = cut(lone, b), ac = cut(lone, c);
        emit(lone, ab, ac); emit(ab, b, c); emit(ab, c, ac);
    }
    // Gable ends: close each edge crossing the ridge between the eave line and the roof.
    for (let i = 0; i < n; i++) {
        const a = pt(ring[i]), b = pt(ring[(i + 1) % n]);
        if (a.d * b.d >= 0) continue;
        const m = cut(a, b);
        const w = pos.n / 3;
        pos.push3(a.x * k, eave, a.y * k); pos.push3(m.x * k, z(m), m.y * k); pos.push3(b.x * k, eave, b.y * k);
        idx.push3(w, w + 1, w + 2);
    }
    return true;
}

function _outerRings(geom) {
    if (!geom) return [];
    if (geom.type === 'Polygon') return [geom.coordinates];
    if (geom.type === 'MultiPolygon') return geom.coordinates;
    return [];
}

/** Wall floor sink below the lowest ground: city2stl/city_model.py build_layer (floor = lowest ground - 0.2 mm). */
export const WALL_SINK_MM = 0.2;
const MAX_SAMPLES_PER_AXIS = 48;

/**
 * Lowest and highest terrain (mm) under a footprint ring: sampled at the corners, along every
 * edge (about one per preview pixel) and on a grid of pixels inside, so terrain between the
 * corners cannot sit below the wall foot (the finished model reads every cell under it,
 * Terrain::ranges_under).
 * @param {Array<[number, number]>} ring  outer ring in preview pixels
 * @param {Function} groundMm             (x, y) → mm
 * @returns {{low: number, high: number}}
 */
export function groundRange(ring, groundMm) {
    let low = Infinity, high = -Infinity;
    const take = (x, y) => {
        const g = groundMm(x, y);
        if (g < low) low = g;
        if (g > high) high = g;
    };
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    const n = ring.length;
    for (let i = 0; i < n; i++) {
        const [ax, ay] = ring[i], [bx, by] = ring[(i + 1) % n];
        take(ax, ay);
        const steps = Math.min(MAX_SAMPLES_PER_AXIS * 4, Math.ceil(Math.max(Math.abs(bx - ax), Math.abs(by - ay))));
        for (let s = 1; s < steps; s++) take(ax + (bx - ax) * s / steps, ay + (by - ay) * s / steps);
        if (ax < x0) x0 = ax;
        if (ax > x1) x1 = ax;
        if (ay < y0) y0 = ay;
        if (ay > y1) y1 = ay;
    }
    if (x1 - x0 >= 1 && y1 - y0 >= 1) {
        const nx = Math.min(MAX_SAMPLES_PER_AXIS, Math.ceil(x1 - x0)), ny = Math.min(MAX_SAMPLES_PER_AXIS, Math.ceil(y1 - y0));
        for (let j = 0; j < ny; j++) {
            const y = y0 + (j + 0.5) * (y1 - y0) / ny;
            for (let i = 0; i < nx; i++) {
                const x = x0 + (i + 0.5) * (x1 - x0) / nx;
                if (_inside(ring, x, y)) take(x, y);
            }
        }
    }
    return { low, high };
}

function _inside(ring, x, y) {
    let c = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [xi, yi] = ring[i], [xj, yj] = ring[j];
        if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) c = !c;
    }
    return c;
}

/**
 * Prism geometry for building footprints in model millimetres.
 *
 * @param {Array}    features      GeoJSON building features (height_m in properties)
 * @param {Object}   o
 * @param {Function} o.toPx        (lat, lon) → {x, y}: preview column / row (pixel centres)
 * @param {Function} o.groundMm    (x, y) → terrain top in mm at that preview pixel
 * @param {number}   o.zMmPerM     vertical mm per metre (ModelScale.z_mm_per_m)
 * @param {number}   o.mmPerPx     horizontal mm per preview pixel
 * @param {number}   [o.multiplier] Building height × (cityLayer_buildings_value)
 * @param {Function} o.triangulate (contour [[x,y]...], holes [[[x,y]...]]) → [[a,b,c]...]
 * @returns {{positions: Float32Array, indices: Uint32Array, count: number}}
 *   positions are [x_mm, z_mm (up), y_mm]; x/y from the preview's pixel (0,0) corner.
 */
export function buildingPrisms(features, o) {
    // Typed arrays grown by doubling: plain arrays of numbers cost 2 s and 0.85 s of
    // garbage collection for Philadelphia's 40 k buildings.
    const pos = new Grow(Float32Array, 1 << 16);
    const idx = new Grow(Uint32Array, 1 << 16);
    const mult = o.multiplier ?? 1, k = o.mmPerPx;
    let count = 0;
    for (const f of features || []) {
        const hM = Number(f?.properties?.height_m) || DEFAULT_HEIGHT_M;
        const heightMm = Math.max(hM * o.zMmPerM * mult, MIN_HEIGHT_MM);
        const kind = roofKind(f?.properties);
        for (const rings of _outerRings(f?.geometry)) {
            const pts = [];
            for (const ring of rings) {
                let n = ring.length;
                // GeoJSON rings repeat the first point at the end.
                if (n > 1 && ring[0][0] === ring[n - 1][0] && ring[0][1] === ring[n - 1][1]) n--;
                if (n < 3) continue;
                const out = new Array(n);
                for (let i = 0; i < n; i++) { const p = o.toPx(ring[i][1], ring[i][0]); out[i] = [p.x, p.y]; }
                pts.push(out);
            }
            if (!pts.length) continue;
            const { high: ground, low } = groundRange(pts[0], o.groundMm);
            const tris = o.triangulate(pts[0], pts.slice(1));
            if (!tris.length) continue;
            const top = ground + heightMm;
            const bottom = low - WALL_SINK_MM;  // walls reach the lowest ground under the footprint, less the finished model's sink
            let wallTop = top, pitched = false;
            if (kind && pts.length === 1) {
                const roofMm = roofHeightM(f.properties, hM) * o.zMmPerM * mult;
                if (roofMm > MIN_ROOF_MM && heightMm - roofMm > 0) {
                    wallTop = top - roofMm;    // the eave; the roof reaches `top`
                    pitched = _pitchedRoof(kind, pts[0], tris, k, wallTop, roofMm, pos, idx);
                    if (!pitched) wallTop = top;
                }
            }
            if (!pitched) {
                const roof0 = pos.n / 3;
                for (const ring of pts) for (const [x, y] of ring) pos.push3(x * k, top, y * k);
                for (const [a, b, c] of tris) idx.push3(roof0 + a, roof0 + b, roof0 + c);
            }
            for (const ring of pts) {
                for (let i = 0; i < ring.length; i++) {
                    const [x0, y0] = ring[i];
                    const [x1, y1] = ring[(i + 1) % ring.length];
                    const w = pos.n / 3;
                    pos.push3(x0 * k, wallTop, y0 * k); pos.push3(x1 * k, wallTop, y1 * k);
                    pos.push3(x1 * k, bottom, y1 * k); pos.push3(x0 * k, bottom, y0 * k);
                    idx.push3(w, w + 1, w + 2); idx.push3(w, w + 2, w + 3);
                }
            }
            count++;
        }
    }
    return { positions: pos.out(), indices: idx.out(), count };
}

/** A typed array that doubles when full. */
class Grow {
    constructor(T, n) { this.T = T; this.a = new T(n); this.n = 0; }
    push3(a, b, c) {
        if (this.n + 3 > this.a.length) { const t = new this.T(this.a.length * 2); t.set(this.a); this.a = t; }
        this.a[this.n++] = a; this.a[this.n++] = b; this.a[this.n++] = c;
    }
    out() { return this.a.slice(0, this.n); }
}

/**
 * Terrain top (mm) at a preview pixel from the loaded DEM grid, by the preview's
 * linear scale: base_mm + (elev - elev_min_m) × z_mm_per_m.
 *
 * @param {{values: ArrayLike<number>, width: number, height: number}} dem
 * @param {number} cols  preview columns (the DEM may be a different grid)
 * @param {number} rows  preview rows
 * @param {{z_mm_per_m: number, elev_min_m: number, base_mm: number, sea_level_cap?: boolean}} scale
 * @returns {(x: number, y: number) => number}
 */
export function demGroundMm(dem, cols, rows, scale) {
    const sx = dem.width / Math.max(cols, 1), sy = dem.height / Math.max(rows, 1);
    const values = dem.values?.__v_raw ?? dem.values;
    return (x, y) => {
        const c = Math.min(dem.width - 1, Math.max(0, Math.floor((x + 0.5) * sx)));
        const r = Math.min(dem.height - 1, Math.max(0, Math.floor((y + 0.5) * sy)));
        let e = Number(values[r * dem.width + c]);
        if (!Number.isFinite(e)) e = scale.elev_min_m;
        if (scale.sea_level_cap) e = Math.max(e, 0);
        return scale.base_mm + (e - scale.elev_min_m) * scale.z_mm_per_m;
    };
}
