# skyline — F-SKY Series

Computer-vision pipeline that estimates per-building heights for a city
region by registering Google Street View imagery against OpenStreetMap
building footprints (+ satellite footprints). The ML height stack lives
alongside it in `city2stl/skyline/height/` (moved here from
`city2stl/height/` on 2026-06-07). Implements the F-SKY series
(F-SKY1, F-SKY2, F-SKY4–F-SKY8, F-SKY10–F-SKY13, F-SKY18, F-SKY22,
F-SKY24).

**Status**: see [docs/STATUS.md](docs/STATUS.md) — the dated "Current
state (2026-06-07)" section is authoritative. Highlights: 360° pano
report v2 (pano-space + top-down tab groups, Distance scan, Heights,
cardinal lines), automatic bearing recovery (silhouette × OSM cross-
correlation with a confidence gate), sliding-window + OSM-anchored
splitter, water-only ground cap, tiled depth, F-SKY1 re-enabled.

Key references:
- [docs/STATUS.md](docs/STATUS.md) — current run metrics + per-seed heading-recovery measurement
- [docs/plans/F-SKY-PIPELINE-CONSOLIDATION.md](../../docs/plans/F-SKY-PIPELINE-CONSOLIDATION.md) — canonical end-state pipeline shape, signal source-of-truth table
- [docs/plans/F-SKY-AUDIT-2026-05-24.md](../../docs/plans/F-SKY-AUDIT-2026-05-24.md) — current structural-audit refresh (11 of 13 F-CLEAN proposals shipped; new pano-recovery non-determinism finding). [Original 2026-05-17 baseline](../../docs/plans/F-SKY-AUDIT-2026-05-17.md) kept for context.
- [docs/F-SKY-INTEGRATION.md](../../docs/F-SKY-INTEGRATION.md) — consolidated F-SKY status (older snapshot)

## Quick start

```bash
# from strm2stl/
export GOOGLE_MAPS_API_KEY=...   # or put in strm2stl/.env

PYTHONPATH=. python city2stl/skyline/scripts/08_region_skyline_pdf.py \
  --region Cartagena
```

That writes
`city2stl/skyline/runs/region_reports/Cartagena_skyline_report.pdf`,
a 30+ page report covering:

- Summary + bbox + region location map
- Screenshot montage of each screened seed location
- Per-seed-view registration pages (image + matched-footprint minimap)
- Stitched 360° pano comparison page per seed
- Aggregated heights with cross-seed disagreement
- Validation scatter against OSM-tagged building heights

A typical Cartagena run takes ~3 minutes and consumes ~$0.10 of Google
Street View API quota (one image per spin view × 12 views × 5 seeds + a
handful of screening images).

## Status

**See [docs/F-SKY-INTEGRATION.md](../../docs/F-SKY-INTEGRATION.md)** for the consolidated F-SKY feature status, measurement results, and integration roadmap. That document is the single source of truth for what's active, disabled, or pending.

Quick summary from [docs/STATUS.md](docs/STATUS.md):

- Heading registration is reliable when seeds are placed across water
  from a tall-building cluster (Bocagrande from across the bay).
- Heights systematically under-predict tall glass towers by 50–100 m —
  this is the main open product gap.
- Cross-seed coverage (the only honest validation signal) is currently
  ~3 buildings per run, which makes the height MAE numbers noisy.

### Active F-SKY Features (2026-05-24)

Core pipeline features enabled by default:
- **F-SKY2**: OSM-anchored silhouette splitting
- **F-SKY4**: SegFormer mask overlay (diagnostic)
- **F-SKY6**: One-to-one segment-to-building assignment
- **F-SKY7**: Local-maxima peak detection + dual baseline
- **F-SKY8**: Satellite-derived building footprints (opt-in per region: `use_satellite_footprints`)
- **F-SKY10**: Cross-view colour/width/edges verification (opt-in: `use_cross_view_scoring`); 3 signals shipped in `cross_view.py`, `cv` field not yet rendered in PDF (see F-CLEAN5)
- **F-SKY11.1 Phase B**: Pano-coastline heading recovery wired as the joint-anchor optimizer's coarse seed (opt-in: `use_pano_coastline_recovery` + `drive_pano_recovery_anchor`); on Cartagena dropped seed_1's manual `anchor_offsets_deg` override

Diagnostic/inspector tools (standalone scripts):
- **F-SKY11**: per-view coastline-keypoint inspector — `scripts/11_coastline_demo.py`
- **F-SKY11.1**: pano-keypoint inspector — `scripts/12_pano_coastline_demo.py`
- Multi-channel heading-recovery experiment — `scripts/13_heading_recovery_demo.py` (replaces the failed bird's-eye demo at the same script number)

Disabled / superseded:
- **F-SKY1**: floor-period diagnostic — computed per-segment but never read by the renderer (audit F-CLEAN4)
- **F-SKY3**: Voronoi splitting (measured regression 2026-05-16; superseded by planned F-SKY5)
- **F-SKY11.2**: bird's-eye IPM registration (denied; monocular depth reach too small — see plan post-mortem)

## Optional SAM instance head (F-SKY5)

MobileSAM (~10 M params) can split merged building blobs that F-SKY2 did not
resolve using SAM point prompts sourced from OSM centroids. It is **off by
default** and has no effect when not installed — the pipeline degrades
gracefully to F-SKY2-only behaviour.

**Install:**
```bash
pip install git+https://github.com/ChaoningZhang/MobileSAM.git
```

**Download checkpoint** (~40 MB) from
https://github.com/ChaoningZhang/MobileSAM/tree/master/weights
and place at `~/.cache/mobile_sam/vit_t.pth`, or set env var
`MOBILESAM_CHECKPOINT_PATH` to the file path.

**Enable:**
```bash
SKYLINE_CV_F_SKY5=1 python scripts/08_region_skyline_pdf.py cartagena
```

## How it works

### Core Files

| File | Role | Lines | F-SKY Features |
|---|---|---|---|
| [pipeline.py](pipeline.py) | CV primitives: SegFormer integration, projection, registration, height extraction, aggregation. Pure functions, unit-tested. | ~3650 | F-SKY1, F-SKY2, F-SKY6, F-SKY7 |
| [region_pdf.py](region_pdf.py) | Orchestration + Street View I/O + seed selection + PDF rendering. Stateful; harder to test in isolation. `_seed_multiview_registration` is a thin orchestrator over 5 phase helpers (F-CLEAN8, 2026-05-27); largest function is now `_render_pdf` (~556 LOC). | ~4500 | F-SKY4, F-SKY8, integration layer |

### Helper Modules (F-SKY Implementation)

| File | Role | F-SKY Features |
|---|---|---|
| [height_trace.py](height_trace.py) | Floor-strip detection via 1D FFT / autocorrelation. | F-SKY1 |
| [satellite_footprints.py](satellite_footprints.py) | Microsoft Global ML Building Footprints fetch + deduplication. | F-SKY8 |
| [satellite_image.py](satellite_image.py) | Satellite image fetch and preprocessing. | F-SKY8, F-SKY10 |
| [cross_view.py](cross_view.py) | Cross-view geometric + appearance verification (roof colour, width, edges). | F-SKY10 |
| [coastline_registration.py](coastline_registration.py) | Water-mask based heading recovery via per-bearing keypoints. | F-SKY11, F-SKY11.1 |
| [osm_water.py](osm_water.py) | OSM `natural=coastline` + water-polygon extraction; primary coastline keypoint source (replaces HSV satellite-water). | F-SKY13 |
| [depth_estimation.py](depth_estimation.py) | Depth Anything V2 on stitched panos; OSM-calibrated `depth_height_m` cross-check (diagnostic, Phase A). | F-SKY12 |
| [html_report.py](html_report.py) | HTML diagnostic report — where the per-building tables live after `pano_only_pdf` trimmed them from the PDF. | F-SKY15 |

End-to-end flow:

```
1. Load region bbox from SQLite + OSM buildings/waterways via Overpass
2. Propose auto-seed standoff positions (8 dirs × 3 standoff radii, scored
   by angular spread of high-rises and water proximity)
3. Screen each candidate with a 1-image Street View probe
4. For each viable seed:
     a. Resolve pano (Photo Sphere id or location fallback)
     b. Capture 12-view spin (every 30°)
     c. SegFormer-b0 (ADE20K) mask per view  → cached, anchored
     d. Joint anchor optimization across all spin views
        (3° coarse + 0.5° fine, weighted by observed-building columns,
         maximizing per-building IoU − water-penalty − miss-penalty)
     e. Per-view registration with ±8° around the seed anchor
     f. Per-view height extraction (pinhole y → height)
     g. Stitched-pano detection by mask-stitching the per-view masks
5. Aggregate heights per building with cross-seed outlier downweighting
6. Render the PDF
```

### OSM-anchored silhouette splitting (F-SKY2)

SegFormer routinely merges adjacent towers in a dense skyline into a
single mask blob, producing one wide silhouette segment for what OSM
knows is three buildings. After registration succeeds (≥ 3 matches), we
re-split such segments at OSM-projected building gaps via
`osm_anchor_silhouettes`. The split column is snapped to the
building-mask coverage minimum inside the gap when available (the
actual visible inter-tower separator), not just the OSM midpoint.

The matcher (`match_segments_to_buildings`) accepts candidates either
via interval-IoU (≥ 0.10) or via containment (≥ 50 % of the projection
inside the segment). The containment fallback lets narrow projections
inside wide multi-building silhouettes still qualify for matching even
when their IoU is small (denominator dominated by segment width) —
needed for cases where SegFormer over-merged and anchored splitting
hasn't fully separated the towers.

See [docs/plans/F-SKY2-osm-anchored-segments.md](../../docs/plans/F-SKY2-osm-anchored-segments.md).

### Satellite-derived building footprints (F-SKY8)

Microsoft Global ML Building Footprints (open data, ODbL) as a second
polygon source for regions where OSM is sparse. Cartagena's Bocagrande
waterfront is the canonical example: SegFormer sees the towers, OSM
doesn't have polygons for them, and the matcher has nothing to assign.
The satellite data covers them.

Opt in per region via `"use_satellite_footprints": true` in
`sites/<region>.json`. First run downloads ~12 MB / quadkey tile to
`runs/satellite_footprints_cache/` (gitignored); subsequent runs use
the cache. Polygons are de-duped against OSM by area-IoU (≥ 0.5);
OSM wins (it has height tags and stable IDs). Satellite-sourced
polygons inherit `height_tag_m=None, height_source="ms_buildings"` and
fall back to the existing sqrt-area `_height_proxy`.

See [docs/plans/F-SKY8-satellite-footprints.md](../../docs/plans/F-SKY8-satellite-footprints.md).

### Local-maxima peak detection (F-SKY7)

`detect_building_silhouettes` originally only split the contour at
sky valleys between towers. When SegFormer's building mask spans a
row of glass towers without sky valleys between them — common in
dense skylines like Cartagena's Bocagrande row seen across the bay —
the contour stays high (low y) everywhere and the global-prominence
filter rejects per-tower bumps. F-SKY7 adds a second-pass peak
detector that finds local maxima relative to a 40 px smoothed
baseline (≈ 6° of FOV at W=640) with a 6 px absolute prominence
floor, so monotone-but-bumpy rooflines still produce one peak per
tower. The new peaks merge with the existing sky-valley peaks via
the same de-dup step. See
[docs/plans/F-SKY7-local-max-peaks-and-layout.md](../../docs/plans/F-SKY7-local-max-peaks-and-layout.md).

### 1:1 dedup + considered-but-lost overlay (F-SKY6)

The matcher post-pass enforces one-to-one segment ↔ OSM building
uniqueness: if two segments both claim the same building, the one
with the lower combined score becomes unmatched (the loser still
keeps its `match_diagnostics` so the audit page shows what it
considered). The per-view minimap also gained an "orange dots" layer
showing OSM projections that the matcher scored as a top-3 candidate
for some segment but didn't win. Together these let you tell apart
three failure modes for an unmatched stretch of skyline: (i) no
orange dots → OSM data gap (nothing to match); (ii) orange dots
present but no segments → silhouette detector didn't carve peaks in
the central mask (next target); (iii) orange dots AND segments
present → matcher rejection (currently rare after F-SKY2.1). See
[docs/plans/F-SKY6-one-to-one-matching.md](../../docs/plans/F-SKY6-one-to-one-matching.md).

### SegFormer mask + page layout (F-SKY4 + F-SKY7)

Each per-view PDF page has three panels:
- **Top-left**: Street View image with skyline-segment overlays.
- **Bottom-left**: SegFormer building mask on its own (faint photo
  background + cyan mask) — direct side-by-side comparison with the
  segment panel above. Cyan present + no segment above = silhouette
  detector missed the peak; no cyan = SegFormer missed the building.
- **Right (full height)**: minimap with matched footprints, OSM context
  in grey, and orange "considered but lost" candidates (F-SKY6).

F-SKY7 replaced F-SKY4's cyan-overlaid-on-photo with the dedicated
bottom-left panel and removed the unused diagnostic legend table. The
mask is persisted on `SeedViewRegistration.building_mask` so the
renderer doesn't depend on the bounded LRU neural cache that would
miss by PDF-render time on multi-seed runs.

### Cross-view colour/geometry verification (F-SKY10)

When a segment is correctly matched to an OSM building, the building's
roof colour should agree across views: red clay tiles look red from
above (satellite) and red from the side (Street View). When the matcher
picks a wrong building (the classic waterfront failure on Cartagena
seed_5: matcher selects an inland tower for what is actually a waterfront
building visible at that bearing), the colours disagree — the visible
building has a different roof colour than the (wrong) OSM polygon.

F-SKY10 adds three independent cross-view signals that score each
segment-to-building match by colour and geometric consistency:

1. **Roof colour consistency (50%)**: Median RGB of the Street View
   segment's roof-strip pixels vs the satellite crop of the matched
   building's roof. Score = 1 - euclidean_distance(RGB_sv, RGB_sat) / 441.67.

2. **Geometric width consistency (30%)**: Segment aspect ratio heuristic.
   Very narrow (needle-like) segments and very wide (flat) segments are
   suspicious. Score peaks at 1:2–1:3 width-to-height ratio (typical
   facade seen in Street View).

3. **Vertical edge consistency (20%)**: Edge density in the segment
   region via Canny detection + Hough line filtering. A well-defined
   building facade has strong vertical edges (corners, wall seams).
   Foliage and noise have diffuse edges.

Combined score blends the three signals with weights [0.5, 0.3, 0.2]
and contributes a 15% nudge to the final matcher score (conservatively,
so intra-view IoU remains authoritative). When enabled, the cross-view
scorer runs after the base matcher and can rerank disputed candidates.

Opt in per region via `"use_cross_view_scoring": true` in
`sites/<region>.json`. First run downloads satellite imagery for the
bbox; subsequent runs use the cached satellite image. The scorer runs
per-view, so cost is minimal (no additional Street View fetches).

See [docs/plans/F-SKY10-F-SKY11.2-IMPLEMENTATION-2026-05-17.md](../../docs/plans/F-SKY10-F-SKY11.2-IMPLEMENTATION-2026-05-17.md)
for the F-SKY10 portion (the F-SKY11.2 portion is archived as failed).
The standalone F-SKY10 demo script has been removed; the F-SKY10 signal
is now exercised inside the main production pipeline.
See [docs/plans/F-SKY4-mask-overlay.md](../../docs/plans/F-SKY4-mask-overlay.md)
and [docs/plans/F-SKY7-local-max-peaks-and-layout.md](../../docs/plans/F-SKY7-local-max-peaks-and-layout.md).

### OSM-marker Voronoi instance indexing (F-SKY3, disabled)

SegFormer-b0 is semantic-only — no instance head, no separation between
adjacent buildings of the same class. F-SKY2 splits at clear mask gaps;
F-SKY3 fills the remaining hole: when a segment contains ≥ 2 OSM markers
but the mask has no visible valley between them (tightly packed
waterfront row, or a single contiguous SegFormer blob), partition the
segment by 1-D Voronoi over the OSM marker x_px values. Each marker gets
its own column strip and a per-strip silhouette is emitted, giving the
matcher one segment per OSM building. This is the cheap stand-in for
what a SAM-style instance segmenter would do with OSM centroids as
point prompts. Runs after `osm_anchor_silhouettes` so gap-based splits
(more precise) take precedence. See
[docs/plans/F-SKY3-osm-marker-instances.md](../../docs/plans/F-SKY3-osm-marker-instances.md).

### Floor-period diagnostic (F-SKY1, optional)

For each per-view building estimate, `_floor_period_for_building` looks
for the horizontal banding floors produce on a facade and reports the
dominant pixel period. Given the pinhole focal length and an assumed
3.2 m floor height, that gives an **OSM-independent distance + height
estimate**. Useful as a sanity check on the geometric path: a
disagreement between `forward_m` (from OSM) and `inferred_distance_m`
(from the period) means either the OSM footprint is wrong or the
period detection has latched onto a non-floor texture. See
[docs/plans/F-SKY1-floor-periodicity.md](../../docs/plans/F-SKY1-floor-periodicity.md).

## Module surface

The public symbols you'd build against:

```python
# pipeline.py
from city2stl.skyline.pipeline import (
    # Dataclasses
    Viewpoint, BuildingRecord, CapturedView, RegisteredBuildingEstimate,
    # Per-view registration
    register_view_to_osm,
    detect_skyline_contour,
    detect_building_silhouettes,
    detect_buildings_from_mask,
    match_segments_to_buildings,
    # Height estimation
    estimate_heights_from_registration,
    aggregate_building_heights,
    # Pano helpers
    stitch_pano_views,
    stitch_pano_masks,
    project_buildings_to_pano,
    # Neural masks (batched prefetch)
    prefetch_label_maps,
)

# region_pdf.py
from city2stl.skyline.region_pdf import run_region_pdf_report
```

## Site configuration

`sites/<region>.json` holds the bbox, seed URLs, and optional per-seed
overrides. Cartagena is the active baseline; Miami is a stub for the
next test region.

```json
{
  "name": "Cartagena",
  "north": 10.4295, "south": 10.3845,
  "east":  -75.5221, "west": -75.5679,
  "seed_urls": [
    "https://www.google.com/maps/place/.../@10.4020,-75.5457,3a,75y,88h,86t/...",
    ...
  ],
  "anchor_offsets_deg": {
    "seed_1": 135.0
  },
  "negative_seeds": ["seed_3"]
}
```

- **`seed_urls`** — Google Street View URLs; each becomes `seed_<N>`.
- **`anchor_offsets_deg`** (optional, per-seed) — manual pano-to-
  geographic heading offset in degrees. When present, the joint IoU
  optimization is **skipped** for that seed and this value is used
  directly. Use it when you can visually identify the correct compass
  direction from a seed's views but the algorithm finds a wrong local
  maximum of the IoU objective (common for seeds with buildings in
  many directions). See STATUS.md for the diagnosis.
- **`negative_seeds`** (optional) — seed names whose per-view height
  estimates are **excluded** from the aggregate. The views are still
  captured and rendered in the PDF (with a `[NEGATIVE EXAMPLE]` banner)
  so you can verify the pipeline correctly rejects them. Use this for
  camera positions that aren't skyline viewpoints (gas stations,
  under-bridge parking, building interiors). They serve as a regression
  fixture — the pipeline should produce ~zero useful contributions
  from them.
- **`max_plausible_height_m`** (optional, default 300) — regional
  building-height ceiling. Bounds both the glass-facade contour-override
  sanity check AND the per-building geometric y-consistency gate. Set
  this just above the region's tallest expected tower: Cartagena uses
  200 (Torre del Reloj ≈ 206 m), Chicago should keep 300+ (Willis ≈
  442 m, most are ≤ 200 m), Miami's 300 default covers Marquis (271 m).
  Lower caps reject implausible per-view estimates earlier; higher caps
  accept more candidate roof pixels at the cost of more noise.

Auto-proposed seeds supplement user-provided URLs; expect 5–11 total
locations screened per run.

## Environment variables

| Var | Purpose | Default |
|---|---|---|
| `GOOGLE_MAPS_API_KEY` | Street View Static API key (required) | — |
| `GOOGLE_MAPS_SIGN_SECRET` | URL-signing secret for paid Static API. When set, requests are HMAC-SHA1 signed and the default spin-view fetch size bumps to 1280×720. Unsigned requests are silently clamped by Google to 640×640 regardless of size, so this is the only way to get higher-resolution imagery. Find the secret in Google Cloud Console → APIs & Services → Credentials → URL signing secret. | unset (unsigned, 960×540 requested / 640×540 delivered) |
| `SKYLINE_CV_SEGFORMER_SIZE` | SegFormer model variant — `b0` (fastest, ~3 min on Cartagena), `b1`, `b2`, `b3` (default, ~5–6 min on Cartagena), `b4`, `b5` (most accurate, ~10× slower). b3 was promoted to default 2026-05-16 after measurement: on Cartagena it doubled the matched-tagged-building count (n=8 → 17), dropped MAE 22.13 → 13.73 m, and collapsed the cross-seed bias from +20.30 m to +0.87 m. **Production runs now pin `b1`** (see [docs/AGENT-GUIDE.md](docs/AGENT-GUIDE.md)) — ~3× faster than b3 with no matched-building loss on Cartagena. First run at a new size downloads ~190 MB (b3) / ~55 MB (b1) / ~80 MB (b0) to the HF cache. | `b3` |
| `SKYLINE_CV_SEGFORMER_BATCH` | Images per batched SegFormer forward pass. Each seed's spin is prefetched in one (chunked) pass before per-view work via `prefetch_label_maps`; measured ~2.4× faster on a 12-view spin (b1, CPU) with bit-identical label maps. `1` disables batching. | `12` |
| `OPENTOPO_API_KEY` | Optional, enables DEM-based terrain elevation for building bases. | unset |

## Caches

Two on-disk caches under `runs/` keep runs reproducible and fast:

- **`runs/seed_resolution_cache.json`** — per-seed-URL resolved pano
  (lat, lon, pano_id). Pins the first successful resolution so the
  Static API's location-snap doesn't drift between runs. Delete to
  force re-resolution.
- **`runs/image_cache/*.png`** — Street View image cache keyed by
  request hash (excluding API key). Delete to force fresh fetches
  (e.g. when a seed's snapped pano returns a placeholder image).

## Tests

```bash
python -m pytest tests/test_skyline.py -v   # 21 tests
```

Tests cover the CV math (URL parsing, frustum culling, occlusion
ordering, Hungarian matching, mask-based silhouette detection,
interval-IoU matcher, aggregation grouping). They do NOT cover the
orchestration in `region_pdf.py` — that's exercised by full-run smoke
tests against the saved Cartagena baseline.

## Dependencies

Beyond standard `requests`/`numpy`/`scipy`/`shapely`/`opencv-python`:

- `transformers` + `torch` for SegFormer-b0 (ADE20K) — **hard dependency**;
  the entire IoU objective and per-building masks rely on it.
- `matplotlib` for PDF rendering.
- `pillow` for image I/O.

## Files

```
skyline/
├── README.md
├── __init__.py
├── pipeline.py            ← CV primitives + math (F-SKY1/2/6/7)
├── region_pdf.py          ← orchestration + rendering, production entry
├── coastline_registration.py  ← F-SKY11/11.1 keypoint heading recovery
├── osm_water.py           ← F-SKY13 OSM coastline + water extraction (primary keypoint source)
├── cross_view.py          ← F-SKY10 cross-view colour/width/edges
├── depth_estimation.py    ← F-SKY12 Depth Anything V2 pano cross-check (diagnostic)
├── satellite_footprints.py    ← F-SKY8 Microsoft Building Footprints
├── satellite_image.py     ← ESRI satellite mosaic fetch
├── height_trace.py        ← F-SKY1 floor-strip detection
├── height_trace_render.py ← F-SKY1 diagnostic rendering
├── html_report.py         ← F-SKY15 HTML diagnostic report (tables live here)
├── docs/                  ← all in-repo docs live here
│   ├── AGENT-GUIDE.md     ← code navigation guide for future agents
│   ├── STATUS.md          ← what works / doesn't / next steps
│   ├── glass-roof-height-fix-plan.md
│   └── archive/           ← historical audits + plans
├── scripts/
│   ├── 08_region_skyline_pdf.py        ← production entry
│   ├── 09_height_trace.py              ← F-SKY1 diagnostic
│   ├── 13_heading_recovery_demo.py     ← multi-channel heading research
│   └── 14_seed5_diagnostic.py          ← seed-level registration diagnostic
├── sites/
│   ├── cartagena.json
│   ├── chicago.json
│   └── miami.json
└── runs/                  ← gitignored output (PDFs, image cache)
    ├── region_reports/    ← production PDFs
    ├── heading_recovery/  ← demo 13 output
    ├── image_cache/, satellite_*/, seed_resolution_cache.json
```
