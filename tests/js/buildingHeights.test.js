/**
 * buildingHeights.test.js — height-source summary, histogram, tallest list and
 * the layer_data payload (app/client/static/js/modules/layers/building-heights.js).
 */
import { describe, it, expect } from 'vitest';
import {
    buildingsWithOverrides, hasOverrides, heightSourceGroup, summarizeBuildingHeights,
} from '../../app/client/static/js/modules/layers/building-heights.js';

const feat = (height_m, height_source, extra = {}) => ({
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
    properties: { height_m, height_source, ...extra },
});

describe('heightSourceGroup', () => {
    it('groups the server sources', () => {
        expect(heightSourceGroup('osm_tag')).toBe('osm_tag');
        expect(heightSourceGroup('osm_levels')).toBe('osm_levels');
        expect(heightSourceGroup('lidar_3dep_copc')).toBe('lidar');
        expect(heightSourceGroup('merged')).toBe('raster');
        expect(heightSourceGroup('ghsl')).toBe('raster');
        expect(heightSourceGroup('default')).toBe('default');
        expect(heightSourceGroup(undefined)).toBe('unknown');
    });
});

describe('summarizeBuildingHeights', () => {
    const features = [
        feat(12, 'osm_tag'),
        feat(7, 'osm_levels'),
        feat(31.5, 'lidar_3dep_copc'),
        feat(18, 'ndsm'),
        feat(22, 'merged'),
        feat(10, 'default'),
        feat(10, 'default'),
        feat(null, undefined),
    ];

    it('counts per source group and per raw source', () => {
        const s = summarizeBuildingHeights(features);
        expect(s.total).toBe(8);
        expect(s.withHeight).toBe(7);
        const byKey = Object.fromEntries(s.groups.map(g => [g.key, g.count]));
        expect(byKey).toEqual({ osm_tag: 1, osm_levels: 1, lidar: 1, raster: 2, default: 2, unknown: 1 });
        expect(s.sources[0]).toEqual({ source: 'default', count: 2 });
        expect(s.groups.reduce((n, g) => n + g.count, 0)).toBe(8);
    });

    it('warns when more than 20% use the default height', () => {
        const s = summarizeBuildingHeights(features);
        expect(s.defaultCount).toBe(2);
        expect(s.defaultShare).toBeCloseTo(0.25);
        expect(s.warnDefault).toBe(true);
        const fewDefaults = [feat(10, 'default'), ...Array.from({ length: 4 }, () => feat(5, 'osm_tag'))];
        expect(summarizeBuildingHeights(fewDefaults).warnDefault).toBe(false);   // exactly 20%
    });

    it('bins every height into a nice-width histogram', () => {
        const s = summarizeBuildingHeights(features, { binCount: 12 });
        const { bins, binWidth, maxCount } = s.histogram;
        expect([1, 2, 5, 10, 20, 50]).toContain(binWidth);
        expect(bins.reduce((n, b) => n + b.count, 0)).toBe(7);
        expect(bins[bins.length - 1].hi).toBeGreaterThanOrEqual(31.5);
        expect(maxCount).toBe(Math.max(...bins.map(b => b.count)));
    });

    it('lists the tallest first, overrides included', () => {
        const s = summarizeBuildingHeights(features, { topN: 3, overrides: { 1: 40 } });
        expect(s.tallest.map(t => t.index)).toEqual([1, 2, 4]);
        expect(s.tallest[0]).toMatchObject({ height: 40, overridden: true });
        expect(s.tallest[1]).toMatchObject({ height: 31.5, overridden: false, source: 'lidar_3dep_copc' });
    });

    it('handles no data', () => {
        const s = summarizeBuildingHeights(undefined);
        expect(s.total).toBe(0);
        expect(s.warnDefault).toBe(false);
        expect(s.tallest).toEqual([]);
    });
});

describe('buildingsWithOverrides', () => {
    it('applies overrides and drops client-only annotations', () => {
        const src = [feat(12, 'osm_tag', { terrain_z: 100, name: 'A' }), feat(10, 'default')];
        src[0]._cityIndex = 0;
        src[0]._px = { key: 'x' };
        const fc = buildingsWithOverrides(src, { 1: 25 });
        expect(fc.type).toBe('FeatureCollection');
        expect(fc.features).toHaveLength(2);
        expect(fc.features[0].properties).toEqual({ height_m: 12, height_source: 'osm_tag', name: 'A' });
        expect(fc.features[0]).not.toHaveProperty('_px');
        expect(fc.features[1].properties).toMatchObject({ height_m: 25, height_source: 'user_override' });
        // the source features are not modified
        expect(src[1].properties.height_m).toBe(10);
        expect(src[0].properties.terrain_z).toBe(100);
    });

    it('hasOverrides ignores empty values', () => {
        expect(hasOverrides({})).toBe(false);
        expect(hasOverrides(null)).toBe(false);
        expect(hasOverrides({ 3: 12 })).toBe(true);
    });
});
