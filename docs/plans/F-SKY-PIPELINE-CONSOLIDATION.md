# F-SKY pipeline consolidation

Status: **plan only** (2026-05-17). Captures the canonical end-to-end
flow that brings together F-SKY1..F-SKY11.1 with redundant signals
rationalized. The current `region_pdf._seed_multiview_registration`
already runs most of these in some order; this document re-orders and
trims them and is the reference for the next refactor pass.

## Goal

A single, ordered pipeline that:

1. **Recovers the seed's true heading at the pano level** (cheapest
   most-constrained signal first).
2. **Uses that heading to project OSM + MS Buildings polygons** into
   each per-view image at known-correct bearings.
3. **Matches per-view silhouettes against those projections** using
   the best of F-SKY2 / F-SKY6 / F-SKY7 / F-SKY8 logic.
4. **Extracts heights, aggregates across seeds, validates** —
   unchanged from today.
5. **Falls back gracefully** when any signal is too weak to use,
   never blocking the matcher from running with the signals it has.

Every signal becomes either *core* (always runs) or *experimental*
(off by default, opt-in by site config). Today the boundary is
blurry; this plan makes it explicit.

---

## Inventory of F-SKY features

| ID | Status today | What it does | Plan disposition |
|---|---|---|---|
| F-SKY1 | implemented, optional | Floor-period diagnostic — independent height/distance estimate from horizontal banding | **Diagnostic-only**, report as side-channel column in the per-view diag table |
| F-SKY2 | core | OSM-anchored silhouette splitting at mask gaps | **Core** — runs when `n_matches >= 3` |
| F-SKY3 | disabled (regressed MAE) | Unconditional Voronoi splitting by OSM marker x_px | **Removed from core path**, kept on module surface for offline A/B; module docstring updated to reflect |
| F-SKY4 | core | Per-view PDF mask overlay | **Core** (rendering only) |
| F-SKY6 | core | 1:1 segment↔building dedup + considered-but-lost orange-dot overlay | **Core** |
| F-SKY7 | core | Local-max peak detection + dual baseline + per-view layout refactor | **Core** |
| F-SKY8 | core opt-in | Microsoft Buildings polygon merge (OSM-sparse fill) | **Core opt-in** via `use_satellite_footprints` |
| F-SKY10 | landed conservatively, opt-in | Per-building roof-colour cross-view scorer (0.15 weight blend) | **Demoted to diagnostic-only**. Per-building colour was empirically fragile; keep the scorer + module on disk but DO NOT default-wire into matcher. F-SKY11.1 supersedes the *intent* (cross-view registration). |
| F-SKY11 | demo + visualization tool | Per-view image-level coastline alignment with numbered keypoints | **Useful diagnostic tool** — `scripts/11_coastline_demo.py` visualises the same numbered keypoints in both the satellite (top-down) and street view (side-on), letting the user verify heading recovery by eye. The PER-VIEW sweep is lossy compared to F-SKY11.1's pano sweep (only ~5 of 24 keypoints per FOV), so production heading recovery uses F-SKY11.1 instead, but the per-view PDF inspector stays the per-direction debug tool. |
| F-SKY11.1 | Phase A landed; Phase B landed gated (log-only by default) | Pano-level coastline-offset recovery | **Real measurement on Cartagena**: seed_1 recovered 136° vs manual 135° (Δ +1°, accurate enough to drop the manual override); seeds 4/5 recovered values 75-85° wrong with apparently-sharp peaks (gates can't filter automatically). Now integrated as the joint-anchor optimizer's coarse seed when `drive_pano_recovery_anchor` is set + sharp + no manual override + peak > 0.15. The fallback to today's full 360° coarse sweep stays in place for all other cases. |
| F-SKY11.2 | experiment failed | Pano→bird's-eye IPM + 2-D rotation IoU registration | **Recorded** in [`plans/F-SKY11.2-pano-birdseye-registration.md`](plans/F-SKY11.2-pano-birdseye-registration.md). The algorithm is sound but monocular SegFormer water classification has limited depth reach (~5-7 m), so the bird's-eye recovers only an inner disc — too small to constrain a rotation against bay-scale satellite shapes. Module + demo deleted 2026-05-24; plan post-mortem preserved. |
| F-SKY12 | Phase A — verifier only | Depth Anything V2 on stitched panos → per-match height as an OSM-independent cross-check | **Diagnostic-only**, currently. `depth_estimation.py` calibrates the model's relative depth against OSM anchor distances and emits `depth_height_m` + `depth_disagreement` flag alongside the geometric pinhole-y estimate. Phase A does NOT influence aggregated heights; Phase B (future) would use it for confidence weighting and rescue. Plan: [`plans/F-SKY12-depth-from-panos.md`](plans/F-SKY12-depth-from-panos.md). |
| F-SKY13 | core opt-in | OSM coastline + water polygon overlay on the minimap + coastline-keypoint source for F-SKY11.1 | **Replaces the unreliable HSV satellite-water detector** as the primary coastline ground truth (see feedback memory). `osm_water.py` extracts `natural=coastline` linestrings + water polygons from the existing OSM fetch, clips to a 1 km radius per seed, and samples evenly-spaced points usable as registration keypoints. Plan: [`plans/F-SKY13-osm-coastline-footprints-overlay.md`](plans/F-SKY13-osm-coastline-footprints-overlay.md). |
| F-SKY15 | parallel renderer (added) | HTML diagnostic report — same data sources as the PDF, but a folder of static HTML pages | **Where the tables live now**. The PDF was shrunk dramatically by `pano_only_pdf: true` + dropping per-building text tables; everything that came out of the PDF is rendered into HTML by `html_report.py`. The PDF stays the canonical archival artefact; the HTML is the grep-able / diff-able / AI-readable companion. Plan: [`plans/F-SKY15-html-diagnostic-report.md`](plans/F-SKY15-html-diagnostic-report.md). |

Other strm2stl features (terrain elevation, screen scoring, height
proxy, aggregation) sit outside the F-SKY series and are unchanged by
this plan.

---

## The consolidated pipeline (target end state)

```
INPUTS: sites/<region>.json (bbox + seeds + flags), GOOGLE_MAPS_API_KEY
        OSM (Overpass), MS Buildings (F-SKY8 opt-in), ESRI satellite tiles

REGION STAGE (once per run)
  R1. Load OSM features + convert to BuildingRecord (existing)
  R2. F-SKY8: merge MS Buildings polygons (when enabled)              [opt-in]
  R3. Attach DEM elevations to each building (existing)
  R4. F-SKY13: extract OSM natural=coastline + water polygons,        [auto if
        clip to bbox, sample as keypoints                              region
                                                                       has any
                                                                       water]
        (HSV satellite-water detector is kept on disk in
         coastline_registration.py for offline A/B but is no longer
         the primary keypoint source.)

SEED STAGE (once per seed)
  S1. Resolve seed pano (cached, existing)
  S2. Pre-fetch 12 spin views at URL pitch; screen each
  S3. Compute pitch correction (auto-pitch trigger + widened
        top-clipping trigger)
  S4. Re-fetch 12 spin views at corrected pitch (uniform per spin)
  S5. Run SegFormer on each view -> sky/building/water masks
        (rejected views still retain their image for pano recovery —
         only excluded from per-view registration. F-CLEAN bug fix.)
  S6. Stitch pano + pano water mask (existing stitch_pano_*)

HEADING RECOVERY (one offset per seed)
  H1. F-SKY11.1 pano-coastline-offset sweep                  ──► PRIMARY
      - Inputs: pano water mask, F-SKY13 keypoints, headings_per_col
      - Output: recovered_offset_deg + peak score + flatness sigma
      - GATE (calibrated 2026-05-24 on Cartagena):
          sigma <= 0.10  AND  peak > 0.40
        Tighter than the original 0.10/0.15 because measured wrong
        recoveries had sharp sigma but lower peak (seeds 4/5 came in
        at peak 0.35/0.33 with sigma 0.023/0.130 → wrong by 76°/34°).
      - When the gate passes AND no manual override is set, fold the
        recovery into the joint optimizer's ±15° fine refine.
  H2. Joint anchor IoU optimizer (today's path)              ──► FALLBACK
      - 3° coarse + 0.5° fine sweep maximizing per-building IoU −
        water − miss penalties.
      - Fires when H1 was gated out OR when a manual override is set
        (then the optimizer skips entirely and uses the override).
  H3. sites/<region>.json `anchor_offsets_deg`               ──► MANUAL OVERRIDE
      - Always wins when present (user explicit), never automatic.
      - 2026-05-24 state on Cartagena: seed_1 / seed_4 / seed_5
        require manual overrides; the latest keypoint detector pulls
        seed_1's H1 recovery 38° off truth so the override is back.
        See F-SKY-AUDIT-2026-05-24.md for the diagnosis.

PER-VIEW STAGE (12 views per seed)
  V1. Per-view registration around the recovered anchor (existing
      register_view_to_osm with ±8° refine)
  V2. Project OSM + MS Buildings into the view
  V3. F-SKY7 silhouette detection: dual-baseline contour peaks +
      local-max peaks within continuous mask regions + mask-component
      detection
  V4. F-SKY2 anchored splitting (gated on n_matches >= 3)
  V5. F-SKY6: match_segments_to_buildings with 1:1 Hungarian dedup
      and considered-but-lost diagnostics
  V6. Height extraction per matched segment (pinhole y -> height m,
      existing _building_roof_y_from_mask + similar)
  V7. F-SKY1 floor period: GATED behind compute_floor_period=False
      default. Compute is skipped unless caller opts in.
  V8. F-SKY10 cross-view (colour / width / edges) on each match
      when use_cross_view_scoring is enabled (all three signals exist
      in cross_view.py; combined as 0.5/0.3/0.2). cv blended into the
      matcher score at 0.85/0.15 weights; per-view PDF header surfaces
      `cv̄=X.XX/min=Y.YY` aggregate.
  V9. F-SKY12 depth-from-pano (Phase A): predict Depth Anything V2 on
      the stitched pano, calibrate via OSM anchor distances, emit a
      per-match depth_height_m field + depth_disagreement flag for
      cross-check. Currently does NOT influence aggregated heights.

SEED AGGREGATION
  A1. aggregate_building_heights with cross-seed downweighting
        (existing, unchanged)
  A2. PDF render
      - When pano_only_pdf is set: skip per-view registration pages;
        skip per-building text-table pages (Seed-Derived heights,
        worst-residuals list, CTBUH per-building). PDF becomes the
        compact orientation/QA artefact (~5 MB on Cartagena vs 28 MB).
      - When pano_only_pdf is unset: render everything (legacy mode).
  A3. F-SKY15 HTML render — writes index.html + seed_N.html files
      with embedded minimap PNGs. ALL tabular data lives here now,
      regardless of pano_only_pdf.
```

---

## What has changed since the original plan (2026-05-17 → 2026-05-24)

### Shipped
- **R4** runs F-SKY13 OSM coastline keypoint extraction (HSV
  satellite-water demoted — it was unreliable, see feedback memory)
- **S5** the prefetch retains rejected-by-screening images for
  pano-recovery use only (cache-key bug at the same site also fixed)
- **H1** F-SKY11.1 pano recovery wired as joint-anchor coarse seed
  via `use_pano_coastline_recovery` + `drive_pano_recovery_anchor`
  flags; gate calibrated to sigma ≤ 0.10 AND peak > 0.40
- **V7** F-SKY1 floor period gated off by default (F-CLEAN4)
- **V8** F-SKY10 expanded to 3 signals (colour/width/edges) and
  surfaced in per-view PDF header (F-CLEAN5)
- **V9** F-SKY12 depth-from-pano landed as Phase A verifier
- **A2** `pano_only_pdf: true` flag drops per-view pages + text tables
- **A3** F-SKY15 HTML report renders the tabular data that came out
  of the PDF
- **Module deletions**: `pano_birdseye.py` + script 13 (F-CLEAN6),
  `config.py` (F-CLEAN1), `osm_marker_voronoi_silhouettes` (F-CLEAN2)
- **Boilerplate consolidation**: 8 `_load_site_*` helpers → 1
  `_read_site_config` (F-CLEAN7)

### Demoted
- **F-SKY3** function removed; only the plan doc + git history remain
- **F-SKY10** per-building rerank now diagnostic-only (still wired
  behind `use_cross_view_scoring` opt-in, doesn't drive production)
- **Per-view F-SKY11** kept as the standalone demo only

### Stays the same
- S6 pano stitch, V1–V6 (registration + matching + height extraction),
  A1 (cross-seed aggregation)
- All site-config flags (`use_satellite_footprints`,
  `anchor_offsets_deg`, `negative_seeds`, `max_plausible_height_m`)
  keep their current semantics; new flags layer on top

---

## Signal source-of-truth table

When two F-SKY signals address the same underlying question, the table
says which one wins.

| Question | Primary | Fallback | Diagnostic-only |
|---|---|---|---|
| What is the seed's true geographic heading? | H1 pano coastline (F-SKY11.1) — keypoints sourced from **F-SKY13 OSM coastline** | H2 joint IoU | F-SKY11 per-view sweep |
| Where is the coastline ground truth? | **F-SKY13 OSM `natural=coastline`** | (HSV satellite-water was previously primary; demoted as unreliable) | — |
| Are two adjacent towers one segment or two? | F-SKY2 anchored split | F-SKY7 local-max peak | F-SKY3 Voronoi (deleted) |
| Does this segment match a real building? | F-SKY6 1:1 IoU+containment+width | (none — segment becomes unmatched) | F-SKY10 colour/width/edges |
| What polygon covers this visible tower? | OSM | F-SKY8 MS Buildings | — |
| How tall is the building? | Pinhole roof-y / forward_m | sqrt-area `_height_proxy` | **F-SKY12 Depth Anything V2** (Phase A verifier), F-SKY1 floor period |
| Did this view actually see the building? | matcher's bearing_in_fov + closest_in_bin + plausibility flags (F/B/P) | — | per-view PDF column-coverage strip |
| Where does the per-building data live for review / diff / AI access? | **F-SKY15 HTML report** (`html_report.py`) | — | PDF (visual-only after pano_only_pdf + table-drop) |

The table is the canonical spec — when implementation diverges from
this, either the table or the implementation is wrong.

---

## Phasing (delivery order — refreshed 2026-05-24)

### Phase 0 — F-SKY11.1 algorithm + demo  ✅ DONE
- ✅ `coastline_registration.score_pano_offset_keypoints` +
  `sweep_pano_heading_offset`
- ✅ Standalone demo scripts (since superseded — script 13 is now
  `13_heading_recovery_demo.py`, the older 10/11/12 demos were
  deleted along with `pano_birdseye.py` in F-CLEAN6)
- ✅ Visual confirmation on Cartagena seeds 1/4/5

### Phase 1 — Production integration in `_seed_multiview_registration`  ✅ DONE
- ✅ Pano stitching happens with API-frame headings; F-SKY11.1
  sweeps the pano water mask against F-SKY13 keypoints
- ✅ Gate calibrated 2026-05-24: sigma ≤ 0.10 AND peak > 0.40
- ✅ Manual `anchor_offsets_deg` wins when present (logs the recovery
  for comparison)
- ✅ When no manual override + sharp recovery: replaces the joint
  optimizer's coarse 360° sweep with ±15° fine refine around the
  recovered offset

**Outcome on Cartagena (current state)**: seed_1 / seed_4 / seed_5
still have manual overrides. seed_1's was dropped briefly when its
recovery matched within ±7°, then restored after the latest keypoint
detector shifted the recovery 30° (see F-SKY-AUDIT-2026-05-24.md
diagnosis). The Phase 1 wiring works; the keypoint-detector calibration
is the open issue, not the integration.

### Phase 2 — F-SKY3 / F-SKY10 cleanup  ✅ DONE
- ✅ `osm_marker_voronoi_silhouettes` function REMOVED 2026-05-18
  (was commented out as the call site even before that)
- ✅ F-SKY10 cross-view stayed in the matcher path but is opt-in,
  default-off, **and** all 3 signals (colour/width/edges) implemented
- ✅ F-SKY10 `cv` field surfaced in per-view PDF header (F-CLEAN5)

### Phase 3 — second region scaffolding  ⏳ PENDING (F-CLEAN13)
- ☐ Wire Miami (`sites/miami.json`) to opt into F-SKY8 + F-SKY11.1
  + F-SKY13 + `pano_only_pdf` and measure recovered headings
- ☐ Same for Chicago (`sites/chicago.json`)
- ☐ Capture per-seed recovery accuracy + coverage in STATUS.md
- Estimate: ~1 hour total (3 min/run × 2 regions + inspection)

### Phase 4 — docs + tests  PARTIAL
- ✅ README.md repointed at the consolidation plan + audit
- ✅ STATUS.md rewritten (F-CLEAN9)
- ✅ Stale in-module docs archived (F-CLEAN10, F-CLEAN11) + the
  glass-roof-fix header updated (F-CLEAN12)
- ☐ Unit test for `sweep_pano_heading_offset` on synthetic input
- ☐ Unit test for F-SKY13 OSM coastline extraction edge cases

### Phase 5 — emerging follow-ups (post-audit, 2026-05-24)
- ✅ Investigated why the latest keypoint detector pulled seed_1's
  recovery 30° off truth. Root cause: `score_pano_offset_keypoints`
  averaged all keypoints with equal weight, but far keypoints
  (>200 m) all predict horizon-y identically and don't discriminate.
  Fix: added `max_signal_dist_m=200` parameter that weights each
  keypoint by `min(1, max_signal_dist_m / distance)`. After the fix
  (2026-05-25): seed_1 recovery 112°→142° (within ±15° of truth 135°)
  and seed_5 235°→339° (within ±19° of truth 320°). seed_4 still
  wrong — coastline isn't sufficient signal there.
- ☐ Consider raising the σ gate from 0.10 to ~0.15 once more regions
  have been measured. Current seed_1 σ=0.120 just clears the strict
  threshold so the correct-direction recovery doesn't auto-drive.
- ✅ F-CLEAN8 (2026-05-27): split the 1211-line
  `_seed_multiview_registration` into 5 named helpers
  (`_capture_pano_views`, `_recover_pano_heading`,
  `_recover_anchor_offset`, `_register_views`, `_stitch_pano_composite`);
  the orchestrator is now ~215 LOC.
- ✅ Coverage 8/2/1 → 2/1/7 regression diagnosed (2026-05-26): it's
  an OSM-fetch drift artefact, not a screening regression. Live OSM
  fetch returns 4 new high-rises vs the 2026-05-17 cached snapshot;
  `_propose_standoff_locations` places auto-proposals at different
  lat/lons in response, and those new positions screen worse than
  the previous geometry-driven picks. Screening function itself is
  bit-for-bit unchanged. See F-SKY-AUDIT-2026-05-24.md for the full
  table comparison.
- ☐ Persist auto-proposal positions so coverage is stable across
  runs (one-line change in `_screen_locations` to read/write a
  per-region cache file similar to `seed_resolution_cache.json`).

---

## Risks / open questions called out

1. **Phase 1 gate threshold.** The σ < 0.10 cutoff is borrowed from
   the pano demo's Cartagena seed_5 numbers (σ = 0.054 there). Once
   we have data from seed_1/2/4 + Miami we'll calibrate the actual
   threshold; until then the gate runs but the manual override still
   wins, so there's no production risk from a wrong threshold.
2. **F-SKY10 demotion is reversible** — the matcher's
   `cross_view_scorer` parameter stays, just defaults to None. If
   future per-building signal experiments succeed they can pass a
   scorer in.
3. **F-SKY1 is wired throughout `estimate_heights_from_registration`
   today**. Demoting it to diagnostic-only means leaving the floor
   period computation but never letting it influence the per-view
   height estimate. The current code structure already supports this
   — `inferred_distance_m` is a read-out field, not a decision input.
   Verify before declaring done.
4. **Re-running SegFormer for the demo script (12_pano_coastline_demo)
   is slow.** The production integration in `_seed_multiview_registration`
   reuses the already-cached per-view masks; the demo doesn't have
   that luxury and re-runs SegFormer per spin view. Acceptable
   for an inspector; not a perf-relevant choice.

---

## What this plan does NOT touch

- **City2stl height/production stack** (`city2stl/height/*`) is
  unrelated to the skyline research branch.
- **Composite DEM** (project_composite_dem) and similar terrain work.
- **MobileSAM (F-SKY5)** stays pending — it would replace F-SKY7's
  silhouette detector if pursued, but is out of scope for this
  consolidation.
- **OSM polygon de-duplication policy** — the current 0.5 area-IoU
  threshold between MS and OSM stays; F-SKY8's policy is unchanged.

---

## When this is done

End state in two sentences:

> The skyline pipeline takes a region + seeds, recovers each
> seed's true heading from the satellite coastline (pano-level), uses
> that heading to register OSM + MS Building polygons against
> SegFormer's per-view silhouettes (with F-SKY2 splitting + F-SKY6
> 1:1 matching + F-SKY7 dual-baseline peak detection), and aggregates
> the resulting per-building heights across seeds. F-SKY1, F-SKY3,
> F-SKY10, and per-view F-SKY11 remain as diagnostic / inspector
> tools but no longer influence the production heights.
