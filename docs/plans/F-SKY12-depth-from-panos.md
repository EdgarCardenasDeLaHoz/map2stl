# F-SKY12 — Depth Anything V2 on Street View panos

Proposal entry: `docs/proposals.md` F-SKY12

## Goal

Add a depth-from-pano signal to the F-SKY pipeline. Current
height extraction is purely geometric (pinhole-y of the matched
silhouette top + known anchor distance + camera intrinsics →
metres). A monocular depth network gives an **independent second
estimate** that can:

- flag matches where the two heights disagree by > threshold
  (likely a misregistered/misassigned building, not a tall one)
- downweight or reject those matches at aggregation time
- (future) replace the pinhole estimate when depth is more
  trustworthy (e.g. very tall buildings where the top is clipped)

Depth Anything V2 is **already in the project** at
`city2stl/height/predict.py:47` — used for monocular depth on
satellite tiles, calibrated to metres with OSM anchors. We are
reusing the same loader and the same calibration idea, just on
side-on pano crops instead of top-down satellite crops.

## Approach (Phase A — verifier only)

Smallest practical change. No new dependencies. No change to
the geometric extraction path. Adds one new step between
segmentation and matching, and one verification hook at
aggregation.

1. **Lazy DA2 loader in `skyline_cv.pipeline`.** Reuse
   `city2stl/height/predict.py:_load_da2` rather than introducing
   a parallel loader. Cache the pipeline as a module-level
   singleton (DA2 is ~300 MB; one load per process).

2. **`predict_pano_depth(view_rgb) -> depth_rel`** in
   `skyline_cv/pipeline.py`. Same pattern as
   `_depth_anything_inference` in `height/predict.py`: feed the
   PIL image, get the predicted-depth tensor, resize, normalise
   to [0, 1]. Pure function, unit-testable.

3. **`calibrate_pano_depth(depth_rel, anchor_distance_m,
   anchor_pixel) -> depth_m`**. The anchor distance from the
   seed to the **closest matched OSM building** is already
   known (used by the pinhole projection). Sample `depth_rel`
   at that building's footprint pixels, take the median, solve
   `depth_m = a + b * depth_rel` with one anchor (or two if
   we have a near and a far building). Worst case: assume
   `depth_m = anchor_distance_m * (1 - depth_rel)` if only one
   reference is available. Returns a per-pixel metric depth map.

4. **`depth_height_from_segment(depth_m, segment_top_xy,
   intrinsics, camera_height_m=2.5) -> height_m`**. For each
   matched silhouette top pixel, read the depth at that pixel,
   then trig:
   `height = camera_height + depth_m *
   tan(pitch_from_pinhole_y)`. The pitch is `atan((cy -
   y_top) / fy)` from the existing camera intrinsics. Returns
   one number per matched segment.

5. **Hook into per-view extraction.** In the loop where
   geometric heights are computed (the `extract_heights` path
   in `region_pdf.py`), also compute the depth-derived height
   for each match and store both. Aggregation keeps both
   columns.

6. **Disagreement flag.** At aggregation
   (`aggregate_building_heights`), if `|h_geometric -
   h_depth| / max(h_geometric, h_depth) > 0.4` for a given
   per-view sample, mark it `depth_disagreement=True` in the
   stored record. Phase A **does not change the aggregate** —
   it only surfaces the flag and reports the count on the
   per-region PDF header.

7. **Diagnostic on per-view PDF page.** Small text next to
   each matched segment: `h_geom=Xm / h_depth=Ym` (red if
   disagreement). Reuses `_render_seed_view_page` text-overlay
   machinery from F-SKY4 / F-SKY7.

## Why DA2 reuse (and not a new model)

- already a project dependency (`transformers`, `torch`)
- already cached on disk via `_DA2_CACHE_DIR`
- proven on this codebase's calibration pattern
  (`height/predict.py:_calibrate_depth`)
- ~300 MB; runs on CPU at ~1–2 s per view at default
  resolution (12 views × ~5 seeds = ~minute per region — slow
  but tolerable for a research pipeline)

The pano use case is closer to DA2's training distribution
(natural ground-level photos) than the satellite use case it's
already serving — so we expect at least as good a relative
ordering.

## Target files

- `city2stl/skyline_cv/pipeline.py` — new `predict_pano_depth`,
  `calibrate_pano_depth`, `depth_height_from_segment` functions
  (pure, unit-testable). Lazy DA2 loader.
- `city2stl/skyline_cv/region_pdf.py` — hook in the per-view
  loop; store `depth_height_m` and `depth_disagreement` in the
  per-match record; render the small text overlay.
- `tests/test_skyline_cv_depth.py` (new) — unit tests on the
  three pure functions with mocked depth maps.
- `docs/F-SKY-INTEGRATION.md` — add F-SKY12 row to active
  features table after Phase A lands.

## Success criteria

Phase A is successful if:
- DA2 loads and runs on a sample pano without errors
- depth-derived heights are within ±50% of geometric heights
  for the majority of well-registered buildings on Cartagena
  (where we have ground-truth from OSM)
- the disagreement flag fires on at least 1 known-bad match
  from the current Cartagena PDF (validates it's a useful
  signal, not noise)
- per-region runtime overhead ≤ 2× (one extra DA2 inference
  per view; SegFormer already runs per view, so we're in the
  same order of magnitude)
- no behavioural change to existing aggregated heights — pure
  observability addition

## Risks & open questions

- **Calibration is undertermined with one anchor.** A single
  point fixes the scale but not the bias. Worth testing with
  the closest+farthest matched buildings as a 2-point linear
  fit. If only one match is available, fall back to scale-only.
- **DA2 on perspective panos vs top-down.** Direction of
  "near/far" is different (camera Z vs ground depth). The
  inverse-depth normalisation in
  `_depth_anything_inference` may need adjustment for the
  side-on case; verify on the first test run.
- **Glass towers + reflections** known DA2 weakness. The
  disagreement flag will likely fire most often here — which
  is *also* where the geometric matcher most often fails. So
  Phase A doesn't necessarily distinguish "depth is wrong" vs
  "geometry is wrong" — that's Phase B.

## Phase B (future, not this round)

- Use depth as a primary signal where it's confident (low
  variance across the matched footprint pixels) and
  geometric where it's not. Confidence weighting.
- Train a calibration head on top of DA2 features
  specifically for the building-height-from-pano task —
  closes the relative-to-metric gap.
- Plug into F-SKY5 (MobileSAM) once that lands so depth is
  sampled within instance masks instead of OSM footprints.

## References

- `city2stl/height/predict.py:47-125` — existing DA2 loader
  and inference path (satellite, top-down)
- `city2stl/height/predict.py:128-180` — existing
  `_calibrate_depth` (linear regression with known anchors)
- `docs/F-SKY-INTEGRATION.md` — current pipeline architecture
- `docs/MODELS-REFERENCE.md` — model checkpoint registry
  (note: currently stale; updating separately)
