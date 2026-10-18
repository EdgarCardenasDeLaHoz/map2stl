/**
 * modules/dem/projection-switch.js — timing helpers for a projection switch on the Edit tab.
 *
 * events/event-listeners-map.js `_switchProjection` uses them to:
 *
 * - **Paint the preview first.** The in-browser warp (projection-preview.js) is drawn and painted
 *   before the other layers' loaders start, because some of them do their work synchronously
 *   (layers/water-mask.js `loadEsaLandCover` re-renders a cached grid for about 1 s on Europa,
 *   and that used to hold back the preview by the same second). See `afterPaint`.
 * - **Survive a newer DEM load.** A second `loadDEM` during the switch (a Detail change with
 *   auto-reload, the Load button, a layer autoload) aborts the switch's own request. The switch
 *   then waits for the load that replaced it, instead of ending the preview while that load is
 *   still running. See `awaitNewestDemLoad`.
 *
 * Pure: no DOM, no window state beyond what the caller passes in, so tests/js can drive it.
 */

/**
 * Resolve once the browser has painted the frame that is queued now: after the next
 * animation frame (which runs window.emitStackUpdate's redraw, queued earlier), then one task
 * later, when that frame is on screen. Falls back to `timeoutMs` where frames do not run (a
 * hidden tab).
 * @param {number} [timeoutMs=50]
 * @param {{raf?: Function, setTimer?: Function}} [clock] - for tests.
 * @returns {Promise<void>}
 */
export function afterPaint(timeoutMs = 50, clock = {}) {
    const raf = clock.raf || globalThis.requestAnimationFrame;
    const setTimer = clock.setTimer || globalThis.setTimeout;
    return new Promise((resolve) => {
        let done = false;
        const go = () => { if (!done) { done = true; resolve(); } };
        if (typeof raf === 'function') raf(() => setTimer(go, 0));
        setTimer(go, timeoutMs);
    });
}

/**
 * Wait for the DEM load a projection switch started, or for whichever `loadDEM` replaced it.
 *
 * `loader` is window.loadDEM: `loader._controller` is the AbortController of its newest call
 * and `loader._promise` that call's promise (both set synchronously by dem-main.js). A cancel
 * (the spinner's ✕) aborts the controller without replacing it; a newer call replaces it.
 *
 * @param {Promise} first - the promise of the switch's own loadDEM call.
 * @param {AbortController|undefined} controller - `loader._controller` read right after that call.
 * @param {{loader: object, hasDem: () => boolean, isCurrent: () => boolean}} opts
 *   hasDem: a DEM is loaded (appState.lastDemData); isCurrent: the switch is still the newest pick.
 * @returns {Promise<'landed'|'stale'|'failed'>} landed: a DEM in this pick's projection is loaded;
 *   stale: a newer pick owns the map (do nothing); failed: the load failed or was cancelled.
 */
export async function awaitNewestDemLoad(first, controller, { loader, hasDem, isCurrent }) {
    let run = first;
    let mine = controller;
    for (;;) {
        try { await run; } catch { /* loadDEM reports its own errors */ }
        if (!isCurrent()) return 'stale';
        if (hasDem()) return 'landed';
        const newest = loader?._controller;
        // Not replaced: our own load failed, or the user cancelled it.
        if (!newest || newest === mine || !loader._promise) return 'failed';
        // Replaced by a newer loadDEM, which reads the same (new) projection from the form.
        mine = newest;
        run = loader._promise;
    }
}
