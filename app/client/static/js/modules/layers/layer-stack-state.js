/**
 * modules/layers/layer-stack-state.js — pure helpers for the map's layer order, opacity and
 * visibility (no DOM). Used by stacked-layers.js and the Edit page's Layers rail
 * (vue/components/dem/EditLayersPanel.vue), which owns all three since 2026-10-14.
 *
 * Two orders exist:
 *   - the stack order (stacked-layers.js `_layerOrder`), bottom → top, every layer key;
 *   - the rail order, top → bottom (top of the list is drawn on top), rail rows only.
 * Layers without a row of their own ride with a host row (COMPANIONS), so the rail can
 * reorder everything the map draws.
 */

/** Default draw order, bottom → top. */
export const DEFAULT_LAYER_ORDER = Object.freeze([
    'Dem', 'CompositeDem', 'WaterHydrology', 'Sat', 'SatImg', 'CityRaster', 'CityOverlay',
    'Trails', 'MeshImport', 'Borders',
]);

/** Default per-layer opacity on the map (0–1). */
export const DEFAULT_LAYER_OPACITY = Object.freeze({
    Dem: 1, WaterHydrology: 0.75, Sat: 0.7, SatImg: 0.8, CityRaster: 0.7, CityOverlay: 0.85,
    Trails: 0.9, MeshImport: 0.8, CompositeDem: 1, Borders: 0.9,
});

/**
 * Layers with no rail row, keyed by the row they ride with, bottom → top around the host.
 * The combined height map is the terrain's picture after carving; the city raster is the
 * buildings' height picture.
 */
export const COMPANIONS = Object.freeze({
    Dem: Object.freeze(['Dem', 'CompositeDem']),
    CityOverlay: Object.freeze(['CityRaster', 'CityOverlay']),
});

/** Layers whose visibility is saved with the region (the rest follow what prints). */
export const SAVED_VISIBILITY = Object.freeze(['Dem', 'Sat', 'Borders']);

const COMPANION_OF = Object.freeze(Object.fromEntries(
    Object.entries(COMPANIONS).flatMap(([host, group]) => group.filter((k) => k !== host).map((k) => [k, host])),
));

/** The row a layer key belongs to (itself, or its host). */
export function hostOf(key) {
    return COMPANION_OF[key] || key;
}

/** Keys that move and fade with a row, bottom → top. */
export function groupOf(host) {
    return COMPANIONS[host] ? [...COMPANIONS[host]] : [host];
}

/** Return a copy of `list` with the item at `from` moved to index `to`. */
export function moveItem(list, from, to) {
    const out = [...list];
    if (from < 0 || from >= out.length) return out;
    const t = Math.max(0, Math.min(out.length - 1, to));
    const [item] = out.splice(from, 1);
    out.splice(t, 0, item);
    return out;
}

/** Rail order (top → bottom) of the rows in `railStacks`, read from a stack order. */
export function stackToRailOrder(stackOrder, railStacks) {
    const rail = new Set(railStacks);
    const seen = new Set();
    const out = [];
    for (let i = stackOrder.length - 1; i >= 0; i--) {
        const host = hostOf(stackOrder[i]);
        if (rail.has(host) && !seen.has(host)) { seen.add(host); out.push(host); }
    }
    for (const s of railStacks) if (!seen.has(s)) out.push(s);
    return out;
}

/**
 * Stack order (bottom → top) for a rail order (top → bottom). Each row brings its companions;
 * keys of `stackOrder` that belong to no row (a hidden tool's layers) keep their index from
 * the bottom, nudged up so they never split another row's group.
 */
export function railOrderToStack(stackOrder, railTopFirst) {
    const placed = new Set();
    const out = [];
    for (const host of [...railTopFirst].reverse()) {
        for (const k of groupOf(host)) {
            if (!placed.has(k) && (stackOrder.includes(k) || k === host)) { placed.add(k); out.push(k); }
        }
    }
    stackOrder.forEach((k, i) => {
        if (placed.has(k)) return;
        placed.add(k);
        let at = Math.min(i, out.length);
        while (at > 0 && at < out.length && hostOf(out[at - 1]) === hostOf(out[at]) && hostOf(out[at]) !== hostOf(k)) at++;
        out.splice(at, 0, k);
    });
    return out;
}

/** Move a row (by stack key) to rail index `to`; returns the new stack order. */
export function moveRow(stackOrder, railStacks, host, to) {
    const rail = stackToRailOrder(stackOrder, railStacks);
    const from = rail.indexOf(host);
    if (from < 0) return [...stackOrder];
    return railOrderToStack(stackOrder, moveItem(rail, from, to));
}

function clamp01(v) {
    if (typeof v !== 'number') return null;
    const n = v;
    return Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : null;
}

/**
 * Validate saved layer state against the known keys. Unknown keys are dropped, missing ones
 * take their default slot and value, companions are put back beside their row, and opacity is
 * clamped to 0–1. Returns null when nothing usable was saved.
 * @param {unknown} saved  {order?: string[], opacity?: {[k]: number}, visible?: {[k]: boolean}}
 */
export function parseLayerState(saved, defaults = DEFAULT_LAYER_ORDER) {
    if (!saved || typeof saved !== 'object') return null;
    const known = new Set(defaults);
    let order = null;
    if (Array.isArray(saved.order)) {
        const kept = [];
        for (const k of saved.order) if (known.has(k) && !kept.includes(k)) kept.push(k);
        if (kept.length) {
            // A key missing from the save goes back beside its default lower neighbour.
            for (const [i, k] of defaults.entries()) {
                if (kept.includes(k)) continue;
                const below = defaults.slice(0, i).reverse().find((d) => kept.includes(d));
                kept.splice(below ? kept.indexOf(below) + 1 : 0, 0, k);
            }
            const rows = [...new Set(kept.map(hostOf))];
            order = railOrderToStack(kept, stackToRailOrder(kept, rows));
        }
    }
    const opacity = {};
    if (saved.opacity && typeof saved.opacity === 'object') {
        for (const [k, v] of Object.entries(saved.opacity)) {
            const c = clamp01(v);
            if (known.has(k) && c !== null) opacity[k] = c;
        }
    }
    const visible = {};
    if (saved.visible && typeof saved.visible === 'object') {
        for (const k of SAVED_VISIBILITY) if (typeof saved.visible[k] === 'boolean') visible[k] = saved.visible[k];
    }
    if (!order && !Object.keys(opacity).length && !Object.keys(visible).length) return null;
    return { order, opacity, visible };
}

/** Plain JSON for the region's settings (view.layers). */
export function serializeLayerState(order, opacities, active) {
    const opacity = {};
    for (const k of order) if (k in opacities) opacity[k] = Math.round(opacities[k] * 100) / 100;
    const visible = {};
    for (const k of SAVED_VISIBILITY) visible[k] = active.has(k);
    return { order: [...order], opacity, visible };
}
