# ui/ — Open Tasks & Improvement Plans

> Source: `docs/ux-audit.md` findings 4–12 + original performance items

## Open TODOs — UX (from Chrome audit)

### [x] UX-4 — BBox editor: expand the cramped strip
**File:** `index.html` lines 510–540, `app.css`
**Source:** Chrome audit finding 4

Replaced single horizontal row with a 2×2 coord grid (`.bbox-coords-grid`) + action row (`.bbox-action-row`). Input font increased to 12px, labels linked with `for=`. Buttons have text labels. Colorbar moved to `.bbox-colorbar` class (removed inline style). All element IDs unchanged — no JS edits needed.

---

### [ ] UX-5 — Settings panel collapsed tab: make it visible
**File:** `index.html` line ~1136, `app.css`
**Source:** Chrome audit finding 5

When collapsed, the `⚙ Settings` tab at the bottom right is too subtle — users can't find it again after collapsing.

**Fix:** Change to a vertical tab along the right edge of the DEM area, always visible. Or use a pill-style floating button with higher contrast and a larger click target.

---

### [ ] UX-6 — Load DEM: promote as primary CTA on Edit tab
**File:** `index.html`, `app.css`
**Source:** Chrome audit finding 6

The empty state says "Select a region and click **Load DEM** to begin" but the Load DEM button is buried in the right-panel strip. New users don't find it.

**Fix:** When no DEM is loaded, show a large centred "Load DEM" button in the canvas area (similar to the existing empty-state message). Dismiss it once data loads.

---

### [ ] UX-7 — Fix cross-section collapsible icon
**File:** `index.html` line ~1318
**Source:** Chrome audit finding 7

Cross-section collapsible uses `▶` while all others use `▼`. One-character fix.

**Fix:** Change `▶` to `▼` at `index.html:1318`.

---

### [x] UX-8 — Clarify `modelDepthScale` vs `modelExaggeration`
**File:** `index.html` lines ~1193–1195
**Source:** Chrome audit finding 8

Two number inputs for vertical scale look identical. `modelDepthScale` affects the exported file; `modelExaggeration` only affects the live 3D preview.

**Fix:** Add a `(preview only)` badge or different background colour to the exaggeration input. Or add a visible section divider between "Export settings" and "Preview settings".

---

### [x] UX-9 — Remove hidden parameter inputs from DOM
**File:** `index.html` lines ~904–911, `app.js`, `export-handlers.js`
**Source:** Chrome audit finding 9

`<input type="hidden" id="paramDepthScale">` etc. in the Edit tab duplicate visible inputs in the Extrude tab. This fragile sync pattern causes bugs when the two get out of sync.

**Fix:** Remove the hidden inputs. Store the values directly in `appState` (or read live from the Extrude tab inputs at export time). Update all `getElementById('paramDepthScale')` references.

---

### [x] UX-10 — Remove dead DOM nodes (partial: removed 4 hidden status dots; mergePanel retained — still wired via dem-merge.js)
**File:** `index.html`
**Source:** Chrome audit finding 10

Dead DOM still present:
- `<div id="mergePanel">` (~lines 1054–1132): "Merge tab removed" comment but still in DOM
- `<div id="regionsContainer">` (~lines 333–360): "legacy, hidden"
- 4 hidden status dots (`status-dem`, `status-water`, `status-satellite`, `status-combined`)

**Before deleting:** `grep -r "mergePanel\|regionsContainer\|status-dem\|status-water\|status-satellite\|status-combined" ui/static/js/`

---

### [ ] UX-11 — Unify CSS variable declarations
**Files:** `app.css`, `main.css`, `index.html`
**Source:** Chrome audit finding 11

`main.css` defines `--bg-primary` etc.; `app.css` defines `--bg-dark` etc. — different names for the same colours. HTML only loads `app.css`. `main.css` is dead or redundant.

**Fix:** Audit which file is actually loaded. Delete `main.css` or merge its variables into `app.css` under consistent names. Update all references.

---

### [~] UX-12 — Replace inline styles with CSS classes (in progress)
**File:** `index.html`, `app.css`
**Source:** Chrome audit finding 12

Added utility classes to `app.css`: `.row`, `.row-gap4`, `.row-gap6`, `.row-gap8`, `.row-wrap`, `.col`, `.check-label`, `.dem-strip-divider`.
Replaced `display:flex;align-items:center;gap:*` inline styles throughout:
- Sidebar header buttons, sidebar draw/save row
- BBox editor (fully converted: coords grid + action row)
- Settings panel: layer mode controls, checkbox labels, projection section, Land Cover/Cities sections
- Merge panel: layer stack header, layer list container, action buttons
- Export panel: city export row, building scale row
- Cache management: terrain cache row
- JSON settings editor: apply/cancel row

Still using inline styles: `demEmptyState`, `sidebarListView` (hidden + flex conflict), cross-section, compare view, per-control sizing (`width`, `padding`, `flex:1`). Incremental — continue per section.

---

## Open TODOs — Performance

### [x] PERF-B — RAF gate for curve drag recolor
**File:** `curve-editor.js`

Verified: `applyCurveTodemSilent()` is NOT in the mousemove path. The drag handler only calls `drawCurve()` (canvas-only); `applyCurveTodemSilent()` fires on `mouseup`/`mouseleave`. No change needed.

---

### [x] UX — Keyboard shortcut for pixel/geo grid toggle
**File:** `keyboard-shortcuts.js`

`setGridPixelMode` is exposed on `window` but has no keyboard binding. Add `'g'` key toggle.

---

### [ ] UX — Undo/redo for preset load
**File:** `presets.js`

Loading a preset overwrites all slider values with no revert. Snapshot current values before loading; expose `window.revertPreset()`.

---

## Improvement Plans

### Plan A — Curve editor as a proper class
Wrap `curve-editor.js` state in a `CurveEditor` class with methods instead of module-scope globals. Makes unit testing (ARCH5) straightforward and enables multiple editors (future per-channel RGB curves).

### Plan B — Settings panel state persistence
Persist collapsible section open/closed states to `localStorage` so the layout survives page reload.

### Plan C — Presets versioning
Add a `version` field to presets with a migration function that fills in defaults for missing keys when loading older presets.
