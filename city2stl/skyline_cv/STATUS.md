# Status — what works, what doesn't

Snapshot of the skyline_cv pipeline as of the latest commit. Updated when
the behaviour changes materially.

## Run characteristics (Cartagena baseline)

| Metric | Value | Notes |
|---|---|---|
| Total runtime | ~170 s | Was 1033 s before vectorisation; 6× speedup |
| Buildings extracted | ~430–590 | Varies run-to-run with non-deterministic SegFormer details |
| Cross-seed coverage | n=3 buildings | The reliable validation signal — this is the headline problem |
| Tagged-height MAE | 19 m single-seed / 53 m cross-seed | Cross-seed bias = −46 m → tall buildings under-predicted |
| Worst residuals | b0389 (146 → 32), b0772 (150 → 48), b0142 (146 → 61) | All tall glass towers |
| Joint anchor success | 4 of 5 seeds | seed_2 produces an empty pano page (open-water view) |
| Tests | 21/21 pass | CV-math only; orchestration is untested |

## What works well

### Heading registration

- The joint-optimization framing (one rigid pano-to-geographic rotation
  per seed, weighted across all 12 spin views by observed-building column
  count) is the right structural choice. Local per-view IoU is ambiguous
  on long skylines because many offsets give similar column-union
  overlap; the joint sum disambiguates.
- 3°-coarse + 0.5°-fine sweep gives reliable convergence within ~0.5°
  for seeds with clear water/sky boundaries (Bocagrande across the bay,
  seed_4).
- Per-view fine search of ±8° around the seed anchor keeps spin views
  consistent. Earlier per-view escape hatches were tried and rolled back
  because they broke that consistency (see git log).

### Segmentation

- SegFormer-b0 (ADE20K) building/sky/water masks are the foundation.
  The LRU cache anchors image arrays so id-based cache misses don't
  produce silent cross-seed mask collisions (a critical bug fixed in
  this branch).
- `detect_buildings_from_mask` with the building-band pre-crop and
  walk-from-peak edge tightening produces visually clean tower
  bounding boxes when the skyline is dense (Bocagrande, Old Town).
- Mask-stitched 360° panoramas are seam-free (we never run SegFormer on
  the wide stitched image — we stitch the per-view masks instead) and
  faster than sliding-window inference.

### PDF report

- Auto-zoomed minimap fits matched footprints + a 100 m margin instead
  of a fixed 1500 m frame.
- Per-seed-view image panel is cropped vertically to the building band,
  killing wasted sea/sky space (matters for seed_4 across the bay).
- Footprint polygons are coloured to match their image-segment numbered
  badges — direct visual cross-reference between image and map.
- Stitched-pano page per seed includes a pano-vs-per-view comparison
  table (overlap count, pano-only, per-view-only).

### Performance

- `_project_all_buildings_vectorized` packs all OSM vertices into flat
  arrays and reduces per-building bearing/forward via `np.minimum.reduceat`.
  Eliminated ~10 M shapely accesses (629 s in the original profile).
- Spatial pre-filter restricts each seed's projection set to buildings
  within 4.5 km, cutting ~3000 buildings to ~400 per seed.
- PatchCollection batching in matplotlib drops ~60 s from per-view
  page rendering.
- 16-slot LRU SegFormer cache covers a seed's 12 spin views with
  headroom; image-array anchoring prevents stale-id collisions.

## What doesn't work

### Heights (the core product gap)

**Tall glass towers under-predict by 50–100 m.** Three hypotheses, none
ruled out:

1. SegFormer mask under-reaches the actual rooftop on reflective glass
   facades (the top of the spire reflects sky and is labelled "sky").
   Phase 2.5 added a sky-contour fallback (when the contour sits > 15 px
   above the mask roof, prefer the contour), but the validation page
   shows the fallback isn't recovering 100 m gaps.
2. The closest-in-column-bin gate drops the actual tall building when a
   nearer short building shares its column — the tall-and-back gets
   killed, then the short-and-front predicts a low roof.
3. Roof-y → height conversion may have a focal-length / pitch bug that
   compounds at distance. Worth verifying with a single hand-traced
   building.

Until one specific failure is traced end-to-end (which view, which
segment, which y_px, which projection), we can't pick between these.

### Cross-seed coverage is anaemic (n=3 buildings)

With 5 seeds and ~480 extracted buildings, only 3 are seen from ≥2 seeds
AND both predictions survive the plausibility filter. Suspected causes:

- Bocagrande seeds and Old Town seeds are ~2 km apart; the 4.5 km
  pre-filter is generous enough to span both, but a Bocagrande camera
  doesn't get good geometry on Old Town buildings beyond ~3 km because
  the bearing wedge is too narrow.
- Auto-screened seeds tend to cluster in the same district as the
  user-supplied seed URLs; we don't actively diversify.

This matters because without n≥10 cross-seed buildings, the height MAE
numbers are dominated by single-seed estimates that can't be cross-checked.

### Display bugs (visual only, not affecting numbers)

- `iou=0.00` shows in many per-view page titles even when 13+ segments
  matched. The `best_iou` field in `register_view_to_osm`'s return value
  isn't refreshed after the forced-anchor Pass 2 — it inherits whatever
  the wide-search would have produced (often zero because n_matches < 3
  at the wrong offsets).
- FOV cone in the auto-zoomed minimap is sized against the default
  `radius_m=1500` instead of the actual axis span. On a 300 m zoomed
  panel the cone overshoots the panel boundary by 5× and dominates the
  matched-footprint polygons.

### seed_2 pano page

For seed_2 (open water + trees, no clear skyline), the pano result is
`segments=0 matched=0 buildings_in_view=0` but a full page still renders.
Should either drop the page or surface a clear "no segmentation possible"
banner.

### Stitched pano can hallucinate

When SegFormer labels palm tree trunks or pavement structures as
"building" (seed_5 inland views), the stitched mask carries those false
positives through to the pano matcher. The per-view path is less
affected because each view's mask is small enough that occasional ghost
detections don't survive the per-segment width-ratio matching.

## Known weaknesses we accept for now

- Photo Sphere panos have arbitrary internal coordinate frames; we
  re-discover the rotation per seed. Some user-submitted Photo Sphere
  ids return the "no imagery" placeholder via the Static API, and we
  fall back to location-resolved road panos. This adds noise — the
  user-pointed pano may be the better camera, but we can't always use
  it.
- The image y_top/y_bot band crop is computed from the mask itself.
  If the mask under-detects, the crop is too tight and the displayed
  image truncates a real rooftop.
- DEM (open-meteo elevations) is wired but unreliable — most buildings
  end up with `terrain_elev_m = 0.0` because the free-tier API
  occasionally returns errors that we silently ignore. Castillo San
  Felipe sits on a 40 m hill that's missing from our height equation.

## Honest "what to do next" priorities

1. **Trace one specific height failure end-to-end.** Pick b0389
   (tag=146 → pred=32) and walk: which view sees it, what segment
   captures it, what y_px the mask returns, what `forward_m` projects,
   whether the closest-in-bin gate killed a higher prediction. One
   hand-trace will tell us which of the three hypotheses is the real
   bug.
2. **Fix the `iou=0.00` display bug.** Have `register_view_to_osm`
   always populate `best_iou` from the chosen offset's
   `_score_offset_semantic_iou(...)` value against the per-view mask.
3. **Fix the over-sized FOV cone.** Scale `cone_len` to the auto-zoom
   axis span, not the default `radius_m=1500`.
4. **Diversify auto-seed placement** to increase cross-seed coverage.
   Force one auto seed per district (split the bbox into 2×2 cells,
   pick the best candidate per cell).
5. **Drop the empty pano page** when `n_segments == 0`.
6. **Investigate why seed_2's pano returns empty** while its per-view
   pages produce some matches.

## What's deliberately not on the list

- Sliding-window SegFormer on stitched RGB — tried, removed in favour
  of mask-stitching (faster and seam-free).
- Per-view escape hatch around the joint anchor — tried, removed
  because it broke spin-view consistency.
- Monocular depth (MiDaS/DPT) integration — would help on glass-roof
  under-reach, but only worth doing after the above height trace
  rules out simpler causes.
