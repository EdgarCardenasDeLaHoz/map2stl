import { describe, it, expect } from 'vitest';
import { parseStored, toolShown, UI_TOOLS } from '../../app/client/static/js/vue/stores/uiMode.ts';
import { choosePreset } from '../../app/client/static/js/modules/ui/workflow-presets.js';

describe('uiMode', () => {
    it('starts in Beginner when nothing (or garbage) is stored', () => {
        expect(parseStored(null)).toEqual({ mode: 'beginner', tools: {} });
        expect(parseStored('{bad json')).toEqual({ mode: 'beginner', tools: {} });
        expect(parseStored('{"mode":"wizard"}').mode).toBe('beginner');
    });

    it('reads a stored mode and tools', () => {
        expect(parseStored('{"mode":"custom","tools":{"puzzle":true}}'))
            .toEqual({ mode: 'custom', tools: { puzzle: true } });
    });

    it('shows a tool only when switched on in Custom, always in Everything', () => {
        const tools = { puzzle: true };
        expect(toolShown('beginner', tools, 'puzzle')).toBe(false);
        expect(toolShown('custom', tools, 'puzzle')).toBe(true);
        expect(toolShown('custom', tools, 'crossSection')).toBe(false);
        expect(toolShown('everything', {}, 'crossSection')).toBe(true);
    });

    it('has unique tool ids, each with a one-line hint', () => {
        const ids = UI_TOOLS.map(t => t.id);
        expect(new Set(ids).size).toBe(ids.length);
        for (const t of UI_TOOLS) expect(t.hint.length).toBeGreaterThan(0);
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
