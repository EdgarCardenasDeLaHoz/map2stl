# Survey and lidar sources

Surveyed elevation (lidar DSM/DTM, national nDSMs) used as a height reference and for landmark
overrides: which endpoints work, how they are read, and what they can and cannot measure.
Endpoint catalogue: [../reference/survey-sources.md](../reference/survey-sources.md). Related:
[building-heights.md](building-heights.md), [roofs-landmarks.md](roofs-landmarks.md).

### 2026-10-13 — Granada is a survey-only truth city: REDIAM 1 m read by window over three screened areas
- **Decision:** `benchmark.REGIONS["granada"] = "rediam_mdhn"`, `TRUTH_AREAS["granada"]` (cathedral,
  alhambra, centre; union ~1.7 x 2.3 km) in `scripts/19_refresh_survey_truth.py`, and
  `--new-truth` run for it: 20 tiles of 620 m read from the REDIAM COG through `/vsicurl/` range
  reads (<= 105 MB received, system-wide counter; the 284 GB mosaic is never fetched). 13,132
  survey-only truth records (p95 over the footprint, flights 2020-21) of 17,043 footprints in the
  areas; 3,911 are void (no cells). Also fixed: `new_truth` dropped every footprint of a provider
  without per-footprint projects (only EPT has them), so it would have read nothing for CNIG,
  REDIAM, IGN or CUZK.
- **Why (first look at OSM tags, truth records with a tag, all of them / not nested):**
  - Tags exist for 98.6 % of the 39,636 OSM footprints in the bbox (13,051 of 13,132 truth
    records), almost all levels x 3.2 m (6.4, 9.6, 12.8, 16, 19.2) or a flat 10 / 12 m on
    the Alhambra towers: coverage is not quality.
  - Survey height bands: < 15 m 10,018; 15-40 m 3,112; 40-100 m 2; > 100 m 0 (max 40.4 m).
  - Tag within 25 % of the survey: < 15 m 1,584 of 9,949 (16 %; not nested 620 of 5,743, 11 %);
    15-40 m 1,609 of 3,100 (52 %; not nested 621 of 1,414, 44 %). Tag minus survey: median +5.4 m.
  - Nested (`nested_keys`, over all footprints): 19,478 of 39,636; 5,907 of the 13,132 truth
    records. Stale: 0 (29 OSM `start_date`s in the bbox, 13 truth records predate the survey, none
    postdate it; `readings_2x` needs image readings, none yet).
  - Sanity: Cathedral outline (8,341 m2, tag 45 m) is void, as `survey-sources.md` says; its small
    parts read 37-40 m, tags 40 m. The 81 m tower is not read (no record above 40.4 m). Alhambra:
    Torre de la Vela 29.3 m, Homenaje 23.4 m, Alcazaba 22.4 m (p95), Palacio de Carlos V 22.5 m,
    against a 10 m tag; the REDIAM flight is 2020-21.
- **Rejected:** reading the whole bbox (3.3 x 4.9 km) now; the three areas are what the drone
  spheres reach.
- **Limits:** no height above 40 m means the tall bands have no truth here; the void roofs keep
  the Cathedral itself unscored; a taller reading needs a second source.
- **Supersedes / superseded by:** —
- **Source:** `runs/benchmark/truth/granada.json`, session 2026-10-13.

### 2026-10-09 — The EPT project read is the newest by flight year, from a measured table, the name, then the work-unit suffix
- **Decision:** `city2stl/height/providers/lidar_3dep_ept_laspy.py::_project_year` ranks a project
  by `PROJECT_YEARS` (flight year measured from one node's GpsTime), else the latest year in its
  name, else the USGS work-unit suffix `_<letter><yy>` (`HI_NOAAMauiOahu_2_B20` -> 2020, the
  funding year, so a lower bound on the flight), else 0. `projects_for_bbox` parses the boundary
  index once per process into an STRtree (`_project_index`) and memoises the spatial answer per
  bbox. `lidar_3dep.py::US_EXTENTS` adds Puerto Rico/USVI, Guam/CNMI, Niihau and St. Lawrence
  Island to the US pre-check. A dropped node read is retried (`_get_node_bytes`, 3 tries).
- **Why:**
  - Honolulu: `HI_NOAAMauiOahu_2_B20` (flown 2023-03-09 at Waikiki) had no year in its name, ranked
    last, and `USGS_LPC_HI_Oahu_2012_LAS_2015` (flown 2013-06-20) was read, missing the 2017-2018
    towers. 226 of the 230 projects without a four-digit year carry the suffix.
  - San Juan: `USGS_LPC_PR_PuertoRico_2015_LAS_2018` was flown 2016-03-19 but ranked 2018 by its
    LAS year, level with the post-Maria `USGS_LPC_PR_PRVI_E_2018` (2018-05-14), which won on its
    name only. And the old US boxes left out Puerto Rico, so the EPT reader read nothing there.
  - The lookup re-read the 2279-feature JSON and rebuilt every shape per call: 1.1-1.2 s per call
    on an idle PC, 3.3 s under load, and `benchmark.survey_part` makes one or two calls per
    footprint. Indexed: 0.2-0.3 ms per call after a 1 s build, the same answers (San Juan and
    Honolulu samples).
  - One S3 `ConnectionResetError` failed a whole 207-footprint Honolulu tile.
- **Rejected:** the year from `ept.json` (it carries no date) or the index properties (only
  `name`, `id`, `count`, `url`); reading GpsTime at lookup time (a network read per project in a
  per-footprint call).
- **Supersedes / superseded by:** —
- **Source:** t40-review, new benchmark cities README (Problems 2-4).

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
