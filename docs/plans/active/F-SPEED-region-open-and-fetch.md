# F-SPEED — Region open and data fetch speed

_Proposed 2026-10-14 from a measured review of the region-open and data-fetch paths. Status:
approved 2026-10-14 for phases 1 and 2 (user); both are implemented, see [Results](#results).
#14 was rejected after measuring, and #8 uses a separable sampler instead of `cv2.remap`.
Phase 3 is open._

Scope: the time from picking a region to seeing terrain, water, land cover and satellite, plus
projection switches and cache behaviour. City builds and exports are out of scope (see
[F-CITYMODEL](F-CITYMODEL-vector-city-model.md)).

## Evidence

- **Server perf log** for one session: 191 timed lines.
- **Usage log**, all dates: `output/usage/*.jsonl`. It gives client-side request times.
- **Micro-benchmarks on cached files**, no network calls. Scripts are in the session scratchpad
  (`dem/`, `hydro/`, `sat/`) and are not kept.

Line numbers below are from the 2026-10-14 review and will drift; resolve each item by its
symbol.

### Three biggest costs

1. **Satellite tiles are uncached and serial.** They are fetched one at a time and never cached.
   The same Colombia box costs 2–5 s on every repeat. Granada took 56.9 s once.
2. **Earth Engine downloads repeat on a projection switch.** ESA land cover and water are
   downloaded again each time: 7–12 s per switch.
3. **Cache hits run blocking work on the event loop.** A layer that takes 0.03–0.15 s when run
   alone logs 0.35–1.0 s during a region open. The difference is queuing.

## Phase 1 — small, low-risk fixes

All are effort S. None changes cached values or the client contract.

| # | Where | Problem | Fix | Expected gain |
|---|---|---|---|---|
| 1 | `geo2stl/imagery.py:191` `stitch_tiles`; `geo2stl/sat2stl.py:124` | `fetch_rgb` gets no `cache_dir`, so tiles are never cached. Tiles download one at a time (63 tiles took 10.6 s) | Tile cache under the cache root, plus a pool of about 8 threads | ~0.1 s on repeats; ~2 s on a first fetch (estimate) |
| 2 | `app/server/core/terrain.py:374` → `cache.py:169` | `np.savez_compressed` runs inside the async handler on every DEM miss: 1.37 s for 1360×1600 | Write in a thread, with zlib level 1 or plain `np.save` (5–9 ms) | 0.3–2.5 s per miss |
| 3 | `terrain.py:450-472` water, `:583-600` ESA, `~1305` borders, `~1180` hydrology hit | Cache-hit paths run inline on the event loop | Wrap them in `run_sync` | 0.3–0.9 s per layer during a region open |
| 6 | `hydrology.py:~780` per-order burn | Nine burn passes: 4.0 s for Colombia order 3+ (126k reaches) | One pass, sorted by order. Output is pixel-identical | 4.0 → 2.67 s |
| 7 | `borders.py:79` `burn_polygons` | Converting to GeoJSON takes 0.47 of the 0.55 s on each Europe miss | Keep per-feature GeoJSON in the `border_lines` memo | 0.45 → ~0.08 s per miss |
| 9 | `app/server/core/cache.py:31,49` `prune_cache` | The cap of 200 counts files, not entries, so pruning splits npz/json pairs. 17 DEM npz files have no sidecar and are read as stale forever. `esa_lc`, `ee`, `ndsm` and `dem_handles` have no TTL. 2.76 GB of Overpass JSON is never pruned | Prune pairs together; cap by entries or bytes; give every namespace a TTL | No recompute after a restart |
| 10 | `water-mask.js:195` (client) | The water-mask response already carries the ESA values, yet the client also calls `esa-land-cover` (164 + 134 calls in the usage log) | Reuse the water-mask ESA when the size matches | One request less per view |
| 11 | `routers/terrain.py` trails route | `source=all` on country-size boxes ran 504–6,611 s before the client gave up, five times | Area guard, like the one on city layers | No 10-minute-plus hangs |

## Phase 2 — moderate changes

| # | Where | Problem | Fix | Gain | Risk |
|---|---|---|---|---|---|
| 4 | `terrain.py:448,581`; `sat2stl.py:426`; `hydrology.py:914` `water_surface_mask` | ESA and water sizes follow each projection's shape (Europe: 600/679/902/936 px). A finer shape misses the cache and downloads again: 11.9 s in Europe. This probably also explains the 2.94 s first cosine hydrology call | Fetch once at the shared source size, then resize per projection | 7–12 s per projection switch | low |
| 5A | `geo2stl/raster.py:69-72` `read_geotiff` | Decoding the full GeoTIFF: Colorado 5.4 s warm / 7.7 s cold, Europa 8.0 s | `GDAL_NUM_THREADS=ALL_CPUS`: 5.4 → 3.8 s. Mask -32768 before any resampling | ~1.6 s on first open | low |
| 8 | `projections.py:694/768/890/973` | Float64 `map_coordinates` runs on every request, cache hits included: 0.22–0.46 s at dim 2000 | `cv2.remap` with float32 maps (~18× faster) | 0.2–0.45 s per request | low: check edge values |
| 13 | `dem.py:453, ~341, 620` | float64 copies and float32/float64 conversions: 18–107 ms each; the shared-grid memo uses twice the memory | float32 throughout, keeping the carve hash stable | ~50 ms; half the memory | low |
| 14 | `projections.py:1069` `project_water_arrays` | The water mask is projected at order 1, plus a separate ESA pass | Order 0, uint8 | ~50 ms | low |
| 12 | `validation.py:95`, `terrain.py:354` | ESA (1.9 MB) and the 0/1 water mask (3.8 MB) go out as float32. `sat_values.tolist()` is 4.9 MB and takes 0.2 s | Send uint8 / base64 with a dtype field | 4× smaller, ~0.2 s | medium: client contract changes |

## Phase 3 — larger changes

| # | Where | Fix | Gain | Risk |
|---|---|---|---|---|
| 17 | `dem_cache.py:131` (`sd` in the key) | Cache the post-water grid once per box, then fit it per projection (2–10 ms). Today each switch redoes water and depth plus the #2 write | Most of a miss | medium: the export must build the same key |
| 16 | `dem.py:932` `make_dem_payload` | Binary response, with metadata in headers. Building the payload and `json.dumps` take 45–265 ms at dim 1000–2000; raw bytes take 2–13 ms | 40–250 ms | low |
| 18 | `terrain.py:818` `_hydrorivers_layers` | Key the non-print path on the shared source size, not the projection | One rasterize per box | low |
| 5B | `geo2stl/raster.py` | `tifffile` with 8 workers plus `cv2.INTER_AREA`: 1.7 s instead of 5.4 s | Up to 6 s on first open | medium: values shift, bump `SHARED_BASE_VERSION` |
| 19 | `numpy2stl/raster/burn.py` GeoJSON step | `cv2` polylines plus dilate. The GeoJSON step is 2.05 s of the 6.1 s hydrology build | Up to ~3.5 s | medium: about 0.91 IoU fidelity loss, so it may not be worth it |

Not measured, effort M: `dem.py:493-516` and `tiles.py:141` (local GEBCO path) run water and depth
at full resolution and read whole 90° tiles. Resizing before the water step and reading windows
should save seconds.

## Overlaps with other plans

- **#4, #17 and #18 are steps toward P-PROJ-CACHE** ([proposals](../../proposals.md),
  Performance): one fetch per box, projected at response time. Do them so that they move toward
  that design, not around it.

- **#15 (cache location) belongs to [F-DATADIR](F-DATADIR-portable-data-dir.md), not here.**
  `MAP2STL_CACHE` is unset, so the ~14 GB cache sits in OneDrive. It should move to
  `~/.cache/map2stl` (not `%LOCALAPPDATA%`, which the MSIX sandbox redirects).
- **#9 (pruning) overlaps with F-DATADIR's TTL and byte ceiling** and with F-CLOUD's
  unbounded-disk finding. The pair-splitting bug is fixed here, because it costs time today;
  the TTLs and the ceiling stay in F-DATADIR.
- **F-DATADIR's default (`%LOCALAPPDATA%`) needs rechecking.** The Claude desktop app's MSIX
  sandbox redirects writes there, so agent-run servers and the user's shell would see different
  caches.

## Measure before or during the work

- Cold ESRI tile and Earth Engine downloads, to confirm #1 and #4. Both need network, and Earth
  Engine has quotas.
- A load test for #3: parallel GETs during a region open, before and after.
- Local GEBCO stitching and the h5 read at Amazon scale (`ocean_root` is not on this PC).
- The Amazon-scale HydroRIVERS rasterize after #6 (it was 99 s).
- A breakdown of `rivers_preview` (2.84 s) and the trails cache (`trails/` is empty; one Europe
  call took 2.76 s).
- `composite/dem-merge`: p90 is 53 s in the usage log and it runs on every region open. It was
  outside this review and is probably the largest single cost left.

## Results

Phases 1 and 2, implemented 2026-10-14. Numbers are measured on this PC unless marked.

| # | Done in | Measured |
|---|---|---|
| 1 | `geo2stl/imagery.py::fetch_rgb` (tile cache `default_tile_cache_dir`, `TILE_WORKERS` = 8) | repeats read tiles from disk |
| 2 | `app/server/core/cache.py::write_array_cache` (`_savez_fast`, zlib level 1; `write_array_cache_async`) | DEM write 1188 → 246 ms |
| 3 | `routers/terrain.py`: water, ESA, hydrology and borders cache hits run through `run_sync` | see the load test below |
| 6 | `geo2stl/hydrology.py::_burn_orders` (one pass, pixel-identical) | Granada-size burn 1.85 → 1.65 s |
| 7 | `geo2stl/borders.py` (`_GEOJSON` memo, `_burn_lines`) | 0.10–0.19 → 0.04–0.10 s; 0.03–0.04 s on a memo hit |
| 9 | `app/server/core/cache.py::prune_cache` (pairs pruned together, `ORPHAN_GRACE_SECONDS`, `MAX_ENTRIES_PER_NAMESPACE`) | 39 tests in `tests/test_cache.py` |
| 10 | `core/cache.js::waterMaskCache.findEsa`, `water-mask.js::loadEsaLandCover` | no `esa-land-cover` request when the water mask for the same view is cached. The client key now includes `dim`, `maintain_dimensions` and `clip_valid_region` (a size change used to return the old grid) |
| 11 | already in commit 8ea974e | — |
| 4 | `routers/terrain.py::_layer_source_dim`, `hydrology.py::water_surface_mask` `fetch_dim` | one Earth Engine image per box. On Granada a fresh server's first water and ESA requests were cache misses at the new size and answered in 0.24 s from the cached finer image |
| 5A | `geo2stl/raster.py::read_geotiff` (`GDAL_NUM_THREADS=ALL_CPUS`) | 5.27 → 3.05 s, bitwise identical |
| 8 | `geo2stl/projections.py::_sample_grid` (separable, float32) | mercator 190 → 65 ms at dim 2000; order 0 5.6× faster; sinusoidal 1.4×. `cv2.remap` rejected: 0.54 m error |
| 12 | `validation.py::b64_encode_u8`, `*_dtype` fields; `sat_values_b64` | Granada water mask 2706 → 676 KB, ESA 1353 → 338 KB |
| 13 | float32 in `shared_base_grid`, `apply_water_and_depth(dtype=)`, `fetch_dem` | carve hash unchanged |
| 14 | not done | order 0 flipped ~1 % of pixels (~5 % of water pixels) to save 15–30 ms |

**Granada load test**: five layer requests in parallel (DEM, water, ESA, hydrology, borders),
as the client sends them at region open. The sequence is cosine → mercator → none → cosine, with
every layer already cached. Wall time in seconds, second run of each:

| Server | cosine | mercator | none | cosine |
|---|---|---|---|---|
| before | 0.51 | 0.71 | 0.43 | 0.52 |
| after | 0.36 | 0.50 | 0.34 | 0.48 |

Hydrology is now the slowest layer of each step (0.30–0.48 s). The first request after the old
server started took 12.8 s for every layer. The new server's first request took 0.52 s. The
cause was not isolated: it may be the old event loop blocking on a cold cache, or process
warm-up.

**Click-through** (2026-10-14, app pane): Granada open, with rivers, land cover and satellite
switched on, then cosine → mercator → cosine. No console errors. Land cover reused the water
mask's ESA (no request) and decoded to the classes 10–80. The water pixel count matched the
server's count.

Follow-ups, not done here:
- The tile cache has no TTL. Skyline and `roof_tiles` do not use it.
- `geo2stl.cache.write_array_cache` still writes at the default zlib level.
- `esa_lc`, `ee`, `ndsm` and `dem_handles` have no TTL (F-DATADIR).
- `composite.py` still casts to float64.
- `hydrology._burn_orders` imports the private `numpy2stl` helper `_as_geojson_pairs`.
- On a projection switch the water-mask and land-cover requests fire together, so `findEsa` misses.
  Awaiting the in-flight water request (`waterMaskCache.dedupe`) would save that request too.

## Verification

- **Each phase:** pytest and vitest; one region open (Granada) and one projection switch, timed
  from the perf log before and after. Use only cached regions (OpenTopography allows 50 calls a
  day).
- **Value-changing items (5B, 8, 12, 19):** an array diff against the old path, with a stated
  tolerance.
