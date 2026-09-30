/**
 * modules/layers/city-fetch.js — the background city fetch, client side (F-UX batch 2).
 *
 * loadCityData() (city-overlay.js) starts the fetch with POST /api/cities/start,
 * then runCityFetch() polls GET /api/cities/status/{id} until the task ends and
 * reads the payload from GET /api/cities/result/{id}. Every status is handed to
 * `onStatus`, which city-overlay.js stores as appState.cityFetch for
 * CityFetchProgress.vue (per-layer list, mirror, elapsed, Cancel).
 *
 * The api object is passed in (window.api.cities in the app, a fake in tests).
 */

/** Icon per layer state, as shown in the progress list. */
export const LAYER_STATE_ICON = {
    pending: '○',
    fetching: '⟳',
    done: '✓',
    cached: '✓',
    failed: '✕',
    cancelled: '–',
};

/** Cities panel defaults (#citySimplifyTolerance, #cityMinArea in FetchLayersSection.vue). */
const CITY_SIMPLIFY_TOLERANCE_DEFAULT = 3.0;
const CITY_MIN_AREA_DEFAULT = 5.0;

/**
 * The Cities panel's OSM fetch settings, parsed the way loadCityData() always
 * has (`parseFloat(x) || default`), so the key they produce matches the cache
 * entry the panel wrote.
 * @param {string|number|undefined} tolerance  #citySimplifyTolerance value
 * @param {string|number|undefined} minArea    #cityMinArea value
 * @returns {{simplify_tolerance:number, min_area:number}}
 */
export function cityPanelOsmParams(tolerance, minArea) {
    return {
        simplify_tolerance: parseFloat(tolerance) || CITY_SIMPLIFY_TOLERANCE_DEFAULT,
        min_area: parseFloat(minArea) || CITY_MIN_AREA_DEFAULT,
    };
}

/**
 * OSM settings for the City Model build: those the loaded city data was
 * fetched with (appState.osmCityParams), else the panel's current values. The
 * server reads the OSM cache entry with this key, so the build reuses exactly
 * the layers the user loaded (and their height overrides) instead of fetching
 * again at other settings.
 * @param {{simplify_tolerance:number, min_area:number, detail?:string}|null} loaded
 * @param {string|number|undefined} tolerance  #citySimplifyTolerance value
 * @param {string|number|undefined} minArea    #cityMinArea value
 * @returns {{simplify_tolerance:number, min_area:number, detail:string}}
 */
export function cityBuildOsmParams(loaded, tolerance, minArea) {
    if (Number.isFinite(loaded?.simplify_tolerance) && Number.isFinite(loaded?.min_area)) {
        return {
            simplify_tolerance: loaded.simplify_tolerance,
            min_area: loaded.min_area,
            detail: loaded.detail || 'full',
        };
    }
    return { ...cityPanelOsmParams(tolerance, minArea), detail: 'full' };
}

/** Terminal task states. */
const FINISHED = new Set(['done', 'error', 'cancelled']);

/** "https://overpass.kumi.systems/api" → "overpass.kumi.systems". */
export function mirrorHost(url) {
    if (!url) return '';
    const m = /^[a-z]+:\/\/([^/]+)/i.exec(String(url));
    return m ? m[1] : String(url);
}

/**
 * One-line summary: "4/7 layers · 12 s · overpass.kumi.systems".
 * @param {{layers?:{name:string,state:string}[], elapsed_s?:number, mirror?:string|null}} status
 */
export function summarizeCityFetch(status) {
    const layers = status?.layers || [];
    const finished = layers.filter(l => ['done', 'cached', 'failed', 'cancelled'].includes(l.state)).length;
    const parts = [`${finished}/${layers.length} layers`];
    if (Number.isFinite(status?.elapsed_s)) parts.push(`${Math.round(status.elapsed_s)} s`);
    const host = mirrorHost(status?.mirror);
    if (host) parts.push(host);
    return parts.join(' · ');
}

const _sleep = (ms, signal) => new Promise((resolve) => {
    if (signal?.aborted) { resolve(); return; }
    const t = setTimeout(resolve, ms);
    signal?.addEventListener?.('abort', () => { clearTimeout(t); resolve(); }, { once: true });
});

/**
 * Start a background fetch and follow it to the end.
 *
 * Resolves `{status, data}` (`data` is the city payload when status is "done"),
 * or `{status: 'error', error}` when a request fails. If `signal` aborts, the
 * server task is cancelled and `{status: 'cancelled'}` is returned.
 *
 * @param {{start:Function, status:Function, result:Function, cancel:Function}} api
 * @param {object} body  POST /api/cities body
 * @param {{onStatus?:Function, signal?:AbortSignal, intervalMs?:number}} [opts]
 */
export async function runCityFetch(api, body, { onStatus, signal, intervalMs = 750 } = {}) {
    const { data: started, error: startErr } = await api.start(body);
    if (startErr || !started?.task_id) return { status: 'error', error: startErr || 'No task id' };
    const taskId = started.task_id;
    let status = started;
    onStatus?.(status);
    while (!FINISHED.has(status.status)) {
        await _sleep(intervalMs, signal);
        if (signal?.aborted) {
            const { data } = await api.cancel(taskId);
            if (data) onStatus?.(data);
            return { status: 'cancelled', taskId };
        }
        const { data, error } = await api.status(taskId);
        if (error || !data) return { status: 'error', error: error || 'Status unavailable', taskId };
        status = data;
        onStatus?.(status);
    }
    if (status.status !== 'done') {
        return { status: status.status, error: status.error || status.message, taskId };
    }
    const { data, error } = await api.result(taskId);
    if (error || !data) return { status: 'error', error: error || 'Result unavailable', taskId };
    return { status: 'done', data, taskId };
}
