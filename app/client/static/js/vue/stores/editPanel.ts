/**
 * The Edit page's right-hand panel (F-EDITPANEL): which sub-page is open and which groups
 * show their ADVANCED rows.
 *
 * A sub-page is one of the old section components (height curve, landmarks, saved presets,
 * layer order, grid, land-use colours, mesh import, plate registration), moved into the panel
 * while it is open by a <Teleport> in CollapsibleSection.vue (`sub` prop). "Show advanced" is a
 * per-viewer preference, kept in localStorage like the old tool switches.
 */
import { defineStore } from 'pinia';

const KEY = 'map2stl_editPanel';

function load(): Record<string, boolean> {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || '{}');
    return v && typeof v.advanced === 'object' ? v.advanced : {};
  } catch {
    return {};
  }
}

export const useEditPanelStore = defineStore('editPanel', {
  state: () => ({
    /** Open sub-page id (CollapsibleSection `sub`), or null for the groups. */
    sub: null as string | null,
    subTitle: '',
    /** Group key ("water:composite") -> ADVANCED rows shown. */
    advanced: load(),
  }),
  actions: {
    open(sub: string, title: string) { this.sub = sub; this.subTitle = title; },
    close() { this.sub = null; },
    toggleAdvanced(key: string) {
      this.advanced = { ...this.advanced, [key]: !this.advanced[key] };
      try { localStorage.setItem(KEY, JSON.stringify({ advanced: this.advanced })); } catch { /* private window */ }
    },
  },
});
