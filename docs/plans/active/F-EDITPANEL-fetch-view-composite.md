# F-EDITPANEL — Edit right panel: one control per setting, per layer, under Fetch / View / Composite

**Requested:** user, 2026-10-04: "removing duplicates and organize everything under Fetch, View,
Composite. Apply the new style to the new panel and make it only appear when the layer is
selected on the left." Resolution: "a number entry and a check box to override the resolution
of the DEM. Use this model for every layer." Grid and other general settings: "their own Canvas
group". ADVANCED rows hidden behind "Show advanced" per group.

Review: `claude/audit-shots/2026-10-04/edit-panel/review.md` (237 controls; the same setting
under two or three names; sections by subsystem, four levels deep). Approved mockup:
`claude/mockups/2026-10-04-edit-panel/panel.png`.

## Goal

The panel shows the selected layer only: its title, then FETCH, VIEW and COMPOSITE groups
(one row per setting, iOS-style, per-group Reset, "Show advanced"), then a CANVAS group that
is the same under every layer (grid, map under the terrain, layer order, compare view).
Every capability of the old panel stays, visible or one sub-page (›) away.

## Approach

1. `LayerSettings.vue` (new) replaces `LayerProperties.vue`, the "More settings" switches
   and the three tool sections. Rows are typed (switch, slider, segmented, number, select,
   link, button) and write the existing controls by id (`dom-fields.ts`), so autosave,
   presets and the modules' listeners are unchanged.
2. The old section components stay mounted, hidden, as the holders of those ids. Blocks
   too rich for a row (height curve and histogram, landmarks, saved presets, layer order,
   grid, land-use colours, mesh import, plate registration, building table) open as a
   sub-page: a `<Teleport>` moves the old block into the panel while it is open (canvas
   content and listeners survive the move).
3. Duplicates merge into one row: rivers (min order, width), river and lake depth, lakes,
   style presets (Style = workflow preset bar), Reload terrain = Load DEM, elevation source,
   detail, colours, building table, per-layer opacity ("On the map" = layer rack opacity),
   trails ski/hiking. Building height: the print value drives the 2D preview scale.
4. Resolution per layer (water, land cover, satellite, city raster, trails): follows the
   terrain's Detail unless "Own resolution" is ticked; then a number. The old dropdowns
   become number inputs (same ids; every reader uses `.value`). Override = value differs
   from Detail, so no new saved field.
5. Compare the build with the mockup at the same size; keep `edit_panel_shots.py` for it.

## Target files

- `app/client/static/js/vue/components/dem/LayerSettings.vue` (new), `DemSettingsPanel.vue`,
  `FetchLayersSection.vue` (number inputs), the section components (Teleport hooks)
- `app/client/static/js/vue/stores/uiMode.ts` (Edit tools removed), `editLayers.ts`
- tests: `tests/js/` for the row model and the resolution-follow rule

## Success criteria

- One visible control per setting; no setting reachable under two names.
- With a layer selected, the panel shows only that layer's groups plus Canvas.
- Every control in the review's capability list is reachable (inventory script).
- Screenshot matches the mockup; one click-through in the app (load, change a setting
  per group, reload, settings saved).

## Risks

- Modules bind listeners by id at startup: every id must stay in the DOM exactly once.
- Teleported blocks with canvases (curve editor) must not be re-created.
- Three building-scale controls have different units (print ×, preview ×, mm/m): only
  the two × controls are merged; mm/m stays as an advanced row.

## Progress

2026-10-04, built to the mockup:
- `LayerSettings.vue` + `settings/layerGroups.ts` (rows per layer) + `settings/SetRow.vue`;
  `stores/editPanel.ts` (sub-page, Show advanced); `CollapsibleSection.vue` `sub` prop
  (Teleport) on Landmarks, Saved presets, Layer order, Grid & map, Height curve +
  Histogram, Land-use colours, Mesh import, Plate registration. `LayerProperties.vue` and
  Edit's tool switches removed; the old sections live in a hidden `#legacyControls`.
- Resolution dropdowns are number inputs; `regions.js` sets them to Detail for a region
  without saved settings; a change of Detail moves the following ones.
- Checks: every row id exists once in the page; `tests/js/editPanelGroups.test.js` (one row per
  control); `claude/scripts/edit_panel_check.py` click-through on a copy of data.db, 19/19 pass
  (Detail, follow, rivers seg + fetch, Own resolution, View seg, Composite switch, Reset,
  Canvas grid, Height curve sub-page, building height sync, saved after reload).
- Build vs mockup: `claude/audit-shots/2026-10-04/edit-panel/build_side_by_side.png`. The
  panel is ~283 px (the mockup 330): dropdowns sit under their name, not beside it.

## Decisions
