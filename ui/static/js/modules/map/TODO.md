# map/ — Open Tasks & Improvement Plans

## New Features

### [ ] UX-1 — Consolidate region creation to one entry point
**Files:** `regions/regions.js`, `index.html`, `events/event-listeners.js`

Currently 4+ ways to create a region. Keep only `floatingDrawBtn` as the primary CTA. Remove the `+ New` button from the regions panel; add a "Draw a region on the map to begin" hint to the panel empty state.

---

### [ ] UX-2 — Add text labels to floating map buttons
**File:** `index.html`, `app.css`

The 6 floating buttons have no text labels, only hover tooltips. Add visible `<span>` labels below each icon (e.g. `🏔️<br><small>Terrain</small>`).

---

### [ ] UX-3 — Clarify sidebar state toggle
**File:** `index.html`, `view-management.js`

The toggle cycles Expanded → Hidden → Normal but labels only show "Hide"/"Show". Use three explicit labels: "Expand", "Collapse", "Hide".

---

## Code Cleanup

### [ ] MAP-2 — bbox-panel drag handle accessibility
**File:** `bbox-panel.js`

Bbox resize handles are mouse-only. Add `tabindex` and `keydown` handlers (arrow keys nudge edges by 0.1°).

---

## Improvement Plans

### Plan A — Leaflet tile caching
Service worker or `localStorage` tile cache with configurable max-age. Useful for satellite basemaps.

### Plan B — Region thumbnails from map view
Capture Leaflet viewport as canvas snapshot when a region is saved. Instant thumbnails without a backend round-trip.

### Plan C — Multi-region comparison
Extend compare view to support elevation delta overlay between any two saved regions.
