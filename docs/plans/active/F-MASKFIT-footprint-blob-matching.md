# F-MASKFIT: match footprints to photo blobs before reading heights

Status: approved by the user 2026-11-17 (form: "Footprint-to-blob assign", "Depth stop in
column_top", "Page and scorer agree"). Proposal: [F-MASKFIT](../../proposals.md).

## Goal

The mask-heights loop (`tools/photo_label/mask_heights.py`) reads a building's height from the
SegFormer mask column above its projected footprint. A review of Cartagena on 2026-11-17 (39 seed
frames, 3 strips) found the photo masks agree with each other (the SegFormer and depth skylines are
within 0.3 to 0.4 deg) but not with the model: depth-vs-model IoU is 0.31 to 0.67. About 40 percent of
the disagreement comes from footprints projected one tower width off their tower, or missing. About 35
percent comes from wrong heights. Camera pose accounts for about 10 percent. 64 percent of readings
come out "too short", because `column_top` walks up into the farther tower behind a misplaced box.
The page also draws the dashed measured outlines without occlusion and in a different height mode
from the scorer.

## Approach

1. **Footprint-to-blob assignment** (new `mask_heights.assign_blobs`): per frame, label connected
   building blobs in the SegFormer mask (split at depth discontinuities when `<stem>_depth.npy`
   exists). Shift each visible footprint's projected column span by up to +/-6 deg of bearing to
   maximise its overlap with one blob, solved as an assignment so two footprints do not take the same
   blob. Read the height at the shifted span; record the shift. A blob with no footprint and a top
   above the tallest neighbour becomes a `missing_tower` candidate (bearing, top elevation angle, blob
   px). These candidates are reported, never written into production heights.
2. **Depth stop in `column_top`**: when the photo depth is available, stop the upward walk at a
   disparity drop to a surface farther than the footprint's distance by more than a set ratio. Drop
   the column if the depth at the footprint's base is inconsistent with its ray distance.
3. **Page and scorer agree**:
   - `terrain_solve.py` emits `measured_outline` occlusion-tested against the same z-buffer
     `project_buildings` uses.
   - The runner records which height mode it scored in, and the page labels the dashed outlines with
     that mode.
4. **Re-score Cartagena** with `claude/scripts/run_mask_heights.py`. Compare the share of too-short
   readings, the agreement across views (`agree_group`) and the score against the San Juan lidar
   answer key where frames exist.

## Target files

- `tools/photo_label/mask_heights.py` (`analyse_frame`, `column_top`, new `assign_blobs`,
  `unmatched_blobs`, `find_reassignments`)
- `tools/photo_label/terrain_solve.py` (`project_buildings`, `measured_outline`)
- `tools/photo_label/static/draw.js` (dashed outline drawing and its label)
- `claude/scripts/run_mask_heights.py` (records the height mode)
- `tests/test_photo_label_mask_heights.py`, `tests/test_photo_label_terrain_solve.py` (or the
  existing test file for `project_buildings`)

Not touched: `city2stl/skyline/`, `runs/`, `sites/annotations`.

## Success criteria

- On Cartagena seeds 1, 4 and 5, too-short readings fall from 64 percent toward the too-tall share
  (target: under 35 percent), with no fall in the count of `ok` readings.
- The median absolute top error against the depth skyline falls on seed_1 (baseline p90 12.5 deg on the
  strip).
- The dark glass tower and Allure in seed_1 appear as `missing_tower` candidates.
- The dashed outlines on the page no longer draw through nearer buildings.
- Existing mask-heights tests pass; new tests cover the shift, the depth stop and the occlusion test.

## Risks

- **A blob can hold two towers** (merged SegFormer mask). Splitting at depth jumps helps; when a
  blob still holds two footprints, keep both at their own spans rather than shifting.
- **A +/-6 deg window can steal a neighbour's blob** in dense rows. The assignment step and a
  penalty on the shift size guard this.
- **Seed 6 (overhead pose) fails the pose gate**: leave it gated.

## Decisions

(Lift into `docs/decisions/` when done.)

Steps 1 to 4 built 2026-11-17. Lifted into [building-heights](../../decisions/building-heights.md).

- Only tower-class footprints (visible box at least 40 px tall) are matched; small boxes stay put.
- Blobs are cut at depth jumps, then split at valleys of the top profile (towers join through low-rise
  at their feet). Blobs wider than 4x the box are not candidates.
- Depth is relative disparity with an offset, not 1/distance: base check uses a robust fit
  `disp = a/dist + b`, not a plain ratio. `find_reassignments` skips blob-matched too_tall rows.
- Runner passes depth to `analyse_frame` (beyond `--out`) and writes `missing_tower`.
- Result (Cartagena, 4 seeds): criteria NOT met. too_short stays 52-87 percent per seed (target under 35);
  ok count up on seeds 1/4/6, down on seed 5 (24 to 14 share, more too_short readings added); score
  median_abs_ln 0.476 to 0.510 (n 23 to 25). Many OSM heights are simply low, so the "walks into the
  farther tower" cause explains only part of too_short. Glass tower found as missing_tower (about 137.7 deg,
  expected 134); Allure not found. Seed 6 stays gated.
- Depth stop made opt-in (user: "Matching on, depth stop opt-in"). Re-score with it off, 4 seeds: ok share 21/16/22/7 percent,
  score median_abs_ln 0.464 (n 24), against 0.476 before F-MASKFIT and 0.510 with the stop on.
- Page outlines are occlusion-tested against the id buffer (bounding-box clip); the runner records
  `height_mode: measured` and `depth_stop` in mask_heights.json.
