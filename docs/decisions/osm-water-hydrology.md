# OSM, water and hydrology

Fetching OSM through Overpass/osmnx, telling outages from empty answers, water and coastline
rasterisation, and rivers and lakes carved into the terrain. Related: [composite.md](composite.md),
[trails.md](trails.md).

### 2026-10-17 — The river carve burns reaches in numpy, cell for cell what GEOS + GDAL burnt
- **Decision:** `map2stl/geo2stl/water_layers.py::_carve_chunk` no longer sends buffers through GeoJSON to rasterio. All orders are filled in one vectorised pass by `map2stl/geo2stl/polygon_fill.py::burn_polygons_max`, which replicates GDAL's scanline rule (inverse geotransform term by term, row centres at `y + 0.5`, crossings at `floor(x + 0.5)`, bottom horizontal edges filled by ring orientation). Reaches that simplify to one segment (99.5 % on the Amazon) skip GEOS too: `burn_segment_buffers_max` builds the `quad_segs=2` buffer with GEOS's own arithmetic (`stadium_rings`). Buffers with a vertex within `FRAGILE_TOL_PX` (1e-7 px) of a row centre, or a crossing that close to a column boundary, go back to `shapely.buffer` (0.5 % of segments), as do multi-segment, near-zero-length and tiny-radius reaches. `RIVER_CARVE_VERSION` is unchanged because the grid is identical.
- **Why:** Amazon (N 15 S -19 E -45 W -85, dim 800, all orders, 1.23M reaches). Rasterising through GeoJSON cost ~6.7 s per worker chunk, and `shapely.buffer` cost another ~6.4 s. One serial worker chunk (307k reaches) went from 19.0 s to 11.0 s. With 4 workers, `river_depth_by_order` took 30–48 s before and 23–24 s after on the same noisy machine. Snapping (`snap_reaches_to_valley`, ~5 s per chunk) is now the largest cost. The full grid (10 orders × 801 × 942) differs from the old one in 0 of 854,846 carved cells.
- **Rejected:** keeping GeoJSON + GDAL, which made the fill 3× slower than the numpy pass for the same cells. Rejected bit-exact buffer vertices: `cos`/`sin` differ from GEOS's libm by 1 ulp on 7 of 305k reaches. That is harmless away from a cell boundary, which is why the fragile test exists rather than trusting the vertices everywhere.
- **Supersedes / superseded by:** —
- **Source:** `tests/test_polygon_fill.py` (against `numpy2stl.raster.burn_polygons` at three scales).

### 2026-10-17 — Rivers size defaults to Most; a saved choice wins, and All warns on a large region
- **Decision:** the Rivers size control (`#hydroMinOrder`: Big only 5 / Most 3 / All 1) starts at
  Most for every region (`app/server/routers/settings.py::_default_region_settings`). A region's
  saved `hydrology.min_order` is kept as it is. On a region whose longest ground side is 200 km or
  more, the client constant `app/client/static/js/modules/layers/hydrology-print.js::LARGE_RIVER_REGION_KM`
  (the same value as `app/server/core/export_params.py::VECTOR_RIVERS_AUTO_MIN_SIDE_KM`) makes the
  Rivers hint warn when All or size 2 is picked (`layerGroups.ts::riversHint`) and makes the
  loading line say "large region" (`riverLoadingMessage`).
- **Why:** user report 2026-10-17: the Amazon loaded every stream and took about a minute. Most
  was already the default; the Amazon showed All because its saved settings carried
  `min_order = 1` (autosave writes it for nearly every region). HydroRIVERS at size 1 on the
  Amazon is 1.23M reaches and took 57 s.
- **Rejected:** forcing Most on large regions over a saved value — it would undo a choice the
  user made on purpose, and a preset load would disagree with the panel; a second threshold for
  the client — one 200 km number for vector rivers and for the warning keeps "large" one idea.
- **Supersedes / superseded by:** —

### 2026-10-17 — Vector rivers on by default for regions ≥ 200 km
- **Decision:** exports cut rivers as vector solids (HydroRIVERS reaches extruded and
  boolean-cut into the terrain) by default when the bbox's longest ground side is 200 km or
  more (`app/server/core/export_params.py::VECTOR_RIVERS_AUTO_MIN_SIDE_KM`); smaller regions keep
  the raster carve. The request's `vector_rivers` is `"auto"` (default), `true` or `false`
  (`resolve_vector_rivers`). Each reach is cut to its raster print depth (same depth reference)
  in 0.05 mm classes, and reaches under `vector_rivers_min_depth_mm` (0.1 mm) are left out.
  Lakes, the sea and every preview keep the raster carve; a non-linear projection falls back to
  it (`vector_rivers_blocker`). UI: Edit → Rivers & lakes → *Smooth river channels*
  (Auto / On / Off; the shallowest-reach slider is advanced).
- **Why:** user request 2026-10-17 ("add vector rivers as an option in the export", then on by
  default for big regions). The study below found the vector network reads cleanly where the
  carve's 0.5 mm staircase fuses into blobs, and that the cost lay in reaches too shallow to
  print and in one global union. With the 0.1 mm floor and a threaded per-tile union
  (`city2stl/city_model.py::union_polygons`), the Amazon print (600 mm, order >= 3, cached data)
  builds in 55 s (composite 15 s, mesh 40 s) at 1.97 GB peak RAM, 877,670 faces, watertight,
  29,751 reaches kept of 315,707, against 931 s and 5.9 GB in the study. Below 200 km the
  carve's staircase is fine enough and vectors only add faces: the Rhine gorge (57 km, 600 px,
  0.25 mm/px) went from 390,698 to 401,050 faces (+2.6 %), 2.0 to 2.1 s, both watertight.
- **Rejected:** on for every region — no visible gain on small prints for the faces; a face
  or reach-count threshold — the bbox side is known before any data is read, so the UI can say
  what Auto will do.
- **Supersedes / superseded by:** supersedes [2026-10-17 — Rivers stay a raster carve by
  default](#2026-10-17--rivers-stay-a-raster-carve-by-default-vector-river-solids-are-an-opt-in-for-reaches-deep-enough-to-print-superseded).
- **Source:** session 2026-10-17; `tests/test_vector_rivers.py`; renders in
  `output/vector_rivers_check/`.

### 2026-10-17 — Rivers stay a raster carve by default; vector river solids are an opt-in for reaches deep enough to print [superseded]
- **Decision:** the default keeps carving rivers into the DEM. Extruding HydroRIVERS reaches as
  solids and boolean-cutting them into the terrain is worth building only as an option
  ("vector rivers") that keeps reaches carved at least 0.1 mm deep, for large regions.
- **Why:** measured on 600 mm prints of the Amazon, the American West and Europe (HydroRIVERS
  order >= 3; `output/river_solids_study/results.md`). Every boolean was watertight (manifold3d,
  20-62 s, no failed slabs or unions), and the vector channels read as a clean network where
  the carve is a 0.5 mm staircase that fuses into blobs in the Amazon. But faces grow 2.0-6.1x
  (Amazon 603k to 3.68M, West 1.23M to 2.44M, Europe 870k to 2.26M), the Amazon build went from
  17 s to 931 s (672 s unioning about 155k tiny buffers) and from 1.8 to 5.9 GB peak RAM. And
  80-86 % of reaches are carved 0.05 mm deep or less, under one 0.12 mm layer, so they print
  the same either way: depth, not width, decides what shows. Every reach was narrower than the
  0.5 mm minimum width (the widest 0.41 mm), so vector width is always the minimum.
- **Rejected:** vector solids as the default — 2-6x the faces for reaches that cannot print.
- **Open (for an implementation):** a depth-class layer in `city2stl/city_model.py`
  (`DEFAULT_LAYERS`, `feature_polygons` with round caps and joins, `build_on_terrain` taking a
  per-feature depth); per-reach print depth from `geo2stl/water_layers.py::river_depth_by_order`;
  a minimum-depth filter and per-tile fetch/union in `geo2stl/hydrology.py::fetch_hydrorivers`;
  an `ExportContext` flag in `app/server/core/export_params.py`. Known faults: no valley
  snapping (channels can cross canyon walls), 0.05 mm steps where depth classes meet.
- **Supersedes / superseded by:** superseded by [2026-10-17 — Vector rivers on by default for
  regions ≥ 200 km](#2026-10-17--vector-rivers-on-by-default-for-regions--200-km).
- **Source:** river-solid study, session 2026-10-17 (results and PNGs in `output/river_solids_study/`).

### 2026-10-09 — osmnx pins an Overpass host to an address that connects, IPv6 or IPv4, not to its IPv4 address
- **Decision:** `geo2stl/osm.py::install_dns_pin` replaces osmnx's `_http._config_dns` with
  `pin_overpass_host`: the host is pinned to the first of its addresses, in the resolver's order
  (IPv6 first on this PC), that accepts a TCP connection within `PIN_CONNECT_TIMEOUT_S`; when none
  does, the host is left unpinned. The choice is kept `PIN_MEMORY_S` and dropped by
  `mark_overpass_failure`. Installed by `use_overpass_endpoint` (city layers, trails) and
  `city2stl/osm_raster.py::_ox`.
- **Why:** osmnx 2.1 resolves the host with `socket.gethostbyname` (IPv4 only) and patches
  `socket.getaddrinfo` process-wide, to keep its /status slot check and the query on one server.
  On 2026-10-08 IPv4 to overpass-api.de timed out from this PC while IPv6 answered in 0.3 s, so
  every osmnx fetch ended in ConnectTimeout (about 30 min lost on the new benchmark cities; the
  scratch workaround switched the pin off). The pin also caught the raw-QL client and the /status
  probe once osmnx had run in the process. Keeping a pin, on an address that works, keeps osmnx's
  one-server reason. (2026-10-09: overpass-api.de refused all four addresses, IPv4 and IPv6; the
  mirrors carry such hours.)
- **Rejected:** switching the pin off (osmnx's slot check and query could then hit different
  servers of the round-robin); `doh_url_template` (only used when the local resolver fails).
- **Supersedes / superseded by:** —
- **Source:** t40-review, new benchmark cities README (Problem 1).

### 2026-10-02 — Rivers and lakes are carved in print millimetres, at least 0.5 mm wide, and coloured in 3D
- **Decision:**
  - With `river_depth_mm` (Edit → Rivers & lakes → *River depth*, `#compositeRiverDepthMm`,
    default 0.5, saved per region) `app/server/core/export.py::_prepare_dem_array` scales the
    terrain from the uncarved DEM, then adds the carve in mm.
  - The main river (99.5th-percentile carve depth, `method="lower"`) gets that depth;
    shallower carve keeps its ratios, deeper carve is capped.
  - Channels are widened to `RIVER_MIN_WIDTH_MM` (0.5 mm) on the print with a minimum filter.
  - The preview returns the water vertices (`water_idx`, `water_pct`), and
    `model-viewer.js::_paintWater` colours them like the Edit map (rivers 30/100/200, deeper
    = more opaque from 0; open sea 0/100/255).
  - *Depth ×* stays in the Composite panel as the river-vs-lake ratio. Requests without
    `river_depth_mm` (SDK) still carve in metres.
- **Why:**
  - User: "Amazon was rendered without rivers subtracted" and "They should match how they
    rendered in the edit view".
  - Measured, the carve was there but invisible:
    - 5 m at 30 mm per 9,515 m of relief is 0.016 mm.
    - Normalising to the deepest cell gave the main channels 0.2 mm: a few −30 m ESA
      open-water cells set the reference.
    - Rivers were 1 px = 0.26 mm wide, under a 0.4 mm nozzle; the every-2-px preview
      skipped them.
    - Even at 0.5 mm, an uncoloured groove barely showed.
  - Edit's 60/255 opacity floor tinted all the land blue in 3D: vertex colours spread over
    triangles, and order-1 streams are 62 % of the Amazon's cells.
- **Rejected:**
  - Normalising to the single deepest cell — an outlier sets the scale.
  - Keeping *depth ×* as the main control — every region needed a different multiplier
    (large-region guide §2).
- **Supersedes / superseded by:** —

### 2026-10-03 — Lakes come from OSM up to 500 km², from the ESA water mask above; lakes are coloured as open water in 3D
- **Decision:**
  - `geo2stl/water_layers.py::make_lakes_source` uses OSM lakes up to
    `OSM_LAKES_MAX_AREA_KM2` (500 km²). Larger boxes use `water_mask_lakes`: components of
    the ESA open-water mask off the sea, at least 1 ha, flattened as `lake_depth_grid` does
    (shared `_flatten_lakes`).
  - Components whose own surface spreads over `LAKE_SURFACE_SPREAD_M` (5 m) are rivers
    and are left to the rivers layer.
  - `compute_composite_dem(water_out=)` returns a still-water grid (lake carve + ESA water,
    cached; `COMPOSITE_CACHE_VERSION` 6). The preview marks those vertices as open water.
- **Why:** user: "there are better sources for large lakes than osm ... lake george is one
  such case"; chose "Both, by size" (ESA now, HydroLAKES later).
  - Lake George: 451 s → 42 s.
  - Telling lakes from rivers by the shore spread classed Lake George itself as a river:
    the shore ring climbs the valley sides. SRTM flattens lake surfaces, so the surface
    spread is the test: 67 lakes, 19,995 px (OSM: 22,158 px).
  - A 2 m lake carve against ~10 m rivers was barely tinted in 3D; Edit draws lakes as
    open water.
- **Rejected:** HydroLAKES now — an 820 MB download; planned for continent-scale boxes
  (roadmap).
- **Supersedes / superseded by:** supersedes the 10,000 km² skip of [2026-10-02 — OSM lakes
  skip boxes over 10 000 km²](#2026-10-02--osm-lakes-skip-boxes-over-10-000-km-one-fetch-per-box-at-a-time-failed-mirrors-go-last).

### 2026-10-03 — OSM lakes are left out after 60 s and cached when the fetch finishes
- **Decision:** `city2stl/fetch.py::fetch_osm_lakes` runs the fetch on its own pool (one
  per bbox, shared by every caller). A caller waits at most `LAKES_WAIT_S` (60 s) and then
  gets `TimeoutError`, which the composite reports as a skipped layer. The fetch carries
  on and caches its result for the next preview or export.
- **Why:** user chose "Time limit, then skip" after Lake George's preview waited 4+ min on
  a slow Overpass (377 lakes in a 30 × 60 km box).
- **Rejected:**
  - Show without lakes, then update — needs a preview refresh path.
  - Keep waiting — minutes on a slow day.
- **Supersedes / superseded by:** — (still applies to boxes up to 500 km², which keep OSM).

### 2026-10-03 — Overpass mirror probes run in parallel and are remembered for 2 minutes
- **Decision:** `geo2stl/osm.py::healthy_overpass_endpoints` probes the mirrors' `/status`
  in parallel and reuses each result for `PROBE_MEMORY_S` (120 s), under a lock.
- **Why:** Miami walk (2026-10-03): overpass.kumi.systems timed out its probe six times in
  70 s, 10 s each, once per fetch (buildings, lakes, railways, green), and probes ran one
  mirror after the other. The Miami preview waited about 6.5 min.
- **Rejected:** a shorter probe timeout — a busy but working mirror would be skipped.
- **Supersedes / superseded by:** completes the "remember an unhealthy mirror" item of the
  large-region guide §6.

### 2026-10-02 — OSM lakes skip boxes over 10 000 km²; one fetch per box at a time; failed mirrors go last [partly superseded: large boxes use the ESA water mask, 2026-10-03]
- **Decision:** `geo2stl/water_layers.py::make_lakes_source` returns an empty layer over
  `LAKES_MAX_AREA_KM2`; `city2stl/fetch.py::fetch_osm_lakes` holds a lock per cache key;
  `geo2stl/osm.py::healthy_overpass_endpoints` orders mirrors that failed a real query in the
  last `FAILURE_MEMORY_S` after the healthy ones. Fetch failures from network errors log one
  line (`city2stl/fetch.py::_log_fetch_failure`), others keep the traceback.
- **Why:** end-to-end run 2026-10-02. The Amazon Extrude hung > 10 min on a continent-wide
  Overpass lakes query (open water there is the ESA layer anyway). Philadelphia fetched the same
  lakes twice at once and kept retrying overpass-api.de, which was timing out: 175 s → 9 s.
- **Rejected:** a coarser lakes source for big boxes — ESA WorldCover water already covers
  lakes at that scale.
- **Supersedes / superseded by:** —

### 2026-10-01 — HydroRIVERS is rasterised once as a Strahler-order grid; depths are a lookup
- **Decision:** `geo2stl/hydrology.py::rasterize_hydrorivers_orders` burns the highest order per
  pixel; `order_depth_grid` maps orders to depths for a min order, depth and exponent.
  `/api/terrain/hydrology` caches the order grid and water mask without those three
  (base min order `min(requested, 3)`). It returns the order grid for the colour-by-order view.
- **Why:** user: "it takes some time to flip between min ord layers" and asked to colour each
  river by its order. Depth rises with the order, so the per-pixel highest order is the deepest
  carve: identical to the old per-order minimum (checked on the Amazon, max diff 0).
  - Amazon: the first request is 16.7 s; each min-order change after it is 0.1 s.
- **Rejected:** caching each min order separately — every first visit still re-rasterises.
- **Supersedes / superseded by:** —

### 2026-09-30 — Hydrology is rivers united with the open water; carve stays off the sea
- **Decision:**
  - `geo2stl/hydrology.py::fetch_and_rasterize_hydrology` (the `/api/terrain/hydrology` layer)
    returns the union of the river grid and `water_surface_mask`, with water at the full
    depression depth. The mask is ESA WorldCover class 80 plus its no-data (the ocean;
    WorldCover maps land only), united with the local store's sea (`geo2stl/water_layers.py::ocean_mask`:
    cells ≤ 0 m connected to the box edge).
  - The composite's `water_esa` layer (`geo2stl/dem.py::fetch_esa_water_layer`) uses the same mask.
  - `app/server/routers/composite.py::compute_composite_dem` zeroes the river/lake carve on
    the open sea.
- **Why:** user: "HydroRIVERS doesn't provide ocean masks, the union of that and the other
  water mask or the mask from ESA should be taken". Rivers alone notch the shore at every
  coastal mouth while the sea stays at its old level — a ring around the coast once subtracted.
  Basins below sea level that do not reach the box edge (Dead Sea, polders) are land.
- **Rejected:** ESA class 80 alone — it misses the open ocean.
- **Supersedes / superseded by:** —

### 2026-09-30 — HydroRIVERS honours the requested min_order; no automatic thinning
- **Decision:** `geo2stl/hydrology.py::fetch_hydrorivers` and `rasterize_hydrorivers` no longer
  raise the minimum Strahler order on large boxes. Burning is fast enough to take every reach:
  - `geo2stl/water_layers.py::rasterize_river_depth`: vectorised metric transform, snapping
    pre-filtered to reaches ≥ 2 px, buffers with 2 segments per quarter circle;
  - `numpy2stl` `burn_polygons`: GeoJSON built in bulk;
  - the overlay burns reaches under ~1.5 px wide as lines, not buffered polygons.
- **Why:** the user wants renders of all the Amazon's rivers. At order ≥ 3 that is 315,707
  reaches; the old cap silently dropped them to order ≥ 5 (88,799). Timings (40° x 34°):
  - export path: 348 s → 61 s at 1,200 px, with identical river pixels;
  - overlay: 16 s for all reaches, where it took 111 s for 88,799.
- **Rejected:** keeping the cap with a warning — it chose the rivers for the user.
- **Supersedes / superseded by:** —

### 2026-09-27 — River and lake carves are applied after the median filter
- **Decision:** `compute_composite_dem(..., split_carve=True)` returns `(composite, carve)`; the
  export adds the carve in `_prepare_dem_array` right after smoothing, before the sea cap and
  scaling. The 2D preview still shows `composite + carve`.
- **Why:** a 1-px river is 3 of 9 cells in a 3x3 window, so the median erased it (tested).
- **Rejected:** carving before the median.
- **Supersedes / superseded by:** —
- **Source:** [F-REGION](../plans/done/F-REGION-large-areas-hydrology.md) Progress; `app/server/core/export.py::_prepare_dem_array`

### 2026-09-27 — Rivers and lakes are terrain-relative layers sized from discharge
- **Decision:**
  - `hydrorivers`, `natural_earth_rivers`, `lakes` are terrain-relative sources (negative metres
    below ground, blended with `add`), projected nearest-neighbour, deepest value kept on resize.
  - Width/depth from mean discharge (Andreadis 2013: W = 7.2 Q^0.5, D = 0.27 Q^0.39), else Q from
    Strahler order; clamped widths 2-3000 m, depth 0.5-30 m, half-width ≥ 0.55 px.
  - Rivers snapped to the least-cost valley path in a corridor (median offset 98 → 15 m).
  - Lakes from OSM `natural=water` / `landuse=reservoir` ≥ 1 ha, flat at min(shore) − depth,
    levelled against the 3x3 median; river polygons skipped (they cut valley-long trenches).
  - A failing optional layer (ESA without Earth Engine, Overpass outage) is skipped with a warning
    and retried after 15 min, instead of failing the composite.
- **Why:** the old merge set rivers to absolute −5 m; HydroRIVERS lines sat up to ~2 km off the SRTM
  valley (Colorado carved 100-150 m up the walls); Region exports lost all rivers when ESA failed.
- **Rejected:** `rivers` blend mode (subtracts from base, not terrain-relative); HydroLAKES (not
  available locally); ESA as the required lake source.
- **Supersedes / superseded by:** —
- **Source:** [F-REGION](../plans/done/F-REGION-large-areas-hydrology.md); `geo2stl/water_layers.py`, `city2stl/fetch.py::fetch_osm_lakes`

### 2026-09-06 — An exhausted mirror list raises and an empty match stays empty
- **Decision:** `fetch_osm_data` raises `OverpassUpstreamError` (router → 500) when mirrors run out
  with a layer still failing; each layer fetcher separates osmnx's `InsufficientResponseError`
  (matched nothing) from real failures; `_fetch_buildings` goes through `_features_or_none`.
- **Why:** an outage and an empty rural bbox were both 200 (Naples vs Palermo in the same minute).
  Worse, osmnx *raises* on no match, so any bbox with buildings but no `building:part` lost every
  footprint. Verified: Granada 9,996 buildings, mid-Pacific 0 with no error; all 39 cached payloads
  had buildings.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-06 entry; `city2stl/fetch.py::OverpassUpstreamError`, `city2stl/fetch.py::_features_or_none`

### 2026-08-28 — A failed fetch is never reported as an empty result
- **Decision:** every path that can return "nothing" distinguishes "source said nothing" from
  "source did not answer". Trails: `_fetch_tags` returns `(features, error)`; the service raises
  `TrailsUpstreamError`; the router serves `upstream_error: true` and caches nothing. Health probes
  call `raise_for_status()`; a mirror that passes its probe then fails triggers retry on the next.
- **Why:** `requests.head` returns normally on a 500; trails re-implemented a bug `city2stl/fetch.py`
  had already fixed by importing the endpoint list instead of the probe function. Share
  infrastructure as functions, not data.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 entry; `geo2stl/trails.py::TrailsUpstreamError`

### 2026-08-27 — osmnx was never blocked by the network, two of its defects were
- **Decision:** `tune_osmnx` turns off `overpass_rate_limit` and makes `_config_dns` a no-op
  (restoring `getaddrinfo`); callers that loop pace their own calls like `_overpass_wait`.
- **Why:** the slot-status request hung, not the query — 90 s timeout with limiter on, 2.3 s off,
  same 253 features. osmnx pins DNS process-wide, so one unhealthy machine took down every later
  request including direct POSTs (a failed building fetch killed a city's water overlay).
  `doh_url_template = None` does not prevent the pin.
- **Rejected:** blaming connectivity (see superseded entry below).
- **Supersedes / superseded by:** supersedes [Overpass queried directly](#2026-08-12--overpass-is-queried-directly-not-through-osmnx-superseded)
- **Source:** decisions.md 2026-08-27 entry; `tools/align_tool/locate.py::tune_osmnx`, `city2stl/registration/osm_water.py::_overpass_wait`

### 2026-08-26 — The drag tool's water layer comes from locate, not get_osm_semantic_masks
- **Decision:** `osm_water.png` comes from the direct-Overpass water raster, falling back to
  `get_osm_semantic_masks` only if that call fails outright.
- **Why:** semantic masks never request `natural=coastline` and keep only polygons, so coastal
  plates lost the sea — the feature a human dragging Barcelona most needs. The semantic masks stay
  as-is; they are correct for buildings and vegetation.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-26 entry; `city2stl/registration/osm_water.py::osm_water`

### 2026-08-26 — Plate water is the water raster minus the solid raster
- **Decision:** `plate_water_mask` differences the water and solid rasters;
  `plate_river_mask` delegates to it.
- **Why:** isotropic `mesh_to_heightmap` pads non-square plates with NaN, the same marker as water;
  Lisbon grew a full-width false-water bar, and a straight line correlates with every window.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-26 entry; `tools/align_tool/locate.py::plate_water_mask`, `tools/align_tool/export_align_data.py::plate_river_mask`

### 2026-08-26 — The sea is recovered from coastline ways by voting, not by seeding
- **Decision:** burn coastline ways into a barrier; each segment probes 3 px either side and votes
  for its water-side region and against its land-side one. Sea = ≥ 3 water votes and > 2x water
  vs land votes.
- **Why:** OSM sea is a directed line, not an area; without it Barcelona's true position scored
  z = −0.1. Seeding failed on every city (one stray seed floods the continent); voting survived
  Barcelona's 40 stray water votes vs ~1100 land votes.
- **Rejected:** flood seeding from the water side.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-26 entry

### 2026-08-12 — Overpass is queried directly, not through osmnx [superseded]
- **Decision:** the water path posts its own Overpass queries — one per selector, 6 s minimum gap,
  doubling backoff over six attempts across three mirrors, each selector cached separately and
  failures never cached; it rasterises water itself (coastline is a way).
- **Why:** osmnx appeared unable to reach Overpass (103 s timeout vs 3 s plain POST). Pacing cured
  429s, splitting cured most 504s; a combined cache once stored Barcelona without its sea.
- **Supersedes / superseded by:** the "osmnx is blocked" diagnosis is superseded by
  [osmnx was never blocked](#2026-08-27--osmnx-was-never-blocked-by-the-network-two-of-its-defects-were);
  the per-selector direct-POST design itself stands.
- **Source:** decisions.md 2026-08-12 entry; `city2stl/registration/osm_water.py::_overpass`
