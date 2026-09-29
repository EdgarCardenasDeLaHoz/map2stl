/**
 * compositeSpec.test.js — the Composite panel's server layer spec
 * (app/client/static/js/modules/layers/composite-spec.js).
 *
 * Two-stage mesh pipeline (docs/plans/active/F-ARCH-consolidation.md): the rasterised
 * OSM feature channels may appear in the 2D preview spec, but never in the
 * spec used for Apply / export, or buildings get printed twice.
 */
import { describe, it, expect } from 'vitest';
import {
    FEATURE_SOURCES, WATER_TERRAIN_SOURCES, buildCompositeLayerSpec, anyFeatureChannelEnabled,
    waterTerrainLayers,
} from '../../app/client/static/js/modules/layers/composite-spec.js';

// Mirrors composite-dem.js DEFAULTS.
const DEFAULTS = {
    demEnabled: true, demWeight: 1.0,
    waterEnabled: true, waterDepth: 5.0, waterWeight: 1.0,
    buildingsEnabled: true, buildingScale: 1.0,
    roadsEnabled: true, roadCut: 0.5,
    waterwaysEnabled: true, riverDepth: 3.0,
    wallsEnabled: true, wallScale: 1.0,
    landcoverEnabled: true, treeHeight: 8.0, landcoverWeight: 0.0,
    satEnabled: true, vegHeight: 5.0, satWeight: 0.0,
    trailsEnabled: true, trailsSkiEnabled: true, trailsHikingEnabled: true, trailsWeight: 0.0,
    riversEnabled: false, riverSource: 'hydrorivers', riverMinOrder: 3,
    riverDepthScale: 1.0, riverWidthScale: 1.0,
    lakesEnabled: false, lakeDepth: 2.0, lakeMinAreaHa: 1.0,
};
const CTX = { dim: 400, demSource: 'h5_local', detail: 'full' };
const sources = spec => spec.layers.map(l => l.source);

describe('buildCompositeLayerSpec — export/apply (default)', () => {
    it('excludes every OSM feature channel even when all are enabled', () => {
        const spec = buildCompositeLayerSpec(DEFAULTS, CTX);
        for (const src of FEATURE_SOURCES) expect(sources(spec)).not.toContain(src);
        expect(sources(spec)).toEqual(['h5_local', 'water_esa']);
    });

    it('keeps water depth as a terrain modifier', () => {
        const spec = buildCompositeLayerSpec(DEFAULTS, CTX);
        const water = spec.layers.find(l => l.source === 'water_esa');
        expect(water).toMatchObject({ blend_mode: 'rivers', weight: 5.0, dim: 400 });
    });

    it('puts the base DEM first', () => {
        const spec = buildCompositeLayerSpec(DEFAULTS, CTX);
        expect(spec.layers[0]).toMatchObject({ source: 'h5_local', blend_mode: 'base', weight: 1.0 });
    });

    it('is the same whether or not feature channels are toggled', () => {
        const off = { ...DEFAULTS, buildingsEnabled: false, roadsEnabled: false,
            waterwaysEnabled: false, wallsEnabled: false };
        expect(buildCompositeLayerSpec(off, CTX)).toEqual(buildCompositeLayerSpec(DEFAULTS, CTX));
    });

    it('reports channels with no server source as unsupported', () => {
        const spec = buildCompositeLayerSpec({ ...DEFAULTS, landcoverWeight: 1, trailsWeight: 0.5 }, CTX);
        expect(spec.unsupported).toEqual(['land cover', 'trails']);
    });

    it('drops zero-weight layers', () => {
        const spec = buildCompositeLayerSpec({ ...DEFAULTS, waterWeight: 0 }, CTX);
        expect(sources(spec)).toEqual(['h5_local']);
    });
});

describe('buildCompositeLayerSpec — 2D preview (includeFeatures)', () => {
    it('keeps the enabled OSM feature channels', () => {
        const spec = buildCompositeLayerSpec(DEFAULTS, CTX, { includeFeatures: true });
        expect(sources(spec)).toEqual(['h5_local', 'water_esa', ...FEATURE_SOURCES]);
        expect(spec.layers.find(l => l.source === 'osm_roads'))
            .toMatchObject({ blend_mode: 'rivers', weight: 0.5, options: { detail: 'full' } });
    });

    it('honours the per-channel toggles', () => {
        const spec = buildCompositeLayerSpec({ ...DEFAULTS, roadsEnabled: false, wallsEnabled: false },
            CTX, { includeFeatures: true });
        expect(sources(spec)).toEqual(['h5_local', 'water_esa', 'osm_buildings', 'osm_waterways']);
    });
});

describe('rivers and lakes (terrain channels)', () => {
    const ON = { ...DEFAULTS, riversEnabled: true, lakesEnabled: true };

    it('are in the export spec, added after the DEM', () => {
        const spec = buildCompositeLayerSpec(ON, CTX);
        expect(sources(spec)).toEqual(['h5_local', 'water_esa', 'hydrorivers', 'lakes']);
        expect(spec.layers.find(l => l.source === 'hydrorivers')).toMatchObject({
            blend_mode: 'add', weight: 1.0, dim: 400, options: { min_order: 3, width_scale: 1.0 },
        });
        expect(spec.layers.find(l => l.source === 'lakes')).toMatchObject({
            blend_mode: 'add', weight: 1, options: { depth_m: 2.0, min_area_m2: 10000 },
        });
        expect(spec.unsupported).toEqual([]);
        for (const src of WATER_TERRAIN_SOURCES) expect(FEATURE_SOURCES).not.toContain(src);
    });

    it('switch to Natural Earth and scale the depth', () => {
        const layers = waterTerrainLayers({ ...ON, lakesEnabled: false,
            riverSource: 'natural_earth_rivers', riverDepthScale: 4 }, 600);
        expect(layers).toEqual([{ source: 'natural_earth_rivers', dim: 600, blend_mode: 'add',
            weight: 4, options: { min_order: 3, width_scale: 1.0 } }]);
    });

    it('are absent when off', () => {
        expect(waterTerrainLayers(DEFAULTS, 400)).toEqual([]);
    });
});

describe('anyFeatureChannelEnabled', () => {
    it('is true when any OSM channel is on, false when all are off', () => {
        expect(anyFeatureChannelEnabled(DEFAULTS)).toBe(true);
        expect(anyFeatureChannelEnabled({ ...DEFAULTS, buildingsEnabled: false, roadsEnabled: false,
            waterwaysEnabled: false, wallsEnabled: false })).toBe(false);
    });
});
