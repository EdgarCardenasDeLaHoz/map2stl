# Trails

The ski and hiking trails layer: sources, grids, display, and how trails reach the model. Related:
[osm-water-hydrology.md](osm-water-hydrology.md) (fetch failure vs empty), [frontend.md](frontend.md),
[composite.md](composite.md).

### 2026-09-27 — Trails are opt-in and filtered
- **Decision:** trails are a city-model layer built into the mesh by `build_on_terrain`, **off by
  default** and fetched only when enabled. The fetch keeps paths, tracks, bridleways, hiking routes
  and footways with `sac_scale` / `trail_visibility` / a name (no sidewalks, crossings, steps);
  `filter_trails` then drops trails with > 50 % of their length within 60 m of buildings or 6 m of
  roads. The OSM fetch is cached 7 days (`trails_osm`).
- **Why:** on Granada trails were the heaviest draped layer (425 k faces vs 200 k terrain; 4,293
  trails) and cost 40 s of Overpass per build. Filtered: 4,293 → 219 fetched → 136 built; cached
  rebuild 0 s. Town footpaths are not trails and at 1:3472 a 2 m path is 0.6 mm wide.
- **Rejected:** trails on by default like the other city layers (F-CITYMODEL's original "all on").
- **Supersedes / superseded by:** supersedes [Trails are an overlay only](#2026-08-27--trails-are-an-overlay-only-not-merged-into-the-stl-superseded)
- **Source:** claude/memory-bank/activeContext.md 2026-09-27 (commit 72fbd5a); [F-CITYMODEL](../plans/active/F-CITYMODEL-vector-city-model.md) build-speed notes; `city2stl/city_model.py::filter_trails`

### 2026-08-28 — New layers enter the composite at weight 0 and signal across bundles with window events
- **Decision:** a newly loaded layer (trails) enters the composite at weight 0 —
  `_addWeightedFeature` refuses non-positive weights, so 0 is a real off switch. `renderTrails`
  dispatches a native `trails-rendered` window event rather than using `window.events`.
- **Why:** loading a layer to look at it is not a request to carve it. The Vue bundle and `main.js`
  mount independently, so a component subscribing to the internal bus in `onMounted` may find it
  undefined and fail silently.
- **Supersedes / superseded by:** —
- **Source:** decisions.md "Trails, fetch settings versus view settings" entry

### 2026-08-28 — Categorical grids are never interpolated and never zero-backfilled
- **Decision:** the ski difficulty grid is projected nearest-neighbour; a cache entry predating it
  is a miss, not zero-filled. It rides the float32 grid transport (exact for small ints), decoded
  by the existing `_decodeGrid`, commented as classes at both ends.
- **Why:** an all-zero class grid means "no graded pistes" — a wrong answer, not a missing one.
  Third instance of the shape (poisoned semantic cache, partial water fetch): never let a partial
  result be representable as a valid complete one.
- **Rejected:** a uint8 encode/decode path — 1 byte vs 4 per pixel, not worth a second transport.
- **Supersedes / superseded by:** —
- **Source:** decisions.md "Trails, fetch settings versus view settings" entry

### 2026-08-28 — A control belongs in Fetch only if it changes the request
- **Decision:** everything the client can recompute from a response it holds lives in the layer's
  View section. For trails, category, area fill, colour and difficulty colouring are repaints.
- **Why:** one `/api/terrain/trails` response carries both categories, both area masks and the
  piste grade. Applying the rule to other layers is the follow-up.
- **Supersedes / superseded by:** —
- **Source:** decisions.md "Trails, fetch settings versus view settings" entry

### 2026-08-28 — Areal trail features engrave their boundary and only tint their interior
- **Decision:** `rasterize_trails` reduces closed-way pistes and ski-area polygons to their
  buffered boundary; interiors return as separate 0/1 masks (`ski_area_grid`, `hiking_area_grid`)
  that the client tints at alpha 46 and that never enter the DEM. Old cache entries without masks
  read as zeros.
- **Why:** buffering polygons painted Breckenridge as solid cyan and would have cut the mountain
  face out of the STL. Zero area mask = "no areal features", which degrades in the right direction.
- **Rejected:** one categorical grid — the client could not restyle areas without a refetch and the
  relief grid would not stay clean for the mesh.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 entry; `geo2stl/trails.py::rasterize_trails`

### 2026-08-27 — Trails return two grids in one response and OSM is authoritative for ski
- **Decision:**
  - Ski and hiking come back as separate grids in one response; toggles repaint from the retained
    payload. Neither projection nor category is in the cache key.
  - Providers: OSM (`OsmTrailsLayer`) for ski and hiking, USDA Forest Service EDW for US hiking,
    unioned when `source=all` (`TrailsService`). Modelled on the hydrology layer.
  - Default engraves rather than raises (a groove survives slicing better than a thin ridge).
  - No `MergeAlg` argument to rasterio (there is no `min`; all shapes carry the same value).
- **Why:** one category per request would put a tens-of-seconds Overpass query behind a checkbox.
  OpenSkiMap / OpenSnowMap are rendered *from* OSM and publish tiles or dumps, not bbox queries.
  Verified on Breckenridge: 340 ski + 859 hiking from `osm+usfs`.
- **Rejected:** OpenSkiMap / OpenSnowMap — same geometry by a slower route.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-27 entry; [F-TRAILS1](../plans/done/F-TRAILS1-ski-hiking-trails-layer.md)

### 2026-08-27 — Trails are an overlay only, not merged into the STL [superseded]
- **Decision:** F-TRAILS1 shipped trails as a display overlay; merging into the exported DEM was
  left open (the hydrology-merge precedent route was itself broken).
- **Supersedes / superseded by:** superseded by [Trails are opt-in and filtered](#2026-09-27--trails-are-opt-in-and-filtered)
  (trails are now a city-model mesh layer; also a composite channel with weight and enable toggle)
- **Source:** [F-TRAILS1](../plans/done/F-TRAILS1-ski-hiking-trails-layer.md) Status and Risks
