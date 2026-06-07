# F-SKY3 — OSM-marker Voronoi for instance indexing

Proposal entry: `docs/proposals.md` F-SKY3

## Goal

Give the pipeline the **instance segmentation layer** that SegFormer-b0
doesn't ship — by using the OSM-projected x_px of each visible building
as a 1-D Voronoi marker, partitioning each merged mask blob into one
strip per OSM building, and emitting a separate silhouette for each.

F-SKY2 splits only at clear mask gaps (`min_gap_px ≥ 6` valley between
OSM projections). For tightly packed waterfront rows where SegFormer
produces a single contiguous mask with NO inter-tower gap (buildings
are visually adjacent, or the mask classifier collapses them), F-SKY2
does nothing — there's no gap to cut at. F-SKY3 cuts the strip
unconditionally at the Voronoi midpoint between adjacent OSM
projections. The result: N OSM buildings inside a blob → N silhouettes,
each indexed to one OSM building, regardless of mask gap presence.

## Approach

For each connected mask blob (or equivalently each merged silhouette
segment after F-SKY2 has already done its splits):

1. **Find marker projections.** Same containment metric F-SKY2 uses:
   any OSM projection whose x-range is ≥ 50 % inside the segment's
   x-range. Skip when < 2 markers (single-marker case is the existing
   1:1 match path; no instance ambiguity).
2. **Sort markers by x_px** and compute Voronoi boundaries — the
   midpoint between adjacent marker x_px values. The blob's column
   range gets partitioned into N strips, one per marker.
3. **Emit one silhouette per strip.** For each strip [cL, cR]:
   - Look only at building-mask pixels in that strip.
   - Extract roof_y as the topmost mask-True row averaged over the
     strip (or the contour minimum within the strip).
   - Compute base_y as the lowest mask-True row.
   - Assign `peak_x = marker.x_px` (the OSM marker that owns the strip).
   - Inherit `osm_anchored = True` so downstream knows this silhouette
     came from anchored processing.
4. **Skip if the strip's mask coverage is below a floor** (`min_mask_coverage = 0.10`
   of strip area). A marker whose Voronoi strip is mostly non-building
   was projected into a region the mask doesn't actually see — skip
   rather than emit a noise silhouette.

Order of operations in `region_pdf.py`:

```
merged_segments = _merge_silhouette_sources(contour_segs, mask_segs)
if reg.n_matches >= 3:
    merged_segments = osm_anchor_silhouettes(...)      # F-SKY2: gap splits
    merged_segments = osm_marker_voronoi_silhouettes(  # F-SKY3: marker splits
        merged_segments, projections, building_mask=_bmask)
```

F-SKY3 runs AFTER F-SKY2 because F-SKY2's mask-anchored gap splits are
more precise (they snap to the actual mask valley); F-SKY3 is the
fallback that catches the no-gap cases F-SKY2 left untouched.

## Target files

- `city2stl/skyline/pipeline.py`
  - New function `osm_marker_voronoi_silhouettes`.
  - Reuse `_proj_x_range` and the containment metric introduced in F-SKY2.
- `city2stl/skyline/region_pdf.py`
  - Add the second call right after `osm_anchor_silhouettes`.

## Success criteria

- `osm_marker_voronoi_silhouettes([], projections)` returns `[]`.
- `osm_marker_voronoi_silhouettes(segments, [])` returns `segments` unchanged.
- A synthetic segment containing 3 OSM markers with no mask gap between
  them produces 3 children.
- On Cartagena, at least one seed_5 spin view that previously had
  segments=9 gains segments at headings where the mask is contiguous
  and F-SKY2 did nothing (the heading 298° middle-skyline gap).
- Aggregate `seed_extracted_buildings` increases vs the F-SKY2.1
  baseline (currently 373).

## Known risks

- **OSM over-split**: when OSM has 2 polygons for what's really 1
  building (common in older imports), F-SKY3 forces a split where
  there shouldn't be one. F-SKY2's gap requirement was a natural
  guard against this; F-SKY3 removes that guard. Mitigation: only
  Voronoi-split when adjacent markers are at least ~20 px apart in
  the image (`min_marker_separation_px`) — closer-than-that markers
  are probably OSM artefacts.
- **Wrong roof_y per strip**: if a tall tower spans 3 OSM markers,
  cutting it into 3 strips and reading roof_y per strip gives 3
  different (and wrong) roof readings. Mitigation: when adjacent
  strips have ~identical roof_y AND no mask valley between them, the
  marker-based split is suspect — flag with low confidence rather
  than refuse the split.

## Outcome (2026-05-16): disabled after measurement

Implemented and measured on Cartagena (b0, default config):

| Metric | F-SKY2.1 baseline | F-SKY3 enabled | Δ |
|---|---|---|---|
| Tagged-building MAE | n=13, 17.28 m | n=8, 22.13 m | **regressed** |
| `seed_extracted_buildings` | 373 | 367 | −6 |

Unconditional Voronoi splitting was too aggressive: every multi-marker
segment got cut, producing tiny child segments that the matcher
re-assigned to wrong OSM buildings (the kiosk-in-front-of-tower
exception fires more often when fragments are short). The
mask-coverage floor and marker-separation guard didn't compensate.

Function kept in the module surface so the next iteration can opt in
or A/B-test, but the call site in ``region_pdf.py`` is commented out.

The right replacement is **a dedicated small instance-segmentation
model** that takes OSM-projected centroids as priors, not heuristic
Voronoi. The SAM family (specifically MobileSAM at ~10 M params or
TinySAM at ~5 M) accepts point prompts and returns per-instance
masks — exactly the indexing layer SegFormer-b0 lacks. See the
follow-up discussion in the session notes for sizing.

## Out of scope (deferred)

- Full 2D watershed (cv2.watershed with OSM markers as seeds). The 1D
  Voronoi over columns is correct for skyline-row geometry and avoids
  the heavy machinery; 2D watershed would matter if we ever processed
  bird's-eye views.
- SAM (Segment Anything) integration. SAM with OSM centroids as point
  prompts is the eventual "correct" model-side answer; F-SKY3 is the
  cheap, OSM-driven approximation that runs at SegFormer-b0 speed.
- Merge path (handle over-splits where one building has multiple
  contour peaks). Same status as in F-SKY2.
