# F-ROOFLIDAR — Roof shape from survey lidar

_Approved 2026-10-18 (user: "Lidar roof shape"). Status: planned, not started. Proposal:
[F-ROOFLIDAR](../../proposals.md)._

## Goal

Granada reads flat: 23,969 buildings, 19,263 flat, 4,604 gabled (19 % pitched), 0 hipped,
against the 79.7 % Iberian reference. The cause is
`city2stl/roof_classifier.py::classify_roof_shapes`: the GBM calls pitched only above 0.90
probability (tuned to a 5 % false-pitched rate, [roof-shape-model](../../reference/roof-shape-model.md)),
and every pitched roof is written `gabled`. Where a survey nDSM covers the box, measure the roof
instead: flat vs pitched, gabled vs hipped, and `roof:height` from the survey heights. Elsewhere
(Cartagena, no lidar) the classifier stays as it is.

## What exists today

- **Survey rasters are already fetched and cached during a city build.**
  `app/server/core/city_data.py::with_survey_heights` → `city2stl/height/survey_fill.py::fill_layer_heights`
  reads one raster per source (`FILL_SOURCES`: `cnig_mdsn`, fallback `rediam_mdhn`) through
  `city2stl/height/providers/survey.py::ndsm_for_bbox`, cached per (provider, bbox, resolution) by
  `providers/_survey.py::cached_ndsm`. `with_roof_shapes` runs after it, on read.
- **Per-footprint samples exist only as a scalar.** `survey_fill.py::footprint_stats` rasterises
  the footprints into a label grid and returns (p90, coverage), and only for `fillable` buildings
  (fallback heights). The benchmark's `city2stl/skyline/benchmark.py::footprint_stats`
  (`ROOF_STATS`) stores more percentiles, but nothing reads a plane or a ridge. US 3DEP has its own
  per-footprint pass (`height/service.py::_enhance_from_lidar`) that also returns heights only.
- **Roof shape and height reach the solid through two tags.** `city2stl/city_model.py::_roof`
  reads `roof:shape` (default flat) and `roof:height` (else 0.3 × `height_m`, clamped to
  0.5 × height, flat below 0.2 mm) and `_roofed` hands them to `city2stl/roofs.py::building_solids`.
  Writing both tags is all this feature needs downstream.
- **The roof cache** (`city2stl/roof_cache.py::feature_key`) keys on sha1 of `VERSION`, the
  checkpoint hash (`model_id`), geometry and `TAG_KEYS` (`building`, `building:levels`, `height`).
  It knows nothing about a survey.

## The Granada blocker (resolve first)

Neither Granada provider we run can carry a roof:

- `rediam_mdhn` is a **vegetation** height model: the lidar building class is suppressed, so an
  ordinary house reads low or void ([survey-sources](../../reference/survey-sources.md)).
- `cnig_mdsn` (`mdsn_e025`) is **2.5 m, int16 whole metres**: a 9 × 10 m house is 4 × 4 cells and a
  2 m ridge rise is one quantisation step. That is the resolution regime that already sank the
  plates (AUC 0.53–0.60) and the Google mesh (1.52 m/px, AUC 0.488).

So Granada needs a new source before any of this can work there: REDIAM's 1 m **surface** (MDS)
minus its 1 m **bare earth** (MDT), both COGs on the same regional mosaic the `rediam_mdhn` reader
already windows, or CNIG's 0.5 m PNOA surface / point cloud via the undocumented
`centrodedescargas.cnig.es` pair. Step 1 below probes REDIAM MDS − MDT first (scriptable, same
reader); if it is not published as a COG, fall back to CNIG 0.5 m and tell the user before
scripting the download form.

## What is different from the refused attempts

[decisions/roofs-landmarks.md](../../decisions/roofs-landmarks.md) refused roof shape from the
plates, the source STLs and the Google mesh, and refused passing the nDSM to the old cascade. This
plan differs on each count, and is gated on a measurement before it ships:

- **Airborne lidar, not photogrammetry or a warped plate.** The Google mesh failed per building
  partly through edge smear (its width-binned pitch rounded the lip over a fixed distance); a lidar
  nDSM has a sharp wall.
- **≥ 1 m cells only, ≥ N cells per footprint.** Below that the plan falls back, it does not guess.
- **Refereed against OSM `roof:shape` tags**, not against the classifier (the mesh was scored
  against the classifier and had nine tags in its box).
- **A plane-fit reader, not the cascade.** The cascade scored its majority rate with or without a
  DSM; the plane fit is what the Miami DSM test recommended (39 of 82 flat below 5°, where the
  p10–p90 spread called 76 of 102 pitched).

## Approach

1. **Source.** Add a buildings-bearing 1 m Granada nDSM (REDIAM MDS − MDT, above) as a survey
   provider; use IGN LiDAR HD (0.5 m), ČÚZK and US 3DEP as they are. CNIG 2.5 m never feeds roof
   shape. Reuse the cached raster and the `footprint_stats` label grid: one rasterisation per box,
   every untagged-roof building (not only `fillable` ones).
2. **Per-footprint features**, on the footprint eroded by one cell (drops wall-edge cells and
   absorbs a small offset): valid-cell count and coverage; height spread p95 − p10; least-squares
   plane fit (slope, residual as a share of spread); two-plane fit split along the principal axis
   (residual drop vs one plane = a ridge); edge-ring vs centre height (centre − edge > 0 for any
   pitched roof, ≈ 0 for flat, < 0 for a parapet with a flat deck).
3. **Flat vs pitched thresholds**, fitted on tagged footprints, not chosen: start from the Miami
   rule (plane slope < 5° or residual > 40 % of spread with a low centre − edge = flat with plant /
   parapet), then set slope and centre − edge cuts by grouped folds on the labelled set below.
4. **Gabled vs hipped** from the ridge: ridge length (the two-plane split line clipped to where both
   planes rise) over the footprint's long side; ≥ ~0.7 gabled with `roof:orientation` along the ridge,
   shorter hipped, a point (both axes symmetric, short ridge) pyramidal. Measured, not assumed:
   the classifier has no hip call to compare with.
5. **Roof height** = robust max (p95 of the eroded footprint) − eave (p10 of the edge ring), written
   as `roof:height` with `roof_source = "survey_<provider>"`. `city_model._roof`'s 0.5 × height
   clamp stays.
6. **Fallback and precedence.** Footprints under N cells (start N = 25 at 1 m, i.e. ~5 × 5 m),
   under 50 % coverage, or with no survey go to `classify_roof_shapes` unchanged. An OSM
   `roof:shape` is never overwritten. Runs inside `city_data.with_roof_shapes`, survey first, so
   Cartagena's output is byte-identical.

## Target files

- `city2stl/height/providers/rediam_mds.py` (new, or a `rediam_mdhn.py` sibling product) and
  `providers/survey.py::PROVIDERS`
- `city2stl/roof_survey.py` (new): features, thresholds, shape + height per footprint
- `city2stl/height/survey_fill.py::footprint_stats` (expose the label grid; no behaviour change)
- `app/server/core/city_data.py::with_roof_shapes` / `_classify_buildings` (survey pass first,
  stats `by_source`)
- `city2stl/roof_cache.py` (`VERSION` bump; survey provider + raster year in the key, or a separate
  table for survey rows)
- `tools/eval/eval_roof_survey.py` (new): grouped-fold scoring on the labelled sets
- Tests: `tests/test_roof_survey.py`, `tests/test_roof_shapes_server.py`
- Docs: `docs/reference/roof-shape-model.md` (survey section), `docs/INDEX.md`, decision entry in
  `docs/decisions/roofs-landmarks.md`

## Success criteria

Labelled sets (OSM `roof:shape` tags inside survey coverage, five folds grouped by window as in the
2026-08-29 research):

- **Granada:** the 957 tagged roofs (748 flat-or-pitched) from the research notebook; the GBM scored
  AUC 0.913 / balanced accuracy 0.783 on them. The survey reader must beat **balanced accuracy 0.783**
  on the same rows with false-pitched ≤ 5 %, or Granada stays on the classifier and the result is
  recorded as a refused hypothesis.
- **Prague (ČÚZK) and Paris (IGN 0.5 m):** tagged roofs (Prague 9.9 % tagged) as a second and third
  city, so the thresholds are not Granada-fitted.
- **San Juan (3DEP)** is the flat-side check, not a label set: its pack
  (`tools/align_tool/data/san_juan_puerto_rico/`) is a height answer key with no roof shapes.
  Called-pitched share must stay low (flat concrete stock), as Cartagena's 6.4 % does for the
  classifier.
- **Roof height:** on footprints carrying both `roof:height` and lidar, median |error| below the
  0.3 × height rule's 1.00 m (39-roof comparison) — read with care, `roof:height` is a landmark tag.
- Granada whole-box pitched share moves from 19 % toward the ~80 % reference; hipped > 0.
- `roof_shapes: "off"` and no-survey boxes byte-identical (existing test).

## Risks

- **Alignment offset** between OSM footprints and the survey (OSM is often 1–3 m off): one-cell
  erosion absorbs ~1 m; measure the offset per box by maximising footprint-vs-nDSM overlap before
  sampling, and refuse the survey pass where the best overlap is poor.
- **Trees over roofs:** ČÚZK DMP and any MDS − MDT include canopy. Mask cells that the vegetation
  product (`rediam_mdhn`, CNIG `mdsn_v025`, IGN classes) marks as trees; a high plane residual with
  a low centre − edge is clutter, not pitch.
- **Survey age vs OSM:** surface and bare earth flown years apart, and a demolished or rebuilt house,
  read as a wrong roof; keep the survey year on the row and skip footprints whose lidar height
  disagrees wildly with the OSM height.
- **Cache invalidation:** the roof cache key has no survey in it; a survey-measured row must not be
  served after the provider changes, nor a classifier row where a survey now exists. Bump
  `roof_cache.VERSION` and key survey rows on provider + year + resolution.
- **REDIAM licence** unconfirmed (survey-sources) and the 1 m MDS may not be published as a COG;
  then the Granada fix depends on CNIG 0.5 m through an undocumented form.
- **Terraces:** a party-wall row is one ridge across many footprints (research: union distance
  transform). Per-footprint ridge length may call short terraced houses hipped; score that case.

## Decisions

(Filled in as the work lands; lifted into `docs/decisions/roofs-landmarks.md` when done.)
