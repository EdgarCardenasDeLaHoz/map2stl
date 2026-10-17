/**
 * modules/dem/graticule.js — where a latitude/longitude lands in the projected height map.
 *
 * Pure (no DOM). Used by the Edit map's grids (stacked-layers.js drawLayerGrid) and the
 * height-map overlay (dem-gridlines.js). The rules MUST match geo2stl/projections.py:
 *   none                         linear in both axes
 *   cosine, equidistant          maintain_dimensions=false: an even resize, so linear;
 *                                true: each row is narrowed by cos(lat)/mean(cos), centred
 *   mercator, lambert, miller,   x linear; y = (f(N) - f(lat)) / (f(N) - f(S))
 *   gall
 *   sinusoidal                   x = 0.5 + (lon - centre)/(E - W) * cos(lat)/widest_cos,
 *                                widest_cos = max(|cos N|, |cos S|); y linear
 * Fractions are of the projected raster: x 0 = west edge, y 0 = north edge.
 */

const toRad = (d) => d * Math.PI / 180;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/** y = f(lat) for the cylindrical projections that stretch latitude. */
const CYLINDRICAL_Y = {
    mercator: (lat) => Math.log(Math.tan(Math.PI / 4 + toRad(clamp(lat, -85, 85)) / 2)),
    lambert: (lat) => Math.sin(toRad(lat)),
    miller: (lat) => 1.25 * Math.log(Math.tan(Math.PI / 4 + 0.4 * toRad(clamp(lat, -89.9, 89.9)))),
    gall: (lat) => (1 + Math.SQRT2 / 2) * Math.tan(toRad(clamp(lat, -89.9, 89.9)) / 2),
};

/**
 * Width / height at which the Edit map shows the height map.
 *
 * With a projection and "Keep fixed canvas shape" off, the server returns the raster in the
 * projection's own shape, so the raster's pixel aspect is the answer: letterboxing it to the
 * bbox's cosine shape squeezed every projection back into one frame, and switching
 * projections looked like nothing happened. Without a projection, or with the fixed shape,
 * the raster is the lat/lon grid and the cosine-corrected bbox shape is kept.
 *
 * @param {{north:number, south:number, east:number, west:number}} bbox
 * @param {{width?:number, height?:number}|null} dem  the loaded height map
 * @param {string} [projection='none']
 * @param {boolean} [maintainDimensions=false]
 * @returns {number}
 */
export function frameAspect(bbox, dem, projection = 'none', maintainDimensions = false) {
    if (projection !== 'none' && !maintainDimensions && dem?.width > 0 && dem?.height > 0) {
        return dem.width / dem.height;
    }
    const latMid = (bbox.north + bbox.south) / 2;
    return ((bbox.east - bbox.west) * Math.cos(toRad(latMid))) / ((bbox.north - bbox.south) || 1);
}

/**
 * Build the geo -> raster-fraction mapping for one region.
 * @param {{north:number, south:number, east:number, west:number}} bbox
 * @param {string} [projection='none']
 * @param {boolean} [maintainDimensions=false]  the "Keep fixed canvas shape" option
 * @returns {(lat:number, lon:number) => {x:number, y:number}}
 */
export function projectedFrac(bbox, projection = 'none', maintainDimensions = false) {
    const { north, south, east, west } = bbox;
    const lonRange = (east - west) || 1;
    const latRange = (north - south) || 1;
    const linX = (lon) => (lon - west) / lonRange;
    const linY = (lat) => (north - lat) / latRange;

    const f = CYLINDRICAL_Y[projection];
    if (f) {
        const fN = f(north);
        const fRange = (fN - f(south)) || 1;
        return (lat, lon) => ({ x: linX(lon), y: (fN - f(lat)) / fRange });
    }
    if ((projection === 'cosine' || projection === 'equidistant') && maintainDimensions) {
        // Mean of cos(lat) over the rows (the server averages every output row).
        let sum = 0;
        const n = 257;
        for (let k = 0; k < n; k++) sum += Math.cos(toRad(north - (k / (n - 1)) * latRange));
        const avgCos = (sum / n) || 1;
        return (lat, lon) => ({
            x: 0.5 + (linX(lon) - 0.5) * Math.cos(toRad(lat)) / avgCos,
            y: linY(lat),
        });
    }
    if (projection === 'sinusoidal') {
        const widest = Math.max(Math.abs(Math.cos(toRad(north))), Math.abs(Math.cos(toRad(south)))) || 1;
        const centre = (east + west) / 2;
        return (lat, lon) => ({
            x: 0.5 + (lon - centre) / lonRange * Math.cos(toRad(lat)) / widest,
            y: linY(lat),
        });
    }
    return (lat, lon) => ({ x: linX(lon), y: linY(lat) });
}

/** lat = f^-1(y) for CYLINDRICAL_Y (closed forms). */
const CYLINDRICAL_LAT = {
    mercator: (v) => Math.atan(Math.sinh(v)) * 180 / Math.PI,
    lambert: (v) => Math.asin(clamp(v, -1, 1)) * 180 / Math.PI,
    miller: (v) => (Math.atan(Math.exp(v / 1.25)) - Math.PI / 4) / 0.4 * 180 / Math.PI,
    gall: (v) => 2 * Math.atan(v / (1 + Math.SQRT2 / 2)) * 180 / Math.PI,
};

/**
 * Inverse of projectedFrac: raster fraction -> latitude/longitude. A point the projection
 * leaves empty (left or right of a sinusoidal / fixed-shape cosine row) gets a longitude
 * outside [west, east]; callers treat that as "no data".
 * @param {{north:number, south:number, east:number, west:number}} bbox
 * @param {string} [projection='none']
 * @param {boolean} [maintainDimensions=false]
 * @returns {(x:number, y:number) => {lat:number, lon:number}}
 */
export function geoAtFrac(bbox, projection = 'none', maintainDimensions = false) {
    const { north, south, east, west } = bbox;
    const lonRange = (east - west) || 1;
    const latRange = (north - south) || 1;
    const linLon = (x) => west + x * lonRange;
    const linLat = (y) => north - y * latRange;

    const f = CYLINDRICAL_Y[projection];
    if (f) {
        const inv = CYLINDRICAL_LAT[projection];
        const fN = f(north);
        const fRange = (fN - f(south)) || 1;
        return (x, y) => ({ lat: inv(fN - y * fRange), lon: linLon(x) });
    }
    if ((projection === 'cosine' || projection === 'equidistant') && maintainDimensions) {
        let sum = 0;
        const n = 257;
        for (let k = 0; k < n; k++) sum += Math.cos(toRad(north - (k / (n - 1)) * latRange));
        const avgCos = (sum / n) || 1;
        return (x, y) => {
            const lat = linLat(y);
            return { lat, lon: linLon(0.5 + (x - 0.5) * avgCos / (Math.cos(toRad(lat)) || 1e-9)) };
        };
    }
    if (projection === 'sinusoidal') {
        const widest = Math.max(Math.abs(Math.cos(toRad(north))), Math.abs(Math.cos(toRad(south)))) || 1;
        const centre = (east + west) / 2;
        return (x, y) => {
            const lat = linLat(y);
            return { lat, lon: centre + (x - 0.5) * lonRange * widest / (Math.cos(toRad(lat)) || 1e-9) };
        };
    }
    return (x, y) => ({ lat: linLat(y), lon: linLon(x) });
}

/**
 * Width / height of the server's projected raster for an equal-angle input. Mirrors
 * geo2stl/projections.py::expected_aspect_ratio (input_shape omitted, as the live DEM fetch
 * does). 'none' and the fixed canvas shape keep the plate-carree ratio.
 * @param {{north:number, south:number, east:number, west:number}} bbox
 * @param {string} [projection='none']
 * @param {boolean} [maintainDimensions=false]
 * @returns {number}
 */
export function projectedAspect(bbox, projection = 'none', maintainDimensions = false) {
    const { north, south, east, west } = bbox;
    const p = maintainDimensions ? 'none' : projection;
    const latSpan = north - south;
    const plate = latSpan ? (east - west) / latSpan : 1;
    const widthRad = toRad(east - west);
    const cylHeight = (f, n, s) => Math.abs(f(n) - f(s));
    let a;
    if (p === 'cosine' || p === 'equidistant') a = plate * Math.cos(toRad((north + south) / 2));
    else if (p === 'mercator') {
        a = widthRad / cylHeight(CYLINDRICAL_Y.mercator, Math.min(north, 85), Math.max(south, -85));
    } else if (p === 'lambert') a = widthRad / cylHeight(CYLINDRICAL_Y.lambert, north, south);
    else if (p === 'miller') a = widthRad / cylHeight(CYLINDRICAL_Y.miller, north, south);
    else if (p === 'gall') a = (widthRad / Math.SQRT2) / cylHeight(CYLINDRICAL_Y.gall, north, south);
    else if (p === 'sinusoidal') {
        const widest = Math.max(Math.abs(Math.cos(toRad(north))), Math.abs(Math.cos(toRad(south))));
        a = (widthRad * widest) / (toRad(latSpan) || 1);
    } else a = plate;
    return Number.isFinite(a) && a > 0 ? a : 1;
}

/**
 * The server's output size for a projection: `dim` is the longest side after projecting
 * (geo2stl/projections.py::projected_shape; Europa at 600: cosine 600 x 578, mercator
 * 573 x 600, sinusoidal 600 x 435). With target_dim the server never trims edge NaNs, so
 * clip_valid_region does not change the shape.
 * @param {{north:number, south:number, east:number, west:number}} bbox
 * @param {string} projection
 * @param {number} dim
 * @param {boolean} [maintainDimensions=false]
 * @returns {{width:number, height:number}}
 */
export function projectedShape(bbox, projection, dim, maintainDimensions = false) {
    const d = Math.max(1, Math.trunc(dim) || 1);
    const aspect = projectedAspect(bbox, projection, maintainDimensions);
    // Python's round() is half-to-even; the halves never occur for real boxes.
    if (aspect >= 1) return { width: d, height: Math.max(1, Math.round(d / aspect)) };
    return { width: Math.max(1, Math.round(d * aspect)), height: d };
}

/** True when the projection bends meridians (so a straight grid cannot show them). */
export function bendsMeridians(projection, maintainDimensions = false) {
    return projection === 'sinusoidal'
        || ((projection === 'cosine' || projection === 'equidistant') && !!maintainDimensions);
}

const DEGREE_STEPS = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 45, 90];

/**
 * A round step in degrees that cuts `span` degrees into at most `target` pieces.
 * @param {number} span
 * @param {number} [target=8]
 */
export function niceDegreeStep(span, target = 8) {
    const s = Math.abs(span);
    if (!(s > 0)) return DEGREE_STEPS[0];
    return DEGREE_STEPS.find((c) => s / c <= target) ?? DEGREE_STEPS[DEGREE_STEPS.length - 1];
}

/**
 * A degree label: "10°E", "45.5°N", "0°". Decimals follow the step so labels stay distinct.
 * @param {number} v
 * @param {boolean} isLat
 * @param {number} step
 */
export function formatDegree(v, isLat, step) {
    const dp = Number.isInteger(step) ? 0 : Math.min(4, (String(step).split('.')[1] || '').length);
    const r = Number(v.toFixed(dp));
    const hemi = r === 0 ? '' : isLat ? (r > 0 ? 'N' : 'S') : (r > 0 ? 'E' : 'W');
    return `${Math.abs(r).toFixed(dp)}°${hemi}`;
}

/** Multiples of `step` inside [lo, hi], rounded so 0.1 steps do not drift. */
function stepsWithin(lo, hi, step) {
    const out = [];
    const start = Math.ceil((lo - 1e-9) / step);
    const end = Math.floor((hi + 1e-9) / step);
    for (let i = start; i <= end && out.length < 1000; i++) out.push(Number((i * step).toFixed(9)));
    return out;
}

/**
 * The true graticule of a region: every meridian and parallel at a round step, as
 * polylines in raster fractions, sampled finely enough that bent lines stay smooth.
 * @param {{north:number, south:number, east:number, west:number}} bbox
 * @param {string} [projection='none']
 * @param {{maintainDimensions?:boolean, step?:number, target?:number, segments?:number}} [opts]
 * @returns {{step:number, lines:{kind:'meridian'|'parallel', value:number, label:string, pts:{x:number,y:number}[]}[]}}
 */
export function graticuleLines(bbox, projection = 'none', opts = {}) {
    const { maintainDimensions = false, target = 8, segments = 48 } = opts;
    const { north, south, east, west } = bbox;
    const step = opts.step || niceDegreeStep(Math.max(north - south, east - west), target);
    const at = projectedFrac(bbox, projection, maintainDimensions);
    const lines = [];
    for (const lon of stepsWithin(west, east, step)) {
        const pts = [];
        for (let k = 0; k <= segments; k++) pts.push(at(north - (k / segments) * (north - south), lon));
        lines.push({ kind: 'meridian', value: lon, label: formatDegree(lon, false, step), pts });
    }
    for (const lat of stepsWithin(south, north, step)) {
        const pts = [];
        for (let k = 0; k <= segments; k++) pts.push(at(lat, west + (k / segments) * (east - west)));
        lines.push({ kind: 'parallel', value: lat, label: formatDegree(lat, true, step), pts });
    }
    return { step, lines };
}
