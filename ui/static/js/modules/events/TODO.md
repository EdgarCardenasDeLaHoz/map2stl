# events/ — Open Tasks & Improvement Plans

## Open TODOs

### [x] PERF11-wire — Call `_invalidateLutCache()` on colormap change
**File:** `event-listeners-map.js` (line 23)

Currently:
```js
document.getElementById('demColormap').onchange = () => window.recolorDEM?.();
```
Change to:
```js
document.getElementById('demColormap').onchange = () => {
    window._invalidateLutCache?.();
    window.recolorDEM?.();
};
```
Requires `window._invalidateLutCache` to be exported from `dem/dem-main.js` first (see `dem/TODO.md`).

**Impact:** Prevents edge-case stale-color rendering after colormap selection changes.

---

### [x] EV-1 — Audit `onchange` vs `addEventListener` inconsistency
**Files:** `event-listeners-map.js`, `event-listeners-ui.js`

Some handlers use `.onchange = ...` (overrides any existing handler) while others use `.addEventListener('change', ...)` (composable). Standardise to `addEventListener` throughout so handlers can be added/removed without accidentally clobbering each other.

---

### [x] EV-2 — Remove `onclick=""` HTML attributes from index.html
**Files:** `ui/templates/index.html`, `event-listeners*.js`

`index.html` has many `onclick="fn()"` attributes calling global functions. These are tightly coupled to `window.*` and untestable. Migrate all HTML `onclick` attributes to `addEventListener` calls in the appropriate `event-listeners-*.js` file.

**Priority order (low risk first):**
1. Export buttons (no state dependencies)
2. Preset load/save buttons
3. DEM load button (calls `window.loadDEM` — closure in app.js; defer until ARCH refactor)

---

## Improvement Plans

### Plan A — Event bus consolidation
`window.events` (in `core/events.js`) is the intended event bus, but many interactions still use direct `window.fn()` calls or `onchange` handlers. Migrating to event-bus patterns would:
- Make data flow traceable (subscribe once, fire from anywhere)
- Enable event logging for debugging
- Unblock proper unit testing

**Migration approach:**
1. Add `EV.COLORMAP_CHANGE`, `EV.DEM_LOADED`, `EV.REGION_SELECTED` constants
2. Fire from setter sites; listen in handler files
3. Remove paired direct calls one by one

### Plan B — Keyboard shortcut registry
`keyboard-shortcuts.js` handles all `keydown` events in one switch statement. As shortcuts grow, conflicts become hard to spot. Replace with a registry:
```js
window.registerShortcut('g', 'Toggle pixel grid', () => gridBtn?.click());
window.registerShortcut('z', 'Reset zoom', () => { stackZoom = ...; });
```
The registry can also auto-generate a "Keyboard shortcuts" help overlay.

### Plan C — Debounce audit
Several `input` event handlers trigger expensive operations (DEM recolor, city overlay redraw) synchronously. Audit all `oninput` / `addEventListener('input')` handlers and apply `debounce` or RAF gating where the target function takes > 5ms.
