# Height providers

_Last updated: 2026-09-28_

Where building heights come from: raster providers merged per pixel, survey nDSM providers for
landmarks, and OSM tags. One module per source in `city2stl/height/providers/`. Why the policy
is what it is → [building-heights.md](../decisions/building-heights.md),
[survey-lidar.md](../decisions/survey-lidar.md).

---

## Registry and merge

- **Registry** — `city2stl/height/service.py::_REGISTRY` lists each provider with the confidence
  and resolution the API reports; `city2stl/height/service.py::provider_infos` adds coverage for a
  bbox; `city2stl/height/service.py::set_opentopo_api_key` rebinds the OpenTopography providers.
- **Merge** — `city2stl/height/__init__.py::merge_height_rasters`: per-pixel, highest
  confidence × `city2stl/height/__init__.py::resolution_priority` wins.
  - A coarse cell averages roof with street, so resolution demotes it (√ of cell-size ratio)
    rather than erasing it.
  - Why → building-heights.md, "Height provider priority accounts for resolution, and sources
    coarser than 10 m are refused"; "The merge stays per-pixel".
- **City enhancement** — `city2stl/height/service.py::enhance_city_data` fills buildings whose
  height is the `default`: 3DEP lidar footprints first (US), then the fine raster providers;
  sources coarser than `city2stl/height/__init__.py::BUILDING_RESOLUTION_LIMIT_M` (10 m) are
  refused for per-building heights. An OSM tag height is a floor, not an estimate
  (`city2stl/heights.py::height_from_tags`).
- **App side** — `app/server/routers/height.py::height_fetch` runs the selected providers in the
  executor, merges and projects (no per-request result cache). Provider
  rasters cache through `city2stl/height/providers/_cache.py::register_ttl` (see
  [packages.md](packages.md#caches)).

## Raster providers (in `_REGISTRY`)

| Provider | Source | Resolution | Confidence |
|---|---|---|---|
| `lidar_3dep.py` | Copernicus COP30 DSM − SRTM DTM via OpenTopography (US; not lidar despite the name) | 30 m | 0.82 |
| `ndsm.py` | GLO-30 (AWS) − SRTM (OpenTopography); 56°S–60°N | 30 m | 0.80 |
| `copernicus.py` | Copernicus EU Building Height 2012 (EEA WCS); Europe only | 10 m | 0.70 |
| `open_buildings.py` | Overture Maps buildings `height` / `num_floors` (not Google Open Buildings) | per footprint (~5 m) | 0.60 |
| `gba.py` | GlobalBuildingAtlas LoD1 (TUM), Source Cooperative parquet; global and complete | 3 m | 0.55 |
| `wsf3d.py` | DLR World Settlement Footprint 3D tiles; `wsf3d_global.py` reads the global BigTIFF by HTTP range when a tile 404s | ~90 m | 0.50 |
| `ghsl.py` | JRC GHS-BUILT-H R2023A, global last resort | 100 m | 0.40 |

- GBA sits below Overture on measured accuracy but is the only globally complete per-building
  source → building-heights.md, "GlobalBuildingAtlas admitted at confidence 0.55, below Overture".
- The WSF3D per-tile endpoint covers only 453 tiles → building-heights.md, "The WSF3D per-tile
  endpoint is a 453-tile sample…".

## Outside the registry

- **Google 3D Tiles** — `city2stl/height/providers/google_3d.py::Google3DProvider`:
  photogrammetric DSM binned from Photorealistic 3D Tiles meshes, ~1 m, confidence 0.9; needs
  `GOOGLE_MAPS_API_KEY`. Used by `/api/cities/enhance-heights` and the SDK.
  - **Allowed** (2026-09-27) for heights, roof geometry and labels → building-heights.md,
    "Google 3D Tiles is allowed as a height and geometry source".
- **3DEP lidar per footprint** — `city2stl/height/providers/lidar_3dep_copc.py::footprint_heights`:
  USGS 3DEP COPC point clouds (Planetary Computer), roof median minus ground ring; US only.
- **Shadow heights** — `city2stl/height/providers/shadow_height.py::ShadowHeightProvider`:
  deprecated, research only → building-heights.md, "Shadow heights stay a research track…".
- **Google Open Buildings 2.5D Temporal** — `city2stl/height/providers/google_ob25d.py`: v1
  (Sirko et al. 2023), 0.5 m rasters (~4 m effective), heights capped at 100 m; Africa, S/SE Asia,
  Latin America and the Caribbean (not the US mainland or Hawaii). Per-footprint readings for
  `skyline/lowrise_prior.py`; not in `_REGISTRY` (measured only as a low-rise prior, refused →
  building-heights.md, "Google Open Buildings 2.5D does not replace the T41 prior…"). CC BY 4.0
  (`ATTRIBUTION`).

## Survey providers (landmark nDSM)

- **Interface** — `city2stl/height/providers/survey.py::ndsm_for_bbox` and
  `city2stl/height/providers/survey.py::available_for_bbox` over
  `city2stl/height/providers/survey.py::PROVIDERS`; contract and caching in
  `city2stl/height/providers/_survey.py::cached_ndsm` (float32, row 0 = north, metres above
  ground, lon/lat Affine).
- **Providers**
  - `ign_lidarhd.py` — France, IGN LiDAR HD MNH, 0.5 m (WMS-R GeoTIFF).
  - `rediam_mdhn.py` — Andalucía, REDIAM MDHN, 1 m, 2020-21 (regional COG).
  - `cnig_mdsn.py` — Spain, CNIG MDSn edificación, 2.5 m, 2008-15 (IDEE WCS).
  - `cuzk_dmp.py` — Czechia, ČÚZK DMP 1G − DMR 5G, ~1 m (ArcGIS ImageServer).
  - `usgs_3dep` — USA, `lidar_3dep_copc.py` (COPC via laspy) first, then `lidar_3dep_ept.py`
    (Entwine/PDAL).
- Used by the landmark override (`city2stl/landmarks.py::resolve_overrides`) and
  `/api/cities/survey-sources`. Per-city sources and gaps → [survey-sources.md](survey-sources.md).
- Why one contract, gated endpoints documented not scraped → survey-lidar.md, "Survey providers
  share one nDSM contract…".

## Why no CNN at runtime

- The Retna height CNN was never promoted or wired at runtime: no `retna*.pt` is loaded by
  `app/`, `city2stl/` or `geo2stl/`.
- `city2stl/height/predict.py::predict` is reached only from the SDK and defaults to a
  checkpoint that does not exist; `city2stl/height/train.py::train` is offline tooling.
- Heights come from providers and OSM tags; the only live model is the roof-shape GBM
  ([roof-shape-model.md](roof-shape-model.md)).
- Tooling doc archived at [ml-pipeline.md](../history/ml-height/ml-pipeline.md); why →
  [ml-height.md](../decisions/ml-height.md).
