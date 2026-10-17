# F-COARSEBLD — Coarse building layer for city-scale regions

_Proposed 2026-10-13 (user request). Status: planned._

## Goal

Let a region of San Francisco's scale (roughly 10–25 km across) carry a building layer without
fetching every building footprint from Overpass. At that scale the per-footprint geometry is far
finer than a 1 px/mm print can show, it is the slowest part of the fetch, and it is the part that
fails first (the Breckenridge run on 2026-10-13 lost `buildings`, `walls`, `towers`, `churches`,
`fortifications` and `waterways` to Overpass and kept only `roads`). The coarse layer builds
built-up blocks from cheap OSM polygons and a global raster instead, and keeps the per-footprint
fetch available as ground truth for scoring the coarse layer.

## Approach

- **Coverage from OSM areas, not buildings.** Query `landuse` (residential, commercial,
  industrial, retail), `place=*` built-up areas and the road network, all of which are a few
  hundred polygons for a 20 km box instead of tens of thousands. The block polygons are the road
  network's enclosed faces clipped to those areas, so streets stay as gaps in the printed mass.
- **Density and height from a global raster.** GHS-BUILT-S (built-up surface fraction per cell)
  scales each block's footprint area; GHS-BUILT-H (average building height, 100 m cells, already
  wrapped by `city2stl/height/providers/ghsl.py`) gives its height. Both are 2023A, global and
  CC-BY-4.0, so a coarse layer works outside Europe and the US where WSF3D and national lidar
  do not reach. WSF3D (90 m, DLR) stays the fallback where GHS-BUILT-H is empty.
- **One new layer in the city pipeline**, beside `buildings`: a `built_blocks` layer whose
  features are block polygons with a `height` property, extruded by the same code path as
  buildings so nothing downstream changes.
- **Tier selection.** Below `CITY_MAX_DIAG_KM` (10 km) nothing changes. Between 10 km and
  `CITY_COARSE_MAX_DIAG_KM` (25 km) the coarse layer replaces per-footprint buildings by
  default, with a control to ask for real footprints anyway.
- **Ground truth.** Keep the per-footprint fetch reachable, and add a scorer that rasterises
  both layers to the print grid and reports built-area IoU and a height error (mean and p90)
  against the OSM footprints, so a change to the coarse layer can be measured rather than argued.

## Other coarse sources considered

| Source | Resolution | Coverage | Use |
|---|---|---|---|
| GHS-BUILT-S / -H / -V (JRC R2023A) | 100 m (10 m for S) | global | chosen: surface fraction, height, volume |
| WSF3D (DLR) | 90 m | global, sampled tiles published | fallback height where GHS-BUILT-H is empty |
| ESA WorldCover built-up class 50 | 10 m | global | coverage mask only, no height |
| ESM (European Settlement Map) | 2.5 m | Europe only | sharper coverage inside Europe |
| Google Open Buildings 2.5D | 4 m | Global South | per-building, so not cheaper than OSM where it exists |

## Target files

- `city2stl/coarse_buildings.py` (new): block polygons from OSM areas + roads, raster lookup,
  height assignment.
- `city2stl/osm.py` / `app/server/routers/cities.py`: the `built_blocks` layer and its Overpass
  query.
- `city2stl/height/providers/ghsl.py`: add the GHS-BUILT-S read beside GHS-BUILT-H.
- `app/client/static/js/modules/layers/city-overlay.js`, `city-render.js`: request and draw the
  new layer in the coarse tier.
- `tools/score_coarse_buildings.py` (new): IoU and height error against the OSM footprints.

## Success criteria

- A 20 km box loads its building layer in under 30 s cold, with no `buildings` Overpass query.
- The printed mass covers the same blocks as the footprint layer: built-area IoU ≥ 0.7 on
  San Francisco and Granada.
- Mean block height within 5 m of the mean footprint height over the same blocks.
- The coarse layer prints as a watertight part in a City Model export.

## Risks

- **Block faces from the road network** need a planar graph; a broken ring turns into a hole or a
  swallowed block (the C triangulator exits 139 on a degenerate ring, see
  `docs/decisions/mesh-pipeline.md`).
- **GHS-BUILT-H is biased low in dense centres** (100 m cells average towers with courtyards), so
  the tallest blocks will read short; the scorer must report that rather than hide it.
- **OSM landuse coverage is uneven** — some cities map it fully, others barely. Where it is
  missing, fall back to the WorldCover built-up class so the layer does not silently vanish.
