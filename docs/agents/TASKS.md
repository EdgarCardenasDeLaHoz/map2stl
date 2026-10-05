# Agent task board

Work for the agents on map2stl: the cloud sessions (claude.ai/code) and the local sessions.
Cloud sessions read this file and the rules in [CLAUDE.md](../../CLAUDE.md) › "Cloud agents"
instead of having tasks pasted in.

## Rules

- **Cloud sessions take only tasks with Owner `cloud` and Status `todo` whose branch does not
  exist yet.** A task is claimed by its branch: before starting, run
  `git ls-remote --heads origin 'cloud/T<n>-*'`; if a branch shows up, another session has it.
  Push the branch early (a first commit) so the claim is visible.
- **Cloud branches don't edit this file** (every branch editing it made merge conflicts, and a
  status set on a branch never reached master). Put the test counts and any deletion list in
  the branch's last commit message; the merging session updates the board on master.
- **Only the user or a local agent adds tasks, assigns owners, or moves a task to `todo` or
  `done`.** A `suggested` task waits for the user to confirm it.
- One task, one branch: `cloud/<task-id>-<slug>`, from the latest `origin/master`. Never push
  to master or to another agent's branch, never merge.
- Owners: `cloud`, `local-map2stl` (the main pipeline session), `local-panoramic` (skyline,
  street view, the GPU and data side).
- Statuses: `suggested` (user to confirm), `todo`, `in-progress`, `review`, `done`.

## Merge process

1. The agent pushes `cloud/<task-id>-<slug>` with the test counts in its last commit message.
   A local session that sees the branch may mark the task `in-progress` or `review` on master.
2. `local-map2stl` reviews the branch, rebases it on master if needed, runs the full suite
   (pytest `-n 4`, vitest, eslint) and a browser smoke test for UI changes, then merges it
   into master and pushes. Anything under `city2stl/skyline/` or F-SKYBENCH is reviewed by
   `local-panoramic` first.
3. The merging session sets the task to `done`, deletes the branch on origin, and tells the
   other local session to pull.

## Tasks

| ID | Task | Owner | Status | Branch | Notes |
|---|---|---|---|---|---|
| T1 | Fix the two xfail tests: `/api/cities/raster` returns `values_b64`, the satellite response carries `dimensions` (issues §0f) | cloud | done | cloud/xfail-format | Merged 2026-10-05 (`0b169b4`). |
| T2 | A finer cached Earth Engine image serves coarser requests (`geo2stl/sat2stl.py::fetch_bbox_image`) | cloud | done | cloud/ee-cache-reuse | Merged 2026-10-05 (`21118bf`); review fix `927fcc5` (float images were rounded). |
| T3 | F-SKYBENCH port: pin the `SKYLINE_CV_*` flags, record whether Street View signing was on, pin the baseline values (b1/512, tag filter on) | cloud | done | cloud/T3-skybench-pin-flags | Merged 2026-10-05 (`ff7c609`). Reviewed by local-panoramic: the pins match the baseline runs (b1 was set on their command line; every other flag is the code default). |
| T4 | F-SKYBENCH port: the auto-proposal cache recomputes when the bbox changes (keyed by name today) | cloud | done | cloud/T4-proposal-cache-bbox | Merged 2026-10-05 (`c8b6f0a`). Reviewed by local-panoramic: the region PDF is the only caller; pre-bbox files are kept, a failed re-proposal keeps the old set. |
| T5 | F-SKYBENCH port: `--refresh-truth`; don't cache reads that failed or ran with `--no-tiles` (`footprint_truth` keeps a survey-only or tiles-only result for good) | cloud | done | cloud/T5-refresh-truth | Merged 2026-10-05 (`befca71`). Reviewed by local-panoramic. Truth read under the old rules stays cached: run `10_benchmark --refresh-truth` to re-measure with T6's rule. |
| T6 | F-SKYBENCH port: accept a truth source only where ≥ 50 % of the footprint's cells are finite (Miami's lidar covers only the coast) | cloud | done | cloud/T6-finite-fraction | Merged 2026-10-05 (`9b56393`). Reviewed by local-panoramic; applies to new truth reads (see T5 for re-measuring). |
| T7 | F-SKYBENCH port: `discover_city_seeds` city filter; keep existing site files unless `--force` | cloud | done | cloud/T7-discover-seeds-filter | Merged 2026-10-05 (`13d8f27`). Reviewed by local-panoramic. |
| T8 | F-SKYBENCH port: `google_3d` cache key gets `require_built` | cloud | done | cloud/T8-google3d-key-require-built | Merged 2026-10-05 (`d2604d4`). Reviewed by local-panoramic: `require_built` is checked on cache hits instead of keyed, so no cached raster is re-downloaded (paid). |
| T9 | Large regions: building the river channels takes 90–190 s on the Amazon at 'All rivers' (1.2M reaches at 900 px); profile and cut it | local-map2stl | done | | 2026-10-05: worker processes, same grid (`geo2stl/water_layers.py::_carve_parallel`, above `CARVE_PARALLEL_MIN` reaches): Amazon 199 → 78 s, Colombia 23.9 → 10.9 s (loaded machine), checked identical by `claude/scripts/river_carve_bench.py` and `tests/test_water_layers.py::test_parallel_carve_matches_one_pass`. Profile: snapping 28 s, buffering 16 s, GeoJSON bridge 23 s, rasterise 15 s; line-burning thin reaches tried and reverted (changed the carve). Not done: a minimum river size by pixel size (user chose same output). |
| T10 | `renderDEMCanvas` re-announces the DEM on every recolour and resize frame | cloud | done | cloud/T10-render-dem-no-reannounce | Merged 2026-10-05 (`62e4391`, its TASKS.md edits dropped). Checked in the app: one DEM_LOADED on load, none on recolour or resize. [issues.md](../issues.md) › audit 2026-10-05. Frontend only; vitest + eslint. |
| T11 | PA-5: export has no resolution cap; raw `dem_values` / `height` / `width` go straight through | cloud | done | cloud/T11-export-resolution-cap | Merged 2026-10-05 (`f88a409`): `app/server/core/export_params.py::check_dem_grid` on every export route. |
| T12 | PA-17: `TerrainSession.select()` swallows errors | cloud | done | cloud/T12-select-errors | Merged 2026-10-05 (`4cd8ea2`): a failed settings load raises; a 404 still means no saved settings. |
| T13 | PA-15: SRTM voids clamped to 0 in `geo2stl/dem.py::fetch_h5_dem` | cloud | done | cloud/T13-srtm-voids | Merged 2026-10-05 (`b241671`): voids left out of block means, below-sea-level ground kept. |
| T14 | `stacked-layers.js` `LAYER_CANVAS_IDS` has no `CityOverlay` entry | cloud | done | cloud/T14-cityoverlay-canvas-id | Merged 2026-10-05 (`bb49064`): `getOrCreateCanvas` returns null for a layer without a canvas. |
| T15 | numpy2stl `stl2numpy/heightmap.py::_auto_resolution` swaps rows and cols | cloud | done | cloud/T15-auto-resolution | Merged in numpy2stl 2026-10-05 (`1d14091`); the map2stl branch (board edits only) dropped. |
