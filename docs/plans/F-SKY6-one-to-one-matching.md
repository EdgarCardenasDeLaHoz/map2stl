# F-SKY6 — 1:1 segment/building matching + all-projections audit overlay

Proposal entry: `docs/proposals.md` F-SKY6

## Test case

Cartagena seed_5 heading 298° (PDF page 31). Diagnostic table extract:

```
 1.[   ] b0268    hdg=-34.3°  d=409m   proxy=18.7  pred=14.0
 2.[   ] b0268    hdg=-34.3°  d=409m   proxy=18.7  pred=14.0     ← duplicate
 3.[  P] b0291    hdg=-29.2°  d=477m   proxy=14.8
 4.[ B ] b0333    hdg=-22.7°  d=580m
 5.[ BP] b0339    hdg=-21.3°  d=600m
 6.[   ] b0379    hdg=-16.9°  d=714m   proxy=23.8
 7.[   ] b0519    hdg=+17.5°  d=629m                              ← 34° gap before this
 8.[  P] b0589    hdg=+30.8°  d=749m   proxy=146.2
 9.[   ] b0601    hdg=+33.3°  d=771m   proxy=13.1
```

Two distinct indexing failures:

**(a)** Segments 1 & 2 both win OSM building `b0268`. The matcher
scores each segment independently and picks each segment's best
candidate without checking whether another segment already claimed
it. This is a global-uniqueness violation, exactly the kind a
Hungarian assignment over a (segment × building) cost matrix
prevents.

**(b)** The 34° angular gap between segment 6 and segment 7
(≈ 259 px wide at this FOV, **40 % of the image width**) covers
clearly visible tall towers in the central glass-tower cluster. The
silhouette detector found 9 segments total — none in that central
band. SegFormer's mask there is likely contiguous with no clear
peaks, and `detect_buildings_from_mask`'s connected-component sweep
either merged them into the adjacent segments or filtered them out
for size.

(a) is a one-function fix in the matcher; the bug is unambiguous.

(b) is harder to diagnose without seeing which OSM projections WERE
considered and rejected in that x-band. The companion
"all-projections audit overlay" change paints every OSM projection
(matched or not) on the per-view minimap as a faint background marker,
so the user can immediately see whether OSM has buildings projecting
into the gap that the matcher discarded.

## Approach

### Part 1 — Greedy / Hungarian 1:1 assignment

Today's matcher loop in `match_segments_to_buildings`:

```
for seg in segments:
    score every (seg, proj) pair  → combined
    bucket  = within 0.10 of best combined
    rescue  = closer credible projections (F-SKY2.1)
    nearest = bucket sorted by forward_m
    match   = nearest  (with kiosk-in-front exception)
```

Each segment picks its match independently. To enforce 1:1:

1. Build the (N segments × M projections) cost matrix where
   `cost[i][j] = -combined[i][j]` for accepted pairs and a large
   positive number for rejected ones.
2. Run `scipy.optimize.linear_sum_assignment` (already imported in
   pipeline.py) on the cost matrix.
3. Each segment's match = whichever projection the assignment paired
   it with. Buildings unpaired (M > N or assignment rejected for
   high cost) stay unmatched.
4. Preserve the existing match_diagnostics list (top-3 candidates
   per segment) for the audit, separately from the assignment result.

`linear_sum_assignment` runs in O(N³) but N is per-view-segment count
(typically 5–15) so this is negligible.

### Part 2 — All-projections diagnostic overlay

In `_draw_view_minimap`, before drawing matched-segment lines,
paint each projection's footprint as a hollow grey dot at its
(centroid_lat, centroid_lon). Matched projections then get the
existing colored treatment painted ON TOP.

This makes "OSM has buildings here but the matcher didn't pick them"
visually obvious — the unmatched grey dots in an under-matched x-band
tell you whether the gap is a matcher failure or an OSM data gap.

## Target files

- `city2stl/skyline_cv/pipeline.py`
  - `match_segments_to_buildings` rewrite to use
    `linear_sum_assignment` for the final pick stage.
- `city2stl/skyline_cv/region_pdf.py`
  - `_draw_view_minimap`: add the all-projections background overlay
    before the matched-segment painting.

## Success criteria

- On seed_5 page 31, segments 1 and 2 no longer match the same
  building. Either segment 2 gets a different OSM building or it
  becomes unmatched (which is correct — there's no second OSM
  building at that exact x position).
- Cartagena run completes without test regressions (21/21 pass).
- The per-view minimap shows faint grey markers at every projection,
  with the colored matched-segment markers painted over them.
- For seed_5 page 31 specifically, the all-projections overlay
  reveals whether OSM has buildings projecting into the 259-px
  central gap or not. If yes → matcher rejection problem (next
  proposal). If no → OSM data gap (out of scope for the pipeline).

## Known risks

- **Hungarian rejects matches the greedy path would have accepted**.
  The 1:1 constraint can force-pair a segment with its second-best
  candidate when a higher-score conflict pushes it out. Mitigation:
  set the cost-matrix "reject" threshold conservatively so only
  CLEARLY bad pairings (combined < 0.05) are rejected; otherwise the
  Hungarian only enforces uniqueness, not quality cuts.
- **All-projections overlay clutter**. Cartagena's seed_5 has 3000+
  buildings; drawing all of them clutters the panel. Mitigation:
  only draw projections that were SCORED (passed the matcher's
  pre-filter — IoU or containment ≥ thresholds), which is typically
  10–30 per view.

## Out of scope (deferred)

- Loosening the silhouette detector to find more peaks in the
  central gap (that's the upstream segmentation issue F-SKY5
  MobileSAM is meant to address).
- Re-running registration after 1:1 assignment as additional
  evidence.
