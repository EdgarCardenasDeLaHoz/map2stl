# Survey and lidar sources

Surveyed elevation (lidar DSM/DTM, national nDSMs) used as a height reference and for landmark
overrides: which endpoints work, how they are read, and what they can and cannot measure.
Endpoint catalogue: [../reference/survey-sources.md](../reference/survey-sources.md). Related:
[building-heights.md](building-heights.md), [roofs-landmarks.md](roofs-landmarks.md).

### 2026-09-27 — Survey providers share one nDSM contract; gated endpoints are documented, not scraped
- **Decision:** every survey provider exposes
  `ndsm_for_bbox(bbox, resolution_m) -> (array row0=north, lon/lat Affine) | None`, dispatched by
  `city2stl/height/providers/survey.py::ndsm_for_bbox` with a shared cache/contract in
  `city2stl/height/providers/_survey.py`. Providers: `ign_lidarhd` (France, 0.5 m), `rediam_mdhn`
  (Andalusia COG, 1 m), `cnig_mdsn` (Spain IDEE WCS `mdsn_e025`, buildings only, 2.5 m, also covers
  Cartagena ES), `cuzk_dmp` (Prague DMP 1G − DMR 5G), and US 3DEP via
  `city2stl/height/providers/lidar_3dep_copc.py::ndsm_for_bbox` (COPC) and
  `city2stl/height/providers/lidar_3dep_ept.py::ndsm_for_bbox` (PDAL/EPT).
- **Why:** landmark nDSM overrides need a 1 m-class surface per footprint, from whichever country
  has one, behind one call. Each was checked live once and is tested offline on synthetic GeoTIFFs.
  Spain's national building nDSM turned out to have a documented WCS (`wcs-mds.idee.es/mds`).
- **Rejected:** scraping endpoints that need an account or an undocumented download form — CNIG
  0.5 m 2nd/3rd PNOA surfaces (form only), Lisbon (account), Barcelona ICGC (WMS returns pictures),
  Salzburg BEV (not wired), Cartagena de Indias (no open surface). Known gap: REDIAM has voids over
  ~26 % of Granada Cathedral's roofs.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-27 F-LANDMARK §3-§5 entry (survey part);
  [F-LANDMARK plan](../plans/active/F-LANDMARK-roofs-and-building-parts.md) §4.

### 2026-09-04 — An OpenTopography 401 usually means the daily cap, not a bad key
- **Decision:** on a 401 from `portal.opentopography.org/API/globaldem`, read the response body before
  touching the API key.
- **Why:** mid twenty-city sweep, both the COP30 fetch (`city2stl/height/providers/lidar_3dep.py::_fetch_opentopo_dem`)
  and the nDSM's SRTM fetch (`city2stl/height/providers/ndsm.py::_fetch_srtm_opentopo`) returned 401;
  the body said "API maximum rate limit reached (50 API calls/24hrs)". After a multi-city batch, expect
  every DTM-dependent provider to degrade to NaN for the rest of the day.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 entry.

### 2026-09-04 — 3DEP point clouds are read from the Entwine octree, all levels, not from the staged LAZ host
- **Decision:** read USGS lidar from the Entwine (EPT) octree on the `usgs-lidar-public` bucket
  (`city2stl/height/providers/lidar_3dep_ept.py`), querying nodes that meet the area, reading every
  level from the root down.
- **Why:** The National Map's download URLs on `rockyweb.usgs.gov` time out on connect every time, and
  the S3 staging bucket holds only `browse/` and `metadata/` for point-cloud products. The octree also
  suits the job: nodes by area instead of whole tiles.
- **Trap:** an octree level is a sample, not a coarse copy — one level covered 43 % of cells and looked
  like a survey gap; all levels covered the land at 2.3 pts/m².
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 entry.

### 2026-09-04 — A rampart is bare earth, so a wall channel is read from the terrain
- **Decision:** read city walls out of the bare-earth (DTM) surface, not the height-above-ground
  surface where buildings live.
- **Why:** the Old San Juan survey classifies masonry tops as ground. On OSM wall cells the
  above-ground surface stands only 1.33 m, and a third of wall cells carry anything ≥ 2.5 m; in the
  bare earth the wall stands 1.42 m above its neighbourhood at the median, 5.09 m at p90 (building
  cells 0.13 m, land 0.01 m).
- **Rejected:** treating walls like buildings — finds two thirds of nothing.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 "A rampart is bare earth, not a building".

### 2026-09-04 — Puerto Rico's 1 m 3DEP DEM confirms the 6 m city-wall width
- **Decision:** keep `city2stl/registration/osm_water.py::BARRIER_WIDTH_M` `city_wall` = 6.0 m.
- **Why:** 3DEP covers Puerto Rico at 1 m (`PR_PuertoRicoUSVI_D24`, public domain, S3
  `prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/`, no key), readable in one GDAL
  `/vsicurl/` call. Cross-sections every 5 m across 207 `barrier=city_wall` ways in Old San Juan: median
  width 5.0 m, quartiles 4.5 / 11.0. The 5 `barrier=wall` profiles are too few (16 m median is retaining
  walls running off the profile).
- **Rejected:** OpenTopography for this — its `USGS1m` dataset is restricted to academics (401); its
  COP30/SRTMGL1 (30 m) and USGS10m do serve Puerto Rico.
- **Limits:** the 1 m product is bare earth (a wall reference, not a building plate); it is US-only, so
  Cartagena's wall width stays unverified.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 entry.
