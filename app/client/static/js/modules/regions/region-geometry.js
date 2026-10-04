/**
 * modules/regions/region-geometry.js — pure helpers for region boxes.
 *
 * No DOM, no Leaflet, no window: imported by region-editor.js, region-boxes.js,
 * viewport-regions.js and map/bbox-panel.js,
 * and unit-tested in tests/js/regionGeometry.test.js.
 *
 * Exports:
 *   bboxSizeKm(b)                      — {widthKm, heightKm, areaKm2} of a lat/lon box
 *   formatBboxSize(size)               — "12.4 × 8.1 km · 100 km²"
 *   formatBboxDims(b)                  — "12.4 × 8.1 km" for a box (header pill, list, map card)
 *   boxAround(lat, lon, widthKm, aspect) — a box of that ground width centred on a point
 *   placeBox(place, aspect)            — the box to suggest for a searched place
 *   parseBbox(n, s, e, w)              — numbers from input strings, or null if invalid
 *   regionBoxStyle(state)              — Leaflet path options for a saved-region box
 *   regionHaloStyle(state)             — the dark halo drawn under it
 */

const KM_PER_DEG_LAT = 110.574;
const KM_PER_DEG_LON_EQUATOR = 111.32;

/**
 * Width, height and area of a {north, south, east, west} box in kilometres.
 * Width is measured at the box's middle latitude (good to a few percent for the
 * box sizes the app saves). A box crossing the antimeridian (west > east) wraps.
 * @param {{north:number, south:number, east:number, west:number}} b
 * @returns {{widthKm:number, heightKm:number, areaKm2:number}}
 */
export function bboxSizeKm(b) {
    const north = Math.min(90, b.north);
    const south = Math.max(-90, b.south);
    let dLon = b.east - b.west;
    if (dLon < 0) dLon += 360;
    dLon = Math.min(dLon, 360);
    const midLat = ((north + south) / 2) * Math.PI / 180;
    const widthKm = Math.abs(dLon * KM_PER_DEG_LON_EQUATOR * Math.cos(midLat));
    const heightKm = Math.abs(north - south) * KM_PER_DEG_LAT;
    return { widthKm, heightKm, areaKm2: widthKm * heightKm };
}

function _fmt(v) {
    if (v < 100) return v.toFixed(1);
    if (v < 1000) return String(Math.round(v));
    return Math.round(v).toLocaleString('en-US');
}

/**
 * Human-readable size: "12.4 × 8.1 km · 100 km²".
 * @param {{widthKm:number, heightKm:number, areaKm2:number}} size
 * @returns {string}
 */
export function formatBboxSize(size) {
    return `${_dims(size)} · ${_fmt(size.areaKm2)} km²`;
}

function _dims(size) {
    return `${_fmt(size.widthKm)} × ${_fmt(size.heightKm)} km`;
}

/**
 * Width × height of a region box: "12.4 × 8.1 km". '' when an edge is not a number.
 * @param {{north:number, south:number, east:number, west:number}|null} b
 * @returns {string}
 */
export function formatBboxDims(b) {
    if (!b || ![b.north, b.south, b.east, b.west].every(Number.isFinite)) return '';
    return _dims(bboxSizeKm(b));
}

/**
 * Parse four coordinate strings (or numbers) into a box.
 * @returns {{north:number, south:number, east:number, west:number}|null}
 *   null when a value is missing, out of range, or north <= south.
 */
export function parseBbox(n, s, e, w) {
    const [north, south, east, west] = [n, s, e, w].map((v) => parseFloat(v));
    if ([north, south, east, west].some((v) => !Number.isFinite(v))) return null;
    if (north > 90 || south < -90 || north <= south) return null;
    if (Math.abs(east) > 180 || Math.abs(west) > 180) return null;
    return { north, south, east, west };
}

/** Accent colour of the selected region (matches the sidebar's selected row). */
export const REGION_ACCENT = '#4a9eff';

/**
 * Region colours (user 2026-10-04: "introduce colors again, and add the color to the table").
 * Bright hues that read over the dark halo on both satellite and street tiles; the blue of
 * REGION_ACCENT is left out so the box being edited stays distinct.
 */
export const REGION_PALETTE = [
    '#ff6b6b', '#ffa94d', '#ffd43b', '#a9e34b', '#51cf66',
    '#38d9a9', '#3bc9db', '#9775fa', '#da77f2', '#f783ac',
];

/**
 * A region's colour, the same on the map and in the list: picked from REGION_PALETTE by
 * its name (FNV-1a hash), so it stays the same across reloads and when regions are added.
 * @param {string} name
 * @returns {string}
 */
export function regionColor(name) {
    let h = 0x811c9dc5;
    for (const ch of String(name ?? '')) {
        h ^= ch.codePointAt(0);
        h = Math.imul(h, 0x01000193) >>> 0;
    }
    return REGION_PALETTE[h % REGION_PALETTE.length];
}

/**
 * Regions in drawing order: largest first, so a small box is drawn over a large one and
 * takes the clicks inside it (user 2026-10-04). The selected region gets no special place:
 * putting it on top let a large selected box swallow the small ones inside it.
 * @param {Array<{north:number, south:number, east:number, west:number}>} regions
 * @returns {Array} a sorted copy
 */
export function regionDrawOrder(regions) {
    return [...regions].sort((a, b) => bboxSizeKm(b).areaKm2 - bboxSizeKm(a).areaKm2);
}

/**
 * Leaflet path options for a saved-region rectangle, outlined in the region's colour.
 *
 * Boxes are outlines; only the selected one is tinted. `fill` stays true with zero opacity
 * so the interior still takes clicks and hovers (SVG only hit-tests painted areas, and a
 * zero-opacity fill counts as painted; `fill: false` would leave only the stroke clickable).
 * Filled boxes for every region tinted the whole map (2026-10-01), hence outlines.
 * @param {'normal'|'hover'|'selected'} state
 * @param {string} [color] the region's colour (regionColor)
 * @returns {object}
 */
export function regionBoxStyle(state, color = '#ffffff') {
    switch (state) {
        case 'selected':
            return { color, weight: 3.5, opacity: 1, fill: true, fillColor: color, fillOpacity: 0.14 };
        case 'hover':
            return { color, weight: 2.5, opacity: 1, fill: true, fillColor: color, fillOpacity: 0.08 };
        default:
            return { color, weight: 1.5, opacity: 0.85, fill: true, fillColor: color, fillOpacity: 0 };
    }
}

/**
 * The dark halo drawn under each box (a second, non-interactive rectangle 2 px
 * wider), so a light outline still reads on light tiles such as OpenStreetMap.
 * @param {'normal'|'hover'|'selected'} state
 * @returns {object}
 */
export function regionHaloStyle(state) {
    const { weight } = regionBoxStyle(state);
    return { color: '#000000', weight: weight + 2, opacity: 0.45, fill: false, interactive: false };
}

/**
 * A box widthKm wide and widthKm / aspect tall (ground km), centred on lat/lon.
 * @param {number} lat
 * @param {number} lon
 * @param {number} widthKm
 * @param {number} [aspect=1] width / height
 * @returns {{north:number, south:number, east:number, west:number}}
 */
export function boxAround(lat, lon, widthKm, aspect = 1) {
    const heightKm = widthKm / (aspect > 0 ? aspect : 1);
    const dLat = heightKm / KM_PER_DEG_LAT / 2;
    const dLon = widthKm / (KM_PER_DEG_LON_EQUATOR * Math.max(0.01, Math.cos(lat * Math.PI / 180))) / 2;
    return { north: lat + dLat, south: lat - dLat, east: lon + dLon, west: lon - dLon };
}

/** Ground width of the box suggested around a point place (a peak, a monument). */
export const POINT_PLACE_KM = 12;

/**
 * The box to suggest for a searched place (new-region flow): the place's own outline when
 * it has one at least 1 km across (a town, a park, a lake), else a POINT_PLACE_KM-wide box
 * around it shaped like the printer bed (a peak's outline is a point).
 * @param {{lat:number, lon:number, bbox?:{north:number, south:number, east:number, west:number}|null}} place
 * @param {number} [aspect=1] bed width / height
 * @returns {{north:number, south:number, east:number, west:number}}
 */
export function placeBox(place, aspect = 1) {
    const b = place.bbox;
    if (b && [b.north, b.south, b.east, b.west].every(Number.isFinite)) {
        const { widthKm, heightKm } = bboxSizeKm(b);
        if (Math.max(widthKm, heightKm) >= 1) return { north: b.north, south: b.south, east: b.east, west: b.west };
    }
    return boxAround(place.lat, place.lon, POINT_PLACE_KM, aspect);
}
