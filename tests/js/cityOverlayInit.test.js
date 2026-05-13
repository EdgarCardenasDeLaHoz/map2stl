import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

describe('city overlay init timing guard', () => {
    const source = readFileSync(
        join(__dirname, '..', '..', 'app', 'client', 'static', 'js', 'modules', 'layers', 'city-overlay.js'),
        'utf8',
    );

    it('defines the named init function used for subscriptions', () => {
        expect(source).toMatch(/function\s+_initCityOverlaySubscriptions\s*\(/);
    });

    it('registers init immediately when DOM is already loaded', () => {
        expect(source).toMatch(/if\s*\(\s*document\.readyState\s*===\s*['\"]loading['\"]\s*\)/);
        expect(source).toMatch(/document\.addEventListener\(\s*['\"]DOMContentLoaded['\"]\s*,\s*_initCityOverlaySubscriptions\s*\)/);
        expect(source).toMatch(/else\s*\{\s*_initCityOverlaySubscriptions\s*\(\s*\)\s*;\s*\}/s);
    });
});
