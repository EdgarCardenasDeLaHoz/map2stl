# F-SKY4 — SegFormer mask overlay on per-view PDF pages

Proposal entry: `docs/proposals.md` F-SKY4

## Goal

Visual debug aid. Each per-view PDF page currently shows the Street
View image with skyline-segment overlays, but **not** the raw SegFormer
building mask. When a building visible to the human eye isn't matched,
there's currently no way to tell whether:

- (a) SegFormer didn't classify it as building (model failure), or
- (b) SegFormer caught it but downstream silhouette extraction /
  matcher / containment filter dropped it (post-processing failure).

Painting the building mask in semi-transparent colour on top of the
view image surfaces this distinction immediately. (a) shows as
"obvious tower not coloured" — model fault. (b) shows as "obvious
tower IS coloured but has no numbered overlay" — pipeline fault.

## Approach

In `_render_seed_view_page`, after the Street View image is plotted
into the axes, overlay the cached SegFormer building mask using
`ax.imshow` with `alpha=0.25` and a colormap that puts True pixels
in a distinct hue (cyan suggested — doesn't clash with the existing
red/yellow/green/etc. segment colours).

The mask is already cached by `_neural_sky_and_building_masks(image)`
via id(image) — call it again in the renderer and it's a cache hit,
no extra inference cost.

Smallest practical change:
- Add ~10 lines to `_render_seed_view_page` to fetch the mask and
  overlay it.
- Add a small legend entry: "cyan tint = SegFormer-building".

## Target files

- `city2stl/skyline/region_pdf.py` — `_render_seed_view_page` only.

## Success criteria

- Every per-view PDF page shows the building mask as a transparent
  overlay. Cyan-tinted regions visibly correspond to tower
  silhouettes in the photo.
- No change to MAE, segment count, or any other numerical metric —
  this is render-only.
- No change to run time (mask is cached).
- 21 unit tests still pass.

## Known risks

- **Visual clutter**: at alpha=0.25 over already-overlaid colored
  segment boxes, the image can get busy. Mitigation: use light cyan
  (low saturation), keep alpha low (0.2-0.25), and overlay BENEATH
  the segment boxes (matplotlib zorder).
- **Mask cache miss**: if `_neural_sky_and_building_masks` is called
  on an image whose id() no longer matches the original capture (e.g.
  PDF rendering is on a different reference), it re-runs SegFormer.
  Mitigation: pass through the cached `building_mask` directly from
  `SeedViewRegistration` rather than re-deriving it from the image.
  Need to check whether `SeedViewRegistration` already carries it.

## Out of scope (deferred)

- Mask overlay on the stitched-pano page. Same idea, but pano is
  composed of multiple per-view masks already aligned by F-SKY1's
  pitch-uniform stitcher — would render the stitched-mask alongside
  the stitched-RGB. Could add later in a follow-up.
- Per-pixel sky mask overlay. Building is the higher-value debug
  signal for the height-extraction failure mode.
