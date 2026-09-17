import { describe, it, expect, beforeAll } from 'vitest';

// ui-helpers.js is a side-effect module that attaches helpers to window.*.
// Only definitions run at import time, so a bare window stub is enough.
let escapeHtml;

beforeAll(async () => {
    globalThis.window = globalThis.window || {};
    await import('../../app/client/static/js/modules/core/ui-helpers.js');
    escapeHtml = globalThis.window.escapeHtml;
});

describe('escapeHtml', () => {
    it('is exposed on window', () => {
        expect(typeof escapeHtml).toBe('function');
    });

    it('escapes the five HTML-significant characters', () => {
        expect(escapeHtml(`<a href="x" title='y'>&</a>`)).toBe(
            '&lt;a href=&quot;x&quot; title=&#39;y&#39;&gt;&amp;&lt;/a&gt;',
        );
    });

    it('neutralises an attribute-breaking payload', () => {
        const out = escapeHtml(`x" onmouseover="alert(1)`);
        expect(out).not.toContain('"');
        expect(out).toBe('x&quot; onmouseover=&quot;alert(1)');
    });

    it('does not double-escape plain text', () => {
        expect(escapeHtml('Grand Canyon, AZ')).toBe('Grand Canyon, AZ');
    });

    it('maps null and undefined to empty string', () => {
        expect(escapeHtml(null)).toBe('');
        expect(escapeHtml(undefined)).toBe('');
    });

    it('stringifies non-string values', () => {
        expect(escapeHtml(42)).toBe('42');
        expect(escapeHtml(0)).toBe('0');
    });
});
