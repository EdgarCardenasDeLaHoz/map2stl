/**
 * Landmark override helpers (app/client/static/js/modules/layers/landmark-overrides.js).
 */
import { describe, expect, it } from 'vitest';
import {
    draftFromSpec,
    meshBounds,
    overrideLabel,
    overridesForBuild,
    specFromDraft,
} from '../../app/client/static/js/modules/layers/landmark-overrides.js';

describe('overridesForBuild', () => {
    it('drops osm and incomplete entries', () => {
        expect(overridesForBuild({
            'way/1': { kind: 'osm' },
            'way/2': { kind: 'ndsm', provider: 'auto' },
            'way/3': { kind: 'mesh' },
            'way/4': { kind: 'mesh', upload_id: 'u1' },
            'way/5': null,
        })).toEqual({ 'way/2': { kind: 'ndsm', provider: 'auto' }, 'way/4': { kind: 'mesh', upload_id: 'u1' } });
    });
    it('returns null when nothing applies', () => {
        expect(overridesForBuild({ 'way/1': { kind: 'osm' } })).toBeNull();
        expect(overridesForBuild(undefined)).toBeNull();
    });
});

describe('draft <-> spec', () => {
    it('round-trips a mesh override', () => {
        const spec = { kind: 'mesh', upload_id: 'u', fit: 'uniform', rotation_deg: 90, scale: 1.2,
            offset_m: [1, -2], vertical: 'fit', height_m: 40, up_axis: 'y', filename: 'c.glb', format: 'glb' };
        expect(specFromDraft(draftFromSpec(spec))).toEqual(spec);
    });
    it('keeps only what the kind uses', () => {
        const d = draftFromSpec({ kind: 'ndsm', provider: 'cnig_mdsn', upload_id: 'stale' });
        expect(specFromDraft(d)).toEqual({ kind: 'ndsm', provider: 'cnig_mdsn' });
        expect(specFromDraft(draftFromSpec({ kind: 'weird' }))).toEqual({ kind: 'osm' });
    });
    it('drops height_m unless fitting vertically', () => {
        const d = draftFromSpec({ kind: 'mesh', upload_id: 'u', height_m: 30 });
        expect(specFromDraft(d).height_m).toBeUndefined();
    });
});

describe('labels and bounds', () => {
    it('labels overrides', () => {
        expect(overrideLabel(undefined)).toBe('OSM');
        expect(overrideLabel({ kind: 'ndsm', provider: 'ign_lidarhd' })).toBe('nDSM · ign_lidarhd');
        expect(overrideLabel({ kind: 'mesh', filename: 'a.stl' })).toBe('Mesh · a.stl');
    });
    it('bounds a flat vertex list', () => {
        const b = meshBounds([0, 0, 0, 10, 2, 4, -2, 1, 1]);
        expect(b.min).toEqual([-2, 0, 0]);
        expect(b.max).toEqual([10, 2, 4]);
        expect(b.center).toEqual([4, 1, 2]);
        expect(b.size).toBe(12);
    });
});
