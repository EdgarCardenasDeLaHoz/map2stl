/**
 * hydrologyPrint.test.js — the shared river settings and the print-model
 * hydrology query (app/client/static/js/modules/layers/hydrology-print.js).
 */
import { describe, it, expect } from 'vitest';
import {
    riverSourceFromHydro, readHydrologyRiverControls, loadedDemGrid, hydrologyPrintQuery,
    readRiverDepthScale, nextDemGrid, readVectorRivers, readVectorRiversMinDepthMm,
    LARGE_RIVER_REGION_KM, isLargeRiverRegion, riverLoadingMessage,
} from '../../app/client/static/js/modules/layers/hydrology-print.js';

/** Minimal document stub: id → {value}. */
const doc = vals => ({ getElementById: id => (id in vals ? { value: vals[id] } : null) });

describe('riverSourceFromHydro', () => {
    it('maps the Fetch > Hydrology values onto composite layer sources', () => {
        expect(riverSourceFromHydro('natural_earth')).toBe('natural_earth_rivers');
        expect(riverSourceFromHydro('hydrorivers')).toBe('hydrorivers');
        expect(riverSourceFromHydro(undefined)).toBe('hydrorivers');
    });
});

describe('readHydrologyRiverControls', () => {
    it('reads source, min order and width', () => {
        const r = readHydrologyRiverControls(doc({
            hydroSource: 'natural_earth', hydroMinOrder: '5', hydroWidthFactor: '2.5',
        }));
        expect(r).toEqual({
            hydroSource: 'natural_earth', riverSource: 'natural_earth_rivers',
            minOrder: 5, widthScale: 2.5,
        });
    });

    it('falls back to the markup defaults when controls are missing', () => {
        expect(readHydrologyRiverControls(doc({}))).toEqual({
            hydroSource: 'hydrorivers', riverSource: 'hydrorivers', minOrder: 3, widthScale: 1,
        });
    });

    it('clamps the order to 1..9', () => {
        expect(readHydrologyRiverControls(doc({ hydroMinOrder: '12' })).minOrder).toBe(9);
    });
});

describe('loadedDemGrid', () => {
    it('is null until a DEM is loaded', () => {
        expect(loadedDemGrid({}, doc({}))).toBeNull();
        expect(loadedDemGrid({ lastDemRequest: { dem: { dim: 800 } } }, doc({}))).toBeNull();
    });

    it('uses the DEM request snapshot', () => {
        const state = { lastDemData: {}, lastDemRequest: { dem: { dim: 800, dem_source: 'COP30' } } };
        expect(loadedDemGrid(state, doc({ paramDim: '600' }))).toEqual({ dim: 800, demSource: 'COP30' });
    });
});

describe('hydrologyPrintQuery', () => {
    const coords = { north: 1, south: 0, east: 1, west: 0 };
    const river = { hydroSource: 'hydrorivers', minOrder: 4, widthScale: 2 };
    const grid = { dim: 700, demSource: 'SRTMGL1' };

    it('sends the DEM grid and the river settings, not the old preview params', () => {
        const q = hydrologyPrintQuery({ coords, grid, river, depthScale: 3 });
        expect(q.get('dem_source')).toBe('SRTMGL1');
        expect(q.get('dim')).toBe('700');
        expect(q.get('source')).toBe('hydrorivers');
        expect(q.get('min_order')).toBe('4');
        expect(q.get('width_scale')).toBe('2');
        expect(q.get('depth_scale')).toBe('3');
        for (const old of ['depression_m', 'order_exponent', 'width_factor']) expect(q.has(old)).toBe(false);
        expect(q.has('projection')).toBe(false);
    });

    it('adds projection params only for a real projection', () => {
        const q = hydrologyPrintQuery({
            coords, grid, river, projection: 'cosine', maintainDimensions: true, clipValidRegion: true,
        });
        expect(q.get('projection')).toBe('cosine');
        expect(q.get('maintain_dimensions')).toBe('true');
        expect(q.get('clip_valid_region')).toBe('true');
    });
});

describe('readRiverDepthScale', () => {
    it('reads Composite Depth × with default 1', () => {
        expect(readRiverDepthScale(doc({ compositeRiverDepthScale: '2.5' }))).toBe(2.5);
        expect(readRiverDepthScale(doc({}))).toBe(1);
    });
});

describe('nextDemGrid', () => {
    it('reads the grid the next DEM fetch will use from the form', () => {
        expect(nextDemGrid(doc({ paramDim: '1000', paramDemSource: 'copernicus' })))
            .toEqual({ dim: 1000, demSource: 'copernicus' });
    });

    it('falls back to 600 px and the local source', () => {
        expect(nextDemGrid(doc({ paramDim: '' }))).toEqual({ dim: 600, demSource: 'local' });
        expect(nextDemGrid(null)).toEqual({ dim: 600, demSource: 'local' });
    });
});

describe('readVectorRivers', () => {
    it('maps Auto / On / Off onto the request value', () => {
        expect(readVectorRivers(doc({ compositeVectorRivers: 'auto' }))).toBe('auto');
        expect(readVectorRivers(doc({ compositeVectorRivers: 'on' }))).toBe(true);
        expect(readVectorRivers(doc({ compositeVectorRivers: 'off' }))).toBe(false);
        expect(readVectorRivers(doc({}))).toBe('auto');
    });

    it('reads the shallowest reach, 0.1 mm by default, 0 allowed', () => {
        expect(readVectorRiversMinDepthMm(doc({ compositeVectorRiversMinDepthMm: '0.25' }))).toBe(0.25);
        expect(readVectorRiversMinDepthMm(doc({ compositeVectorRiversMinDepthMm: '0' }))).toBe(0);
        expect(readVectorRiversMinDepthMm(doc({}))).toBe(0.1);
    });
});

describe('isLargeRiverRegion / riverLoadingMessage', () => {
    const amazon = { north: 5, south: -20, east: -45, west: -80 };
    const town = { north: 40.1, south: 40, east: -105.1, west: -105.3 };
    it('uses the 200 km side, like export_params.VECTOR_RIVERS_AUTO_MIN_SIDE_KM', () => {
        expect(LARGE_RIVER_REGION_KM).toBe(200);
        expect(isLargeRiverRegion(amazon)).toBe(true);
        expect(isLargeRiverRegion(town)).toBe(false);
        expect(isLargeRiverRegion({ north: '5', south: '-20', east: '-45', west: '-80' })).toBe(true);
        expect(isLargeRiverRegion(null)).toBe(false);
    });
    it('says how long a big region takes', () => {
        expect(riverLoadingMessage(town, 1)).toBe('Loading rivers…');
        expect(riverLoadingMessage(amazon, 3)).toBe('Loading rivers… large region, may take a while');
        expect(riverLoadingMessage(amazon, 1)).toBe('Loading rivers… large region, about a minute');
    });
});
