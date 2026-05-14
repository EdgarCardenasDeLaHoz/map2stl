# Status — what works, what doesn't

Snapshot of the skyline_cv pipeline as of the latest commit. Updated when
the behaviour changes materially.

## Run characteristics (Cartagena baseline, with overrides)

| Metric | Value | Notes |
|---|---|---|
| Total runtime | ~120 s | Was 1033 s before vectorisation (~8.6× speedup) |
| `seed_urls_used` | 5 | seed_1/2/3/4/5 from `sites/cartagena.json` |
| `seed_registration_views` | 27 | Pass-2 successful spin views across all seeds |
| `seed_extracted_buildings` | ~370 | Excludes seed_3 (negative example); was ~480 when all seeds contributed |
| `negative_seeds` | `["seed_3"]` | Gas-station view, not a skyline; estimates excluded by design |
| Cross-seed coverage | low (~3 buildings) | The reliable validation signal — still the headline gap |
| Tagged-height MAE | 19 m single-seed / 53 m cross-seed | Cross-seed bias = −46 m → tall buildings under-predicted |
| Tests | 21/21 pass | CV-math only; orchestration is untested |

## Active per-seed overrides (Cartagena)

`sites/cartagena.json` ships with the following hand-verified anchor
offsets (joint IoU optimization found wrong local maxima for these — see
"What doesn't work" below):

```json
"anchor_offsets_deg": {
    "seed_1": 135.0,
    "seed_4": -180.0,
    "seed_5": 320.0
},
"negative_seeds": ["seed_3"]
```

seed_2 is screened-rejected (snapped pano lands in dense interior, no
clear skyline in any direction).

## What works well

### Heading registration

- The joint-optimization framing (one rigid pano-to-geographic rotation
  per seed, weighted across all 12 spin views by observed-building column
  count) is structurally correct.
- 3°-coarse + 0.5°-fine sweep gives reliable convergence within ~0.5°
  for seeds with clear water/sky boundaries.
- Per-view fine search of ±8° around the seed anchor keeps spin views
  consistent. Earlier per-view escape hatches (and the wide-search Pass 2
  fallback) were tried and rolled back because they let individual views
  drift to wildly different offsets, breaking the "one pano = one offset"
  geometric invariant.
- **Manual override** (`anchor_offsets_deg` in `sites/<region>.json`)
  bypasses the joint optimization for seeds where the IoU objective has
  degenerate local maxima. User visually verifies orientation, fills in
  the override once, gets a reproducible result.

### Segmentation

- SegFormer-b0 (ADE20K) building/sky/water masks are the foundation.
  The LRU cache anchors image arrays so id-based cache misses don't
  produce silent cross-seed mask collisions (a critical bug fixed earlier).
- `detect_buildings_from_mask` with the building-band pre-crop and
  walk-from-peak edge tightening produces visually clean tower
  bounding boxes when the skyline is dense (Bocagrande, Old Town).
- Mask-stitched 360° panoramas are seam-free (we never run SegFormer on
  the wide stitched image — we stitch the per-view masks instead).

### Reproducibility

- **Per-seed resolution cache** (`runs/seed_resolution_cache.json`) pins
  the snapped (lat, lon, pano_id) on first resolution. Subsequent runs
  hit the same pano even when the Static API metadata returns slightly
  different snaps on different calls. Cleared by deleting the file.
- **Negative-seed regression suite** (`negative_seeds` in
  `sites/<region>.json`): processes the seed's views end-to-end and
  renders them in the PDF, but excludes its estimates from the
  aggregate. Acts as a "this should produce zero useful contributions"
  fixture — if the pipeline ever starts emitting estimates from these,
  that signals a screening or matching defect.

### PDF report

- Auto-zoomed minimap fits matched footprints + a 100 m margin instead
  of a fixed 1500 m frame.
- Per-seed-view image panel is cropped vertically to the building band.
- Footprint polygons are coloured to match their image-segment numbered
  badges — direct visual cross-reference between image and map.
- Per-view title shows **effective geographic heading**
  (`api + offset`), not the raw API heading, so the depicted scene's
  compass direction is unambiguous.
- Negative-seed views display `[NEGATIVE EXAMPLE — estimates excluded]`
  in the title.
- Stitched-pano page per seed includes yellow compass-heading ticks
  overlaid on the strip so the user can verify the column-to-heading
  mapping visually.

### Performance

- `_project_all_buildings_vectorized` packs all OSM vertices into flat
  arrays and reduces per-building bearing/forward via
  `np.minimum.reduceat`. Eliminated ~10 M shapely accesses (629 s in the
  original profile).
- Spatial pre-filter restricts each seed's projection set to buildings
  within 4.5 km, cutting ~3000 buildings to ~400 per seed.
- 16-slot LRU SegFormer cache covers a seed's 12 spin views with
  headroom; image-array anchoring prevents stale-id collisions.

## What doesn't work

### Heights (the core product gap)

**Tall glass towers under-predict by 50–100 m.** Three hypotheses, none
ruled out:

1. SegFormer mask under-reaches the actual rooftop on reflective glass
   facades (the top of the spire reflects sky and is labelled "sky").
   The Phase 2.5 sky-contour fallback isn't recovering 100 m gaps.
2. The closest-in-column-bin gate drops the actual tall building when a
   nearer short building shares its column.
3. Roof-y → height conversion may have a focal-length / pitch bug that
   compounds at distance.

Until one specific failure is traced end-to-end, we can't pick between
these.

### Heading registration finds wrong local maxima for bay seeds

**The IoU objective is multi-modal when buildings exist in many
directions from the seed.** Multiple offsets project OSM building columns
into observed mask-building columns with similar fidelity. The algorithm
picks *a* local maximum; the user knows which one is geographically
correct.

For Cartagena specifically, **3 of 5 seeds (seed_1, seed_4, seed_5)
require the manual override** documented above. Only seed_3 (a
non-skyline view — negative example) and seed_2 (screened-rejected) work
without manual intervention via the algorithm alone.

**Tried and abandoned algorithmic fixes**:
- URL `h_token` as initial anchor (±25°, then ±60°): edges of search
  range were hitting the optimum.
- Stronger miss-penalty in the IoU score (0.3 → 0.6): no change in the
  found optima.
- 180°-symmetric check + tiebreaker bias: planned but not yet built.

The manual override is the practical answer; an algorithmic fix would
need either (a) a Photo Sphere metadata source that gives the pose
heading directly, or (b) a stronger discriminating signal in the IoU
objective (e.g. monocular depth comparison).

### Cross-seed coverage is anaemic (~3 buildings)

With 4 active seeds (seed_3 negative, seed_2 rejected) and ~370 extracted
buildings, only ~3 are seen from ≥2 seeds AND both predictions survive
the plausibility filter. Suspected causes:
- The 4.5 km pre-filter is geometrically tight enough to span Bocagrande
  + Old Town, but the bearing wedge from a Bocagrande seed onto Old Town
  buildings 2 km away is narrow, so few of the same buildings appear in
  both seeds' views.
- Auto-screened seeds tend to cluster near user-supplied URLs.

This matters because without n≥10 cross-seed buildings, the height MAE
numbers are dominated by single-seed estimates that can't be cross-checked.

### Display bugs (visual only, not affecting numbers)

- `iou=0.00` shows in many per-view page titles even when 13+ segments
  matched. The `best_iou` field in `register_view_to_osm`'s return isn't
  refreshed after the forced-anchor Pass 2.
- FOV cone in the auto-zoomed minimap is sized against the default
  `radius_m=1500` instead of the actual axis span. On a 300 m zoomed
  panel the cone overshoots and dominates.

### seed_2 (rejected)

For seed_2's snapped pano (dense interior), no direction in the 12-view
spin has a clear distant skyline. The pano page renders empty
(`segments=0 matched=0`). Should either drop the page or surface a
clear "no segmentation possible" banner.

## Known weaknesses we accept for now

- Photo Sphere panos have arbitrary internal coordinate frames; we
  re-discover the rotation per seed. Some user-submitted Photo Sphere
  IDs return the "no imagery" placeholder via the Static API, and we
  fall back to location-resolved road panos.
- The image y_top/y_bot band crop is computed from the mask itself.
  If the mask under-detects, the crop is too tight and the displayed
  image truncates a real rooftop.
- DEM (open-meteo elevations) is wired but unreliable — most buildings
  end up with `terrain_elev_m = 0.0`. Castillo San Felipe sits on a
  40 m hill that's missing from our height equation.

## Honest "what to do next" priorities

1. **Trace one specific height failure end-to-end.** Pick a tall
   tagged tower (e.g. b0389 tag=146 → pred=32) and walk: which view
   sees it, what segment captures it, what y_px the mask returns, what
   `forward_m` projects, whether the closest-in-bin gate killed a
   higher prediction.
2. **Fix the `iou=0.00` display bug** in `register_view_to_osm`.
3. **Fix the over-sized FOV cone** — scale `cone_len` to the auto-zoom
   axis span.
4. **Diversify auto-seed placement** to increase cross-seed coverage
   (split the bbox into 2×2 cells, pick the best candidate per cell).
5. **Drop the empty pano page** when `n_segments == 0`.

## Detailed implementation roadmap (next pass)

This section expands the priority list into an execution plan with explicit
success gates. The order is intentional: improve segment quality first,
then matching, then pano rendering/cropping diagnostics, then reporting UX.

### Phase A — split buildings more aggressively but safely

**Goal:** increase true building instance separation in dense skylines
(adjacent towers currently merge too often).

**Primary code surface:** `pipeline.py` (`detect_buildings_from_mask`).

**Plan:**

1. Keep the current mask-height and contour-based peak detection, but add a
  third per-column signal derived from **vertical gradients** inside each
  connected component.
2. Build that signal from Sobel/Scharr x-gradient magnitude and integrate it
  over the active building band so facade edges contribute strongly.
3. Union peak candidates from all signals (contour, mask-height, gradient),
  then deduplicate with a small pixel tolerance.
4. Use valley boundaries as hard limits, but refine each split edge toward
  nearby local minima in the gradient profile so split borders align with
  facade gaps, not arbitrary midpoints.
5. Add anti-over-split guards:
  - minimum inter-peak distance
  - minimum split width
  - reject low-prominence peaks in tiny/noisy blobs

**Acceptance criteria:**

- More separated segments on seed_1/4/5 skyline pages without exploding
  fragment count.
- No major regression in obvious single-tower cases (one tower should remain
  one segment).

### Phase B — make matching prefer plausible foreground buildings

**Goal:** reduce "background matched, foreground ignored" outcomes.

**Primary code surface:** `pipeline.py` (`match_segments_to_buildings`).

**Plan:**

1. Keep interval IoU as the base score (best horizontal overlap).
2. Add stronger distance prior: among close-IoU candidates, nearer footprints
  should win unless strongly contradicted by width plausibility.
3. Add explicit occlusion-aware penalty: if a farther candidate overlaps the
  same x-interval as a much nearer candidate, down-rank the farther one.
4. Tighten width plausibility weighting so candidates with grossly mismatched
  projected widths cannot win on weak IoU alone.
5. Record diagnostics per match (IoU, depth penalty, width score, final rank)
  to support audit when users flag incorrect matches.

**Acceptance criteria:**

- Fewer counterintuitive matches on flagged examples (including seed_5
  heading ~227 case family).
- Distinct-building match count should improve or stay flat; it must not drop
  significantly.

### Phase C — pano crop/order hardening and cut-off prevention

**Goal:** prevent building truncation at panel boundaries and make ordering
verification explicit.

**Primary code surface:** `pipeline.py` (`stitch_pano_views`,
`stitch_pano_masks`).

**Plan:**

1. Introduce a small horizontal crop safety margin (in px or deg-equivalent)
  around the central `step_deg` slice so boundary towers are less likely to
  be clipped.
2. Mirror the exact same crop logic for RGB and mask stitchers to preserve
  geometry consistency.
3. Keep luminance normalization in place (already added) so seams do not look
  like ordering errors.
4. Add optional debug metadata for stitched views (e.g., per-panel centre
  heading sequence) for rapid order validation during triage.

**Acceptance criteria:**

- Known cut-off case(s) no longer clipped at seam boundaries.
- Heading sequence remains monotonic in stitched order after offset+sort.

### Phase D — improve report usability (page numbering)

**Goal:** make review feedback unambiguous by page reference.

**Primary code surface:** `region_pdf.py` (`_render_pdf`).

**Plan:**

1. Stamp every page footer with `Page X / N`.
2. Use a final-pass page counter strategy so total page count is accurate.
3. Keep the numbering style subtle enough to avoid obscuring plots/images.

**Acceptance criteria:**

- All pages in `Cartagena_skyline_report.pdf` have stable page numbers.
- User feedback can reference exact page IDs without ambiguity.

### Validation protocol after implementation

Run `08_region_skyline_pdf.py --region Cartagena`, then compare:

1. **Segmentation quality:** per-seed segment counts and visual split quality
  on dense skyline strips.
2. **Matching quality:** reduction of implausible background matches on pages
  previously flagged.
3. **Pano continuity:** verify no seam-induced clipping regressions and no
  perceived order anomalies.
4. **Report UX:** confirm page numbering appears on every page and is legible.

If metrics improve but a specific failure remains, capture one concrete
segment/building mismatch with its score breakdown and adjust only the
responsible phase (avoid broad parameter churn).

## What's deliberately not on the list

- Sliding-window SegFormer on stitched RGB — tried, removed in favour
  of mask-stitching (faster and seam-free).
- Per-view escape hatch around the joint anchor — tried, removed
  because it broke spin-view consistency.
- Monocular depth (MiDaS/DPT) integration — would help on glass-roof
  under-reach, but only worth doing after the above height trace
  rules out simpler causes.

## Per-region configuration cheat sheet

`sites/<region>.json` schema:

```json
{
  "name": "Cartagena",
  "north": 10.42950, "south": 10.38450,
  "east":  -75.52210, "west": -75.56790,
  "seed_urls": ["https://www.google.com/maps/..."],
  "anchor_offsets_deg": {
    "seed_1": 135.0
  },
  "negative_seeds": ["seed_3"]
}
```

- `seed_urls` — Google Street View URLs; each one becomes `seed_<N>`.
- `anchor_offsets_deg` — per-seed manual pano-to-geographic offset (deg).
  Skip the joint optimization for these. Use when you can identify the
  correct compass direction visually but the algorithm finds a wrong
  local maximum.
- `negative_seeds` — seed names whose height estimates are excluded
  from the aggregate. The views are still rendered in the PDF so you
  can verify the pipeline isn't producing spurious matches.
