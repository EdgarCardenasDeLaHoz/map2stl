/**
 * previewGate.test.js — the dedupe gate in front of POST /api/export/preview.
 *
 * model-viewer.js is a side-effecting browser module, so the pure
 * _createPreviewGate() is sliced out of the real source rather than copied
 * into a helper that could drift from it.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const source = readFileSync(
    join(__dirname, '..', '..', 'app', 'client', 'static', 'js', 'modules', 'export', 'model-viewer.js'),
    'utf8',
);
const match = source.match(/^function _createPreviewGate\(\) \{[\s\S]*?^\}$/m);
const createPreviewGate = new Function(`${match[0]}; return _createPreviewGate;`)();

describe('_createPreviewGate', () => {
    let gate;
    beforeEach(() => { gate = createPreviewGate(); });

    it('lets the first request through', () => {
        expect(gate.isDuplicate('a', false)).toBe(false);
    });

    it('skips an identical request while one is in flight', () => {
        gate.begin('a');
        expect(gate.isDuplicate('a', false)).toBe(true);
    });

    it('skips an identical request once its mesh is shown', () => {
        gate.settle(gate.begin('a'), true);
        expect(gate.isDuplicate('a', true)).toBe(true);
    });

    it('resends when the shown mesh was cleared', () => {
        gate.settle(gate.begin('a'), true);
        expect(gate.isDuplicate('a', false)).toBe(false);
    });

    it('retries a request that failed', () => {
        gate.settle(gate.begin('a'), false);
        expect(gate.isDuplicate('a', true)).toBe(false);
    });

    it('sends a request whose parameters differ', () => {
        gate.begin('a');
        expect(gate.isDuplicate('b', true)).toBe(false);
    });

    it('resends the old parameters after a newer request replaced them', () => {
        const first = gate.begin('a');
        gate.begin('b');
        gate.settle(first, true);          // superseded ticket: ignored
        expect(gate.isDuplicate('a', true)).toBe(false);
        expect(gate.isDuplicate('b', false)).toBe(true);
    });
});
