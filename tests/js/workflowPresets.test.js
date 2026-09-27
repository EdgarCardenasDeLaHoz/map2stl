/**
 * workflowPresets.test.js — City / Mountain / Coast presets
 * (app/client/static/js/modules/ui/workflow-presets.js), applied to a minimal
 * fake document: values are set, input + change fire, unavailable options are
 * skipped, and the undo list restores the previous state.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import {
    WORKFLOW_PRESETS, applyFields, applyWorkflowPreset, regionDemSource,
} from '../../app/client/static/js/modules/ui/workflow-presets.js';

class FakeEl {
    constructor(id, { tagName = 'INPUT', value = '', checked = false, options = null } = {}) {
        this.id = id;
        this.tagName = tagName;
        this.value = value;
        this.checked = checked;
        this.options = options;
        this.events = [];
    }
    dispatchEvent(e) { this.events.push(e.type); return true; }
}

function makeDoc({ srtmDisabled = false } = {}) {
    const els = {};
    const add = (id, opts) => { els[id] = new FakeEl(id, opts); };
    add('paramDemSource', {
        tagName: 'SELECT', value: 'h5_local',
        options: [
            { value: 'h5_local', disabled: false, textContent: 'Local SRTM H5' },
            { value: 'SRTMGL1', disabled: srtmDisabled, textContent: 'SRTM 30m (Global)' },
            { value: 'SRTMGL3', disabled: false, textContent: 'SRTM 90m (Global)' },
        ],
    });
    add('paramDim', { value: '600' });
    add('exportZMode', {
        tagName: 'SELECT', value: 'fit',
        options: ['auto', 'true', 'fit'].map(value => ({ value, disabled: false })),
    });
    add('exportExaggeration', { value: '1.0' });
    add('exportSeaLevelCap', { checked: false });
    add('bedSizeSelect', { tagName: 'SELECT', value: '256x256' });
    add('cityPuzzleEnabled', { checked: false });
    add('cityPieceMm', { value: '200' });
    for (const id of ['buildings', 'fortifications', 'walls', 'towers', 'churches',
        'roads', 'railways', 'trails', 'green', 'waterways']) {
        add(`cityLayer_${id}_enabled`, { checked: id !== 'trails' });
    }
    add('cityLayer_waterways_mode', {
        tagName: 'SELECT', value: 'water',
        options: [{ value: 'water', disabled: false }, { value: 'engraved', disabled: false }],
    });
    add('cityLayer_waterways_value', { value: '1' });
    for (const id of ['compositeEnabled', 'compositeRiversEnabled', 'compositeLakesEnabled']) {
        add(id, { checked: id === 'compositeEnabled' });
    }
    return { els, getElementById: id => els[id] || null };
}

describe('WORKFLOW_PRESETS', () => {
    it('defines City, Mountain, Region and Coast with labelled fields', () => {
        expect(Object.keys(WORKFLOW_PRESETS)).toEqual(['city', 'mountain', 'region', 'coast']);
        for (const p of Object.values(WORKFLOW_PRESETS)) {
            expect(p.label).toBeTruthy();
            expect(p.fields.every(f => f.id && ('value' in f || 'checked' in f))).toBe(true);
        }
    });
});

describe('applyWorkflowPreset', () => {
    let doc;
    beforeEach(() => { doc = makeDoc(); });

    it('City: source, resolution, vertical, all layers, puzzle sized to the bed', () => {
        const { skipped } = applyWorkflowPreset('city', doc);
        expect(skipped).toEqual([]);
        const e = doc.els;
        expect(e.paramDemSource.value).toBe('SRTMGL1');
        expect(e.paramDim.value).toBe('1000');
        expect(e.exportZMode.value).toBe('auto');
        expect(e.exportExaggeration.value).toBe('1');
        expect(e.cityLayer_trails_enabled.checked).toBe(true);
        expect(e.cityPuzzleEnabled.checked).toBe(true);
        expect(e.cityPieceMm.value).toBe('246');    // 256 bed - 10 mm
        expect(e.paramDim.events).toEqual(['input', 'change']);
    });

    it('Mountain: buildings and roads off, trails and water on, 1.5x', () => {
        applyWorkflowPreset('mountain', doc);
        const e = doc.els;
        expect(e.exportExaggeration.value).toBe('1.5');
        expect(e.cityLayer_buildings_enabled.checked).toBe(false);
        expect(e.cityLayer_roads_enabled.checked).toBe(false);
        expect(e.cityLayer_trails_enabled.checked).toBe(true);
        expect(e.cityLayer_waterways_enabled.checked).toBe(true);
        expect(e.cityPuzzleEnabled.checked).toBe(false);   // untouched
    });

    it('Region: terrain only, rivers + lakes on, vertical auto, 30 m under 100 km', () => {
        // ~50 km box around the Middle Rhine.
        const bbox = { north: 50.35, south: 49.95, east: 7.95, west: 7.45 };
        const { skipped } = applyWorkflowPreset('region', doc, { bbox });
        expect(skipped).toEqual([]);
        const e = doc.els;
        expect(e.paramDemSource.value).toBe('SRTMGL1');
        expect(e.exportZMode.value).toBe('auto');
        for (const id of ['buildings', 'roads', 'waterways', 'trails', 'green']) {
            expect(e[`cityLayer_${id}_enabled`].checked).toBe(false);
        }
        expect(e.compositeRiversEnabled.checked).toBe(true);
        expect(e.compositeLakesEnabled.checked).toBe(true);
        expect(e.cityPuzzleEnabled.checked).toBe(false);
    });

    it('Region: 90 m source beyond ~100 km, skipped without a region', () => {
        const big = { north: 37.0, south: 35.8, east: -111.6, west: -113.2 };   // Grand Canyon, ~145 km
        applyWorkflowPreset('region', doc, { bbox: big });
        expect(doc.els.paramDemSource.value).toBe('SRTMGL3');
        const fresh = makeDoc();
        const { skipped } = applyWorkflowPreset('region', fresh);
        expect(skipped).toEqual([{ id: 'paramDemSource', reason: 'select a region first' }]);
        expect(fresh.els.paramDemSource.value).toBe('h5_local');
    });

    it('Coast: sea-level cap, water engraved 1 mm, buildings on', () => {
        applyWorkflowPreset('coast', doc);
        const e = doc.els;
        expect(e.exportSeaLevelCap.checked).toBe(true);
        expect(e.cityLayer_waterways_mode.value).toBe('engraved');
        expect(e.cityLayer_waterways_value.value).toBe('1');
        expect(e.cityLayer_buildings_enabled.checked).toBe(true);
    });

    it('skips an unavailable DEM source instead of selecting it', () => {
        doc = makeDoc({ srtmDisabled: true });
        const { skipped } = applyWorkflowPreset('city', doc);
        expect(skipped.map(s => s.id)).toEqual(['paramDemSource']);
        expect(doc.els.paramDemSource.value).toBe('h5_local');
    });

    it('does not fire events for fields already at the preset value', () => {
        doc.els.paramDim.value = '1000';
        const { applied } = applyWorkflowPreset('city', doc);
        expect(applied).not.toContain('paramDim');
        expect(doc.els.paramDim.events).toEqual([]);
    });

    it('the undo list restores the previous values', () => {
        const before = Object.fromEntries(Object.entries(doc.els).map(([k, el]) => [k, [el.value, el.checked]]));
        const { undo } = applyWorkflowPreset('mountain', doc);
        applyFields(undo, doc);
        const after = Object.fromEntries(Object.entries(doc.els).map(([k, el]) => [k, [el.value, el.checked]]));
        expect(after).toEqual(before);
    });

    it('rejects an unknown preset', () => {
        expect(() => applyWorkflowPreset('desert', doc)).toThrow(/Unknown/);
    });
});

describe('regionDemSource', () => {
    it('picks 30 m up to 100 km on the longer side and 90 m beyond', () => {
        expect(regionDemSource({ north: 0.9, south: 0, east: 0.8, west: 0 })).toBe('SRTMGL1');  // 99.5 x 89 km
        expect(regionDemSource({ north: 0.8, south: 0, east: 0.8, west: 0 })).toBe('SRTMGL1');  // 88 km
        expect(regionDemSource({ north: 1, south: 0, east: 1, west: 0 })).toBe('SRTMGL3');
        expect(regionDemSource(null)).toBeNull();
    });
});
