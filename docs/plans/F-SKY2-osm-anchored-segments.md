# F-SKY2 — OSM-anchored silhouette splitting / merging

Proposal entry: `docs/proposals.md` F-SKY2

## Goal

Use the projected OSM footprint x-ranges as **structural priors** during
silhouette extraction so the segment count for a view tracks the actual
building count, not whatever SegFormer happened to merge or split.

Today the pipeline produces segments from two OSM-blind sources
(contour peaks, mask connected components) and then asks the matcher to
align them with OSM projections. When SegFormer:
- **merges** adjacent towers (common on Cartagena's Bocagrande row —
  three glass towers sharing a contiguous mask blob become one segment),
  the matcher has at most one segment to assign 3 buildings to. Two
  match-target buildings are silently dropped.
- **splits** a single building (a window column with sky reflection in
  the middle of a tower → contour shows two peaks), the matcher
  generates two segment dicts for one OSM footprint; one wins, the
  other matches a neighbouring building wrongly.

OSM tells us exactly how many buildings should be in any image x-band
and where their boundaries fall. Using that as a *structural anchor*
during silhouette extraction fixes both failure modes upstream of the
matcher.

## Approach

After `_merge_silhouette_sources` produces the merged silhouettes, and
after `register_view_to_osm` produces the OSM projections (with x_left_px,
x_right_px), pipe the segments through one new function:

```python
osm_anchor_silhouettes(
    segments, projections, *, building_mask=None, contour=None,
    min_gap_px=6, min_split_overlap=0.20,
) -> list[dict]
```

**Splitting logic** (handles SegFormer merges):

1. For each segment with x-range [sL, sR], find OSM projections whose
   x-range overlaps it with interval-IoU ≥ `min_split_overlap`.
2. If ≥ 2 distinct projections qualify, sort them by `x_px`.
3. For each adjacent pair (proj_i, proj_{i+1}), the OSM gap is
   `(proj_i.x_right_px, proj_{i+1}.x_left_px)`. If that gap is ≥
   `min_gap_px` wide and lies inside [sL, sR], compute a split column:
   - Default: midpoint of the OSM gap.
   - Refined: column with the minimum building-mask coverage within the
     gap (preferred — snaps the split to where the mask itself is
     thinnest, which is the actual inter-building separator).
4. Cut the segment at each split column, distributing peak_x to the
   nearest OSM projection in each piece. Inherit base_y / top_y from
   the parent segment (refine later if needed).

**Merging logic** (handles SegFormer over-splits):

1. For each OSM projection, find all segments whose mid_x lies within
   its `[x_left_px, x_right_px]` AND whose IoU with it is ≥
   `min_split_overlap`.
2. If ≥ 2 segments map to one projection, fuse them into a single
   segment spanning min(x_left)..max(x_right), top_y = min(top_y),
   peak_x = column with highest contour (smallest y).

The split path is much higher value than the merge path on Cartagena
(over-merging is observably common; over-splitting less so). Implement
split first; merge can be a follow-up if data justifies it.

**Idempotency**: a segment unaffected by either rule passes through
unchanged. The function should be safe to call even when projections
is empty (returns the segments unchanged) so it doesn't add a hard
dependency on the registration step.

## Target files

- `city2stl/skyline/pipeline.py`
  - New function `osm_anchor_silhouettes`.
  - Optional: add `osm_split_count` / `osm_merge_count` to the segment
    dict for diagnostic visibility.
- `city2stl/skyline/region_pdf.py`
  - After `_merge_silhouette_sources`, call `osm_anchor_silhouettes(
    segments, reg["all_projections"], building_mask=_bmask)`.

## Success criteria

- For at least one Cartagena seed view, a previously single segment
  that spanned 3 OSM buildings becomes 3 segments after the anchored
  split. Visible in the per-view PDF page as 3 numbered overlays
  instead of 1.
- The aggregate `n_matched` per seed increases (more OSM buildings
  successfully match against segments), and the validation MAE either
  stays the same or improves (more correct matches = more correct
  per-view heights).
- `osm_anchor_silhouettes([], projections)` returns `[]`.
- `osm_anchor_silhouettes(segments, [])` returns `segments` unchanged.

## Known risks

- **Anchor-from-wrong-offset**: if the registration's `best_offset` is
  wrong (the joint anchor optimization picked a 180° symmetric local
  max for example), the OSM projections themselves are wrong, and
  anchored splitting will *split correct segments at wrong places*.
  Mitigation: only run anchored splitting when registration succeeded
  with `n_matches ≥ 5` (the same gate the per-view path uses for trust).
- **OSM-data sparsity**: in regions with sparse OSM, the gap between
  two adjacent OSM buildings can be a real-world 30 m gap (parking
  lot) that doesn't correspond to anything in the silhouette. The
  `min_gap_px ≥ 6` filter + the mask-coverage-min refinement protect
  against splitting at columns where there's actually a wall.
- **OSM-merges**: if OSM has two adjacent towers merged into one
  polygon (common in older imports), we'd produce one projection for
  what is really two buildings, and lose the chance to split. Out of
  scope here — a future "split OSM by mask" pass could address it.

## Out of scope (deferred)

- Re-running registration with the post-anchored segments as
  additional evidence.
- Smarter peak_x assignment after a split (Hungarian over distance
  to OSM x_px).
- The merge path (handle over-splits) — defer until split-path data
  shows it's necessary.

## F-SKY2.1 refinements (2026-05-16)

After the first integration shipped, visual inspection of seed_5's
spin view at heading 322° (page 31) showed the middle ~40 % of the
visible skyline had clearly distinct tall towers with no numbered
match — the OSM polygons project into that band, but the matcher
filtered them out before F-SKY2 ever saw them. Two compounding fixes:

1. **Lower the F-SKY2 trust gate from `n_matches ≥ 5` to `≥ 3`.** The
   gate excludes the exact failure case it was designed to fix: a
   view that has SegFormer-merged adjacent towers ends up with few
   detectable peaks → few matches → gate excludes the view → no
   anchored splitting → still few matches. 3 is the floor where the
   registration's pano-to-geo offset is still consistent across the
   joint optimization; below that we can't trust the anchor positions.
2. **Add a containment fallback to the matcher's IoU pre-filter.**
   `match_segments_to_buildings` currently rejects any (segment, proj)
   pair with `interval_iou < 0.10`. For a 500 px segment containing
   three 50 px projections, each projection has IoU ≈ 0.10 (borderline)
   and gets dropped — denying F-SKY2 the projections it needs to find
   gaps. Add: also accept when `proj_containment ≥ 0.5` (the same
   metric F-SKY2 itself uses to find anchor candidates). A narrow
   projection mostly inside a wide segment qualifies for matching
   even when IoU is small.

These are one-line edits to the gate threshold and the matcher's
candidate filter; no new function or data structure. They land as
part of F-SKY2 (no separate proposal entry — same goal, same files).
