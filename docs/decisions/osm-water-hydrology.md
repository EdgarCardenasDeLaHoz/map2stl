# OSM, water and hydrology

Fetching OSM through Overpass/osmnx, telling outages from empty answers, water and coastline
rasterisation, and rivers and lakes carved into the terrain. Related: [composite.md](composite.md),
[trails.md](trails.md).

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
- **Superseded by:** — (user, same day: OSM is not the best lake source for large boxes;
  see the HydroLAKES plan).

### 2026-10-03 — Overpass mirror probes run in parallel and are remembered for 2 minutes
- **Decision:** `geo2stl/osm.py::healthy_overpass_endpoints` probes the mirrors' `/status`
  in parallel and reuses each result for `PROBE_MEMORY_S` (120 s), under a lock.
- **Why:** Miami walk (2026-10-03): overpass.kumi.systems timed out its probe six times in
  70 s, 10 s each, once per fetch (buildings, lakes, railways, green), and probes ran one
  mirror after the other. The Miami preview waited about 6.5 min.
- **Rejected:** a shorter probe timeout — a busy but working mirror would be skipped.
- **Supersedes / superseded by:** completes the "remember an unhealthy mirror" item of the
  large-region guide §6.

### 2026-10-02 — OSM lakes skip boxes over 10 000 km²; one fetch per box at a time; failed mirrors go last
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
