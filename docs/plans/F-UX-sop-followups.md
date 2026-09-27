# F-UX — Workflow and UI follow-ups from the city STL / puzzle SOP

Status: approved 2026-09-27 ("do all now"). Source: `docs/sop/city-stl-and-puzzle-sop.md`
§3–§5, items not covered by F-CITYMODEL. Order: consolidation (F-ARCH) first for anything
that touches library code; client-only items run alongside it.

## Batch 1 — client only (parallel with F-ARCH)

- **Edit tab**: *Load DEM* is the primary action at the top of the panel; the save icon gets
  a label; rarely used DEM sources collapse under "More sources".
- **Buildings panel**: height-source summary (OSM tag / levels / lidar / raster / default)
  with a small histogram; warning when > 20% of buildings use the default height; list of
  the tallest buildings (click to select on the map); per-building height override that
  is sent with the city build as `layer_data`.
- **Extrude tab**: remove the stale "Load a DEM" message while a mesh builds; draw the
  printer-bed outline under the preview; show the vertical exaggeration relative to true
  scale and the model's real-world scale ("1 mm = 3.5 m").
- **Printer selector drives sizing**: the selected bed sets the default puzzle piece size
  and shows "fits / needs N×M pieces".
- **Presets**: City / Mountain / Coast set DEM source, resolution, vertical mode and
  exaggeration, layer toggles and puzzle options in one click.

## Batch 2 — needs server work (after F-ARCH)

- **Landmark search** for the region box (geocoding endpoint) and a warning when a named
  POI lies within 200 m of the box edge.
- **Source resolution**: DEM response reports the native sample count; the Edit tab shows
  "~30 m → 165×165 real samples, upsampled to 600×600".
- **Default DEM source**: SRTM 30 m (or best available) instead of the ~90 m local file.
- **City fetch as a background job** with per-layer progress, mirror state and cancel.
- **Printability rules** in the city model: minimum wall width (0.8 mm) and slenderness
  cap (height ≤ 8× min width, clamp or flag), reported per build.
- **Pre-flight report + panel**: size vs bed, thinnest feature, tallest spike, piece
  count, estimated filament/print time, watertight — computed server-side, shown before
  download.
- **Decimated preview** (≤ 150k faces) using the adaptive terrain mesh.
- **Puzzle**: fast heightmap-mask cutting for terrain-only puzzles; engraved piece IDs and
  a north arrow on the underside; pieces laid out on the plate; knob shapes (rectangular,
  dovetail, rounded); draggable cut lines (non-uniform grid) in the preview.
