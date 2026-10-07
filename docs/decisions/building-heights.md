# Building heights

Where per-building heights come from (OSM tags, raster providers, Google 3D Tiles, shadows), how
providers are merged and ranked, and how height accuracy is measured. Related:
[survey-lidar.md](survey-lidar.md) (surveyed lidar references), [roofs-landmarks.md](roofs-landmarks.md)
(roof geometry). Research notebook behind the shadow entries: [../research/shadow-heights.md](../research/shadow-heights.md).

### 2026-10-07 — Two agreeing satellite shadows do not verify a height; satellite readings are weighted by method and height
- **Decision:** a building counts as verified by two images only when independent methods agree:
  two drone seeds, drone and satellite, or satellite lean and shadow on a tall building. Shadow
  agreement across scenes alone does not count. In fusion, a satellite reading gets
  `weight_scale = min(1, (0.15 / sigma_log)^2)` by method and band:
  - lean over 100 m: 1;
  - lean 40-100 m: 0.35-0.9 by confidence;
  - lean under 40 m: dropped;
  - shadow over 40 m: 1;
  - shadow 15-40 m: 0.05;
  - shadow under 15 m: 0.03-0.5 by confidence.
  A shadow is a lower bound and never outvotes a higher photo reading. Several shadow scenes of
  one building count as one reading.
- **Why:** tested on 1,794 LiDAR-confirmed buildings in 7 cities (Prague, Miami, Seattle,
  Madrid, Benidorm, La Défense, Chicago).
  - One satellite reading is within 25 % of truth 37 % of the time; two agreeing readings 64 %.
  - Miami shadows from different scenes agreed on 8 towers over 100 m and 1 was right: a shadow
    cut by a podium or neighbour is cut the same way in every scene. Shadows read 20-40 % low.
  - Lean over 100 m is within 25 % 94 % of the time (Chicago WV03). Where lean and shadow agree
    on a building over 100 m, 26 of 27 are right.
- **Rejected:** counting any two agreeing satellite scenes as verified. On Cartagena that gave
  214 "sat-sat verified" buildings, overstating the result.
- **Supersedes / superseded by:** —
- **Source:** scratchpad satellite_cities/validation.md (2026-10-07), copied to
  `Code/claude/scratch_backup_2026-10-07/`. Multi-scene stereo is unvalidated: Miami's archive is
  near-nadir orthos, and Chicago's scenes are not fetched yet.

### 2026-10-06 — Drone fusion and trust rules for elevated seeds
- **Decision:** `city2stl/skyline/footprint_detect.py::fuse_heights` overrules by a factor `overrule=3`.
  - A reading 3x off the other seeds' agreeing value is dropped.
- **Decision:** `city2stl/skyline/_pano/elevated.py::trusted` accepts a roof reading only with confidence >= 0.5 and distance <= 1000 m.
- **Decision:** the overhead camera fit (`_pano/elevated.py::overhead_pose`) is used when the waterline camera height is < 15 m, the waterline fit fails, or the height is > 130 m.
- **Why:**
  - Gran Bay: seed_4 read 20 m at a depth edge, seed_6 read 170 m from a confident roof fit; the tag agrees with 170.
  - Untrusted readings are wrong (median 200-400 % against tags), so the gates are not relaxed.
- **Rejected:** looser trust gates — coverage has to come from more images, not weaker gates (see Rejected hypotheses, 2026-10-06).
- **Supersedes / superseded by:** —
- **Source:** T43 in [TASKS](../agents/TASKS.md); `city2stl/skyline/_pano/roof_fit.py::fit_roof_heights`.

### 2026-10-03 — Skyline height accuracy is judged on surveyed truth, not on Cartagena
- **Decision:** skyline (street-view) height changes are scored on eight cities with per-footprint
  truth from survey lidar and Google 3D Tiles, cross-checked: Miami, Chicago, Seattle, Boston,
  Benidorm, Paris La Défense, Madrid Cuatro Torres, Prague Pankrác. Cartagena stays a heading and
  registration smoke test only.
- **Why:** user, 2026-10-03: Cartagena "has poor ground truth for building heights, it might
  confuse results". Its truth is 22 OSM `building:levels` × 3.4 m values and no open survey exists.
  Earlier skyline decisions rested on about 20 such buildings.
- **Rejected:** OSM height tags as the yardstick (sparse, floor-count estimates); one truth source
  alone (the user chose both, so new construction and ground-estimate errors show as disputes).
- **Supersedes / superseded by:** —
- **Source:** [F-SKYBENCH](../plans/active/F-SKYBENCH-height-benchmark.md)

### 2026-09-27 — Google 3D Tiles is allowed as a height and geometry source
- **Decision:** Google Photorealistic 3D Tiles stay a supported provider
  (`city2stl/height/providers/google_3d.py::Google3DProvider`), usable for heights, roof geometry and
  as a label source (ray-cast shadow labels, roof-pitch measurement).
- **Why:** user decision 2026-09-27, reversing the earlier "off limits / not approved" note. The
  project owner had already determined in Aug 2026 that Google's terms do not bar this use, the work
  being non-commercial research ([shadow notebook](../research/shadow-heights.md#google-3d-tiles-as-a-label-source)).
  Scope of that determination: research use, not redistribution of a derived dataset or a
  commercial product built on one. Revisit if the project's purpose changes.
- **Rejected:** treating the tiles as barred by ToS — the restriction was never written down
  anywhere in the repo (only listed as an unexplained item in
  [AUDIT-2026-05-17](../history/audits/AUDIT-2026-05-17.md)).
- **Supersedes / superseded by:** supersedes the "Google 3D Tiles probe: not approved, do not start"
  line in claude/memory-bank/activeContext.md and the AUDIT-2026-05-17 "can't be used (ToS)" item. No
  decisions.md entry had forbidden it.
- **Source:** memory project_building_heights.md; claude/memory-bank/activeContext.md (2026-09-27);
  [F-LANDMARK plan](../plans/active/F-LANDMARK-roofs-and-building-parts.md) "Where it stands".

### 2026-09-04 — The WSF3D per-tile endpoint is a 453-tile sample, so a global fallback reads the BigTIFF
- **Decision:** when every one-degree WSF3D tile 404s, `city2stl/height/providers/wsf3d.py::WSF3DProvider`
  falls back to range reads of the single global BigTIFF (`city2stl/height/providers/wsf3d_global.py::read_grid`,
  86.58 m/px, Int16 × 0.1), cached under the same namespace. A failed global read returns the old empty
  raster — a coarse provider that raises would take the whole merge down.
- **Why:** the tiles directory lists only 453 entries; Frankfurt, Rotterdam, Panama, Miami, Dubai
  all 404'd and were silently read as "no settlements". Range reads fetch a few hundred kB per 3.5 km box.
  Frankfurt after the fix: 245 099 cells, p90 33.1 m, max 226.7 m, 21 s cold / 1.4 s cached.
- **Rejected:** opening it with GDAL `/vsicurl/` — the Windows GDAL build's schannel fails the DLR
  chain's revocation check and every CA/verify env var failed; disabling certificate verification
  was deliberately not done. Reads use `tifffile` + `imagecodecs` over a small range adapter.
- **Note:** changes no current output where fine providers exist — `resolution_priority(90)` = 0.236,
  below GBA's 0.55, so a 90 m product never outranks a 3 m one.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 entry (landed 2026-09-05).

### 2026-09-04 — An OSM height is a floor, not an estimate
- **Decision:** treat tagged OSM heights as a lower bound when comparing sources.
- **Why:** in Old San Juan, buildings with a height or storey tag claim 3.48 m less than the lidar
  survey at the median (MAE 4.99 m), with a consistent sign. Part is the per-storey constant, part
  roof structure the survey sees.
- **Note:** the survey was measured when levels × 3.0 m was the rule; F-ARCH made it 3.2 m per level
  plus one roof level (`city2stl/heights.py::height_from_tags`,
  [F-ARCH](../plans/active/F-ARCH-consolidation.md)), which closes part of the gap. The number was
  not re-measured.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 "OSM building heights in Old San Juan run about three and a half metres short".

### 2026-09-04 — A two-stage high-rise router works only fitted per city, and is not landed
- **Decision:** do not ship a low/high-rise classifier that routes tall footprints to
  `max(fine, wsf3d)`. If revisited, fit it per city on that city's own OSM tags.
- **Why:**
  - Oracle bound on Cartagena: MAE 6.43 → 4.62 (tall 24.83 → 14.87); break-even at ~80 % specificity / 60 % sensitivity.
  - Rebuilt on height-independent features (footprint geometry, neighbourhood, satellite
    appearance), cross-validated in Cartagena: 5.15 at sens 0.70 / spec 0.96 — 71 % of the oracle gain.
  - Does not transfer: Miami-trained model gives 6.51 in Cartagena. Miami leans on `log_area`
    (0.564), Cartagena on `halo_dark_p90` (0.608).
  - Gain is 1.01 ± 0.89 m with 60 labels (110 tagged, 20 tall) — one standard deviation. Needs a
    second city with both WSF3D and OSM tags to validate.
- **Rejected:** using Overture/GBA samples (or their presence flags) as features — AUC 0.914 but
  zero gain; routing gain correlated −0.415 with the model's confidence. A model that corrects an
  estimator must never see that estimator's output, since coverage is itself a proxy for "the
  estimator works here".
- **Lesson:** shadow presence is useless as a regressor (MAE 265.84, corr −0.347 at 59° sun) yet the
  best single classifier feature: detection survives conditions that destroy measurement.
- **Supersedes / superseded by:** the router approach is superseded by the [high-rise mask](plate-critic.md#2026-09-04--the-high-rise-mask-is-a-real-signal-with-no-downstream-consumer); the "do not ship the router" decision stands.
- **Source:** decisions.md 2026-09-04 entry (scripts were scratch highrise_*.py, not kept).

### 2026-09-04 — The merge stays per-pixel; Cartagena's tall bias is missing data, not policy
- **Decision:** keep `city2stl/height/__init__.py::merge_height_rasters` per-pixel with priority =
  confidence × `resolution_priority`.
- **Why:** Miami (1492 tagged footprints vs Cartagena's 110) settles it: the same merge reads Miami's
  tall band at +5.18 m bias, MAE 10.36, and wins overall (2.62 vs footprint-max 2.79). Overture covers
  23.9 % of Miami up to 265 m; in Cartagena 1.6 % and GBA tops out at 87 m. Where a
  building-resolution product has data the merge is right; where not, nothing recovers the towers
  without inflating the low city.
- **Rejected (all measured in Cartagena):**
  - lifting `BUILDING_RESOLUTION_LIMIT_M` (10 m) to admit WSF3D — no measurable change;
  - provider reordering — a 90 m source can never outrank a 3 m one, identical numbers;
  - per-footprint priority selection — worse in both bands;
  - WSF3D only on large footprints (area gate) — smooth trade, never a win (1600 m² gate: tall
    24.83 → 17.85, short 2.35 → 4.79, all 6.43 → 7.16); old-town blocks are large and short.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 entry.

### 2026-09-04 — The IQR outlier fence was removed from the height merge
- **Decision:** `city2stl/height/__init__.py::_filter_outliers` keeps only the below-ground and >600 m
  checks; the `iqr_scale` parameter was deleted (not defaulted off) so no caller can reintroduce it.
- **Why:** quartiles over all pixels track the ground (most pixels ≈ 0 m), so Q3 + 3·IQR cut off
  the skyline. Unfiltered: Miami MAE 45.61 → 35.48, corr +0.306 → +0.662, tallest 147 → 233 m (tag
  max 228); Cartagena MAE 34.68 → 20.09, corr +0.345 → +0.773, merged max 36.5 → 190 m.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 entry.

### 2026-08-30 — GlobalBuildingAtlas admitted at confidence 0.55, below Overture
- **Decision:** GBA.LoD1 is a registered provider (`city2stl/height/providers/gba.py::GBAProvider`),
  read from the Source Cooperative GeoParquet mirror, ranked below Overture (0.60).
- **Why:** it is the only globally complete per-footprint source. Against Miami tags Overture scores
  MAE 5.86 / corr +0.912, GBA 25.90 / +0.489 and under-predicts by 67.4 m above 50 m — but pixels
  above 50 m are 18 % of Miami, 0.31 % of Cartagena, 0 % of Tunis, where GBA is actually needed.
  The parquet has per-row-group bbox stats (pyarrow pushdown); it is WGS84 despite the upstream README
  claiming EPSG:3857.
- **Rejected:** upstream HuggingFace GeoJSON / mediaTUM zips (5° tiles of 0.7-2 GB, no index);
  scaling confidence by the `var` column or correcting the tall bias — both would be fitted on one
  city, the same mistake as the per-city `roof:height` calibration.
- **Rule:** score per-footprint sources against the plate's `osm_heights` (same grid, zero
  registration error), never the warped plate. The warped plate reversed this ranking (GBA 22.0 vs
  Overture 39.8, both corr ≈ 0.1); the symptom was the two providers agreeing with each other
  (+0.519) far better than with the reference.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 entry.

### 2026-08-30 — Height provider priority accounts for resolution, and sources coarser than 10 m are refused
- **Decision:**
  - merge ranks on `confidence × min(1, (5 / resolution_m) ** 0.5)` (`city2stl/height/__init__.py::resolution_priority`);
  - `city2stl/height/service.py::enhance_city_data` refuses providers coarser than
    `BUILDING_RESOLUTION_LIMIT_M` (10 m), so a city with only coarse sources keeps the 10 m fallback;
  - the emitted confidence stays unscaled — resolution decides who wins a pixel, not whether it is
    usable, so downstream `min_confidence` gates keep their tuned meaning.
- **Why:** a 30 m nDSM (0.80) outranked 5 m Open Buildings (0.60); it averages roofs with streets
  (median 2.0-2.4 m), which `city2stl/heights.py::enhance_buildings_with_raster`'s 3 m clamp turned
  into a field of 3 m boxes. Plain 10 m fallback beat it in 7 of 7 plate cities. Re-ranking alone was a
  near no-op (nDSM covers 100 % of every plate, Overture 20-61 %); the 10 m gate moved the error.
  Evidence tables: bug 3 in [issues.md](../issues.md).
- **Rejected:** a global shrinkage toward the constant — best k spans 0.00-0.90 across seven cities,
  fitted on plates already documented as untrustworthy referees ([plate-critic](../research/plate-critic.md)).
- **Principle:** for per-building heights, resolution matters and source reputation is no substitute.
  Median absolute error cannot referee "is this estimator informative" — at low correlation a constant
  beats an unbiased noisy estimate by construction; use correlation and best-k.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 entry.

### 2026-08-30 — Height coverage is measured from height_source, weighted by built area
- **Decision:** coverage metric = share of built area whose `height_source` is real OSM (`osm_tag`,
  `osm_levels`) or our estimate (`merged`) vs the 10 m constant (`default`); set in
  `city2stl/heights.py::_fill_heights`.
- **Why:** needs no ground truth, so works for every city, not just the eight with plates. Weight by
  area: a thousand tagged sheds and one untagged tower are not 99.9 % coverage.
- **Rejected:** measuring coverage against a plate.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 entry.

### 2026-08-28 — Shadow heights stay a research track; the old shadow provider stays deprecated
- **Decision:** do not promote satellite shadow heights to a production provider;
  `city2stl/height/providers/shadow_height.py` stays deprecated.
- **Why (Miami, ESRI 2025-01-08 scene, 41° sun):**
  - accepted estimates: n=17 of 122 footprints, MAE 17.48 m, corr +0.792 vs the plate — coverage is the
    real limit;
  - ray-cast ablation: the length operator is near exact (1.82 m on isolated shadows); shadow merging
    downtown costs ~30 m, imperfect segmentation about as much again;
  - a U-Net segmenter only helps if its receptive field spans the shadow reach
    (`max_height / tan(elevation)`); 2 m/px was the working point; segmentation IoU does not predict
    height error (shown three times);
  - cross-city transfer failed on the class prior; sun elevation has to be an input.
- **Findings worth keeping:** ESRI's `identify` endpoint returns the scene date (Google Static does
  not); sun elevation is solved from the shadow bearing + date; score plates with the `register`
  alignment, not `truth` (the latter correlated −0.017 with OSM tags). Old provider had five disqualifying
  defects (fixed June/10:00 sun, azimuth unused, no footprint association, long shadows rejected,
  heights clamped to 1-50 m).
- **Rejected:** greedy tallest-first occlusion claiming (MAE 62.5 → 75.5); quantile profiles;
  first-local-edge length; fitting acceptance thresholds to OSM tags (fits reference noise — plate and
  tags disagree by 26 m).
- **Supersedes / superseded by:** see the Cartagena failure under [Rejected hypotheses](#2026-09-04--satellite-shadow-heights-work-in-cartagena).
- **Source:** claude/memory-bank/shadow_heights.md → [../research/shadow-heights.md](../research/shadow-heights.md).

### 2026-08-28 — Google 3D Tiles fetch is level-order, depth tied to the output cell, faces sampled, tiles binned one at a time
- **Decision:** in `city2stl/height/providers/google_3d.py`:
  - walk the tile tree level-order, fetching each level's routing documents together;
  - target geometric error = 6 × output cell (`_target_error_m`, `_ERROR_PER_CELL`);
  - sample each face in proportion to its area (`_surface_points`), keeping vertices for exact ridges;
  - download in bounded chunks and fold each mesh into one accumulator (`_accumulate_mesh`).
- **Why:**
  - depth-first: 1505 requests / 131 s for a 1500-tile budget; level-order 31 s, and an exhausted
    budget leaves a coarser but complete surface instead of one detailed corner;
  - finer targets are worse under a fixed budget (Miami: 32 m target → 99 % fill in 28 s, MAE 14.01;
    8 m → 82 % in 240 s, MAE 14.72); 6× sits at the coarse end of the flat range (64/32/16 m →
    13.79/14.01/14.02) so the choice does not rest on noise;
  - vertex-only binning filled 19 % of the grid at coarse levels; face sampling 99 % on the same fetch;
  - holding every mesh until the end made a 4000-tile run die in trimesh's cache hashing.
- **Rejected:** the original fixed 4 m target (from the tiles' own resolution, not the raster's).
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 subsections inside "Vegetation, not bridges" (misfiled there).

### 2026-08-28 — Outside Google's photorealistic coverage the provider returns empty, not flat
- **Decision:** `city2stl/height/providers/google_3d.py::_looks_built` measures relief in block-sized
  windows (250 m); if under 10 % of windows exceed 8 m, return an empty result with a warning.
- **Why:** outside covered cities the same endpoint serves a global base mesh that looks like a
  successful fetch — over Cartagena a surface flat to 1 m for a skyline of 40-storey towers. A wrong
  height that looks like a measurement is worse than a missing one. Measured relief: ≥35 m in every
  covered city, ≤4.3 m in every uncovered one.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 subsection "An empty raster is preferred to a flat one".

### 2026-08-28 — Provider accuracy is scored with the tag filter disabled and split by tagged/untagged
- **Decision:** score every height source on the `nofilter` region report and split every claim by
  whether OSM tags the building.
- **Why:** the skyline tag-disagreement filter makes accuracy against tagged buildings partly
  self-fulfilling. This test established Overture as independent of OSM: better on untagged
  buildings than tagged, and matching no OSM tag exactly.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 subsection.

### 2026-08-28 — Google 3D Tiles ground comes from the mesh, not from a DEM
- **Decision:** ground = a low percentile (5th) of mesh altitudes in a ~200 m neighbourhood
  (`city2stl/height/providers/google_3d.py::_ground_from_dsm`); a caller may pass a DEM to override.
- **Why:** tiles are WGS84 ellipsoidal; project DEMs are orthometric; the geoid separation is tens of
  metres and city-specific (≈ −26 m at Miami), so DEM subtraction adds an uncalibratable bias. Mesh
  ground cancels the datum.
- **Limit:** assumes terrain varies less across the neighbourhood than buildings rise; fails in hills,
  where DEM + geoid model is the honest answer.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 subsection.

### 2026-08-28 — An unmeasured building ground means the camera's ground, not sea level
- **Decision:** skyline heights default a building's unknown ground to the camera's ground plane,
  flagged by `city2stl/skyline/_core/types.py::BuildingRecord` `terrain_known`; elevation lookups
  return `None` on failure, not 0.0.
- **Why:** 0.0 is silently wrong (adds the camera's elevation to every height; plausible at the
  coast, gated away inland). Same-ground cancels the absolute term; error is bounded by local slope.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 subsection.

### 2026-08-05 — STL and OSM height units are deliberately different; do not "fix" them
- **Decision:** STL heightmaps keep raw mesh Z units, OSM heightmaps metres;
  `city2stl/osm_raster.py::derive_scale_m_per_unit` is not applied to the STL heightmap.
  `numpy2stl/src/numpy2stl/registration/compare.py::compare` reconciles them by robust regression
  (`stl_m = stl * height_scale + offset`), recorded as `ComparisonResult.height_scale_used`.
- **Why:** Barcelona's 3.83 vs 100.0 max is expected; the fitted scale should land near 26 m/unit. A
  value near 1.0 means too few buildings matched — a registration symptom, not a units bug.
  Segmentation never compares the two heights and its threshold is Z-scale invariant.
- **Measured:** Barcelona coverage OSM 0.642 vs STL 0.754 — over-segmentation ≈ 0.11, replacing the
  earlier "83.3 % of the raster" figure.
- **Status 2026-09-28:** still holds after F-ARCH (numpy2stl is geo-free; registration takes rasters).
  The F-ARCH 3.2 m/level change affects OSM tag heights only, not this units split.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-05 entry.

## Rejected hypotheses

### 2026-10-06 — Relaxing the trust gates to get more coverage
- **Hypothesis:** loosening the confidence and distance gates of `_pano/elevated.py::trusted` adds usable readings.
- **Measured:** untrusted readings are wrong: median error 200-400 % against tags.
- **Verdict:** refused; coverage must come from more images (more seeds, the satellite cross-check T44), not looser gates.

### 2026-10-06 — A shared-top-edge occlusion check
- **Hypothesis:** a trusted reading whose top row matches a farther footprint's top row is an occluder and wrong.
- **Measured:** caught none of the 5 wrong trusted readings and flagged 25 good ones.
- **Verdict:** refused.

### 2026-10-06 — The "behind" flag for occluded tops
- **Hypothesis:** flag a tower top that lies behind a nearer surface.
- **Measured:** over-flagged (good readings marked).
- **Verdict:** removed.

### 2026-10-06 — Street-fit camera cost from SegFormer road labels
- **Hypothesis:** fit the drone camera to the street network using SegFormer road pixels.
- **Measured:** the cost surface is flat; the road mask is too patchy.
- **Verdict:** refused; the overhead roof fit (`_pano/elevated.py::overhead_pose`) is used instead.

### 2026-10-06 — Placing no-GPS Commons photos by outline search, SIFT, building_groups voting or photo_columns
- **Hypothesis:** a Commons photo with no GPS can be placed by searching its outline, by SIFT matches, by `building_groups` voting, or by `city2stl/skyline/photo_columns.py::photo_columns`.
- **Measured:** the oracle (best possible choice) got only 4/7 headings and 1/17 rank.
- **Verdict:** refused.

### 2026-09-04 — Satellite shadow heights work in Cartagena
- **Hypothesis:** the shadow pipeline fails in Cartagena because of a wrong shadow bearing.
- **Measured:** bearing swept 0-345° in 15° steps over the 400 largest footprints: `near_level` p50
  0.037-0.060 against a 0.17 gate at every bearing, `length_m` p50 168-254 m, 0-10 of ~330 accepted.
  No bearing shows short runs with high near_level.
- **Verdict:** refused; the cause is physical. The ESRI scene was taken at 59° sun elevation
  (10.4° N, 2026-02-10, ~11:00), so shadows are ~0.6 × height in a dark, wet, vegetated scene; the
  other acquisitions are no better. Do not use shadow heights as a reference at low latitude
  without checking the acquisition's sun elevation first.
- **Source:** decisions.md 2026-09-04 entry.

### 2026-08-30 — Granada's export is flat because it lacks height data
- **Hypothesis:** Granada exports flat because OSM has little height data there.
- **Measured:** OSM carries `building:levels` on 4272 of 4476 buildings in the window (95.4 %,
  median 4); only 0.5 % carry `height`. Overture covers 55.6 % of pixels, GBA 31.8 %. The shipped
  3MF had 1026 distinct building heights (1.50-32.50 model units), every level count 1-8 at its own
  height, nothing floored to the 10 m default.
- **Cleared on the way:** the empty 3.5-7.1 m band in the vertex histogram belongs to the terrain
  object (base underside to lowest terrain), not the buildings; the 3.9 m/level in the old
  buildings module sat in dead code (deleted 2026-09-01), so 3.2 m/level is the only rule.
- **Verdict:** refused. Whatever is wrong with a Granada export, missing heights are not it.
- **Also measured:** GBA does not help Granada. It adds 8276 pixels Overture lacks against 70 689
  the other way, and reads 7.5 m lower on the median (corr +0.646), the same downward bias as in
  Miami. nDSM's 100 % coverage there is ground at 30 m. GBA stays a Cartagena/Tunis source.
- **Source:** claude/memory-bank/activeContext.md, 2026-08-30 (moved 2026-09-28).
