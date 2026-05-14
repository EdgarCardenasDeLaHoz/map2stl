# skyline_cv

Computer-vision pipeline that estimates per-building heights for a city
region by registering Google Street View imagery against OpenStreetMap
building footprints. Research branch — not part of the production height
stack in `city2stl/height/`.

## Quick start

```bash
# from strm2stl/
export GOOGLE_MAPS_API_KEY=...   # or put in strm2stl/.env

PYTHONPATH=. python city2stl/skyline_cv/scripts/08_region_skyline_pdf.py \
  --region Cartagena
```

That writes
`city2stl/skyline_cv/runs/region_reports/Cartagena_skyline_report.pdf`,
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

See [STATUS.md](STATUS.md) for the current honest picture of what works,
what's flaky, and what's broken. Short version:

- Heading registration is reliable when seeds are placed across water
  from a tall-building cluster (Bocagrande from across the bay).
- Heights systematically under-predict tall glass towers by 50–100 m —
  this is the main open product gap.
- Cross-seed coverage (the only honest validation signal) is currently
  ~3 buildings per run, which makes the height MAE numbers noisy.

## How it works

The pipeline is in two files:

| File | Role | Lines |
|---|---|---|
| [pipeline.py](pipeline.py) | CV primitives: SegFormer integration, projection, registration, height extraction, aggregation. Pure functions, easy to test. | ~2400 |
| [region_pdf.py](region_pdf.py) | Orchestration + Street View I/O + seed selection + PDF rendering. Stateful; harder to test in isolation. | ~2750 |

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

## Module surface

The public symbols you'd build against:

```python
# pipeline.py
from city2stl.skyline_cv.pipeline import (
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
)

# region_pdf.py
from city2stl.skyline_cv.region_pdf import run_region_pdf_report
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

Auto-proposed seeds supplement user-provided URLs; expect 5–11 total
locations screened per run.

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
python -m pytest tests/test_skyline_cv.py -v   # 21 tests
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
skyline_cv/
├── README.md
├── STATUS.md              ← what works / doesn't / next steps
├── __init__.py
├── pipeline.py            ← CV primitives + math
├── region_pdf.py          ← orchestration + rendering
├── docs/
│   ├── cartagena-audit-2026-05.md     ← session audit
│   └── implementation-plan.md         ← historical plan
├── scripts/
│   └── 08_region_skyline_pdf.py       ← only entry point
├── sites/
│   ├── cartagena.json
│   └── miami.json
└── runs/                  ← gitignored output (PDFs, image cache)
```
