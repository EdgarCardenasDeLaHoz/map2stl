/**
 * Beginner / Custom / Everything (design guidelines §1.4, F-DESIGN).
 *
 * Beginner shows each page's essentials. Each tool adds its own card or row when
 * switched on in ⚙ Settings; switching any tool on sets Custom. Everything shows
 * every tool. A page asks `ui.shows('<tool>')`.
 *
 * The mode is a per-viewer preference (like a remembered tab), so it lives in
 * localStorage, not in the region settings on the server.
 */
import { defineStore } from 'pinia';

export type UiMode = 'beginner' | 'custom' | 'everything';

export interface UiTool {
    id: string;
    label: string;
    icon: string;
    /** One plain line: what switching it on adds. */
    hint: string;
    page: 'edit' | 'extrude';
}

/** Tools per page, in the order Settings lists them. Edit's tools arrive with its rebuild. */
export const UI_TOOLS: UiTool[] = [
    { id: 'vertical', icon: '↕', label: 'Vertical & surface', page: 'extrude',
      hint: 'Fit height, vertical mode, smoothing, sea-level cap' },
    { id: 'viewer', icon: '👁', label: '3D view options', page: 'extrude',
      hint: 'Colours, wireframe, auto-rotate' },
    { id: 'cityLayers', icon: '🏙', label: 'City model layers', page: 'extrude',
      hint: 'Per-layer extrude, raise or engrave, and by how much' },
    { id: 'puzzle', icon: '🧩', label: 'Puzzle details', page: 'extrude',
      hint: 'Grid, knob shape and size, clearance, plates, dragged cuts' },
    { id: 'engraving', icon: '🖋', label: 'Engraving & contours', page: 'extrude',
      hint: 'Region name in the base, contour lines' },
    { id: 'crossSection', icon: '✂️', label: 'Cross-section', page: 'extrude',
      hint: 'A slab cut along a latitude or longitude' },
    { id: 'modelScore', icon: '📏', label: 'Model score', page: 'extrude',
      hint: 'Compare building heights with a reference plate or nDSM' },
];

const KEY = 'map2stl_uiMode';

interface Stored { mode: UiMode; tools: Record<string, boolean> }

/** Parse the stored preference; anything unreadable means a fresh Beginner. */
export function parseStored(raw: string | null): Stored {
    try {
        const v = JSON.parse(raw || '');
        const mode: UiMode = ['beginner', 'custom', 'everything'].includes(v?.mode) ? v.mode : 'beginner';
        const tools = v?.tools && typeof v.tools === 'object' ? v.tools : {};
        return { mode, tools };
    } catch {
        return { mode: 'beginner', tools: {} };
    }
}

/** Whether a tool's controls show in this mode. */
export function toolShown(mode: UiMode, tools: Record<string, boolean>, id: string): boolean {
    return mode === 'everything' || (mode === 'custom' && !!tools[id]);
}

function _load(): Stored {
    try { return parseStored(localStorage.getItem(KEY)); } catch { return parseStored(null); }
}

export const useUiModeStore = defineStore('uiMode', {
    state: () => _load(),
    getters: {
        shows: (s) => (id: string) => toolShown(s.mode, s.tools, id),
    },
    actions: {
        setMode(mode: UiMode) {
            this.mode = mode;
            this._save();
        },
        setTool(id: string, on: boolean) {
            this.tools = { ...this.tools, [id]: on };
            // Guidelines §1.4: turning any tool on sets Custom.
            if (on && this.mode === 'beginner') this.mode = 'custom';
            this._save();
        },
        _save() {
            try { localStorage.setItem(KEY, JSON.stringify({ mode: this.mode, tools: this.tools })); } catch { /* private window */ }
        },
    },
});
