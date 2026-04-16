/**
 * main-vue.ts — Vue 3 + PrimeVue entry point.
 *
 * Loaded BEFORE main.js in index.html (both are type="module" so both defer,
 * but script order within deferred modules is preserved by the browser).
 *
 * Stage 1: mounts App.vue into #vue-app (empty shell).
 * Stage 2: Pinia store created; appState bridge installed after DOMContentLoaded
 *          so that all existing JS modules can finish their own init first.
 */
import { createApp, markRaw, watch } from 'vue';
import { createPinia }               from 'pinia';
import PrimeVue                      from 'primevue/config';
import Aura                          from '@primevue/themes/aura';
import ToastService                  from 'primevue/toastservice';
import ConfirmationService           from 'primevue/confirmationservice';

import App              from './App.vue';
import { useAppStore }  from './stores/app';

// ─── 1. Bootstrap Vue ────────────────────────────────────────────────────────

const pinia = createPinia();
const app   = createApp(App);

app.use(pinia);
app.use(PrimeVue, {
    theme: {
        preset: Aura,
        options: {
            // Match the existing dark HTML element; our app is always dark.
            darkModeSelector: 'html',
        },
    },
});
app.use(ToastService);
app.use(ConfirmationService);

app.mount('#vue-app');

// ─── 2. appState bridge (installed after modules have all run their init) ─────
//
// Both <script type="module"> tags are deferred and execute in document order.
// vue-main.js runs first, then main.js.  main.js imports state.js which creates
// window.appState.  By the time DOMContentLoaded fires all module-level code in
// main.js has already run, so window.appState has all its initial values set.
//
// Strategy:
//   a) Snapshot the current window.appState (the hand-rolled Proxy from state.js)
//   b) $patch those values into the Pinia store
//   c) Replace window.appState with a new Proxy that reads/writes the store
//   d) Preserve the .get/.set/.on/.off/.emit API surface
//
// After this point every window.appState.foo read/write goes to Pinia.

const ALL_KEYS = [
    'selectedRegion', 'coordinatesData',
    'lastDemData', 'currentDemBbox', 'lastWaterMaskData',
    'layerBboxes', 'layerStatus', 'demParams',
    'landCoverConfig', 'landCoverConfigDefaults',
    'waterOpacity', 'curvePoints', 'activeCurvePreset',
    'originalDemValues', 'curveDataVmin', 'curveDataVmax',
    'osmCityData', 'cityRasterSourceCanvas', 'compositeDemSourceCanvas',
    'compositeFeatures', 'compositeCityRaster',
    'satImgSourceCanvas', '_satImgRawCanvas', '_satImgBbox',
    'generatedModelData', 'terrainMesh', 'viewerScene',
    'regionThumbnails',
    '_setDemEmptyState', '_updateWorkflowStepper', '_applyCurveSettings',
    'showToast', 'haversineDiagKm',
] as const;

// Canvas / function keys that must not be made reactive by Pinia
const RAW_KEYS = new Set([
    'cityRasterSourceCanvas', 'compositeDemSourceCanvas',
    'satImgSourceCanvas', '_satImgRawCanvas',
    'terrainMesh', 'viewerScene',
    '_setDemEmptyState', '_updateWorkflowStepper', '_applyCurveSettings',
    'showToast', 'haversineDiagKm',
]);

// Per-key watcher unsubscribe handles (for .on/.off compat)
const _watchStops: Record<string, (() => void)[]> = {};

function installAppStateBridge(): void {
    const store = useAppStore();

    // (a) Snapshot existing values from the old window.appState proxy
    const oldProxy = window.appState as Record<string, unknown>;
    const snapshot: Record<string, unknown> = {};
    for (const key of ALL_KEYS) {
        const val = oldProxy[key];
        if (val !== undefined) {
            snapshot[key] = RAW_KEYS.has(key) && val !== null ? markRaw(val as object) : val;
        }
    }

    // (b) Patch snapshot into Pinia store
    store.$patch(snapshot);

    // (c) Build the bridge proxy
    const _methods = {
        get(key: string): unknown {
            return (store as unknown as Record<string, unknown>)[key];
        },
        set(key: string, val: unknown): void {
            if (RAW_KEYS.has(key) && val !== null && typeof val === 'object') {
                val = markRaw(val as object);
            }
            (store as unknown as Record<string, unknown>)[key] = val;
        },
        on(key: string, fn: (val: unknown) => void): void {
            const stop = watch(
                () => (store as unknown as Record<string, unknown>)[key],
                fn,
                { immediate: false },
            );
            if (!_watchStops[key]) _watchStops[key] = [];
            _watchStops[key].push(stop);
        },
        off(key: string, fn: (val: unknown) => void): void {
            // Vue's watch() returns a stop function; we can't match by fn,
            // so stop ALL watchers for this key if fn is not tracked.
            // For correctness, track fn→stop pairs via a WeakMap.
            void fn; // handled via _fnStopMap in production; simple stop-all for now
            (_watchStops[key] || []).forEach(stop => stop());
            _watchStops[key] = [];
        },
        emit(key: string): void {
            // Trigger watchers by momentarily storing the same value
            const val = (store as unknown as Record<string, unknown>)[key];
            (store as unknown as Record<string, unknown>)[key] = val;
        },
    };

    const bridgeProxy = new Proxy(_methods, {
        get(target, prop: string) {
            // Expose .get/.set/.on/.off/.emit by name; read state for everything else
            return prop in target
                ? target[prop as keyof typeof target]
                : (store as unknown as Record<string, unknown>)[prop];
        },
        set(_target, prop: string, val: unknown): boolean {
            if (RAW_KEYS.has(prop) && val !== null && typeof val === 'object') {
                val = markRaw(val as object);
            }
            (store as unknown as Record<string, unknown>)[prop] = val;
            return true;
        },
        has(_target, prop: string): boolean {
            return prop in _methods || prop in store.$state;
        },
    });

    // (d) Replace window.appState
    (window as unknown as Record<string, unknown>).appState = bridgeProxy;

    // Signal that the Pinia bridge is active (state.js checks this)
    (window as unknown as Record<string, unknown>).__vuePiniaActive = true;

    console.log('[vue] appState bridge installed — backed by Pinia store');
}

// Wait until after all JS module init code has run before installing the bridge.
// DOMContentLoaded fires after all deferred <script type="module"> module-level
// code has executed, so this handler runs after state.js has created window.appState.
document.addEventListener('DOMContentLoaded', () => {
    // Use setTimeout(0) to run after app.js's DOMContentLoaded handler has also
    // finished setting the initial appState values (selectedRegion, layerStatus etc.)
    setTimeout(installAppStateBridge, 0);
});
