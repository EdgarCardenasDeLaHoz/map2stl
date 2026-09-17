# strm2stl v2

A second implementation of the saved-location-to-STL pipeline, built to be compared against
v1 rather than to replace it. Both run at the same time, against the same `data.db` and the
same `geo2stl` / `numpy2stl` libraries, on different ports.

| | v1 | v2 |
|---|---|---|
| Server | `strm2stl/app/server/` | `strm2stl/v2/server/` |
| Client | `app/client/` (plain JS modules + a partial Vue tree) | `v2/web/` (Svelte 5 + SvelteKit) |
| Port | 9000 | 9100 |
| Compute | `geo2stl`, `numpy2stl` | the same, unchanged |

## Running it

```bash
# API + the built page, one process
cd strm2stl && python -m uvicorn v2.server.main:app --port 9100
```

```bash
# Frontend dev server with hot reload, proxying /api to port 9100
cd strm2stl/v2/web && npm run dev
```

The FastAPI process serves `v2/web/build` at `/`, so after `npm run build` port 9100 alone is
the whole application. Both are registered in `.claude/launch.json` as `strm2stl-v2` and
`strm2stl-v2-dev`.

## What is different, and why

### The DEM handle replaces the cache-key handshake

v1's pipeline is two independent HTTP calls joined by a disk cache. `GET /api/terrain/dem`
writes the array under an MD5 of the bounding box plus seven settings; `POST /api/export/start`
rebuilds that same key from settings the client sends a second time, and reads the array back.
When the two derivations disagree by so much as a float, the export fails with
`Missing DEM data`. That has happened twice in this project's history.

v2 issues an opaque id. `POST /api/dem` fetches the grid, keeps it, and returns a `demId`.
`POST /api/export` takes that id and nothing else about the terrain. The client never derives
a key, so the two halves cannot disagree. See `server/store.py`.

### One settings object instead of 464 DOM ids

v1's client reads settings back out of the document at save time with roughly 712
`getElementById` calls. The id string is the interface between a template and the module that
reads it, and renaming one silently breaks the other because there is no import to fail.

v2 binds controls to fields of a single `RegionSettings` object (`web/src/lib/state.svelte.ts`).
A rename is a compile error. That is also what makes progressive disclosure cheap: every
control goes through one `Field` component, so marking a group advanced is a one-word change
rather than a hand edit of the markup.

### Things v1 does not have

- **Cancel.** The pipeline is asked between stages whether it should still be running, so a
  large export can be stopped instead of running to completion in a daemon thread nobody is
  watching. Verified: a 1400x1400 export stopped 6.1 s in, mid-mesh.
- **Pushed progress.** Progress arrives over SSE, one event per real change. v1 polls every
  250 ms — about 480 requests to observe eight state changes on a two-minute render.
- **A versioned settings blob.** `schemaVersion` plus a migration, so the `water.sat_scale`
  to `water.dim` and `esa` to `satellite` renames that are still visible in live v1 rows are
  handled on read instead of leaving rows that half-load.
- **A size readout before the render.** The panel says what the current settings will produce
  in millimetres and triangles. v1 gave no feedback until the file was on disk.
- **Repeatable downloads.** v1 deletes the file as it serves it, so an interrupted download
  means rendering again. v2 keeps it until the TTL sweep.

## Defects found in v1 while building this

Both are fixed in v2 and still present in v1.

1. **Sea-level cap is inverted.** `app/server/core/export.py:144` uses `np.minimum(im, 0.0)`
   for a control whose own tooltip promises to "clamp all ocean surfaces to z=0". `np.minimum`
   flattens the land and keeps the trenches — the exact opposite. v2 uses `np.maximum`.
2. **Base thickness does nothing.** Both versions add `baseHeight` to every elevation, but
   `array_to_mesh` puts the bottom cap one unit below the surface's lowest point, so the cap
   moves up with it and the solid is always `modelHeight + 1` mm tall. v2 passes
   `floor_val=0`, which is what makes the setting a real thickness. Measured: with
   modelHeight 30 and base 10, v2 exports a 40 mm part where v1 exports a 31 mm one.

A third defect is in `geo2stl` and shared by both: when no local SRTM tile covers the
requested area, the tile reader prints a warning and returns an array of zeros. v1 accepts
that array and exports a flat slab. v2 does not change the library — it rejects a grid with
no relief (`pipeline._reject_degenerate`) and reports which source had no data.

## Layout

```
v2/
├── server/
│   ├── main.py        14 API routes; also serves web/build at /
│   ├── pipeline.py    the only module that knows the shape of a render
│   ├── store.py       DEM handles — the architectural centrepiece
│   ├── jobs.py        background jobs, progress, cancel
│   ├── regions.py     read-only region access, settings read/write with migration
│   ├── models.py      pydantic request/response models
│   └── config.py      paths, limits, the OpenTopography key
└── web/
    └── src/
        ├── lib/
        │   ├── api.ts             one request helper, every response type
        │   ├── state.svelte.ts    all application state, one object
        │   └── components/        Field, Section, SettingsPanel, RegionList,
        │                          MapView, TerrainView, ModelView, ExportBar, Notices
        └── routes/+page.svelte    the whole screen: regions, viewport, settings, actions
```

## Status

Verified end to end on Breckenridge: DEM from `h5_local` in 0.31 s, watertight STL of 126,988
faces at 249 x 249 x 40 mm, downloaded twice from the same job. Region list, settings
auto-save, satellite/water/land-cover overlays, the three viewport tabs, SSE progress and
cancel all work.

Out of scope, and still v1-only: city and OSM building models, the building-height model, the
composite/stacked-layer view, and the skyline tools.
