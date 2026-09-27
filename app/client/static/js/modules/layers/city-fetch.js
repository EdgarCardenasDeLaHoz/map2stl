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

/** Terminal task states. */
export const FINISHED = new Set(['done', 'error', 'cancelled']);

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
