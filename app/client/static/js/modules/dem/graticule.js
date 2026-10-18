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
 *   laea, lcc                    spherical Lambert azimuthal equal-area / conformal conic, centred
 *                                on the bbox centre; the raster is the bounding rectangle of the
 *                                projected bbox (see conicAzimuthalModel). lcc falls back to
 *                                equidistant when lccParameters(bbox) is null.
 * Fractions are of the projected raster: x 0 = west edge, y 0 = north edge.
 */

const toRad = (d) => d * Math.PI / 180;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

const MAX_LAT = 89.9;
const EXTENT_SAMPLES = 1001;
const tanHalf = (phi) => Math.tan(Math.PI / 4 + phi / 2);

/**
 * Standard parallels [lat1, lat2] (degrees) of the Lambert conformal conic: south + span/6 and
 * north - span/6. Null when the cone is undefined (bbox straddles the equator, or the parallels
 * coincide within 1e-6 degrees), where the server treats `lcc` as `equidistant`.
 * Mirrors geo2stl/projections.py::lcc_parameters.
 * @param {{north:number, south:number}} bbox
 * @returns {[number, number]|null}
 */
export function lccParameters(bbox) {
    const { north, south } = bbox;
    if (south < 0 && north > 0) return null;
    const span = north - south;
    const lat1 = south + span / 6;
    const lat2 = north - span / 6;
    if (Math.abs(lat2 - lat1) < 1e-6) return null;
    return [clamp(lat1, -MAX_LAT, MAX_LAT), clamp(lat2, -MAX_LAT, MAX_LAT)];
}

/** `lcc` is `equidistant` where its cone is undefined; every other name is unchanged. */
function effectiveProjection(bbox, projection) {
    return projection === 'lcc' && !lccParameters(bbox) ? 'equidistant' : projection;
}

/**
 * Spherical LAEA or LCC on the unit sphere (mirrors geo2stl/projections.py::_ConicAzimuthalModel).
 * forward(lat, lon) -> {x, y} (north up); inverse(x, y) -> {lat, lon} in degrees, or null for a
 * point off the sphere. extents = projected bounding rectangle of the bbox, which is the raster.
 * @param {{north:number, south:number, east:number, west:number}} bbox
 * @param {'laea'|'lcc'} projection  (lcc needs lccParameters(bbox) non-null)
 */
export function conicAzimuthalModel(bbox, projection) {
    const { north, south, east, west } = bbox;
    const lat0 = toRad((north + south) / 2);
    const lon0 = toRad((east + west) / 2);
    let forward, inverse;
    if (projection === 'laea') {
        const s0 = Math.sin(lat0), c0 = Math.cos(lat0);
        forward = (lat, lon) => {
            const phi = toRad(lat), dl = toRad(lon) - lon0;
            const k = Math.sqrt(2 / (1 + s0 * Math.sin(phi) + c0 * Math.cos(phi) * Math.cos(dl)));
            return { x: k * Math.cos(phi) * Math.sin(dl),
                     y: k * (c0 * Math.sin(phi) - s0 * Math.cos(phi) * Math.cos(dl)) };
        };
        inverse = (x, y) => {
            const rho = Math.hypot(x, y);
            if (rho > 2) return null;
            const c = 2 * Math.asin(rho / 2);
            const sinPhi = Math.cos(c) * s0 + (rho > 1e-15 ? y * Math.sin(c) * c0 / rho : 0);
            const lat = Math.asin(clamp(sinPhi, -1, 1));
            const lon = lon0 + Math.atan2(x * Math.sin(c), rho * c0 * Math.cos(c) - y * s0 * Math.sin(c));
            return { lat: lat * 180 / Math.PI, lon: lon * 180 / Math.PI };
        };
    } else {
        const [lat1, lat2] = lccParameters(bbox);
        const p1 = toRad(lat1), p2 = toRad(lat2);
        const n = Math.log(Math.cos(p1) / Math.cos(p2)) / Math.log(tanHalf(p2) / tanHalf(p1));
        const F = Math.cos(p1) * Math.pow(tanHalf(p1), n) / n;
        const rho0 = F / Math.pow(tanHalf(lat0), n);
        const sgn = Math.sign(n);
        forward = (lat, lon) => {
            const rho = F / Math.pow(tanHalf(toRad(clamp(lat, -MAX_LAT, MAX_LAT))), n);
            const theta = n * (toRad(lon) - lon0);
            return { x: rho * Math.sin(theta), y: rho0 - rho * Math.cos(theta) };
        };
        inverse = (x, y) => {
            const dy = rho0 - y;
            const rho = sgn * Math.hypot(x, dy);
            const theta = Math.atan2(sgn * x, sgn * dy);
            const lat = 2 * Math.atan(Math.pow(F / rho, 1 / n)) - Math.PI / 2;
            if (!Number.isFinite(lat)) return null;
            return { lat: lat * 180 / Math.PI, lon: (lon0 + theta / n) * 180 / Math.PI };
        };
    }
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    const take = (lat, lon) => {
        const p = forward(lat, lon);
        if (p.x < x0) x0 = p.x;
        if (p.x > x1) x1 = p.x;
        if (p.y < y0) y0 = p.y;
        if (p.y > y1) y1 = p.y;
    };
    for (let k = 0; k < EXTENT_SAMPLES; k++) {
        const t = k / (EXTENT_SAMPLES - 1);
        take(south + t * (north - south), west);
        take(south + t * (north - south), east);
        take(north, west + t * (east - west));
        take(south, west + t * (east - west));
    }
    return { forward, inverse, extents: { x0, x1, y0, y1 } };
}

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
    projection = effectiveProjection(bbox, projection);
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
    if (projection === 'laea' || projection === 'lcc') {
        const { forward, extents: e } = conicAzimuthalModel(bbox, projection);
        const w = (e.x1 - e.x0) || 1, h = (e.y1 - e.y0) || 1;
        return (lat, lon) => {
            const p = forward(lat, lon);
            return { x: (p.x - e.x0) / w, y: (e.y1 - p.y) / h };
        };
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
    projection = effectiveProjection(bbox, projection);
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
    if (projection === 'laea' || projection === 'lcc') {
        const { inverse, extents: e } = conicAzimuthalModel(bbox, projection);
        return (x, y) => {
            const g = inverse(e.x0 + x * (e.x1 - e.x0), e.y1 - y * (e.y1 - e.y0));
            // Off the sphere: a longitude outside [west, east] means "no data".
            return g || { lat: linLat(y), lon: west - lonRange };
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
    const p = maintainDimensions ? 'none' : effectiveProjection(bbox, projection);
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
    } else if (p === 'laea' || p === 'lcc') {
        const { x0, x1, y0, y1 } = conicAzimuthalModel(bbox, p).extents;
        a = y1 > y0 ? (x1 - x0) / (y1 - y0) : 1;
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

/**
 * True when the projection bends meridians (so a straight grid cannot show them).
 * Pass `bbox` so an `lcc` that falls back to `equidistant` is told apart from a real cone.
 */
export function bendsMeridians(projection, maintainDimensions = false, bbox = null) {
    if (projection === 'lcc' && bbox) projection = effectiveProjection(bbox, projection);
    return projection === 'sinusoidal' || projection === 'laea' || projection === 'lcc'
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
