# F-CLEAN14 — Split the over-large skyline source files

**Status:** Phase H + R done + smoke-validated (2026-06-07). Phase P2 done;
P1 deferred. Two paid Cartagena smoke runs passed with **identical** output
(673 extracted buildings, 63 views, coverage good:3) before vs after the
pipeline.py changes — refactor confirmed behaviour-neutral end-to-end.
**Owner handle:** F-CLEAN14
**Origin:** docs/AUDIT-2026-06-07.md, Part 2 finding #3.

> **Persistence note:** This file is the source of truth for the refactor.
> Each step has a checkbox. On resume, read the checkboxes to see what's
> done, re-run the verification gate, then continue at the first unchecked
> step. Do NOT trust memory or session todos over this file.

## Goal

Break the three over-large skyline files into focused modules **without any
behaviour change**:

| File | Before | Target |
|---|---|---|
| `region_pdf.py` | 6,524 LOC, 5 concerns | ~6 modules, each one concern |
| `html_report.py` | 2,816 LOC | 2 modules (HTML assembly + PNG plot renderers) |
| `pipeline.py` | 4,124 LOC | in-place: decompose 2 giant functions (full package split deferred) |

## Hard invariants (must hold after every step)

1. **Public API unchanged.** These imports must keep working verbatim:
   - `from city2stl.skyline.region_pdf import run_region_pdf_report`
   - everything re-exported in `city2stl/skyline/__init__.py`
   - the `pipeline.py` symbols listed in README "Module surface"
   Achieve this with re-exports from the original module path.
2. **Verbatim moves only.** Cut a function, paste it unchanged, fix its
   imports. No logic edits in the same step as a move.
3. **Verification gate passes before moving to the next step** (see below).

## Verification gate (run after EACH step)

```powershell
cd "d:/OneDrive/Documents/Projects/3D Maps/Code/strm2stl"
# 1. import smoke — public API still resolves
"C:\venvs\strm2stl\Scripts\python.exe" -c "from city2stl.skyline.region_pdf import run_region_pdf_report; import city2stl.skyline; from city2stl.skyline import pipeline, region_pdf, html_report; print('imports OK')"
# 2. unit tests (covers pipeline.py + html_report.py + cv math)
"C:\venvs\strm2stl\Scripts\python.exe" -m pytest tests/test_skyline*.py -q
```

Expected: `imports OK` + **129 passed, 1 skipped**.

> **Residual risk:** the 130 unit tests do NOT cover orchestration in
> `region_pdf.py`. The only full check is a paid Cartagena run
> (`python -m city2stl.skyline.scripts.08_region_skyline_pdf --region
> Cartagena`, ~3 min, ~$0.10 API). Recommend ONE such run after the whole
> region_pdf split lands (step R7). Not run per-step to avoid cost.

---

## Phase H — html_report.py (do FIRST — it's unit-tested, proves the pattern)

- [x] **H1.** Create `report_plots.py`. Move the pure PNG/plot renderers:
  `_render_pano_reconstruction_png`, `_render_pano_bearing_scan_png`,
  `_render_pano_minimap_polar_png`, `_render_view_reconstruction_png`,
  `_render_pano_heights_polar_png`, `_render_pano_segformer_overlay_png`,
  `_render_seed_minimap_png`, `_build_osm_nearest_per_degree`,
  `_bearing_xcorr_offset`, `_draw_pano_north_line_inplace` (+ any private
  helper used only by these). `html_report.py` imports them back.
- [x] **H2.** Verification gate green (129 pass, 1 skip). html_report.py 2882->1217 LOC; new report_plots.py 1708 LOC.

## Phase P — pipeline.py (in-place function decomposition only) — DEFERRED

**Decision (2026-06-07):** deferred to a focused follow-up, done AFTER Phase R.
Rationale: unlike the verbatim file-moves in H and R, decomposing a 458-line
function edits code *inside* the function (parameter threading, local-variable
lifetimes) — higher risk of a subtle behaviour change. `pipeline.py` is also
cohesive and the best-tested module, so the readability payoff is the lowest
of the three files. Better as its own pass with full attention than bundled
into a session also moving 6.5k lines of `region_pdf.py`.

- [x] **P2 (done, 2026-06-07).** `detect_buildings_from_mask` (320 LOC)
  decomposed into 2 helpers via verbatim line-extraction:
  `_component_gradient_col_signal` (the Sobel/band gradient precompute) and
  `_component_peak_columns` (the gradient-subdivided + contour + col_counts
  peak union/dedup/support/cap). The function body dropped ~120 LOC. Gate green
  + undefined-name check green. Also fixed a latent `logger` NameError
  (F-SKY12 depth except-branches referenced `logger`; module never defined one).
- [ ] **P1 (NOT done — left for a dedicated pass).**
  `estimate_heights_from_registration` (458 LOC) is a single per-building loop
  with ~8 early-`continue` gates over heavily shared local state (trace,
  building_mask, contour, f_px, cy, cam_z, projections, _all_x/_all_fwd).
  Safe extraction needs converting gates into a helper that returns
  reason-or-None — real refactoring judgment, not a mechanical move — and it is
  the most critical function with NO direct unit test (only the paid smoke run
  validates it). Deliberately deferred rather than risk subtle behaviour change
  under a multi-task session. Do it alone, with a height-output diff harness.
- [ ] **P3.** Full `pipeline/` package split stays DEFERRED.

## Phase R — region_pdf.py (the main payoff — leaf-first, 7 steps)

Dependency DAG (extract in this order so no circular imports):
`region_types` ← `region_data` / `streetview_io` ← `seed_selection` /
`pano_registration` ← `region_render` ← `region_pdf`.

- [x] **R1.** `region_types.py` — dataclasses (145 LOC). Gate green.
- [x] **R1b (added).** `region_config.py` (73 LOC) — F-SKY env flags +
  `_SEGMENT_PALETTE`, extracted so render AND pano_registration can share them
  without a cycle. (Not in the original plan; needed once R5/R6 both referenced
  the flags.) Gate green.
- [x] **R2.** `region_data.py` (557 LOC) — `_load_region_bbox`, `_load_osm_for_region`,
  `_osm_to_building_records`, `_parse_height`, `_feature_centroid`,
  `_feature_rings`, `_extract_high_rises`, `_distance_m`, `_bearing_deg`,
  `_drop_buildings_in_water`, `_fetch_elevations`, `_attach_building_terrain`,
  `_read_site_config` + all `_load_site_*` readers.
- [x] **R3.** `streetview_io.py` — `_resolve_api_key`,
  `_streetview_signing_enabled`, `_default_streetview_image_size`,
  `_sign_streetview_url`, `_streetview_metadata`, `_streetview_image`,
  `_is_no_imagery_placeholder`, `_meta_location`, `_extract_pano_id`,
  `_parse_streetview_url`.
- [x] **R4.** `seed_selection.py` — `_propose_standoff_locations`,
  `_screen_score_from_image`, `_screen_score_from_image_uncached`,
  `_auto_replace_bad_seeds`, `_screen_locations`.
- [x] **R5.** `region_render.py` — `_render_pdf`, `_render_seed_view_page`,
  `_render_stitched_pano_page`, `_draw_view_minimap`,
  `_draw_osm_coastline_overlay`, `_draw_location_map`, `_registration_overlay`,
  `_negative_seed_views`, `_count_seg_flags`, `_load_known_heights`,
  `_StepTimer`.
- [x] **R6.** `pano_registration.py` — `_capture_pano_views`,
  `_recover_pano_heading`, `_recover_anchor_offset`, `_register_views`,
  `_smooth_matches_across_views`, `_smooth_pano_matches_against_views`,
  `_stitch_pano_composite`, `_pano_sliding_window_split`,
  `_multires_sam_instances`, `_multires_pano_refine`,
  `_split_by_depth_discontinuity`, `_seed_multiview_registration`.
  (Hardest — most internal coupling; done last while region_pdf is smallest.)
- [x] **R7 (code-complete).** `region_pdf.py` is now the wiring layer:
  `run_region_pdf_report` (349 LOC) + 7 re-import blocks, **698 LOC total**
  (was 6,524). `__init__.py` unchanged (it only re-exports pipeline symbols;
  `run_region_pdf_report` is imported via `region_pdf` which still works).
  Verification gate green + custom undefined-name AST check green on all 10
  modules. **PENDING:** one paid Cartagena smoke run (~$0.10) — held for user
  go-ahead since it spends Google API quota. Static + unit validation is
  strong but does not exercise full orchestration paths.

## Doc updates when done

- [x] Refresh line citations + file map in `city2stl/skyline/docs/AGENT-GUIDE.md`.
- [x] Update the "Core Files" table + Files tree in `city2stl/skyline/README.md`.
- [x] Mark F-CLEAN14 `done` in `docs/proposals.md`.
- [x] Update `docs/AUDIT-2026-06-07.md` Part 5 item #3 as completed.

## Progress log (append one line per completed step)

- 2026-06-07: plan written; proposal entry F-CLEAN14 added.
- 2026-06-07: Phase H done. report_plots.py extracted (16 renderers + POLAR_MAX_M); verbatim moves, one-directional re-import. Gate green.
- 2026-06-07: Phase R done (R1-R7 code-complete). region_pdf.py 6524->698. New modules: region_types(145), region_config(73), region_data(557), streetview_io(331), seed_selection(637), region_render(1877), pano_registration(2571). Dependency DAG acyclic. Built an AST-based extractor (moves named funcs/consts verbatim + adds re-import) and an AST undefined-name checker.
- 2026-06-07: BUG FIX surfaced by the undefined-name check: original region_pdf.py referenced `logger` in two osm_water-unavailable branches but never defined it (latent NameError, never hit). Added `logger = logging.getLogger(__name__)` to region_render.py.
- 2026-06-07: Doc refresh done (AGENT-GUIDE/README/proposals/audit).
- 2026-06-07: Phase P2 done — detect_buildings_from_mask split into
  _component_gradient_col_signal + _component_peak_columns. Fixed a 2nd latent
  `logger` NameError (pipeline.py F-SKY12 except-branches). P1 (estimate_heights)
  deferred: too entangled / untested for a safe mechanical move.
- 2026-06-07: SMOKE VALIDATED. Two full Cartagena runs (b1), exit 0, identical
  output (673 buildings / 63 views / good:3) pre- and post-pipeline.py changes.
  Refactor is behaviour-neutral.
- 2026-06-07: COMMITTED on branch `refactor/f-clean14-skyline-split` (commit
  02d835d, 23 files). `.gitattributes` was committed in the same commit, which
  by itself cleaned the CRLF phantom — `git status` is now fully clean and
  `git add --renormalize .` found nothing further, so no separate normalize
  commit was needed.
- 2026-06-07: Commit hygiene findings while landing it: (a) fixed the
  pre-commit hook's dead Python path (pointed at the removed OneDrive .venv 3.11
  → now prefers C:\venvs\strm2stl); (b) installed the missing `triangle` dep
  (20 export/puzzle tests were failing on ModuleNotFoundError). (c) The
  full-suite hook still flaky-segfaults on native code in unrelated height/
  raster tests (different test each run) — committed with --no-verify per
  explicit user approval; the skyline changes are independently validated.
  REMAINING: Phase P1 (estimate_heights decomposition) only.
