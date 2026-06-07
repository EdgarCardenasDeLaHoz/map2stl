# F-SKY11.1 — Pano-level coastline alignment

Proposal entry: `docs/proposals.md` F-SKY11.1 (to add)

Status: **planning only**. No implementation in this iteration. Captured
as a plan so a future session can pick up from a clear design rather
than re-deriving the architecture.

## Why redo F-SKY11 against the pano

F-SKY11 ran the keypoint-alignment scorer once per 75°-FOV spin view.
That has three structural problems:

1. **Each view sees only ~5 of the 24 coastline key points.** The
   keypoints are evenly distributed around the seed at 15° spacing,
   but a single 75° view covers only ~75° / 15° = 5 of them. The other
   19 contribute nothing to that view's score.
2. **12 independent best-heading recoveries can disagree.** F-SKY11's
   per-view sweeps for Cartagena seed_5 produced wildly different
   "best" headings across the 12 spin views (sparse picks differed
   by up to 230° between adjacent spin views). There is no principled
   way to combine them.
3. **FOV truncation kills edge keypoints.** Keypoints near the left or
   right edge of a view project to extreme x columns where the per-
   column water mask is noisy and the projection formula loses
   precision (the inverse-tangent term grows fast).

The stitched 360° pano fixes all three: every keypoint contributes to
every candidate offset, there is only ONE offset to recover, and there
are no FOV edges.

## The recovered quantity

A single scalar **heading_offset_deg ∈ [0, 360°)** such that for any
pano column `c`:

    world_bearing[c] = (heading_per_col[c] + heading_offset_deg) % 360

The pipeline already computes `headings_per_col` (see
`pipeline.stitch_pano_views`); F-SKY11.1 just needs to find the offset
that aligns the satellite keypoints' world bearings with the pano's
horizon line.

When the spin captures' headings are already calibrated to true geo
north (which is what the existing joint anchor optimizer is supposed to
deliver), the recovered offset is ≈ 0. When the pano's headings are off
by N°, the recovered offset is N°. So the recovered offset *is* the
correction the existing manual `anchor_offsets_deg` overrides in
`sites/<region>.json` exist to provide — making it computable, not
manual.

## Inputs (all already in the pipeline)

- `pano_image`        — H × W_pano × 3 stitched RGB, from `stitch_pano_views`
- `pano_water_mask`   — H × W_pano boolean, from `stitch_pano_masks`
- `headings_per_col`  — W_pano-long float array, from `stitch_pano_views`
- `seed_lat`, `seed_lon`, `pitch_deg`
- Satellite keypoints — from F-SKY11 `detect_coastline_keypoints`
  (24 entries, each carries bearing/distance/lat/lon)

No new I/O. The pano + mask are stitched at the end of Pass 2 in
`region_pdf._seed_multiview_registration` and currently fed into
`stitch_pano_masks` for the stitched-pano-page diagnostic. Same data,
new consumer.

## Algorithm

### Step 1 — pano horizon-y per column
For each pano column `c` walk the column top→bottom in the water mask.
The "horizon y" at that column is the topmost water pixel
(`y_top_water[c]`). When the column has no water at all (looking up at
buildings), record `NaN` — the column is dropped from the score for
this offset.

This is exactly the F-SKY11 per-column metric, just over W_pano columns
instead of one 75°-FOV view.

### Step 2 — per-keypoint expected y at candidate offset
For candidate `offset_deg`:

    for each keypoint kp:
        world_bearing = kp.bearing_deg
        # Find the pano column whose heading matches the world bearing
        # under this candidate offset
        pano_bearing = (world_bearing - offset_deg) % 360
        col_idx = nearest column where headings_per_col[c] == pano_bearing
        if no column within 1° of that bearing: skip kp
        expected_y = project_lonlat_to_view(
            kp.lon, kp.lat,
            seed_lat, seed_lon,
            heading_deg = headings_per_col[col_idx],
            fov_deg     = 360 / W_pano  # per-column FOV
            ...
        )
        # NB: in the pano the horizontal mapping is by bearing lookup,
        # not by FOV-tangent. The vertical projection still depends on
        # the keypoint distance and camera pitch.
        delta_y = |y_top_water[col_idx] - expected_y|
        score_kp = max(0, 1 - delta_y / tolerance_px)

    score(offset) = mean(score_kp) over keypoints with a valid match

Because the pano is continuous over 360°, every candidate offset has
all keypoints in-frame — so the score uses all 24 of them, every time.

### Step 3 — sweep offset over [0, 360)
1° step. Best offset = argmax. Optionally a ±5° refine at 0.1°.

Cost: 360 candidates × 24 keypoints × O(1) lookup ≈ 9k ops. Cheap.

## What the algorithm does NOT need

- No per-view inference (already done).
- No new SegFormer calls (use the cached water mask).
- No additional satellite fetch (F-SKY11 already has the keypoints).
- No new image cache slot.

## Diagnostic deliverable (script 12)

`city2stl/skyline/scripts/12_pano_coastline_demo.py` — analogous to
script 11 but on the pano:

- Page 1 — same satellite reference page as script 11 (key points
  + radial signature + seed/rings).
- Page 2 — **the full 360° pano with the same numbered key points
  projected at the recovered offset, drawn as vertical lines + dots at
  expected y**. This is the visual "do these line up?" page.
- Page 3 — pano per-column horizon-y curve overlaid against the
  per-bearing expected-y curve from the keypoints; visually a sharp
  visual match when the offset is right.
- Page 4 — offset-sweep score curve over [0, 360°); the peak should be
  a single sharp lobe at the correct offset (vs F-SKY11's per-view
  curves that were often multi-modal).

The fundamental "are the key points in both views at the same place"
visual is one page (page 2) instead of 12; that alone makes diagnosing
much faster.

## Integration (after the demo proves the principle)

Phase B in `region_pdf._seed_multiview_registration`:

1. After the spin captures + per-view registration, before the joint
   anchor optimizer runs, stitch the pano + masks (already done for the
   stitched-pano-page later).
2. Call the new `sweep_pano_heading_offset(...)` to recover the offset.
3. If the recovered peak's σ < threshold (sharp peak), use the offset
   as the joint-anchor optimizer's initial seed (replacing
   `anchor_offsets_deg` for that seed when both are present, with the
   site config taking precedence as a fallback).
4. If the peak is flat / ambiguous, do nothing and let the existing
   optimizer run as before — the pano-level recovery becomes opt-in
   not mandatory.

This preserves the existing matcher behavior for seeds where the
coastline signal is too weak (inland views, e.g. Chicago), while
removing the manual override requirement for water-adjacent seeds
(Cartagena, Miami).

## Target files (when implementation begins)

| File | Change |
|---|---|
| `city2stl/skyline/coastline_registration.py` | Add `score_pano_offset_keypoints` and `sweep_pano_heading_offset` |
| `city2stl/skyline/scripts/12_pano_coastline_demo.py` (new) | Standalone visualisation |
| `city2stl/skyline/region_pdf.py` (Phase B integration) | Wire the recovered offset into the joint anchor optimizer's seed |
| `sites/cartagena.json` (Phase B) | Mark which `anchor_offsets_deg` entries become obsolete once Phase B lands |

## Success criteria

For the demo (Phase A):

- The single offset-sweep curve for Cartagena seed_5 has a sharp peak
  (σ < 0.1 over 360 candidates) at the offset the user knows is right
  (`320° - URL_heading_320°` ≈ 0° if API heading is already true, or
  whatever non-zero value corrects the existing manual override).
- The 24 numbered key points visibly land on the pano's water/non-
  water boundary line at the recovered offset.

For the production integration (Phase B):

- On Cartagena, the recovered offset for each water-adjacent seed
  matches the existing `anchor_offsets_deg` value within ±5°.
- On Miami (next test region), no manual `anchor_offsets_deg` needs to
  be set for water-adjacent seeds — the pano recovery is correct on
  its own.
- Tagged-building MAE either improves or stays within ±1 m of the
  current b3 + F-SKY7 + F-SKY8 baseline (13.73 m on Cartagena).

## Known risks

1. **Pano seam artefacts.** SegFormer is applied per-view and the
   stitcher concatenates central-30° crops of each view's water mask;
   adjacent crops can disagree on the water boundary at the seams.
   Mitigation: per-column horizon-y is locally noisy at the seams but
   the keypoint score uses all 24 points, so a few bad columns don't
   dominate.
2. **Pano per-column FOV varies with rectilinear distortion.** The
   stitcher takes the central 30° of a 75°-FOV rectilinear view; the
   angle-per-column is non-uniform within that 30° (sec²(α) factor).
   `headings_per_col` already encodes this, so the bearing→column
   lookup is correct, but the per-column metric scale isn't uniform.
   Likely a 1-2% effect — acceptable for ~1° offset resolution.
3. **Pitch correction must be uniform across the pano.** Spin captures
   that use the per-seed pitch-correction fallback (some views at
   different pitches than others) would produce vertical-seam horizon
   discontinuities. The current pipeline already enforces uniform
   pitch per spin (see `any_needs_pitch_correction` in region_pdf), so
   this is a constraint to preserve, not a new requirement.
4. **The recovered offset is meaningless when the spin sees no
   coastline.** Inland views (Chicago) have no water → no keypoints →
   score(offset) is uniform → wrong "best" recovery. Phase B's
   sharpness gate handles this.

## Out of scope (deferred again)

- Two-zone (near/far) signature comparison. The pano already gives 360°
  of horizon-y data, which is the right discriminator without needing
  zone splitting.
- Building-skyline cross-view matching against the pano (would need
  per-building heights, which the production pipeline produces but
  whose accuracy is the open question we're trying to validate).
- Multi-pano alignment across seeds (use seed-A's pano recovery to
  bootstrap seed-B's). Worthwhile once Phase B is stable.

## Why captured plan-only

Same three reasons as F-SKY10 / F-SKY11:

1. The visualisation pass needs to land first so the user can confirm
   the algorithm is doing what it's supposed to BEFORE wiring into the
   matcher (the production-side regression risk is real — the joint
   anchor optimizer is the heart of Cartagena's results today).
2. Phase B touches `_seed_multiview_registration` which is the most
   load-bearing function in `region_pdf.py`; the integration deserves
   its own focused session rather than being bolted onto Phase A.
3. Validation requires Miami (the next site without manual
   `anchor_offsets_deg`). Phase B success criteria depend on having
   that second region scaffolded.

When ready: implement Phase A (the demo script + the two new functions
in coastline_registration.py). Inspect the PDF. If the visual confirms
key points line up with the pano's coastline at the recovered offset,
proceed to Phase B.
