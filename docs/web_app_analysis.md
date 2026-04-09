# 3D Maps — Web Application Reference

_Last updated: 2026-04-09_

This document is the single reference for the state of the FastAPI web application, covering architecture, implemented features, known gaps, and developer notes.

---

## Architecture Overview

| Layer | Location | Notes |
|-------|----------|-------|
| HTTP server | `server.py` | FastAPI entry point, port 9000 |
| Routers | `app/routers/` | 7 router modules (cache, cities, composite, export, regions, settings, terrain) |
| Business logic | `app/core/` | cache.py, db.py, dem.py, export.py, osm.py, cities_3d.py |
| Config / schemas | `config.py`, `schemas.py` | Pydantic models, path constants |
| HTML shell | `templates/index.html` | ~1 448 lines |
| CSS | `static/css/app.css` | ~3 180 lines |
| JS entry | `static/js/main.js` | Bootstraps ES module imports |
| JS modules | `static/js/modules/` | Fully modularised — see JS Module Map below |
| DEM processing | `geo2stl/geo2stl.py` | Tile stitching, projections |
| Earth Engine | `geo2stl/sat2stl.py` | Water mask, ESA land cover |
| STL generation | `numpy2stl/` | Mesh output (STL, OBJ, 3MF) |
| Region data | `data.db` | SQLite — regions + per-region settings |

**Entry point:** `python server.py` (or `make serve`) → `http://localhost:9000`

---

## Key API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/regions` | Typed region list |
| POST | `/api/regions` | Create a new region |
| PUT | `/api/regions/{name}` | Update bbox + metadata (used by app.js "Save bbox") |
| DELETE | `/api/regions/{name}` | Delete region |
| GET | `/api/regions/{name}/settings` | Get per-region settings |
| PUT | `/api/regions/{name}/settings` | Save per-region settings |
| GET/POST | `/api/terrain/dem` | Fetch DEM elevation data (primary route used by app.js) |
| GET/POST | `/api/terrain/dem/raw` | Fetch raw unprocessed DEM array |
| GET/POST | `/api/terrain/water-mask` | Fetch water mask + ESA land cover (used by app.js) |
| GET/POST | `/api/terrain/satellite` | Fetch satellite imagery |
| GET | `/api/terrain/sources` | List available DEM datasets (used by merge panel) |
| GET | `/api/terrain/elevation-profile` | Cross-section endpoint (returns 501) |
| POST | `/api/terrain/composite` | Composite DEM from stacked layers |
| POST | `/api/dem/merge` | Merge multiple DEM layers |
| GET | `/api/cities/cached` | Check if city data cached for bbox |
| POST | `/api/cities` | Fetch city data from Overpass API |
| POST | `/api/export/stl` | Generate STL (used by app.js) |
| POST | `/api/export/obj` | Generate OBJ |
| POST | `/api/export/3mf` | Generate 3MF |
| POST | `/api/export/preview` | Return elevation values for Three.js preview |
| GET | `/api/cache` | Server-side cache status |
| DELETE | `/api/cache` | Clear server-side cache |
| GET | `/api/cache/check` | Check if specific bbox is cached |
| GET | `/api/settings/projections` | List available projections |
| GET | `/api/settings/colormaps` | List available colormaps |
| GET | `/api/settings/datasets` | List available DEM datasets |
| GET | `/api/global_dem_overview` | Cached global DEM PNG overview |

---

## Implemented UI Features

### Navigation
- Three main views: **Explore** (2D map + globe), **Edit** (DEM preview + settings), **Extrude** (3D model)
- Tab switching persists state; settings panel collapses to free space

### Explore View
- Leaflet map with 7 base tile options (OSM, OpenTopo, ESRI World/Topo, CartoDB Light/Dark, Stamen Terrain)
- Optional hillshade terrain relief overlay with opacity slider
- Three.js globe with region markers
- Sidebar: 3-state (compact → expanded table → hidden), 👁 visibility toggle for bbox rectangles and edit markers
- Region table in expanded state: N/S/E/W, dimension, Edit + Map buttons

### Edit View (DEM Preview)
- Stacked layer canvas: DEM, Water Mask, Satellite, Gridlines rendered as independent overlays
- Settings panel (640 px wide, collapsible to zero with vertical re-open tab)
- Inline mini-map for drag-to-resize bounding box (Leaflet rectangle with handles)
- Bbox inputs (N/S/E/W) with ↺ Reload and 💾 Save bbox buttons
- Save bbox persists edits to backend via `PUT /api/regions/{name}`

### Settings Panel Sections
| Section | Controls |
|---------|----------|
| Histogram & Curves | Elevation histogram, interactive transfer function (Linear / Peaks / Depths / S-Curve) |
| Colormap | Colormap picker, min/max override, sea level buffer |
| Map Display | Map style, terrain overlay, gridlines toggle + count, auto-reload, map projection |
| Resolution | DEM dimension, dataset selector (ESA / Copernicus / NASADEM / USGS / GEBCO) |
| Layers | Per-layer visibility, opacity, satellite scale |
| Compare | Side-by-side view of two regions with independent colormap + exaggeration |

### Map Projections (client-side, no data reload)
- None (Plate Carrée), Cosine Correction, Web Mercator, Lambert Equal-Area, Sinusoidal
- Applied to DEM, water mask, land cover, and gridline overlays

### Data & Export
- DEM colorbar inline with bbox row
- Zoom & pan on all layer canvases (wheel/pinch, drag)
- Hover tooltip: elevation + lat/lon
- Elevation curve editor applied before rendering
- 3D model preview (Three.js), STL / OBJ / 3MF download

### Region Management
- Preset profiles (5 built-in + custom, localStorage)
- Favorites (star icon, localStorage)
- Region notes modal (localStorage)
- Keyboard shortcuts: Ctrl+1–4 (tabs), Ctrl+S (save), Ctrl+R (reload), Arrows (navigate regions)

---

## Known Gaps & Pending Work

| Item | Location | Priority |
|------|----------|----------|
| `GET /api/terrain/elevation-profile` | `app/routers/terrain.py` | Medium — cross-section transect, returns 501 |
| Live bbox save feedback in sidebar table | `templates/index.html` | Low |

---

## Caching

| Cache type | Location | Cleared by |
|------------|----------|-----------|
| Earth Engine tiles | `cache/ee/` | `DELETE /api/cache` or manual |
| Server-side DEM/OSM | `cache/` (SQLite-backed) | `DELETE /api/cache` or manual |
| Client layer data | JS module state | `clearLayerCache()` on bbox change |

Cache directories are `.gitignore`d. Delete them freely — they regenerate on next request (Earth Engine requires auth).

---

## Running Tests

```bash
# From strm2stl/ with venv active
python -m pytest tests/ -v
```

108 tests across 7 files. The pre-commit hook runs them automatically — all must pass before a commit is accepted.

---

## Developer Notes

- **Adding a region programmatically:** POST to `/api/regions` with `{name, north, south, east, west}`.
- **Saving an edited bbox:** PUT to `/api/regions/{name}` with full region payload. The UI `💾 Save bbox` button does this.
- **Adding a backend endpoint:** add a route to the relevant `app/routers/X.py` file; import it in `server.py`.
- **Adding a JS feature:** add it to the relevant module under `static/js/modules/`. See `docs/modules.md` for the module map.
- **Cache invalidation:** client calls `clearLayerCache()` before any bbox change.
- **Projection:** handled in `static/js/modules/dem/dem-loader.js`. Adding a new projection: add a branch there and a `<select>` option in `templates/index.html`.
