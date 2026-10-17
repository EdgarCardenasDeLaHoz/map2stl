# F-WATERSURF — Water as a flat surface just below the banks

_Proposed 2026-10-14 (user choice from the "more realistic City Model" form). Status: planned._

## Goal

Rivers, lakes and the sea should print as a flat, level water surface lying slightly below their
banks, so that water reads as water and not as a trench, or as a groove that copies every bump
of the terrain.

## What exists today

- The `waterways` layer already has its own mode, `water`
  (`city2stl/city_model.py::DEFAULT_LAYERS`: `offset_mm=1.0`, `line_width_m=6.0`). It is
  fetched by `city2stl/fetch.py::_fetch_waterways` (`waterway=*`;
  `natural=water/wetland/coastline/bay/strait`; `landuse=reservoir/basin`; `place=ocean/sea`).
- In `city2stl/city_model.py::build_layer` (water branch), standing water (lakes, reservoirs)
  is already cut flat to 1.0 mm below its lowest shore point. Flowing water
  (`city2stl/city_model.py::_is_flowing_water`) is a slab cut 1.0 mm below the terrain surface,
  so it follows the terrain: it is level neither along its length nor across its width.
- Water is a *cut*: `city2stl/city_model.py::assemble_model` subtracts the cuts from the ground
  and from every added layer. There is no water body and no water part in the 3MF. The 3D view
  has a colour for a `waterways` part (`city-fullmodel.js::PART_COLORS`), but no such part is
  emitted.
- Coastlines are lines. A buffered `natural=coastline` line is not flowing water, so it is cut
  as a 6 m-wide flat trench along the shore rather than as the sea.
- The sea in a City Model comes from the terrain stage: with `sea_level_cap`,
  `app/server/core/export.py::_prepare_dem_array` raises everything below 0 to 0, so the sea
  prints flat at the same level as the beach, not below it.
- The terrain-stage river carve (`river_depth_mm`, `RIVER_MIN_WIDTH_MM`) is a separate
  mechanism, used mainly for large regions; why it works the way it does is in
  `docs/decisions/osm-water-hydrology.md`.

## Approach

- **Lakes: keep the flat cut, make it shallower.** Change the default depth below the lowest
  shore from 1.0 mm to 0.4 mm (two 0.2 mm print layers), still a `LayerStyle` setting, so the
  water reads as a surface just under the bank and not as a pit.
- **Rivers: level across, stepping down along.** Sample the terrain along each river's
  centreline (for a riverbank area, its skeleton or the nearest `waterway=river` line), take the
  running minimum downstream so the level never rises, and build the cut as flat pieces between
  stations, each at that level minus the offset, with steps no larger than one print layer. The
  surface is then level across the channel and descends with the valley, as a real river does.
- **Sea: a real polygon.** Build the sea polygon from the coastline ways with the existing
  voting code (`city2stl/registration/osm_water.py::osm_water`, `_coast_layer`) and cut it flat
  at the coast's lowest point minus the offset, replacing the coastline trench. Where the
  terrain stage has already flattened the sea to 0, this simply lowers it by the offset.
- **One owner per water cell.** Where the composite river carve and the city `waterways` layer
  both cover a river, the city layer wins inside its polygons, so the depth is not applied twice.
- **Water as its own 3MF part (option, off by default).** The volume between the new surface and
  the old terrain can be kept as a `water` part instead of being cut away, so a multi-colour
  print gets blue water at no extra geometry. The user dropped "rivers and lakes as a separate
  3MF part" for large regions on 2026-09-29 (roadmap, "Not pursuing"), so this stays an opt-in
  to confirm with the user, not the default.

## Target files

- `city2stl/city_model.py::build_layer` (water branch), `city2stl/city_model.py::_is_flowing_water`,
  `city2stl/city_model.py::DEFAULT_LAYERS`, `city2stl/city_model.py::assemble_model` (the
  optional water part).
- `city2stl/registration/osm_water.py::osm_water` (the sea polygon; move the coastline voting
  into a shared helper if the city model needs it outside registration).
- `city2stl/fetch.py::_fetch_waterways` (keep coastline ways whole, with their direction).
- `app/client/static/js/modules/export/city-fullmodel.js::partColor` (`PART_COLORS`: a `water`
  colour, and drop the unused `waterways` entry if it stays unused).

## Success criteria

- Lakes on Granada and Breckenridge: the water top is flat (z spread under 0.01 mm) and lies
  0.4 mm (± 0.05 mm) below the lowest bank vertex.
- Rivers (the Genil at Granada, the Blue River at Breckenridge): the surface is level across
  each cross-section (spread under 0.05 mm) and never rises downstream.
- Cartagena: the sea is one flat surface below the waterfront, and no trench follows the
  coastline.
- The merged model stays watertight on the reference-city set; face count grows under 5 %.

## Risks

- **River centrelines** are missing or broken for some riverbank polygons. A polygon with no
  usable line falls back to today's draped cut.
- **Coastline voting** was written for street-scale plates. On a 20 km box (San Francisco,
  1 mm = 33 m) the vote has much more coastline to resolve, and a wrong side floods the land.
  Test it on San Francisco and Cartagena before it is turned on.
- **Narrow rivers at coarse scales**: at 33 m per mm a 20 m river is 0.6 mm wide and is widened
  to `PRINT_MIN_WIDTH_MM` (0.8 mm). A 0.4 mm recess that narrow is barely visible; that is
  acceptable, but the city guide should say so.
