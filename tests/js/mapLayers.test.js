import { describe, it, expect } from 'vitest';
import {
    TILE_STYLES, DEFAULT_STYLE, isTileStyle, mapLayers,
} from '../../app/client/static/js/vue/explore/mapLayers.ts';
import { viewBoxOf } from '../../app/client/static/js/vue/explore/regionBoxes.ts';

describe('mapLayers', () => {
    it('lists seven base maps and defaults to a listed one', () => {
        expect(TILE_STYLES).toHaveLength(7);
        expect(isTileStyle(DEFAULT_STYLE)).toBe(true);
    });
    it('rejects unknown or non-string styles', () => {
        expect(isTileStyle('nope')).toBe(false);
        expect(isTileStyle(null)).toBe(false);
    });
    it('starts with grid on, labels and terrain off', () => {
        expect(mapLayers.grid).toBe(true);
        expect(mapLayers.labels).toBe(false);
        expect(mapLayers.terrain).toBe(false);
    });
});

describe('viewBoxOf', () => {
    const bounds = { getNorth: () => 2, getSouth: () => 1, getEast: () => 4, getWest: () => 3 };
    it('reads the map bounds', () => {
        expect(viewBoxOf({ getSize: () => ({ x: 800, y: 600 }), getBounds: () => bounds }))
            .toEqual({ north: 2, south: 1, east: 4, west: 3 });
    });
    it('is null while the map has no size or throws', () => {
        expect(viewBoxOf({ getSize: () => ({ x: 0, y: 0 }), getBounds: () => bounds })).toBeNull();
        expect(viewBoxOf({ getSize: () => ({ x: 1, y: 1 }), getBounds: () => { throw new Error('x'); } })).toBeNull();
        expect(viewBoxOf(null)).toBeNull();
    });
});
