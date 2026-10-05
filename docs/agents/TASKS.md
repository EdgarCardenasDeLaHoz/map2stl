# Agent task board

Work for the agents on map2stl: the cloud sessions (claude.ai/code) and the local sessions.
Cloud sessions read this file and the rules in [CLAUDE.md](../../CLAUDE.md) › "Cloud agents"
instead of having tasks pasted in.

## Rules

- **Cloud sessions take only tasks with Owner `cloud` and Status `todo`.** Set the task to
  `in-progress` with the branch name when starting, and to `review` with the test counts when
  the branch is pushed. Edit only your own task's row (commit the board change on your branch).
- **Only the user or a local agent adds tasks, assigns owners, or moves a task to `todo` or
  `done`.** A `suggested` task waits for the user to confirm it.
- One task, one branch: `cloud/<task-id>-<slug>`, from the latest `origin/master`. Never push
  to master or to another agent's branch, never merge.
- Owners: `cloud`, `local-map2stl` (the main pipeline session), `local-panoramic` (skyline,
  street view, the GPU and data side).
- Statuses: `suggested` (user to confirm), `todo`, `in-progress`, `review`, `done`.

## Merge process

1. The agent pushes `cloud/<task-id>-<slug>` and sets the task to `review`.
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
| T3 | F-SKYBENCH port: pin the `SKYLINE_CV_*` flags, record whether Street View signing was on, pin the baseline values (b1/512, tag filter on) | cloud | todo | | From [F-SKYBENCH](../plans/active/F-SKYBENCH-height-benchmark.md) › Progress 2026-10-05. Base master. Review: local-panoramic. |
| T4 | F-SKYBENCH port: the auto-proposal cache recomputes when the bbox changes (keyed by name today) | cloud | todo | | As T3. |
| T5 | F-SKYBENCH port: `--refresh-truth`; don't cache reads that failed or ran with `--no-tiles` (`footprint_truth` keeps a survey-only or tiles-only result for good) | cloud | todo | | As T3. |
| T6 | F-SKYBENCH port: accept a truth source only where ≥ 50 % of the footprint's cells are finite (Miami's lidar covers only the coast) | cloud | todo | | As T3. |
| T7 | F-SKYBENCH port: `discover_city_seeds` city filter; keep existing site files unless `--force` | cloud | todo | | As T3. |
| T8 | F-SKYBENCH port: `google_3d` cache key gets `require_built` | cloud | todo | | As T3. `dem` and `max_tiles` are already in the key (audit fix on master). |
| T9 | Large regions: building the river channels takes 150–190 s on the Amazon (315k reaches at 900 px); profile and cut it | local-map2stl | todo | | Needs the local HydroRIVERS cache. Perf audit `claude/scripts/perf_audit.py`, 2026-10-04. |
| T10 | `renderDEMCanvas` re-announces the DEM on every recolour and resize frame | cloud | todo | | [issues.md](../issues.md) › audit 2026-10-05. Frontend only; vitest + eslint. |
| T11 | PA-5: export has no resolution cap; raw `dem_values` / `height` / `width` go straight through | cloud | todo | | [issues.md](../issues.md) › pipeline audit. Server-side validation + tests. |
| T12 | PA-17: `TerrainSession.select()` swallows errors | cloud | todo | | [issues.md](../issues.md). SDK only; tests with the HTTP layer stubbed. |
| T13 | PA-15: SRTM voids clamped to 0 in `geo2stl/dem.py::fetch_h5_dem` | cloud | todo | | [issues.md](../issues.md). Unit test on a synthetic array; no real H5 data needed. |
| T14 | `stacked-layers.js` `LAYER_CANVAS_IDS` has no `CityOverlay` entry | cloud | todo | | [issues.md](../issues.md) › carried over 2026-09-28. Frontend only. |
| T15 | numpy2stl `stl2numpy/heightmap.py::_auto_resolution` swaps rows and cols | cloud | todo | | In the **numpy2stl** repo (cloned next to map2stl): branch `cloud/T15-auto-resolution` there, its own tests (`numpy2stl/tests`, also run from map2stl's pytest). local-map2stl merges it in numpy2stl. |
