/**
 * Default projection for a region that has no saved settings.
 *
 * Sinusoidal shears a continent (Spain leans about 12 degrees on a Europe map), so a large
 * region defaults to the Lambert azimuthal equal-area projection. Smaller regions keep the
 * cosine default. A projection saved with the region always wins (the caller skips this).
 */
import { bboxLongestSideKm } from '../export/print-scale.js';

/** Longest ground side (km) from which a region defaults to `laea`. */
export const LARGE_PROJECTION_REGION_KM = 1000;

/** Default projection for a region without saved settings: `laea` when large, else `cosine`. */
export function defaultProjectionFor(bbox) {
    if (!bbox) return 'cosine';
    // Coordinates may arrive as form strings: `+` would concatenate them.
    const [north, south, east, west] = [bbox.north, bbox.south, bbox.east, bbox.west].map(Number);
    if (![north, south, east, west].every(Number.isFinite)) return 'cosine';
    return bboxLongestSideKm({ north, south, east, west }) >= LARGE_PROJECTION_REGION_KM
        ? 'laea' : 'cosine';
}

/**
 * Set #paramProjection to the default for `bbox` and fire `change`. Only moves the select
 * off a value this function (or the page default `cosine`) put there, so a choice the user
 * made in this session survives selecting another region.
 * @returns {string|null} the projection applied, or null when left alone
 */
export function applyDefaultProjection(bbox) {
    const el = document.getElementById('paramProjection');
    if (!el) return null;
    const want = defaultProjectionFor(bbox);
    const untouched = el.value === 'cosine' || el.dataset.autoProjection === el.value;
    if (!untouched || el.value === want) return null;
    el.value = want;
    el.dataset.autoProjection = want;
    el.dispatchEvent(new Event('change'));
    return want;
}
