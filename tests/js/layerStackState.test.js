// Layers rail rules (modules/layers/layer-stack-state.js): order, companions, saved state.
import { describe, it, expect } from 'vitest';
import {
    DEFAULT_LAYER_ORDER, groupOf, hostOf, moveItem, moveRow, parseLayerState, railOrderToStack,
    serializeLayerState, stackToRailOrder,
} from '../../app/client/static/js/modules/layers/layer-stack-state.js';

// The Edit rail's rows (stores/editLayers.ts), by stack key.
const RAIL = ['Dem', 'WaterHydrology', 'Sat', 'SatImg', 'CityOverlay', 'Trails', 'MeshImport', 'Borders'];
// The rail without the city tool: its layers have no row.
const NO_CITY = RAIL.filter((k) => k !== 'CityOverlay');

describe('companions', () => {
    it('ride with their row', () => {
        expect(hostOf('CompositeDem')).toBe('Dem');
        expect(hostOf('CityRaster')).toBe('CityOverlay');
        expect(hostOf('Sat')).toBe('Sat');
        expect(groupOf('Dem')).toEqual(['Dem', 'CompositeDem']);
        expect(groupOf('Trails')).toEqual(['Trails']);
    });
});

describe('moveItem', () => {
    it('moves and clamps', () => {
        expect(moveItem(['a', 'b', 'c'], 0, 2)).toEqual(['b', 'c', 'a']);
        expect(moveItem(['a', 'b', 'c'], 2, -5)).toEqual(['c', 'a', 'b']);
        expect(moveItem(['a', 'b'], 7, 0)).toEqual(['a', 'b']);
    });
});

describe('rail order', () => {
    it('reads top first, one row per group', () => {
        expect(stackToRailOrder(DEFAULT_LAYER_ORDER, RAIL))
            .toEqual(['Borders', 'MeshImport', 'Trails', 'CityOverlay', 'SatImg', 'Sat', 'WaterHydrology', 'Dem']);
    });
    it('appends rows missing from the stack', () => {
        expect(stackToRailOrder(['Dem', 'Sat'], ['Sat', 'Dem', 'Trails'])).toEqual(['Sat', 'Dem', 'Trails']);
    });
    it('round-trips the default order', () => {
        const rail = stackToRailOrder(DEFAULT_LAYER_ORDER, RAIL);
        expect(railOrderToStack(DEFAULT_LAYER_ORDER, rail)).toEqual([...DEFAULT_LAYER_ORDER]);
    });
    it('keeps companions beside their row and unlisted keys in place', () => {
        const out = railOrderToStack(DEFAULT_LAYER_ORDER, ['Dem', 'Borders', 'MeshImport', 'Trails', 'SatImg', 'Sat', 'WaterHydrology']);
        expect(out.indexOf('CompositeDem')).toBe(out.indexOf('Dem') + 1);
        expect(out.at(-1)).toBe('CompositeDem');
        // The city tool is hidden: its two layers have no row, keep their slot and stay together.
        expect(out.indexOf('CityRaster')).toBe(DEFAULT_LAYER_ORDER.indexOf('CityRaster'));
        expect(out.indexOf('CityOverlay')).toBe(out.indexOf('CityRaster') + 1);
        expect(out).toHaveLength(DEFAULT_LAYER_ORDER.length);
    });
});

describe('moveRow', () => {
    it('moves Rivers & lakes above Buildings', () => {
        const out = moveRow(DEFAULT_LAYER_ORDER, RAIL, 'WaterHydrology', 3);
        expect(stackToRailOrder(out, RAIL))
            .toEqual(['Borders', 'MeshImport', 'Trails', 'WaterHydrology', 'CityOverlay', 'SatImg', 'Sat', 'Dem']);
        expect(out.indexOf('WaterHydrology')).toBeGreaterThan(out.indexOf('CityOverlay'));
    });
    it('keeps a hidden group together when another row moves', () => {
        const out = moveRow(DEFAULT_LAYER_ORDER, NO_CITY, 'SatImg', 0);
        expect(out.indexOf('CompositeDem')).toBe(out.indexOf('Dem') + 1);
        expect(out.indexOf('CityOverlay')).toBe(out.indexOf('CityRaster') + 1);
        expect(out.at(-1)).toBe('SatImg');
        expect(parseLayerState({ order: out }).order).toEqual(out);
    });
    it('never puts a hidden layer inside a group', () => {
        // CityRaster's default slot (5) lands between Dem and CompositeDem once Dem is moved up.
        const out = moveRow(DEFAULT_LAYER_ORDER, NO_CITY, 'Dem', 0);
        expect(out.indexOf('CompositeDem')).toBe(out.indexOf('Dem') + 1);
        expect(out.indexOf('CityOverlay')).toBe(out.indexOf('CityRaster') + 1);
    });
    it('ignores a key with no row', () => {
        expect(moveRow(DEFAULT_LAYER_ORDER, RAIL, 'Nope', 0)).toEqual([...DEFAULT_LAYER_ORDER]);
    });
});

describe('parseLayerState', () => {
    it('returns null when nothing usable was saved', () => {
        expect(parseLayerState(null)).toBeNull();
        expect(parseLayerState('x')).toBeNull();
        expect(parseLayerState({ order: ['Bogus'], opacity: { Bogus: 1 }, visible: { Trails: true } })).toBeNull();
    });
    it('drops unknown and duplicate keys, restores missing ones', () => {
        const { order } = parseLayerState({ order: ['Borders', 'Dem', 'Dem', 'Bogus', 'Sat'] });
        expect(order).toHaveLength(DEFAULT_LAYER_ORDER.length);
        expect(new Set(order)).toEqual(new Set(DEFAULT_LAYER_ORDER));
        expect(order.indexOf('Borders')).toBeLessThan(order.indexOf('Sat'));
        expect(order.indexOf('CompositeDem')).toBe(order.indexOf('Dem') + 1);
    });
    it('regroups a companion saved away from its row', () => {
        const saved = DEFAULT_LAYER_ORDER.filter((k) => k !== 'CityRaster');
        const { order } = parseLayerState({ order: ['CityRaster', ...saved] });
        expect(order.indexOf('CityRaster')).toBe(order.indexOf('CityOverlay') - 1);
    });
    it('clamps opacity and keeps only boolean visibility of saved keys', () => {
        const st = parseLayerState({
            opacity: { Sat: 1.7, Trails: -1, Borders: null, Dem: 'x', Bogus: 0.5, WaterHydrology: 0.42 },
            visible: { Dem: false, Sat: 'yes', Borders: true, Trails: true },
        });
        expect(st.order).toBeNull();
        expect(st.opacity).toEqual({ Sat: 1, Trails: 0, WaterHydrology: 0.42 });
        expect(st.visible).toEqual({ Dem: false, Borders: true });
    });
});

describe('serializeLayerState', () => {
    it('writes order, rounded opacity and the saved visibility', () => {
        const st = serializeLayerState(['Dem', 'Sat'], { Dem: 1, Sat: 0.333333, Extra: 1 }, new Set(['Sat']));
        expect(st).toEqual({ order: ['Dem', 'Sat'], opacity: { Dem: 1, Sat: 0.33 }, visible: { Dem: false, Sat: true, Borders: false } });
    });
    it('round-trips through parseLayerState', () => {
        const order = moveRow(DEFAULT_LAYER_ORDER, RAIL, 'Sat', 0);
        const st = serializeLayerState(order, { Sat: 0.5 }, new Set(['Dem', 'Borders']));
        const back = parseLayerState(JSON.parse(JSON.stringify(st)));
        expect(back.order).toEqual(order);
        expect(back.opacity).toEqual({ Sat: 0.5 });
        expect(back.visible).toEqual({ Dem: true, Sat: false, Borders: true });
    });
});
