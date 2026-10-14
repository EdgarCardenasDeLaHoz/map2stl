# Composite DEM

How the composite heightmap is specified, computed (browser and server) and controlled. Layering
rationale: [composite-dem-design.md](../reference/composite-dem-design.md). Related:
[osm-water-hydrology.md](osm-water-hydrology.md) (rivers and lakes as composite sources),
[frontend.md](frontend.md).

### 2026-10-14 — The base DEM and the river carve are computed once per box on a shared grid, not per projection
- **Decision:** the composite and the rivers preview read a built-in base DEM once onto
  `geo2stl/projections.py::shared_source_shape` (the largest projection's source grid, capped at
  2 x dim; `geo2stl/dem.py::shared_base_grid`, memory LRU of 4, disk for OpenTopography, single
  flight). Rivers are carved once on that grid and each projection pools the per-order stack down
  to its own source grid by an exact partition max (`geo2stl/water_layers.py::pool_river_stack`;
  `pool_deepest` for the composite's relative grid). `COMPOSITE_CACHE_VERSION` 9, rivers preview
  print key v3; `RIVER_CARVE_VERSION` unchanged (a carve for a given key is unchanged).
- **Why:** the source grid depends on the projection, so each switch changed the carve key
  (shape and DEM hash) and re-ran everything. Europa at 600 px: composite 12.9 s and hydrology
  14.4 s per switch (river_carve 9.9 s, DEM GeoTIFF decode 3.6 s per request). Now a new
  projection with warm region caches: hydrology 0.14-0.26 s, composite 0.8-1.05 s. Against the
  old per-projection carve, 99.4-99.9 % of river pixels lie within 1 px (cosine, sinusoidal,
  none, lambert); Mercator, whose grid is the shared grid, is identical. The exact IoU is
  0.40-0.55 because channels sit one pixel apart, not because they are lost.
- **Rejected:** carve per projection with an LRU and single flight only (still ~10 s for each
  new projection); `resize_relative`'s ceil-window minimum filter for the pooling (a factor just
  above 1 becomes a 2-cell window and doubles river width); nearest resampling (drops 1-px
  rivers); reprojecting the river vectors per projection (still a snap and burn per projection,
  ~8 s).
- **Supersedes / superseded by:** —
- **Source:** issues.md "Edit map: changing the projection changed nothing visible"; tests in
  `tests/test_shared_grid_carve.py`.

### 2026-09-06 — Both engines keep compositing and the export falls back to the browser's values
- **Decision:** the browser keeps the live preview; the server composites at Apply and Export. An
  export uses the server spec only when every enabled channel has a server source; otherwise it
  ships the browser's computed values inline.
- **Why:** a server round trip per slider drag is far slower than the 80 ms client debounce. A
  partial server composite would export a mesh silently missing a layer the user enabled — worse
  than a slower, larger request.
- **Rejected:** remove one engine — the parity test is the mitigation, not deletion; send the spec
  anyway and build what the server can — silent loss.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-06 entry; [F-COMPOSITE3](../plans/active/F-COMPOSITE3-server-side-composite.md) Risks

### 2026-09-06 — Server composite pass-1 choices made during implementation
- **Decision:**
  - `MergeLayerSpec.weight` cap raised 10 to 100 (water folds `waterDepth * waterWeight` into one
    weight, which the panel pushes past 10 → would 422).
  - The base layer's weight is applied where the grid is built (default 1.0); it never reaches
    `blend_layers`, so the DEM weight was silently dropped.
  - Dict layer specs are coerced to `MergeLayerSpec` (export sends JSON dicts; processing reads by
    attribute).
  - `detail` joined the city-raster cache key (coarse and full requests collided).
- **Why:** each was a latent failure found wiring the panel to the server.
- **Supersedes / superseded by:** —
- **Source:** [F-COMPOSITE3](../plans/active/F-COMPOSITE3-server-side-composite.md) Pass 1 outcome; `app/server/schemas.py::MergeLayerSpec`

### 2026-09-06 — The server composite request is an ordered layer list
- **Decision:** the request is an ordered list of `MergeLayerSpec` (source, blend mode, weight,
  processing, `options` bag), not a flat mirror of the panel's ~25 parameters.
- **Why:** extends without a schema change (new channel = new registered source; reorder; same
  source twice). The panel maps losslessly: the browser's arithmetic is already
  `bScale*buildings - rCut*roads - rDepth*waterways + wScale*walls`, four independent terms = four
  layers. The only misfit, ESA's 11-class height table, is why `options` exists.
- **Rejected:** flat spec — cheaper now, but schema + client + server edits for every channel, and
  passes 2-3 add three more.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-06 entry; `app/server/routers/composite.py::compute_composite_dem`,
  `app/server/routers/composite.py::register_city_layer_sources`

### 2026-09-06 — An off switch and a zero weight are separate controls
- **Decision:** land cover, satellite vegetation and trails got an enable checkbox beside the
  weight, gated `enabled && weight > 0`, default true (output unchanged, no migration).
- **Why:** when zero doubles as off, switching off destroys the setting and it cannot be switched
  back on.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-06 entry

### 2026-09-06 — The layer engine owns stack state and the rack is a view
- **Decision:** `stacked-layers.js` owns order and active set (`getLayerOrder()`,
  `getActiveLayers()`, `layer-stack-changed` event); the rack in `LayerViewSection.vue` is a pure
  view. New per-layer properties go in its LAYERS table; class-specific extras in
  `LayerDisplaySections.vue`.
- **Why:** two racks disagreed about what a layer has; the hidden one existed because the module
  rendered UI instead of publishing state. A module that mutates shared state announces it; the UI
  never keeps a hand-maintained copy.
- **Rejected:** merging the two racks' markup — fixes the symptom, not ownership.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-06 entry

### 2026-09-05 — The Composite panel stays in application order
- **Decision:** Fetch and View list layers in `_layerOrder`; the Composite panel lists them in the
  order the arithmetic applies them, matching its pipeline caption.
- **Why:** consistency between a panel and what it controls beats consistency between panels.
- **Rejected:** make all four panels identical — the caption would describe something the panel no
  longer shows.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-05 entry

### 2026-07-19 — City data has two size tiers
- **Decision:** tier 1 (≤10 km diagonal) keeps full per-building detail; tier 2 (10-25 km) fetches
  roads, water and large buildings only (area threshold, no walls).
- **Why:** an 18.3 km SF region showed only tree height — client (10 km) and server (15 km) gates
  silently dropped all OSM data while land cover had no gate. User chose a coarse tier over lifting
  the cap.
- **Rejected:** one tier / raise the detailed cap — Overpass cost on dense cities.
- **Supersedes / superseded by:** —
- **Source:** [F-COMPOSITE2](../plans/done/F-COMPOSITE2-rebuild.md) Design decisions (AskUserQuestion)

### 2026-07-19 — Every contribution is a toggleable channel with its own histogram
- **Decision:** the base DEM became a toggle + weight channel; `cityWeight` split into independent
  buildings / roads / waterways / walls toggles + weights; histograms both per group and for the
  final composite; split view is a basic dual pane (composite | satellite) with synced pan/zoom.
- **Why:** user request — every layer should contribute its own channels; the split view avoided
  redesigning the stacked-layers architecture.
- **Rejected:** a full stacked-layers redesign for split view.
- **Supersedes / superseded by:** —
- **Source:** [F-COMPOSITE2](../plans/done/F-COMPOSITE2-rebuild.md) Design decisions
