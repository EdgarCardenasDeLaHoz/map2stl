/**
 * city-detail.js — the F-DETAIL settings of Buildings & roads › Composite: Roof shapes, Trees
 * and the Alhambra reference model (mockups claude/mockups/2026-10-17-detail). Pure helpers
 * (request fields, extent test) plus a small survey-coverage cache for the Trees row.
 *
 * Controls (holders in CompositeDemSection.vue, rows in settings/layerGroups.ts):
 *   #cityRoofShapes  select auto | off   → request `roof_shapes` (export, /api/cities[/start])
 *   #cityTrees       checkbox, default on → `layers.trees.enabled: true`, only where survey lidar covers
 *   #cityAlhambra    checkbox, default off → `reference_landmarks: true|false`
 */

/**
 * Lon/lat extent of the Alhambra reference model (Thingiverse 5141408, CC BY-SA): the valid
 * cells of `thing_valid` in output/detail_study/shared/alhambra_grids.npz placed with that
 * folder's `bbox` (thingiverse_to_geo.json carries the STL → lon/lat transform), rounded out.
 */
export const ALHAMBRA_BBOX = Object.freeze({ north: 37.1777, south: 37.1744, east: -3.5850, west: -3.5931 });

/** Do two {north,south,east,west} boxes overlap? False when either is missing. */
export function bboxIntersects(a, b) {
    if (!a || !b) return false;
    return a.west < b.east && a.east > b.west && a.south < b.north && a.north > b.south;
}

/**
 * Roof shapes are classified when the buildings load, so a change only shows after a reload.
 * Reloads right away when buildings are on the map (nothing to do before the first load).
 * @param {{appState?:object, loadCityData?:Function}} w the window (or a stand-in)
 * @returns {boolean} whether a reload was started
 */
export function reloadBuildingsForRoofShapes(w) {
    if (!w?.appState?.osmCityData?.buildings?.features?.length) return false;
    if (typeof w.loadCityData !== 'function') return false;
    w.loadCityData();
    return true;
}

/** "auto" | "off" from the #cityRoofShapes value (anything but "off" is auto). */
export function roofShapesValue(raw) {
    return raw === 'off' ? 'off' : 'auto';
}

/**
 * The F-DETAIL fields of the City Model export request. Trees are sent only when the switch
 * is on and survey lidar covers the box; off leaves the request as before.
 * @param {{roofShapes?:string, trees?:boolean, covered?:boolean, alhambra?:boolean, alhambraInBox?:boolean}} s
 * @returns {{roof_shapes:string, reference_landmarks:boolean, trees?:{enabled:true}}}
 */
export function cityDetailFields(s) {
    const out = {
        roof_shapes: roofShapesValue(s.roofShapes),
        reference_landmarks: !!(s.alhambra && s.alhambraInBox),
    };
    if (s.trees && s.covered) out.trees = { enabled: true };
    return out;
}

/** Providers whose surface includes vegetation: city2stl/canopy.py::TREE_PROVIDERS (CNIG MDSn
 *  models buildings only, so Spain outside Andalucía has no tree source). */
export const TREE_PROVIDERS = Object.freeze(['rediam_mdhn', 'ign_lidarhd', 'cuzk_dmp', 'usgs_3dep', 'usgs_3dep_ept']);

/** Does a GET /api/cities/survey-sources list include a vegetation-capable source that covers the box? */
export function coversTrees(sources) {
    return Array.isArray(sources) && sources.some((s) => s?.available && TREE_PROVIDERS.includes(s.name));
}

// ── Survey coverage (GET /api/cities/survey-sources, the `sources` list) ─────────────────────────
const _cache = new Map();   // bbox key → 'yes' | 'no' | 'pending'
const _pending = new Map();   // bbox key → the request's promise while 'pending'

const _key = (b) => [b.north, b.south, b.east, b.west].map((v) => Number(v).toFixed(5)).join(',');

/**
 * Does survey lidar cover the box? 'yes' | 'no' | 'unknown' (not asked yet, or asking). The first
 * call for a box starts the request; `onChange` runs when the answer arrives. A failed request
 * counts as 'no' (trees are never requested without a confirmed source).
 */
export function surveyCoverage(bbox, getSources, onChange) {
    if (!bbox || ![bbox.north, bbox.south, bbox.east, bbox.west].every(Number.isFinite)) return 'unknown';
    const k = _key(bbox);
    const hit = _cache.get(k);
    if (hit === 'yes' || hit === 'no') return hit;
    if (hit === 'pending') return 'unknown';
    _cache.set(k, 'pending');
    const done = Promise.resolve(getSources(bbox)).then((sources) => {
        _cache.set(k, coversTrees(sources) ? 'yes' : 'no');
    }, () => _cache.set(k, 'no')).then(() => { _pending.delete(k); onChange?.(); });
    _pending.set(k, done);
    return 'unknown';
}

/** :func:`surveyCoverage`, waiting for the answer ('yes' | 'no' | 'unknown' for no box), so an
 *  export started before the Edit panel asked still requests the trees. */
export async function surveyCoverageReady(bbox, getSources) {
    const now = surveyCoverage(bbox, getSources);
    if (now !== 'unknown') return now;
    const p = bbox && _pending.get(_key(bbox));
    if (!p) return now;
    await p;
    return surveyCoverage(bbox, getSources);
}

/** The `sources` of GET /api/cities/survey-sources for a box (the getSources of surveyCoverage). */
export async function fetchSurveySources(b) {
    const { data } = await window.api.cities.surveySources(
        new URLSearchParams({ north: b.north, south: b.south, east: b.east, west: b.west }));
    return data?.sources;
}

/** Forget cached answers (tests). */
export function clearSurveyCoverage() { _cache.clear(); _pending.clear(); }
