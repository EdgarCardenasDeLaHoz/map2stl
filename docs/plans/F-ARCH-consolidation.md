# F-ARCH — Consolidate duplicated functionality into layered, reusable modules

Status: planned 2026-09-26 from a read-only audit of both repos. Dependency rule:
`app → city2stl / geo2stl → numpy2stl`; inside numpy2stl
`registration / applications → stl2numpy / processing / raster → core / io / utils`.
Nothing lower may import anything higher; numpy2stl never imports strm2stl; libraries
never import `app.*`.

## Duplicated capabilities → single home

| Capability | Today | Home |
|---|---|---|
| Mesh → heightmap | `stl2numpy.mesh_to_heightmap` (binning), `city2stl/skyline/height/stl_import` (ray-cast + bbox), `building_simplify/_io._rasterize_mesh` | `numpy2stl.stl2numpy.heightmap(method="bin"\|"raycast")`; `geo2stl.raster.georef_heightmap` for the bbox mapping |
| Decimation | `stl2numpy/reduction` (2 fallback chains), `building_simplify/decimate`, `processing/simplify` | `numpy2stl.processing.decimate` + lossless `simplify` (verify wrapper moved from city_model) |
| `simplify_surface` | `core/solid.py`, `processing/simplify.py` | core only |
| Raster → polygons, terrain residual, building mask | `registration/align/segmentation`, `tools/align_tool/plate_vectors` | `numpy2stl.raster.{vectorize,segment}` (fixes processing → registration) |
| Polygon → raster ("burn") | 11 implementations (numpy2stl cities, composite router — overlapping footprints *add*, holes ignored — city2stl.rasterize, tools, roof code, city_model) | `numpy2stl.raster.burn` primitive; `geo2stl.raster.burn_geojson`; `city2stl.rasterize` for city layers |
| OSM / Overpass | `city2stl/fetch`, `geo2stl/trails` (imports a city2stl private), numpy2stl `applications/cities`, align tool raw client, tools/ml | `geo2stl.osm` (endpoints, osmnx config, features, raw query); `city2stl.fetch` keeps layer semantics |
| Tag heights | 4 copies with 3.0 / 3.2 / 3.5 / 4 m per level | `city2stl.heights.height_from_tags` |
| Prisms / extrusion | `core.generate.polygon_to_prism`, `processing/extrusion` (x/y swap), `city2stl/mesh` ear-clip + roofs, `city_model._prism/_slab/_close_surface`, `applications/puzzle._extrude` | `numpy2stl.core.extrude` (`prism`, `close_surface`, `orient_ccw`); roofs in `city2stl.mesh.roofed_prism` |
| Heightfield → solid | `array_to_mesh`, `export._grid_mesh`, `v2 build_mesh`, `city_model.terrain_tin/solid` | `numpy2stl.core.heightfield` (`grid_solid`, `tin_solid`) |
| lon/lat ↔ px/m/mm, metres per degree | ~70 inline `111_320·cos(lat)` (some 111 000), 6 transform helpers, 4 bbox-size helpers | `geo2stl.geo`: constants, `bbox_size_m`, `bbox_diagonal_km`, `GeoGrid(bbox, shape, row0)` |
| Satellite tiles | `geo2stl/sat2stl` (base64 JPEG), `city2stl/roof_tiles`, `skyline/satellite_image` (tile math ×3) | `geo2stl.imagery.fetch_rgb` + tile math |
| DEM fetch / OpenTopography | `geo2stl/dem`, routing/fallback inside `routers/terrain.py`, provider copies, align tool | `geo2stl.dem.fetch_dem` + `opentopo` |
| GeoTIFF bytes → array | 6 copies | `geo2stl.raster.read_geotiff` |
| Height providers, registry, merge | `city2stl/skyline/height/*`, `app/core/height/service.py` | `city2stl.height/` (out of skyline); app keeps async glue |
| Caches | `app/core/cache.py` (imported by libraries), numpy2stl `_paths`, 8 hand-rolled npz caches | `geo2stl.cache`; `numpy2stl.utils.cache` |
| Booleans | `processing/boolean` (float64), `city_model` (float32) | public `processing.boolean.{to_manifold,from_manifold,union}` |
| Mesh I/O | numpy2stl `io`, raw `trimesh.load`/`export` in app, tools | `numpy2stl.io.load_trimesh` (public) + `write_mesh` |
| Label engraving, NaN fill, roof-crop harvest | app vs v2; 5 fill variants; tools/data vs tools/ml | `numpy2stl.utils.image.engrave_text`; `numpy2stl.raster.fill_nan`; tools/data |

## Layering violations to remove

1. numpy2stl `processing/building_simplify` → `registration.align.segmentation`.
2. numpy2stl `applications/cities` ↔ `registration` (cycle, incl. a private import).
3. `_paths.py` assumes a sibling `strm2stl/`; cache root inside `registration/runs`.
4. Libraries importing `app.*`: skyline height providers (`_cache`, `google_3d`, `ndsm`, `wsf3d`), `skyline/region_data` (`app.server.core.db`), `geo2stl/processing` (type-only).
5. `geo2stl/trails` → private `city2stl.fetch` function.
6. App → privates/scripts (`reports.py` → skyline scripts; `_get_api_key`; `geodem._OPENTOPO_API_KEY`).
7. Tools reimplementing library code (Overpass, rasterisers, DEM caches, affine decompose).
8. General-purpose code living in `skyline/` (height, satellite image, OSM water, projection, timing).

## Migration (small steps, one-release shims at old paths, tests per step)

Mechanical: (1) `geo2stl.geo` constants/bbox helpers; (2) cache → `geo2stl.cache`;
(3) `skyline/height` → `city2stl.height`; (4) provider registry out of the app; (5)
`geo2stl.osm`; (6) numpy2stl dedupes (`simplify_surface`, public `load_trimesh`,
decimation chain); (7) `numpy2stl.raster` (vectorise/segment out of registration);
(8) tool dedupes.
Design-dependent: (9) one rasteriser; (10) `core.extrude`; (11) `core.heightfield`;
(12) one mesh→heightmap with `method=`; (13) OSM/lidar out of numpy2stl; (14) public
boolean helpers. Decisions are recorded in `memory-bank/decisions.md`.

## Decisions (user, 2026-09-26)

- **numpy2stl is geo-free**: OSM and lidar fetching move to strm2stl (`city2stl`);
  registration takes OSM rasters/polygons as inputs. One-release shims for the old
  `numpy2stl.applications.cities` API.
- **Raster row 0 = north** (image convention) by default; mesh/registration code flips
  explicitly at its boundary.
- **Tag heights**: 3.2 m per level, plus one level-equivalent for the roof when
  `roof:levels` is absent.
- **Order**: finish the city-model fixes and re-render first, then migrate step by step
  (tests + commit per step).
- Defaults taken without asking: keep both mesh→heightmap methods (`method="bin"|"raycast"`);
  WGS84 metres per degree (110 574 lat, 111 320·cos φ lon); `v2/` is retired per F-FE1;
  delete notebook-only `city2stl/buildings.py`.

## Decision (user, 2026-09-27): one two-stage mesh pipeline

1. **Terrain stage** (raster): DEM source/merges, curve edits, median smoothing, land cover,
   vegetation, satellite relief, water depth → one terrain heightfield. This is what the
   composite becomes; its building / road / wall / waterway channels are removed from the
   mesh path (they stay only for 2D preview and ML rasters). The legacy merge panel goes.
2. **Feature stage** (vector): `city2stl.city_model` extrudes / drapes / cuts OSM features on
   the terrain-stage heightfield and merges in 3D.

Every mesh export (terrain STL/OBJ/3MF, preview, puzzle, city model) runs both stages; a
"terrain only" export is the city model with no feature layers. The city build therefore
resolves its DEM through the same request path as the other exports (composite spec or
edited values first, DEM handle otherwise) instead of the raw handle.

### Progress

- 2026-09-27 — server side of the two-stage pipeline done: `app/server/core/export.py`
  `terrain_stage()` (composite / edited values / `dem_id`, median, sea-level cap, scale,
  label, contours) feeds `city2stl.city_model.build_on_terrain()` for every mesh export;
  the city task resolves its DEM through `ExportContext`; composite `osm_*` feature
  sources are dropped from mesh exports (`export_params.mesh_composite_layers`). The
  session's puzzle and city builds share `_mesh_export_body()`.
- 2026-09-27 — client side of the two-stage pipeline done: `composite-dem.js` keeps the
  `osm_*` channels in the 2D preview only; Apply to DEM writes the terrain-only composite
  into `lastDemData.values` (so `dem_values` never carries them) and publishes a
  terrain-only `compositeLayerSpec`; the pure builder is `layers/composite-spec.js`
  (`FEATURE_SOURCES`, tested in `tests/js/compositeSpec.test.js`) and
  `export-handlers.js` `_demSettings()` filters `FEATURE_SOURCES` again. The Composite
  panel's City / OSM group says these come from the City Model layers in 3D. Legacy merge
  panel removed (`dem-merge.js`, `#mergePanel`, its CSS, the `merge` DEM subtab,
  `api.dem.merge`, `getActiveCompositeSpec`/`setCompositeActive`); `_initDemSources` was
  dropped rather than moved, as `dem-main.js` `populateDemSources()` already owns the
  source dropdown. `/api/composite/dem-merge` itself stays (SDK `merge_dem()` uses it).
- Step 5 started: `geo2stl/osm.py` holds the Overpass mirrors, health probe and osmnx
  settings for both city layers and trails (removes violation 5, trails -> city2stl private).
- 2026-09-27 — steps 2–4 done. (2) `geo2stl/cache.py` holds `CACHE_ROOT`, TTLs, key helpers
  and the array/OSM cache; `app/server/core/cache.py` keeps pruning/clear/migration and
  re-exports the rest (`CACHE_ROOT` forwarded live). Tests patch `geo2stl.cache.CACHE_ROOT`.
  (3) `city2stl/skyline/height/` → `city2stl/height/`; `skyline/height/__init__.py` is a
  one-release re-export shim. (4) registry, selection and `enhance_city_data` in
  `city2stl/height/service.py`; the app keeps the async `/api/height/*` glue and passes its
  OpenTopography key in. `skyline/region_data` reads the regions table read-only via
  `sqlite3` (`REGIONS_DB`, optional `region_lookup`); `geo2stl/processing` uses a local
  `ProcessingSpec` Protocol. Violation 4 removed: no `app.*` imports left in the libraries.
- 2026-09-27 — tag heights done: `city2stl.heights.height_from_tags(props)` (`height` /
  `building:height` with units, else (`building:levels` + `roof:levels`, or + 1 roof level)
  × 3.2 m) replaces the copies in `_fill_heights`, `skyline/_core/util` (3.4 m, deleted),
  `skyline/region_data`, the height diagnostic script and `_report_plots` (3.0 m) and
  `tools/ml/data/collect_osm_tiles` (3.5 m). Callers keep their own 10 m default and clamps.
- 2026-09-27 — GeoTIFF reader done: `geo2stl.raster.read_geotiff(bytes|path, fit_dim=,
  zero_as_nodata=, dtype=) -> (array, transform, crs, nodata)` (rasterio, PIL fallback)
  replaces the rasterio blocks in `height/providers/_raster.py` (now a None-on-failure
  wrapper), `ndsm._read_geotiff` (deleted), `wsf3d` and `geo2stl.dem.fetch_opentopo_dem`.
  `wsf3d_global` (tifffile range reads of a 2 GB COG) and `geo2stl.tiles` (skimage on local
  SRTM tiles, no georef) stay as they are.
- 2026-09-27 — satellite tiles done: `geo2stl/imagery.py` holds slippy tile math
  (`lon/lat_to_global_px`, `global_px_to_lonlat`, `lonlat_to_tile`, `tile_bounds`,
  `m_per_px`, `tile_range`), `choose_zoom` (target m/px or `dim`, per-side / total caps),
  `fetch_tile` (optional disk cache), `stitch_tiles` and `fetch_rgb`. `sat2stl.fetch_satellite_tiles`,
  `roof_tiles` (`lon_to_gpx`, `lat_to_gpy`, `m_per_px`, `tile`, `prefetch_bbox`, `crop_for_ring`)
  and `skyline/satellite_image.fetch_region_satellite` keep their signatures as thin wrappers.
- 2026-09-27 — DEM fetch / OpenTopography done: `geo2stl/opentopo.py` holds the key
  (`get_api_key` / `set_api_key`), `OPENTOPO_DATASETS`, `request_geotiff` and the cached
  `fetch_opentopo_dem` (re-exported by `geo2stl.dem`); `geo2stl.dem.fetch_dem(bbox, dim, source,
  api_key)` owns source routing and fallbacks (`fetch_dem_from_source` is a one-release alias),
  and the terrain router's `_fetch_dem_array` is a one-call adapter. `app/server/config.py` reads
  the key and dataset table from geo2stl; `routers/auth.py` calls `opentopo.set_api_key` instead
  of rebinding module privates. nDSM / 3DEP providers request through `opentopo.request_geotiff`
  and fall back to the geo2stl key. `google_3d._get_api_key` → public `get_api_key`. Violation 6:
  `reports.py` reads the region-report rows through `city2stl.skyline.report_index` (shared with
  `scripts/build_landing_page.py`), not the script.
- 2026-09-27 — step 8 (tool dedupes), the identical-behaviour part: `geo2stl.osm.overpass_query`
  (+ `overpass_wait` / `overpass_backoff`, one pacing clock per process) replaces the raw
  Overpass loops in `align_tool/locate._overpass` and `osm_model._nodes` (both pass their own
  mirror list, timeouts and user agent). `numpy2stl.raster.burn_polygons` replaces the rasterio
  burns in `locate._rasterize`, `osm_model.coverage`, `tools/ml/data/collect_osm_tiles`
  (`_rasterize_buildings`) and `tools/ml/eval/eval_pseudo_ndsm`. `street_place._terrain_tile`
  caches through `geo2stl.cache` (namespace `align_terrain`, one-year TTL). Left: `tune_osmnx`
  (process-wide osmnx surgery, not the app's per-endpoint settings), the tools/ml osmnx building
  fetches (need raw per-footprint tags; `city2stl.fetch` dissolves), `plate_vectors` /
  `plate_height_truth` pixel-space `cv2.fillPoly` (different edge rule; moving with
  `numpy2stl.raster` step 7), affine decompose (no public numpy2stl helper yet).
- 2026-09-27 — `v2/` retired (F-FE1): directory, `Code/docs/app-v2.md`, its INDEX section, the
  two `.claude/launch.json` configurations, the eslint ignore and the `.svelte-kit/` ignore are
  gone; nothing outside it imported it. `city2stl/buildings.py` deleted (no importer; the notebook
  path that named `get_polygons` already failed on the missing `city2stl.create`).
- 2026-09-27 — label engraving / NaN fill (app side): `app/server/core/export.py`
  `_apply_label_engraving` has no twin left in strm2stl (the other copy was in `v2/`), so it
  stays until `numpy2stl.utils.image.engrave_text` exists. `city2stl.height.infill.infill_nearest`
  (used by the app's mesh import and the session) now delegates to `numpy2stl.raster.fill_nan`
  (all-NaN still returns zeros, output still float32).
- 2026-09-27 — step 13 (OSM / lidar out of numpy2stl): numpy2stl is geo-free.
  `numpy2stl.registration.register_city_stl(stl_file, reference)` takes a `ReferenceSource`
  (`registration/reference.py`: building heightmap with `cell_size_m`, optional masks / nDSM,
  scale anchor, centre-search candidates; `StaticReference` for in-memory arrays); the centre
  search is `center_search.find_best_target` over the source's candidates; `config.M_PER_DEG_LAT`
  is gone. The OSM code is `city2stl/osm_raster.py` (cache now `cache/osm_raster/`, the old
  `registration/runs/osm_cache` files were copied there), the 3DEP EPT nDSM is
  `city2stl/height/providers/lidar_3dep_ept.py`, and `city2stl.registration.register_city_stl`
  (same arguments as before) builds an `OSMReference` for the app's mesh import and the align
  tool. The three OSM CLIs moved to `city2stl/registration/scripts/`; the Fourier–Mellin
  prototype stays in numpy2stl and reads its reference from an .npz. numpy2stl cannot import
  strm2stl, so `applications/cities.py` / `lidar.py` raise an ImportError naming the new home
  (one release) and a city name passed to numpy2stl raises a TypeError;
  `tests/test_geo_free.py` fails on any osmnx / requests / pdal / strm2stl import. Removes
  violation 2.
- 2026-09-27 — step 9 (one rasteriser), the identical-behaviour part: `city2stl.rasterize`,
  `height/providers/gba.py`, `geo2stl/trails.py` (relief, areas, piste grades) and
  `geo2stl/hydrology.py` (both river rasterisers) burn through `numpy2stl.raster.burn_polygons`;
  old vs new compared on random polygons/lines, holes and multipolygons: byte-identical.
  Behaviour change on purpose: the composite router's building channel keeps the tallest
  footprint and leaves holes empty (was: overlaps added, holes ignored, PIL edge rule); its
  cache key gained `"burn": 2`. Left: `tools/align_tool/plate_vectors._burn` (pixel-space cv2
  fill, a different edge rule: 198 of 14 400 cells differ on a test pair), the roof /
  city-model burns (being edited), the composite road / waterway / wall line draws (lines,
  not polygon burns). Known quirk kept identical: in `rasterize_city_data` any building
  np.maximum's the whole grid with 0, erasing negative water / road depressions.
- 2026-09-27 — step 12 finished: `city2stl/height/stl_import.py` ray-casts through
  `mesh_to_heightmap(method="raycast", row0="north")` and keeps the bbox → grid mapping (rays
  now at cell centres instead of `linspace` edge-to-edge samples, so values move by up to half
  a cell at feature edges). `mesh_to_heightmap` defaults to `row0="north"`; the registration
  pipeline and stages, `building_simplify` (`prism`, `_io`) and `align_tool/locate._heightmap`
  pass `row0="south"`. The STL heightmap cache keys are unchanged for south renders. The align
  tool no longer imports the `registration.align.segmentation` shim (now `numpy2stl.raster`).

Not complete — rows still open: label engraving (`numpy2stl.utils.image.engrave_text` does not
exist yet), mesh I/O (`numpy2stl.io.write_mesh` does not exist), caches (`numpy2stl.utils.cache`
does not exist), the plate / roof / city-model burns above, and `geo2stl.raster.georef_heightmap`
(the STL-import bbox mapping stays in `stl_import` by choice).
