# Design review against the guidelines (2026-10-01)

Step 1 of [F-DESIGN](../../plans/active/F-DESIGN-guidelines-redesign.md). Judged against the
shared `design-guidelines.md` §1 principles and §7 checklist, with the map2stl appendix
([design-guidelines.md](../../design-guidelines.md)).

- **Evidence:**
  - screenshots in `Code/claude/audit-shots/2026-10-01/`, at 1440×900 and 1024×768;
  - guide images in `docs/guides/img/`;
  - the usage log (`output/usage/`, sessions of 2026-09-30).
- **Capability inventory:** 462 controls, in [capabilities-2026-10-01.md](capabilities-2026-10-01.md).
  It is the baseline: no control in it may be lost.

## Main job per page

| Page | Main job | Centre of the screen today |
|---|---|---|
| Explore | Pick or draw the area to print | The map. ✓ |
| Edit | Choose what goes into the model and see it as a height map | The DEM canvas. A debug pixel grid (red lines every 100 px, labelled) draws over it by default. |
| Extrude | Check the model fits the printer and export it | The 3D viewer. ✓ The default model does not fit the bed. |

## Severe

1. **Edit has no Beginner view (§1.4).**
   - 288 controls sit behind three tabs and about 20 collapsible sections.
   - Projection, curve editor, land-cover classes, composite weights, mesh import and plate registration all sit beside Load DEM.
   - Workflow: a first-time user cannot tell which 5 controls matter.
2. **The defaults don't make a printable model (§1.10, §1.12).**
   - Granada with defaults: 797 × 802 × 88 mm at 1 mm/px, on a 250 × 210 mm bed.
   - The Extrude panel says so in orange, and the fix (mm/px, puzzle) is spread over two tabs.
   - No magic button: the whole job needs Explore → Load DEM → presets → Extrude → mm/px → Puzzle → Export.
3. **Many controls for one quantity (§1.5, §1.10).** The inventory lists 32 duplicates or name collisions. The worst:
   - water depth set in 5 places, building scale in 3, road depth in 3;
   - "Resolution" means four things (grid points, layer px, mm/px, m/px);
   - Load DEM has 3 buttons;
   - region bounds are editable in 5 places;
   - "Edit" means both the sidebar region editor and the Edit tab;
   - two puzzle systems (terrain split, city pieces).
4. **The result is covered by a debug overlay (§1.1).**
   - On Edit, the red 100-px pixel grid with px labels draws over the DEM by default.
   - It's useful for registration work, but it's the first thing a user sees on the main canvas.
5. **Apply buttons where changes should apply live (§1.5, §3).**
   - Composite `✓ Apply to DEM`, curve `✓ Apply`, elevation range `Apply`, land cover `Apply Colors`, JSON `Apply`.
   - Some are real steps (Apply to DEM rewrites the DEM; see the plan's risks). The display ones are not.

## Moderate

6. **Settings are not one panel grouped by task (§3).**
   - Edit settings sit in a right panel with tabs, and Extrude has a second panel with its own tabs.
   - Map settings are a third (popover), Keys and Diagnostics a fourth and fifth (modals).
   - There is no search and no per-group Reset.
   - ⓘ help is mixed in as paragraphs, for example the pipeline note and the sampling warnings.
7. **The header has no context pill (§2).**
   - Which region is open is shown only by the highlighted row in the sidebar list.
   - That list stays on screen on Edit and Extrude, where it takes 250 px from the result.
8. **Toast floods (§1.13, §5).** One Load DEM click produces 6–8 toasts within a second (usage log, Amazon, 2026-09-30): Loading DEM, water mask, ESA, satellite, skipping cities, then each result. Load state belongs in status dots on the layers, not toasts.
9. **Too many sections open at once in Fetch.**
   - Fetch opens on Load DEM, the presets, an edge warning, then four collapsed groups.
   - Inside Fetch Layers each source has its own Load button, though Load DEM already loads them all.
10. **Visual style (§4).** Grey borders on every card, 4–8 px radii, three blues (`#4a9eff`, `#0a84ff`-like tabs, link blue), orange and green status text, emoji icons mixed with glyphs. Panels use borders instead of background steps.
11. **Extrude Export tab is long (§1.7).** Pre-flight, downloads, engraving, puzzle, cross-section, city model (30-control layer table), score panel and printer all sit in one scroll. The printer (bed) choice, which decides the model size, is at the bottom of Export, not in Build.

## Minor

12. Header buttons Guides / Results / Diagnostics / Keys / Docs are five equal-weight buttons. They belong in ⚙ Settings or a help menu, leaving the header for the title, context, magic button, Saved and ⚙.
13. Step badges `1 2 3` plus ✓ plus arrows repeat what the tabs already say.
14. The Buildings collapsed tab (vertical, between canvas and settings) splits the Edit page into four columns.
15. Plurals and units: "1 landmarks" is possible; "Min ord", "Width ×", "Bldg scale (mm/m)", "Road dep (m)" are internal names.
16. 30 orphaned ids and two unreachable legacy views (regions table, region compare) are dead code.

## What works (keep)

- Real renderers everywhere: Leaflet map, the DEM stack canvas, the Three.js model with the bed outline and mm labels, colour by satellite.
- Explore follows the Manager + map pattern: list with search, one primary action (`Load DEM ›`), boxes synced with the list (20 largest in view).
- Printed size versus bed is computed live (`#modelPrintSize`, bed outline in 3D).
- Autosave with "✓ Saved" in the header (2026-10-01); one source of truth for region settings on the server.
- Workflow presets (City / Mountain / Region / Coast) are already "few, good choices" and the seed of Beginner mode and the magic button.
- Contrast ≥ 4.5:1 and nothing under 11 px (2026-10-01); toasts no longer overlap panels.

## §7 checklist

| Item | Explore | Edit | Extrude |
|---|---|---|---|
| Main job in one sentence, at the centre | ✓ | ~ (debug grid on top) | ✓ |
| Layout of its type | ✓ Manager | ~ 4 columns, no tool strip | ~ no tool strip, settings in tabs |
| Result drawn by the real renderer | ✓ | ✓ | ✓ |
| First-time user can do the main job unaided | ✓ | ✗ | ✗ (doesn't fit the bed) |
| Every capability kept | baseline | baseline | baseline |
| One button per action, no Save, Saved shown, one name per thing | ~ (Save region ×2) | ✗ (duplicates) | ~ (two puzzles) |
| Panels agree on what is open | ✓ | ✓ | ✓ |
| Properties show only the selection | n/a | ✗ | ✗ |
| Advanced hidden in Beginner | ✗ | ✗ | ✗ |
| Settings by task, search, Reset, ≤ 2 levels | ✗ | ✗ | ✗ |
| Live state visible | ~ | ~ (toasts, not dots) | ✓ |
| Plain words, units, plurals | ~ | ✗ | ~ |
| ≥ 11 px, ≥ 4.5:1 | ✓ | ✓ | ✓ |
| Layout stays put | ✓ | ~ (sections reflow when opened) | ✓ |
| Matches approved mockup | — | — | — |

## Next

Mockups (step 2): one per page plus the ⚙ Settings panel (Beginner and Everything), using the
real Granada map, DEM and model. Fixes for severe 1–5 are the mockups' main content.
