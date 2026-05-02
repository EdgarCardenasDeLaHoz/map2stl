# Plan: Filling Cartagena (and Other Sparse-OSM Regions) — Data Source Strategies

_Created: 2026-04-29._
_Status: investigation; concrete recommendation at end._

## What the audit found (2026-04-29 session)

The 514-tile training dataset was 100% European cities (Amsterdam, Berlin,
Paris, Vienna, Prague, Rotterdam, Cologne, Bruges, Florence, Munich,
Barcelona). After the first 25-epoch RoofNetV3 training run we saw:

- best `val_mae` 4.49–4.84 m on building pixels
- `val_mask_iou` 0.34–0.40 (struggling to converge on tall structures)
- `pearson(MAE, target_mean_height) = +0.91` — height head systematically
  hedges toward a constant ~8 m output

The `+0.91` correlation says the model can't extrapolate past the
training distribution. The training distribution had no high-rise. So
either (a) we add a tall-building region, or (b) we accept the model
won't generalise outside European mid-rise.

### Cartagena attempt

Added Cartagena (Colombia) to `TRAIN_CITIES` and ran the OSM collector.
30 tiles saved. **The labels were poor:**

- ~70% of tiles had per-tile coverage < 10%
- Most buildings fell back to the OSM **default 10 m** (no `height` tag,
  no `building:levels`)
- `hmax=10.0m` on 5 of 6 sample tiles — the few high-rises in OSM are
  not tagged with heights

Conclusion: **OSM is the wrong label source outside the European/North
American urban footprint.** Adding more Cartagena tiles with default-10m
labels would corrupt training, not help it.

### Trusted-source diagnostic for the Cartagena bbox

Querying `/api/height/diagnostics` on `(N=10.430, S=10.380, E=-75.510,
W=-75.560)`:

| Source | Coverage | Mean | Max | p95 | Notes |
|---|---|---|---|---|---|
| WSF3D | **48%** | 5.8 m | 21 m | 12 m | Best coverage; coarse 90 m — undersells tall buildings |
| OpenBuildings | 3.6% | 15.3 m | **65 m** | 54 m | Sparse but realistic, captures the Bocagrande high-rises |
| nDSM | 0% | — | — | — | FABDEM tile 404 |
| GHSL | 0% | — | — | — | WMS 404 |

**WSF3D + OpenBuildings together** would give us a usable label, if we
combine them carefully (OpenBuildings overrides WSF3D where it has data
and exceeds it).

## Strategy 1 — Provider-merge labels for the collector (recommended)

Replace the OSM-as-truth path in `tools/ml/collect_osm_tiles.py` with a
call to `app/server/core/height/merge_height_rasters` that fuses
`OpenBuildings` and `WSF3D` (and any other provider that responds).
The merged height raster becomes the per-pixel label; the satellite RGB
is unchanged.

**Pros:**
- Reuses the provider stack we already debugged (parallel fetch +
  outlier filtering already in place per F-ROOF1)
- Open Buildings has reasonable Cartagena coverage and realistic tall
  values
- Same approach works for any non-OSM region: Lagos, São Paulo,
  Mumbai, Manila

**Cons:**
- Provider rasters are noisier than OSM tags where OSM is good. A model
  trained on merged-provider labels will inherit that noise.
- Open Buildings 2.5 D heights are rounded to 1 m bins → can't beat 1 m
  MAE in principle from this label alone.

**Implementation sketch:**
1. New function `_fetch_provider_label(north, south, east, west, dim)`
   in the collector. It calls `merge_height_rasters` with `OpenBuildings`,
   `WSF3D`, and any other provider that `covers()` the bbox.
2. Returns a `(dim, dim)` float32 array with NaN where no provider had
   data (these pixels are masked out of the L1 loss anyway).
3. New CLI flag `--label-source {osm,providers,both}`. Default stays
   `osm` for backwards compatibility; we can collect new `_providers`
   tile sets without disturbing the existing one.

**Effort:** ~1 hour. The hard part is invalidating cache entries when a
provider changes, and we already have that via cache versioning.

## Strategy 2 — Skyline-photo height-from-image (research-grade)

The user's idea: *given a photo of a city skyline, predict the heights
of the buildings in the photo.*

This is a real but harder problem than overhead imagery. Two formulations:

### 2a — Bottom-only ground-photo → heights

Input: a single ground-level photo of a row of buildings. Output:
height of each building (in metres or relative units).

This is *monocular depth + scene parsing*. The literature is
substantial — recent approaches (Depth Anything V2, Marigold, ZoeDepth)
predict relative depth on any photo. To convert relative depth to
metric height we need either:
- a calibrated camera (sensor size, focal length, tilt) — usually
  unavailable in scraped photos
- a **ground-truth scale anchor** in the scene (a known-height
  reference object — a person, a car, a sign)
- **EXIF metadata** (most photos have lat/lng/altitude/heading from
  phone GPS — this can give the camera's position; combined with OSM
  building footprints in that direction we can solve for height by
  triangulation)

The Depth Anything V2 path is **already wired** in this repo at
`city2stl/height/predict.py` (we saw it in the earlier audit:
`model="pretrained"` runs DA V2 + linear calibration). It expects an
**overhead** RGB tile, but the model itself doesn't care about
viewpoint — it just predicts depth. Adapting to ground-photo input
would mean replacing the linear calibration step with a triangulation
step using EXIF + OSM footprints.

**Pros:**
- Could harvest training data from any geo-tagged tourist photo (Flickr,
  Mapillary, Google Street View) — orders of magnitude more data than
  satellite + OSM
- DA V2 backbone is already in the repo

**Cons:**
- Requires solving viewpoint geometry per-photo (camera pose,
  building-segmentation, scale anchor)
- Mapillary has 1.5 B images but their API has rate limits and image
  metadata varies in quality
- Bottom-photo heights only inform a small subset of the bbox (the
  buildings facing the camera) — we'd need many photos per region

### 2b — Stereo / multi-view photogrammetry

Input: 2+ photos of the same skyline from different angles. Output:
3D point cloud → building heights via segmentation.

This is **structure-from-motion** (SfM). Open-source pipelines exist
(COLMAP, OpenMVG). The user could provide a directory of photos and the
pipeline would produce a building-height raster.

**Pros:**
- More accurate than monocular depth
- For tourist destinations (Cartagena, Rome, Paris) there are millions
  of overlapping photos publicly available

**Cons:**
- SfM is computationally expensive (hours per scene)
- Aligning the resulting point cloud to a geographic bbox needs
  ground-control points — we'd need at least a few buildings of
  known location to pin it down
- Doesn't scale to "any random city"; needs per-city manual setup

### Recommendation on skyline approach

The skyline-photo idea is a real research direction but it is **one to
two months of work** for a useful prototype. Strategy 1 (provider-merge
labels) gets the same benefit for Cartagena in **one hour** and reuses
the provider stack already in the codebase.

If we want to chase the skyline idea anyway, the **lowest-effort path**
is:

1. Pull Mapillary photos for a small bbox via their free API
2. Use Depth Anything V2 (already in repo) for monocular depth on each
3. For each photo, use camera EXIF (lat/lng/heading) + OSM building
   footprints to assign each depth pixel to a footprint
4. Aggregate per-building (median depth ± uncertainty) → height
5. Use these per-building scalars as additional labels alongside the
   provider raster

This is realistic as a **next-phase project** but should not block
unblocking Cartagena training.

## Recommended next concrete steps (in order)

### Now (fits this session)

1. Add `--label-source providers` to `collect_osm_tiles.py` (Strategy 1
   implementation). Test on Cartagena: re-collect 30 tiles using
   merged WSF3D+OpenBuildings labels.
2. Compare tile quality: how does the provider-labeled Cartagena tile's
   height distribution differ from the OSM-default-10m version?
3. If provider labels look reasonable (max > 30 m, std > 10 m), retrain
   RoofNetV3 with the original 514 European tiles + 30 provider-labeled
   Cartagena tiles, at `tile_size=128`.

### Next session

4. If the model improves on tall-building tiles, expand to Manhattan
   and Tokyo using the same provider-merge path. Manhattan in
   particular is well-covered by `lidar_3dep` (USGS) at 1m resolution,
   so its labels will be the best in the dataset.
5. If results plateau again, sketch the skyline-photo pipeline as a
   separate proposal (`F-SKYLINE1`).

### Out of scope for this session

- Skyline-photo / SfM / Mapillary integration (requires its own design
  doc and dependency review).
- Re-collecting all European cities at higher zoom — the existing
  256×256 source is already plenty for `tile_size=128` (a free
  upgrade per the previous session's tile-shape audit).

## Files this plan will modify

| File | Change |
|---|---|
| `tools/ml/collect_osm_tiles.py` | New `--label-source providers` path; new helper `_fetch_provider_label()`; existing OSM path unchanged |
| `tools/ml/config.py` | Already added Cartagena, Manhattan, Tokyo |
| `cache/height_tiles_provider/*.npz` | New tile set produced by the new path; doesn't replace `height_tiles_osm/` |
| (training script) | Pass mixed train list `[osm_dir, provider_dir]` to `make_height_loaders` |

## Open questions for user before implementing

1. **Are we OK consuming Open Buildings + WSF3D as labels?** Both are
   permissively licensed (Apache 2.0 / CC-BY-4.0 respectively) for
   research/training use. Should still confirm we are not redistributing
   the rasters themselves — only the trained model.
2. **Do we want to gate Strategy 2 (skyline photos) on a successful
   Strategy 1?** I think yes — it's a much bigger commitment.
