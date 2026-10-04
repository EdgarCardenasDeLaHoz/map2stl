/**
 * editPanelGroups.test.js — the Edit panel's rows (F-EDITPANEL,
 * app/client/static/js/vue/components/dem/settings/layerGroups.ts): one control per setting.
 */
import { describe, it, expect } from 'vitest';
import { CANVAS, GROUPS, RES_IDS, SUB_TITLES } from '../../app/client/static/js/vue/components/dem/settings/layerGroups.ts';

const allRows = () => [
    ...Object.values(GROUPS).flatMap((g) => [...g.fetch, ...g.view, ...g.composite]),
    ...CANVAS,
];

describe('Edit panel groups', () => {
    it('has Fetch, View and Composite for every layer', () => {
        for (const id of ['terrain', 'water', 'city', 'satellite', 'trails', 'landcover', 'mesh']) {
            expect(GROUPS[id], id).toBeTruthy();
            for (const k of ['fetch', 'view', 'composite']) expect(Array.isArray(GROUPS[id][k]), `${id}.${k}`).toBe(true);
        }
    });

    it('writes each control from one row only (no duplicates)', () => {
        const ids = allRows().filter((r) => r.id).map((r) => r.id);
        const dup = ids.filter((id, i) => ids.indexOf(id) !== i);
        expect(dup).toEqual([]);
    });

    it('gives every row a name, an icon and what it needs for its kind', () => {
        for (const r of allRows()) {
            expect(r.label && r.icon && r.color, JSON.stringify(r)).toBeTruthy();
            if (['switch', 'slider', 'number', 'select', 'color', 'res'].includes(r.kind) && !r.get) {
                expect(r.id, r.label).toBeTruthy();
            }
            if (r.kind === 'seg') expect(r.options?.length, r.label).toBeGreaterThanOrEqual(2);
            if (r.kind === 'seg') expect(r.options.length, r.label).toBeLessThanOrEqual(4);
            if (r.kind === 'sub') expect(SUB_TITLES[r.sub], r.label).toBeTruthy();
            if (r.kind === 'button') expect(r.click, r.label).toBeTruthy();
        }
    });

    it('offers "Own resolution" for every layer resolution that follows Detail', () => {
        const res = allRows().filter((r) => r.kind === 'res').map((r) => r.id).sort();
        expect(res).toEqual([...RES_IDS].sort());
    });
});
