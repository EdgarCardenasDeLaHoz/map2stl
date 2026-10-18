# F-CITYTREES — Trees in the printed City Model

_Proposed 2026-10-14 (user choice from the "more realistic City Model" form). Status: planned._

> **Lidar canopy moved to F-DETAIL D3 (2026-10-17).** Where a vegetation-capable survey covers
> the box, trees are built from lidar as a canopy (`city2stl/canopy.py`, layer `trees`):
> [F-DETAIL-survey-detail.md](F-DETAIL-survey-detail.md). This plan stays for OSM-only trees
> (`natural=tree` points, `tree_row` lines, crowns) where no survey exists.

(Not F-TREES: that ID is the buildings-vs-vegetation classifier in
[F-TREES](F-TREES-buildings-vs-vegetation.md), which reads trees *out of* scanned models.)

## Goal

Print trees as small rounded crowns, about 2–4 mm across, sitting on the ground and printed in
the green layer's colour: single trees and tree rows from OSM, and woods and forests as a
canopy of crowns, so parks, avenues and wooded hillsides read as such.

## What exists today

- No tree is fetched or built. OSM `natural=tree` (points) and `natural=tree_row` (lines) are
  not queried anywhere in `city2stl/fetch.py`.
- The `green` layer (`city2stl/fetch.py::_layer_jobs`, `green`) fetches parks, gardens, grass,
  meadows, forests, woods, scrub and grassland as polygons. It prints as a `raised` slab 0.2 mm
  above the terrain (`city2stl/city_model.py::DEFAULT_LAYERS`), so a forest looks the same as
  a lawn.
- `city2stl/roofs.py::_revolved` already builds revolved solids (dome, onion, cone, round) on a
  centre point with a radius and a rise. A tree crown is the same kind of solid.
- The roadmap already notes that draped layers repeat terrain detail on steep ground
  (Breckenridge green), and that green is one of the heaviest layers in faces (233 k on Granada
  before the 2026-09-27 speed pass).

## Approach

- **Fetch.** A new `trees` job in `city2stl/fetch.py::_layer_jobs` for `natural=tree` (nodes,
  keeping `height`, `diameter_crown`, `leaf_type`) and `natural=tree_row` (ways). Woods and
  forests come from the `green` layer already fetched, so no extra query is needed for them.
- **Crowns.** Each tree is a low-poly revolved dome (built like `roofs.py::_revolved`, about 8
  segments) standing on the terrain at its point. Its diameter is the real crown size
  (`diameter_crown`, otherwise 8 m) at model scale, clamped to 2–4 mm, and its height about
  0.7 times the diameter, capped by the `height` tag. A dome has no overhang beyond 45° that
  matters at this size, so it prints without supports on a 0.4 mm nozzle. Tree rows place
  crowns along the line at one crown diameter apart.
- **Woods and forests.** Inside `natural=wood` and `landuse=forest` polygons, scatter crowns on
  a jittered grid one crown diameter apart (fixed seed, so a rebuild gives the same trees),
  keeping clear of buildings, roads and water. Overlapping crowns are unioned into one canopy
  solid per wood, which also keeps the face count down. A cap on crowns per model (default
  20,000) keeps the build within budget; above it, the spacing grows.
- **A layer of its own, printed as green.** `trees` becomes a layer in `DEFAULT_LAYERS` and
  `LAYER_PRIORITY` (just before `green`), with its own switch and crown size, and the 3MF puts
  its solids into the `green` part so a multi-colour print needs no extra filament.

## Scale behaviour

At Granada (1 mm = 2.5 m) an 8 m crown is 3.2 mm, true to scale. At San Francisco
(1 mm = 33 m) the same crown is 0.24 mm, under the 0.8 mm minimum printable width, and a 2 mm
crown would stand for a 66 m tree. So the layer switches individual trees off when the true
crown size falls below 1 mm on the print (about 8 m per mm and coarser), and woods then fall
back to the existing green slab, perhaps raised a little (0.6 mm) so they still read as
denser than lawns. The layer report says which rule applied.

## Target files

- `city2stl/fetch.py::_layer_jobs` (the `trees` job), `city2stl/fetch.py::fetch_osm_data`.
- `city2stl/city_model.py::DEFAULT_LAYERS`, `city2stl/city_model.py::LAYER_PRIORITY`,
  `city2stl/city_model.py::build_layer` (a `trees` mode: points and lines to crowns; woods
  scattered), `city2stl/city_model.py::assemble_model` (trees into the `green` part).
- `city2stl/roofs.py::_revolved` (shared crown solid).
- `app/server/core/city_model_task.py` (`OSM_LAYERS`, `normalize_request`),
  `app/client/static/js/vue/components/dem/LayerDisplaySections.vue` (switch and size),
  `app/client/static/js/modules/export/city-fullmodel.js::partColor`.

## Success criteria

- Granada: every fetched `natural=tree` inside the box is a crown in the print (built count
  equals fetched count less those under buildings or roads), each crown is 2–4 mm across, and
  every crown touches the terrain (no floating crowns).
- Face budget: the trees layer adds at most 300 k faces on Granada and the cold build grows by
  at most 15 s.
- The merged model stays watertight on the reference-city set.
- San Francisco scale: the layer builds no individual crowns and says so in the report.

## Risks

- **OSM tree coverage is very uneven**: some cities map every street tree, others none. Woods
  give a model trees even where single trees are missing, but the result will look different
  from city to city.
- **Face count and boolean time**: tens of thousands of small solids are slow to union with
  manifold3d. Building crowns as one batch, and woods as one canopy per polygon, is the main
  defence; measure before tuning.
- **Tiny solids at puzzle cuts**: a crown cut in half by a jigsaw edge leaves a sliver; the
  existing sliver rules in the pre-flight should count them.
