import { describe, it, expect } from 'vitest';
import { parseStored, UI_TOOLS } from '../../app/client/static/js/vue/stores/uiMode.ts';

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
