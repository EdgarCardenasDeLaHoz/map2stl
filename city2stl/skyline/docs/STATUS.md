# Status — what works, what doesn't

> The dated **Current state (2026-06-07)** section directly below is the
> authoritative snapshot. The older 2026-05-24 material further down is
> kept for historical context; where the two disagree, this top section
> wins.

---

## Current state (2026-06-07) — pano report v2, bearing recovery, F-SKY22/24

### Headline metrics (all three test regions, full pipeline)

| Region | Seeds (user + auto) | `seed_extracted_buildings` | Bearing recovery |
|---|---|---|---|
| Cartagena | 5 + 6 auto = 11 | ~716 | all SKIP (already aligned) |
<!-- negative_seeds for Cartagena is ["seed_2", "seed_3"] (see sites/cartagena.json) -->
<!-- seed_2 is screened-rejected dense interior; seed_3 is the gas-station view. -->

| Miami | 4 + 2 auto = 6 | ~381 | seed_3/4 APPLY, rest SKIP |
| Chicago | 2 + 3 auto = 5 | ~210 | 4/5 APPLY (Loop was ~180° off) |

Auto-proposed standoff locations now run through the **full** pipeline in
addition to the user seeds (previously they were only a swap pool). This
roughly doubled Cartagena (375→716) and Chicago (101→210) coverage.

### HTML report structure (per seed page)

Two **independent** tab groups so a pano view and a top-down view are
visible together:

- **Pano-space** (wide strips): Street view · SegFormer mask · Depth ·
  **Distance scan**.
- **Top-down** (square polar plots, shared 1500 m axis): Footprints ·
  Satellite · Reconstruction · **Heights**.

Cross-cutting overlays:
- **Cardinal lines** N/E/S/W drawn on every pano-space strip
  (`_draw_pano_north_line_inplace`) so the column→bearing mapping is
  visually checkable.
- **Distance scan** (`_render_pano_bearing_scan_png`): column-indexed
  (x = pano column, aligns 1:1 under the pano) plot of depth-derived
  nearest-building distance (blue, raw) vs OSM nearest (orange), with
  N/E/S/W dashed guides and building-column tick markers. The horizontal
  gap between the two curves is the bearing error.
- **Image zoom/pan modal**: click any report image → wheel-zoom +
  drag-pan + esc.
- Anything farther than `POLAR_MAX_M = 1500 m` is dropped from all
  top-down plots and the bearing recovery.

### Bearing recovery (F-SKY24 Phase 3) — `region_pdf._stitch_pano_composite`

A silhouette × OSM cross-correlation refines (or rescues) the satellite-
coastline anchor:
1. Per-degree depth silhouette (nearest building distance from the depth
   mask) and per-degree nearest-OSM-building distance, both filled with a
   **`NO_BUILDING_M = 3000 m` sentinel** on empty bearings. The sentinel
   is what makes "pano sees a building where OSM says open space" a strong
   penalty — without it, depth saturation flattened the score landscape
   and a wrong rotation could win.
2. Cross-correlate over all 360 rotations; **apply only when `improve ≥
   45 %`** (MAE drop vs the current anchor) — empirically this cleanly
   separates already-aligned seeds (improve <30 %) from genuinely
   misaligned ones (improve >50 %). A naive "distinct peak" gate was
   tried and rejected (broke on broad clusters like the Chicago Loop).

Result: Chicago's Loop (satellite recovery landed ~180° off) is now
auto-corrected; Cartagena/Miami's already-good anchors are preserved.

### Splitter (F-SKY22 + F-SKY24 Phase 1)

- **F-SKY22 sliding-window splitter** (`_pano_sliding_window_split`):
  the global splitter's per-component split cap clipped towers on a wide
  pano where the whole skyline is one connected blob. Windowed splitting
  (360 px window, 280 px stride) gives each slice its own budget +
  density filter. seed_1 went 12→33 segments, seed_5 13→25.
- **F-SKY24 depth-fused post-cut**: depth-discontinuity cuts on the
  pano-wide splitter — **measured no-op** across all 9 seeds because
  F-SKY21's per-cluster depth pass (threshold 0.08) already drained that
  signal upstream. Kept (cheap), but it is not contributing splits.
- **OSM-anchored split** (F-SKY2 on the pano path): the win that depth
  could not give — splits a wide segment at contained OSM projections.
  +5 segments across the 9 seeds, catches same-distance adjacent towers.

### Mask: water-only ground cap — `pipeline._neural_sky_and_building_masks`

The waterline clip now (a) finds the foreground waterline **bottom-up**
(distant bay water at the horizon no longer chops buildings standing in
front of it) and (b) uses **water classes only** — earth/sand removed,
because buildings stand *on* sand and Cartagena's bright sandy towers
were being partially mislabelled sand and clipped. Recovered the seed_5
peninsula-tip cluster.

### Depth (Depth Anything V2)

- **Tiled inference** (`predict_pano_depth_tiled`): the HF pipeline
  squashes a 2688-wide pano to ~518 px before inference; tiling at full
  resolution per 518 px tile sharpens the depth pano. Cost-neutral
  (~4-7 s). Computed once per seed, shared via
  `StitchedPanoResult.pano_depth`.
- **Saturation past ~1.2 km is structural** to DA2's [0,1] inverse-depth
  and is the dominant residual error (Chicago, far Miami/Cartagena
  towers). Verified NOT fixable by: larger model (Base ≈ Small, 3× cost),
  higher input res (tiling), or column-averaging the sample (base pixel
  is marginally better — averaging biases farther via longer sightlines).
  Worked around with an **OSM-clamp** (>1200 m silhouette values that
  have a nearby OSM building adopt the OSM distance). The remaining path
  to break saturation is a metric-depth model (ZoeDepth/Metric3D) — not
  yet attempted.

### F-SKY1 floor-strip periodicity — re-enabled (default on)

The "striations ≈ 1 storey" signal. Re-enabled
(`compute_floor_period=_F_SKY1_ENABLED`, env `SKYLINE_CV_F_SKY1=0` to
disable) with a **sub-harmonic-descent fix**: the autocorrelation peak is
frequently a 2-3× multiple (coarse banding), so we descend from the
dominant peak to the fundamental. Status:
- `inferred_height` (a ratio, f_px-independent) is **usable** — ~97 m
  median on Cartagena.
- `inferred_distance` is **not trustworthy yet** — still ~2.5× low
  (~122 m vs ~300 m true), because the stitched-pano `f_px` is wrong.
  Calibrating the pano `f_px` is the open task before this feeds heights.
- Hit rate is low (~64/1672 on Cartagena; 0 on Chicago/Miami where far
  facades are <80 px tall). Diagnostic-summary line printed per run.

### Project move

`city2stl/height/` → **`city2stl/skyline/height/`** (the ML height
stack now lives inside skyline). All imports across `app/`, `tests/`,
`tools/` were rewritten accordingly.

### Known open items

- F-SKY1 pano `f_px` calibration (blocks trustworthy floor-derived
  distance).
- Depth saturation > 1.2 km (metric-depth model is the real fix).
- Miami seed_3 bearing correction passed at the 47 % gate edge — worth a
  visual confirm; raising the gate to 50 % would skip it.
- F-SKY1 `inferred_height` is computed but not yet surfaced in the report
  or fed into the height aggregate.

---

## Historical snapshot (2026-05-24)

Snapshot of the skyline pipeline as of 2026-05-24. Updated when the
behaviour changes materially.

> **New here?** Read [`AGENT-GUIDE.md`](AGENT-GUIDE.md) first — it's the
> navigation map for the codebase: where each pipeline stage lives,
> how the cross-view consensus + water filter + auto-seed-replacement
> chain together, env vars, troubleshooting cheatsheet, and known
> dead-ends to avoid.

For a structural audit of the whole module (dead code, redundant
signals, oversized functions) see
[`docs/plans/F-SKY-AUDIT-2026-05-24.md`](../../../docs/plans/F-SKY-AUDIT-2026-05-24.md)
(latest refresh — 11 of 13 F-CLEAN proposals shipped, surfaces a new
pano-recovery non-determinism finding).
For the canonical pipeline shape see
[`docs/plans/F-SKY-PIPELINE-CONSOLIDATION.md`](../../../docs/plans/F-SKY-PIPELINE-CONSOLIDATION.md).

## Run characteristics (Cartagena, current baseline)

| Metric | Value | Notes |
|---|---|---|
| Total runtime | ~120 s | Same as before; F-SKY8 + F-SKY11.1 added negligible overhead |
| `building_records` | 7076 | 3015 OSM + 4061 MS-merged (F-SKY8 opt-in via `use_satellite_footprints`) |
| `seed_urls_used` | 5 | seed_1/2/3/4/5 from `sites/cartagena.json` |
| `seed_registration_views` | 26 | Per-seed view count varies with screening + pitch correction |
| `seed_extracted_buildings` | ~593 | Up from ~370 once F-SKY8 added polygons in OSM-sparse waterfronts |
| `negative_seeds` | `["seed_3"]` | Gas-station view, not a skyline; estimates excluded by design |
| Coverage | 8 good / 2 medium / 1 weak | of 11 screened locations |
| Tests | 21/21 pass | CV-math only; orchestration exercised by full-run smoke |

## Active per-seed overrides (Cartagena)

`sites/cartagena.json` previously shipped with hand-verified anchor
offsets for seed_1/4/5; these were REMOVED on 2026-05-17 so the pano
recovery (F-SKY11.1) determines headings freely. The new ground-truth
for seed_5 (closest land is at WSW per visual inspection) showed the
unaided water-only correlation locking onto the symmetry-twin 180° away;
see `scripts/13_heading_recovery_demo.py` for the multi-channel approach
that adds an asymmetric RGB channel to break the tie.

```json
"anchor_offsets_deg": {},   // intentionally empty
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

### Heading registration is partially automated, still partially manual

**The IoU objective is multi-modal when buildings exist in many
directions from the seed.** Multiple offsets project OSM building columns
into observed mask-building columns with similar fidelity. The algorithm
picks *a* local maximum; the user knows which one is geographically
correct.

**Heading-recovery stack as of 2026-05-24** (precedence order):

1. **`anchor_offsets_deg` manual override** — highest precedence;
   user-explicit. Only seed_4 (-180°) + seed_5 (320°) still need it on
   Cartagena. seed_1's manual 135° was dropped after F-SKY11.1
   recovered 136° (Δ +1°).
2. **F-SKY11.1 pano-coastline recovery** — when `use_pano_coastline_recovery`
   + `drive_pano_recovery_anchor` are set, a sharp recovery (σ ≤ 0.10,
   peak > 0.15) replaces the joint-anchor coarse sweep with a ±15° fine
   refine. Currently in use on Cartagena for seed_1.
3. **Joint anchor IoU optimizer** — today's fallback when (1) and (2)
   don't apply; 3° coarse over 360°, then 0.5° fine over ±5°.

**Measured per-seed on Cartagena** (recovery vs ground truth, 2026-05-17 run):

| Seed | Manual | F-SKY11.1 recovered | Verdict |
|---|---|---|---|
| 1 | (dropped) | 142° | matches old manual within ~7° — auto path works |
| 2 | (none) | low-peak | falls through to joint optimizer |
| 3 | (negative seed) | flat curve | doesn't matter |
| 4 | -180° | 104° (Δ -76°, sharp) | recovery confidently wrong — keep manual |
| 5 | 320° | 310° (Δ -10°) | borderline; could drop manual with widened refine |

**Tried and ruled out**:
- F-SKY11.2 pano→birdseye IPM: monocular SegFormer water has insufficient depth reach (~5–7 m); IoU rotation search produces flat signal. Marked `denied` in proposals. See [F-SKY11.2 plan post-mortem](../../../docs/plans/F-SKY11.2-pano-birdseye-registration.md).
- Per-bearing F-SKY11 sweep as production primary: lossy compared to F-SKY11.1 pano sweep; kept as the per-direction visualisation demo.

The manual override is still the answer for seed_4's failure mode. An
algorithmic fix would need a stronger discriminator — `scripts/13_heading_recovery_demo.py`
is exploring a multi-channel approach (water + asymmetric RGB) for the
peninsula 180° symmetry case.

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
   higher prediction. Concrete plan with trace points and Phase-2
   monocular-depth gating: [glass-roof-height-fix-plan.md](glass-roof-height-fix-plan.md).
2. **Fix the `iou=0.00` display bug** in `register_view_to_osm`.
3. **Fix the over-sized FOV cone** — scale `cone_len` to the auto-zoom
   axis span.
4. **Diversify auto-seed placement** to increase cross-seed coverage
   (split the bbox into 2×2 cells, pick the best candidate per cell).
5. **Drop the empty pano page** when `n_segments == 0`.

## Roadmap — archived

The Phase A / B / C / D implementation roadmap that previously lived
here described work that's been done (in different shapes) since this
section was written. The canonical references now live in:

| Concern | Where it lives now |
|---|---|
| Building split / instance separation (was Phase A) | F-SKY2 anchored split + F-SKY7 local-max + dual baseline (both shipped); see consolidation plan |
| Matching plausibility (was Phase B) | F-SKY6 1:1 dedup + F-SKY2.1 containment fallback (shipped) |
| Pano cropping (was Phase C) | `stitch_pano_views` central-30° crop + luminance normalisation (shipped) |
| Page numbering (was Phase D) | Not done; folded into `F-CLEAN9` items if revisited |
| Monocular depth integration | F-SKY11.2 (denied — see plan); future work owns its own proposal |

The detailed plans for each shipped feature are in `docs/plans/F-SKY*.md`.
The consolidated end-state pipeline shape is in
[`docs/plans/F-SKY-PIPELINE-CONSOLIDATION.md`](../../../docs/plans/F-SKY-PIPELINE-CONSOLIDATION.md).
The structural audit (dead code, redundancies, function sizes) and the
F-CLEAN1..13 cleanup proposals are in
[`docs/plans/F-SKY-AUDIT-2026-05-17.md`](../../../docs/plans/F-SKY-AUDIT-2026-05-17.md).

<details>
<summary>(Historical Phase A–D + depth-integration plan, kept for reference)</summary>

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

### Depth integration plan (where it helps, and rollout order)

Depth should be integrated as a **ranking and QA signal first**, not as a
hard replacement for the geometric pipeline. The best near-term value is
resolving foreground/background confusion in matching.

#### Target use points in the current pipeline

1. **Segment-to-building matching** (`pipeline.py::match_segments_to_buildings`)
  - Add a depth-derived foreground score per segment region.
  - Use it to down-rank projected buildings that are farther than the
    dominant segment depth when x-overlap is similar.
  - This directly addresses "background matched, foreground ignored".

2. **Segment splitting / instance separation** (`pipeline.py::detect_buildings_from_mask`)
  - Use depth discontinuities as another split cue (alongside contour,
    mask-height, and vertical gradients).
  - Helpful when adjacent towers have similar color/roofline but different
    depth planes.

3. **Height plausibility checks** (post-estimation, before aggregation)
  - Use relative depth consistency to flag implausible height outcomes:
    if a predicted-tall segment is consistently farther and lower in image
    support than its neighbors, mark for low confidence.
  - Keep this as a soft confidence modifier, not a hard reject in v1.

4. **PDF diagnostics** (`region_pdf.py`)
  - Add optional per-segment depth summary in the legend (e.g. median inverse
    depth percentile) so failure cases can be audited quickly.

#### Rollout stages

1. **Stage 0: offline ablation (no pipeline behavior change)**
  - Run depth model on cached seed views and stitched panos.
  - Save per-segment depth stats; compare against known bad matches.
  - Decide whether depth signal is stable enough for ranking.

#### Stage 0 data schema (implementation-ready)

Persist one JSON artifact per region run, e.g.
`runs/depth_ablation/<region>_depth_ablation.json`.

Top-level shape:

```json
{
  "meta": {
    "region": "Cartagena",
    "run_id": "2026-05-14T22-15-00Z",
    "depth_model": "<model-id>",
    "image_count": 0,
    "notes": "offline ablation, no ranking changes"
  },
  "views": [],
  "segments": [],
  "candidates": [],
  "cases": []
}
```

Required arrays and fields:

1. `views[]` (one row per processed image: per-view + stitched pano)
  - `seed_name`, `view_name`, `kind` (`seed_view` | `stitched_pano`)
  - `heading_deg`, `fov_deg`, `image_w`, `image_h`
  - `depth_min`, `depth_p10`, `depth_p50`, `depth_p90`, `depth_max`
  - `depth_valid_frac` (non-NaN/non-zero fraction)
  - `inference_ms`

2. `segments[]` (one row per detected segment)
  - `seed_name`, `view_name`, `segment_id`
  - `x_left`, `x_right`, `top_y`, `base_y`, `peak_x`
  - `seg_depth_p25`, `seg_depth_p50`, `seg_depth_p75`
  - `seg_inv_depth_p25`, `seg_inv_depth_p50`, `seg_inv_depth_p75`
  - `seg_depth_iqr`
  - `depth_edge_strength` (median gradient magnitude on segment borders)
  - `matched_feature_id_baseline` (current matcher output, unchanged)

3. `candidates[]` (one row per segment-building candidate used by matcher)
  - `seed_name`, `view_name`, `segment_id`, `feature_id`
  - `interval_iou`, `width_score`, `distance_m`
  - `depth_consistency_score` (0..1, higher = candidate depth aligns with segment)
  - `occlusion_penalty_depth` (0..1, higher = likely behind foreground)
  - `rank_baseline` (existing matcher rank)
  - `rank_with_depth_shadow` (simulated rank in Stage 0, not applied)

4. `cases[]` (manually flagged failures for focused review)
  - `case_id` (e.g. `seed5_h227_bg_match`)
  - `seed_name`, `view_name`, `segment_id`
  - `expected_behavior` (`foreground_should_win` etc.)
  - `baseline_feature_id`, `depth_shadow_feature_id`
  - `improved` (boolean)
  - `comment`

#### Stage 0 scoring protocol

Run baseline matcher unchanged, then compute a **shadow rank** using depth
terms (no production behavior change). Compare:

1. Case-level win rate on flagged failures (`cases[].improved`).
2. Candidate-rank deltas where depth flips winner among near-IoU candidates.
3. Stability: fraction of segments where depth is low-confidence
   (`seg_depth_iqr` too large or `depth_valid_frac` too low).
4. Runtime overhead per image (`inference_ms`).

Promotion gate to Stage 1/2:

- Flagged-case win rate improves materially.
- No broad instability (low-confidence depth does not dominate).
- Runtime overhead remains acceptable for full regional runs.

2. **Stage 1: passive diagnostics (log-only)**
  - Integrate depth inference behind a feature flag.
  - Attach depth diagnostics to segment objects; do not alter ranking.
  - Validate runtime/memory impact and failure modes.

3. **Stage 2: matching prior (active, low risk)**
  - Blend depth prior into candidate score when IoU is close.
  - Constrain impact with caps so IoU geometry remains primary.
  - Expected gain: fewer far-building false matches.

4. **Stage 3: split refinement (active, medium risk)**
  - Use depth edges to refine inter-tower split boundaries.
  - Guard against over-splitting with minimum width/prominence rules.

5. **Stage 4: optional height support (experimental)**
  - Explore depth-assisted roof localization for glass towers.
  - Keep separate from default height equation until validated on tagged
    buildings and cross-seed consistency.

#### Model and operational constraints

- Prefer a lightweight monocular depth model for throughput (12 views/seed +
  pano pages).
- Cache depth outputs alongside segmentation cache to avoid repeated inference.
- Keep a strict fallback path: if depth fails/unavailable, behavior must match
  current baseline exactly.
- Depth is relative-scale by default; do not treat it as absolute metric depth
  without calibration.

#### Success criteria for enabling depth by default

1. Measurable reduction in known bad background matches on Cartagena failure
  pages (including seed_5 heading~227 family).
2. No significant drop in distinct matched buildings.
3. Runtime increase remains acceptable for current report workflow.
4. Cross-seed disagreement does not worsen.

</details>

## What's deliberately not on the list

- Sliding-window SegFormer on stitched RGB — tried, removed in favour
  of mask-stitching (faster and seam-free).
- Per-view escape hatch around the joint anchor — tried, removed
  because it broke spin-view consistency.
- Monocular depth as a **default hard dependency** — defer until the staged
  rollout above shows clear matching gains with acceptable runtime.
- Monocular depth (MiDaS/DPT) for the glass-roof roof-y fix is **planned but
  gated** on the Priority 1 height trace ruling out the simpler causes (mask
  under-reach vs. closest-in-bin gate vs. pinhole math). Full plan and
  acceptance gate: [glass-roof-height-fix-plan.md](glass-roof-height-fix-plan.md).

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
