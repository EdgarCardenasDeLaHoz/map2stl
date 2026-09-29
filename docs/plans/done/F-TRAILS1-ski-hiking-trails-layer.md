# F-TRAILS1 — Ski and hiking trails layer

**Status:** done (2026-08-27) — server, client, and tests landed and verified live on
Breckenridge (340 ski + 859 hiking from `osm+usfs`). Update 2026-09-28: trails now reach the
STL as a City Model layer (F-CITYMODEL), and `/api/composite/hydrology-merge` was fixed
2026-09-06 (issues history §2). Decisions: [decisions/trails.md](../../decisions/trails.md).

**Follow-up (2026-08-28):** areal features were being buffered as polygons, so every piste
or ski-area boundary mapped as a closed way rasterized as a solid blob. `rasterize_trails`
now reduces areal geometry to its boundary and returns the interiors separately, as the
0/1 display masks `ski_area_grid` / `hiking_area_grid`. The client tints those interiors at
alpha 46 in the category colour, so a ski area reads as an extent rather than a fill; the
masks are never merged into the DEM. See `docs/issues.md` § Resolved Bugs.
**Requested by:** user, 2026-08-27 — "add a new layer called trails, this should use osm
and every other source to populate ski and hiking trails, giving me the option to toggle
each between the two. it should work somewhat like the river hydrology layer."

## Goal

A stacked map layer named **Trails** that fetches ski pistes and hiking paths for the
current bounding box, rasterizes them to a relief grid, and renders them as an overlay.
The two categories are toggled independently in the UI, and toggling re-renders from the
already-fetched payload rather than issuing a new request.

The hydrology layer is the architectural template: a provider base class in `geo2stl`, a
service that dispatches to the requested provider, a cached server endpoint with
in-flight de-duplication, and a client overlay module that owns an offscreen canvas
mirrored onto `window.appState`.

## Approach

- **`geo2stl/trails.py`** — `TrailsLayerBase` interface plus two providers. `OsmTrailsLayer`
  queries Overpass through osmnx (already a dependency, already endpoint-rotating in
  `city2stl/fetch.py`) for `piste:type` (ski) and `highway=path|footway|track|bridleway|steps`
  / `route=hiking` (hiking). `UsfsTrailsLayer` queries the USDA Forest Service EDW ArcGIS
  layer for US national-forest trails — hiking only, no API key. A `TrailsService`
  dispatches by `source`, where `all` unions every provider that applies to the bbox and
  de-duplicates by rasterizing into one grid per category.
- **Two grids, not one.** The fetch returns `ski_grid` and `hiking_grid` separately so the
  client can toggle either category without refetching. A single categorical grid would
  force a round trip on every toggle.
- **`/api/terrain/trails`** in `app/server/routers/terrain.py`, mirroring
  `get_terrain_hydrology`: bbox validation, a `trails` array cache keyed on everything that
  changes the raster (`dim`, source, relief, width, categories), an `asyncio.Future`
  in-flight map, projection applied per-request on top of the cached raw grids, and a
  `TEST_MODE` short-circuit.
- **`trails-overlay.js`** modelled on `hydrology-overlay.js` — module-scope abort
  controller and in-flight key, `window.loadTrails()` / `clearTrails()` / `cancelTrailsLoad()`,
  and a `renderTrails()` that paints ski in cyan and hiking in orange onto an offscreen
  canvas stored at `window.appState.trailsSourceCanvas`. The last payload is retained so a
  checkbox change re-renders locally.
- **Layer registration** — a `Trails` entry in `LAYER_CANVAS_IDS`, `_layerOrder` (above
  imagery, below city so paths stay visible), `_layerOpacities`, and `sourceMap` in
  `stacked-layers.js`; a mode button and opacity row in `LayerViewSection.vue`; a controls
  block in `FetchLayersSection.vue`; wiring in `event-listeners.js`; teardown in
  `clearLayerCache()`.

## Target files

| File | Change |
|---|---|
| `geo2stl/trails.py` | New — providers, service, rasterizer |
| `geo2stl/__init__.py` | Export `fetch_and_rasterize_trails` |
| `app/server/routers/terrain.py` | New `GET /api/terrain/trails` |
| `app/server/core/cache.py` | `trails` namespace TTL |
| `app/client/static/js/modules/layers/trails-overlay.js` | New — fetch + render + toggles |
| `app/client/static/js/main.js` | Import the new module |
| `app/client/static/js/modules/core/api.js` | `api.dem.trails()` |
| `app/client/static/js/modules/layers/stacked-layers.js` | Register the layer |
| `app/client/static/js/vue/components/dem/FetchLayersSection.vue` | Trails controls |
| `app/client/static/js/vue/components/dem/LayerViewSection.vue` | Mode button + opacity row |
| `app/client/static/js/modules/events/event-listeners.js` | Button wiring |
| `app/client/static/js/app.js` | Clear on region change |
| `app/client/static/js/modules/ui/presets.js` | Save/restore trail settings |
| `tests/test_trails.py` | New — rasterizer, service dispatch, endpoint |

## Success criteria

- `GET /api/terrain/trails?north=…&south=…&east=…&west=…` returns two base64 grids plus
  per-category feature counts, and a second identical request is served from cache.
- Loading a mountain region (Breckenridge) yields a non-zero ski count and a non-zero
  hiking count; the overlay shows two visually distinct colours.
- Unchecking "Ski" repaints without a network request.
- Switching regions clears the layer and aborts any in-flight fetch.
- The full pytest suite stays green.

## Risks and known limits

- **Overpass is slow and rate-limited.** Trail tags are far denser than river geometry in
  populated valleys. Mitigated by the same endpoint rotation `city2stl/fetch.py` uses, the
  30-day array cache, and in-flight de-duplication — but a first fetch over a large bbox
  can take tens of seconds.
- **"Every other source" is thinner than it sounds for ski.** OpenSkiMap and OpenSnowMap
  are both rendered *from* OSM and publish tiles or whole-planet dumps, not bbox vector
  queries, so adding them would duplicate OSM data at high cost. OSM is therefore the
  authoritative ski source here. The genuinely independent source found is the USDA Forest
  Service trails layer, which is hiking-only and US-only; it is wired in as a second
  provider and unioned with OSM when `source=all`.
- **Thin features and print scale.** A trail buffered to the two-pixel minimum can vanish
  at coarse `dim`. The width control exists for that; the default engraves rather than
  raises, since a groove survives slicing better than a sub-millimetre ridge.
- Trails are not merged into the exported DEM in this change. The hydrology merge path
  (`/api/composite/hydrology-merge`) is the precedent if that is wanted later — note that
  it currently imports a module (`app.server.core.hydrology`) that does not exist, so that
  precedent needs its own fix first.
