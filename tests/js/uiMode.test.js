import { describe, it, expect } from 'vitest';
import { parseStored, UI_TOOLS } from '../../app/client/static/js/vue/stores/uiMode.ts';
import { choosePreset } from '../../app/client/static/js/modules/ui/workflow-presets.js';

describe('uiMode (tool switches)', () => {
    it('starts with every tool off when nothing (or garbage) is stored', () => {
        expect(parseStored(null)).toEqual({ tools: {} });
        expect(parseStored('{bad json')).toEqual({ tools: {} });
    });

    it('keeps the tools of a store written while the mode existed', () => {
        expect(parseStored('{"mode":"custom","tools":{"puzzle":true}}')).toEqual({ tools: { puzzle: true } });
    });

    it('turns every tool on for an old "everything" store', () => {
        const { tools } = parseStored('{"mode":"everything","tools":{}}');
        expect(UI_TOOLS.every(t => tools[t.id])).toBe(true);
    });

    it('has unique tool ids, each on a page with a one-line hint', () => {
        const ids = UI_TOOLS.map(t => t.id);
        expect(new Set(ids).size).toBe(ids.length);
        for (const t of UI_TOOLS) {
            expect(t.hint.length).toBeGreaterThan(0);
            expect(['edit', 'extrude']).toContain(t.page);
        }
    });
});

describe('choosePreset (✨ Make it printable)', () => {
    it('picks City for a city-sized box', () => {
        // Granada, 2 × 2 km
        expect(choosePreset({ north: 37.1873, south: 37.1693, east: -3.5867, west: -3.6093 })).toBe('city');
    });
    it('picks Mountain between 25 and 100 km across', () => {
        expect(choosePreset({ north: 39.8, south: 39.4, east: -105.8, west: -106.3 })).toBe('mountain');
    });
    it('picks Region beyond 100 km', () => {
        // Amazon
        expect(choosePreset({ north: 15, south: -19, east: -45, west: -85 })).toBe('region');
    });
});
