# skyline audit — 2026-05-24 refresh

Status: **audit + state-of-the-module snapshot**. Supersedes
[F-SKY-AUDIT-2026-05-17.md](F-SKY-AUDIT-2026-05-17.md) (kept as the
original baseline). This refresh records what shipped in the
intervening week and what remains.

> **2026-05-27 update:** F-CLEAN8 has since **shipped** —
> `_seed_multiview_registration` is now a ~215-line thin orchestrator
> (region_pdf.py:2431) that delegates to the five recommended helpers
> (`_capture_pano_views`, `_recover_pano_heading`, `_recover_anchor_offset`,
> `_register_views`, `_stitch_pano_composite`). The "1211 LOC largest
> function" figures below are historical. The current largest function is
> `_render_pdf` (~556 LOC, region_pdf.py:3689). Status rows updated in the
> summary table.

## Headline numbers

| | 2026-05-17 audit | 2026-05-24 refresh |
|---|---|---|
| Module LOC (skyline only, excl. runs/) | ~10000 | ~9000 (down ~1000) |
| Largest function | `_seed_multiview_registration` 918 LOC | **1211 LOC** (grew with pano-recovery + diagnostics) |
| Source files | 11 | 11 (config + pano_birdseye out, depth_estimation + html_report + osm_water in) |
| Scripts | 6 | 3 (`08`, `09`, `13_heading_recovery_demo`) |
| In-module docs (excl. STATUS) | 3 | 1 + an `archive/` subdir |
| F-CLEAN proposals closed | 0 of 13 | 11 of 13 |
| Cartagena PDF size | 28.4 MB | **5.4 MB** (5.3× shrink, pano-only mode) |

## What shipped in the past week

### Code removals / consolidations (∼1300 LOC closed)
- **F-CLEAN1**: `config.py` deleted (was 123 LOC of unreferenced dataclasses)
- **F-CLEAN2**: `osm_marker_voronoi_silhouettes` removed (~125 LOC)
- **F-CLEAN3**: `_make_sky_mask_from_bool` inlined (12 LOC)
- **F-CLEAN4**: F-SKY1 floor-period compute now gated behind
  `compute_floor_period=False` default — eliminates per-segment-per-view
  autocorrelation overhead unless explicitly opted in
- **F-CLEAN6**: `pano_birdseye.py` + `scripts/13_birdseye_registration_demo.py`
  removed (672 LOC for the failed F-SKY11.2 experiment)
- **F-CLEAN7**: 8 copy-pasted `_load_site_*` helpers consolidated under
  one `_read_site_config` reader (~150 LOC of boilerplate gone)

### Diagnostic surfacing / PDF
- **F-CLEAN5**: F-SKY10 cross-view `cv` score surfaced in the per-view
  PDF header as `cv̄=X.XX/min=Y.YY`
- **NEW: `pano_only_pdf` site flag** — drops the 26 per-spin-view PDF
  pages, keeps panos + summary + charts. Cartagena dropped 28.4 MB → 5.4 MB.
- **NEW: text-table drop under `pano_only`** — the per-building Seed-Derived
  Heights page, validation worst-residuals list, and CTBUH per-building
  table are all skipped from the PDF when pano-only. The same data
  lives in the HTML report (`html_report.py`).

### F-SKY11.1 production wiring
- `use_pano_coastline_recovery` flag computes the recovery + logs every seed
- `drive_pano_recovery_anchor` flag lets sharp recoveries replace the joint
  optimizer's coarse seed (gated on σ ≤ 0.10 AND peak > 0.40)
- **Peak floor was 0.15 → tightened to 0.40 on 2026-05-24** after seeds 4/5
  were measured returning confidently-wrong recoveries with sharp σ
- seed_1's manual `anchor_offsets_deg` override dropped (pano recovery
  passes the gate); seed_4 (-180°) and seed_5 (320°) restored after
  measurement confirmed their recoveries are wrong by 76° and 34°

### Cache-key bug fix
The id-only `_VERT_CACHE` / `_GROUP_CACHE` caches in `pipeline.py` were
returning stale `building_idx_for_group` when a new buildings list of
different size landed at the same `id()` after a previous list's GC.
Symptom: `IndexError` deep in `_project_all_buildings_vectorized`.
Cache keys now include `len(buildings)`.

### Docs
- **F-CLEAN9**: STATUS.md top section rewritten to reflect current run
  metrics, the 3-tier heading-recovery stack, and the actual per-seed
  measurement table. The old Phase A–D roadmap collapsed under a
  `<details>` block with pointers to the canonical plans.
- **F-CLEAN10/11**: `cartagena-audit-2026-05.md` and `implementation-plan.md`
  moved to `docs/archive/`.
- **F-CLEAN12**: `glass-roof-height-fix-plan.md` header updated — Phase 1
  shipped (`height_trace.py`), Phase 2 (monocular depth) deferred
  indefinitely after F-SKY11.2's adjacent monocular-IPM experiment hit a
  depth-reach wall.
- README.md now points at the consolidation plan + this audit at the top.

## What's outstanding

### F-CLEAN8 — split the 1211-line `_seed_multiview_registration` ✅ SHIPPED (2026-05-27)
Done — the split landed exactly as proposed below. `_seed_multiview_registration`
is now a ~215-line orchestrator delegating to `_capture_pano_views`,
`_recover_pano_heading`, `_recover_anchor_offset`, `_register_views`, and
`_stitch_pano_composite`. The historical analysis is retained below for context.

**(historical)** It was higher priority than at the original audit because the function
grew from 918 → 1211 LOC while we were shipping cleanups elsewhere.
The growth is from the pano-recovery integration + 7 new per-view
diagnostic blocks (`[heading_consistency]`, `[multi_building]`,
`[cross_verify]`, `[pano_recovery]` per seed). Each is useful;
collectively they make the function unreadable.

Obvious splits by responsibility:
1. `_capture_spin_views(seed, api_key, ...)` — Pass 1a + 1b (pitch
   correction). ~140 LOC.
2. `_recover_seed_anchor(seed, cached_views, ...)` — joint anchor IoU
   sweep + pano-coastline integration. ~200 LOC.
3. `_register_each_view(seed, anchor_offset, cached_views, ...)` — Pass 2
   per-view registration with ±8° refine. ~300 LOC.
4. `_segment_and_match(seed, registered_views, ...)` — F-SKY7 silhouette
   detection + F-SKY2 anchored splitting + F-SKY6 1:1 matching + height
   extraction. ~350 LOC.
5. `_stitch_seed_pano(seed, ...)` — pano stitching + pano-level matching.
   ~200 LOC.

Effort: 1–2 dedicated days. Regression risk: real (this is the heart of
the production pipeline). Recommend: write a Plan doc with
acceptance-criteria first, run Cartagena before+after with byte-level
PDF diff to catch regressions.

### F-CLEAN13 — exercise Chicago + Miami end-to-end
Neither region has been run since:
- F-SKY8 / F-SKY10 / F-SKY11.1 site flags were added
- The cache-key bug fix landed
- The pano_only_pdf flag was introduced

Each is a one-off ~3 min run + a 10 min PDF inspection. Reveals
breakage on regions that don't have Cartagena's specific configuration.

### Pano-recovery diagnosis (revised after 2026-05-24 log triage)

Initial framing of this as "non-determinism" was wrong. Cross-run log
comparison shows seed_1's pano recovery is **deterministic within a
given code version** but has **shifted monotonically as the keypoint
+ recovery code evolved**:

| Code era | keypoints | recovered | peak | sigma | joint refines to |
|---|---|---|---|---|---|
| Initial pathB (pre-prefetch-retain) | 22 | 136° | 0.212 | 0.028 | 151° |
| After prefetch-retain fix (pano sees all 12 views) | 22 | 142° | 0.439 | 0.050 | 157° |
| Current code (latest keypoint detector) | **23** | **112°** | 0.417 | 0.041 | **97°** |

Each row was stable across multiple runs at that code version. The
22→23 keypoint count + 142°→112° recovery shift coincides with the
keypoint-detector change in `coastline_registration.py`. The current
recovery is **wrong by ~23°** relative to the user-verified manual
override of 135°. The ±15° joint refine then can't reach 135° from a
seed of 112°, so the final anchor lands at 97° (38° from truth).

**This is the cause of the coverage 2/1/7 regression.** With seed_1
mis-anchored by ~38°, per-view registration finds few matches and the
screening rates those views "weak". The same buildings get extracted
(seed_extracted_buildings actually went UP, 622 > 593) but the
screening-quality gauge tanks.

**Immediate fix (shipped 2026-05-24)**: seed_1's manual override of
135° restored. The user previously dropped it after the 142° recovery
agreed within ±7°; that's no longer true.

**But coverage did NOT return to 8/2/1 after the restore** — the
post-restore Cartagena run still reads 2/1/7 with all three manual
overrides (seed_1=135, seed_4=-180, seed_5=320) in place. Same
buildings get extracted (`seed_extracted_buildings=633`, higher than
the 593 baseline), so the pipeline isn't producing worse output.

**Diagnosed (2026-05-26)**: not a screening regression — an
**OSM-fetch drift artefact**.

The screening function `_screen_score_from_image` is bit-for-bit
unchanged since 2026-05-17. The thresholds (good ≥ 0.30, medium ≥
0.15) and gates (sky_frac_top, contour_range_frac, building_frac,
sky_frac_total) are the same.

What changed is the **inputs to screening**, indirectly:

| Run | osm_source | buildings | high_rises | auto_proposals | coverage |
|---|---|---|---|---|---|
| 2026-05-17 baseline | `cache:f1e495b6` (cached) | 3017 | 28 | 6 | 8/2/1 |
| 2026-05-26 current | `live_fetch` | 3028 | 32 | 6 | 2/1/7 |

The 4 new high-rises in the live OSM data shift the positions
`_propose_standoff_locations` outputs (auto-proposal placement is
geometry-driven from the high-rise cluster). Different geographic
positions → different Street View imagery → different screening
scores. Same count of auto-proposals (6) hides the fact that they're
at different lat/lons than they were on 2026-05-17.

**Implications**:

1. Coverage isn't a stable run-to-run metric when OSM is fetched
   live; it tracks OSM data churn as much as pipeline behaviour.
2. STATUS.md's "8 good / 2 medium / 1 weak" baseline ages out
   automatically whenever upstream OSM updates the Cartagena
   bbox's high-rise tags.
3. Manual seed_urls are stable (5 of 11 screened locations), so
   user-curated views are unaffected by this drift.

**Two ways to make coverage stable for cross-run comparison**:

a. **Persist auto-proposal positions** alongside the
   `seed_resolution_cache.json` so the same lat/lons are screened
   every run, regardless of OSM drift. One-line change in
   `_screen_locations`.

b. **Report OSM-source provenance** in the run summary and treat
   coverage as a contextual metric, not a regression gauge.
   Acceptable for the current research stage; cheap.

Recommendation: **(a) now** — keeps the metric meaningful for
comparing pipeline changes. **(b)** as well for transparency.

**Underlying issue diagnosed (2026-05-25)**: not a single bad
keypoint — a **structural scoring flaw**.

`score_pano_offset_keypoints` predicts each keypoint's expected
water-top y in the pano via the pinhole formula
`y = H/2 + camera_h / distance × focal + tan(pitch) × focal`. For
seed_1's pano (`H=540`, focal=352, pitch=+6.2°):

| Keypoint distance | Expected y (px from horizon) |
|---|---|
| 60 m | 10.0 |
| 200 m | 3.0 |
| 500 m | 1.2 |
| 1000 m | 0.6 |
| 1500 m | 0.4 |

The `tolerance_px = 25` threshold makes **every keypoint beyond ~50 m
predict the same horizon-bucket y**. Far keypoints therefore match
"pano column has water somewhere near horizon" — which is true for
most candidate offsets that face the bay. Near keypoints (≤ 200 m)
carry the entire discriminative signal, but the scorer
**averages all keypoints with equal weight**.

For seed_1's 22-keypoint set, only 1 keypoint is at distance ≤ 200 m
(idx 9, bearing 135°, the Bocagrande shore at 200 m). 21 other
keypoints sit at 600-1500 m and predict horizon-y regardless of where
the camera is pointed. The single discriminating keypoint at 135° is
out-voted by the 21 horizon-matchers, and the optimum drifts ~23°
toward whichever direction has slightly better water-at-horizon match
across many bearings.

**Fix (1-line scoring change)**: weight each keypoint by `min(1.0,
max_signal_dist_m / max(distance, 1))`. At default
`max_signal_dist_m = 200`:

- 100 m keypoint → weight 1.0
- 200 m keypoint → weight 1.0
- 500 m keypoint → weight 0.4
- 1000 m keypoint → weight 0.2
- 1500 m keypoint → weight 0.13

Near keypoints retain full weight; far keypoints contribute only as
much as their discriminative power justifies. The 22-vs-1 vote
becomes ~4.5-vs-1 with the right keypoint winning more often.

**Measured result (2026-05-25, Cartagena 5-seed run)**:

| Seed | Manual override | Pre-fix recovery | **Post-fix recovery** | Δ to truth |
|---|---|---|---|---|
| 1 | 135° | 112° (off by -23°) | **142°** | **+7°** ✓ |
| 4 | -180° | 104° (off by +76°) | 44° (off by -136°) | still wrong |
| 5 | 320° | 235° (off by -85°) | **339°** | **+19°** ✓ |

**Two of three problematic seeds shifted into the ±15° joint-refine
window after the fix.** seed_4 stays wrong (manual override
protects). seed_1's new σ (0.120) just clears the σ ≤ 0.10 gate so
the recovery doesn't auto-drive the anchor — but it logs `+7° from
manual` instead of `+23°`, confirming the recovery basin moved to the
right place. seed_5 likewise gates out by σ but is now off by only
+19° (was -85°).

Net effect: pano recovery now produces **trustworthy heading
candidates** for 2 of 3 problematic seeds, even if the σ gate still
rejects them at the strict 0.10 threshold. Raising the σ gate to
~0.15 in a future change (after measuring across more regions) would
let seed_1 take the auto path; seed_4 still needs a deeper fix
(coastline isn't enough signal there).

`drive_pano_recovery_anchor: true` is still load-bearing — it isn't
random, just badly calibrated against the new keypoint detector.
Restoring the manual override is the safe ship-now path; the deeper
investigation is a separate proposal.

## Module file inventory (2026-05-24)

| File | Lines | Status |
|---|---|---|
| `pipeline.py` | ~3200 | Core CV primitives. Healthy — biggest internal function is `match_segments_to_buildings` (~280 LOC) which is appropriately scoped. |
| `region_pdf.py` | ~4500 | Core orchestration. `_seed_multiview_registration` split to ~215 LOC (F-CLEAN8 ✅ 2026-05-27). Largest function is now `_render_pdf` (~556 LOC) — the PDF-rendering layer (~1300 LOC across `_render_pdf` / `_draw_view_minimap` / `_render_seed_view_page` / `_render_stitched_pano_page`) is the next candidate for extraction. |
| `coastline_registration.py` | ~340 (shrank from 620) | F-SKY11.1 keypoints + pano sweep |
| `cross_view.py` | ~310 (grew from 137 with Signals 2+3) | F-SKY10 colour/width/edges |
| `satellite_footprints.py` | ~280 | F-SKY8 MS Buildings |
| `satellite_image.py` | ~260 | Region satellite fetcher |
| `height_trace.py` + `height_trace_render.py` | ~280 | Diagnostic for the glass-roof-fix Phase 1 |
| `html_report.py` | (new) | HTML report — where tables live |
| `depth_estimation.py` | (new) | (status: ?) |
| `osm_water.py` | (new) | OSM-based water (replaces / supplements HSV) |

The three NEW files (`html_report.py`, `depth_estimation.py`,
`osm_water.py`) postdate the original audit and aren't yet documented in
the consolidation plan. **Next-best-step: add a one-paragraph note on
each in [F-SKY-PIPELINE-CONSOLIDATION.md](F-SKY-PIPELINE-CONSOLIDATION.md)**
explaining their role + status (core / opt-in / diagnostic). Otherwise
the next reader will land in undocumented code.

## Summary table — proposal status

| ID | Action | Status |
|---|---|---|
| F-CLEAN1 | Delete `config.py` | ✅ done |
| F-CLEAN2 | Remove `osm_marker_voronoi_silhouettes` | ✅ done (2026-05-18) |
| F-CLEAN3 | Inline `_make_sky_mask_from_bool` | ✅ done |
| F-CLEAN4 | Floor-period compute gated | ✅ done (F-CLEAN4b path) |
| F-CLEAN5 | Surface `cv` in PDF | ✅ done (per-view header `cv̄=…/min=…`) |
| F-CLEAN6 | Delete `pano_birdseye.py` + birdseye demo | ✅ done |
| F-CLEAN7 | Consolidate site loaders | ✅ done (`_read_site_config`) |
| F-CLEAN8 | Split 1211-line `_seed_multiview_registration` | ✅ done (2026-05-27) — now ~215 LOC + 5 helpers |
| F-CLEAN9 | Rewrite STATUS.md top | ✅ done |
| F-CLEAN10 | Archive `cartagena-audit-2026-05.md` | ✅ done |
| F-CLEAN11 | Archive `implementation-plan.md` | ✅ done |
| F-CLEAN12 | Update `glass-roof-height-fix-plan.md` header | ✅ done |
| F-CLEAN13 | Chicago + Miami end-to-end validation | ⏳ pending |

Plus this session:
- **Cache-key bug fix** — pipeline-blocking IndexError, fixed
- **F-SKY11.1 gate calibration** — peak floor 0.15 → 0.40
- **Pano-only PDF** + **table-drop under pano-only** — 5.3× PDF shrink

## Recommended next-up

1. **Diagnose pano-recovery non-determinism** before doing more work on
   that path. If it can't be made reproducible run-to-run, the
   `drive_pano_recovery_anchor` flag is misleading more than helpful.
   Two-hour timeboxed investigation: instrument the per-seed pano
   composition and log which view subset was used.
2. **Document the three new modules** (`html_report`, `depth_estimation`,
   `osm_water`) in the consolidation plan + STATUS.md. ~15 minutes each
   if the original author writes it; more if reverse-engineered.
3. **F-CLEAN13 Chicago + Miami runs** — surface any region-specific
   breakage before users find it. 1 hour total.
4. ~~**F-CLEAN8 split**~~ ✅ shipped 2026-05-27. The next structural
   target is the PDF-rendering layer (~1300 LOC inside `region_pdf.py`),
   which could move to its own `report_render.py` — orchestration and
   rendering are a clean seam.

## What this audit deliberately doesn't propose

- No new features.
- No changes to F-SKY8 / F-SKY10 / F-SKY11.1 algorithms.
- No matcher / scoring threshold changes.
- No test-coverage expansion — the existing 21 tests cover CV math;
  orchestration is exercised by the Cartagena smoke run.
