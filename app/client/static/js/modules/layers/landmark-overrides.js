/**
 * modules/layers/landmark-overrides.js — pure helpers for landmark overrides
 * (F-LANDMARK §3/§5). No DOM, no window: used by the Landmarks panel
 * (CityLandmarksSection.vue) and the City Model request (export-handlers.js).
 *
 * An override is keyed by OSM id ("way/123") and is one of
 *   {kind: 'osm'}                                   the tag-driven building (default)
 *   {kind: 'ndsm', provider: 'auto'|name, resolution_m?}
 *   {kind: 'mesh', upload_id, fit?, rotation_deg?, scale?, offset_m?, vertical?, height_m?, up_axis?}
 * (server: city2stl/landmarks.py).
 */

const OVERRIDE_KINDS = ['osm', 'ndsm', 'mesh'];

export const CATEGORY_LABELS = {
    worship: 'Place of worship',
    civic: 'Town hall / civic',
    historic: 'Castle / palace / fort',
    attraction: 'Attraction / museum',
    tallest: 'Tallest',
};

/**
 * The overrides worth sending with the City Model build: kind 'osm' and
 * malformed entries dropped. Returns null when nothing is left.
 *
 * @param {Object<string, Object>} overrides  osm_id → spec
 * @returns {Object<string, Object>|null}
 */
export function overridesForBuild(overrides) {
    const out = {};
    for (const [id, spec] of Object.entries(overrides || {})) {
        if (!id || !spec || typeof spec !== 'object') continue;
        if (spec.kind === 'ndsm') out[id] = spec;
        else if (spec.kind === 'mesh' && spec.upload_id) out[id] = spec;
    }
    return Object.keys(out).length ? out : null;
}

/**
 * A draft override for the panel's form from a stored spec (or defaults).
 * @param {Object|undefined} spec
 */
export function draftFromSpec(spec) {
    const s = spec || {};
    return {
        kind: OVERRIDE_KINDS.includes(s.kind) ? s.kind : 'osm',
        provider: s.provider || 'auto',
        resolution_m: Number.isFinite(+s.resolution_m) && +s.resolution_m > 0 ? +s.resolution_m : null,
        upload_id: s.upload_id || '',
        filename: s.filename || '',
        format: s.format || '',
        fit: s.fit === 'uniform' ? 'uniform' : 'rectangle',
        rotation_deg: Number.isFinite(+s.rotation_deg) ? +s.rotation_deg : 0,
        scale: Number.isFinite(+s.scale) && +s.scale > 0 ? +s.scale : 1,
        offset_x_m: Array.isArray(s.offset_m) ? +s.offset_m[0] || 0 : 0,
        offset_y_m: Array.isArray(s.offset_m) ? +s.offset_m[1] || 0 : 0,
        vertical: s.vertical === 'fit' ? 'fit' : 'true',
        height_m: Number.isFinite(+s.height_m) && +s.height_m > 0 ? +s.height_m : null,
        up_axis: ['z', 'y'].includes(s.up_axis) ? s.up_axis : 'auto',
    };
}

/**
 * The spec to store / send for a panel draft (only the fields its kind uses).
 * @param {ReturnType<typeof draftFromSpec>} d
 */
export function specFromDraft(d) {
    if (d.kind === 'ndsm') {
        const spec = { kind: 'ndsm', provider: d.provider || 'auto' };
        if (d.resolution_m) spec.resolution_m = d.resolution_m;
        return spec;
    }
    if (d.kind === 'mesh') {
        const spec = {
            kind: 'mesh', upload_id: d.upload_id, fit: d.fit, rotation_deg: d.rotation_deg || 0,
            scale: d.scale || 1, offset_m: [d.offset_x_m || 0, d.offset_y_m || 0],
            vertical: d.vertical, up_axis: d.up_axis,
        };
        if (d.filename) spec.filename = d.filename;
        if (d.format) spec.format = d.format;
        if (d.vertical === 'fit' && d.height_m) spec.height_m = d.height_m;
        return spec;
    }
    return { kind: 'osm' };
}

/** Short label of a stored override for the list ("OSM", "nDSM · rediam_mdhn", "Mesh · x.glb"). */
export function overrideLabel(spec) {
    if (!spec || spec.kind === 'osm' || !spec.kind) return 'OSM';
    if (spec.kind === 'ndsm') return `nDSM · ${spec.provider || 'auto'}`;
    if (spec.kind === 'mesh') return `Mesh · ${spec.filename || spec.upload_id || '?'}`;
    return spec.kind;
}

/**
 * Flat vertex / face lists (the preview endpoint's answer) → the triangle
 * count and the bounding box, for the viewer's camera.
 * @param {number[]} vertices  x0,y0,z0,x1,…
 */
export function meshBounds(vertices) {
    const min = [Infinity, Infinity, Infinity];
    const max = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i + 2 < vertices.length; i += 3) {
        for (let k = 0; k < 3; k++) {
            const v = vertices[i + k];
            if (v < min[k]) min[k] = v;
            if (v > max[k]) max[k] = v;
        }
    }
    const center = min.map((m, k) => (m + max[k]) / 2);
    const size = Math.max(...max.map((m, k) => m - min[k]), 1e-9);
    return { min, max, center, size };
}
