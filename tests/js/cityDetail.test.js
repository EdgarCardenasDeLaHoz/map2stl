/**
 * cityDetail.test.js — F-DETAIL settings (app/client/static/js/modules/layers/city-detail.js):
 * request fields, the Alhambra extent test and the survey-coverage cache.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import {
    ALHAMBRA_BBOX, bboxIntersects, cityDetailFields, clearSurveyCoverage, coversTrees, reloadBuildingsForRoofShapes, roofShapesValue,
    surveyCoverage,
} from '../../app/client/static/js/modules/layers/city-detail.js';
import { resetActions } from '../../app/client/static/js/vue/components/dem/settings/resetRows.ts';
import { GROUPS } from '../../app/client/static/js/vue/components/dem/settings/layerGroups.ts';

const box = { north: 37.18, south: 37.17, east: -3.58, west: -3.60 };

describe('cityDetailFields', () => {
    it('sends roof_shapes auto by default and off when off', () => {
        expect(cityDetailFields({}).roof_shapes).toBe('auto');
        expect(cityDetailFields({ roofShapes: 'off' }).roof_shapes).toBe('off');
        expect(roofShapesValue('garbage')).toBe('auto');
    });
    it('asks for trees only when switched on and covered', () => {
        expect(cityDetailFields({ trees: true, covered: true }).trees).toEqual({ enabled: true });
        expect(cityDetailFields({ trees: true, covered: false })).not.toHaveProperty('trees');
        expect(cityDetailFields({ trees: false, covered: true })).not.toHaveProperty('trees');
    });
    it('sends reference_landmarks only on and over the Alhambra', () => {
        expect(cityDetailFields({ alhambra: true, alhambraInBox: true }).reference_landmarks).toBe(true);
        expect(cityDetailFields({ alhambra: true, alhambraInBox: false }).reference_landmarks).toBe(false);
        expect(cityDetailFields({}).reference_landmarks).toBe(false);
    });
});

describe('Alhambra extent', () => {
    it('intersects a box over Granada, not one elsewhere', () => {
        expect(bboxIntersects(box, ALHAMBRA_BBOX)).toBe(true);
        expect(bboxIntersects({ north: 40.5, south: 40.4, east: -3.6, west: -3.7 }, ALHAMBRA_BBOX)).toBe(false);
        expect(bboxIntersects(null, ALHAMBRA_BBOX)).toBe(false);
    });
});

describe('survey coverage', () => {
    beforeEach(() => clearSurveyCoverage());
    const src = (name, available) => ({ name, available });

    it('counts only vegetation-capable sources', () => {
        expect(coversTrees([src('rediam_mdhn', true)])).toBe(true);
        expect(coversTrees([src('cnig_mdsn', true), src('rediam_mdhn', false)])).toBe(false);
        expect(coversTrees(undefined)).toBe(false);
    });
    it('asks once per box, then answers from the cache', async () => {
        let calls = 0;
        const get = async () => { calls++; return [src('rediam_mdhn', true)]; };
        let changed = 0;
        expect(surveyCoverage(box, get, () => changed++)).toBe('unknown');
        expect(surveyCoverage(box, get, () => changed++)).toBe('unknown');
        await new Promise((r) => setTimeout(r, 0));
        expect(surveyCoverage(box, get)).toBe('yes');
        expect(calls).toBe(1);
        expect(changed).toBe(1);
    });
    it('treats a failed request as not covered', async () => {
        const get = async () => { throw new Error('down'); };
        surveyCoverage(box, get);
        await new Promise((r) => setTimeout(r, 0));
        expect(surveyCoverage(box, get)).toBe('no');
    });
});

describe('Composite rows', () => {
    it('has Roof shapes (Auto/Off), Trees and Alhambra model in Buildings & roads', () => {
        const rows = GROUPS.city.composite;
        const roof = rows.find((r) => r.id === 'cityRoofShapes');
        expect(roof.options.map((o) => o.value)).toEqual(['auto', 'off']);
        expect(rows.find((r) => r.id === 'cityTrees').kind).toBe('switch');
        expect(typeof rows.find((r) => r.id === 'cityAlhambra').show).toBe('function');
    });
});

describe('surveyCoverageReady', () => {
    it('waits for the first answer instead of reporting unknown', async () => {
        const { surveyCoverageReady, clearSurveyCoverage } = await import('../../app/client/static/js/modules/layers/city-detail.js');
        clearSurveyCoverage();
        const box = { north: 1, south: 0, east: 1, west: 0 };
        const got = await surveyCoverageReady(box, async () => [{ name: 'rediam_mdhn', available: true }]);
        expect(got).toBe('yes');
        expect(await surveyCoverageReady(null, async () => [])).toBe('unknown');
    });
});

describe('Edit panel Reset (Composite)', () => {
    const rows = GROUPS.city.composite;
    // A stand-in page: Trees is a checkbox that starts ticked, Roof shapes a select starting auto.
    const page = {
        cityTrees: { kind: 'checkbox', initial: true },
        cityRoofShapes: { kind: 'select', initial: 'auto' },
        cityAlhambra: { kind: 'checkbox', initial: false },
    };
    it('restores the Trees row like the others', () => {
        const acts = resetActions(rows, (id) => page[id] ?? null, '600');
        expect(acts).toContainEqual({ id: 'cityTrees', op: 'checked', value: true });
        expect(acts).toContainEqual({ id: 'cityRoofShapes', op: 'field', value: 'auto' });
    });
    it('still leaves a getter row over a non-checkbox control alone', () => {
        const sat = GROUPS.satellite.composite;
        const acts = resetActions(sat, () => ({ kind: 'select', initial: 'terrain' }), '600');
        expect(acts.find((a) => a.id === 'viewerColormap')).toBeUndefined();
    });
});

describe('Roof shapes change', () => {
    const loaded = { osmCityData: { buildings: { features: [{}] } } };
    it('reloads the buildings at once when they are on the map', () => {
        let n = 0;
        expect(reloadBuildingsForRoofShapes({ appState: loaded, loadCityData: () => { n++; } })).toBe(true);
        expect(n).toBe(1);
    });
    it('does nothing before the first load', () => {
        let n = 0;
        expect(reloadBuildingsForRoofShapes({ appState: {}, loadCityData: () => { n++; } })).toBe(false);
        expect(reloadBuildingsForRoofShapes({ appState: { osmCityData: { buildings: { features: [] } } }, loadCityData: () => { n++; } })).toBe(false);
        expect(n).toBe(0);
    });
});
