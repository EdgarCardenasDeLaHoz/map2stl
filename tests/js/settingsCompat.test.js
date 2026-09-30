import { describe, it, expect } from 'vitest';
import { normalizeSettingsKeys } from '../../app/client/static/js/modules/ui/settings-compat.js';

describe('normalizeSettingsKeys', () => {
    it('renames projection.clip_nans to clip_valid_region', () => {
        const out = normalizeSettingsKeys({ dem: { dim: 600 }, projection: { projection: 'cosine', clip_nans: false } });
        expect(out).toEqual({ dem: { dim: 600 }, projection: { projection: 'cosine', clip_valid_region: false } });
    });

    it('keeps clip_valid_region when both keys are present', () => {
        const out = normalizeSettingsKeys({ projection: { clip_nans: true, clip_valid_region: false } });
        expect(out.projection).toEqual({ clip_valid_region: false });
    });

    it('does not mutate its input', () => {
        const input = { projection: { clip_nans: false } };
        normalizeSettingsKeys(input);
        expect(input).toEqual({ projection: { clip_nans: false } });
    });

    it('returns current-format and empty values unchanged', () => {
        const current = { projection: { clip_valid_region: true } };
        expect(normalizeSettingsKeys(current)).toBe(current);
        expect(normalizeSettingsKeys({})).toEqual({});
        expect(normalizeSettingsKeys(null)).toBe(null);
        expect(normalizeSettingsKeys(undefined)).toBe(undefined);
    });
});
