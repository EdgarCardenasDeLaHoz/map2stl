# ui/ — Open Tasks & Improvement Plans

## Performance

### [ ] PERF-RAF — RAF-gate curve drag recolor
**File:** `curve-editor.js`, mousemove handler

`applyCurveTodemSilent` fires on every `mouseup`/`mouseleave` — this is correct and already optimised (not in mousemove). However, if future changes move it back into mousemove, gate it with:
```js
if (!_curveRafPending) {
    _curveRafPending = true;
    requestAnimationFrame(() => { _curveRafPending = false; applyCurveTodemSilent(); });
}
```

---

## Code Cleanup

### [~] UX-12 — Replace inline styles with CSS utility classes (in progress)
**File:** `index.html`, `app.css`

Utility classes added: `.row`, `.row-gap4/6/8`, `.row-wrap`, `.col`, `.check-label`, `.dem-strip-divider`.

Still using inline styles: `demEmptyState`, `sidebarListView`, cross-section panel, compare view, per-control sizing (`width`, `padding`, `flex:1`). Continue per-section.

---

## New Features

### [ ] FEAT — Undo/redo for preset load
**File:** `presets.js`

Loading a preset overwrites all slider values with no revert. Snapshot current values before loading; expose `window.revertPreset()`.

---

## Improvement Plans

### Plan A — Curve editor as a proper class
Wrap `curve-editor.js` state in a `CurveEditor` class. Enables unit testing (ARCH5) and future per-channel RGB curves.

### Plan B — Settings panel state persistence
Persist collapsible section open/closed states to `localStorage`.

### Plan C — Presets versioning
Add a `version` field to presets with a migration function for missing keys.
