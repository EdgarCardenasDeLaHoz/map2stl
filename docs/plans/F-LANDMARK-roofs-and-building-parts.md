# F-LANDMARK — Roofs, building parts and landmark detail

Status: planned 2026-09-27; §6 (scoring) done 2026-09-27; §3 overrides, §4 survey sources,
§5 Landmarks panel done 2026-09-27 (user: "we still have not finalized any pipelines for adding
rooftop details for rendering complex key buildings like cathedrals, city hall").
Survey: this session's read-only audit (roofs, parts, sources) — findings below.

## Where it stands

- `city2stl/mesh._extrude_ring_with_roof`: flat, pyramidal, gabled = hipped, skillion;
  dome / onion / cone map to pyramidal. **Gabled and hipped come out flat on any convex
  footprint**: roof z is a function of distance from the ridge axis but no ridge vertices
  are added, so every outline vertex is at the eave (confirmed on a 10 × 6 rectangle).
- `city_model._roofed`: pitched roofs on concave footprints fall back to flat at mid-roof.
- `building:part`, `min_height`, `building:min_level`, `roof:direction`, `roof:orientation`,
  `roof:levels` are fetched (`fetch.py`) but unused; parts are unioned with the outline.
- Roof classifier (`roof_classifier.py`) only decides pitched vs flat, SDK-only.
- Heights: OSM tag > levels × 3.2 > 10 m; US lidar gives one number per building.
- Google 3D Tiles provider is still live code although ToS-prohibited (decision needed).

## Approach

1. **Roof geometry** (`city2stl/roofs.py`, replaces the mesh.py generator):
   - near-rectangular footprints (area ≥ 0.9 × min rotated rectangle): exact gable / hip /
     half-hip / gambrel / mansard / pyramid with ridge vertices, along `roof:direction` /
     `roof:orientation` when tagged, long axis otherwise;
   - any other footprint: straight-skeleton hip roof (faces at one pitch from each eave);
     gable = skeleton with gable-end edges held vertical;
   - dome, onion, cone, round: surfaces of revolution over the footprint's inscribed
     circle (segment count from print resolution), on a drum up to the eave;
   - every roof validated closed; failure → flat at mid-roof (as now) and counted.
2. **Building parts (S3DB)**: where parts exist, the outline contributes only the area no
   part covers; each part is its own solid from `min_height` (or `building:min_level` ×
   3.2 m) to its height with its own roof. Cathedral towers, spires (cone/pyramid), domes
   and apses then come from OSM tags alone.
3. **Landmark overrides** (per OSM id): (a) a mesh (STL/OBJ/glTF) placed by footprint
   alignment and scaled to the model, or (b) an nDSM patch (lidar DSM − DTM) turned into a
   roof solid clipped to the footprint. Stored per region; applied by `build_on_terrain`.
   **Done 2026-09-27** (`city2stl/landmarks.py`, `app/server/core/landmarks.py`):
   - ids: `fetch.py` keeps `osm_id` (`way/123`) plus `name`, `building`, `amenity`, `historic`,
     `tourism`; a dissolved group keeps its largest member's (so overriding that id replaces the
     merged footprint). `CITY_PIPELINE_VERSION` 4; a v3 cache re-fetches only its buildings.
   - storage: table `region_landmarks` (region, osm_id, spec_json), not a key in
     `region_settings` — every panel save replaces that blob wholesale and would drop overrides.
   - resolution before geometry (`resolve_overrides`): mesh loaded, vertices merged, holes
     filled, then refused with the reason if manifold3d still cannot read it as a solid; nDSM
     fetched for footprint + 5 m. A failure fails the export task naming the landmark.
   - mesh placement: min-rotated-rectangle long axis onto the footprint's, per-axis
     (`fit=rectangle`) or uniform scale, 0°/180° chosen by overlap, then manual rotation /
     scale / offset (m). Vertical `true` = mesh proportions at the horizontal fit (geometric mean
     of the two stretches) × `z_mm_per_m`; `fit` = `height_m` (OSM height by default). glTF is
     Y-up (auto by extension). Base on the footprint's highest ground, prism skirt down to the
     lowest, clipped to the model.
   - nDSM: TIN of the footprint outline + raster cell centres inside it, bilinear heights,
     voids filled from the nearest measured cell, closed to the skirt floor; < 30 % measured
     coverage is refused. A geometry-time failure keeps the OSM building and is reported.
   - `LandmarkPlan` runs inside `build_layer`: overridden building, its parts and any other
     extrude-layer feature ≥ 50 % inside (e.g. the `churches` copy) are removed; report block
     `landmarks: {osm_id: {status: applied|failed|missing, ...}}`.
4. **Survey sources** (for 3b): promote the lidar readers from scratch into
   `city2stl/height/providers/`: USGS 3DEP (exists), Spain CNIG PNOA / REDIAM (Granada,
   Cartagena ES), France IGN LiDAR HD, Prague ČÚZK; 1 m nDSM per landmark footprint.
   **Done 2026-09-27**: `ndsm_for_bbox(bbox, resolution_m) -> (array row0=north, lon/lat
   Affine) | None` in `ign_lidarhd` (WMS-R MNH float GeoTIFF, 0.5 m), `rediam_mdhn` (regional
   COG, 1 m), `cnig_mdsn` (IDEE WCS `mdsn_e025`, buildings only, 2.5 m, 2008-15 — also covers
   Cartagena ES), `cuzk_dmp` (DMP 1G − DMR 5G), and 3DEP (`lidar_3dep_copc.ndsm_for_bbox` grids
   the COPC cloud; `lidar_3dep_ept.ndsm_for_bbox` wraps the PDAL path); registry `survey.py`,
   shared contract/cache `_survey.py`. All checked live once, tested offline with synthetic
   GeoTIFFs. Gaps (not scraped): CNIG 0.5 m surfaces of the 2nd/3rd PNOA coverages (download
   form only), Lisbon (account), Barcelona ICGC (WMS returns pictures), Salzburg BEV (not wired),
   Cartagena de Indias (no open surface). REDIAM has voids on Granada Cathedral's roofs (~26 %
   of the window). Details: `Code/docs/survey-sources.md`.
5. **UI**: a Landmarks panel — notable buildings (place of worship, town hall, castle,
   attraction, tallest N) with part count, roof shapes, height source; per landmark:
   choose OSM parts / nDSM / uploaded mesh, preview in 3D.
   **Done 2026-09-27**: `CityLandmarksSection.vue` (Settings → Fetch, under Fetch Layers);
   `POST /api/cities/landmarks`, `POST /api/cities/landmarks/preview` (the landmark alone,
   true scale, no slenderness cap, through `build_on_terrain`),
   `/api/regions/{name}/landmarks/{osm_id}`; saved overrides go out with the City Model build as
   `landmark_overrides`.
6. **Scoring**: promote the scratch "plate critic" (generated model vs plate / lidar) to
   `tools/critic/` and track roof error on the landmark set.
   **Done 2026-09-27** as `city2stl/registration/critic.py` (library code, not `tools/`, so the
   app can call it): the scratch `critic/` code was not recoverable, so the metrics were
   re-implemented from `Code/docs/plate-critic.md` — both surfaces as height above ground in
   metres on the reference grid; plate warped by the pack's placement and put in metres by the
   tallest-roof anchor; our model as filled footprints at `height_m` or a rendered STL with the
   80 m top-hat ground removed (scale fitted by median ratio when units are unknown). Reports
   per-building median |error| (headline), p90, bias, within 2/5 m, footprint IoU / precision /
   recall, per-cell MAE / r, and a roof shape error flagged unresolvable above 2 m cells. The
   learned critic was **not** promoted (monotone in relief, 0/8 argmin agreement). Endpoint
   `POST /api/registration/critic/score`; UI "Score this model" in the Export tab
   (`ModelScorePanel.vue`). Tracking roof error on the landmark set waits on §1–§3 and on a
   ≤ 1 m reference (lidar nDSM or surveyed pack such as Old San Juan).

## Target files

`city2stl/roofs.py` (new), `city2stl/city_model.py` (parts + overrides), `city2stl/fetch.py`
(part fields), `city2stl/height/providers/*` (EU lidar), `app/server/routers/cities.py`
(landmark list / override API), client Landmarks panel, `tests/test_roofs.py`.

## Success criteria

- Gable/hip roofs rise to `roof:height` on rectangles and L-shapes; closed solids.
- Granada Cathedral, Alhambra towers, Cartagena city hall rendered from OSM parts with
  spires/domes where tagged; a landmark with an nDSM override matches lidar to ≤ 1 m.
- Registration/critic run shows no regression on footprint IoU.

## Risks

- Straight skeleton robustness on messy OSM outlines (use a tested implementation, fall back
  to flat); part/outline overlap tagging errors in OSM; EU lidar download endpoints are
  undocumented.
