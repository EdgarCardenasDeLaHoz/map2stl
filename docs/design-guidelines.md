# Design guidelines — map2stl appendix

The shared guidelines are `design-guidelines.md` in the OneDrive `Projects` folder (three levels
up from `Code/`). This appendix adds map2stl's pages, its brand and its exceptions, and it wins
where the two differ. Adopted 2026-10-01.

## Pages

The app's job: **turn a box on the map into a printable 3D model.**

| Page | Main job (one sentence) | Page type | Result in the centre |
|---|---|---|---|
| 1 Explore | Pick or draw the area to print. | Manager with a map: region list left, map centre | The map with the region boxes |
| 2 Edit | Choose what goes into the model and see it as a height map. | Designer: layers left, canvas centre, settings right | The DEM / composite drawn by the real renderer |
| 3 Extrude | Check the 3D model fits the printer and export it. | Designer: 3D viewer centre, settings right | The model in the 3D viewer, with the printer bed outline |

- The header's context pill is the selected region (name · size in km · DEM source).
- The magic action is one button that takes the selected region to a printable model. Its exact
  behaviour is settled in the mockup round (see the roadmap).

## Settings

- The Edit panel rule (decisions/frontend.md, 2026-09-30) becomes the settings groups (names
  proposed; confirmed or changed in the mockup round):
  - **Data** (was Fetch): what is fetched, and settings that affect everything.
  - **Look** (was View): display only; never changes the model.
  - **Model** (was Composite): only what changes the 3D result (carve depth, layer weights).
- Beginner shows the region, the size on the bed, and the layer switches. Projection, curve,
  composite weights, registration, mesh simplification and ML tools are ADVANCED.
- Region settings save themselves; the header shows "✓ Saved" (`modules/ui/presets.js`).

- ⚙ Settings is docked on the left and pushes the page (not a sheet over it), and lists only
  the open page's tools (user, 2026-10-02; overrides shared §3's right-hand slide-in).

## Brand and style

- Accent: blue `#0a84ff` instead of the shared pink, because the app has used blue since the
  start and the map's water is blue too. The magic gradient is `#0a84ff → #5e5ce6`.
- Because the accent is blue, links in settings use the text colour plus underline, not a
  second blue.
- Maps, heat maps and the 3D view keep their own data colour scales; the one-accent rule covers
  UI chrome only.
- Units are print millimetres for sizes and depths, metres or km on the ground; say which.

## Exceptions

- Desktop first. The map and 3D viewer need a mouse; at phone width the app shows the region
  list and the model only.
- Power tools that are not settings (registration, skyline pipeline, ML training) stay on their
  own pages (`/reports`, the tools) and are not part of the Beginner view.

## Tooling

- Review screenshots: `Code/claude/scripts/ui_screens.py` (1:1 panel crops + layout audit).
- Mockups: static HTML under `Code/claude/mockups/<date>/`, screenshotted with headless Edge at
  1300×820; PNGs are sent for approval before code.
