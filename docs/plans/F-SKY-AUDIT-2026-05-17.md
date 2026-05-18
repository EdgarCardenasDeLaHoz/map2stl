# skyline_cv audit — 2026-05-17

Status: **audit only**. No code changes in this iteration. Items below
are individually small but each has its own proposal entry so cleanup
work can be picked up incrementally.

## Why audit now

After F-SKY1..F-SKY11.2 + the consolidation pass, the module has
~10000 LOC across 9 source files + 4 demo scripts + 11 F-SKY plan
docs + 4 in-module docs. Several pieces are dead, redundant, or stale.
This document catalogues them with proposed cleanup actions so the
next round of work hits the highest-leverage trims first.

## Module inventory (lines, status)

| File | Lines | Status |
|---|---|---|
| `pipeline.py` | 3150 | **Core** — CV primitives, projection, matcher, height extraction |
| `region_pdf.py` | 3703 | **Core orchestration + PDF** — biggest file; one 918-line function inside |
| `coastline_registration.py` | 620 | **Active** — F-SKY11 keypoints + F-SKY11.1 pano scorer (Path B) |
| `satellite_footprints.py` | 280 | **Active** — F-SKY8 MS Buildings merge |
| `satellite_image.py` | 259 | **Active** — region satellite fetcher (shared by F-SKY8 / F-SKY11 / F-SKY10) |
| `pano_birdseye.py` | 215 | **Dead-end** — F-SKY11.2 failed experiment (kept on disk) |
| `height_trace_render.py` | 189 | **Diagnostic** — used by script 09 only |
| `cross_view.py` | 137 | **Conditionally wired, default off** — F-SKY10 colour scorer, no UI consumer |
| `config.py` | 123 | **DEAD** — `SiteSpec`/`ViewpointSpec`/etc. unreferenced; predates JSON sites/ |
| `height_trace.py` | 96 | **Diagnostic** — used by script 09 + a unit test |
| `__init__.py` | 19 | re-exports two symbols |

| Demo / entry script | Lines | Status |
|---|---|---|
| `scripts/08_region_skyline_pdf.py` | 47 | **Core entry point** |
| `scripts/09_height_trace.py` | 98 | **Diagnostic** — Phase 1 of glass-roof-fix |
| `scripts/10_cross_view_demo.py` | 390 | **Diagnostic** — F-SKY10 per-building inspector (signal demoted) |
| `scripts/11_coastline_demo.py` | 433 | **Diagnostic** — F-SKY11 per-view keypoints inspector |
| `scripts/12_pano_coastline_demo.py` | 366 | **Diagnostic** — F-SKY11.1 pano-keypoint inspector |
| `scripts/13_birdseye_registration_demo.py` | 457 | **Dead-end** — F-SKY11.2 demo of failed approach |

## Dead code (purely removable, zero blast radius)

### D1. `config.py` (123 LOC)

Defines `ViewpointSpec`, `SiteSpec`, `RunSpec` dataclasses + `miami_site()`,
`cartagena_site()` factories. **Nothing in the codebase imports any of
them**. They predate the JSON-driven `sites/<region>.json` system. The
hard-coded `cartagena_site()` factory has been superseded by
`sites/cartagena.json` which the JSON loader reads.

→ **Proposal F-CLEAN1: delete `config.py`** (proposal entry below).

### D2. `osm_marker_voronoi_silhouettes` (~122 LOC in `pipeline.py`)

F-SKY3 function, **disabled in 2026-05-16 after MAE regression**. The
only call site (`region_pdf.py` lines around 2086) is commented out.
The plan note says "kept on module surface for offline A/B"; nothing
calls it; nothing tests it.

→ **Proposal F-CLEAN2: remove the function**, keep a one-liner
breadcrumb in `pipeline.py` pointing at `docs/plans/F-SKY3-osm-marker-instances.md`.

### D3. `_make_sky_mask_from_bool` (~12 LOC in `pipeline.py`)

Trivial single-call helper that wraps a bool→uint8 cast. Inline it
into `detect_skyline_contour`'s one call site and delete.

→ **Proposal F-CLEAN3: inline + delete** (low priority — 12 lines).

## Compute-but-never-use (real performance overhead)

### O1. F-SKY1 floor-period diagnostic

`_floor_period_for_building` runs per matched segment per view and
populates four fields on `RegisteredBuildingEstimate`: `floor_period_px`,
`floor_confidence`, `inferred_distance_m`, `inferred_height_m`. **None
of these are read by `region_pdf.py` rendering or by the aggregation
step.** The plan promised "diagnostic side-channel column in the
per-view diag table" — never built.

Cost: one autocorrelation per building per view across the run. On
Cartagena that's ~600 estimates × O(rows²) per estimate; non-trivial
fraction of total run time.

Two clean exits:
- **F-CLEAN4a**: actually surface the field somewhere (per-view diag
  table or aggregate page) — keep computing.
- **F-CLEAN4b**: drop the four fields and gate the
  `_floor_period_for_building` call behind a `compute_floor_period`
  parameter that defaults to False.

Recommendation: **F-CLEAN4b**. The signal hasn't paid for itself in
diagnostic value over the 6 months it's been live.

### O2. F-SKY10 `cv` field in `match_diagnostics`

When `use_cross_view_scoring=true`, every matched-segment candidate
gets a `cv` entry in `match_diagnostics`. The PDF render code never
reads this field. The flag is default-off so the overhead is
conditional, but the dead-output pattern is the same as O1.

→ **Proposal F-CLEAN5**: either surface `cv` in the per-view diag
table (the PDF page already shows other `match_diagnostics` fields),
or drop the field and the scorer wiring entirely. Path A is the
"keep the diagnostic capability" call; Path B is the "F-SKY10
intent is superseded by F-SKY11.1" call. The consolidation plan
already favours Path B.

## Conditionally-wired with weak gates

### W1. F-SKY11.2 bird's-eye module + demo

`pano_birdseye.py` (215) + `scripts/13_birdseye_registration_demo.py`
(457) = **672 LOC for a documented dead-end** (see post-mortem in the
F-SKY11.2 plan). The script defaults now produce an honest PDF instead
of a misleading one, but the module is otherwise unreachable.

→ **Proposal F-CLEAN6**: delete both files. The F-SKY11.2 plan stays
as the post-mortem; the consolidation plan keeps the row pointing at
the plan. Reproducing the experiment is one `git log` away.

## Boilerplate

### B1. Seven copy-pasted site-config loaders

`region_pdf.py` has eight near-identical 25-line loaders:

```
_load_site_seed_urls
_load_site_negative_seeds
_load_site_anchor_overrides
_load_site_max_plausible_height_m
_load_site_use_satellite_footprints
_load_site_use_cross_view_scoring
_load_site_use_pano_coastline_recovery
_load_site_drive_pano_recovery_anchor
```

All eight perform the same `open + json.load + try/except + .get(...)`
ritual. Replacing with one `_load_site_value(region_name, key,
default)` saves ~150 LOC.

→ **Proposal F-CLEAN7**: consolidate into one helper + a per-flag
typed wrapper layer that's ~5 lines each.

### B2. `_seed_multiview_registration` is 918 lines

The single biggest function in the codebase. Mixes:
- Pass 1a: spin capture + screening
- Pass 1b: pitch correction
- Per-view mask precomputation
- Joint anchor IoU optimization (~60 lines)
- Per-view re-registration (Pass 2)
- Per-view segmentation + matching + height extraction
- Pano stitching + pano-level matching
- F-SKY11.1 pano-recovery integration

Five obvious split points by responsibility. The function is hard to
read top-to-bottom and harder to modify without regressions.

→ **Proposal F-CLEAN8** (larger): refactor into a small number of
top-level helpers — `_capture_spin_views`, `_compute_pitch_correction`,
`_recover_seed_anchor`, `_register_each_view`, `_stitch_seed_pano`.
This is a couple-of-PRs job, not a single sitting.

## Stale or redundant docs

### S1. `STATUS.md` (513 lines)

Opens with: `Tagged-height MAE | 19 m single-seed / 53 m cross-seed`
and a "seed_registration_views | 27" baseline. Latest measured run
shows `seed_registration_views | 26`, different MAE numbers, different
override count (the doc lists seed_1 + 4 + 5 as overridden; seed_1 is
now empirically droppable per Path B measurement). The roadmap section
(Phases A–D) describes work that's been partially done in different
forms (mask split is now F-SKY7's local-max + F-SKY2 anchored split;
not "vertical gradients" as planned).

→ **Proposal F-CLEAN9**: rewrite the top of STATUS.md to reflect
current numbers + override status; convert the Phase A–D roadmap
section into a "completed work" archive linking to the relevant
F-SKY*.md plans + the consolidation plan.

### S2. `docs/cartagena-audit-2026-05.md` (78 lines)

Audit from EARLIER in May, before most fixes. Cites "MAE ≈ 151 m" from
when seeds 2/3 were unscreened aerials. Reading time is wasted; users
new to the module land on this and think the pipeline is broken.

→ **Proposal F-CLEAN10**: move to `docs/archive/` or just delete.
The relevant historical bits (aerial detection) are already captured
in `STATUS.md` and `implementation-plan.md`.

### S3. `docs/implementation-plan.md` (158 lines)

References "Issue 1: Aerial/Drone Image Detection" — resolved.
References "Issue 2: ..." — already in the codebase.

→ **Proposal F-CLEAN11**: mark complete + move to `docs/archive/`.

### S4. `docs/glass-roof-height-fix-plan.md` (208 lines)

Phase 1 was the `height_trace` infrastructure — implemented (script 09
+ `height_trace.py`). Phase 2 is "monocular depth gating" — not done,
likely never to be done given F-SKY11.x explored adjacent territory.

→ **Proposal F-CLEAN12**: update header status to "Phase 1 complete;
Phase 2 deferred indefinitely". Keep the doc.

## Untested site configs

### U1. `chicago.json` (97 LOC), `miami.json` (109 LOC)

Both have seed URLs and (for miami) `known_heights_m`. Neither has
`use_satellite_footprints`, `use_pano_coastline_recovery`, or any of
the newer flags enabled. **The new pipeline has never been measured on
them.** Risk: when the user wants to try them they'll find subtle
breakage that only Cartagena dogfooding caught.

→ **Proposal F-CLEAN13**: at minimum, run the current pipeline on
each and capture metrics in a one-line entry under STATUS.md "regions
exercised". Reveals whatever's broken before the user finds it.

## Summary table

| ID | Action | Type | Effort | Estimated LOC removed |
|---|---|---|---|---|
| F-CLEAN1 | Delete `config.py` | Dead code | 5 min | 123 |
| F-CLEAN2 | Remove `osm_marker_voronoi_silhouettes` + reference | Dead code | 15 min | ~125 |
| F-CLEAN3 | Inline `_make_sky_mask_from_bool` | Trivial | 5 min | 12 |
| F-CLEAN4b | Drop floor-period compute + 4 unread fields | Dead-output | 1 h | ~120 |
| F-CLEAN5 | Surface `cv` in PDF OR drop F-SKY10 wiring | Dead-output | 1-2 h | varies |
| F-CLEAN6 | Delete `pano_birdseye.py` + script 13 | Dead-end | 10 min | 672 |
| F-CLEAN7 | Consolidate 8 site loaders → 1 helper | Boilerplate | 1 h | ~150 |
| F-CLEAN8 | Split `_seed_multiview_registration` (918 lines) | Refactor | 1–2 days | reorg only |
| F-CLEAN9 | Rewrite STATUS.md top | Docs | 30 min | doc cleanup |
| F-CLEAN10 | Archive `cartagena-audit-2026-05.md` | Docs | 5 min | 78 |
| F-CLEAN11 | Archive `implementation-plan.md` | Docs | 5 min | 158 |
| F-CLEAN12 | Update `glass-roof-height-fix-plan.md` header | Docs | 5 min | – |
| F-CLEAN13 | Measure chicago + miami end-to-end | Validation | 30 min | – |

**Total trivial / quick wins: ~1300 LOC removable in ~3 hours.**

## Recommended order

If picking up tomorrow:

1. **F-CLEAN1 + F-CLEAN6 + F-CLEAN10 + F-CLEAN11** (45 min): pure
   deletes. Brings the module down by ~1000 LOC with zero risk.
2. **F-CLEAN9 + F-CLEAN12** (40 min): doc rewrites to make STATUS.md
   reflect reality and shrink the historical-fluff load on new
   readers.
3. **F-CLEAN2** (15 min): remove the disabled Voronoi function once
   the F-SKY3 plan link is updated to say "kept only in git history".
4. **F-CLEAN4b** (1 hour): drop floor-period compute. Measurable
   speedup on the per-view height extraction loop.
5. **F-CLEAN7** (1 hour): consolidate the eight site loaders.
6. **F-CLEAN13** (30 min): exercise chicago + miami sites end-to-end,
   capture findings.

Items F-CLEAN5 and F-CLEAN8 are the big ones; defer until the small
trims are done so the changes are clear-eyed instead of mixed with
unrelated cleanup.

## What the audit deliberately does NOT propose

- No new features.
- No changes to F-SKY8 (Microsoft Buildings) — works.
- No changes to F-SKY11.1 Path B — just landed.
- No matcher / scoring changes — the cleanup is structural, not
  algorithmic.
- No test-coverage expansion — the existing 21 tests cover the CV math;
  the orchestration is intentionally exercised by the full Cartagena
  run rather than by unit tests.

## Open questions for the user

1. **F-CLEAN5**: keep F-SKY10 wiring for future per-building
   experiments, or rip it out now since F-SKY11.1 supersedes the
   *intent*? Both are defensible; the consolidation plan favours
   rip-it-out.
2. **F-CLEAN8**: who owns the time for the 918-line function split?
   It's a 1–2 day refactor with real regression risk that's worth
   doing but not urgent.
3. **F-CLEAN13**: should we ship a `miami.json` config with
   `use_pano_coastline_recovery: true` by default, or keep it off until
   measurement shows it helps? My recommendation: keep off until
   measured, mirror the Cartagena two-flag gating story.
