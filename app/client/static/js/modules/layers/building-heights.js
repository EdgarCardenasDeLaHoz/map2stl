/**
 * modules/layers/building-heights.js — pure summaries of OSM building heights.
 *
 * No DOM, no window: used by the Buildings panel (CityBuildingsPanel.vue) and
 * export-handlers.js, unit-tested in tests/js/buildingHeights.test.js.
 *
 * Each building feature carries properties.height_m and properties.height_source
 * (server: city2stl building height resolution). Sources seen in practice:
 * osm_tag, osm_levels, lidar_* (e.g. lidar_3dep_copc), merged or a raster
 * provider name (ndsm, wsf3d, ghsl, ...), and default (nothing known — a fixed
 * fallback height).
 */

/** Share of default-height buildings above which the panel warns. */
const DEFAULT_SHARE_WARN = 0.2;

/** Coarse group for a height_source value, in display order. */
const SOURCE_GROUPS = [
    { key: 'osm_tag', label: 'OSM height tag' },
    { key: 'osm_levels', label: 'OSM levels' },
    { key: 'lidar', label: 'Lidar' },
    { key: 'raster', label: 'Raster / model' },
    { key: 'default', label: 'Default height' },
    { key: 'unknown', label: 'Unknown' },
];

export function heightSourceGroup(source) {
    const s = String(source ?? '').trim().toLowerCase();
    if (!s) return 'unknown';
    if (s === 'osm_tag' || s === 'osm_height' || s === 'height') return 'osm_tag';
    if (s === 'osm_levels' || s.includes('levels')) return 'osm_levels';
    if (s.startsWith('lidar')) return 'lidar';
    if (s === 'default' || s.startsWith('default')) return 'default';
    return 'raster';
}

/** Finite number, or null — Number(null) and Number('') are 0, not missing. */
function _finite(v) {
    if (v == null || v === '') return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
}

function _height(feat) {
    return _finite(feat?.properties?.height_m);
}

/** Ceiling of a nice bin width (1, 2, 5 × 10^n) so ~binCount bins cover maxH. */
function _niceBinWidth(maxH, binCount) {
    const raw = Math.max(maxH, 1) / binCount;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const m of [1, 2, 5, 10]) if (m * mag >= raw) return m * mag;
    return 10 * mag;
}

/**
 * Summarise building heights.
 *
 * @param {Array} features   buildings FeatureCollection features
 * @param {Object} [opts]
 * @param {Object<number, number>} [opts.overrides]  index → height_m set by the user
 * @param {number} [opts.binCount=12]
 * @param {number} [opts.topN=10]
 * @returns {{total:number, withHeight:number,
 *            groups:Array<{key:string,label:string,count:number,share:number}>,
 *            sources:Array<{source:string,count:number}>,
 *            defaultCount:number, defaultShare:number, warnDefault:boolean,
 *            histogram:{binWidth:number, bins:Array<{lo:number,hi:number,count:number}>, maxCount:number},
 *            tallest:Array<{index:number, height:number, source:string, overridden:boolean}>}}
 */
export function summarizeBuildingHeights(features, { overrides = {}, binCount = 12, topN = 10 } = {}) {
    const list = Array.isArray(features) ? features : [];
    const groupCounts = Object.fromEntries(SOURCE_GROUPS.map(g => [g.key, 0]));
    const sourceCounts = new Map();
    const heights = [];

    list.forEach((feat, index) => {
        const raw = feat?.properties?.height_source;
        const source = String(raw ?? '') || 'unknown';
        sourceCounts.set(source, (sourceCounts.get(source) || 0) + 1);
        groupCounts[heightSourceGroup(raw)] += 1;
        const override = _finite(overrides?.[index]);
        const overridden = override != null;
        const h = overridden ? override : _height(feat);
        if (h != null) heights.push({ index, height: h, source, overridden });
    });

    const total = list.length;
    const groups = SOURCE_GROUPS
        .map(g => ({ ...g, count: groupCounts[g.key], share: total ? groupCounts[g.key] / total : 0 }))
        .filter(g => g.count > 0);
    const sources = [...sourceCounts.entries()]
        .map(([source, count]) => ({ source, count }))
        .sort((a, b) => b.count - a.count || a.source.localeCompare(b.source));
    const defaultCount = groupCounts.default;
    const defaultShare = total ? defaultCount / total : 0;

    const maxH = heights.reduce((m, h) => Math.max(m, h.height), 0);
    const binWidth = _niceBinWidth(maxH, binCount);
    const nBins = Math.max(1, Math.ceil(Math.max(maxH, 1e-9) / binWidth));
    const bins = Array.from({ length: nBins }, (_, i) => ({ lo: i * binWidth, hi: (i + 1) * binWidth, count: 0 }));
    for (const { height } of heights) {
        const i = Math.min(nBins - 1, Math.max(0, Math.floor(height / binWidth)));
        bins[i].count += 1;
    }

    const tallest = [...heights]
        .sort((a, b) => b.height - a.height || a.index - b.index)
        .slice(0, topN);

    return {
        total,
        withHeight: heights.length,
        groups,
        sources,
        defaultCount,
        defaultShare,
        warnDefault: defaultShare > DEFAULT_SHARE_WARN,
        histogram: { binWidth, bins, maxCount: bins.reduce((m, b) => Math.max(m, b.count), 0) },
        tallest,
    };
}

/**
 * A clean buildings FeatureCollection for the city build's `layer_data`, with
 * the user's height overrides applied. Client-only annotations (terrain_z,
 * _cityIndex, pre-baked pixel paths) are not sent.
 *
 * @param {Array} features
 * @param {Object<number, number>} overrides  index → height_m
 * @returns {{type:'FeatureCollection', features:Array}}
 */
export function buildingsWithOverrides(features, overrides = {}) {
    const out = (Array.isArray(features) ? features : []).map((feat, index) => {
        const properties = { ...(feat?.properties || {}) };
        delete properties.terrain_z;
        const h = _finite(overrides?.[index]);
        if (h != null) {
            properties.height_m = h;
            properties.height_source = 'user_override';
        }
        return { type: 'Feature', geometry: feat?.geometry ?? null, properties };
    });
    return { type: 'FeatureCollection', features: out };
}

/** True if at least one finite override is set. */
export function hasOverrides(overrides) {
    return Object.values(overrides || {}).some(v => _finite(v) != null);
}
