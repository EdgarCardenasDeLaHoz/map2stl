# FIX — DEM coverage failures, orphaned generate path, diagnostics & key UX

User-requested (2026-07-18) after a Playwright audit of the region→export
workflow found the "Europa" region silently produces an all-zeros DEM and
export fails with a generic 400.

## Goal

Make DEM-coverage failures visible and recoverable, remove a dead code path
that throws, and guide the user to the (already-built) OpenTopo key modal.

## Root causes found

1. **Bug A — orphaned throw.** `export-handlers.js:generateModelFromTab()`
   reads `document.getElementById('modelResolution').value`, but that element
   no longer exists (renamed to `mmPerPixel`). The function is also unreferenced
   (no button calls it). The live path is the 3D-preview auto-rebuild
   (`model-viewer.js:previewModelIn3D`).
2. **Bug B — opaque 400.** `/api/export/preview` returns
   `{"error":"Missing DEM data"}` with no hint that the cause is an empty /
   uncovered DEM.
3. **Silent zeros.** `terrain.py:_fetch_dem_array` catches a local-DEM failure
   (`'NoneType' has no attribute 'copy'` for out-of-coverage boxes) and returns
   an all-zeros array, so the client believes the DEM "loaded".

## Approach

- **Bug A:** delete the orphaned `generateModelFromTab` (and its doc/export
  lines). Nothing calls it; the preview auto-rebuild is the real path.
- **Silent zeros → flagged zeros:** when the local fetch fails, still return a
  usable array (so the UI doesn't crash) but tag the response
  `dem_empty: true` + a `dem_warning` string. Add a response header
  `X-DEM-Empty: 1`. The DEM loader surfaces a toast + records
  `appState.lastDemEmpty`.
- **Bug B:** `generate_mesh_preview` returns a specific message when the
  resolved DEM is empty/flat: distinguish "no DEM cached" from "DEM is flat/all
  zero (no elevation data for this area)".
- **Diagnostics endpoint + button:** `GET /api/diagnostics` returns server-side
  status (auth keys, DEM sources available, cache size, selected-region
  coverage probe). New "🩺 Diagnostics" button in the header opens a modal
  showing it. This is the "request info from the server" button.
- **Key UX:** (a) `save_opentopo_key` also updates the running process config so
  it applies without a restart; (b) the DEM-empty toast links to the Keys
  modal when the failure is coverage/large-region related.
- **Cache config (fold-in):** `config.py` cache dir honours `STRM2STL_CACHE`
  env var (default unchanged) so caches can move out of OneDrive later.

## Target files

- `app/client/static/js/modules/export/export-handlers.js` (delete Bug A)
- `app/server/routers/terrain.py` (flag empty DEM)
- `app/server/core/export.py` (clearer preview error)
- `app/server/routers/diagnostics.py` (new) + `server.py` (register)
- `app/client/static/js/vue/components/layout/MainHeader.vue` (Diagnostics button/modal + link to Keys)
- `app/server/routers/auth.py` (hot-apply key)
- `app/server/config.py` (STRM2STL_CACHE env)
- `app/client/static/js/modules/dem/dem-main.js` (surface empty-DEM warning)

## Success criteria

- Loading Europa shows a clear "no elevation data / try OpenTopo key" message,
  not a blank flat DEM.
- The Extrude/export path never calls the throwing `generateModelFromTab`.
- `/api/diagnostics` returns JSON; the header button renders it.
- Saving an OpenTopo key makes `/api/auth/status` report configured without a
  server restart.
- Existing tests still pass; new behaviour covered by a Playwright check.
