# F-FRAME: projected rectangle frame

Every print becomes an axis-aligned rectangle in the projection's own x/y plane, with real terrain
in every cell. This page is the plan; status lives in the roadmap row in [../README.md](../README.md).

User-requested 2026-10-17. Proposal: [../../proposals.md](../../proposals.md) › F-FRAME.

## Goal

- Each region stores its own frame (Cover / Inside / Custom). The region bbox is unchanged.
- The DEM, every raster layer, vector features, the print scale and the client overlays share one
  exact projected affine.
- Fixes the "wings" slab ([../../issues.md](../../issues.md) › "Projections: wings printed as a flat
  slab"). On the Europe region under laea, today's corners are NaN, and `prepare_dem` fills them
  with the minimum elevation.

## Approach

- **One projection model.** New `geo2stl/frame.py::ProjectionModel(region_bbox, projection)` has
  `forward(lat, lon) -> (x_km, y_km)` and `inverse`.
  - It wraps `_ConicAzimuthalModel` for laea and lcc.
  - It adds closed forms for none, cosine, equidistant, mercator, lambert, miller, gall and
    sinusoidal.
  - The anchor (centre, parallels, avgcos) always comes from the region bbox. Moving the frame
    never changes the projection.
- **Today's rectangle is the Cover rectangle.** The rectangle the code builds today is the bounding
  box of the projected bbox outline. The wings are NaN only because the source grid stops at the
  bbox.
  - Cover keeps today's shape.
  - It fills the wings by sampling a source grid fetched over a larger *fetch bbox*.
- **One resampler.** `frame.py::project_to_frame(src, src_bbox, model, frame, out_shape, order)`
  inverse-projects each output pixel centre and samples it. When a frame is engaged, every raster
  route calls it in place of `project_grid`.
- **When the frame path runs.**
  - It runs only for a Custom frame, or for a projection whose outline is not rectangular
    (sinusoidal, laea, lcc).
  - Otherwise the legacy `project_grid` path runs unchanged. none, cosine and mercator outputs
    stay byte-identical.
- **Vectors and scale.**
  - `Terrain` and the client overlays place points with `model.forward` plus the frame affine.
  - The print scale comes from the frame's size in km.

## Frame data model

Stored in region settings as `settings.frame`.

| field | unit | default |
|---|---|---|
| `mode` | `cover` / `inside` / `custom` | `cover` |
| `center_lat`, `center_lon` | deg | custom only |
| `w_km`, `h_km` | km in the projected plane | custom only |
| `aspect` | `free` / `1:1` / `4:3` / `bed` | `free` |

**Resolving the frame**
- Cover and Inside are recomputed from the bbox and the projection on every request, so they follow
  projection switches.
- The resolved frame is `Frame(x0, x1, y0, y1)` in km.
- For Custom: centre = `forward(center_lat, center_lon)`, then ± w/2 and ± h/2.

**Cover**
- Forward-project the densely sampled bbox boundary and take its bounding box.
- An aspect lock grows the short side about the centre.

**Inside**
- For cylindrical projections it is the bbox itself.
- For sinusoidal, laea and lcc, rasterise the projected outline polygon. Take the largest rectangle
  with `geo2stl/projections.py::largest_valid_rect`.
- With an aspect lock, binary-search the scale about the centre until the inverse-projected edges
  fall inside the bbox.
- It is geometric, so every layer gets the same rectangle.

**Fetch bbox**
- Inverse-project the frame boundary. Take the lat/lon min/max plus a margin of 2 source pixels.
- If a pole projects inside the frame, use ±90 and full longitude.
- If the frame crosses ±180°, reject it and say why.
- For Inside, and for cylindrical projections, the fetch bbox is the region bbox.

**Output shape and pixels**
- Output shape: longest side = `dim`, aspect = w/h. The default Cover equals `projected_shape`.
- Pixel centres sit at `x0 + (j + 0.5)·dx`. This matches `GeoGrid` and `Terrain`, which removes
  the half-pixel mismatch on the frame path.

**Wire format**
- Parameters: `frame_mode`, `frame_clat`, `frame_clon`, `frame_w_km`, `frame_h_km`,
  `frame_aspect`.
- New `app/server/core/frame_params.py::parse_frame` parses them.
- `resolve_frame(bbox, projection, md, raw)` returns `None` when the legacy path should run.

## Phases

Sonnet agents execute the phases, at most two at once, on disjoint files. Each phase follows the
documentation checklist in `map2stl/CLAUDE.md`.

### P0. Baselines
Add `tests/test_frame_baseline.py`, with golden sha256 values taken from the current code:
- the none-projection small-city STL;
- the DEM payloads for none, cosine and mercator;
- the shape of the laea DEM.

### P1. Python core
- **Files:** new `geo2stl/frame.py`, model hooks in `geo2stl/projections.py`, new
  `app/server/core/frame_params.py`, `tests/test_frame.py`, `tests/fixtures/frame_cases.json`.
- **Acceptance:**
  - The forward/inverse round trip is under 1e-9 for every projection.
  - Cover equals today's extents.
  - The bbox boundary lies inside Cover.
  - Every Inside corner lies inside the bbox.
  - `project_to_frame` leaves no NaN for Europe under laea, lcc and sinusoidal.

### P1b. Client math (in parallel with P1, once the fixture exists)
- **Files:** new `modules/dem/frame.js`, `tests/js/frame.test.js`.
- **Acceptance:** results match `frame_cases.json` to 1e-9.

### P2. Server raster routes
- **Files:**
  - `app/server/routers/terrain.py`: dem, water-mask, esa-land-cover, satellite, hydrology, borders,
    trails and river-lines;
  - `app/server/core/dem_cache.py`: the key uses the fetch bbox and the frame;
  - `app/server/core/dem_store.py`: its metadata adds the frame, the fetch bbox and the projection;
  - `geo2stl/dem.py`: the fetch takes the fetch bbox.
- **Acceptance:**
  - The P0 goldens are unchanged.
  - The Europe laea DEM has no NaN.
  - The layer shapes align.

### P3. Other raster routes (in parallel with P2)
- **Files:** `app/server/routers/composite.py`, `app/server/routers/cities.py`
  (`/api/cities/raster`), `app/server/routers/height.py`.
- **Acceptance:** the composite shape equals the DEM shape, and the goldens are unchanged.

### P4. Export and city model (after P2 and P3)
- **Files:**
  - `app/server/core/export_params.py`: the frame comes from the `dem_store` metadata or the
    request;
  - `app/server/core/export.py::terrain_stage`;
  - `city2stl/city_model.py`: `Terrain(frame=...)` places points through the model and the affine,
    and `choose_scale` uses the frame's km;
  - `app/server/core/city_model_task.py`: the OSM fetch uses the fetch bbox.
- **Acceptance:**
  - A Europe laea Cover export has no flat slab and keeps the full bbox.
  - Under miller, a vector river lands within 0.5 px of the raster carve.

### P5. Client wiring, no new UI (in parallel with P4)
- **Files:**
  - `modules/core/ui-helpers.js::getProjectionParams`;
  - `modules/ui/presets.js`: `collectAllSettings` must always write `frame`, because the blob is
    replaced wholesale;
  - `app/server/routers/settings.py::_default_region_settings` (`settings-compat.js` was removed 2026-10-28);
  - `modules/dem/graticule.js`;
  - `modules/layers/city-overlay.js`: `_buildGeoToPx` is replaced by `projectedFrac`;
  - `modules/export/print-scale.js::modelScale`: uses the frame's km;
  - `modules/dem/projection-switch.js`, `modules/export/export-handlers.js`.
- **Acceptance:**
  - The overlay matches the server's fractions for every projection.
  - The size label equals the frame's km at the chosen scale.

### P6. PNG mockups
User approval comes before P7.
1. The Canvas fetch group, collapsed: the frame mode segment and a size summary.
2. The frame tools, expanded:
   - W and H in km, plus the centre lat/lon;
   - the aspect lock;
   - the Cover / Inside / Reset buttons.
3. The Edit map with the Cover frame, the dashed bbox outline and the handles (laea Europe).
4. Inside mode, with the discarded wedges shaded.
5. Mid-drag: a live km readout and a ghost of the fetched area.
6. The error state: antimeridian, pole, or a fetch that is too large.
7. A small city under the none projection, with the frame minimised.

### P7. UI (after approval; two agents)
- **P7a:** `layerGroups.ts` `GROUPS.canvas.fetch` rows, new
  `components/dem/settings/FrameTools.vue`, `tests/js/editPanelGroups.test.js`.
- **P7b:** new `modules/dem/frame-overlay.js`, which draws the outline, 8 handles and a ghost
  outline. It hooks into `modules/layers/stacked-layers.js`.
- **Acceptance:**
  - A drag refetches once, on release.
  - The aspect lock holds while dragging.
  - Reset returns to Cover.
  - The frame survives a reload and a region switch.

### P8. Docs
- Update `docs/reference/projections.md`, `docs/reference/api.md` and `docs/INDEX.md`.
- Add an entry to `docs/decisions/projections-raster.md`.
- Close the issues.md "wings" entry.

## Success criteria

- **Europe/laea Cover export:**
  - The wing NaN count before `prepare_dem` is 0.
  - Every projected bbox boundary point lies inside the print.
- **No frame stored:** none, cosine and mercator outputs are byte-identical to the P0 goldens.
- **Layer alignment:** every raster layer has the DEM's shape under any frame.
- **Vectors:** buildings, trails and vector rivers land within 0.5 px under every projection when a
  frame is engaged.
- **Size label:** the print-size label matches the exported model within 1 mm.

## Risks

- **Larger fetches.** A Cover fetch bbox for Europe under laea is much wider in longitude than the
  bbox. That means more DEM, Earth Engine and carve work, and the cell budget may lower the
  resolution.
- **Shared grid reuse.** `shared_base_grid` stops being shared across projections. Mitigation: one
  region fetch bbox (the union of every projection's Cover fetch bbox) when no Custom frame is set.
- **Scale.** laea is not conformal, so `m_per_px` is exact only at the centre.
- **Cache keys.** The water, ESA and hydrology caches have no version token. The frame path must key
  on the fetch bbox.
- **Saved settings.** The settings blob is replaced wholesale, so a save without `frame` drops it.
  Add a test for this.

## Interim

- `app/server/core/export_params.py::_crop_wings` (flag `dem.crop_wings`, off by default) crops the
  export to the largest wing-free rectangle.
- The user turned it down as the default on 2026-10-17. It kept 43% of Europe and shrank the print
  to 452 mm.
- Geometric Inside (P1) replaces it.

## Open questions

The user answered these on 2026-10-17.

- **Projection switch with a Custom frame:** the frame resets to Cover.
- **Oversized Cover fetch:** a fetch bbox over 3× the bbox area, or one that touches a pole or ±180°,
  falls back to Inside with a one-line notice.
- **"Print bed" aspect:**
  - When the print fits one bed, it locks to that bed's w:h.
  - When the print is split, it locks to the puzzle grid's cols:rows piece aspect.
- **City layers:** buildings, trails and OSM water are fetched over the whole frame, margins
  included.
- **Interim crop:** `_crop_wings` and `dem.crop_wings` are deleted once the geometric Inside mode
  passes its tests.
