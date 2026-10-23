# F-WATERLAYER — One Water layer, by material rather than source

_Approved 2026-10-18 (user: "One Water layer"). Status: planned; PNG mockup first._

## Goal

Water is one layer in the Edit panel and one set of cuts in the model, whatever dataset a river
or lake came from. Today the split follows the source. The user put it this way: "a weird
separation of concerns and layers there based on the source, not what the material actually is."

## What exists today (survey of 2026-10-18)

- **Two groups in the Edit panel.**
  - `vue/stores/editLayers.ts` has `id:'water'` (Rivers & lakes) and `id:'city'` (Buildings &
    roads). `CITY_IDS` includes `waterways`, so the city switch also turns water on and off.
  - `vue/components/dem/settings/layerGroups.ts::GROUPS.city` holds Show water, Water colour,
    Water switch (`cityLayer_waterways_enabled`), Water cut on the map (`compositeRiverDepth`) and
    3D city water offset (`cityWaterOffset`).
  - `GROUPS.water` holds the river size and source, water areas dataset, river depth and width,
    smooth channels and the ESA switches.
  - Lakes have no row of their own.
- **Two carves.**
  - Rivers & lakes rasterise HydroRIVERS / Natural Earth plus lakes (OSM `natural=water` up to
    500 km², ESA above) into the composite (`geo2stl/water_layers.py::river_carve_layers`,
    `make_lakes_source`; `app/server/routers/composite.py`).
  - The City Model then cuts OSM `waterways` (`city2stl/fetch.py::_fetch_waterways`: waterways
    plus `natural=water`) again, 1.0 mm deep (`city2stl/city_model.py::DEFAULT_LAYERS["waterways"]`,
    the water branch of `build_layer`).
  - The composite also subtracts an `osm_waterways` mask
    (`modules/layers/composite-spec.js`, `composite.py::_rasterize_waterways`).
- **Nothing removes the double cut.** OSM lakes are cut by both the `lakes` source and the city
  `waterways` layer, from the same OSM data. Rivers get the hydro carve and then the city cut
  stacked on top. `LAYER_PRIORITY` only merges the city cut solids among themselves.

## Approach

1. **Mockup (PNG) first** (design guidelines). The Rivers & lakes group gains Rivers, Lakes and
   Sea rows, each with an "in the print" switch and a source line ("OSM, gaps from HydroRIVERS").
   Buildings & roads loses its water rows. Approve before building.
2. **One owner per water body on the server.** Rivers come from OSM `waterway=river/stream/canal`
   where the box has them, and from HydroRIVERS where OSM has none (by reach overlap). Lakes come
   from OSM up to 500 km² and from ESA above, as today. Each feature is cut exactly once: in the
   City Model when it builds the region, otherwise in the composite. Add a dedupe step in
   `geo2stl/water_layers.py`, and stop the composite carve under features that the City Model
   will cut.
3. **Frontend move.**
   - Take `waterways` out of `CITY_IDS`.
   - Move its rows from `GROUPS.city` to `GROUPS.water`.
   - Keep the control ids that modules read (`cityLayer_waterways_enabled`, `cityWaterOffset`),
     or rename them in one change with a grep.
   - The City Model request takes the water switch from the Water layer.
4. **Saved settings.** Map the old keys to the new ones in `app/server/routers/settings.py`, so
   existing regions keep their water on or off.
5. **Coordinate with F-WATERSURF** (flat water surface). Do this plan first: F-WATERSURF then
   changes only how the single water cut is shaped.

## Target files

`vue/stores/editLayers.ts`, `vue/components/dem/settings/layerGroups.ts`,
`vue/components/dem/LayerSettings.vue`, `modules/layers/composite-spec.js`,
`modules/layers/city-overlay.js`, `modules/layers/city-render.js`,
`modules/layers/hydrology-print.js`, `modules/export/city-fullmodel.js`,
`modules/ui/presets.js`, `app/server/routers/settings.py`, `app/server/routers/composite.py`,
`app/server/core/city_model_task.py`, `app/server/core/export.py`, `export_params.py`,
`city2stl/city_model.py`, `city2stl/fetch.py`, `geo2stl/water_layers.py`, plus their tests and
`docs/reference/layer-system.md`.

## Success criteria

- Granada and Cartagena: every river and lake appears once in the Edit panel's Rivers & lakes
  group and is cut once. The cut depth of a lake equals the configured depth, not double it.
- Turning Buildings & roads off leaves the water in the print.
- Old saved regions open with the same water on or off.
- Tests pass: `test_city_model.py`, `test_water_layers.py`, `test_vector_rivers.py`,
  `test_composite.py`, `tests/js/layerStackState.test.js`, `compositeSpec.test.js`.

## Risks

- This is a large change across the Edit panel, the composite and the City Model. Build it in
  two commits: server single-cut first, then the panel move.
- The Edit panel and its `settings/` are owned by local sessions (map2stl CLAUDE.md, cloud rules).
  Run this locally, not as a cloud task.
- HydroRIVERS reaches and OSM rivers do not line up exactly. Overlap-based dedupe needs a
  tolerance, about the river width plus the DEM pixel size.
