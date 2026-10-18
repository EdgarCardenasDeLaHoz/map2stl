/**
 * hydrology-print.js — one source of truth for the river settings, shared by
 * the hydrology preview (water-hydrology-combined.js) and
 * the Composite panel (composite-dem.js).
 *
 * The river dataset, minimum Strahler order and width multiplier live in
 * Fetch > Hydrology (#hydroSource, #hydroMinOrder, #hydroWidthFactor). The
 * Composite panel keeps only the 3D-only Depth × (#compositeRiverDepthScale).
 *
 * "Preview = print": GET /api/terrain/hydrology with `dem_source` returns the
 * exact river carve the composite / export applies on that DEM (same grid,
 * same projection), so the preview needs the loaded DEM's request snapshot.
 *
 * Pure helpers (no window access except through the arguments), so vitest can
 * import them directly.
 */

import { bboxLongestSideKm } from '../export/print-scale.js';

/**
 * Longest ground side (km) from which a region counts as large for rivers: smooth river
 * channels turn on ('auto'), and loading warns it is slow. Same value as
 * app/server/core/export_params.py::VECTOR_RIVERS_AUTO_MIN_SIDE_KM.
 */
export const LARGE_RIVER_REGION_KM = 200;

/** True when the bbox's longest ground side is at least LARGE_RIVER_REGION_KM. */
export function isLargeRiverRegion(bbox) {
    if (!bbox) return false;
    // Coordinates may arrive as form strings ("12.5"): `+` would concatenate them.
    const [north, south, east, west] = [bbox.north, bbox.south, bbox.east, bbox.west].map(Number);
    if (![north, south, east, west].every(Number.isFinite)) return false;
    return bboxLongestSideKm({ north, south, east, west }) >= LARGE_RIVER_REGION_KM;
}

/**
 * What the Rivers & lakes row and the Fetch status line say while the rivers load.
 * HydroRIVERS at size 1 on the Amazon (1.23M reaches) took 57 s, so a large region
 * says so; size 1–2 there is the slow case.
 *
 * @param {{north:number,south:number,east:number,west:number}|null} bbox
 * @param {number} [minOrder]  #hydroMinOrder
 */
export function riverLoadingMessage(bbox, minOrder) {
    if (!isLargeRiverRegion(bbox)) return 'Loading rivers…';
    return Number.isFinite(minOrder) && minOrder <= 2
        ? 'Loading rivers… large region, about a minute'
        : 'Loading rivers… large region, may take a while';
}

/** Fetch > Hydrology `hydroSource` value → composite layer source name. */
export const HYDRO_TO_RIVER_SOURCE = Object.freeze({
    natural_earth: 'natural_earth_rivers',
    hydrorivers: 'hydrorivers',
});

/**
 * @param {string} hydroSource  'natural_earth' | 'hydrorivers'
 * @returns {'natural_earth_rivers'|'hydrorivers'}
 */
export function riverSourceFromHydro(hydroSource) {
    return HYDRO_TO_RIVER_SOURCE[hydroSource] || 'hydrorivers';
}

/**
 * Read the Fetch > Hydrology river controls, with the markup defaults when a
 * control is missing or empty.
 *
 * @param {Document} [doc]
 * @returns {{hydroSource:string, riverSource:string, minOrder:number, widthScale:number}}
 */
export function readHydrologyRiverControls(doc = globalThis.document) {
    const val = id => doc?.getElementById?.(id)?.value;
    const hydroSource = val('hydroSource') || 'hydrorivers';
    const mo = parseInt(val('hydroMinOrder'), 10);
    const ws = parseFloat(val('hydroWidthFactor'));
    return {
        hydroSource,
        riverSource: riverSourceFromHydro(hydroSource),
        minOrder: Number.isFinite(mo) ? Math.max(1, Math.min(9, mo)) : 3,
        widthScale: Number.isFinite(ws) && ws > 0 ? ws : 1,
    };
}

/**
 * The grid the hydrology preview is carved on: the loaded DEM's request
 * snapshot (the same one composite-dem.js and the export use), or null when no
 * DEM has been loaded yet.
 *
 * @param {Object} appState  window.appState
 * @param {Document} [doc]   fallback for a snapshot without dim / source
 * @returns {{dim:number, demSource:string}|null}
 */
export function loadedDemGrid(appState, doc = globalThis.document) {
    const snapshot = appState?.lastDemRequest?.dem;
    if (!snapshot || !appState?.lastDemData) return null;
    const dim = parseInt(snapshot.dim ?? doc?.getElementById?.('paramDim')?.value, 10) || 600;
    const demSource = snapshot.dem_source
        || doc?.getElementById?.('paramDemSource')?.value || 'local';
    return { dim, demSource };
}

/**
 * The grid the next loadDEM() will fetch (its dim and dem_source, read from the form the same
 * way dem-main.js loadDEM does), so the hydrology can be requested before that DEM lands. Equal
 * to loadedDemGrid() once it has, so the two requests share one in-flight key.
 *
 * @param {Document} [doc]
 * @returns {{dim:number, demSource:string}}
 */
export function nextDemGrid(doc = globalThis.document) {
    const dim = parseInt(doc?.getElementById?.('paramDim')?.value, 10) || 600;
    const demSource = doc?.getElementById?.('paramDemSource')?.value || 'local';
    return { dim, demSource };
}

/**
 * Query for GET /api/terrain/hydrology in print mode.
 *
 * @param {Object} o
 * @param {{north:number,south:number,east:number,west:number}} o.coords
 * @param {{dim:number, demSource:string}} o.grid        from loadedDemGrid()
 * @param {{hydroSource:string, minOrder:number, widthScale:number}} o.river
 * @param {number} [o.depthScale=1]                     Composite Depth ×
 * @param {string} [o.projection='none']
 * @param {boolean} [o.maintainDimensions=false]
 * @param {boolean} [o.clipValidRegion=false]
 * @returns {URLSearchParams}
 */
export function hydrologyPrintQuery({
    coords, grid, river, depthScale = 1,
    projection = 'none', maintainDimensions = false, clipValidRegion = false,
}) {
    const { north, south, east, west } = coords;
    const q = new URLSearchParams({
        north, south, east, west,
        dim: grid.dim,
        dem_source: grid.demSource,
        source: river.hydroSource,
        min_order: river.minOrder,
        width_scale: river.widthScale,
        depth_scale: Number.isFinite(depthScale) ? depthScale : 1,
    });
    if (projection && projection !== 'none') {
        q.append('projection', projection);
        q.append('maintain_dimensions', maintainDimensions ? 'true' : 'false');
        q.append('clip_valid_region', clipValidRegion ? 'true' : 'false');
    }
    return q;
}

/** Composite Depth × (#compositeRiverDepthScale), default 1. */
export function readRiverDepthScale(doc = globalThis.document) {
    const v = parseFloat(doc?.getElementById?.('compositeRiverDepthScale')?.value);
    return Number.isFinite(v) ? v : 1;
}

/**
 * Print depth of the deepest river or lake carve, mm (#compositeRiverDepthMm), default 0.5:
 * the export scales the whole carve to it, keeping the ratios between rivers and lakes.
 */
export function readRiverDepthMm(doc = globalThis.document) {
    const v = parseFloat(doc?.getElementById?.('compositeRiverDepthMm')?.value);
    return Number.isFinite(v) && v > 0 ? v : 0.5;
}

/**
 * Smooth river channels (#compositeVectorRivers): the export request's `vector_rivers`,
 * 'auto' (the server turns it on for regions 200 km and larger), true or false.
 * Vector channels are cut as solids with smooth walls instead of the pixel-stepped
 * raster carve; they cost faces and export time. Previews keep the raster carve.
 */
export function readVectorRivers(doc = globalThis.document) {
    const v = doc?.getElementById?.('compositeVectorRivers')?.value;
    return v === 'on' ? true : v === 'off' ? false : 'auto';
}

/** Smooth channels: reaches shallower than this on the print are left out, mm
 *  (#compositeVectorRiversMinDepthMm), default 0.1. */
export function readVectorRiversMinDepthMm(doc = globalThis.document) {
    const v = parseFloat(doc?.getElementById?.('compositeVectorRiversMinDepthMm')?.value);
    return Number.isFinite(v) && v >= 0 ? v : 0.1;
}

/** Status / toast text when the preview is asked for before a DEM exists. */
export const NEED_DEM_MESSAGE = 'Load the DEM first — the hydrology preview is carved on it';
