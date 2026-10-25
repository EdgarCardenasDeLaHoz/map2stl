# F-PLINTH — Building plinths on slopes

_Proposed 2026-10-14 (user choice from the "more realistic City Model" form). Status: planned._

## Goal

On sloping ground, each building should stand on a level base at the lowest ground point under
its footprint, the way a real hillside building sits on a terrace cut into the slope, and its
height should be counted from that point. Today the opposite happens: the roof is measured from
the *highest* ground point, so every building on a slope prints too tall on its downhill side.

## What exists today

- `city2stl/city_model.py::build_layer` (extrude branch) reads the lowest and highest terrain
  under each footprint from `city2stl/city_model.py::Terrain` (`ranges_under`: the footprint's
  vertices plus the DEM cells inside). The floor is `lo - 0.2` mm, a skirt into the ground so
  nothing floats, and the top is `hi + height_m * z_mm_per_m`. The same rule is used by
  `_roofed` and `merge_flat_roofs`. It was chosen on 2026-09-26 ("features seated on the DEM":
  highest point, with a skirt to the lowest; `docs/decisions/mesh-pipeline.md`).
- So buildings already have a flat bottom, but it is buried. Nothing visible is levelled, and
  the terrain keeps its slope right up to the walls.
- OSM's Simple 3D Buildings `height` is measured from the lowest point where the building
  touches the ground, so adding it to the highest point overstates the height by the slope drop
  across the footprint. At Granada (1 mm = 2.5 m, true vertical scale 0.4 mm per metre) a
  building 20 m deep on a 20 % slope drops 4 m, so a 10 m building prints 1.6 mm, or 40 %, too
  tall on its downhill side.
- The landmark preview already shows a building "on a plinth"
  (`app/server/core/landmarks.py::preview`), but that is a display stand, not this feature.

## Approach

- **Level pads in the terrain, not cuts in the solids.** Before the terrain TIN is built
  (`city2stl/city_model.py::terrain_tin`, called from `build_on_terrain`), rasterise each
  building's pad (the footprint buffered by a margin: 0.4 mm on the print or 1 m real, whichever
  is larger) onto the 1 px/mm heightfield and set those cells to the pad's level, the
  footprint's `lo`. Every draped layer (roads, green, trails) samples the same `Terrain`, so a
  road running past a pad follows the terrace instead of floating over a hole, which a boolean
  cut on the finished solid could not give.
- **Height from the pad.** The building's top becomes `lo + height` instead of `hi + height`,
  in `build_layer`, `_roofed` and `merge_flat_roofs` (whose "same print layer" test compares
  absolute tops, so it must use the new ones). Building parts (`is_part`) keep standing on
  their own base, as now.
- **Only where it shows.** A pad is made only when `hi - lo` is at least one print layer
  (`LayerStyle.layer_height_mm`, 0.1 mm) and at most a cap (default 3 mm of drop). A building
  on a cliff above the cap keeps today's seating, so the model does not get a pit. Overlapping
  pads in a dense block are merged and take the block's lowest level, which gives the stepped
  terraces of hill towns.
- **A seating option on the buildings layer** (`LayerStyle`, for example `seat="pad"`, with
  `seat="skirt"` for today's rule) so the old look stays one switch away. The counts (`pads`,
  `pads_capped`, mean and largest drop) go into the layer report.

## Target files

- `city2stl/city_model.py::build_on_terrain` (pad pass before `terrain_tin`),
  `city2stl/city_model.py::Terrain` (`ranges_under`, plus a new pad-flattening helper),
  `city2stl/city_model.py::build_layer`, `city2stl/city_model.py::_roofed`,
  `city2stl/city_model.py::merge_flat_roofs`, `city2stl/city_model.py::LayerStyle`.
- `city2stl/model_cache.py::terrain_key` (the terrain now depends on the building footprints;
  bump `MODEL_CACHE_VERSION`).
- `app/server/core/city_model_task.py::normalize_request` (the seating option);
  `app/client/static/js/vue/components/dem/LayerDisplaySections.vue` (one switch).

## Success criteria

- Granada: for every building with `hi - lo >= 0.1` mm, the printed top equals
  `lo + height * z` within 0.05 mm, and the terrain inside each pad is flat within 0.05 mm.
- Roads next to pads touch the pad surface (no gap over 0.05 mm between road slab and ground
  along pad edges), checked on the Albaicín.
- The reference-city regression set (`tests/test_reference_cities.py::test_reference_city`)
  stays watertight; Cartagena (flat) changes by under 1 % in volume; cold build time grows by
  under 5 %.
- At San Francisco scale (1 mm = 33 m) the drop under a single footprint is mostly below one
  print layer, so almost no pads are made there; the report says how many.

## Risks

- **The terrain cache now depends on buildings**, so a buildings edit rebuilds the terrain TIN.
  The pad pass must be cheap and keyed by the footprints only.
- **The 1 mm grid stair-steps the pad edge.** A pad is a few pixels wide at Granada's scale, so
  its outline is stepped at 1 mm. Rasterising finer inside the pad mask, or snapping TIN
  vertices to the pad outline, may be needed.
- **Heights change on every hillside build**, which moves the regression baseline and any stored
  comparison against printed plates. Record the new baseline in the same change.
- **Merged roofs**: `merge_flat_roofs` may merge different buildings than before; compare its
  counts before and after.

## Status (2026-10-23, T50)

- [x] Pad pass before the terrain TIN (`city_model.py::level_pads`, called from `build_on_terrain`).
- [x] Heights from the pad: no change to `build_layer` / `_roofed` / `merge_flat_roofs` needed (lo == hi on the flattened terrain).
- [x] Only where it shows: min one print layer, cap 3 mm; clusters take the lowest level.
- [x] Option: request field `level_ground` (default on) instead of `LayerStyle.seat`; Composite tab switch.
- [x] `MODEL_CACHE_VERSION` 8; tests `tests/test_city_plinth.py`.
- [ ] Granada / Albaicin / reference-city measurements (needs cached data); new regression baseline.
