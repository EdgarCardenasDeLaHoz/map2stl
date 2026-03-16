# Bounding Box Editing — Improvement Options

Audited 2026-03-15. Four options identified for improving the Edit view bbox experience.

**Option A — Inline mini-map (IMPLEMENTED)**
Embed a small Leaflet map directly below the bbox coordinate inputs. The current region's
bounding box appears as a draggable/resizable rectangle with Leaflet-Draw handles. Dragging
a handle updates the N/S/E/W inputs in real-time; releasing triggers a debounced DEM reload
(300 ms). Replaces the old "✎ Map" button that forced navigation away from the Edit view.
The mini-map is toggled open/closed by the ↕ Map button in the bbox row.

**Option B — Live-apply on blur/Enter**
Add `blur` + `Enter` listeners to all four coordinate inputs. When a valid change is detected,
pulse-highlight the Reload button and auto-reload after a short debounce (~500 ms). No
layout change needed — quickest single-change win. Can be combined with any other option.

**Option C — Single-source map interaction**
Remove the N/S/E/W text inputs entirely. Bounding box is only edited by dragging the
rectangle on the Explore map. Show coordinates as read-only text (with a pencil icon to
switch to map editing). Eliminates the three-source-of-truth problem
(`boundingBox` / `selectedRegion` / `currentDemBbox`) at the cost of precision typing.

**Option D — Compass-rose coordinate widget**
Replace the four inline inputs with a compact 2×2 grid arranged spatially (N top, S bottom,
W left, E right). Add ▲▼◀▶ nudge buttons (0.1° steps) beside each input. Show a live
green/red border indicating bbox validity as the user types. Auto-validate N > S and
−90 ≤ lat ≤ 90, −180 ≤ lon ≤ 180.

---

## Known issues in the original code (all still present except the navigation-away problem)

| # | Issue |
|---|-------|
| 1 | Inputs empty until DEM loads (selectCoordinate doesn't populate them) |
| 2 | No East > West validation (only N > S is checked) |
| 3 | No lat/lon range clamp (values like N=200 accepted) |
| 4 | `selectedRegion` mutated in-place — no undo |
| 5 | No live feedback while typing — must click Reload or press Enter |
