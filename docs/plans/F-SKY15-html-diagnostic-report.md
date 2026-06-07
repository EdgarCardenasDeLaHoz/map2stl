# F-SKY15 — HTML diagnostic report alongside the PDF

Proposal entry: `docs/proposals.md` F-SKY15

## Goal

Add a per-seed HTML report next to the existing PDF, sharing the
same upstream data (`SeedViewRegistration`, `StitchedPanoResult`,
`osm_data`) so there is no second source of truth. The PDF path
stays untouched and remains the canonical archival format. HTML
becomes the iteration surface — for both human readers and AI
contributors who can grep / diff / verify field values directly
in the DOM.

This isn't a migration; it's a parallel renderer.

## Why now

- We just landed F-SKY12 + F-SKY13 with several new optional fields
  on `SeedViewRegistration` (`depth_height_m`, `pano_osm_iou`,
  `pano_projected_coastline`). Verifying these is currently a
  visual exercise on the PDF, which is slow and error-prone.
- The F-SKY13 minimap already carries 5+ overlay layers
  (OSM coast, water polygons, 1 km circle, pano-projected dots,
  IoU annotation). Toggleable layers will make it usable.
- HTML output makes future AI-driven additions cheap — new
  diagnostic = a `<div>` + CSS rule, not matplotlib axis
  positioning.

## Approach

### Output layout

```
output/skyline/<region>/
├── index.html          ← seed list + region summary
├── seed_1.html         ← per-seed page (matches PDF page structure)
├── seed_2.html
├── ...
└── assets/
    ├── views/          ← stitched pano + per-view images (PNG)
    └── minimap/        ← prerendered matplotlib minimaps OR
                          structured JSON for client-side Leaflet
```

Two rendering paths for the minimap, chosen per environment:

1. **Static PNG** (Phase A) — call the existing
   `_draw_view_minimap` + `_draw_osm_coastline_overlay`, save
   the figure as PNG, embed via `<img>`. Zero new rendering
   code. Loses interactivity but gets the page shipped today.

2. **Client-side Leaflet** (Phase B, future) — emit the
   underlying OSM/coastline data as JSON + a Leaflet map per
   seed. Adds zoom/pan + layer toggles. Bigger change.

Phase A is what this proposal ships.

### Per-seed page structure

A single `seed_N.html` per seed, mirroring what `_render_seed_view_page`
writes to the PDF:

```
┌──────────────────────────────────────────────────────────┐
│ <h1> Seed N — <name> </h1>                               │
│ <p> 12 spin views @ FOV=75°, recovered offset=312°       │
│     pano↔OSM IoU=0.78 (42 keypoints)                     │
│     pano_recovered_peak=0.61, sigma=0.082                │
│ </p>                                                     │
├──────────────────────────────────────────────────────────┤
│ <section class="minimap">                                │
│   <img src="assets/minimap/seed_N.png">                  │
│   <p class="legend"> blue=OSM coast · orange=pano-derived│
│                      · grey dashed=1 km window </p>      │
│ </section>                                               │
├──────────────────────────────────────────────────────────┤
│ <section class="estimates">                              │
│   <table>                                                │
│     <tr><th>building</th><th>h_geom</th><th>h_depth</th> │
│         <th>disagree?</th><th>view</th></tr>             │
│     <tr><td>b0142</td><td>52 m</td><td>48 m</td>         │
│         <td>no</td><td>v3</td></tr>                      │
│     ...                                                  │
│   </table>                                               │
│ </section>                                               │
├──────────────────────────────────────────────────────────┤
│ <section class="views">                                  │
│   <details><summary>View 3 (heading 90°)</summary>       │
│     <img src="assets/views/seed_N_view_3.png">           │
│   </details>                                             │
│   <details>... per view ...</details>                    │
│ </section>                                               │
└──────────────────────────────────────────────────────────┘
```

The `<table>` row data comes directly from the
`RegisteredBuildingEstimate` list — the F-SKY12 depth columns are
visible here even though they aren't in the PDF.

### index.html

A region-level summary table:

| Seed | n_views | matched | mean MAE | pano↔OSM IoU | link |
|------|---------|---------|----------|--------------|------|
| 1 | 12 | 8 | 4.3 m | 0.81 | seed_1.html |
| 2 | 11 | 6 | 5.1 m | 0.62 | seed_2.html |

Reuses the same aggregation already computed for the PDF's
front-matter page.

## Target files

- `city2stl/skyline/html_report.py` (NEW) — pure rendering
  helpers. Three public functions:
  - `render_region_index(region_name, seed_views, ...) -> str`
  - `render_seed_page(sv, osm_data, ...) -> str`
  - `write_region_report(out_dir, region_name, seed_views, osm_data, ...) -> None`
  No new dependencies — uses stdlib `html.escape` + simple
  string templates. **Reuses** existing
  `_draw_view_minimap` for the PNG (saving the figure to a
  buffer instead of a PDF page).
- `city2stl/skyline/region_pdf.py` — at the end of the
  existing `generate_region_report` function, if
  `SKYLINE_CV_HTML_REPORT=1` (or unconditionally — TBD after
  Phase A trial), call `write_region_report` with the same
  data the PDF just consumed. ~10 lines.
- `tests/test_skyline_html_report.py` (NEW) — render with
  a tiny synthetic seed_view fixture; assert the output is
  valid HTML and contains the key field values
  (`pano_osm_iou`, `depth_height_m`, etc).

## Success criteria

Phase A is successful if:
- Running `08_region_skyline_pdf.py cartagena` produces both
  the existing PDF and a new `output/skyline/cartagena/`
  directory with `index.html` + per-seed pages.
- The HTML page for a coastal seed shows the same minimap PNG
  the PDF shows (visual parity for Phase A).
- The estimates table includes the F-SKY12 depth columns even
  though the PDF doesn't render them.
- HTML opens correctly in a current Chromium / Firefox.
- AI (this assistant) can `Read` the HTML and answer
  "what was the IoU for seed_5?" by grepping the DOM, without
  needing a screenshot.
- No change to existing PDF output (parallel renderer, not a
  replacement).

## Reuse audit (per `feedback_reuse_city2stl_libraries`)

Before writing new code, audit what already exists:

- ✅ `_draw_view_minimap` — reuse via `fig.savefig(buf,
  format='png')` to embed the same minimap visually.
- ✅ `_render_seed_view_page` building-band crop logic —
  same per-view PNG can be saved + linked.
- ✅ `SeedViewRegistration` + `RegisteredBuildingEstimate`
  field set — drive the HTML table directly, no shadow data
  model.
- ✅ `aggregate_building_heights` — already computes the
  per-building aggregate the index page needs.
- ❓ Existing templating in the project? `city2stl` doesn't
  use Jinja or similar (it's a research module, not a
  webapp). Plain string templates suffice — fewer
  dependencies and the templates are short.

## Risks & open questions

- **Static minimap PNG limits interactivity.** Acceptable for
  Phase A; Phase B adds Leaflet.
- **HTML file size.** A region with 5 seeds × 12 views × PNG
  embedded = 60 PNGs. Saving as separate files (linked from
  HTML) keeps each page <200 KB. Don't inline as data URIs.
- **Cross-platform path separators.** Use `pathlib` and
  forward-slash `href` URLs throughout — Windows users
  opening files locally via `file://` need `/` in `<a href>`.
- **Output directory collision** — `output/` is gitignored
  but if the user has the PDF open while the HTML
  regenerates, no conflict (different files).

## Phase B (future, not this round)

- Replace the static minimap PNG with a Leaflet map that
  consumes OSM coastline + projected coastline as GeoJSON,
  with toggleable layers.
- Per-segment hover tooltips on the estimates table → flash
  the matching badge on the minimap.
- Inline depth maps from F-SKY12 (the `depth_m` arrays) as
  optional per-view heatmaps when `SKYLINE_CV_F_SKY12=1`.
- Single-file mode: bundle everything into one HTML via
  data URIs, for emailing.
