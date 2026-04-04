# events/ — Open Tasks & Improvement Plans

## Improvement Plans

### Plan A — Event bus consolidation
`window.events` is the intended event bus but many interactions still use direct `window.fn()` calls. Migrating would make data flow traceable and enable event logging.

**Approach:**
1. Add `EV.COLORMAP_CHANGE`, `EV.DEM_LOADED`, `EV.REGION_SELECTED` constants
2. Fire from setter sites; listen in handler files
3. Remove paired direct calls one by one

### Plan B — Keyboard shortcut registry
Replace the `keydown` switch statement in `keyboard-shortcuts.js` with a registry that can auto-generate a help overlay:
```js
window.registerShortcut('g', 'Toggle pixel grid', () => gridBtn?.click());
```

### Plan C — Debounce audit
Audit all `input` event handlers and apply `debounce` or RAF gating where the target function takes > 5ms.
