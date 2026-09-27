# F-REGION — Large regions with rivers, and registration in the UI

Status: steps 1–3 and 5 done 2026-09-27 (planned 2026-09-27) (user: "do the same thing for new other examples, large areas
don't require city layers, but may require river and hydrology layers … registration
pipelines … are we able to follow those steps using the UI tool").

## Where it stands

- HydroRIVERS / Natural Earth rivers exist (`geo2stl/hydrology.py`) with a
  `/api/terrain/hydrology` overlay route, but are **not a composite layer source**, so no
  mesh export can carve them. Width is ~1 px × factor, not metres; discharge unused.
- `merge_rivers_with_dem` set river cells to an absolute −5 m (fixed 2026-09-27).
- City-model rivers were cut flat to the lowest point (trench); now follow the valley.
- `water_esa` needs Earth Engine auth; no fallback for lakes.
- `get_city_layers` has no bbox cap on the city-model path (a 100 km OSM fetch would run).
- Registration (vendor plate vs OSM, align_tool street placement, skyline) is scripts and
  notebooks; the UI has mesh import, manual/auto registration returning 3 numbers, and a
  `/reports` browser for skyline output only. Our own generated STL is scored only by a
  scratch "plate critic".

## Approach

1. **Done 2026-09-27 — Rivers in the terrain stage**: register `hydrorivers` and `natural_earth_rivers` as
   geo2stl layer sources (depth relative to ground, width in metres from order or
   discharge, rasterised at the DEM grid); expose them in the Composite panel; they then
   reach every export through `terrain_stage`.
2. **Done 2026-09-27 — Lakes without Earth Engine**: OSM `natural=water` (≥ size threshold) or HydroLAKES as a
   flat-cut layer; ESA stays optional.
3. **Done 2026-09-27 — Large-region preset**: Region preset (terrain only, rivers on, Vertical auto → fit,
   30 m source up to ~100 km, 90 m beyond), city layers disabled with a size guard on
   `get_city_layers` for boxes > 25 km.
4. **Examples + SOP**: render and review three new regions end to end (e.g. Grand Canyon
   ~100 km, Middle Rhine valley ~50 km, Sierra Nevada + Granada ~60 km), write the SOP
   section "Large regions and rivers", and record reference results like §1 of the city SOP.
5. **Registration in the UI**: `/reports` roots for `_reports/` (plate registration) and
   `tools/align_tool/data/`; auto-register returns the report path and score breakdown;
   a "Plate registration" panel wrapping street placement + auto-register as background
   tasks; "score this model" against a registered plate or lidar nDSM using the promoted
   critic (F-LANDMARK §6). **Done 2026-09-27** (see Progress).

## Progress

- 2026-09-27 — steps 1–3 done.
  - **Sources** (`geo2stl/water_layers.py`): `hydrorivers`, `natural_earth_rivers`, `lakes`
    are *terrain-relative* layer sources (`provider.terrain_relative = True`): negative
    metres below the ground, 0 elsewhere, blended with `add` (`dem + depth`; the `rivers`
    blend mode subtracts, so it is not used for them). `compute_composite_dem` hands them
    the base DEM layer's raw grid (`fetch_layer_data(..., base=)`), projects them
    nearest-neighbour and resizes keeping the deepest value, so a 1-px channel is not
    blurred away; overlapping river/lake carves take the deeper one.
  - **Width/depth**: from HydroRIVERS mean discharge `DIS_AV_CMS` via the Andreadis et al.
    (2013) global fit W = 7.2 Q^0.5, D = 0.27 Q^0.39; no discharge → Q from Strahler order
    (Q ≈ 0.2·4^(order−1)); Natural Earth order = clip(10 − scalerank, 4, 9). Width clamped
    to 2–3000 m, depth 0.5–30 m, half-width never below 0.55 px, so every drawn river is at
    least one connected pixel wide. `width_scale` option and the layer weight (depth ×)
    tune it; at fit scale a 100 km box needs depth × > 1 to read on a print.
  - **Median filter — chosen: carve after smoothing.** A 1-px river is 3 of the 9 cells
    in a 3×3 window, so the median erased it (tested). `compute_composite_dem(...,
    split_carve=True)` returns `(composite, carve)`; `ExportContext.carve_m` holds the carve
    and `export._prepare_dem_array` adds it right after `prepare_dem` (before the sea-level
    cap and scaling). The 2D preview / `dem-merge` response still returns `composite +
    carve`. Lakes are levelled against the raw DEM, so after the median their surface is
    flat to within the median's change at the shore.
  - **Lakes**: OSM `natural=water` + `landuse=reservoir` (`city2stl.fetch.fetch_osm_lakes`,
    cached in the OSM cache under `osm_lakes`; an Overpass outage raises, never caches
    empty). ≥ `min_area_m2` (default 1 ha, and ≥ 1 pixel), flat at min(shore ring) −
    `depth_m` (default 2 m), only ever lowers; `water=river|stream|canal|…` and
    `waterway=riverbank` are skipped (flattening a river polygon to its lowest point cuts a
    valley-long trench). HydroLAKES is not available locally, so not used. `water_esa`
    unchanged and optional.
  - **Client**: Composite panel group "Rivers & lakes" (`CompositeDemSection.vue`; source
    select, min order, depth ×, width ×, lake depth, min area); `composite-spec.js`
    `waterTerrainLayers` puts them in the terrain (export/apply) spec; the 2D preview
    fetches the carve from `/api/composite/dem-merge` on a zero-weight base. Off by default.
  - **Region preset** (`workflow-presets.js`): terrain only (all city layers + puzzle off),
    composite rivers + lakes on, Vertical auto (fit above 20 km), 1000 px, SRTMGL1 when the
    longer side ≤ 100 km else SRTMGL3 (`regionDemSource`; skipped with a warning when no
    region is selected). The export only carries the composite after Apply to DEM, so the
    preset's toast says to load the DEM and Apply.
  - **Size guard**: `core/city_data.check_city_area` / `get_city_layers(allow_large=)` raise
    `CityAreaTooLarge` for city layers on > 25 km diagonal; the city export task fails with
    that message (trails included) unless the request sets `allow_large_city`.
    `/api/cities` already caps at 15/25 km.
  - Tests: `tests/test_water_layers.py` (synthetic GeoDataFrames), `tests/js/compositeSpec.test.js`,
    `tests/js/workflowPresets.test.js`.
  - Left: step 4 (render three reference regions, SOP), step 5 (registration UI); the
    100 km < 2 min criterion is not yet measured on a real region.

- 2026-09-27 — step 5 done (registration in the UI).
  - **Promoted, not shelled out**: the align tool's placement and check now live in
    `city2stl/registration/` — `street_place.py` + `osm_model.py` (moved whole; the tool files
    are `sys.modules` aliases / a CLI shim), `osm_water.py` (locate's OSM-water section),
    `correlate.py` (`find_peaks`, `edges`, `ncc_surface`, `height_channel`), `consensus.py`
    (`crop_consensus`, `verdict`, `correct`, `score_export(matrix=...)`, `record_verdict`) and
    `align_paths.py`. `locate`, `refine_guess` and `auto_register` import them, so the tool
    CLIs are unchanged; pack discovery / adoption / the batch export stay in the tool.
  - **Reports**: `GET /api/reports/registration` scans `Code/_reports/`, `_reports_regen/`,
    `Cities/micropolitan/reports/`, the app's `cache/mesh_imports/reports/` and summarises every
    `tools/align_tool/data/<slug>/meta.json` (roots overridable with
    `STRM2STL_REGISTRATION_REPORT_ROOTS`; read-only; same traversal guard). New Registration tab.
  - **Auto-register**: writes the numpy2stl HTML report to `cache/mesh_imports/reports/<city>_<hash>/`
    (per import source, overwritten on re-run; `write_report: false` for the old fast path) and
    returns `report_url` + `scores` (RMSE, MAE, bias, Pearson r, coverage, footprint IoU, match
    score, per-building p95), shown as a table in `MeshImportSection.vue`.
  - **Plate registration panel** (`PlateRegistrationSection.vue`, Composite tab): library plate →
    matched pack → street placement + tile consensus as a background task
    (`core/plate_registration.py`, `/api/registration/plate/*`, city-fetch task pattern) →
    verdict, placement table, outline on the map → saved to the location sidecar through the
    existing location route (new optional `placement` record beside the bbox).
  - **Score this model** (`ModelScorePanel.vue`, Export tab → City Model): see F-LANDMARK §6.
  - Known limits: a pack whose placement window is not in the Overpass cache takes minutes (the
    placement fetches buildings, decks, parks and water for a new window); the sidecar bbox is
    the plate's unrotated extent about the placed centre, with the turn kept in `placement`.

## Target files

`geo2stl/hydrology.py`, `app/server/routers/composite.py` (layer sources), composite
client spec + panel, `app/server/core/city_data.py` (size guard), presets, `routers/reports.py`,
`app/server/core/mesh_import.py`, new registration panel, `docs/sop/`.

## Success criteria

- A 100 km terrain export with rivers carved relative to the ground, watertight, in < 2 min.
- Three large-region reference renders + SOP section.
- A user can place a plate, register it and see its report and score from the UI.
