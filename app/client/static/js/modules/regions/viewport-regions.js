/**
 * modules/regions/viewport-regions.js — which saved regions the Explore map draws
 * and the sidebar list shows. Pure (no DOM, no Leaflet); used by
 * region-boxes.js::refreshRegionViewSet for both, so the map and the list cannot
 * drift apart. Tested in tests/js/viewportRegions.test.js.
 *
 * The rule:
 *   - candidates intersect the view AND fit in it (box area <= view area), so a
 *     box much bigger than the window is not drawn and zooming in reveals the
 *     smaller regions that now fit;
 *   - largest first, at most `limit` of them (the world view shows the biggest);
 *   - the selected region is always included and does not count against `limit`.
 *
 * Exports: selectViewportRegions(regions, view, selectedName, limit)
 */

import { bboxSizeKm } from './region-geometry.js';

export const VIEWPORT_REGION_LIMIT = 20;

/** Longitude overlap of [w1, e1] and [w2, e2], allowing the view to be shifted by ±360°. */
function _lonOverlaps(region, view) {
    if (view.east - view.west >= 360) return true;
    for (const shift of [-360, 0, 360]) {
        if (region.west <= view.east + shift && region.east >= view.west + shift) return true;
    }
    return false;
}

function _intersects(region, view) {
    return region.south <= view.north && region.north >= view.south && _lonOverlaps(region, view);
}

function _viewAreaKm2(view) {
    return bboxSizeKm({
        north: Math.min(85, view.north), south: Math.max(-85, view.south),
        west: 0, east: Math.min(360, view.east - view.west),
    }).areaKm2;
}

/**
 * @param {Array<{name:string, north:number, south:number, east:number, west:number}>} regions
 * @param {{north:number, south:number, east:number, west:number}|null} view
 *   The map's visible bounds (west/east may run past ±180 on a wrapped map);
 *   null when there is no map yet, treated as the whole world.
 * @param {string|null} [selectedName]
 * @param {number} [limit=VIEWPORT_REGION_LIMIT]
 * @returns {{regions: Array, inViewCount: number, shownInView: number, total: number}}
 *   `regions` largest first (selected appended when it did not qualify);
 *   `inViewCount` = candidates before the limit; `shownInView` = how many of
 *   `regions` are candidates (the selected extra is not); `total` = regions given.
 */
export function selectViewportRegions(regions, view, selectedName = null, limit = VIEWPORT_REGION_LIMIT) {
    const world = { north: 85, south: -85, west: -180, east: 180 };
    const v = view || world;
    const viewArea = _viewAreaKm2(v);
    const withArea = regions.map((r) => ({ r, area: bboxSizeKm(r).areaKm2 }));
    const candidates = withArea
        .filter(({ r, area }) => area <= viewArea && _intersects(r, v))
        .sort((a, b) => b.area - a.area);
    const shown = candidates.slice(0, limit).map(({ r }) => r);
    const shownInView = shown.length;
    if (selectedName && !shown.some((r) => r.name === selectedName)) {
        const sel = regions.find((r) => r.name === selectedName);
        if (sel) shown.push(sel);
    }
    return { regions: shown, inViewCount: candidates.length, shownInView, total: regions.length };
}
