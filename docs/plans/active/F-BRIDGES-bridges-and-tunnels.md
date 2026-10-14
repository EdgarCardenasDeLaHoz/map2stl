# F-BRIDGES — Bridges at deck height, tunnels left out

_Proposed 2026-10-14 (user choice from the "more realistic City Model" form). Status: planned._

## Goal

Roads and railways that cross water or a valley on a bridge should print as a level deck at
bridge height, standing on supports, instead of being draped down into the river bed. Roads in
tunnels should not be printed on top of the hill they pass under.

## What exists today

- No bridge handling at all. `city2stl/fetch.py::_fetch_roads` takes the osmnx `drive`
  network, whose edges carry `bridge`, `tunnel` and `layer`, but it keeps only `highway`,
  `name`, `lanes`, `maxspeed` and `road_width_m`, so those tags are thrown away. Railways
  (`city2stl/fetch.py::_layer_jobs`, `railways`) keep only `name` and `railway`.
- Roads and railways are `raised` layers (`city2stl/city_model.py::DEFAULT_LAYERS`): a slab
  0.4 mm (roads) or 0.3 mm (rail) above the terrain that follows it everywhere, including down
  a valley side and across a river bed.
- Worse, water is a cut that `city2stl/city_model.py::assemble_model` subtracts from every
  added layer, and the flowing-water slab reaches 1.0 mm above the terrain, so every road and
  rail line is severed where it crosses a river. Bridges today print as gaps.
- Tunnels are drawn on the surface like any other road.
- F-TREES (the buildings-vs-vegetation classifier) mentions viaducts, but only as something to
  tell apart from buildings in a scanned model; it does not build them.

## Approach

- **Keep the tags.** Add `bridge`, `tunnel`, `layer` and `bridge:structure` to the columns
  kept by `_fetch_roads` and by the `railways` polygon job, and also fetch `man_made=bridge`
  outlines where they exist (deck width and extent). Cached city data lacks these columns, and
  the OSM cache key (`geo2stl/cache.py::osm_cache_key`) has no version part today, so either
  the key gains one or a cached `roads` layer without a `bridge` column is treated as stale;
  either way the first build after the change re-fetches.
- **Drop tunnels.** Road and rail segments with `tunnel=yes` (or `building_passage`, `culvert`
  for waterways) are left out of the print, and `waterway` culverts are left out of the water
  cut, so streams no longer cut through roads where they pass underneath. This step is small
  and can ship first.
- **Level decks.** A bridge segment (a run of consecutive `bridge=yes` edges) becomes a deck:
  its height runs straight between the terrain heights at its two ends (the abutments) plus the
  road offset, and it is built as a flat-bottomed prism of at least 0.6 mm thickness (three
  0.2 mm layers), not a draped slab. A deck that would not clear the ground below it by at
  least 0.6 mm (short overpasses, or any bridge at a coarse scale) stays draped as today.
- **Supports.** Under a deck, piers (a wall across the deck's width, 0.8 mm thick) go down to
  the terrain or the water bed so that no unsupported span exceeds a set length (default
  12 mm, a span FDM printers bridge without sagging). The deck's underside is horizontal, so
  each span prints as a bridge, never as an overhang.
- **Bridges after the water cut.** Decks and piers are added to the solid after the cuts in
  `assemble_model`, so the river does not cut through them, and they join the `roads` or
  `railways` part of the 3MF.

## Scale behaviour

At Granada (1 mm = 2.5 m) a bridge over the Genil about 5 m above the water clears it by
2 mm, and the 10–20 m spans need few piers. At San Francisco (1 mm = 33 m) the Golden Gate
deck, 67 m above the water, is 2 mm up and 82 mm long; its 27 m width is 0.8 mm, exactly the
minimum printable width, so the main bridges still show while ordinary overpasses fall under the
clearance threshold and stay draped.

## Target files

- `city2stl/fetch.py::_fetch_roads`, `city2stl/fetch.py::_layer_jobs` (railways),
  `city2stl/fetch.py::_fetch_waterways` (culverts).
- `city2stl/city_model.py::feature_polygons` (split features into surface, bridge and tunnel),
  `city2stl/city_model.py::build_layer` (deck and pier solids),
  `city2stl/city_model.py::assemble_model` (adds applied after the cuts),
  `city2stl/city_model.py::LayerStyle` (deck thickness, clearance, maximum span).
- `city2stl/model_cache.py` (`MODEL_CACHE_VERSION`), `geo2stl/cache.py::osm_cache_key` (or the
  staleness check in `geo2stl/cache.py::read_osm_cache`).

## Success criteria

- No road or rail segment tagged `tunnel=yes` appears in the print.
- On Granada and on a San Francisco box with the Golden Gate: every bridge segment over water
  is continuous (one connected piece with its approach roads), its deck clears the water
  surface by at least 0.6 mm along its whole length, and no span between supports is longer
  than the maximum span.
- Roads that cross rivers are no longer severed: the count of road gaps at river crossings
  (measured by intersecting the road centrelines with the water cut) drops to the number of
  real fords.
- The merged model stays watertight; the puzzle keeps its volume check on the reference cities.

## Risks

- **Deck heights from the ends** are wrong where a bridge's ends are themselves on ramps or
  viaducts; consecutive bridge edges must be chained first, and `layer` used only to order
  stacked decks, not to set heights.
- **Thin, long decks are fragile**: a 0.8 mm-wide deck 80 mm long will snap off a print bed
  or a puzzle piece. Puzzle cuts should avoid decks, or decks should get a minimum width above
  the general one.
- **Piers in the river** need the water cut to leave them standing; adding bridges after the
  cuts handles that, but the 3MF part split must still keep parts disjoint.
- **Re-fetch cost**: every cached region re-fetches roads once after the cache bump.
