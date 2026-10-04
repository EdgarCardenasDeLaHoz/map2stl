/**
 * Optional settings sections ("tools") per page (F-DESIGN).
 *
 * Each page's right-hand panel shows its essentials, then a "More settings" list of switches
 * (ToolSwitches.vue); a switched-on tool shows its full section in the same panel. A page asks
 * `ui.shows('<tool>')`. The Beginner / Custom / Everything mode was removed on 2026-10-02
 * (user: "get rid of Mode … that feature should be rethought"); see the roadmap.
 *
 * The switches are a per-viewer preference (like a remembered tab), so they live in
 * localStorage, not in the region settings on the server.
 */
import { defineStore } from 'pinia';

export interface UiTool {
    id: string;
    label: string;
    icon: string;
    /** One plain line: what switching it on adds. */
    hint: string;
    page: 'edit' | 'extrude';
}

/** Tools per page, in the order Settings lists them. */
export const UI_TOOLS: UiTool[] = [
    // The Edit page's tools (Data sources, Display, Composite & imports, JSON) became the
    // per-layer Fetch / View / Composite groups (F-EDITPANEL, 2026-10-04).
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

interface Stored { tools: Record<string, boolean> }

/**
 * Parse the stored switches; anything unreadable means all off. Stores written while the
 * mode existed ({mode, tools}) keep their tools; "everything" turned every tool on.
 */
export function parseStored(raw: string | null): Stored {
    try {
        const v = JSON.parse(raw || '');
        const tools = v?.tools && typeof v.tools === 'object' ? { ...v.tools } : {};
        if (v?.mode === 'everything') for (const t of UI_TOOLS) tools[t.id] = true;
        return { tools };
    } catch {
        return { tools: {} };
    }
}

function _load(): Stored {
    try { return parseStored(localStorage.getItem(KEY)); } catch { return parseStored(null); }
}

export const useUiModeStore = defineStore('uiMode', {
    state: () => _load(),
    getters: {
        shows: (s) => (id: string) => !!s.tools[id],
    },
    actions: {
        setTool(id: string, on: boolean) {
            this.tools = { ...this.tools, [id]: on };
            this._save();
        },
        /** Every tool of a page (or of all pages) on or off: screenshot scripts, Reset. */
        setAll(on: boolean, page?: UiTool['page']) {
            const next = { ...this.tools };
            for (const t of UI_TOOLS) if (!page || t.page === page) next[t.id] = on;
            this.tools = next;
            this._save();
        },
        _save() {
            try { localStorage.setItem(KEY, JSON.stringify({ tools: this.tools })); } catch { /* private window */ }
        },
    },
});
