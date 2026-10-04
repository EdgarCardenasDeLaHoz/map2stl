# F-SKYBENCH — Skyline height benchmark on surveyed truth

**Status**: planned (user-requested 2026-10-03)
**Owner module**: `city2stl/skyline/` (reads `city2stl/height/providers/`, writes nothing back)

## Goal

One command that scores skyline (street-view) building heights against surveyed truth on
eight cities, so every later change (the tall-tower ceiling, roof-to-building assignment,
segmentation size, metric depth) is judged on hundreds of buildings, not ~20 OSM tags.

Why now (measured 2026-10-03 on the 2026-08-28 reports, against OSM `building:levels` × 3.4 m):

| | Cartagena | Miami |
|---|---|---|
| Truth buildings | 22 | 43 |
| MAE / median AE | 18.8 / 10.4 m | 57.0 / 39.8 m |
| Within 25 % | 18 % | 23 % |
| Towers > 100 m, bias | −23 m | −59 m |
| Low and mid-rise, bias | −11 m | +29 to +42 m |

- Confidence does not predict error (corr 0.02 / 0.16); 2+ views score worse than 1 view on
  Cartagena (24.9 vs 15.3 m); buildings seen from 2+ seeds disagree by a median 61 m.
- Past decisions (b3 default, F-SKY3 removal, F-SKY2/7 tuning) were measured on Cartagena,
  whose height truth is weak (user, 2026-10-03; no open survey exists, see
  [survey-sources.md](../../reference/survey-sources.md#cartagena-has-no-answer)).

## Benchmark set (user choice, 2026-10-03)

| City | Survey truth | Skyline state |
|---|---|---|
| Miami | USGS 3DEP (`survey.py` `usgs_3dep`) | site + reports |
| Chicago | USGS 3DEP | site + reports |
| Seattle | USGS 3DEP | site + reports |
| Boston | USGS 3DEP | site + reports |
| Benidorm | Spain CNIG (`cnig_mdsn`) | site + report (auto seeds only) |
| Paris La Défense | France IGN LiDAR HD (`ign_lidarhd`) | **new site file** |
| Madrid Cuatro Torres | Spain CNIG | **new site file** |
| Prague Pankrác | Czechia ČÚZK (`cuzk_dmp`) | **new site file** |

Every city also gets Google Photorealistic 3D Tiles (`Google3DProvider`). Truth is
**both, cross-checked** (user choice).

Cartagena stays a heading and registration smoke test, not an accuracy benchmark.

## Approach

1. **Truth per footprint** (`city2stl/skyline/benchmark.py::footprint_truth`)
   - Read the footprint polygons a run actually scored (`heights.json` → `footprint_lonlat`),
     plus every OSM footprint in the bbox, for coverage.
   - Survey: `survey.ndsm_for_bbox(name, bbox)` (US: `lidar_3dep_copc.footprint_heights`);
     3D Tiles: `Google3DProvider.fetch_heights`. Both are height above ground already.
   - Per footprint: p95 of the nDSM inside an inward-buffered polygon (drops edge bleed), with
     the cell count.
   - Cross-check: `confirmed` when the two agree within max(3 m, 10 %); otherwise `disputed`
     with both values (new construction, demolition, vegetation, ground estimate). The headline
     uses confirmed buildings only; disputed counts are reported.
   - Cache per region at `runs/benchmark/<region>_truth.json` with the source vintages.
2. **Scorer** (`benchmark.py::score_region`)
   - Join on footprint geometry (`feature_id` is not stable across OSM fetches).
   - MAE, median AE, bias, % within 15 % / 25 %, by height band (< 30, 30–60, 60–100, > 100 m),
     by number of views and of seeds; coverage = scored / confirmed truth in the bbox.
   - Also score against OSM tags, to show how far the old yardstick was off.
   - Reuse `registration/critic.py::score_heights` conventions where they fit (per-building
     median absolute error as the headline).
3. **Stable runs**
   - Persist auto-proposed seed positions per region (open item in the skyline README), so
     two runs see the same panoramas and score differences come from the code.
   - Pin `SKYLINE_CV_*` flags in the benchmark command; record them and the git hash in the
     result.
4. **New sites**: La Défense, Madrid Cuatro Torres, Prague Pankrác: bbox + seeds via
   `scripts/discover_city_seeds.py`, `max_plausible_height_m` from the tallest tower.
5. **Command**: `python -m city2stl.skyline.scripts.10_benchmark [--regions …] [--score-only]`
   writes `runs/benchmark/<date>/summary.json` + one table; `--score-only` re-scores existing
   reports without Street View calls.
6. **Baseline**: run all eight with today's code, record the table in `docs/STATUS.md`
   (replacing the Cartagena-only figures as the headline).

## Target files

- New: `city2stl/skyline/benchmark.py`, `city2stl/skyline/scripts/10_benchmark.py`,
  `sites/la_defense.json`, `sites/madrid_cuatro_torres.json`, `sites/prague_pankrac.json`,
  `tests/test_skyline_benchmark.py` (synthetic nDSM + footprints, no network).
- Edited: `seed_selection.py` (persisted proposals), `docs/STATUS.md`, skyline `README.md`
  (benchmark section, open items), `docs/INDEX.md`.

## Success criteria

- ≥ 150 confirmed truth buildings in each city except Madrid (a deliberate tall-tower case).
- Survey and 3D Tiles agree on ≥ 80 % of footprints in each city; the rest are listed.
- `--score-only` on cached reports runs in < 1 min and is deterministic.
- Two back-to-back full runs of one city give the same seeds and MAE within 1 m.
- Baseline table for all eight cities in STATUS.md.

## Risks

- **Survey vintage vs Street View date**: towers built after the lidar flight. The
  cross-check flags them; 3D Tiles is usually newer.
- **Miami 3DEP project** (`FL_TopobathyFLKeysNOAA…`) may not cover downtown: check
  `survey.available_for_bbox` first; fall back to another FL project or 3D Tiles-only with a
  note.
- **La Défense deck**: towers stand on a raised slab, so "ground" differs between the street
  and the deck. Report heights above the survey's bare earth and flag the deck footprints.
- **Quota and cost**: 3D Tiles and Street View calls for eight cities; the truth is cached, so
  this is a one-time cost per city. Street View is ~$0.10 per region run.
- **Small buildings**: few nDSM cells and hidden behind others from the street; the coverage
  figure is per band so this doesn't hide in the average.

## Progress

- 2026-10-03: plan written; set and truth sources chosen by the user.
- 2026-10-03/04 (branch `f-skybench`, worktree `~/worktrees/map2stl-skybench`):
  - Steps 1, 2, 3, 5 done: `benchmark.py`, `scripts/10_benchmark.py`, persisted auto-proposals
    (`seed_selection.py::_persisted_proposals`); new sites La Défense, Madrid, Prague.
  - Deviation: US truth reads USGS's EPT octree with laspy (`lidar_3dep_ept_laspy.py`, user
    choice): Planetary Computer's COPC has no tiles over central Miami or Seattle.
  - Deviation: no "coverage over all OSM buildings in the bbox"; truth is measured only on
    scored footprints (a 10 km bbox of 3D Tiles is hours per city).
  - Baseline (step 6) for six cities is in `city2stl/skyline/docs/STATUS.md`. Benidorm and Madrid
    failed on an Overpass outage; skyline now needs only the buildings layer (`7ff7ba5`).
  - Finding: untagged buildings read 65–113 m too tall in every city; the tag filter hides it on
    tagged ones (scorer now reports both groups).
  - Success criteria: ≥ 150 confirmed in Miami (192), Chicago (265), Seattle (156), Boston (587);
    not La Défense (87, small run) or Prague (23, 3D Tiles artefact). Agreement ≥ 80 % fails in
    Seattle (46 %) and Prague (30 %), see STATUS.
