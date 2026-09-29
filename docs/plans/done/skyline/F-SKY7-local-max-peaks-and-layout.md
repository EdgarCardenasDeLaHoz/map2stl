# F-SKY7 — Local-max contour peaks + per-view layout refactor

Proposal entry: `docs/proposals.md` F-SKY7

## Goal

Two coupled changes for the same test case (Cartagena seed_5 page 31,
heading 298°):

1. **Find peaks even when the contour doesn't drop to sky.** The
   current `detect_building_silhouettes` splits the contour wherever
   the sky reaches between towers. For continuous mask regions where
   the SegFormer-building blob spans many towers without a clean sky
   valley, no peaks are emitted and the matcher has nothing to assign.
   F-SKY7 adds a second-pass peak detector that finds local maxima
   *relative to a smoothed contour baseline* — i.e. a row of glass
   towers whose tops bump up and down by 20–40 px above a moving mean
   produces one peak per bump, even if the contour stays high enough
   that no pixel is sky-classified between them.
2. **Restructure the per-view PDF page.** The diagnostic legend table
   at the bottom is unused (user feedback). Move the SegFormer mask
   from a cyan tint OVERLAID on the photo to its own dedicated panel
   directly below the photo so they can be compared side-by-side at
   full size. Reclaim the legend's vertical space for the mask plot
   and give the minimap full page height.

## Approach

### Part 1 — Local-max contour peaks

In `detect_building_silhouettes` (`pipeline.py`), the current peak
extraction uses `scipy.signal.find_peaks` on the inverted contour
(peaks = lowest y = highest in the image). It requires a peak
prominence relative to the SURROUNDING (which is sky-level when sky
breaks between towers). When the contour is monotone-but-bumpy
because the mask is continuous, prominence is too low and the peak
is rejected.

Add a second pass:
1. Compute a smoothed baseline of the contour via
   `uniform_filter1d(contour, size=40, mode="nearest")` (40 px is ~6°
   at FOV=75 / W=640 → captures cluster-scale shape, not individual
   towers).
2. Compute the "above-baseline" signal: `bump = baseline - contour`
   (positive when contour is HIGHER on screen, i.e. tower top).
3. Find `find_peaks` on `bump` with prominence ≥ 6 px and distance
   ≥ 12 px (one tower per ~2° of FOV at 640 width — denser than the
   primary pass).
4. Merge the new peaks with the existing sky-valley peaks via the
   existing `_merge_silhouette_sources` semantics (de-dup by x-IoU).
5. For each new peak, derive x_left / x_right from the baseline
   crossings (where `bump > 0` ends on either side), and base_y from
   `_building_base_y_from_mask` as the existing path does.

### Part 2 — Page layout refactor

Current axes:
- `ax_img` = `[0.03, 0.10, 0.55, 0.78]` (left half top)
- `ax_map` = `[0.62, 0.10, 0.35, 0.78]` (right half top)
- `fig.text` legend at `(0.03, 0.07)` filling the bottom

New axes:
- `ax_img`  = `[0.03, 0.52, 0.55, 0.40]`  — top-left, image + segments
  (cyan tint REMOVED — it goes to its own plot)
- `ax_mask` = `[0.03, 0.08, 0.55, 0.40]`  — bottom-left, SegFormer
  building mask on its own (with the photo as a faint background so
  the user can still see what region the mask covers)
- `ax_map`  = `[0.62, 0.08, 0.35, 0.84]`  — right, minimap (taller)

The legend table is removed. The aggregate diagnostic info that
mattered (counts of F/B/P flags) moves into the figure title:
`"… segments=9  estimates=40  flags=2B/3P"`.

The `ax_mask` plot shows the building mask as a high-contrast
cyan-on-faint-greyscale image. Useful comparison: the photo's actual
buildings should align with the mask's coloured regions. Discrepancies
(towers in photo, no mask) point at SegFormer failures.

## Target files

- `city2stl/skyline/pipeline.py`
  - Augment `detect_building_silhouettes` with the local-max pass.
- `city2stl/skyline/region_pdf.py`
  - Rework `_render_seed_view_page` axes layout.
  - Remove the bottom legend text.
  - Move flag counters into the figure title.

## Success criteria

- On seed_5 page 31 specifically, the segment count rises above 9
  (the central gap acquires at least 2–3 new segments from the
  local-max pass).
- 21 unit tests still pass.
- The new per-view PDF page shows three panels (image+segs / mask /
  minimap) with no bottom text and noticeably bigger panels — the
  user explicitly asked for "better use of space".
- The local-max pass doesn't over-segment views that previously
  worked (no large segment-count jumps on seed_4 / seed_1 spin views
  that were already producing the right count).

## Known risks

- **Over-segmentation on noisy contours**: a contour with high-
  frequency noise (per-column SegFormer mask noise) could trigger
  spurious local-max peaks. Mitigation: smooth the contour with a
  small kernel before computing `bump`; the existing
  `gaussian_filter1d` is already imported.
- **Wrong x_left / x_right derivation**: baseline-crossing-based
  bounds may be too wide for tightly packed clusters. Mitigation:
  cap the new peak's width at the distance to the nearest neighbour
  peak's midpoint (Voronoi-style).
- **Plot ordering inside `_render_seed_view_page`**: matplotlib's
  `add_axes` ordering vs zorder interactions can be subtle. The
  refactor needs to test the rendering on a known good view (any
  seed_1 page) to confirm no visual regression.

## Out of scope (deferred)

- F-SKY5 (MobileSAM) — the heavier alternative; deferred unless
  local-max-only doesn't close the gap.
- A "diff view" plot that highlights where the mask and the matched-
  building footprints disagree.
- Drawing the mask in the minimap (mask is image-coords, minimap is
  lat/lon — non-trivial transform).
