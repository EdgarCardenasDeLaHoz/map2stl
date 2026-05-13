# Skyline CV Baseline

This folder is a self-contained research scaffold for a computer-vision approach to building-height estimation.

It does not replace the current raster-first height stack. The goal is to make a Cartagena baseline easy to inspect, run, and compare against the existing Google 3D / WSF3D / shadow / nDSM pipeline.

All skyline-specific artifacts now live under `city2stl/skyline_cv/`, including staged run outputs, review bundles, site seed configuration, and region PDF reports.

## Process Summary

1. Resolves a few known Cartagena viewpoints by name.
2. Loads persisted Street View seed URLs from `sites/<city>.json` when available.
3. Pulls building footprints and roads from OpenStreetMap, preferring cache before live fetch.
4. Downloads Street View imagery for seeds and nearby multiview headings.
5. Detects the skyline/building boundary using a top-connected sky mask instead of a naive bright-sky threshold.
6. Registers observed skyline structure against projected OSM building locations.
7. Estimates per-building heights from the registered skyline geometry and aggregates them across views.
8. Produces review/debug artifacts and a single PDF report for region-driven screening runs.

## Enabled Here

- Staged skyline CV pipeline for one-city research runs.
- Region-driven skyline screening tied to saved project regions.
- Persisted seed URL storage in `sites/<region>.json`.
- Automatic `.env` loading from `strm2stl/.env` for Google Maps API key resolution.
- Auto-proposed candidate viewpoints using road geometry, skyline spread, and elevation preference.
- OSM footprint overlays and heading vectors on region report maps.
- Seed multiview registration and extracted building-height summary pages.
- Manual review bundle generation and manual correction application.

## Why this shape

The existing height code in `city2stl/height/` is raster-centric. That is still the production path.

This folder is deliberately separate because the computer-vision approach has different failure modes:

- it depends on viewpoint quality,
- it is sensitive to skyline occlusion and weather,
- it needs multiple images to stabilize height estimates,
- and it should be audited as a research branch before any integration.

## Requirements

- `GOOGLE_MAPS_API_KEY` or `GOOGLE_STREETVIEW_API_KEY` for Street View.
- Internet access for Overpass and Nominatim.
- The existing repo dependencies already include `requests`, `numpy`, `scipy`, `opencv-python`, and `shapely`.

## Scripts

Run the staged workflow from the repo root:

```bash
python city2stl/skyline_cv/scripts/00_show_site.py
python city2stl/skyline_cv/scripts/01_fetch_osm.py
python city2stl/skyline_cv/scripts/02_fetch_streetview.py
python city2stl/skyline_cv/scripts/03_register_views.py
python city2stl/skyline_cv/scripts/04_estimate_heights.py
python city2stl/skyline_cv/scripts/05_report.py
python city2stl/skyline_cv/scripts/06_review_bundle.py
python city2stl/skyline_cv/scripts/07_apply_review_bundle.py
```

Or run the full demo in one pass:

```bash
python city2stl/skyline_cv/scripts/run_cartagena_demo.py
```

That one-command demo also writes a review bundle under `city2stl/skyline_cv/runs/cartagena/06_review/` so you can inspect or correct the view-to-building alignment without rerunning the whole pipeline.

Outputs land in `city2stl/skyline_cv/runs/cartagena/` by default.

Region PDF outputs now land in `city2stl/skyline_cv/runs/region_reports/` by default.

## Region-Tied Single PDF Mode

To tie skyline screening to an existing saved project region and generate one PDF (instead of a directory tree), use:

```bash
python city2stl/skyline_cv/scripts/08_region_skyline_pdf.py \
	--region your_region_name \
	--seed-url "https://www.google.com/maps/place/Virgen+del+Carmen+Bahia+De+Cartagena/@10.4020262,-75.5456527,3a,75y,88.94h,86.82t/..." \
	--seed-url "https://www.google.com/maps/..." \
	--out city2stl/skyline_cv/runs/region_reports/your_region_skyline_report.pdf
```

Behavior:

- Loads bbox from the `regions` SQLite table.
- Loads default seed URLs from `city2stl/skyline_cv/sites/<region>.json` `seed_urls` when present.
- Loads Google Maps credentials from `strm2stl/.env` if they are not already present in the process environment.
- Reuses cached OSM city data first (with key-parameter fallback), then live-fetches only if cache is missing.
- Uses persisted seed URLs and merges any `--seed-url` values you pass at runtime.
- Auto-proposal now favors farther standoff distances and higher elevation points.
- Screens each location with Street View + skyline-quality heuristics.
- Uses a top-connected sky detector to reduce false skyline picks from bright sky regions.
- Draws OSM building footprints on the screening map.
- Draws heading vectors on the screening map to show camera angles used.
- For seed locations, captures multiple headings and runs skyline registration + height extraction.
- Produces a single PDF with summary, map+angles, per-location screenshots, seed registration overlays, and extracted building height tables.

## Notes for the next iteration

- Add a manual correspondence editor if the automatic skyline registration is too loose.
- Add an optional Google Earth / oblique imagery source once the Street View baseline is characterized.
- Compare the fused results against `height_source`-aware OSM defaults and the current `google3d` provider.
- Use `06_review_bundle.py` and `07_apply_review_bundle.py` when you want to hand-correct weak skyline matches.
