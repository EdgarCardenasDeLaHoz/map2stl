/**
 * modules/map/landmarks.js — pure helpers for landmark search and the
 * POI-near-edge warning (F-UX batch 2).
 *
 * LandmarkSearch.vue calls GET /api/geocode and uses extendBboxToInclude() for
 * "extend the box to include it"; EdgeLandmarkWarnings.vue calls
 * GET /api/geocode/edge-landmarks and keys its memo with bboxKey().
 *
 * No window or DOM access here, so vitest imports it directly.
 */

const M_PER_DEG_LAT = 110574;
const M_PER_DEG_LON_EQ = 111320;

/**
 * {north, south, east, west} from a region, a plain bbox or a Leaflet
 * LatLngBounds / rectangle. Returns null when the input has no usable extent.
 * @param {object|null} src
 * @returns {{north:number, south:number, east:number, west:number}|null}
 */
export function toBbox(src) {
    if (!src) return null;
    const b = typeof src.getBounds === 'function' ? src.getBounds() : src;
    const box = typeof b.getNorth === 'function'
        ? { north: b.getNorth(), south: b.getSouth(), east: b.getEast(), west: b.getWest() }
        : { north: +b.north, south: +b.south, east: +b.east, west: +b.west };
    const ok = [box.north, box.south, box.east, box.west].every(Number.isFinite)
        && box.north > box.south && box.east > box.west;
    return ok ? box : null;
}

/**
 * Stable string for a bbox at *decimals* places (~11 m at 4), so an edit that
 * only nudges the box by float noise does not trigger another query.
 */
export function bboxKey(bbox, decimals = 4) {
    const b = toBbox(bbox);
    if (!b) return '';
    return [b.north, b.south, b.east, b.west].map(v => v.toFixed(decimals)).join(',');
}

function _mPerDegLon(lat) {
    return M_PER_DEG_LON_EQ * Math.cos((lat * Math.PI) / 180);
}

/**
 * The smallest box containing *bbox* and the search result *place*, plus a
 * margin of *marginM* metres around the place.
 *
 * The place's own extent is used when it is small (a building, a square: at most
 * *maxPlaceKm* across); a city or a country result contributes only its point,
 * or the region would jump to the size of Spain.
 *
 * @param {{north,south,east,west}} bbox
 * @param {{lat:number, lon:number, bbox?:{north,south,east,west}|null}} place
 * @param {{marginM?:number, maxPlaceKm?:number}} [opts]
 * @returns {{north,south,east,west}|null}
 */
export function extendBboxToInclude(bbox, place, { marginM = 100, maxPlaceKm = 3 } = {}) {
    const b = toBbox(bbox);
    if (!b || !place || !Number.isFinite(+place.lat) || !Number.isFinite(+place.lon)) return null;
    let target = { north: +place.lat, south: +place.lat, east: +place.lon, west: +place.lon };
    const pb = place.bbox ? toBbox(place.bbox) : null;
    if (pb) {
        const midLat = (pb.north + pb.south) / 2;
        const wKm = ((pb.east - pb.west) * _mPerDegLon(midLat)) / 1000;
        const hKm = ((pb.north - pb.south) * M_PER_DEG_LAT) / 1000;
        if (Math.hypot(wKm, hKm) <= maxPlaceKm) target = pb;
    }
    const dLat = marginM / M_PER_DEG_LAT;
    const dLon = marginM / Math.max(_mPerDegLon((target.north + target.south) / 2), 1e-6);
    return {
        north: Math.min(90, Math.max(b.north, target.north + dLat)),
        south: Math.max(-90, Math.min(b.south, target.south - dLat)),
        east: Math.min(180, Math.max(b.east, target.east + dLon)),
        west: Math.max(-180, Math.min(b.west, target.west - dLon)),
    };
}

/** True when the point lies inside the bbox (edges included). */
export function bboxContains(bbox, lat, lon) {
    const b = toBbox(bbox);
    return !!b && lat <= b.north && lat >= b.south && lon <= b.east && lon >= b.west;
}

/** "tourism · attraction" style caption for a search result. */
export function placeCaption(place) {
    return [place?.class, place?.type].filter(v => v && v !== 'yes').join(' · ');
}
