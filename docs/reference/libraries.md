# Support Libraries -- strm2stl

_Last updated: 2026-05-03_

The app server delegates heavy computation to support libraries. Core modules should be thin wrappers that translate HTTP requests into library calls rather than re-implementing library logic.

> Principle: app/server/core/ is an API adapter around geo2stl, city2stl, and numpy2stl.

---

## Library Overview

```mermaid
flowchart LR
    subgraph Server ["app/server/core/"]
        DEM["dem.py"]
        EXP["export.py"]
        HEIGHT["height/train.py"]
    end

    subgraph Libs ["Support Libraries"]
        GEO["geo2stl/<br/>projections, tiles,<br/>DEM/sat/hydrology helpers"]
        CITY["city2stl/<br/>height providers,<br/>OSM/city mesh"]
        NUMPY["numpy2stl/<br/>mesh generation"]
    end

    DEM --> GEO
    HEIGHT --> CITY
    EXP --> NUMPY
```

---

## Import Map

### core -> libraries

| Core module | Library | Import | Purpose |
|---|---|---|---|
| dem.py | geo2stl.dem | fetch_layer_data, fetch_local_dem, ... | DEM/data layer fetch + processing via shim |
| export.py | numpy2stl | array_to_mesh, writeOBJ, write3MF | Mesh generation and writers |
| height/train.py | city2stl.height.train | train helpers | Training shim + server-specific tile collector |

### routers -> libraries (intentional direct use)

| Router | Library | Import | Note |
|---|---|---|---|
| routers/terrain.py | geo2stl.hydrology | HYDROLOGY_LAYER | Uses class-based hydrology service object |
| routers/terrain.py | geo2stl.sat | SAT_LAYER | Uses class-based satellite service object |
| routers/terrain.py | geo2stl.projections | project_grid, project_water_arrays, project_rgb_image | Projection helpers now imported directly from geo2stl |
| routers/cities.py | city2stl.fetch/rasterize/mesh/heights | fetch_osm_data, rasterize_city_data, generate_city_3mf, enhance_buildings_with_raster | City pipeline endpoints |
| routers/settings.py | geo2stl.projections | get_projection_info | Projection metadata endpoint |

### session -> libraries

terrain_session.py is primarily an HTTP client. It also imports city2stl provider classes for offline provider-merge utilities used in the SDK layer.

---

## Library Public APIs (high-value surface)

### geo2stl/

| Module | Key functions |
|---|---|
| dem.py | fetch_layer_data, fetch_local_dem, fetch_h5_dem, compute_raw_dem |
| sat.py | SAT_LAYER, fetch_satellite_tiles, fetch_water_mask, fetch_sat_overlay, fetch_bbox_image |
| sat2stl.py | compatibility shim -> geo2stl.sat |
| projections.py | get_projection_info, project_coordinates, project_grid |
| hydrology.py | HYDROLOGY_LAYER, fetch_and_rasterize_hydrology, merge_rivers_with_dem |
| hydrorivers.py | HydroRIVERS backend helpers used by hydrology service |
| write.py | save_im, save_stl, savefile (mostly notebook/offline helpers) |

### numpy2stl/

| Category | Key functions |
|---|---|
| Core | array_to_mesh, polygon_to_prism, perimeter_to_walls, writeSTL, write3MF, writeOBJ |
| simplify | simplify_mesh_surfaces |
| boolean/view/verify | advanced or notebook/offline workflows |

### city2stl/

| Area | Key modules/functions |
|---|---|
| Height providers | city2stl.height.providers.* (copernicus, ghsl, google_3d, lidar_3dep, ndsm, open_buildings, roofnet, shadow_height, wsf3d) |
| Height orchestration | city2stl.height.merge_height_rasters, provider_stats |
| City raster + mesh | city2stl.fetch, city2stl.rasterize, city2stl.mesh |
| Height enhancement | city2stl.heights.enhance_buildings_with_raster |

---

## Thin Wrapper Assessment

| Module | Status | Notes |
|---|---|---|
| core/dem.py | Clean | Pure compatibility shim into geo2stl.dem |
| core/height/train.py | Mixed | Re-exports training helpers + server-specific collect_tiles() |
| core/export.py | Clean | Correctly remains in core due to HTTP concerns (task lifecycle, responses, headers, temp files); mesh ops delegated to numpy2stl |

No active library-boundary debt is currently tracked here.

---

## Unused or Limited-Use Capabilities

| Library | Capability | Notes |
|---|---|---|
| geo2stl.write | save_* helpers | Mostly notebook/offline; app primarily uses export pipeline |
| numpy2stl.writeSTL | direct STL writer | App export path uses trimesh for STL repair/export |
| numpy2stl boolean/view/verify | advanced utilities | Mostly offline/notebook workflows |

The app actively uses these support-library functions in production paths: array_to_mesh, write3MF, writeOBJ, make_dem_image, fetch_satellite_tiles, fetch_water_mask, calculate_scale_for_dimensions/project_coordinates (through wrappers), hydrology rasterization, and city2stl height/city pipeline functions.
