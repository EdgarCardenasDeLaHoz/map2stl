# F-DESIGN — redesign to the design guidelines

User-requested 2026-10-01 ("Adopt them", "Full review + mockups", "Quick wins now").
Guidelines: `Projects/design-guidelines.md` (shared) and [../../design-guidelines.md](../../design-guidelines.md) (map2stl appendix).

## Goal

Explore, Edit and Extrude follow the guidelines: one main job per page at the centre, Beginner
first with tools switched on in ⚙ Settings, settings in one slide-in panel grouped by task, no
Save or Apply buttons, a magic button that turns the selected region into a printable model,
and one visual style. No capability is lost.

## Approach (guidelines §6)

1. **Quick wins** (no mockup needed):
   - autosave with "✓ Saved" in the header, Save button and auto-save switch removed (done 2026-10-01, bbb4fd9);
   - nothing under 11 px (done 2026-10-01, 53b1085).
2. **Review**: screenshots of every page, problems by severity, what works, and the capability
   inventory (every control and readout per page) so the redesign can be checked against it.
   Output: [design-review-2026-10-01.md](../../history/audits/design-review-2026-10-01.md) and
   [capabilities-2026-10-01.md](../../history/audits/capabilities-2026-10-01.md) (done 2026-10-01).
3. **Mockups**: static HTML in `Code/claude/mockups/2026-10-01/` with real data (real DEM,
   map and model images), screenshotted at 1300×820 with headless Edge. One per page plus the
   Settings panel (Beginner and Everything). Iterate with the user before any code.
4. **Build** to the approved mockups, page by page, Beginner first.
5. **Compare** build screenshots with the mockups; run `ui_screens.py` and the layout audit.

## Target files

- `app/client/static/js/vue/components/layout/MainHeader.vue` (context pill, magic button, ⚙)
- `app/client/static/js/vue/components/dem/DemSettingsPanel.vue` and its sections → Settings panel
- `app/client/static/js/vue/components/views/ModelContainer.vue` (Extrude)
- `app/client/static/css/app.css` (tokens §4)
- `app/client/static/js/modules/ui/presets.js`, `workflow-presets.js` (magic button, mode)

## Success criteria

- Every item of the §7 checklist passes on each page, or the appendix records the exception.
- Every capability in the inventory is reachable (visible, or one Settings switch away).
- Default settings give a model that fits the selected bed (today: 797 × 802 mm on a 250 × 210 bed).
- pytest, vitest, eslint pass; `ui_screens.py` layout audit clean.

## Risks

- Control ids are the interface the modules read: moving controls must keep ids (grep first).
- Composite "Apply to DEM" is a real step today (it rewrites the DEM); making it live needs the
  composite to be fast enough or cached, or it stays as the one explicit step.
- Large change surface: build page by page, commit each.

## Decisions

- Accent stays blue (appendix). Settings group names Data / Look / Model are proposals for the
  mockup round.
