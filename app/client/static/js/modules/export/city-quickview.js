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
 * Pure: the geometry builder takes its inputs as arguments (vitest imports it).
 */

/** The City Model never prints a building thinner than this (style.min_height_mm). */
export const MIN_HEIGHT_MM = 0.4;
/** Height for a building without height_m, as city_model's OSM fallback. */
export const DEFAULT_HEIGHT_M = 10;

function _outerRings(geom) {
    if (!geom) return [];
    if (geom.type === 'Polygon') return [geom.coordinates];
    if (geom.type === 'MultiPolygon') return geom.coordinates;
    return [];
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
            let ground = -Infinity, low = Infinity;
            for (const [x, y] of pts[0]) {
                const g = o.groundMm(x, y);
                if (g > ground) ground = g;
                if (g < low) low = g;
            }
            const tris = o.triangulate(pts[0], pts.slice(1));
            if (!tris.length) continue;
            const top = ground + heightMm;
            const bottom = low - 0.1;          // walls reach the lowest ground under the footprint
            const roof0 = pos.n / 3;
            for (const ring of pts) for (const [x, y] of ring) pos.push3(x * k, top, y * k);
            for (const [a, b, c] of tris) idx.push3(roof0 + a, roof0 + b, roof0 + c);
            for (const ring of pts) {
                for (let i = 0; i < ring.length; i++) {
                    const [x0, y0] = ring[i];
                    const [x1, y1] = ring[(i + 1) % ring.length];
                    const w = pos.n / 3;
                    pos.push3(x0 * k, top, y0 * k); pos.push3(x1 * k, top, y1 * k);
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
