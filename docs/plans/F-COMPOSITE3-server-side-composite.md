# F-COMPOSITE3 — Move the composite DEM to the server

**Status: pass 1 complete (2026-09-06). Passes 2 and 3 open.**

## Goal

The composite the user configures in the Composite panel is currently computed
entirely in the browser. Move that computation to the server, so the server
adds and subtracts the requested layers from the DEM, returns one combined
heightmap, and the 3D model and every export are built from it.

The transport for this already exists and is not being invented here.
`compute_composite_dem` (`app/server/routers/composite.py:352`) is already called
inline by the export pipeline (`app/server/core/export_params.py:99-107`) at the
highest priority, and `generate_mesh_preview` plus every export format share
`_parse_export_params`. What is missing is the arithmetic and the layer sources,
not the wiring.

## Shape of the request: an ordered layer list

The composite is expressed as an ordered list of `MergeLayerSpec`, not as a flat
set of named channel parameters. Each entry names a source, a blend mode, a
weight, and a processing pipeline; the server fetches, processes, and blends
them in order. This was chosen over a flat spec mirroring the browser's
parameter object because it extends without a schema change: a new channel is a
new registered source, layers can be reordered, and the same source can appear
more than once.

The browser's flat parameters map onto it cleanly:

| Channel | source | blend_mode | weight |
|---|---|---|---|
| DEM | `local` / `h5_local` / an OpenTopography dataset | `base` | `demWeight` |
| Water | `water_esa` | `rivers` | `waterDepth * waterWeight` |
| Buildings | `osm_buildings` | `add` | `buildingScale` |
| Roads | `osm_roads` | `rivers` | `roadCut` |
| Waterways | `osm_waterways` | `rivers` | `riverDepth` |
| Walls | `osm_walls` | `add` | `wallScale` |
| Trails (pass 2) | `trails_ski`, `trails_hiking` | `rivers` | `trailsWeight` |
| Land cover (pass 2) | `esa_landcover` | `add` | `landcoverWeight` |
| Satellite vegetation (pass 3) | `sat_vegetation` | `add` | `satWeight` |

`rivers` mode is `base - layer * weight`, which is exactly how the browser
subtracts: its water contribution is a 0/1 mask scaled by a depth, and its road
and waterway contributions are rasters scaled by a cut depth.

One thing does not fit a scalar weight: the ESA class-to-height table, which
maps 11 WorldCover class codes to 11 heights. That is why `MergeLayerSpec` gains
an `options` dict — a per-source parameter bag for anything that is not a
weight. Land cover is a pass-2 concern; the field is added in pass 1 so the
source registry can rely on it.

## Approach

**Pass 1 (this pass) — foundation and the city channels.**

1. Add an `add` blend mode to `blend_layers` (`geo2stl/processing.py:78`).
   The existing six modes are `base`, `replace`, `blend`, `rivers`, `max`,
   `min`; there is no additive mode at all, and four of the nine composite
   channels add.
2. Add a **source registry** to `geo2stl/dem.py` so `fetch_layer_data` can be
   extended from outside the library. The OSM rasterizers live in
   `app/server/routers/composite.py` and depend on the server-side OSM cache, so
   `geo2stl` cannot import them without inverting the layering. A registry
   inverts the dependency instead: `geo2stl` offers `register_layer_source`, and
   the server registers `osm_buildings`, `osm_roads`, `osm_waterways` and
   `osm_walls` at import.
3. **Project each layer.** `compute_composite_dem` never calls `project_grid`;
   it resizes with `cv2.resize` and returns. Every other layer router projects.
   Without this a server composite does not align with the browser's grid.
   Projection parameters join `MergeRequest` and the composite cache key.
4. **Real tests.** `blend_layers`, `apply_layer_processing` and
   `fetch_layer_data` have no coverage: the only two dem-merge tests run under
   `TEST_MODE`, which replaces the arithmetic with a `linspace` stub, and both
   accept `"error" in result` as a pass. These are pure functions, so they are
   tested directly, outside the endpoint and outside `TEST_MODE`.
5. **Wire the Composite panel to the server** at Apply and Export. The browser
   keeps computing the live preview — a slider drag stays instant — and builds a
   layer spec at Apply time so the mesh and the export come from the server.

**Pass 2** — register `esa_landcover`, `trails_ski` and `trails_hiking`.
Both have server-side producers already (`fetch_bbox_image` with `dataset="esa"`
and `rasterize_trails`), so each is a wrapper plus a registry entry.

**Pass 3** — satellite vegetation, the only channel with no server-side numpy
producer at all. `fetch_satellite_tiles` returns a base64 JPEG and nothing
caches it, so this needs a decode-to-array path, a cache, and a port of the
browser's `(G-R)/(G+R+1)` greenness math.

## Target files

- `geo2stl/processing.py` — `add` blend mode
- `geo2stl/dem.py` — source registry, `fetch_layer_data`
- `app/server/routers/composite.py` — projection in `compute_composite_dem`,
  cache key, source registration, `_rasterize_city` reuse
- `app/server/schemas.py` — `MergeLayerSpec.options`, `MergeRequest` projection
- `app/client/static/js/modules/layers/composite-dem.js` — build the layer spec
- `app/client/static/js/modules/export/export-handlers.js` — send it
- `tests/test_composite_blend.py` (new) — arithmetic coverage

## Success criteria

- A composite applied in the panel produces a mesh built from the server's
  arithmetic, not from values shipped inline from the browser.
- For the same region and settings, the server composite and the browser
  composite agree within a small tolerance. This is the safety net that
  substitutes for the absent test history.
- `blend_layers`, `apply_layer_processing` and `fetch_layer_data` have direct
  tests that exercise real arithmetic rather than the `TEST_MODE` stub.
- No behaviour change for a user who does not open the Composite panel.

## Risks

- **Parity drift.** The browser and the server now both know how to composite.
  Pass 1 keeps both deliberately, because a server round trip on every slider
  drag would be far slower than the present 80 ms debounce. The mitigation is
  the parity test, not the removal of one engine.
- **Weight ceiling.** `MergeLayerSpec.weight` is capped at 10.0. Water depth
  folded into the weight is `waterDepth * waterWeight`, which the panel can push
  past that. The cap needs raising or the depth needs its own field.
- **Untested ground.** Any change to `blend_layers` or `fetch_layer_data`
  currently breaks nothing detectable. Tests land in the same pass as the
  changes for that reason.

## Pass 1 outcome (2026-09-06)

All five pass-1 items are in place.

| Item | Where |
|---|---|
| `add` blend mode | `geo2stl/processing.py:110` |
| Layer source registry | `geo2stl/dem.py:108-137`, used by `fetch_layer_data` |
| The four OSM channels registered | `register_city_layer_sources`, `app/server/routers/composite.py` |
| Projection per layer, and in the cache key | `compute_composite_dem`, `_composite_cache_key` |
| Direct tests, outside TEST_MODE | `tests/test_composite_blend.py` (22 tests) |
| Panel wired to the server | `buildCompositeLayerSpec` in `composite-dem.js`, consumed by `export-handlers.js` |

Four things were decided during implementation rather than in the plan:

- **The weight cap moved from 10.0 to 100.0.** Water folds `waterDepth *
  waterWeight` into one weight and the panel can push that past 10, which would
  have been a 422 on a setting the panel offers.
- **The base layer's weight is now applied.** The first layer sets the output
  grid and never reaches `blend_layers`, so its weight was silently dropped;
  the panel's DEM weight would have meant nothing server-side. It is applied
  where the grid is built, and defaults to 1.0, so no existing spec changes.
- **Dict layer specs are coerced to `MergeLayerSpec`.** The export path sends
  plain dicts from JSON, and `apply_layer_processing` reads its spec by
  attribute, so a dict spec with a processing block would have raised. Coercion
  also gives dict specs the schema's defaults.
- **`detail` joined the city-raster cache key.** It was keyed on (bbox, width,
  height) alone, so coarse and full requests over one bbox collided. Recorded
  in `docs/issues.md` as issue 2b.

**Parity is not yet enforced by a test.** The plan calls for a browser-vs-server
comparison as the safety net; pass 1 ships the two engines and the translation
between them, and the panel falls back to inline values whenever a channel has
no server source, so an export never silently drops a channel. The parity test
belongs with pass 2, when land cover and trails stop being fallback cases.
