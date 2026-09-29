# F-FE1 + F-DEMID — One frontend (Vue), one DEM handle

Status: active. F-DEMID step 1 shipped 2026-09-26 (`/api/terrain/dem` returns `dem_id`;
every export path sends it; an unknown id falls back to the settings-derived key, else 410). F-FE1 step 7 (delete `v2/`) done 2026-09-27.
Next: F-FE1 step 0, then the F-DEMID leftovers ([roadmap](../README.md#frontend-and-ux-city-workflow)).
Decision: standardise on **Vue** (not Svelte/v2); why: [decisions/frontend.md](../../decisions/frontend.md).

## Why Vue

- Vue already renders the whole live UI shell: 31 components (App/AppShell, header,
  sidebar, every DEM settings panel, all views), with Pinia, PrimeVue, TypeScript, and
  ESLint/Vite/vitest wiring in place.
- v2's Svelte client is 9 components. Choosing Svelte means rewriting all 31 Vue
  components *and* the 22.6k lines of vanilla JS; choosing Vue means moving the vanilla
  code behind Vue components and stores.
- What v2 got right is mostly **backend and data-model design** (DEM handle, typed
  settings, cancel, pushed progress, settings versioning). Those port to v1 regardless
  of UI framework, so v2's value is kept without its UI.

## Current coupling (from the 2026-09-26 survey)

- Vue templates are mostly id-bearing containers (461 `id="…"`) that ~20 vanilla
  modules fill via `getElementById`/`innerHTML`; renaming an id breaks silently.
- `window.appState` is swapped for a Pinia-backed proxy at DOMContentLoaded
  (`app/client/static/js/vue/main-vue.ts:92-170`); ordering-sensitive, and `off()` drops every watcher on a key.
- Export re-derives the DEM cache key from settings sent a second time
  (`export-handlers.js:95` `_demSettings`, DOM fallback `:104-114`); any float or
  default mismatch → "Missing DEM data" (raised at 8 sites in `app/server/core/export.py`).
- `main.js` is served raw from source while vue-main.js comes from `dist/`: two module
  graphs, no shared imports (`server.py:264`).

## F-DEMID — DEM handle (do first: removes the riskiest coupling)

1. `app/server/core/dem_store.py`, ported from v2/server/store.py (deleted): opaque id →
   **raw (pre-projection)** grid + bbox + settings; in-memory LRU with `.npy` spill under
   the cache dir, TTL. Raw, so projection/clip stay per-request as today.
2. `GET /api/terrain/dem` returns `demId` (additive field). Edited grids (curve editor,
   merges) are registered with `POST /api/dem/{id}/values` → new derived id.
3. Export (stl/obj/3mf/preview/puzzle/crosssection), city 3MF and hydrology-merge accept
   `demId`. During transition, fall back to `resolve_dem_from_cache`; remove the
   fallback and `_demSettings` once the client sends ids everywhere.
4. Unknown/expired id → 410 with "reload the DEM" (not the generic missing-data error).
5. Port v2's `_reject_degenerate` guard (flat/zero grids).
Tests: export by id; expired id; LRU spill + reload; derived-values id; the e2e
`export_pipeline` test keeps guarding "Missing DEM data".

## F-FE1 — Vue consolidation, in shippable steps

Each step: vitest + Playwright e2e green, ESLint clean, no new `window.*` exports.

0. **Delete dead code**: `bbox-panel.js` `populateRegionsPanelTable` (ids exist nowhere),
   `cache-inventory.js`, dead buttons in `cache.js`, unused composables
   (`useAppStateBridge.ts`, `useEventListeners.ts`). Make vitest import real modules
   instead of copies in `tests/js/helpers/`.
1. **One module graph**: bundle `main.js` through Vite with the Vue entry; serve only `dist/`.
2. **Pinia is the state**: typed store replaces the proxy bridge; vanilla modules import
   a store adapter instead of `window.appState`.
3. **Typed settings** (v2's `RegionSettings` idea): one settings object in the store with a
   `schemaVersion` + migration; `presets.js`, `dem-main.js` query building and export read
   it instead of DOM ids.
4. **Migrate duplicated UI** (vanilla DOM code for UI a component already renders):
   region lists (`region-ui.js`, `view-management.js`), `bbox-panel`, `compare-view`,
   `dem-main.js` DOM writes, toasts → PrimeVue Toast.
5. **Wrap engines** as components/composables that own their canvas: map/globe,
   DEM canvas + stacked layers, model viewer, curve editor, city overlay. Engine code
   becomes TS modules imported by components.
6. **Remove glue**: `app.js`, `events.js` bus → store actions, `window.*` exports.
7. **Delete `v2/`** — done 2026-09-27 (F-ARCH step list; its DEM handle store already
   lived on as `app/server/core/dem_store.py`). v2's cancel/SSE job model can follow as its
   own item.

## Risks

- Behavioural drift in the DEM canvas / stacked layers during step 5: add Playwright
  screenshot checks before touching them.
- `appState.on` ordering during step 2: migrate listeners module by module, keep the
  adapter API identical until the last consumer moves.
