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
