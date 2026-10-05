# Skyline results, 2026-10-05 — data for review

A compact export of the skyline height results so far: the street-level pipeline on eight benchmark
cities, Wikimedia Commons photos on Miami, and drone Photo Spheres on Cartagena. The run data live
in gitignored `city2stl/skyline/runs/`; these files are what a reviewer without them needs. Produced
from the runs named below; nothing here is hand-edited.

## Files

| File | What it is |
|---|---|
| `benchmark_8cities_2026-10-04.json` | `scripts/10_benchmark.py` summary for the eight cities: per city MAE, median AE, bias, within 15/25 %, height bands, per-view and per-seed counts, tagged vs untagged OSM buildings, relative order (pair order, Spearman). Street View pipeline, SegFormer b1, tag filter on. |
| `miami_photos_vs_streetview_2026-10-04.json` | The Miami Commons photo run (`scripts/17_photo_pipeline.py`): photos kept, gates, photo vs truth, OSM tags vs truth, the buildings both photos and Street View measured. |
| `miami_buildings.csv` | One row per Miami building measured by Street View or photos: OSM tag, Street View height (`effective_height_m`, views, seeds, seed disagreement), photo height (photos, spread), truth (status, truth, survey, 3D Tiles), centroid, area. |
| `miami_photos.csv` | One row per candidate Commons photo, kept or not: route (labels, recorded location, linked, search), gate, coverage, deviation of its towers from their OSM heights, solved camera (position, heading, field of view, sigma), towers, score. |
| `cartagena_drone_footprints.csv` | Drone seeds 4, 5 and 1 measured footprint first (F-DET6) at their fitted camera positions: per seed height, distance, base visible, visible share, what ended the run; fused height (`footprint_detect.fuse_heights`), the seeds it used, disputed. Cartagena has no survey truth; OSM tags are few and partly planned heights. |
| `cartagena_drone_cameras.json` | The camera fit per drone seed (`footprint_detect.fit_camera_position`): shift from the recorded position, heading, height, pitch, waterline misfit before and after, ground score, source. |

## Truth

- Per footprint: p95 of the nDSM inside the footprint shrunk by 1 m, from survey lidar and from Google
  3D Tiles. `confirmed` when they agree within max(3 m, 10 %); `disputed`, `survey_only`, `tiles_only`
  otherwise. Only `confirmed` counts in the headline scores.
- Miami's lidar covers only the coast; from 2026-10-05 a source needs half the footprint's cells
  (T6). The truth in these files predates that rule.

## Headlines (details in `city2stl/skyline/docs/STATUS.md` and the plans)

- **Street View pipeline, eight cities:** untagged buildings read 65–113 m too tall in every city; the
  OSM tag filter hides it on tagged ones. Relative order inside one view is near chance (Miami 44 %).
- **Miami photos** (101 candidates, 27 kept, 193 buildings): MAE 23.2 m on 142 confirmed buildings,
  pair order 0.856; OSM tags on the same buildings 8.9 m. On the 34 buildings both photos and Street
  View measured: photos 13.0 m, Street View 71.8 m, OSM tags 5.9 m. Photos measure only towers with
  OSM heights so far (one photo cannot tell which building forms the skyline): task T16.
- **Cartagena drone seeds** (no truth): footprint-first measurement from the waterline-fitted camera.
  seed_4's recorded position was ~360 m off (waterline misfit 0.62° → 0.16°). The seeds agree within
  25 % on 25–35 % of the footprints they share; most disagreements are the farther seed reading the
  tower behind.

## What the review should produce (task T20)

A ranked improvement plan in `docs/plans/proposals/`: for each proposal, the evidence from these
files (with the numbers), the expected gain, the cost, how to verify it on the benchmark, and the
risk. Cover the street-level pipeline, the photo pipeline and the drone path. Say which proposals
need this PC (GPU, caches, Street View or 3D Tiles keys) and which the cloud can build and test.
