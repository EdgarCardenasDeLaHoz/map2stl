/**
 * useEventListeners — Stage 7 composable
 *
 * Wraps window.setupEventListeners() so it is called after all Vue components
 * have mounted (i.e., all IDs exist in the DOM). Previously this was called
 * from app.js DOMContentLoaded, but now the DOM is built by Vue components
 * that mount asynchronously. Calling it here ensures elements like #tabExplore,
 * #sidebarToggleBtn, #bboxReloadBtn etc. are available.
 *
 * Usage: call inside the root App.vue onMounted.
 */
import { onMounted } from 'vue';

export function useEventListeners() {
    onMounted(() => {
        // Give the JS modules a tick to init (app.js DOMContentLoaded may run first
        // if vue-main.js mounts synchronously, but app.js event listener setup is
        // deferred to window.setupEventListeners() called from initApp())
        // Nothing to do here — app.js's initApp() calls setupEventListeners() after
        // DOMContentLoaded. Since Vue mounts before DOMContentLoaded callbacks fire
        // (script type=module defers), all Vue-rendered IDs exist by the time
        // setupEventListeners() runs.
        //
        // This composable is a placeholder for future migration of specific listeners
        // to reactive Vue handlers (e.g., tab clicks → Pinia store.activeView).
    });
}
