# Index — 3D Maps

_Last updated: 2026-10-23_

The one index for developers and agents. Three parts:
1. [Doc map](#1-doc-map) — which document answers which kind of question.
2. [Where to edit](#2-where-to-edit) — task → the files that own it.
3. [Locator](#3-locator) — topic → `file::symbol`, split into the area pages in [index/](index/).

Conventions
- Code paths are relative to `Code/` (the workspace root): `map2stl/...`, `numpy2stl/src/numpy2stl/...`.
- Code is cited as `path::symbol`, never `path:line` (line numbers drift within weeks).
  - Resolve a symbol from `Code/`: `~/.venvs/graphify/Scripts/graphify explain "<symbol>"`, or grep `def <symbol>`.
- A bare name in backticks after a path, e.g. `path` (`a`, `b`), is another symbol in that file.
- Missing a topic? Search for it, then add a bullet here before finishing.

---

## 1. Doc map

Links are relative to `map2stl/docs/`.

**Start here**
- This file — the index.
- [../CLAUDE.md](../CLAUDE.md) — project rules, workflow, editing rules.
- [../../AGENTS.md](../../AGENTS.md) — multi-repo git and OneDrive rules (workspace root).

**Folders**
- [guides/](guides/) — **user** SOPs, served in the app at `/guides` (every `.md` there becomes a guide).
  - [city-stl-and-puzzle-sop.md](guides/city-stl-and-puzzle-sop.md), [large-region-sop.md](guides/large-region-sop.md), [printing-prusaslicer.md](guides/printing-prusaslicer.md)
- [reference/](reference/) — how the code works **now**.
  - [overview.md](reference/overview.md) — architecture, backend layout, two-stage mesh pipeline, caches.
  - [pipeline.md](reference/pipeline.md) — saved location → printable file, stage by stage.
  - [api.md](reference/api.md) — every route and its Pydantic models.
  - [sdk.md](reference/sdk.md) — notebooks → `TerrainSession` → routes.
  - [frontend.md](reference/frontend.md) — Vue + ES modules, `window.appState` ↔ Pinia, control ownership.
  - [frontend-modules.md](reference/frontend-modules.md) — JS/Vue module map and function index.
  - [packages.md](reference/packages.md) — geo2stl / city2stl / numpy2stl boundaries, app → library calls.
  - [layer-system.md](reference/layer-system.md), [projections.md](reference/projections.md), [composite-dem-design.md](reference/composite-dem-design.md)
  - [height-providers.md](reference/height-providers.md), [survey-sources.md](reference/survey-sources.md), [roof-shape-model.md](reference/roof-shape-model.md), [align-refinement.md](reference/align-refinement.md)
- [decisions/](decisions/README.md) — **why** the code is the way it is; one file per topic, entry format in its README.
- [plans/](plans/README.md) — the roadmap (every open item, one line each); plan files in `plans/active/`, `plans/done/`, `plans/archive/`.
- [research/](research/) — experiments whose findings still matter: [plate-critic.md](research/plate-critic.md), [shadow-heights.md](research/shadow-heights.md), [roof-shape-research.md](research/roof-shape-research.md).
- [issues.md](issues.md) — live bugs and technical debt.
- [proposals.md](proposals.md) — proposal tracker (pending → approved → done).
- [history/](history/README.md) — finished, abandoned or superseded work (audits, refactors, ML height training). Not how the code works now.

**Outside `map2stl/docs/`**
- Skyline (street-view heights): `map2stl/city2stl/skyline/README.md` (the one skyline doc), `map2stl/city2stl/skyline/docs/STATUS.md`.
- numpy2stl: `numpy2stl/README.md`, registration usage `numpy2stl/docs/registration.md`, registration design `numpy2stl/src/numpy2stl/registration/docs/ARCHITECTURE.md`.
- Checkpoints on disk and which are live: `map2stl/models/README.md` (only the roof-shape GBM runs; no CNN height model at runtime).
- Session state (unversioned): `claude/memory-bank/activeContext.md`.

---

## 2. Where to edit

Paths in this part are relative to `map2stl/`.

### First decision

| If the task is about... | Start here |
|---|---|
| Route shape, request payloads, validation | [reference/api.md](reference/api.md), then `app/server/routers/<family>.py` |
| Processing, caching, export generation, data fetching | `app/server/core/`, or the library below it (`geo2stl/`, `city2stl/`, numpy2stl) |
| The mesh itself (terrain, buildings, roads, water, puzzle) | [reference/pipeline.md](reference/pipeline.md); `app/server/core/export.py::terrain_stage` → `city2stl/city_model.py::build_on_terrain` |
| Notebook / Python SDK | [reference/sdk.md](reference/sdk.md), `app/session/terrain_session.py` |
| Browser UI | [design-guidelines.md](design-guidelines.md) (how pages look and behave; read first), [reference/frontend.md](reference/frontend.md), [reference/frontend-modules.md](reference/frontend-modules.md) |
| Building heights | [reference/height-providers.md](reference/height-providers.md), `city2stl/height/` |
| Roofs, building parts, landmarks | `city2stl/roofs.py`, `city2stl/landmarks.py`, `city2stl/city_model.py` (`drop_building_copies`: churches/towers/fortifications copies of buildings; keeps the copy of an `is_open_enclosure`) |
| Plate registration / align tool | [Registration locator](index/numpy2stl-registration.md), `city2stl/registration/`, numpy2stl `registration/` |
| Skyline (street-view heights) | `city2stl/skyline/README.md` |
| User guides (/guides) | `docs/guides/*.md` (content), `app/server/routers/guides.py` (rendering) |
| Tests | `tests/conftest.py`, the matching `tests/test_*.py`; JS tests in `tests/js/`; while developing `scripts/quicktest.py` (affected tests only) |

### Router or core?
- Edit `app/server/routers/` when the HTTP shape, validation boundary or endpoint orchestration changes.
- Edit `app/server/core/` (or the library) when processing, caching, export or fetch internals change.
- Libraries stay app-free: numpy2stl is geo-free; geo2stl/city2stl never import `app`. [why](decisions/architecture.md)

### Route families (`app/server/routers/`)

| Concern | Router |
|---|---|
| DEM (`dem_id`), water, land cover, satellite, hydrology, trails, sources | `terrain.py` |
| STL/OBJ/3MF, preview, preflight, puzzle, async export tasks | `export.py` |
| OSM city layers, fetch tasks, city raster, height enhancement, landmarks | `cities.py` |
| Server composite (city raster, DEM merge, hydrology merge) | `composite.py` |
| Saved regions, settings blob, landmark overrides | `regions.py` |
| Mesh import (upload / library) → heightmap → registration | `layers.py` |
| Plate registration packs + tasks, model critic | `registration.py` |
| Height sources | `height.py` |
| Guides, reports, usage log | `guides.py`, `reports.py`, `usage.py` |
| Settings, cache, API keys / tile folder, place search, diagnostics | `settings.py`, `cache.py`, `auth.py`, `geocode.py`, `diagnostics.py` |

### Frontend by view (`app/client/static/js/`)
- Markup is Vue (`vue/components/{layout,sidebar,views,dem,shared}/`); behaviour is mostly ES modules (`modules/`).
  - Control ids are the interface between them: grep an `id=` before renaming it.
  - `.vue`/`.ts` edits need `npm run build`; `modules/**` is served raw.
- Explore: `modules/map/`, `modules/regions/`, `app/client/static/js/vue/components/views/LandmarkSearch.vue`.
- Edit: `modules/dem/`, `modules/layers/`, `modules/ui/`, `vue/components/dem/`.
- Extrude: `modules/export/`, `app/client/static/js/vue/components/views/ModelContainer.vue`.
- Shared state: `window.appState` (bridged into the Pinia store `app/client/static/js/vue/stores/app.ts`) — [reference/frontend.md](reference/frontend.md).

### Quick examples

| I want to... | Start here |
|---|---|
| Add a DEM source or option | `geo2stl/dem.py`, `app/server/routers/terrain.py`, `app/client/static/js/modules/dem/dem-main.js` |
| Change a city layer's geometry in the print | `city2stl/city_model.py` (`feature_polygons`, `build_layer`) |
| Add an export format | `app/server/core/export.py`, `app/server/routers/export.py`, then `modules/export/` |
| Add a composite layer | `app/server/routers/composite.py`, `app/client/static/js/modules/layers/composite-spec.js`, `app/client/static/js/vue/components/dem/CompositeDemSection.vue` |
| Add a height provider | `city2stl/height/providers/`, register in `city2stl/height/service.py` |
| Fix region save/load | `app/server/routers/regions.py`, `modules/regions/`, `app/client/static/js/modules/ui/presets.js` |
| Add a notebook helper | `app/session/terrain_session.py` |
| Write or fix a user guide | `docs/guides/` (keep file names: they are the `/guides/<slug>` URLs) |

### Before editing
- Rules: [../CLAUDE.md](../CLAUDE.md). Open work: [plans/README.md](plans/README.md), [issues.md](issues.md).
- A significant unrequested feature needs a [proposals.md](proposals.md) entry and a plan first.

---

## 3. Locator

Rows live in one page per area (each opens with a scope line). Pick the area, open that page only.

| Area | Page |
|---|---|
| Two-stage mesh pipeline, export, tasks, puzzle, preflight, caches | [index/mesh-pipeline.md](index/mesh-pipeline.md) |
| city2stl: city model, OSM fetch, building heights, roofs, landmarks | [index/city2stl.md](index/city2stl.md) |
| geo2stl (DEM, imagery, projections, water, trails), server composite, regions, settings | [index/geo2stl-server.md](index/geo2stl-server.md) |
| Frontend, guides and reports pages, Python SDK (function index: [reference/frontend-modules.md](reference/frontend-modules.md)) | [index/frontend-sdk.md](index/frontend-sdk.md) |
| numpy2stl meshes, registration, plates, align tool | [index/numpy2stl-registration.md](index/numpy2stl-registration.md) |
| Skyline (street-view building heights) | [index/skyline.md](index/skyline.md) |
| Photo-label tool: photo pairing, camera solve, Google 3D registration, our model over Google 3D | [index/photo-label.md](index/photo-label.md) |
| Entry points, scripts, tests, tooling | [index/entry-points.md](index/entry-points.md) |

Most-used direct pointers
- Stage 1, DEM to terrain: `map2stl/app/server/core/export.py::terrain_stage`; stage 2, city layers on it: `map2stl/city2stl/city_model.py::build_on_terrain`.
- Export entry points: `map2stl/app/server/core/export.py::generate_stl`; request context `map2stl/app/server/core/export_params.py::ExportContext`.
- DEM cache key: `map2stl/app/server/core/dem_cache.py::dem_cache_key`.
- Height service: `map2stl/city2stl/height/service.py`; providers `map2stl/city2stl/height/providers/`.
