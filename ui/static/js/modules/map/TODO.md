# map/ — Open Tasks & Improvement Plans

> Source: `docs/ux-audit.md` findings 1, 2, 3, 15 + original MAP-1/2/3

## Open TODOs

### [ ] UX-1 — Consolidate region creation to one entry point
**File:** `regions/regions.js`, `index.html`, `events/event-listeners.js`
**Source:** Chrome audit finding 1

Currently 4+ ways to create a region: `+ New Region` floating button on map, `+ New` in the floating regions panel, `+ New Region` collapsible in sidebar, and `✏️ Draw bbox` in sidebar edit view. Users can't tell which is canonical.

**Fix:** Keep only `floatingDrawBtn` ("+ New Region") as the primary CTA. Remove the `+ New` button from the regions panel (keep panel for viewing/searching only). Add a "Draw a region on the map to begin" hint to the panel empty state.

---

### [ ] UX-2 — Add text labels to floating map buttons
**File:** `index.html` lines 249–266, `app.css`
**Source:** Chrome audit finding 2

The 6 floating buttons (`🏔️ 📐 🌍 📋 🏷️ ⚙️`) have no text labels — only tooltips on hover. First-time users have no idea what they do.

**Fix:** Add visible `<span>` labels below each icon (e.g. `🏔️<br><small>Terrain</small>`). `.map-floating-btn` CSS already supports this pattern.

---

### [ ] UX-3 — Clarify sidebar state toggle
**File:** `index.html`, `app.js`/`view-management.js`
**Source:** Chrome audit finding 3

The toggle cycles "Expanded → Hidden → Normal" but the label only shows "Hide" / "Show", which doesn't communicate the third state. `sidebarListView`, `sidebarEditView`, and `sidebarTableView` display the same data three different ways — visually inconsistent.

**Fix:** Use three explicit labels: "Expand", "Collapse", "Hide". Or reduce to two states (show/hide) if the third state adds no value.

---

### [x] UX-E — Vendor Leaflet assets locally
**File:** `index.html` lines 7–8, new `ui/static/vendor/` directory
**Source:** Chrome audit finding 15

Leaflet and Leaflet-draw are loaded from `unpkg.com` CDN. The app silently breaks offline. Since this is a local tool, assets should be self-hosted.

**Fix:** Download `leaflet.css`, `leaflet.js`, `leaflet-draw.css`, `leaflet-draw.js` to `ui/static/vendor/` and update the `<head>` links.

---

### [x] MAP-1 — Globe container init guard cleanup
**File:** `map-globe.js`

Console logs `Globe container not ready, skipping init` on every page load (expected — globe is on Explore tab). Change to a silent early-return or wire init to the Explore tab's first activation event.

---

### [ ] MAP-2 — bbox-panel drag handle accessibility
**File:** `bbox-panel.js`

The bbox resize handles are mouse-only. Add `tabindex` and `keydown` handlers (arrow keys to nudge edges by 0.1°).

---

### [x] MAP-3 — Compare view memory leak
**File:** `compare-view.js`

Verified: compare-view.js uses canvas only, no Leaflet map instances. The only Leaflet maps are the main map (map-globe.js, singleton) and bbox mini-map (bbox-panel.js, singleton). Neither are tab-toggled in a way that causes accumulation. No action needed.

---

## Improvement Plans

### Plan A — Leaflet tile caching
Leaflet tiles are re-fetched on every session. Implement a service worker or `localStorage` tile cache with a configurable max-age. Useful for satellite basemaps.

### Plan B — Region thumbnails from map view
Capture the Leaflet viewport as a canvas snapshot when a region is saved (using `leaflet-image` or similar). Gives instant thumbnails without a backend round-trip.

**Files:** `regions/region-ui.js`, new `map/map-snapshot.js`

### Plan C — Multi-region comparison
Extend the compare view to support comparing any two saved regions on demand, with a difference overlay showing elevation delta.
