/**
 * The Edit page's right-hand panel (F-EDITPANEL): which sub-page is open, which tab
 * (View / Fetch / Composite) shows, whether Canvas is collapsed, and which groups show their
 * ADVANCED rows.
 *
 * A sub-page is one of the old section components (height curve, landmarks, saved presets,
 * layer order, grid, land-use colours, mesh import, plate registration), moved into the panel
 * while it is open by a <Teleport> in CollapsibleSection.vue (`sub` prop). "Show advanced" is a
 * per-viewer preference, kept in localStorage like the old tool switches; so are the tab and
 * Canvas's collapsed state.
 */
import { defineStore } from 'pinia';

const KEY = 'map2stl_editPanel';

export type PanelTab = 'view' | 'fetch' | 'composite';
export const PANEL_TABS: PanelTab[] = ['view', 'fetch', 'composite'];

interface Saved { advanced: Record<string, boolean>; tab: PanelTab; canvasOpen: boolean }

function load(): Saved {
  const d: Saved = { advanced: {}, tab: 'view', canvasOpen: true };
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || '{}') || {};
    return {
      advanced: typeof v.advanced === 'object' && v.advanced ? v.advanced : d.advanced,
      tab: PANEL_TABS.includes(v.tab) ? v.tab : d.tab,
      canvasOpen: typeof v.canvasOpen === 'boolean' ? v.canvasOpen : d.canvasOpen,
    };
  } catch {
    return d;
  }
}
const saved = load();

export const useEditPanelStore = defineStore('editPanel', {
  state: () => ({
    /** Open sub-page id (CollapsibleSection `sub`), or null for the groups. */
    sub: null as string | null,
    subTitle: '',
    /** Group key ("water:composite") -> ADVANCED rows shown. */
    advanced: saved.advanced,
    /** The tab under the layer's name; kept when another layer is selected. */
    tab: saved.tab,
    /** The Canvas group at the top is expanded. */
    canvasOpen: saved.canvasOpen,
  }),
  actions: {
    open(sub: string, title: string) { this.sub = sub; this.subTitle = title; },
    close() { this.sub = null; },
    toggleAdvanced(key: string) {
      this.advanced = { ...this.advanced, [key]: !this.advanced[key] };
      this.save();
    },
    setTab(tab: PanelTab) { this.tab = tab; this.save(); },
    toggleCanvas() { this.canvasOpen = !this.canvasOpen; this.save(); },
    save() {
      const v: Saved = { advanced: this.advanced, tab: this.tab, canvasOpen: this.canvasOpen };
      try { localStorage.setItem(KEY, JSON.stringify(v)); } catch { /* private window */ }
    },
  },
});
