# Plan — Glass-Roof Under-Reach: Trace First, Then Monocular Depth

_Created: 2026-05-14. Owner: skyline. Status (2026-05-24): **Phase 1 shipped**
(`height_trace.py` + `height_trace_render.py` + `scripts/09_height_trace.py`
record every gate decision per building per view). **Phase 2 (monocular
depth) deferred indefinitely** — F-SKY11.2 explored adjacent monocular-IPM
territory and hit a depth-reach wall, weakening the case for the heavier
depth-model investment until a clear gating result emerges from Phase 1
traces._

## Problem

Tall glass / curtain-wall towers under-predict by 50–100 m in `skyline` output (see [STATUS.md](../STATUS.md#heights-the-core-product-gap)). Three live hypotheses, none ruled out:

1. **Mask under-reach** — SegFormer-b0 labels the reflective spire as `sky` because the top of the tower mirrors the sky.
2. **Closest-in-column-bin gate** — drops the tall building when a nearer short building shares its column ([pipeline.py:2196-2211](../pipeline.py#L2196-L2211)).
3. **Pinhole geometry bug** — focal-length / pitch error that compounds with distance in [pipeline.py:2283-2288](../pipeline.py#L2283-L2288).

The glass-facade override at [pipeline.py:2243-2277](../pipeline.py#L2243-L2277) already falls back to the sky-contour when the mask roof is ≥ 15 px below the contour, but the gap on Cartagena's Bocagrande towers persists. We don't yet know which of (1)/(2)/(3) is dominant.

## Why this plan exists

Monocular depth (MiDaS / DPT) is the obvious "big hammer" — adds an absolute foreground cue that doesn't depend on SegFormer's class boundary. But it's a new model, a new cache layer, and a new failure mode. **We don't pay that cost until we've shown the simpler causes are not the actual blocker.** The two phases below are sequential, not parallel.

---

## Phase 1 — End-to-end height trace (gating diagnostic)

**Goal:** for ONE specific tagged tall tower, walk every step from pixel → metres and identify which gate or estimator is responsible for the under-prediction. STATUS.md priority 1.

### Target case

Pick from the Cartagena report's "tagged height vs predicted height" scatter — preferably the highest |tag − pred| residual where tag ≥ 80 m. Suggested first target: any building with `tag_h ≥ 100` and `pred_h ≤ 50` in `Cartagena_skyline_report.pdf`.

### Trace points (in pipeline order)

| # | Stage | Code location | What to capture |
|---|---|---|---|
| 1 | Which seed views see the building | `region_pdf.py::_seed_multiview_registration` | seed_name, heading_deg, raw view filename |
| 2 | Building's projected x_px and forward_m per view | `pipeline.py::_project_all_buildings_vectorized` | `forward_m`, `x_px`, `x_left_px`, `x_right_px` |
| 3 | Whether mask has building pixels in the projected x-range | [pipeline.py:2213-2240](../pipeline.py#L2213-L2240) (`_footprint_roof_y_from_mask`) | `roof_y_mask`, `coverage` |
| 4 | Whether glass-facade contour override fired | [pipeline.py:2241-2277](../pipeline.py#L2241-L2277) | `contour_top_y`, `gap`, `implied_h`, fired y/n |
| 5 | Whether closest-in-column-bin gate dropped a higher prediction | [pipeline.py:2196-2211](../pipeline.py#L2196-L2211) | for each rival projection in the bin: `forward_m`, `forward - closest_in_bin` |
| 6 | Final y_px → angle → height conversion | [pipeline.py:2283-2288](../pipeline.py#L2283-L2288) | `y_px`, `pitch_rad`, `angle_rad`, `top_above_camera`, `cam_z`, `terrain_elev_m`, final `height_m` |
| 7 | Geometric y-consistency gate result | [pipeline.py:2292-2314](../pipeline.py#L2292-L2314) | `min_y_for_building`, `max_y_for_building`, kept/dropped |
| 8 | Plausibility cap result | [pipeline.py:2316-2333](../pipeline.py#L2316-L2333) | `tag_h`, kept/dropped |

### Deliverable

`runs/height_traces/<region>_<feature_id>_trace.json` — one record per view-seed pair the building appears in, with every trace point above. Plus a 1-page side-by-side render: image crop + projected x_range + mask overlay + contour overlay + arrows annotating each gate decision.

### Implementation

A new tool: `city2stl/skyline/scripts/09_height_trace.py`.

```bash
python city2stl/skyline/scripts/09_height_trace.py \
    --region Cartagena --feature-id b0389 --out runs/height_traces/
```

The script re-uses the cached image and SegFormer mask (no new API spend, no new model inference). Adds **logging-only** hooks inside `estimate_heights_from_registration` controlled by a `trace_feature_id` kwarg — no behavior change when unset. The hooks write to a `dict` that the script serialises.

### Acceptance gate (mandatory before Phase 2)

Trace at least **3 distinct buildings** with tag ≥ 80 m and |tag − pred| ≥ 40 m. From the records, answer:

- Is the dominant failure mask-roof being too low (mask under-reach)? → Phase 2 monocular depth is justified.
- Is it the closest-in-column-bin gate dropping the correct estimate? → Phase 1.5 below; **do not start Phase 2**.
- Is it the pinhole math (consistent angle/forward bias)? → Phase 1.5 below; **do not start Phase 2**.

### Phase 1.5 — Cheap fixes if the trace points elsewhere

Only listed for completeness; do these instead of Phase 2 if the trace finds them:

- **Bin gate too aggressive:** widen the ±15 px window only when the rival has tag_h < own tag_h, or raise the 200 m closer-than threshold to 400 m. Keep gate-decision logging on by default afterwards.
- **Pinhole bug:** suspect order of operations in [pipeline.py:2283-2284](../pipeline.py#L2283-L2284) (`angle_rad = math.atan((cy - y_px) / f_px) + pitch_rad`). Validate against a known-height reference building at known distance with a manually-measured `y_px`.

---

## Phase 2 — Monocular depth (MiDaS / DPT) integration

**Gated on Phase 1 concluding "mask under-reach is the dominant cause."** Do not start until that gate is passed.

### Where depth helps glass roofs

Two distinct uses, in priority order:

1. **Roof-y recovery** (the glass-roof fix) — When SegFormer's building class stops short of the actual rooftop, the depth map for the same column does NOT stop: the spire and the sky behind it have very different depths. We can find the rooftop y as the topmost row where `depth(x, y) < depth_threshold` (foreground-vs-sky), then use that y in the same pinhole equation.

2. **Foreground prior for matching** (the secondary use, already scoped in [STATUS.md](../STATUS.md#detailed-implementation-roadmap-next-pass) "Depth integration plan"). Out of scope for THIS plan — STATUS.md already has Stages 0–4 for it. This plan adds the roof-y use only.

### Model choice

| Option | Params | Latency / 384px image | License | Recommendation |
|---|---|---|---|---|
| **MiDaS v3.1 DPT-Hybrid** | 123 M | ~150 ms on CPU, ~25 ms on GPU | MIT | **Primary.** Robust, well-tested, hub-loadable in one line. |
| Depth Anything V2 Small | 24.8 M | ~80 ms CPU | Apache 2.0 | Alternative if MiDaS feels heavy; better edges on near objects but younger codebase. |
| ZoeDepth | 343 M | ~300 ms CPU | MIT | Skip — metric depth output not needed; relative is fine for foreground-vs-sky. |

We need **relative depth**, not metric. The decision rule "is this pixel foreground or sky?" works on any monotonic depth proxy.

### Architecture

```
existing:
  CapturedView ──► SegFormer mask (cached) ──► building_mask, sky_mask
                                          └──► contour ──► roof_y per column

new:
  CapturedView ──► MiDaS depth (cached) ────► depth_map (float, H×W)
                                          └──► sky_threshold per view (Otsu on bg depth)
                                          └──► roof_y_depth per column
```

The depth path runs alongside SegFormer per view. Both outputs feed the existing `estimate_heights_from_registration` with a new resolution policy described below.

### Roof-y resolution policy (the key behaviour change)

Inside [pipeline.py::estimate_heights_from_registration](../pipeline.py#L2140), after the existing glass-facade contour override at [line 2243](../pipeline.py#L2243):

```python
# Priority chain for roof_y (most specific first):
#   1. mask roof y from footprint x-range (existing)
#   2. contour override if gap ≥ 15 px AND implied_h ≤ 300 m (existing)
#   3. depth-derived roof y if available and contour override didn't fire
#   4. fallback: skyline contour y at centroid x (existing)
```

The depth-derived roof y for column `x` is the smallest (topmost) `y` such that `depth_map[y, x] < sky_threshold`, scanned downward from the top of the building's projected band. Confidence proportional to `1 - (sky_threshold - depth_map[y, x]) / sky_threshold`.

### Files to add

| File | Purpose | Lines (est.) |
|---|---|---|
| `city2stl/skyline/depth.py` | `compute_depth_map(image) -> np.ndarray`; LRU cache mirroring `_segformer_cache`; `roof_y_from_depth(depth, x_range, sky_threshold)`; `sky_threshold_from_depth(depth, sky_mask) -> float` (uses the SegFormer sky_mask to pick a robust threshold) | ~250 |
| `tests/test_skyline_depth.py` | unit tests for `sky_threshold_from_depth`, `roof_y_from_depth`, cache anchoring | ~150 |

### Files to modify

| File | Change |
|---|---|
| `pipeline.py::estimate_heights_from_registration` | Insert priority-chain step 3 above; thread an optional `depth_map` kwarg; default `None` preserves current behaviour |
| `region_pdf.py::_seed_multiview_registration` | Compute (and cache) depth alongside the SegFormer mask per view; pass into the estimator |
| `region_pdf.py` PDF rendering | Add `[depth roof]` annotation in the per-view legend when step 3 fired |
| `STATUS.md` | Move "Monocular depth" from "deliberately not on the list" to the active roadmap once Phase 1 gate is passed |
| `README.md` Dependencies section | Add `timm` (MiDaS dependency); document the new on-disk depth cache |

### Cache strategy

- In-memory LRU keyed by `id(image)` with image-array anchoring (mirror the SegFormer cache fix in [pipeline.py](../pipeline.py); see STATUS.md "Segmentation" notes on the silent cross-seed mask collision bug that the anchor fixes).
- On-disk under `runs/depth_cache/` keyed by the same request hash as `runs/image_cache/`.
- 16-slot in-memory cap matches SegFormer (12 spin views + headroom).

### Performance budget

| Cost | Per region | Notes |
|---|---|---|
| One DPT-Hybrid inference per view | 60 views × ~150 ms CPU = ~9 s | vs. the current ~120 s baseline = ~7% overhead. Acceptable. |
| RAM headroom | ~400 MB for DPT-Hybrid weights + ~30 MB per cached depth map | Fits comfortably; SegFormer is already ~85 MB. |
| First-run model download | ~470 MB | One-time. Document in README. |

### Acceptance criteria

1. **Trace cases improve.** Re-run the 3+ Phase 1 trace cases. The depth-derived roof y should land **within 10 px of the manually-marked rooftop** in ≥ 2 of 3 cases.
2. **Cartagena regression.** Cross-seed MAE for tagged buildings tag ≥ 80 m drops by **≥ 25 m** (from current 53 m cross-seed → < 28 m).
3. **No regression on short buildings.** Single-seed MAE for tagged buildings tag < 30 m stays within ±1 m of the current 19 m baseline. (Short buildings shouldn't have the under-reach problem; depth shouldn't be making them worse.)
4. **Tests stay green.** All 21 existing skyline tests still pass; new depth tests pass.
5. **Fallback works.** With depth disabled (`SKYLINE_CV_DISABLE_DEPTH=1`), output is byte-identical to current behaviour.

### Rollback plan

The roof-y priority chain treats depth as opt-in; setting `compute_depth_map` to return `None` reverts to current behaviour. The depth cache directory can be deleted without state loss. No schema changes to `BuildingRecord` or `RegisteredBuildingEstimate`.

---

## Deliberately rejected approaches (anti-relitigation)

These were tried in earlier sessions and **rolled back**. Capture here so future sessions don't re-relitigate them. If revisiting one is genuinely required, write a new design note explaining what changed about the geometry, not just "let's try again".

### A. Sliding-window SegFormer on stitched RGB

**Why tried.** Running SegFormer over the entire stitched 360° pano in one pass (with overlapping tiles) seemed like it would give a globally consistent mask without per-view stitching artefacts.

**Why removed.** Two failures:

1. **Seams.** SegFormer's per-tile class probabilities don't compose at tile boundaries — the same column got different labels in adjacent windows, producing visible seam discontinuities in the mask that propagated into bad column heights.
2. **Cost.** A 360° stitched pano is ~6,000 px wide; sliding windows multiplied inference cost by ~6× vs. running SegFormer once per spin view.

**Current approach instead.** SegFormer runs **per-view** on the 12 spin views; the per-view masks are then stitched ([pipeline.py::stitch_pano_masks](../pipeline.py)). Mask-stitching is seam-free by construction (we never blend probabilities across views, only their argmax outputs).

**When to reconsider.** Only if SegFormer is replaced with a model whose probability outputs compose across overlapping windows (e.g., a true panoramic-aware backbone). Until then: no.

### B. Per-view escape hatch around the joint anchor

**Why tried.** The joint anchor optimization picks one pano-to-geographic offset per seed across all 12 views. Some individual views had clearly better IoU at a different offset than the joint optimum — the "escape hatch" let each view fine-tune ±25° (later ±60°) outside the joint anchor.

**Why removed.** It broke the "one pano = one rigid offset" geometric invariant. Individual views drifted to wildly different offsets, so the same OSM building appeared at different geographic headings depending on which view you read. Spin-view consistency went from "always agrees within 0.5°" to "disagrees by up to 90° in pathological cases", which then poisoned the cross-seed aggregator.

**Current approach instead.** Per-view fine search is **clamped to ±8° around the seed's joint anchor** ([STATUS.md "Heading registration"](../STATUS.md#heading-registration)). For seeds where the joint optimum is the wrong local maximum, the user supplies `anchor_offsets_deg` in `sites/<region>.json` once — a one-line manual override.

**When to reconsider.** Only with a stronger discriminating signal that makes the joint IoU objective unimodal (e.g., monocular depth comparison, Photo Sphere pose metadata). Phase 2 of THIS plan adds a depth signal — but its first use is roof-y recovery, not re-opening the per-view anchor search. Don't bundle those changes.

---

## Timeline

| Stage | Effort | Calendar |
|---|---|---|
| Phase 1 trace tool + 3 traces | ~1 day | session 1 |
| Phase 1 gate decision | — | end of session 1 |
| Phase 2 implementation (if gated through) | ~2–3 days | sessions 2–3 |
| Cartagena re-validation | ~1 day | session 3 |

Total **upper bound** ~5 days if depth ends up being the right fix. Lower bound ~1 day if the trace exposes a Phase 1.5 cheap fix.
