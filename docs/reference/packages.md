# Packages: app, geo2stl, city2stl, numpy2stl

_Last updated: 2026-09-28_

What each library owns, what the app calls in it, and where the caches live. Paths are relative
to `map2stl/`; numpy2stl paths are under `numpy2stl/src/numpy2stl/`.

---

## Layering

```
app/ (server, client, session SDK)
  └─► city2stl/  (OSM city model, building heights, registration against OSM)
  └─► geo2stl/   (lat/lon → rasters: DEM, imagery, water, projections, disk cache)
        └─► numpy2stl  (geo-free meshes, rasters, STL/OBJ/3MF I/O, registration core)
```

- **One-way.** `app → city2stl / geo2stl → numpy2stl`; a library never imports `app`, and
  numpy2stl never imports map2stl.
  - Why → [architecture.md](../decisions/architecture.md), "Both repos install editable,
    layering is one-way, and shims were removed".
- **numpy2stl is geo-free.** No lat/lon, no fetching: it takes arrays, polygons and reference
  sources. Each capability that used to be duplicated has one home.
  - Why → [architecture.md](../decisions/architecture.md), "numpy2stl is geo-free, and each
    duplicated capability gets one home".
- **Both repos install editable** (`pip install -e`), so `import numpy2stl` resolves to the
  sibling checkout; the old `core/*` re-export shims are removed. Routers import library code
  directly; `app/server/core/` keeps only server concerns (tasks, caches, DB, responses).
- **Metres per degree are defined once**, in `geo2stl/geo.py::M_PER_DEG_LAT` and
  `geo2stl/geo.py::m_per_deg_lon`.
  - Why → [architecture.md](../decisions/architecture.md), "Metres per degree are defined once,
    in geo2stl.geo".
- **Raster row 0 is north by default.** Mesh-derived and registration rasters that keep
  row 0 = south say so and flip at their boundary (e.g. `city2stl/osm_raster.py`,
  `numpy2stl/src/numpy2stl/stl2numpy/heightmap.py::mesh_to_heightmap` with `row0="south"`).
  - Why → [architecture.md](../decisions/architecture.md), "Raster row 0 is north by default"
    (supersedes the old row 0 = south rule).

---

## geo2stl — lat/lon bbox → rasters

| Module | Owns | Key symbols |
|---|---|---|
| geo | Metres per degree, bbox size, pixel grids | `geo2stl/geo.py::m_per_deg_lon`, `geo2stl/geo.py::bbox_size_m`, `geo2stl/geo.py::bbox_diagonal_km`, `geo2stl/geo.py::GeoGrid` |
| cache | Namespaced disk cache (array `.npz`, OSM `.json.gz`, JSON) shared by libraries and app | `geo2stl/cache.py::make_cache_key`, `geo2stl/cache.py::read_array_cache`, `geo2stl/cache.py::write_array_cache`, `geo2stl/cache.py::read_osm_cache`, `geo2stl/cache.py::read_json_cache`, `geo2stl/cache.py::NAMESPACE_TTL` |
| osm | Overpass mirrors, health probe, osmnx settings, raw-QL client | `geo2stl/osm.py::healthy_overpass_endpoints`, `geo2stl/osm.py::overpass_query` |
| imagery | Web Mercator tile math, ESRI World Imagery fetch and stitching | `geo2stl/imagery.py::choose_zoom`, `geo2stl/imagery.py::fetch_tile`, `geo2stl/imagery.py::stitch_tiles`, `geo2stl/imagery.py::fetch_rgb` |
| opentopo | OpenTopography key, dataset table, cached GeoTIFF fetch | `geo2stl/opentopo.py::get_api_key`, `geo2stl/opentopo.py::set_api_key`, `geo2stl/opentopo.py::request_geotiff`, `geo2stl/opentopo.py::fetch_opentopo_dem` |
| dem | DEM source dispatch (local / h5 / OpenTopography), layer-source registry, payloads | `geo2stl/dem.py::fetch_dem`, `geo2stl/dem.py::fetch_layer_data`, `geo2stl/dem.py::register_layer_source`, `geo2stl/dem.py::DEM_SOURCE_INFO`, `geo2stl/dem.py::compute_raw_dem`, `geo2stl/dem.py::make_dem_payload` |
| water_layers | Rivers and lakes as terrain-relative composite sources (relative depth, blended with `add`) | `geo2stl/water_layers.py::register_water_layer_sources`, `geo2stl/water_layers.py::rasterize_river_depth`, `geo2stl/water_layers.py::resize_relative` |
| hydrology | HydroRIVERS (default) and Natural Earth river rasters | `geo2stl/hydrology.py::HYDROLOGY_LAYER`, `geo2stl/hydrology.py::fetch_and_rasterize_hydrology`, `geo2stl/hydrology.py::merge_rivers_with_dem` |
| trails | Ski and hiking trails (OSM, USFS) rasterized per category | `geo2stl/trails.py::TRAILS_LAYER`, `geo2stl/trails.py::fetch_and_rasterize_trails`, `geo2stl/trails.py::OsmTrailsLayer` |
| projections | Map projections and cross-layer alignment; see [projections.md](projections.md) | `geo2stl/projections.py::project_coordinates`, `geo2stl/projections.py::project_grid`, `geo2stl/projections.py::project_water_arrays`, `geo2stl/projections.py::project_rgb_image`, `geo2stl/projections.py::get_projection_info` |
| raster | GeoTIFF reading | `geo2stl/raster.py::read_geotiff` |
| tiles | Local SRTM tile discovery, cropping, stitching | `geo2stl/tiles.py::get_tile_files`, `geo2stl/tiles.py::stitch_tiles_no_rasterio` |
| geocode | Nominatim place search (1 req/s, cached) | `geo2stl/geocode.py::search_places` |
| landmarks | Named landmarks near a region box edge | `geo2stl/landmarks.py::edge_landmarks`, `geo2stl/landmarks.py::fetch_edge_features` |
| processing | Pure raster ops: clip / smooth / blend / upsample | `geo2stl/processing.py::apply_layer_processing`, `geo2stl/processing.py::blend_layers`, `geo2stl/processing.py::upsample_dem` |
| sat2stl | Satellite bbox JPEG, water mask (ESA/JRC), Earth Engine | `geo2stl/sat2stl.py::fetch_satellite_tiles`, `geo2stl/sat2stl.py::fetch_water_mask`, `geo2stl/sat2stl.py::fetch_sat_overlay`, `geo2stl/sat2stl.py::initialize_earth_engine` |
| write | Notebook-era save helper; not on the app path | `geo2stl/write.py::savefile` |

---

## city2stl — OSM city model, heights, registration

- **City model** — `city2stl/city_model.py`: terrain + OSM layers merged into one printable
  solid (vector layers, manifold3d booleans, one scale for all layers).
  - `city2stl/city_model.py::build_on_terrain` (the export entry), `city2stl/city_model.py::build_city_model`,
    `city2stl/city_model.py::ModelScale`, `city2stl/city_model.py::choose_scale`,
    `city2stl/city_model.py::Terrain`, `city2stl/city_model.py::prepare_dem`,
    `city2stl/city_model.py::resolve_layers`, `city2stl/city_model.py::CityModel`.
  - Why one pipeline → [mesh-pipeline.md](../decisions/mesh-pipeline.md), "Every mesh export runs
    the terrain stage, then the city model"; overview in [pipeline.md](pipeline.md).
- **Heights** — `city2stl/height/`; providers and the merge are in
  [height-providers.md](height-providers.md).
  - `city2stl/height/__init__.py::HeightResult`, `city2stl/height/__init__.py::merge_height_rasters`,
    `city2stl/height/__init__.py::resolution_priority`.
  - `city2stl/height/service.py::enhance_city_data` (registry, selection, per-building fill).
  - `city2stl/height/infill.py::infill_idw`, `city2stl/height/stl_import.py::stl_to_heightmap`.
  - `city2stl/height/predict.py::predict`, `city2stl/height/train.py::train` — ML tooling, not
    wired at runtime (see [height-providers.md](height-providers.md)).
- **OSM heights** — `city2stl/heights.py::height_from_tags` (the one tag rule),
  `city2stl/heights.py::enhance_buildings_with_raster`.
- **Fetch** — `city2stl/fetch.py::fetch_osm_data` (buildings, roads, water, POIs via osmnx),
  `city2stl/fetch.py::fetch_osm_lakes`; Overpass mirrors come from `geo2stl/osm.py`.
- **Cache policy** — `city2stl/cache_policy.py::CITY_PIPELINE_VERSION`,
  `city2stl/cache_policy.py::city_cache_missing_height_source` (when a cached OSM payload is stale).
- **Rasters** — `city2stl/rasterize.py::rasterize_city_data` (row 0 = north, via
  `numpy2stl/src/numpy2stl/raster/burn.py::burn_polygons`);
  `city2stl/osm_raster.py::get_osm_building_heightmap` (registration rasters, row 0 = south).
- **Roads** — `city2stl/roads.py::get_road_width_m` (total width by `highway` tag).
- **Roofs**
  - `city2stl/roofs.py::building_solids` — pitched-roof solids (hipped / gabled).
  - `city2stl/roof_classifier.py::classify_roof_shapes` — satellite-signal cascade.
  - `city2stl/roof_model.py::RoofShapeModel` — trained flat-vs-pitched call; details in
    [roof-shape-model.md](roof-shape-model.md).
  - `city2stl/roof_features.py::extract`, `city2stl/roof_tiles.py::crop_for_ring` (zoom-18
    crops).
- **Landmarks** — `city2stl/landmarks.py::list_landmarks`, `city2stl/landmarks.py::resolve_overrides`
  (replace one building by mesh or survey nDSM).
- **Model cache** — `city2stl/model_cache.py` (see [Caches](#caches)).
- **Registration** — `city2stl/registration/`: the OSM side of STL and plate registration.
  - `city2stl/registration/__init__.py::OSMReference`, `city2stl/registration/__init__.py::register_city_stl`.
  - `city2stl/registration/street_place.py::place_plate`, `city2stl/registration/consensus.py::verdict`,
    `city2stl/registration/critic.py::score_model`.
- **Skyline** — `city2stl/skyline/`: street-view / panorama height pipeline; see
  [skyline README](../../city2stl/skyline/README.md).

---

## numpy2stl — geo-free meshes

Brief map; detail in [numpy2stl README](../../../numpy2stl/README.md).

- **core** — `numpy2stl/src/numpy2stl/core/extrude.py::prism` (indexed watertight prisms),
  `numpy2stl/src/numpy2stl/core/heightfield.py::tin_solid`, `numpy2stl/src/numpy2stl/core/solid.py::Solid`,
  `numpy2stl/src/numpy2stl/core/polygon.py::triangulate_polygon`,
  `numpy2stl/src/numpy2stl/core/generate.py::array_to_mesh` (grid solid).
- **processing**
  - `numpy2stl/src/numpy2stl/processing/decimate.py::heightfield_tin`,
    `numpy2stl/src/numpy2stl/processing/decimate.py::heightfield_tin_budget`,
    `numpy2stl/src/numpy2stl/processing/decimate.py::decimate_to_tolerance`.
  - `numpy2stl/src/numpy2stl/processing/boolean.py::union`, `numpy2stl/src/numpy2stl/processing/boolean.py::cut_jigsaw`.
  - `numpy2stl/src/numpy2stl/processing/simplify.py::simplify_mesh_surfaces` (lossless flat-region simplify).
  - `numpy2stl/src/numpy2stl/processing/building_simplify/__init__.py::simplify_building_mesh`.
- **raster** — `numpy2stl/src/numpy2stl/raster/burn.py::burn_polygons`,
  `numpy2stl/src/numpy2stl/raster/fill.py::fill_nan`, `numpy2stl/src/numpy2stl/raster/segment.py::building_mask`,
  `numpy2stl/src/numpy2stl/raster/vectorize.py::vectorize_buildings`.
- **stl2numpy** — `numpy2stl/src/numpy2stl/stl2numpy/heightmap.py::mesh_to_heightmap` (the one
  mesh → heightmap; `row0="north"` default).
- **io** — `numpy2stl/src/numpy2stl/io/writers.py::writeSTL`, `numpy2stl/src/numpy2stl/io/writers.py::write3MF`,
  `numpy2stl/src/numpy2stl/io/writers.py::writeOBJ`, `numpy2stl/src/numpy2stl/io/readers.py::load_mesh`.
- **registration** — `numpy2stl/src/numpy2stl/registration/`: city STL ↔ reference heightmap;
  takes a reference source, fetches nothing (city2stl supplies `OSMReference`).

---

## App capability → library call

| App capability | App module | Library call |
|---|---|---|
| DEM fetch | `app/server/routers/terrain.py` | `geo2stl/dem.py::fetch_dem`, `geo2stl/dem.py::make_dem_payload`, `geo2stl/processing.py::upsample_dem` |
| DEM sources list | `app/server/routers/terrain.py`, `app/server/routers/settings.py` | `geo2stl/dem.py::DEM_SOURCE_INFO`, `geo2stl/dem.py::default_dem_source` |
| Composite DEM | `app/server/routers/composite.py` | `geo2stl/dem.py::fetch_layer_data`, `geo2stl/water_layers.py::register_water_layer_sources`, `city2stl/fetch.py::fetch_osm_lakes`, `numpy2stl/src/numpy2stl/raster/burn.py::burn_polygons` |
| Projection | `app/server/routers/terrain.py`, `app/server/core/export_params.py` | `geo2stl/projections.py::project_grid`, `geo2stl/projections.py::project_water_arrays`, `geo2stl/projections.py::project_rgb_image` |
| Projection metadata | `app/server/routers/settings.py` | `geo2stl/projections.py::get_projection_info` |
| Satellite / water mask | `app/server/routers/terrain.py` | `geo2stl/sat2stl.py::fetch_satellite_tiles`, `geo2stl/sat2stl.py::fetch_water_mask`, `geo2stl/sat2stl.py::fetch_sat_overlay` |
| Earth Engine auth | `app/server/routers/auth.py` | `geo2stl/sat2stl.py::initialize_earth_engine`, `geo2stl/sat2stl.py::reset_earth_engine_status_cache` |
| OpenTopography key | `app/server/config.py`, `app/server/routers/settings.py` | `geo2stl/opentopo.py::set_api_key` |
| Hydrology / trails | `app/server/routers/terrain.py` | `geo2stl/hydrology.py::fetch_and_rasterize_hydrology`, `geo2stl/trails.py::fetch_and_rasterize_trails` |
| Place search, edge landmarks | `app/server/routers/geocode.py` | `geo2stl/geocode.py::search_places`, `geo2stl/landmarks.py::edge_landmarks` |
| City OSM fetch | `app/server/core/city_data.py` | `city2stl/fetch.py::fetch_osm_data`, `city2stl/cache_policy.py::city_cache_missing_height_source` |
| City raster overlay | `app/server/routers/cities.py` | `city2stl/rasterize.py::rasterize_city_data` |
| Height fetch / sources | `app/server/routers/height.py` | `city2stl/height/service.py::provider_infos`, `city2stl/height/__init__.py::merge_height_rasters` |
| City height enhancement | `app/server/core/city_data.py`, `app/server/routers/cities.py` | `city2stl/height/service.py::enhance_city_data`, `city2stl/heights.py::enhance_buildings_with_raster` (Google 3D) |
| Mesh export (terrain + city) | `app/server/core/export.py`, `app/server/core/city_model_task.py` | `city2stl/city_model.py::build_on_terrain`, `numpy2stl/src/numpy2stl/processing/decimate.py::heightfield_tin_budget`, `numpy2stl/src/numpy2stl/io/writers.py::write3MF`, `numpy2stl/src/numpy2stl/io/writers.py::writeOBJ` |
| Export pre-flight | `app/server/core/preflight.py` | `city2stl/city_model.py::layer_preflight`, `numpy2stl/src/numpy2stl/processing/decimate.py::heightfield_tin` |
| Landmarks | `app/server/core/landmarks.py` | `city2stl/landmarks.py::resolve_overrides`, `city2stl/height/providers/survey.py::PROVIDERS` |
| STL import / registration | `app/server/core/mesh_import.py` | `city2stl/height/stl_import.py::stl_to_heightmap`, `city2stl/height/infill.py::infill_idw`, `city2stl/registration/__init__.py::register_city_stl` |
| Plate registration | `app/server/core/plate_registration.py`, `app/server/routers/registration.py` | `city2stl/registration/street_place.py::place_plate`, `city2stl/registration/consensus.py::score_export`, `city2stl/registration/critic.py::score_model` |
| Puzzle | `app/server/core/puzzle.py` | `numpy2stl/src/numpy2stl/processing/boolean.py::cut_jigsaw`, `numpy2stl/src/numpy2stl/io/writers.py::write3MF` |
| Height model training (offline) | — (`app/server/core/height/train.py` deleted 2026-10-05) | `city2stl/height/train.py::train` |

- The provider registry, selection and `enhance_city_data` live in `city2stl/height/service.py`;
  the app imports it directly. `app/server/core/height/service.py` (an unused async fetch with a
  per-provider cache, plus a re-export) was deleted 2026-09-30; `/api/height/fetch` fetches
  uncached in `app/server/routers/height.py::height_fetch`.

---

## Caches

- **`geo2stl/cache.py`** — the storage primitives; root `map2stl/cache/` or `$MAP2STL_CACHE`.
  - Key: `make_cache_key(namespace, bbox, extra_params)` → MD5 hex; TTL per namespace in
    `geo2stl/cache.py::NAMESPACE_TTL` (default 7 days).
  - Namespaces in `NAMESPACE_TTL`: `dem`, `water`, `satellite`, `osm`, `composite`,
    `opentopo`, `hydrology`, `trails`, `trails_osm`, `geocode`, `landmarks`, and the four
    city-model namespaces below.
  - Registered elsewhere: each height provider registers its own through
    `city2stl/height/providers/_cache.py::register_ttl` (`ndsm`, `lidar_3dep`, `copernicus_bh`,
    `open_buildings`, `gba`, `ghsl`, `wsf3d`, `google3d`, `shadow_height`); the app writes
    `esa_lc` (terrain router); Earth Engine joblib files go to `ee/`
    (`geo2stl/sat2stl.py::CACHE_DIR`, `app/server/config.py::EE_CACHE_DIR`);
    `city2stl/fetch.py::fetch_osm_lakes` writes `osm_lakes`.
- **`city2stl/model_cache.py`** — content-keyed city builds: `city_polygons`, `city_solids`,
  `city_terrain`, `city_models`. Each key digests everything the result depends on plus
  `MODEL_CACHE_VERSION`, so a hit is exactly what a rebuild would produce;
  `MAP2STL_CITY_CACHE=0` disables it.
  - Why → [mesh-pipeline.md](../decisions/mesh-pipeline.md), "City builds are fast through
    print-layer merging, content-keyed caches and no Triangle in draped slabs".
- **`app/server/core/cache.py`** — app-side policy only: `app/server/core/cache.py::prune_cache`,
  `app/server/core/cache.py::prune_all_caches`, `app/server/core/cache.py::clear_bbox_cache`,
  `app/server/core/cache.py::migrate_osm_plain_json`.
- **DEM key and handles** — `app/server/core/dem_cache.py::dem_cache_key` is the single
  definition of the DEM key (projection excluded; projected per request);
  `app/server/core/dem_store.py::DemStore` hands export the exact grid the client saw, by id.
  - Why → [terrain-dem.md](../decisions/terrain-dem.md), "The DEM cache key has exactly one
    definition" and "DEM cache defaults live in exactly one module".
