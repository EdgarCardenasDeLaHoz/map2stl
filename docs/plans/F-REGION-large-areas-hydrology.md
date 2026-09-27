# F-REGION — Large regions with rivers, and registration in the UI

Status: planned 2026-09-27 (user: "do the same thing for new other examples, large areas
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

1. **Rivers in the terrain stage**: register `hydrorivers` and `natural_earth_rivers` as
   geo2stl layer sources (depth relative to ground, width in metres from order or
   discharge, rasterised at the DEM grid); expose them in the Composite panel; they then
   reach every export through `terrain_stage`.
2. **Lakes without Earth Engine**: OSM `natural=water` (≥ size threshold) or HydroLAKES as a
   flat-cut layer; ESA stays optional.
3. **Large-region preset**: Region preset (terrain only, rivers on, Vertical auto → fit,
   30 m source up to ~100 km, 90 m beyond), city layers disabled with a size guard on
   `get_city_layers` for boxes > 25 km.
4. **Examples + SOP**: render and review three new regions end to end (e.g. Grand Canyon
   ~100 km, Middle Rhine valley ~50 km, Sierra Nevada + Granada ~60 km), write the SOP
   section "Large regions and rivers", and record reference results like §1 of the city SOP.
5. **Registration in the UI**: `/reports` roots for `_reports/` (plate registration) and
   `tools/align_tool/data/`; auto-register returns the report path and score breakdown;
   a "Plate registration" panel wrapping street placement + auto-register as background
   tasks; "score this model" against a registered plate or lidar nDSM using the promoted
   critic (F-LANDMARK §6).

## Target files

`geo2stl/hydrology.py`, `app/server/routers/composite.py` (layer sources), composite
client spec + panel, `app/server/core/city_data.py` (size guard), presets, `routers/reports.py`,
`app/server/core/mesh_import.py`, new registration panel, `docs/sop/`.

## Success criteria

- A 100 km terrain export with rivers carved relative to the ground, watertight, in < 2 min.
- Three large-region reference renders + SOP section.
- A user can place a plate, register it and see its report and score from the UI.
