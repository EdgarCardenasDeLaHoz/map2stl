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
