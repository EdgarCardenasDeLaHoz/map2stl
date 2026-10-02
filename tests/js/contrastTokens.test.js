import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// The dark theme's text tokens must stay readable (WCAG 2.2 SC 1.4.3, 4.5:1).
// They were #555 / #333 (1.85:1 and 1.09:1 on the #2d2d2d panels) until the
// 2026-09-30 UI audit; this parses app.css :root so a regression fails here.

const CSS = readFileSync(
    fileURLToPath(new URL('../../app/client/static/css/app.css', import.meta.url)), 'utf8');

/** Custom properties declared in the first :root block of app.css. */
function rootTokens(css) {
    const m = css.match(/:root\s*\{([^}]*)\}/);
    const out = {};
    for (const [, name, value] of (m ? m[1] : '').matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
        out[name] = value.trim();
    }
    return out;
}

/** WCAG relative luminance of a #rgb / #rrggbb colour. */
function luminance(hex) {
    let h = hex.replace('#', '');
    if (h.length === 3) h = h.split('').map((c) => c + c).join('');
    const [r, g, b] = [0, 2, 4].map((i) => {
        const c = parseInt(h.slice(i, i + 2), 16) / 255;
        return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio between two colours (1 … 21). */
function contrast(a, b) {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
}

const PANEL = '#2d2d2d';
const INPUT = '#404040';
const BODY_TEXT = '#e0e0e0';
const tokens = rootTokens(CSS);

describe('contrast helper', () => {
    it('matches known WCAG values', () => {
        expect(contrast('#000', '#fff')).toBeCloseTo(21, 1);
        expect(contrast('#555', PANEL)).toBeCloseTo(1.85, 1);
        expect(contrast('#888', PANEL)).toBeCloseTo(3.89, 1);
    });
});

describe('app.css text tokens on the dark theme', () => {
    it('declares the tokens as hex colours', () => {
        for (const name of ['--text-muted', '--text-dim']) {
            expect(tokens[name]).toMatch(/^#[0-9a-f]{3}([0-9a-f]{3})?$/i);
        }
    });

    it.each(['--text-muted', '--text-dim'])('%s passes 4.5:1 on the #2d2d2d panels', (name) => {
        expect(contrast(tokens[name], PANEL)).toBeGreaterThanOrEqual(4.5);
    });

    it('--text-muted also passes 4.5:1 on #404040 inputs (placeholders use it)', () => {
        expect(contrast(tokens['--text-muted'], INPUT)).toBeGreaterThanOrEqual(4.5);
    });

    it('keeps a lower emphasis than body text', () => {
        const body = luminance(BODY_TEXT);
        expect(luminance(tokens['--text-muted'])).toBeLessThan(body);
        expect(luminance(tokens['--text-dim'])).toBeLessThanOrEqual(luminance(tokens['--text-muted']));
    });
});
