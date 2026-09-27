/**
 * composite-spec.js — pure translation of the Composite panel's parameters
 * into the server's ordered layer list (MergeLayerSpec in app/server/schemas.py).
 *
 * No DOM, no window: composite-dem.js reads the panel state and passes it in,
 * and tests/js/compositeSpec.test.js exercises this directly.
 *
 * Two-stage mesh pipeline (docs/plans/F-ARCH-consolidation.md): every mesh is
 * a raster *terrain* stage (DEM, water depth, land cover, vegetation, trails)
 * followed by a vector *feature* stage (city2stl.city_model: OSM buildings,
 * roads, waterways, walls as 3D prisms/drapes/cuts). The rasterised OSM
 * channels below therefore belong only in the 2D composite preview. Feeding
 * them into the terrain heightfield prints every building twice — once as
 * pixel blocks, once as a vector prism — so the export/apply spec leaves them
 * out. In 3D those features come from the City Model layers (Extrude tab).
 */

/** Rasterised OSM feature channels: 2D preview only, never the mesh terrain. */
export const FEATURE_SOURCES = Object.freeze([
    'osm_buildings', 'osm_roads', 'osm_waterways', 'osm_walls',
]);

/**
 * Terrain-relative water sources (F-REGION, geo2stl/water_layers.py): each
 * returns negative metres below the ground on the base DEM grid, blended with
 * `add`. The mesh export carves them after its median filter. Unlike the
 * `osm_*` channels they are terrain, so they go in the export spec.
 */
export const RIVER_SOURCES = Object.freeze(['hydrorivers', 'natural_earth_rivers']);
export const WATER_TERRAIN_SOURCES = Object.freeze([...RIVER_SOURCES, 'lakes']);

/**
 * The river / lake layers for `params` (empty when both are off). Shared by
 * the spec builder and the 2D preview's server fetch.
 *
 * @param {Object} params  Composite panel parameters
 * @param {number} dim
 * @returns {Array<Object>}
 */
export function waterTerrainLayers(params, dim) {
    const layers = [];
    if (params.riversEnabled && params.riverDepthScale > 0) {
        const source = RIVER_SOURCES.includes(params.riverSource) ? params.riverSource : 'hydrorivers';
        layers.push({
            source, dim, blend_mode: 'add', weight: params.riverDepthScale,
            options: { min_order: params.riverMinOrder ?? 3, width_scale: params.riverWidthScale ?? 1 },
        });
    }
    if (params.lakesEnabled && params.lakeDepth > 0) {
        layers.push({
            source: 'lakes', dim, blend_mode: 'add', weight: 1,
            options: { depth_m: params.lakeDepth, min_area_m2: (params.lakeMinAreaHa ?? 1) * 10000 },
        });
    }
    return layers;
}

/**
 * Build the ordered server layer list for the composite panel.
 *
 * Each channel becomes one layer. `add` raises the terrain and `rivers`
 * subtracts from it; the per-channel depths and scales collapse into the
 * layer's single `weight`. The base layer comes first, because the server
 * takes the first layer as the grid every later layer is resized onto.
 *
 * Land cover, satellite vegetation and trails have no server-side source yet
 * (F-COMPOSITE3 passes 2 and 3). When one of those is on with a non-zero
 * weight it is reported in `unsupported`, and the caller falls back to
 * shipping the browser's terrain values inline rather than exporting a mesh
 * that quietly omits a channel the user enabled.
 *
 * @param {Object} params  Composite panel parameters (composite-dem.js DEFAULTS shape)
 * @param {{dim:number, demSource:string, detail?:string}} ctx
 * @param {{includeFeatures?:boolean}} [opts]  true only for the 2D preview;
 *        the default (false) is the terrain-only spec used for Apply/export.
 * @returns {{layers: Array<Object>, unsupported: string[]}}
 */
export function buildCompositeLayerSpec(params, ctx, { includeFeatures = false } = {}) {
    const { dim, demSource, detail = 'full' } = ctx;
    const layers = [];
    const unsupported = [];
    const push = (source, blendMode, weight, options) => {
        if (!(weight > 0)) return;
        layers.push({ source, dim, blend_mode: blendMode, weight, options: options || {} });
    };

    if (params.demEnabled && params.demWeight > 0) {
        push(demSource, 'base', params.demWeight, {});
    }
    // Water depth is a terrain modifier (lowers the ground under water), not
    // a vector feature, so it stays in the terrain stage.
    if (params.waterEnabled) {
        push('water_esa', 'rivers', params.waterDepth * params.waterWeight, {});
    }
    // Rivers and lakes: terrain-relative depth grids, always in the terrain spec.
    for (const l of waterTerrainLayers(params, dim)) push(l.source, l.blend_mode, l.weight, l.options);
    if (includeFeatures) {
        if (params.buildingsEnabled) push('osm_buildings', 'add', params.buildingScale, { detail });
        if (params.roadsEnabled) push('osm_roads', 'rivers', params.roadCut, { detail });
        if (params.waterwaysEnabled) push('osm_waterways', 'rivers', params.riverDepth, { detail });
        if (params.wallsEnabled) push('osm_walls', 'add', params.wallScale, { detail });
    }

    if (params.landcoverEnabled && params.landcoverWeight > 0) unsupported.push('land cover');
    if (params.satEnabled && params.satWeight > 0) unsupported.push('vegetation');
    if (params.trailsEnabled && params.trailsWeight > 0) unsupported.push('trails');

    return { layers, unsupported };
}

/** True when any rasterised OSM feature channel is switched on in `params`. */
export function anyFeatureChannelEnabled(params) {
    return !!(params.buildingsEnabled || params.roadsEnabled
        || params.waterwaysEnabled || params.wallsEnabled);
}

// ─── Apply-to-DEM guard ──────────────────────────────────────────────────────

/** Terrain range (m) at or below which a composite counts as flat. */
export const FLAT_COMPOSITE_RANGE_M = 1e-3;

/**
 * Identity of the inputs a composite was computed from: the loaded DEM (a
 * per-values-array token), its grid, the bbox and every panel parameter. Two
 * computes with equal keys produce the same terrain.
 *
 * @param {{demToken:(number|null), width:number, height:number,
 *          bbox:(Object|null), params:Object}} inputs
 * @returns {string}
 */
export function compositeInputKey({ demToken, width, height, bbox, params }) {
    const b = bbox ? [bbox.north, bbox.south, bbox.east, bbox.west] : null;
    const p = Object.keys(params || {}).sort().map((k) => [k, params[k]]);
    return JSON.stringify({ demToken: demToken ?? null, width, height, bbox: b, params: p });
}

/**
 * May Apply to DEM use this composite? Apply replaces the active DEM, so it
 * must never use a result computed for other inputs (e.g. the all-zero
 * baseline computed before the DEM loaded) or one that came out flat.
 *
 * @param {Object|null} result   What the last finished compute recorded:
 *   { key, usedDem, min, max } (null when nothing was computed)
 * @param {{key:string, hasDem:boolean}} current  The inputs as they are now
 * @returns {{ok:boolean, reason?:'no-dem'|'not-computed'|'stale'|'baseline'|'flat'}}
 */
export function compositeApplyCheck(result, current) {
    if (!current?.hasDem) return { ok: false, reason: 'no-dem' };
    if (!result) return { ok: false, reason: 'not-computed' };
    if (result.key !== current.key) return { ok: false, reason: 'stale' };
    if (!result.usedDem) return { ok: false, reason: 'baseline' };
    const range = result.max - result.min;
    if (!(Number.isFinite(range) && range > FLAT_COMPOSITE_RANGE_M)) return { ok: false, reason: 'flat' };
    return { ok: true };
}
