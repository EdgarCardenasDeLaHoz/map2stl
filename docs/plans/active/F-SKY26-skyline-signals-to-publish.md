# F-SKY26 — Skyline research signals into fusion and publishing

**Status**: in progress (2026-10-07)
**Proposal**: [../../proposals.md](../../proposals.md) → Skyline CV → F-SKY26
**Owner module**: `city2stl/skyline/`. The functions involved:
- `floor_bands.py`
- `footprint_detect.py::fuse_heights`
- `_pano/elevated.py::elevated_estimates`
- `_core/height.py::withhold_untagged_street_view`

## Goal
Floor counts, MobileSAM instances and satellite heights appear only on review images. Make them
change the published heights:
- dispute wrong drone readings;
- flag high-rise plots for the fallback;
- fix the tower-behind assignment;
- add satellite readings without letting them outweigh drone readings.

## How drone heights reach publishing today
1. `region_pdf.py::run_region_pdf_report` loads `elevated_seeds`.
2. `_pano/orchestrator.py::_seed_multiview_registration` calls `measure_elevated_seed` once per
   drone seed.
3. `_pano/elevated.py::elevated_estimates` takes the trusted readings, runs `fuse_heights`, and
   drops disputed footprints. For each seed in `used` it emits that seed's raw reading; the fused
   value itself is not published.
4. `_core/height.py::aggregate_building_heights` produces the rows.
5. `withhold_untagged_street_view`:
   - tagged building: the tag;
   - untagged building with a drone reading: the median of the drone readings;
   - otherwise: the T41 prior.
6. The result is written to `heights.json`.

Two constraints follow:
- `elevated_estimates` looks up `by_key[(seed, fid)]` for every key in `used`, so any non-drone
  key needs its own estimate builder.
- Satellite readings in the scratch scorer use `dist_m=100`, so their weight is conf/1e4. That is
  25x a drone reading at 500 m, so a satellite reading always wins.

## Approach, in order (smallest useful step first)
0. **Harness.** Make `Code/claude/scripts/seed_experiment.py` region-aware:
   - `--region` (Miami LiDAR truth from `runs/benchmark/truth/<region>.json`);
   - `--refresh-truth`, `--floors`, `--sat`;
   - new metrics: per-kind σ_log; caught versus false disputes; high-rise precision and recall;
     coverage with independent ≥ 2.
1. **Floors factor-2 check (no new published values).**
   - `floor_bands.calibrate_storey`: a per-city storey height from tag / floors on OSM-tagged
     plots. It does not depend on the camera pose.
   - `footprint_detect.floor_check`: disputes a drone reading that is ≥ 2x off the floors height.
     When the base is hidden, the floors height is a lower bound only.
2. **Shared fusion plumbing.**
   - New reading fields: `kind`, `lower_bound`, `image`.
   - `fuse_heights` anchors on the heaviest reading that is not a lower bound. A lower bound below
     the kept value is ignored. Output gains `independent`.
   - `elevated_estimates` emits only publishable kinds.
3. **Satellite weight.** Calibrate `SAT_REF_DIST_M`: the drone distance at which trusted drone
   readings have σ_log 0.15, the reference in the `weight_scale` formula of decision 2026-10-07.
4. **High-rise flag.**
   - Plots with ≥ 10 floors seen get `high_rise_seen` in `heights.json`.
   - Untagged ones publish max(prior, floors × storey + 3 m).
5. **Floors as weighted readings.** Use the σ table. `FLOORS_PUBLISH_ALONE` is decided by the score.
6. **Floor distance re-credits the tower behind.** A run that climbed onto a farther plot is
   untrusted and re-credited to that plot. This also needs an instance change.
7. **Satellite package.**
   - New `city2stl/height/satellite/`: `scene.py`, `readings.py`, `weights.py`.
   - Offline script `scripts/19_satellite_heights.py`.
   - Opt-in site flag `use_satellite_heights`.
   - Not `height/providers/shadow_height.py`, which is deprecated (2026-08-28).
8. **Camera pose check from floor distance.** Floor-implied distance does not depend on the
   camera's position, heading or height. Log it first, then use it as a gate or tie-break.
9. **Commons photo signature** (floors and widths). Only after an oracle test ranks the true
   camera in the top 3.

## Target files
- Skyline: `city2stl/skyline/floor_bands.py`, `footprint_detect.py`, `_pano/elevated.py`,
  `_pano/stage_cache.py`, `_pano/orchestrator.py`, `_core/height.py`, `region_pdf.py`.
- New: `city2stl/height/satellite/`, `scripts/19_satellite_heights.py`.
- Tests: `tests/test_floor_bands.py`, `tests/test_skyline_footprint_detect.py`,
  `tests/test_skyline_elevated.py`.
- Harness: `Code/claude/scripts/seed_experiment.py`.

## Success criteria
- **Floors check:**
  - catches ≥ half of the trusted drone readings that are ≥ 25 % off LiDAR or tags (Miami seeds
    2/3/4; Cartagena seeds 1/4/5/6/7);
  - false disputes ≤ 5 % of correct readings;
  - no published Cartagena tower lost.
- **High-rise flag:**
  - precision ≥ 0.9 on untagged confirmed footprints ≥ 30 m;
  - withheld MAE on untagged high-rises goes down on `10_benchmark --score-only`.
- **Satellite:**
  - fused within-25 % on LiDAR is no lower than drones alone;
  - no "sat-sat verified".
- Each step lands with its seed_experiment diff recorded in `docs/decisions/building-heights.md`.

## Risks
- Storey height varies from 3 to 4.8 m (Cartagena luxury towers run 4.3-4.8 m). Calibrate per
  band if σ demands it.
- Floors need ≥ 5 px per floor, about 270 m at low-res and 770 m at hi-res, so coverage is
  near-field only.
- Miami labels are thin until truth is refreshed. Miami has no `elevated_seeds` yet; add the drone
  spheres first.
- Earlier occlusion checks (shared top edge, the "behind" flag) failed, so step 6 must be scored on
  the same known cases first.
- Shadow errors are correlated and one-sided. Commons placement was refused once (2026-10-06).

## Progress
- 2026-10-07: plan written from a design review. Floor counts fixed first: storey height from the
  footprint range, one plot per instance (`611bbf9`).
- 2026-10-07, step 0 done: `seed_experiment.py` region-aware (`--region`, `--floors`, `--sat`,
  caught/false-dispute, high-rise P/R, independent coverage); Cartagena metrics identical to the old
  harness. **Step 1 dry run fails:** the floors factor-2 check caught 0 of 50 wrong trusted Miami
  drone readings (only 5-6 of 91 have a floors reading on the same plot) and falsely disputed 2
  correct towers (One Biscayne, SE Financial: floors 46 / 83 m on partly hidden facades counted as
  full); Cartagena has 1-3 wrong readings per set, too few to judge. Not wired. Needs a `base_seen`
  field in `InstanceFloors` and floors coverage on matched plots first.
  - Storey height: Cartagena 4.32 m (LOO sigma_log 0.11, n=5); Miami 3.08 m but sigma_log 1.08:
    Miami floor counts are unreliable.
  - High-rise flag: Miami precision 0.92 (n=12), recall 0.11; Cartagena precision 0.5-0.73.
  - Satellite with today's weight lowers fused within-25 % (0.73-0.89 -> 0.61-0.67) and stereo
    agreements still count as "sat-sat verified" (57-70): step 3 comes next.
  - Miami trusted drone readings: sigma_log 0.74, bias +0.46, 45 % within 25 % (seed_3 sphere worst,
    seed_4 spin best at 86 %): the roof-fill readings are the main error source.
