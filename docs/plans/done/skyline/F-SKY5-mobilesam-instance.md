# F-SKY5 — MobileSAM instance head gated on OSM markers

Proposal entry: `docs/proposals.md` F-SKY5

## Goal

Replace F-SKY3's failed heuristic-Voronoi split with a real
instance-segmentation model. SegFormer-b0 is semantic-only — when
adjacent buildings share a mask blob there's no model-side mechanism
to separate them. **MobileSAM** (10 M params, distilled from SAM's
632 M) takes point prompts and returns per-instance masks. Feeding it
the OSM-projected centroids of buildings inside a merged blob gives us
the instance-indexing layer SegFormer lacks, sourced from learned
image features instead of arithmetic midpoints.

## Constraints (per session direction)

- Prefer small models, multi-stage. The total footprint of the proposed
  pipeline is SegFormer-b0 (~4 M) + MobileSAM (~10 M) = **~14 M params**.
  60× smaller than original SAM, 3× smaller than Mask-RCNN-ResNet50.
- Gate the SAM call so it only fires when we need it — single-building
  blobs skip SAM entirely.
- Be honest about install footprint. MobileSAM weights ~40 MB.

## Approach

### Stage A — install + checkpoint

Two viable install paths:
1. **`pip install git+https://github.com/ChaoningZhang/MobileSAM.git`** —
   upstream, lighter dependency surface (only `torch`, `torchvision`).
   Checkpoint manually downloaded from the repo's `weights/`
   directory (~40 MB) and pointed to via env var
   `MOBILESAM_CHECKPOINT_PATH` (default `~/.cache/mobile_sam/vit_t.pth`).
2. **`pip install ultralytics`** — bigger dependency (~150 MB transitive
   incl. opencv, matplotlib redundancies we already have) but
   auto-downloads the checkpoint on first use.

Path 1 is preferred. The function lazily imports `mobile_sam` and
returns a no-op (None) when the import fails OR the checkpoint isn't
present, mirroring how `_ensure_segformer` handles its load. Pipeline
still runs without MobileSAM installed — just without the SAM
instance head.

### Stage B — new function

```python
def osm_sam_instance_silhouettes(
    image_rgb: np.ndarray,
    segments: list[dict],
    projections: list[dict],
    *,
    building_mask: np.ndarray | None = None,
    min_proj_containment: float = 0.5,
    min_marker_separation_px: int = 20,
    confidence_floor: float = 0.65,
    min_child_width_px: int = 4,
) -> list[dict]:
```

For each segment containing ≥ 2 OSM markers (same containment metric
F-SKY2/F-SKY3 use), gather the marker x_px values plus the segment's
mid-y as `(x, y)` point prompts. Hand them and the cached image to
MobileSAM's predictor.

For each returned mask + score:
- Reject below `confidence_floor` (default 0.65 — SAM-family quality
  threshold below which masks are typically noise).
- Intersect with the SegFormer building mask if present (catches SAM
  predictions that leak into sky / water).
- Bound by the segment's [x_left, x_right] band.
- Emit one silhouette per surviving mask with peak_x from the marker.

When 0 masks survive the floor, leave the original segment unchanged
(F-SKY5 is non-destructive; the matcher fallback still operates).

### Stage C — gated wire-in

```
# region_pdf.py, after F-SKY2:
if reg.n_matches >= 3:
    segments = osm_anchor_silhouettes(...)
    # F-SKY5 (opt-in): SAM instance separation for segments F-SKY2
    # didn't already split. Only fires when MobileSAM is installed.
    if _mobilesam_available():
        segments = osm_sam_instance_silhouettes(
            image, segments, all_proj_list, building_mask=_bmask)
```

The function is structurally similar to F-SKY3 (same input shape,
same marker logic) — but uses the SAM masks as evidence instead of
Voronoi midpoints, and the per-mask confidence as a rejection
mechanism so bad markers don't introduce phantom silhouettes (F-SKY3's
specific failure mode).

## Target files

- `city2stl/skyline/pipeline.py` — `osm_sam_instance_silhouettes`,
  `_ensure_mobilesam`, `_mobilesam_predict`.
- `city2stl/skyline/region_pdf.py` — gated call site, import the
  new function.
- `city2stl/skyline/README.md` — new "Optional SAM instance head"
  section + checkpoint download instructions.
- `requirements-optional.txt` (or similar) — `mobile_sam` git URL.

## Success criteria

- Function returns `segments` unchanged when MobileSAM isn't installed
  (graceful no-op).
- On Cartagena with MobileSAM enabled:
  - At least one previously-merged seed_5 segment gets split into the
    correct number of children matching OSM markers (visible by
    increased segment count on the affected per-view PDF page).
  - Tagged-building MAE either improves or stays within ±1 m of the
    F-SKY2.1 baseline (17.28 m). Unlike F-SKY3, SAM has a confidence
    floor so bad splits should be rejected, not introduce regressions.
  - F-SKY4 mask overlay shows the new instance boundaries don't
    cross obviously-non-building pixels (sanity check that the
    SegFormer-intersect step is doing its job).
- 21 unit tests still pass.
- Run time increase < 2× current b0 baseline (~3 min → ~6 min on
  Cartagena). SAM inference is fast on the small encoder; the gate
  ensures we only call it on a fraction of segments.

## Known risks

- **MobileSAM checkpoint redistribution**: ~40 MB binary, can't be
  checked into git. Plan handles this with the env-var path +
  graceful no-op when missing.
- **CPU-only inference time**: each SAM forward is ~200–500 ms on CPU
  for a 640-px image. With the gate (only fires on multi-marker
  merged blobs) the total per-region cost should be ≤ 30 calls ≈ 15 s
  added.
- **Confidence threshold tuning**: 0.65 is a starting guess from the
  SAM paper's ablation. May need region-specific tuning.
- **Over-segmentation**: SAM at very high confidence can still
  separate "one tower" into "tower body" + "tower spire". The
  SegFormer-intersect step rejects sky-leaking masks but won't
  catch this. Mitigation: post-filter sibling masks whose centres
  are within `min_marker_separation_px` of each other (collapse).

## Out of scope (deferred)

- Box prompts (instead of point prompts). Point is simpler and SAM
  is well-validated for it; box adds OSM polygon → image-box
  projection complexity.
- Per-region SAM variant selection. Stick with MobileSAM
  (`mobile_sam` vit_t variant) for now; TinySAM / EfficientSAM are
  drop-in if MobileSAM proves too slow.
- SAM 2 (video / streaming-optimised) — purely about latency for
  video; doesn't help our per-image case.
- Re-running registration with the SAM-instance-derived silhouettes
  as additional evidence.

## Decision point before implementation

This is the first proposal in the session that adds a network-dependent
optional dependency (model checkpoint download). The right call is to
**show this plan first and confirm the user wants the install path
exercised before pip-installing and downloading weights**. The plan
file by itself is no-cost; everything past Stage A is.
